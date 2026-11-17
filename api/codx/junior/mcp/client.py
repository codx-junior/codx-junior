"""
Minimal MCP client over streamable HTTP.

The implementation is intentionally synchronous: tool calls in ``SmolAgent``
already run in a worker thread with a hard timeout, and discovery happens while
the agent request is being assembled. Avoiding a second event loop keeps the
client usable from both the API and the background worker.

Protocol
--------
MCP is JSON-RPC 2.0. The client performs the three calls needed by an agent::

    initialize
    notifications/initialized
    tools/list
    tools/call

The server may answer with ``application/json`` or with a finite
``text/event-stream`` response; both are supported. The optional
``Mcp-Session-Id`` response header is echoed on every later request.

Boundaries
----------
* Only ``http``/``https`` URLs are accepted.
* ``transport="stdio"`` and legacy SSE are rejected explicitly instead of
  silently doing something surprising. Use a streamable HTTP bridge for a
  coworking-space service.
* Every request has a timeout; a broken MCP server never hangs a shift.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
from itertools import count
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

CLIENT_NAME = "codx-junior"
CLIENT_VERSION = "1.0"


class McpError(RuntimeError):
    """An MCP server could not be reached or returned an invalid response."""


def _header_value(headers: Any, name: str) -> str:
    """Return a response header by case-insensitive name."""
    for key, value in (headers or {}).items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def _jsonrpc_error(data: Dict[str, Any]) -> str:
    """Render a JSON-RPC error object as a readable message."""
    error = data.get("error")
    if not isinstance(error, dict):
        return ""
    code = error.get("code")
    message = error.get("message") or "unknown MCP error"
    return f"MCP error {code}: {message}"


def _sse_messages(text: str, request_id: Any) -> Optional[Dict[str, Any]]:
    """
    Return the JSON-RPC response with *request_id* from an SSE body.

    Streamable HTTP servers send one or more ``data:`` events. Notifications are
    ignored; the response carrying our request id is the one we need. A second
    pass accepts a response without an id (some minimal servers do that).
    """
    fallback: Optional[Dict[str, Any]] = None
    for block in (text or "").replace("\r\n", "\n").split("\n\n"):
        payloads: List[str] = []
        for line in block.split("\n"):
            if line.startswith("data:"):
                payloads.append(line[5:].lstrip())
        if not payloads:
            continue
        raw = "\n".join(payloads).strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if "id" not in message:
            continue
        if message.get("id") == request_id:
            return message
        if fallback is None and ("result" in message or "error" in message):
            fallback = message
    return fallback


class McpHTTPClient:
    """
    One short-lived MCP session against a streamable HTTP endpoint.

    Args:
        url: Streamable HTTP endpoint.
        api_key: Optional Bearer token.
        headers: Extra headers (merged after the default ones).
        timeout_seconds: Request timeout.
        protocol_version: MCP protocol version to advertise.
        transport: ``http`` only; ``sse``/``stdio`` raise a clear error.
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: str = "",
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: float = 30.0,
        protocol_version: str = "2025-03-26",
        transport: str = "http",
    ) -> None:
        self.url = str(url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.extra_headers = dict(headers or {})
        self.timeout_seconds = max(1.0, float(timeout_seconds or 30.0))
        self.protocol_version = str(protocol_version or "2025-03-26")
        self.transport = (transport or "http").strip().lower()
        self.session_id = ""
        self._ids = count(1)
        self._client: Optional[httpx.Client] = None
        if not self.url:
            raise McpError("MCP server URL is empty")
        if not self.url.lower().startswith(("http://", "https://")):
            raise McpError(
                f"MCP URL must be http:// or https://, got {self.url!r}"
            )
        if self.transport not in ("http", "streamable_http", "streamable-http"):
            raise McpError(
                f"MCP transport '{self.transport}' is not supported by this "
                "client; use streamable HTTP ('transport': 'http')."
            )

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "McpHTTPClient":
        self._client = httpx.Client(
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=True,
        )
        return self

    def __exit__(self, *_: Any) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        """Return the HTTP client, creating it for direct (non-context) use."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self.__exit__()

    # -- low level --------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        """Build request headers, including auth and the MCP session id."""
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(
            {str(key): str(value) for key, value in self.extra_headers.items()}
        )
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, payload: Dict[str, Any], *, expect_response: bool = True) -> Dict[str, Any]:
        """POST one JSON-RPC message and return the decoded response."""
        try:
            response = self.client.post(
                self.url, json=payload, headers=self._headers()
            )
        except httpx.HTTPError as ex:
            raise McpError(f"MCP request failed: {ex}") from ex

        session_id = _header_value(response.headers, "Mcp-Session-Id")
        if session_id:
            self.session_id = session_id

        if response.status_code >= 400:
            excerpt = (response.text or "")[:500]
            raise McpError(
                f"MCP server returned HTTP {response.status_code}: {excerpt}"
            )

        if not expect_response or not (response.content or b"").strip():
            return {}

        content_type = _header_value(response.headers, "Content-Type").lower()
        if "text/event-stream" in content_type:
            message = _sse_messages(response.text, payload.get("id"))
            if message is None:
                raise McpError("MCP SSE response did not contain a JSON-RPC result")
            return message

        try:
            message = response.json()
        except ValueError as ex:
            raise McpError(
                f"MCP server returned non-JSON content: {(response.text or '')[:300]}"
            ) from ex
        if not isinstance(message, dict):
            raise McpError("MCP server returned a non-object JSON-RPC response")
        return message

    @staticmethod
    def _result(message: Dict[str, Any]) -> Dict[str, Any]:
        """Return the JSON-RPC result or raise a readable MCP error."""
        error = _jsonrpc_error(message)
        if error:
            raise McpError(error)
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    # -- protocol ---------------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        """Negotiate the MCP session; returns the server's initialize result."""
        request_id = next(self._ids)
        message = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": CLIENT_NAME,
                        "version": CLIENT_VERSION,
                    },
                },
            }
        )
        result = self._result(message)
        negotiated = str(result.get("protocolVersion") or "").strip()
        if negotiated:
            self.protocol_version = negotiated
        # The initialized notification has no id and must not expect a body.
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
        )
        return result

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return every tool advertised by the server (paginated)."""
        tools: List[Dict[str, Any]] = []
        cursor = ""
        for _ in range(20):
            request_id = next(self._ids)
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            message = self._post(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": params,
                }
            )
            result = self._result(message)
            page = result.get("tools")
            if isinstance(page, list):
                tools.extend([tool for tool in page if isinstance(tool, dict)])
            cursor = str(result.get("nextCursor") or "")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        """Call one MCP tool and render its content as text for the model."""
        request_id = next(self._ids)
        message = self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": str(name), "arguments": dict(arguments or {})},
            }
        )
        result = self._result(message)
        if result.get("isError"):
            return f"MCP tool '{name}' reported an error"
        chunks: List[str] = []
        content = result.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if text:
                    chunks.append(str(text))
                elif block.get("type") == "image":
                    chunks.append("[image content omitted]")
                elif block.get("type") == "resource":
                    resource = block.get("resource") or {}
                    chunks.append(str(resource.get("text") or "[resource]"))
        structured = result.get("structuredContent")
        if structured is not None:
            chunks.append(
                structured
                if isinstance(structured, str)
                else json.dumps(structured, ensure_ascii=False)
            )
        if not chunks:
            chunks.append(json.dumps(result, ensure_ascii=False))
        return "\n".join(chunks)

    def probe(self) -> Dict[str, Any]:
        """Initialize and return the server's capabilities and tools."""
        info = self.initialize()
        tools = self.list_tools()
        return {
            "server": info.get("serverInfo") or {},
            "protocol_version": info.get("protocolVersion") or self.protocol_version,
            "tools": tools,
        }


# Made with ❤️ by codx-junior
