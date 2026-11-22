# Architecture

codx-junior runs as **one application container**. Traefik is optional and lives
only at the edge (server mode); the container serves the SPA, the API and the
background worker through supervisord.

```
browser ──► Traefik (optional edge, :80/:443, ACME)
                │
                ▼
        codx-junior container :19981 (nginx)
                ├──► /            compiled Vue SPA
                └──► /api         FastAPI + Socket.IO :19980
                                    ├── background worker (same image)
                                    ├── embedded LanceDB
                                    └── Docker socket (optional: coworking/desktops)

dev only: the same compose with the checkout mounted (dev-install.sh)
```

## Services

| Service | Role |
| --- | --- |
| `codx-junior` | The application container: nginx (SPA + `/api` proxy), FastAPI, background worker and embedded LanceDB. |
| `traefik` | Optional edge proxy in `server` mode: TLS (Let's Encrypt), the public domain(s) and custom routes from `/api/traefik/config`. |
| `dind` | Optional wrapper in `dind` mode: a nested Docker daemon with one isolated stack per container. |

## Backend modules

The API code lives in `api/codx/junior`:

| Module | What it does |
| --- | --- |
| `chat/`, `chat_manager.py` | Chat engine, chat storage, chat modes and the bridge to Socket.IO events. |
| `ai/` | Provider clients (OpenAI-compatible), the tool-calling agent loop (`ai/smol`), cancellation, raw AI logs and wallet checks. |
| `tools/` | Tools agents can call: project search, read/write files, apply patches, web fetch and DuckDuckGo search, images, Docker and Docker Compose, Git, tasks, tutorials and recipes. |
| `agents/` | Specialised agents such as the GitHub issues agent. |
| `knowledge/`, `engine/knowledge_engine.py` | Code splitting, embeddings, LanceDB storage, AI search and knowledge graph. |
| `profiles/` | Profile manager and the built-in profiles. |
| `mentions/` | `@codx` mention detection and processing in project files. |
| `engine/git_engine.py`, `api/git.py` | Branches, commits, diffs and pull-request views. |
| `wiki/` | AI-maintained project wiki. |
| `workspaces/` | Workspace templates, Docker Compose generation and lifecycle. |
| `analytics/` | Token and cxjcoin accounting, usage queries and dashboards. |
| `security/` | Users, roles, permissions, API keys and GitHub OAuth. |
| `plugins/` | Plugin loading and execution. |

## Frontend

The client (`client/src`) is organised by feature under `components/`: `chat`, `kanban`, `vibe`, `workspaces`, `teams`, `messenger`, `knowledge`, `repo`, `wiki`, `analytics`, `security`, `ai_settings`, `global_settings` and more. State lives in Vuex stores (`store/`), and the main routes are:

| Route | View |
| --- | --- |
| `/` and `/chats/...` | Quick chat: the launcher home with recent chats and workspace apps |
| `/kanban` | Kanban boards |
| `/workspaces/...` | Workspaces |
| `/messenger` | Teams and messenger |
| `/desktop/...` | Desktop with windows and tabs |
| `/analytics` | Usage and cost dashboard |
