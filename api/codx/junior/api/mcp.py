"""
MCP connection API.

Project settings own the persistent ``mcp_servers`` list; this router is the
operational surface used while installing and validating a connection:

* list the discovered tools and the last error of every active server,
* test a connection before saving it,
* force a rediscovery after the server or its credentials change.

The router never runs a model turn and never writes project settings. Saving the
connection stays in the normal project-settings update, so the usual validation
and settings history apply.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from codx.junior.api import get_current_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])


class McpTestRequest(BaseModel):
    """A connection to probe before it is saved."""

    name: str = Field(default="mcp", description="Display name of the server")
    url: str = Field(description="Streamable HTTP MCP endpoint")
    api_key: str = Field(default="", description="Optional Bearer token")
    transport: str = Field(default="http", description="http (streamable HTTP)")
    headers: Dict[str, str] = Field(
        default_factory=dict, description="Extra HTTP headers"
    )
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    protocol_version: str = Field(default="2025-03-26")
    tool_allowlist: List[str] = Field(default_factory=list)


def _settings(request: Request) -> Any:
    """Return the project settings of the request session."""
    session = get_current_session(request)
    settings = getattr(session, "settings", None)
    if settings is None:
        raise HTTPException(
            status_code=400,
            detail="No project in the request: pass the codx_path of a project.",
        )
    return settings


def _server(settings: Any, name: str) -> Any:
    """Resolve one configured active server by name."""
    getter = getattr(settings, "get_mcp_server_by_name", None)
    server = getter(name) if callable(getter) else None
    if server is None:
        raise HTTPException(
            status_code=404, detail=f"MCP server '{name}' is not configured"
        )
    return server


@router.get("/mcp/servers", summary="MCP servers and their discovered tools")
def list_mcp_servers(request: Request) -> List[Dict[str, Any]]:
    """Return every active MCP server with its tools and last error."""
    from codx.junior.mcp.registry import McpRegistry

    return McpRegistry.server_report(_settings(request))


@router.get("/mcp/tools", summary="MCP tool names granted to profiles")
def list_mcp_tools(request: Request) -> Dict[str, Any]:
    """Return the namespaced names a profile can grant."""
    from codx.junior.mcp.registry import McpRegistry

    names = McpRegistry.tool_names(_settings(request))
    return {"tools": names, "count": len(names)}


@router.post("/mcp/servers/{name}/refresh", summary="Rediscover an MCP server")
def refresh_mcp_server(name: str, request: Request) -> Dict[str, Any]:
    """Force tool discovery and return the server report."""
    from codx.junior.mcp.registry import McpRegistry

    settings = _settings(request)
    _server(settings, name)
    McpRegistry.invalidate(settings)
    report = McpRegistry.server_report(settings, refresh=True)
    return next(
        (entry for entry in report if entry.get("name") == name),
        {"name": name, "tools": [], "error": "server is not active"},
    )


@router.post("/mcp/test", summary="Test an MCP connection")
def test_mcp_connection(payload: McpTestRequest, request: Request) -> Dict[str, Any]:
    """
    Initialize a connection and list its tools without saving it.

    This is the "does my CRM MCP work?" button: it validates the URL, auth and
    protocol before the server is added to the project settings.
    """
    from codx.junior.mcp.client import McpError, McpHTTPClient
    from codx.junior.mcp.registry import mcp_tool_name

    _settings(request)  # require a project session
    try:
        with McpHTTPClient(
            payload.url,
            api_key=payload.api_key,
            headers=payload.headers,
            timeout_seconds=payload.timeout_seconds,
            protocol_version=payload.protocol_version,
            transport=payload.transport,
        ) as client:
            result = client.probe()
    except McpError as ex:
        raise HTTPException(status_code=502, detail=str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        raise HTTPException(status_code=502, detail=f"MCP test failed: {ex}")

    allowlist = {str(name) for name in payload.tool_allowlist if str(name).strip()}
    tools = [
        tool
        for tool in result.get("tools") or []
        if not allowlist or str(tool.get("name")) in allowlist
    ]
    return {
        "ok": True,
        "server": result.get("server") or {},
        "protocol_version": result.get("protocol_version") or "",
        "tools": [
            {
                "name": str(tool.get("name") or ""),
                "exposed_name": mcp_tool_name(payload.name, str(tool.get("name") or "")),
                "description": str(tool.get("description") or ""),
            }
            for tool in tools
        ],
    }


# Made with ❤️ by codx-junior
