# Personal access tokens and public exposure protection

A personal access token (PAT) lets a third-party application talk to
codx-junior without a browser session: a support widget, a CRM, a helpdesk, a
chat embedded in a public website. The token is **scoped** — it names the
project, the teammates and the API operations it may use — and it is the only
credential the external application holds.

A public assistant is therefore two things:

1. a normal [profile](/features/profiles) with its instructions, model and
   tools, exposed as an API model;
2. its **Public Exposure Protection** policy, which decides what a public user
   may ask and what the assistant may answer.

## Creating a token

Open **Account settings → Personal Access Tokens → Create token**. The form
asks for:

| Field | Meaning |
| --- | --- |
| **Name** | A label for your own reference (`Support widget`, `Website chat`). |
| **Permissions** | `chat:write` (call the chat endpoint), `models:read`, `profiles:read`, `projects:read`. A chat token is always bound to a project. |
| **Teammates** | Optional. Empty means any profile that is shared through the API *and* protected. Select specific profiles for a narrower token. |
| **Require Public Exposure Protection** | The default. A profile whose protection is disabled is never reachable through this token. |
| **Expiry** | 7 days, 30 days, 90 days, one year, or never. |

The clear token is shown **once** (`cxj_pat_…`). codx-junior stores only its
SHA-256 hash, so it cannot be recovered later; revoke the token and create a
new one if it is lost. Tokens are stored in
`<config folder>/pat_tokens.json` with owner-only permissions and written
through a cross-process lock.

## Calling the public API

Send the token as a bearer:

```bash
curl -s https://your-instance/api/v1/models \
  -H "Authorization: Bearer cxj_pat_..."
```

| Endpoint | Scope | Description |
| --- | --- | --- |
| `GET /api/v1/models` | `models:read` | Profiles exposed as OpenAI models. Unprotected profiles are hidden when the token requires protection. |
| `GET /api/v1/profiles` | `profiles:read` | Public profile metadata (never the prompt or knowledge files). |
| `GET /api/v1/projects` | `projects:read` | The project(s) the token may use. |
| `GET /api/v1/me` | any | Token metadata; useful as a connection test. |
| `POST /api/v1/chat/completions` | `chat:write` | OpenAI-compatible chat completion. |
| `POST /api/v1/completions` | `chat:write` | Legacy single-prompt alias. |

The chat request uses the OpenAI shape. `model` may be the public model name
(`<project>/<profile>`), `<project-id>/<profile>`, or the bare profile name
when it is unambiguous. If the token reaches exactly one model, `model` is
optional.

```bash
curl -s https://your-instance/api/v1/chat/completions \
  -H "Authorization: Bearer $CODX_PAT" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "support/acme-support",
    "messages": [
      {"role": "user", "content": "Where is my invoice?"}
    ]
  }'
```

The response is an OpenAI `chat.completion`. The `codx` field adds the project,
the profile, and the guardrail verdict for that turn:

```json
{
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "..."}}],
  "codx": {
    "project_id": "...",
    "project_name": "support",
    "profile": "acme-support",
    "guardrail": {"action": "pass"},
    "refused": false
  }
}
```

A refused turn is still a `200` with the refusal text as the assistant answer,
so the embedding application can render it like any other answer. A request the
profile is not allowed to serve at all answers `403`/`413`/`404` with an
OpenAI-shaped `error` body.

## Public Exposure Protection

Enable **Public Exposure Protection** in the teammate's settings. It adds two
layers around the ordinary conversation:

### Deterministic layer

Cheap checks run first, before any model call:

- **Static rules** catch obvious prompt injection and secret-extraction
  patterns.
- **Scope keywords and embedding similarity** decide whether the request is
  on-topic.
- **Blocked topics** are refused by phrase.
- **Max input** and the chat tool allowlist bound what a public user can spend.
- **Output rules** look for secrets and echoed injections in the answer.

### Decision model

When the decision mode is `llm` or `hybrid`, a small single-shot model call
classifies each input (and each answer) as on-topic, off-topic, prompt
injection, misbehaviour, secret extraction, unsafe or other, with a risk score.
The result becomes a guardrail detection and the deterministic policy decides
what to do:

| Signal | Action |
| --- | --- |
| High/critical risk, prompt injection, secret extraction, unsafe | **Block** the turn and return the refusal message. |
| Medium risk (off-topic, abuse, model uncertainty) | **Refuse** the turn: tools are disabled and the scope instructions are added to the system prompt. |
| Low/info | Pass with the scope instructions. |

The decision model defaults to the project's **Guardrail model** (Global
Settings → AI models), then the global one. A teammate can override it.

**Fail closed** makes a decision-model outage refuse instead of allowing an
unclassified request. It is the safer choice for a public support assistant; a
`rules`-only profile does not depend on a model at all.

### What the public user can never reach

- the system prompt, profile content or knowledge files;
- another teammate that is not exposed through the token;
- a tool that is not in the profile's allowlist (the guardrail disables tools
  whenever a request is refused);
- a project other than the one bound to the token.

## Revoking and rotating

Revoke a token from the same account panel. Revocation is immediate in every
API worker (the store is read per request). An expired or revoked token answers
`401` with `invalid_credentials`; a token lacking a permission answers `403`
with `insufficient_scope`.

Rotate by creating a new token, deploying it to the application, and revoking
the old one. Tokens are independent, so several applications can have separate
credentials with separate permissions.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODX_JUNIOR_CONFIG_FOLDER` | `$HOME` | Folder holding `pat_tokens.json`. |
| `CODX_JUNIOR_PAT_FOLDER` | `CODX_JUNIOR_CONFIG_FOLDER` | Optional override for the token store folder. |
| `CODX_PUBLIC_API_REQUEST_TIMEOUT_SECONDS` | `600` | Request budget for `/api/v1/*` (the generic API budget is 30 s). |

The OpenAI-compatible endpoints are **closed by default for anonymous
callers**: they now require a PAT. A browser session token is not accepted
there, so a token can never silently inherit the owner's full API access.
