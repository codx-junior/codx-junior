"""
Offline tests of the project MCP bridge.

A tiny in-process HTTP server speaks the slice of MCP the agent needs:
initialize, initialized notification, tools/list and tools/call. No provider and
no external network are involved.

Run with::

    cd api && pytest tests/test_mcp.py -q
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from codx.junior.mcp.client import McpHTTPClient
from codx.junior.mcp.registry import McpRegistry, mcp_tool_name


class _McpHandler(BaseHTTPRequestHandler):
    """Minimal streamable-HTTP MCP server."""

    protocol_version = "HTTP/1.1"
    calls: List[str] = []
    session_counter = 0

    def log_message(self, *_: Any) -> None:
        """Keep pytest output clean."""

    def _send(self, payload: Optional[Dict[str, Any]], status: int = 200) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", f"session-{self.session_counter}")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length) or b"{}")
        method = message.get("method")
        request_id = message.get("id")
        self.__class__.calls.append(str(method))

        if method == "initialize":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "stub-crm", "version": "1.0"},
                    },
                }
            )
            return
        if method == "notifications/initialized":
            self._send(None, status=202)
            return
        if method == "tools/list":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "get_customer",
                                "description": "Read one customer",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "customer_id": {"type": "string"}
                                    },
                                    "required": ["customer_id"],
                                },
                            },
                            {
                                "name": "create_refund",
                                "description": "Refund an invoice",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "invoice_id": {"type": "string"},
                                        "amount": {"type": "number"},
                                    },
                                    "required": ["invoice_id"],
                                },
                            },
                        ]
                    },
                }
            )
            return
        if method == "tools/call":
            params = message.get("params") or {}
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "tool": params.get("name"),
                                        "arguments": params.get("arguments") or {},
                                    }
                                ),
                            }
                        ]
                    },
                }
            )
            return
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        )


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    """Start the stub on a free localhost port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/mcp"


class _Settings:
    """Project settings shape used by the registry."""

    def __init__(self, servers: List[Any], codx_path: str = "/tmp/mcp-test"):
        self.codx_path = codx_path
        self._servers = servers

    def get_active_mcp_servers(self):
        return list(self._servers)

    def get_mcp_server_by_name(self, name: str):
        return next(
            (server for server in self._servers if server.name == name), None
        )


def test_mcp_client_probe_and_call():
    """The client negotiates, lists tools and invokes one."""

    server, url = _start_server()
    try:
        with McpHTTPClient(url, timeout_seconds=5) as client:
            result = client.probe()
            assert result["server"]["name"] == "stub-crm"
            assert {tool["name"] for tool in result["tools"]} == {
                "get_customer",
                "create_refund",
            }
            output = client.call_tool("get_customer", {"customer_id": "c-1"})
            assert "get_customer" in output
            assert "c-1" in output
    finally:
        server.shutdown()
        server.server_close()


def test_registry_namespaces_and_enforces_allowlist():
    """A server can expose only a subset and tools are namespaced."""

    server, url = _start_server()
    try:
        from codx.junior.model.model import MCPServer

        settings = _Settings(
            [
                MCPServer(
                    name="CRM Cloud",
                    url=url,
                    active=True,
                    tool_allowlist=["get_customer"],
                )
            ]
        )
        McpRegistry.invalidate()
        report = McpRegistry.server_report(settings)
        assert report[0]["error"] == ""
        assert [tool["name"] for tool in report[0]["tools"]] == ["get_customer"]

        entries = McpRegistry.tool_entries(settings)
        assert len(entries) == 1
        assert entries[0]["tool_json"]["function"]["name"] == "mcp_crm_cloud_get_customer"
        assert entries[0]["tags"][:3] == ["mcp", "CRM Cloud", "external"]

        output = entries[0]["tool_call"](customer_id="c-42")
        assert "c-42" in output
    finally:
        server.shutdown()
        server.server_close()


def test_smol_agent_loads_granted_mcp_tools():
    """The agent adds only the MCP tools the profile granted."""

    server, url = _start_server()
    try:
        from codx.junior.ai.smol.smol_agent import SmolAgent
        from codx.junior.model.model import MCPServer
        from codx.junior.tools import TOOLS

        settings = _Settings([MCPServer(name="CRM Cloud", url=url, active=True)])
        agent = object.__new__(SmolAgent)
        agent.tools = list(TOOLS)
        agent.settings = settings

        McpRegistry.invalidate()
        names = agent._load_mcp_tools({"mcp_crm_cloud_get_customer"})

        assert names == ["mcp_crm_cloud_get_customer"]
        assert any(
            tool["tool_json"]["function"]["name"] == "mcp_crm_cloud_get_customer"
            for tool in agent.tools
        )
    finally:
        server.shutdown()
        server.server_close()


def test_board_only_keeps_mcp_integration_tools():
    """A support actor can work the board and call the helpdesk, not the shell."""

    from types import SimpleNamespace

    from codx.junior.autonomy import constants as C
    from codx.junior.autonomy.models import ActorConfig
    from codx.junior.autonomy.service import ActorService

    service = object.__new__(ActorService)
    config = ActorConfig(enabled=True, policy=C.POLICY_BOARD)
    profile = SimpleNamespace(tools=["project_manager", "mcp_support_reply"])
    allowed = service.tool_allowlist(config, profile)

    assert "project_manager" in allowed
    assert "mcp_support_reply" in allowed
    assert "project_write_file" not in allowed
    assert "docker_exec" not in allowed


def test_chat_engine_wildcard_grants():
    """A profile can grant a whole MCP server with ``mcp_<server>_*``."""

    from codx.junior.chat.chat_engine import ChatEngine

    collected = ["mcp_crm_get_customer", "mcp_crm_create_refund", "docker_exec"]
    assert ChatEngine._apply_tool_allowlist(
        collected, {"mcp_crm_*", "project_manager"}
    ) == ["mcp_crm_get_customer", "mcp_crm_create_refund"]
