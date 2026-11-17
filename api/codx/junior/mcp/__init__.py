"""
Project-scoped MCP connections.

A codx-junior project can register Model Context Protocol servers in its
settings (Project settings → MCP servers). The runtime discovers their tools and
exposes them to profiles under namespaced names::

    mcp_<server>_<tool>

This package is the bridge between the project configuration and the agent
runtime:

* :mod:`.client` — JSON-RPC over streamable HTTP / SSE.
* :mod:`.registry` — discovers tools, caches them, and builds OpenAI function
  entries with a callable wrapper.

MCP servers are a **business integration surface**, not a code-execution
surface. Keep each server's credentials scoped to its domain and grant only the
tools each profile needs.

Made with ❤️ by codx-junior
"""

from .client import McpError, McpHTTPClient
from .registry import McpRegistry, mcp_tool_name

__all__ = [
    "McpError",
    "McpHTTPClient",
    "McpRegistry",
    "mcp_tool_name",
]

# Made with ❤️ by codx-junior
