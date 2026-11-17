# MCP connections

A **connection** gives a project's profiles access to a business system through
the Model Context Protocol: a CRM, a helpdesk, a billing system, an internal
admin API or a tool you build yourself.

codx-junior does not need a new code path for every SaaS product. You install the
tool where it belongs (for example a coworking-space container), expose it as a
streamable HTTP MCP endpoint, register the endpoint in the project and grant its
tools to the profiles that should use them.

::: tip One base, many companies
codx-junior is the **base**: profiles, actors, teams, boards, schedules, budgets
and the MCP bridge are shared. Each company defines its own projects,
connections and profiles on top. The core does not need to know what a CRM is.
:::

## Register a connection

Open **Project settings → MCP servers** and add:

| Field | Meaning |
| --- | --- |
| `name` | Stable short name used in tool names (`crm`, `support`, `billing`). |
| `url` | Streamable HTTP MCP endpoint, e.g. `http://crm-mcp:8080/mcp`. |
| `api_key` | Optional Bearer token. Keep the real credential in the MCP service/secret store. |
| `active` | Whether profiles may see this server. |
| `tool_allowlist` | Optional native tool names to expose. Empty means every tool. |
| `transport` | `http` (streamable HTTP). |
| `timeout_seconds` | Per-request timeout. |

Use **Test connection** before saving. The backend initializes the MCP session,
lists the tools and returns their names; nothing is written and no model turn is
started.

The same probe is available from the API:

```bash
curl -X POST "$CODX/api/mcp/test?codx_path=/projects/app/.codx" \
  -H 'Content-Type: application/json' \
  -d '{"name":"crm","url":"http://crm-mcp:8080/mcp","api_key":"..."}'
```

## Tools become profile grants

A discovered native tool is exposed under a namespaced name:

```text
mcp_<server>_<tool>
```

For example, `get_customer` on the `crm` server becomes:

```text
mcp_crm_get_customer
```

Open a profile and grant it in the profile's **tools** list, exactly like a
built-in tool. A whole server can be granted with a prefix:

```text
mcp_crm_*
```

The agent only discovers and loads MCP tools for profiles whose grant contains
an `mcp_` entry. A project can therefore register a CRM without every profile
seeing it.

## Board-only actors

A board-only actor keeps the board/task tools and may also use the MCP tools
explicitly granted to its profile:

```json
{
  "tools": ["project_manager", "mcp_support_get_ticket", "mcp_support_reply"],
  "actor": {
    "enabled": true,
    "policy": "board_only",
    "boards": ["Support"],
    "schedule": { "enabled": true, "timezone": "Europe/Madrid", "windows": [] },
    "daily_budget_cxjcoins": 20
  }
}
```

This is the support pattern: the actor can work its board and call the helpdesk,
but it never receives the shell, git, file or browser tools.

## Example: a CRM as a coworking space

1. Install the CRM service in a workspace or a container with its own database.
2. Run its MCP bridge next to it. The bridge exposes typed business tools:
   `get_customer`, `list_opportunities`, `create_note`, `create_task`.
3. Register the bridge URL in the relevant project (for example
   `cloud-sales`), not in the product project.
4. Test the connection and copy the namespaced tool names.
5. Grant the read tools to the sales profile and only the write tools to the
   profile that is allowed to change the CRM:
   - `GrowthLead`: `mcp_crm_get_customer`, `mcp_crm_list_opportunities`
   - `SalesOps`: `mcp_crm_create_note`, `mcp_crm_create_task`
6. Add a schedule and a daily budget to each actor.

The same flow applies to a helpdesk, a billing system, an email gateway or a
custom internal admin API.

## Security boundaries

- **Credentials stay with the MCP service.** The `${...}` secret reference and
  the Bearer token are for the bridge, not for every profile.
- **The allowlist is enforced before the model sees a tool.** A tool the server
  exposes but the connection does not allow never reaches a profile.
- **Server credentials are project-scoped.** A sales project does not inherit
  the billing project's connection.
- **Use typed business tools, not a generic `http_request`.** A generic HTTP tool
  becomes an SSRF/exfiltration primitive.
- **High-risk writes still need an approval gate.** The current bridge can
  restrict tools, but it does not yet pause a shift for a human approval. Keep
  refunds, plan changes, production deploys and destructive operations in a
  profile whose connection is read-only, or use a separate human-approved
  workspace action until the approval layer lands.
- **Never expose a profile directly to customers.** In particular, do not mark a
  support profile as an OpenAI-compatible model (`api_settings.active`) and do
  not route customers to `/api/v1/*`. Build a support intake that creates a
  board task; the support actor works internally.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/mcp/servers` | Configured servers, discovered tools and last error. |
| `GET /api/mcp/tools` | Namespaced names available for profile grants. |
| `POST /api/mcp/test` | Probe a connection without saving it. |
| `POST /api/mcp/servers/{name}/refresh` | Force rediscovery of a saved server. |

Project settings still own the persistent `mcp_servers` list.

## Current limits

- Streamable HTTP is implemented; stdio and the legacy SSE transport are
  rejected explicitly. Use a streamable HTTP bridge for a coworking-space
  service.
- Tool discovery is cached for 120 seconds (30 seconds after a failure).
- The connection is registered manually. A catalog/installer that can create the
  workspace service and register its MCP endpoint automatically is the next
  layer, not part of this first bridge.
- Approval gates for MCP write tools are not implemented yet; restrict the tool
  grant or keep the action human-driven until they are.

## Tests

```bash
cd api && pytest tests/test_mcp.py -q
```

The offline suite starts a stub MCP server, negotiates a session, lists tools,
enforces the allowlist, invokes a tool, loads the grant into `SmolAgent` and
checks that a board-only actor keeps its MCP integration tools while losing
shell/file tools.
