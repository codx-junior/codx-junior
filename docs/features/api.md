# API

## REST API

The backend is a FastAPI application served under `/api`. Interactive documentation is available on your instance at:

* `/api/docs` (Swagger UI)
* `/api/redoc`
* `/api/openapi.json`

Real-time events (chat streaming, notifications, progress) use Socket.IO at `/api/socket.io`.

Requests are authenticated with the user token returned by `POST /api/users/login`.

## Personal access tokens

A machine client (a support widget, a CRM, an automation) authenticates with a
personal access token created in **Account settings → Personal Access Tokens**.
The token is scoped to a project, optionally to specific teammates, and carries
only the permissions it needs. The clear value is shown once; only its hash is
stored.

| Endpoint | Scope | Description |
| --- | --- | --- |
| `GET /api/users/tokens` | session | List the caller's tokens (metadata only). |
| `POST /api/users/tokens` | session | Create a token; the response is the only time the secret is returned. |
| `DELETE /api/users/tokens/{id}` | session | Revoke a token immediately. |
| `GET /api/v1/models` | `models:read` | Lists the profiles exposed as models, named `<project>/<profile>` unless a model name is set. |
| `GET /api/v1/profiles` | `profiles:read` | Public profile metadata. |
| `GET /api/v1/projects` | `projects:read` | The project bound to the token. |
| `POST /api/v1/chat/completions` | `chat:write` | OpenAI-compatible chat completion. |
| `POST /api/v1/completions` | `chat:write` | Legacy single-prompt completion. |

Send the token as `Authorization: Bearer cxj_pat_…` (or `x-api-key`). The
OpenAI-compatible endpoints do **not** accept a browser session, so a token can
never inherit the owner's full API access.

Any [profile](/features/profiles) can be exposed as a model. Turn on **API
settings → active** in the profile and optionally give it a model name and
description. Public tokens require **Public Exposure Protection** by default:
the profile's scope, decision model and tool allowlist are enforced on every
public turn. See [Personal access tokens and public exposure
protection](/features/personal-access-tokens).

This lets you plug codx-junior's project-aware assistants into other tools, such
as IDE extensions and support widgets.
