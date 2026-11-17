"""
Project-scoped MCP tool registry.

The registry turns the ``mcp_servers`` list of a project into tool entries the
agent runtime understands:

* discovery is cached for a short TTL, so a CRM is not queried on every model
  round,
* a broken server never breaks the chat, it simply contributes no tools,
* tools are namespaced as ``mcp_<server>_<tool>`` so two servers can expose the
  same native name,
* a server's ``tool_allowlist`` is enforced before the model ever sees a tool.

The entry format is the same one used by :data:`codx.junior.tools.TOOLS`::

    {"tool_json": {...}, "settings": {...}, "tags": [...], "tool_call": fn}

The callable is synchronous and is executed by ``SmolAgent`` in its worker
thread; MCP requests therefore have a hard timeout and cannot block the event
loop.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from functools import partial
from typing import Any, Dict, Iterable, List, Optional, Set

from codx.junior.tools.model import ToolSettings

from .client import McpError, McpHTTPClient

logger = logging.getLogger(__name__)

#: How long a successful tool discovery remains valid.
DISCOVERY_TTL_SECONDS = 120.0

#: How long a failed discovery is remembered (avoids retrying a dead server on
#: every agent round while still recovering quickly).
FAILURE_TTL_SECONDS = 30.0

#: Discovery happens while the agent request is assembled, so it gets a shorter
#: timeout than a normal tool call. A slow CRM must not stall every chat turn.
DISCOVERY_TIMEOUT_SECONDS = 8.0

#: Prefix of every MCP tool exposed to the model.
TOOL_PREFIX = "mcp_"

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.RLock()


def _field(server: Any, name: str, default: Any = "") -> Any:
    """Read a field from an MCPServer model or a plain mapping."""
    if isinstance(server, dict):
        value = server.get(name, default)
    else:
        value = getattr(server, name, default)
    return default if value is None else value


def _slug(value: Any, limit: int = 40) -> str:
    """Return a stable, OpenAI-safe tool/server name fragment."""
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_").lower()
    return (text or "server")[:limit]


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """
    Return the model-facing name of an MCP tool.

    The native tool name is kept readable when it fits; long combinations get a
    short deterministic suffix so two tools never silently collide. OpenAI
    function names are limited to 64 characters.
    """
    base = f"{TOOL_PREFIX}{_slug(server_name, 24)}_{_slug(tool_name, 36)}"
    if len(base) <= 64:
        return base
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    return f"{base[:55]}_{digest}"


def _server_fingerprint(server: Any) -> str:
    """Return a cache key that changes when the connection changes."""
    payload = {
        "name": _field(server, "name"),
        "url": _field(server, "url"),
        "transport": _field(server, "transport", "http"),
        "api_key": _field(server, "api_key", ""),
        "headers": _field(server, "headers", {}),
        "protocol_version": _field(server, "protocol_version", "2025-03-26"),
        "tool_allowlist": _field(server, "tool_allowlist", []),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _cache_key(settings: Any, server: Any) -> str:
    """Return the per-project cache key of one server."""
    return (
        f"{getattr(settings, 'codx_path', '')}:"
        f"{_field(server, 'name')}:{_server_fingerprint(server)}"
    )


def _tool_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an MCP tool description into an OpenAI function schema."""
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    elif schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    name = str(tool.get("name") or "").strip()
    description = str(tool.get("description") or "").strip()
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"MCP tool '{name}'",
            "parameters": schema,
        },
    }


class McpRegistry:
    """Discover and build the MCP tools of one project."""

    # -- discovery --------------------------------------------------------

    @classmethod
    def _active_servers(cls, settings: Any) -> List[Any]:
        """Return the active MCP servers of *settings* (best effort)."""
        getter = getattr(settings, "get_active_mcp_servers", None)
        if not callable(getter):
            return []
        try:
            return [server for server in (getter() or []) if _field(server, "active", True)]
        except Exception as ex:  # pragma: no cover - configuration is external
            logger.warning("MCP: cannot read active servers: %s", ex)
            return []

    @classmethod
    def _discover_server(cls, server: Any) -> List[Dict[str, Any]]:
        """Query one server and return its allowed tools."""
        try:
            with McpHTTPClient(
                str(_field(server, "url")),
                api_key=str(_field(server, "api_key", "")),
                headers=_field(server, "headers", {}) or {},
                timeout_seconds=min(
                    float(_field(server, "timeout_seconds", 30.0)),
                    DISCOVERY_TIMEOUT_SECONDS,
                ),
                protocol_version=str(
                    _field(server, "protocol_version", "2025-03-26")
                ),
                transport=str(_field(server, "transport", "http")),
            ) as client:
                result = client.probe()
        except McpError:
            raise
        except Exception as ex:  # pragma: no cover - defensive
            raise McpError(str(ex)) from ex

        allowlist = {
            str(name)
            for name in (_field(server, "tool_allowlist", []) or [])
            if str(name).strip()
        }
        tools: List[Dict[str, Any]] = []
        for tool in result.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            if allowlist and name not in allowlist:
                continue
            tools.append(tool)
        return tools

    @classmethod
    def discover(
        cls, settings: Any, *, refresh: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """
        Return ``{server_name: {server, tools, error}}`` for a project.

        A failed server is represented with an ``error`` and an empty tool list;
        it never raises into the chat.
        """
        result: Dict[str, Dict[str, Any]] = {}
        now = time.monotonic()
        for server in cls._active_servers(settings):
            name = str(_field(server, "name") or "server").strip()
            key = _cache_key(settings, server)
            with _CACHE_LOCK:
                cached = _CACHE.get(key)
            ttl = DISCOVERY_TTL_SECONDS
            if cached and cached.get("error"):
                ttl = FAILURE_TTL_SECONDS
            if not refresh and cached and (now - cached["at"]) < ttl:
                result[name] = {
                    "server": server,
                    "tools": cached.get("tools") or [],
                    "error": cached.get("error") or "",
                }
                continue

            error = ""
            tools: List[Dict[str, Any]] = []
            try:
                tools = cls._discover_server(server)
                logger.info(
                    "MCP: discovered %d tool(s) on '%s' (%s)",
                    len(tools),
                    name,
                    _field(server, "url"),
                )
            except Exception as ex:
                error = str(ex)
                logger.warning("MCP: server '%s' unavailable: %s", name, error)
            with _CACHE_LOCK:
                _CACHE[key] = {
                    "at": now,
                    "tools": tools,
                    "error": error,
                }
            result[name] = {"server": server, "tools": tools, "error": error}
        return result

    @classmethod
    def invalidate(cls, settings: Any = None) -> None:
        """Drop the discovery cache (all projects, or one project)."""
        with _CACHE_LOCK:
            if settings is None:
                _CACHE.clear()
                return
            prefix = f"{getattr(settings, 'codx_path', '')}:"
            for key in [key for key in _CACHE if key.startswith(prefix)]:
                _CACHE.pop(key, None)

    # -- tool entries -----------------------------------------------------

    @classmethod
    def _call_tool(
        cls,
        *,
        server: Any,
        native_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """Execute one MCP tool with the configured server credentials."""
        payload: Dict[str, Any] = dict(arguments or {})
        payload.update(kwargs)
        with McpHTTPClient(
            str(_field(server, "url")),
            api_key=str(_field(server, "api_key", "")),
            headers=_field(server, "headers", {}) or {},
            timeout_seconds=float(_field(server, "timeout_seconds", 30.0)),
            protocol_version=str(_field(server, "protocol_version", "2025-03-26")),
            transport=str(_field(server, "transport", "http")),
        ) as client:
            client.initialize()
            return client.call_tool(native_name, payload)

    @classmethod
    def tool_entries(
        cls,
        settings: Any,
        *,
        allowed: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build the tool entries available to the model.

        Args:
            settings: Project settings (provides the MCP server list).
            allowed: Optional set of model-facing tool names to include. This is
                the profile grant intersected with the discovered tools.

        Returns:
            Tool entries in the ``TOOLS`` registry format.
        """
        allowed_names = {str(name) for name in allowed} if allowed is not None else None
        allowed_prefixes = {
            name[:-1] for name in (allowed_names or set()) if name.endswith("*")
        }
        entries: List[Dict[str, Any]] = []
        for server_name, info in cls.discover(settings).items():
            if info.get("error"):
                continue
            server = info["server"]
            timeout = float(_field(server, "timeout_seconds", 30.0))
            for tool in info.get("tools") or []:
                native = str(tool.get("name") or "")
                if not native:
                    continue
                exposed = mcp_tool_name(server_name, native)
                if allowed_names is not None and exposed not in allowed_names:
                    if not any(exposed.startswith(prefix) for prefix in allowed_prefixes):
                        continue
                schema = _tool_schema(tool)
                schema["function"]["name"] = exposed
                entries.append(
                    {
                        "tool_json": schema,
                        "settings": ToolSettings(
                            scope="chat",
                            timeout_seconds=timeout,
                            cache=False,
                        ).dict(),
                        "tags": ["mcp", str(server_name), "external"],
                        "tool_call": partial(
                            cls._call_tool,
                            server=server,
                            native_name=native,
                        ),
                    }
                )
        return entries

    @classmethod
    def tool_names(cls, settings: Any) -> List[str]:
        """Return every discovered, allowlisted MCP tool name."""
        names: List[str] = []
        for server_name, info in cls.discover(settings).items():
            if info.get("error"):
                continue
            for tool in info.get("tools") or []:
                native = str(tool.get("name") or "")
                if native:
                    names.append(mcp_tool_name(server_name, native))
        return sorted(dict.fromkeys(names))

    @classmethod
    def server_report(cls, settings: Any, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """Return the UI/API shape of every active MCP server."""
        report: List[Dict[str, Any]] = []
        for name, info in cls.discover(settings, refresh=refresh).items():
            server = info["server"]
            report.append(
                {
                    "name": name,
                    "url": _field(server, "url"),
                    "transport": _field(server, "transport", "http"),
                    "active": bool(_field(server, "active", True)),
                    "tool_allowlist": list(_field(server, "tool_allowlist", []) or []),
                    "tools": [
                        {
                            "name": str(tool.get("name") or ""),
                            "exposed_name": mcp_tool_name(name, str(tool.get("name") or "")),
                            "description": str(tool.get("description") or ""),
                        }
                        for tool in info.get("tools") or []
                    ],
                    "error": info.get("error") or "",
                }
            )
        return report


# Made with ❤️ by codx-junior
