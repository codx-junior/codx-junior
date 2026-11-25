# Features

**0.0.1 ships a focused set: 17 features, not 49.** This page separates what the
public release actually enables from the rest of the platform, which stays in
the codebase behind the install-time feature list and is turned on feature by
feature.

::: tip The 0.0.1 promise
Install one container, connect one model, add one repository, and ship a
reviewed change. Everything below under **Available in 0.0.1** is part of that
loop. Everything under **Roadmap** is documented in advance but hidden in the
release.
:::

The full release scope — usefulness score, hide decision and the fixes each
feature needs — is in the
[0.0.1 feature matrix](https://github.com/codx-junior/codx-junior/blob/main/docs/product/v0.0.1-feature-matrix.md).

## Available in 0.0.1

### Get started

| Feature | Summary |
| --- | --- |
| [Install and first run](/getting-started) | One container, one provider, one project; local, server and DIND modes. |
| [Initial setup](/initial-setup) | Admin, workspace, model connection and the first project. |
| [AI providers and models](/features/ai-providers) | Any OpenAI-compatible provider, with a model per purpose. |
| [Configuration](/configuration) | Installer variables, project settings, Git identity and environment. |

### Build

| Feature | Summary |
| --- | --- |
| [Chats and agents](/features/chats-and-agents) | Project-aware chats with modes, attachments and a tool-using agent loop. |
| [Profiles](/features/profiles) | Reusable instructions that pin the model, tools, knowledge and coding standards. |
| [Knowledge (RAG)](/features/knowledge) | Projects indexed into embedded LanceDB so the agent finds the right context first. |
| [Vibe coding](/features/vibe-coding) | The conversation and the live file changes side by side. |
| [File explorer](/features/development-tools) | Browse the repository and open files without leaving the workspace. |

### Plan and ship

| Feature | Summary |
| --- | --- |
| [Kanban and tasks](/features/kanban) | A work queue where every card is a chat with sub-tasks, checklists and assignees. |
| [Git and code review](/features/code-review) | Status, branches, unified diffs, commit, PR view and history. |

### Extend and operate

| Feature | Summary |
| --- | --- |
| [API](/features/api) | REST, Socket.IO and the OpenAI-compatible endpoint, with OpenAPI docs. |
| [Login and admin access](/initial-setup) | Single-admin login and session; multi-user roles and invitations are on the roadmap. |
| Responsive mobile shell, themes and languages | Works on a phone; the desktop workspace is the primary target. |

## Roadmap — hidden in 0.0.1

These pages document the wider platform. **They are not enabled in the public
release.** They remain in the tree so 0.1 can enable them without forking the
product; each one has a "before enabling" note in the
[feature matrix](https://github.com/codx-junior/codx-junior/blob/main/docs/product/v0.0.1-feature-matrix.md).

### Multi-user and teams

| Feature | Why it is hidden |
| --- | --- |
| [Users, roles and invitations](/features/users-and-security) | User creation and project grants need a tested admin flow; 0.0.1 is single-developer. |
| [Teams and messenger](/features/teams) | Needs multi-user and moderation before a team can rely on it. |
| [Company assistant](/features/company-assistant) | A second assistant next to the project chat adds confusion until the core chat is proven. |
| [Company meetings](/features/company-meetings) | Live calls, recording and transcription are a separate product surface. |
| [Calendar and scheduling](/features/company-meetings) | Supports meetings and autonomous runs; both hidden. |

### Autonomous work

| Feature | Why it is hidden |
| --- | --- |
| [Autonomous agents (actors)](/features/autonomous-agents) | High-risk autonomy; needs budgets, pause switches and an audit trail first. |
| [Autonomous tickets](/features/autonomous-tickets) | Depends on the agents and the ticket lifecycle. |
| [Company tickets](/features/company-tickets) | The unattended delivery workbench; kanban covers planning in 0.0.1. |
| [Company files and coworking spaces](/features/company-spaces) | Needs the workspace backend and a clear project-vs-company file model. |

### Run and collaborate

| Feature | Why it is hidden |
| --- | --- |
| [Workspaces (dev containers)](/features/workspaces) | Requires the Docker socket and is the highest-risk surface for a first release. |
| [Computer use](/features/computer-use) | Privileged desktops and VMs; needs a security boundary and quotas. |
| [Browser use and co-browsing](/features/browser-use) | Needs the Docker socket and Chromium images; lower demo value than the repo loop. |
| [Development tools](/features/development-tools) | The desktop window manager and infrastructure panel are advanced surfaces. |

### Context and automation

| Feature | Why it is hidden |
| --- | --- |
| [Wiki](/features/wiki) | No wiki is created by default and generation needs a configured model. |
| [@codx mentions](/features/mentions) | A background file watcher that writes in place; needs a visible undo. |
| [Suggested replies](/features/suggested-replies) | Nice-to-have, model-dependent, outside the first-run path. |
| [Voice conversation](/features/voice-conversation) | Microphone permissions and latency make it a poor first demo. |
| [DSH task sync](/features/dsh-sync) | External harness integration with `ok: false` on a fresh install. |
| [codx-junior harness](/features/codx-junior-harness) | A second execution model; the project chat is the 0.0.1 path. |

### Control and integrate

| Feature | Why it is hidden |
| --- | --- |
| [CISO](/features/ciso) | A whole security workflow; no scan has run on a fresh install. |
| [Budget and analytics](/features/budget-and-analytics) | Support/ops surface; pricing tables are empty on a fresh install. |
| [MCP connections](/features/mcp-connections) | Strong value, but needs connection tests, grants and a secrets story. |
| [Plugins](/features/plugins) | Arbitrary code execution before the core is stable. |
| [Channel content creator](/features/channel-creator) | Marketing content generation, not a developer feature. |
| [External actors and outreach](/features/external-actors) | Email deliverability and consent are a separate product. |
| [Slack and WhatsApp channels](/features/slack-whatsapp-integration) | Design only; no runtime integration exists. |
