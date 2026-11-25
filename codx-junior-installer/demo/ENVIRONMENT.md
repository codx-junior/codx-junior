# DEMO ENVIRONMENT — the M1 one-command stack

Everything below is taken from the files that actually run the demo:
`demo/up.sh` (the parser and the compose calls), `demo/docker-compose.yaml`,
`demo/entrypoint.sh`, `demo/nginx.conf`, `demo/supervisord.conf` and
`demo/.env.example`. If a command in this document ever disagrees with
`demo/up.sh`, the script wins and this document is the bug.

**No secret lives in this file.** The AI key, the admin password and the
workspace token live in `demo/.env`, which `demo/up.sh` creates from
`demo/.env.example` and which is never committed.

Contents
1. [Host requirements](#1-host-requirements)
2. [One-command bring-up](#2-one-command-bring-up)
3. [URLs](#3-urls)
4. [Environment variables](#4-environment-variables)
5. [Demo credentials](#5-demo-credentials)
6. [Reset / tear-down](#6-reset--tear-down)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. HOST REQUIREMENTS

| Requirement | Why | Check |
|---|---|---|
| Linux or macOS with `bash` | `demo/up.sh` is `#!/usr/bin/env bash` and uses `set -euo pipefail`, `BASH_SOURCE`, `[[ =~ ]]` | `bash --version` |
| Docker Engine, running | the stack is one container | `docker info` |
| Docker Compose **v2 plugin** (`docker compose`, not `docker-compose`) | `up.sh` calls `docker compose version` and refuses to continue without it | `docker compose version` |
| `curl` | only used to wait for `/api/health`; without it the script warns and returns without waiting | `curl --version` |
| Outbound network | pull `codxjuniorai/codx-junior` from the registry + reach the AI provider endpoint | |
| ~2 GB free disk | one image + two named volumes (`codx-junior-settings`, `codx-junior-analytics`) | `df -h` |

Not required and deliberately absent from this stack: Traefik, LocalAI, a vector
database service, Postgres, init containers, `docker.sock` (so **no** workspaces,
browser/computer sessions or infra panel on a demo host), and any build on the
demo host — the default path pulls the published image.

`./up.sh --help` and `./up.sh --list-providers` work **without Docker**.

---

## 2. ONE-COMMAND BRING-UP

From the repository root:

```bash
cd codx-junior-installer
cp demo/.env.example demo/.env      # only if demo/.env does not exist yet
$EDITOR demo/.env                   # set CODX_JUNIOR_AI_PROVIDER and CODX_JUNIOR_AI_API_KEY
./demo/up.sh
```

`./demo/up.sh` is idempotent. **First run on a clean checkout** (no
`demo/.env`) does not start anything: it copies `.env.example` to `.env`,
prints what to fill in and **exits 2**. Fill in one provider + one key and run
it again.

If the file is not executable: `bash demo/up.sh` (or `chmod +x demo/up.sh`).

What a successful run does, in order: resolve the image reference → `docker
compose pull` (a failure is only a warning) → `docker compose up -d --no-build`
→ wait up to `CODX_JUNIOR_WAIT_SECONDS` (180 s) for
`http://127.0.0.1:<port>/api/health` → print the web URL.

### Options — exactly the set the parser accepts

| Option | Effect |
|---|---|
| *(none)* | bring the stack up and wait for health |
| `--ref <tag>` / `--ref=<tag>` / `-r <tag>` | pin the image tag. `1.2.3` and `v1.2.3` both select `codxjuniorai/codx-junior:v1.2.3`; any other token-shaped value is used as-is; a value containing `/` is a usage error (exit 2) |
| `--build` | build the image from this checkout (`up -d --build`) instead of pulling |
| `--no-wait` | do not wait for `/api/health` |
| `--url` | print the URL of the running stack and exit |
| `--logs` | `docker compose logs -f --tail=100` and exit |
| `--down` | `docker compose down` and exit (volumes kept) |
| `--list-providers` | print the provider values `demo/.env` accepts and exit |
| `-h`, `--help` | print the usage and exit |

Anything else — an unknown option or a positional argument — is a usage error
and exits **2**. There is no `--tag`, no `--port`, no `--env-file`: the port
comes from `CODX_JUNIOR_WEB_PORT` in `demo/.env`, the settings always come from
`demo/.env`.

An unusable `--ref` (e.g. `--ref 'not a tag!'`) does **not** fail the demo: it
warns and falls back to `:latest`.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | stack started (or the requested read-only action completed) |
| `1` | fatal: docker/compose missing, compose file missing, etc. |
| `2` | usage error, or `demo/.env` was just created and must be filled in |

A stack that never answers on `/api/health` does **not** change the exit code —
the script warns and prints the URL anyway. Verify with §7 before declaring the
demo good.

### The verification that counts as evidence

```bash
./demo/up.sh --url
curl -fsS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:<port>/api/health"   # expect 200
docker compose -f demo/docker-compose.yaml ps                                      # expect "healthy"
```

---

## 3. URLS

| URL | What it is |
|---|---|
| `http://localhost:19981` | the web UI (nginx: SPA on `/`, `/api/` proxied to `127.0.0.1:19980`) — **the default**, change it with `CODX_JUNIOR_WEB_PORT` |
| `http://localhost:19981/api/health` | the readiness probe `up.sh` waits on |
| `http://localhost:19981/api/...` | every API call, same origin as the SPA (no CORS, no Traefik needed) |
| `127.0.0.1:19980` **(container only)** | the API process itself |
| `127.0.0.1:19984` **(container only)** | the background worker (orchestration, watchers) |

`./demo/up.sh --url` prints the effective URL instead of hardcoding the port.

**Reached via Traefik.** This stack publishes a host port and declares **no**
`traefik.*` labels, so it is *not* routed by Traefik on its own. When the demo is
published through the platform's Traefik, the route must:

- target the container `codx-junior-demo` on port **19981** (the nginx port — the
  API port 19980 is never the entry point);
- strip its path prefix, because nginx serves the SPA from `/`;
- be protected by the auth middleware (`codx-junior-auth@file`);
- preserve `X-Forwarded-Proto` — nginx forwards it to the API and overwriting it
  makes the API believe every request is plain HTTP.

Then the demo is the same application at `https://<domain>/<path>`. See §7.3 for
the diagnosing commands.

---

## 4. ENVIRONMENT VARIABLES

### 4.1 Read by `demo/up.sh` (shell environment, not `demo/.env`)

| Name | Default | Required | Secret | Effect |
|---|---|---|---|---|
| `CODX_JUNIOR_TAG` | `latest` | no | no | default image tag when `--ref` is absent |
| `CODX_JUNIOR_IMAGE_REPO` | `codxjuniorai/codx-junior` | no | no | image repository |
| `CODX_JUNIOR_IMAGE` | derived from the two above | no | no | the full image reference, **exported by `up.sh`** for `docker-compose.yaml`'s `image:`. Set `--ref`/`CODX_JUNIOR_TAG` instead of exporting it by hand |
| `CODX_JUNIOR_REF` | — | no | no | **not read by `demo/up.sh`.** The image-pin knob is `--ref <tag>` (or `-r`, or `CODX_JUNIOR_TAG`). Exporting `CODX_JUNIOR_REF` pins nothing |
| `CODX_JUNIOR_WEB_PORT` | `19981` | no | no | host port published by compose (`${CODX_JUNIOR_WEB_PORT:-19981}:19981`) and the fallback used by `--url`. Set it in `demo/.env` to change the host port |
| `CODX_JUNIOR_WAIT_SECONDS` | `180` | no | no | how long `up.sh` waits for `/api/health` (polls every 3 s) |

### 4.2 Settings in `demo/.env` (created from `demo/.env.example`, read by compose)

| Name | Default | Required | Secret | Effect |
|---|---|---|---|---|
| `CODX_JUNIOR_AI_PROVIDER` | `deepseek` (as shipped in `.env.example`) | yes, for AI | no | provider preset: `openai`, `deepseek`, `gemini`, `openrouter`, `mistral`, `groq`, `custom`, `none`. `none` = UI starts, AI features disabled |
| `CODX_JUNIOR_AI_API_KEY` | empty | yes for AI, unless provider is `none` | **yes** | expanded from the environment when the provider is used, never copied into the settings files |
| `CODX_JUNIOR_AI_API_URL` | provider preset | only for `custom` | no | OpenAI-compatible endpoint, must end in `/v1` |
| `CODX_JUNIOR_AI_MODEL` | per-provider preset | no | no | chat + coding model |
| `CODX_JUNIOR_AI_RAG_MODEL` / `_WIKI_MODEL` / `_GUARDRAIL_MODEL` | the chat model | no | no | per-role model override |
| `CODX_JUNIOR_AI_EMBEDDINGS_MODEL` / `_DIM` | provider preset / `1536` | no | no | embeddings; empty model keeps knowledge/RAG disabled |
| `CODX_JUNIOR_AI_CONTEXT_LENGTH` | `32768` | no | no | context window |
| `CODX_JUNIOR_PROJECTS_PATH` | `./projects` | no | no | **host** folder bind-mounted to `/home/codx-junior-projects` (compose overrides the in-container value) |
| `CODX_JUNIOR_PUBLIC_URL` | unset | no | no | informational address printed in the banner; it changes no binding |
| `CODX_JUNIOR_IMAGE` | published image | no | no | image tag to run; `up.sh` overrides it for the current run |
| `CODX_JUNIOR_ADMIN_USER` / `_ADMIN_PASSWORD` / `_ADMIN_EMAIL` | unset | no | password **yes** | first admin, seeded on the **first boot only**; the password is bcrypt-hashed and never logged |
| `CODX_JUNIOR_WORKSPACE_TOKEN` | unset | no | **yes** | long random per-instance API key acting as the workspace owner (hosted console); rotate by changing it and restarting |
| `CODX_JUNIOR_HIDDEN` | `0` | no | no | `1` hides the internal projects from the project list |
| `CODX_JUNIOR_AI_FORCE` | `0` | no | no | `1` re-applies the provider configuration on every start (key rotation) |
| `CODX_JUNIOR_AI_BOOTSTRAP` | `1` | no | no | `0` disables automatic provider configuration (configure it in the UI) |
| `CODX_JUNIOR_KNOWLEDGE_BACKEND` | `lancedb` | no | no | `lancedb` (embedded, in-process) or `none` |

### 4.3 Fixed inside the container (do not set in `demo/.env`)

| Name | Value | Why |
|---|---|---|
| `CODX_JUNIOR_PROJECTS_PATH` | `/home/codx-junior-projects` | compose pins it: `.env` carries the *host* path, which does not exist in the container |
| `WEB_CONCURRENCY` | `1` | more than one uvicorn worker breaks the in-process session state (socket.io) |
| `ANONYMIZED_TELEMETRY` | `False` | |
| `CODX_SUPERVISOR_LOG_FOLDER` | `/var/log/codx-junior-supervisor` | supervisord + nginx + api logs (`supervisord.log`, `api.log`, `api-background.log`, `web.log`, `nginx-access.log`) |
| `CODX_JUNIOR_CONFIG_FOLDER` | `/home/codx-junior/.codx-junior` | settings, volume `codx-junior-settings` |
| `CODX_JUNIOR_API_ANALYTICS_DATA_PATH` | `/home/codx-junior/analytics` | usage/logs, volume `codx-junior-analytics` |
| `CODX_JUNIOR_STATIC_FOLDER` | `/home/codx-junior/static` | uploads |
| `CODX_JUNIOR_PATH` | `/home/codx-junior/codx-junior` | application code (the `Dockerfile.dev` overlay mounts the checkout here) |

---

## 5. DEMO CREDENTIALS

The demo never uses a personal account and never uses a production key.

- **The account.** The first admin is seeded from `CODX_JUNIOR_ADMIN_USER`,
  `CODX_JUNIOR_ADMIN_PASSWORD` and `CODX_JUNIOR_ADMIN_EMAIL` **on the first boot
  only**. The password is bcrypt-hashed before it is stored and is never logged.
  The email is also usable as a login identifier.
- **Set them before the first `./demo/up.sh`.** On an already-initialised stack
  (the `codx-junior-settings` volume exists) editing these keys changes nothing —
  see §6 for a reset.
- **Use a dedicated demo identity**, e.g.
  `CODX_JUNIOR_ADMIN_USER=demo`, `CODX_JUNIOR_ADMIN_EMAIL=demo@example.invalid`,
  `CODX_JUNIOR_ADMIN_PASSWORD=<a freshly generated value>`. Never the presenter's
  own account, never a colleague's mailbox.
- **Where the value lives.** Only in `demo/.env` on the demo host (never in this
  file, never in a card, never in a chat message). Hand it to the audience out of
  band; rotate it after the demo by resetting the stack (§6).
- **AI key.** Same rule: `CODX_JUNIOR_AI_API_KEY` in `demo/.env`. For a public
  demo prefer a provider key scoped to the demo, `CODX_JUNIOR_AI_PROVIDER=none`
  when the demo does not need AI, or `CODX_JUNIOR_WORKSPACE_TOKEN` for a hosted
  console.

---

## 6. RESET / TEAR-DOWN

| Goal | Command | Data |
|---|---|---|
| Stop the stack, keep everything | `./demo/up.sh --down` (= `docker compose -f demo/docker-compose.yaml down`) | volumes kept: settings, analytics, and the projects bind mount |
| Restart in place | `./demo/up.sh` | kept |
| **Full reset** (fresh first boot) | `docker compose -f demo/docker-compose.yaml down -v` | **destroys** `codx-junior-settings` and `codx-junior-analytics` |

```bash
# full reset — DESTRUCTIVE, ask before running it on a shared host
docker compose -f demo/docker-compose.yaml down -v
./demo/up.sh                       # first boot: the admin is re-seeded from demo/.env
```

Notes:

- `-v` deletes the two **named** volumes only. `CODX_JUNIOR_PROJECTS_PATH` is a
  bind mount: the user's projects are never deleted by a reset.
- The volume names are fixed (`codx-junior-settings`, `codx-junior-analytics`),
  so a `down -v` also clears them for any other stack using the same names.
- A full reset is also how you pick up a new `CODX_JUNIOR_ADMIN_*` value or a
  new provider configuration that is only applied at first boot.
- `demo/.env` is **not** removed by any of the above; it stays until you delete it.

---

## 7. TROUBLESHOOTING

### 7.1 Port already in use

Symptom: `docker compose up` fails with `port is already allocated` /
`address already in use`, or the UI shows someone else's application.

```bash
docker compose -f demo/docker-compose.yaml ps                      # is another stack already on 19981?
ss -ltnp | grep ':19981'                                           # Linux: who owns the port
lsof -iTCP:19981 -sTCP:LISTEN                                      # macOS
docker ps --filter publish=19981 --format '{{.Names}}\t{{.Image}}'  # which container
```

Fix: set `CODX_JUNIOR_WEB_PORT=<free port>` in `demo/.env` and run
`./demo/up.sh` again. The mapping is `${CODX_JUNIOR_WEB_PORT:-19981}:19981` —
only the **host** side moves; the container always serves 19981. Confirm with
`./demo/up.sh --url`.

### 7.2 Image pull

Symptom: `up.sh: warning: could not pull ...; continuing with a local image if
present`, then the container exits or a stale image runs.

```bash
docker image ls codxjuniorai/codx-junior                          # what is on disk, and its tag
docker compose -f demo/docker-compose.yaml pull codx-junior       # the pull, with its real error
docker info | grep -i -A3 'registry\|auth'                        # registry reachable/authenticated?
```

Fixes, in order: pin a tag known to be published
(`./demo/up.sh --ref v1.2.3`); if the tag does not exist, build from this
checkout (`./demo/up.sh --build`, needs the full repository and
`demo/Dockerfile`); if the registry is rate-limiting or private, authenticate
with `docker login` — the credential goes in Docker's own store, never in
`demo/.env`, never in this document.

### 7.3 Traefik routing

Symptom: `http://localhost:19981` works but `https://<domain>/<path>` returns
**404** (no route) or **502** (route matched, backend unreachable).

The stack declares no Traefik labels, so a proxy failure is a **route** problem,
not an application problem. Diagnose in this order:

```bash
# 1. is the application itself alive? (expect 200)
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:19981/api/health

# 2. is the container up and on which port?
docker ps --filter name=codx-junior-demo --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'

# 3. what does the proxy think? 404 = no router, 502 = router matched, backend refused
docker logs --tail=50 traefik 2>&1 | grep -i '<domain\|<path>'
curl -fsS http://127.0.0.1:19981/api/traefik/config | head        # the route table codx-junior publishes

# 4. is nginx answering inside the container? (the router's target port must be 19981)
docker exec codx-junior-demo curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:19981/
```

Checklist when adding the route: target = container `codx-junior-demo`, port
**19981**; path prefix stripped (nginx serves the SPA from `/`); middleware
`codx-junior-auth@file`; `X-Forwarded-Proto` forwarded, not overwritten. A route
that points at 19980 bypasses nginx and loses the SPA and the socket.io
websocket.

### 7.4 Stack up, but the demo is not ready

```bash
./demo/up.sh --logs                                                    # api / background / web logs
docker inspect --format '{{.State.Health.Status}}' codx-junior-demo    # healthy?
docker exec codx-junior-demo tail -n 50 /var/log/codx-junior-supervisor/api.log
```

The health wait is bounded by `CODX_JUNIOR_WAIT_SECONDS` (180 s by default); on
a slow host or a cold database the UI can answer before the API worker is
finished. `curl` missing on the host only skips the wait (`up.sh: warning: curl
not found, not waiting for the stack to answer`) — it does not mean the stack is
broken.

---

## What this document covers

The docs half of milestone **M1 — one-command demo bring-up**. Scope of the doc:
this demo stack as it exists in `demo/`. Out of scope: workspace preview (M2),
the efforts feature (M3), cloud bootstrap, dind and vllm — see `demo/DEMO-M2.md`
and `demo/DEMO-M3.md`.
