# Configuration

Most settings are edited in the app (**Global settings** and **Project settings**). The variables below configure the services themselves.

## `.env` (installer)

| Variable | Description |
| --- | --- |
| `CODX_JUNIOR_DOMAIN` | Public host name (server/slave modes). |
| `CODX_JUNIOR_WEB_PORT` | Host port for the UI (`19981` by default). |
| `CODX_JUNIOR_PROJECTS_PATH` | Host folder that holds the projects. |
| `CODX_JUNIOR_AI_PROVIDER` | Provider preset (`openai`, `deepseek`, `gemini`, `openrouter`, `mistral`, `groq`, `custom`). |
| `CODX_JUNIOR_AI_API_KEY` | Provider key. |
| `CODX_JUNIOR_AI_API_URL` | Endpoint for a `custom` provider. |
| `CODX_ACME_EMAIL` | Email for Let's Encrypt certificates (server mode). |
| `CODX_JUNIOR_KNOWLEDGE_BACKEND` | `lancedb` (default) or `none`. |
| `CODX_JUNIOR_COWORKING` | `1` mounts the Docker socket for coworking/desktops. |
| `CODX_JUNIOR_HIDDEN` | Hides codx-junior's own project from the projects list. |

## API service

| Variable | Description |
| --- | --- |
| `CODX_JUNIOR_CONFIG_FOLDER` | Folder where global settings, users and chats are stored. |
| `CODX_JUNIOR_PROJECTS_PATH` | Root folder for cloned projects. |
| `CODX_JUNIOR_API_URL` | URL the background worker uses to reach the API. |
| `CODX_JUNIOR_API_BACKGROUND` | Set to `1` to run the process as the background worker. |
| `CODX_JUNIOR_API_VENV` | Python virtual environment used by the API. |
| `CODX_JUNIOR_API_LOGS` | Folder for API logs. |
| `CODX_JUNIOR_AI_RAW_LOG_PATH` | Folder for raw AI request and response logs. |
| `CODX_JUNIOR_API_ANALYTICS_DATA_PATH` | Folder for usage analytics data. |
| `CODX_JUNIOR_ADMIN_USER`, `CODX_JUNIOR_ADMIN_PASSWORD`, `CODX_JUNIOR_ADMIN_EMAIL` | First admin created on the first boot of an unattended install (password bcrypt-hashed, never logged). The email also works as a login identifier. |
| `CODX_JUNIOR_WORKSPACE_TOKEN` | Per-instance scoped API key a hosted console uses to act on the workspace without the owner's password. Accepted as `Authorization: Bearer <token>`; rotate it by changing the value and restarting the API. |

The machine-client contract (accepted auth headers, `codx_path` rules, login by
email and the `/api/health` capability block) is documented in
`codx-junior-installer/cloud/DEPLOYMENT-CONTRACT.md` §5b and §7b.

## Global settings

Edited by admins in **Settings → Global settings**. Every change is versioned, so you can see the history of a section and roll it back.

* **AI providers and models** and the default model for each purpose.
* **Agent settings**, such as the maximum number of agent iterations.
* **Chat global instructions** added to every conversation.
* **Users**, roles, permissions, wallets and token limits.
* **OAuth providers** (GitHub).
* **Workspaces** and the port range they use (`16000`-`17000` by default).
* **Environment variables** shared with projects and workspaces.
* **Git** user name and email.
* **Bookmarks**, **plugins** and the **projects root path**.

## Project settings

Edited by project admins in **Settings → Project settings**.

* Name, icon, repository URL and branches.
* Models for chat, RAG, embeddings, wiki, vision and images.
* Knowledge options: search type, document count, relevance cutoffs, ignore patterns, external folders and sub-projects.
* **Project scripts**: named bash scripts that can run in the background and restart automatically.
* **Preview URL** for the running app.
* **Wiki** on or off and its folder.
* **MCP servers** available to agents.
* **Watching**: turn the file watcher on to process `@codx` mentions and keep knowledge fresh.
