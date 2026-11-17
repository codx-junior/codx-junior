class MCPServer {
  constructor(data = {}) {
    this.id = data.id || null
    this.name = data.name || ''
    this.url = data.url || ''
    this.api_key = data.api_key || ''
    this.active = data.active !== undefined ? data.active : true
    this.transport = data.transport || 'http'
    this.tool_allowlist = Array.isArray(data.tool_allowlist)
      ? [...data.tool_allowlist]
      : []
    this.headers = data.headers && typeof data.headers === 'object'
      ? { ...data.headers }
      : {}
    this.timeout_seconds = Number(data.timeout_seconds || 30)
    this.protocol_version = data.protocol_version || '2025-03-26'
    this.command = data.command || ''
    this.args = Array.isArray(data.args) ? [...data.args] : []
  }

  isValid() {
    return Boolean(this.name && this.url)
  }

  toJSON() {
    return {
      id: this.id,
      name: this.name,
      url: this.url,
      api_key: this.api_key,
      active: this.active,
      transport: this.transport,
      tool_allowlist: this.tool_allowlist,
      headers: this.headers,
      timeout_seconds: this.timeout_seconds,
      protocol_version: this.protocol_version,
      command: this.command,
      args: this.args
    }
  }

  static fromJSON(data) {
    return new MCPServer(data)
  }
}

export default MCPServer
