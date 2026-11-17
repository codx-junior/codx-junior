/**
 * MCP connections API.
 *
 * MCP servers are stored in the project settings (`mcp_servers`); these
 * endpoints discover their tools and validate a connection before it is saved.
 *
 *   GET  /api/mcp/servers              configured servers + discovered tools
 *   GET  /api/mcp/tools                namespaced tool names for profile grants
 *   POST /api/mcp/test                 probe a connection without saving it
 *   POST /api/mcp/servers/{name}/refresh  rediscover one saved server
 */

export const mcpModule = (API) => ({
  /** Configured MCP servers with tool counts, names and last error. */
  servers() {
    return API.get('/api/mcp/servers')
  },

  /** Namespaced tool names (`mcp_<server>_<tool>`) available to profiles. */
  tools() {
    return API.get('/api/mcp/tools')
  },

  /** Probe a connection before saving it. */
  test(connection) {
    return API.post('/api/mcp/test', connection)
  },

  /** Force discovery for one saved server. */
  refresh(name) {
    return API.post(
      `/api/mcp/servers/${encodeURIComponent(name)}/refresh`,
      {}
    )
  }
})
