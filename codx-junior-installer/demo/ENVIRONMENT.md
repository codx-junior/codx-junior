# Demo environment — names, ports and the workspace preview

Reference sheet for the tier 1 deployment described in [`README.md`](README.md).
Everything below is taken from `docker-compose.yaml`, `up.sh` and the workspace
definitions in `codx-junior/workspaces/`.

## 1. Compose objects

The project and the container have **fixed names**, so these commands work
regardless of the folder you cloned into.

| Object | Name | Source |
|---|---|---|
| compose project | `codx-junior-demo` | `docker-compose.yaml` — `name:` |
| service | `codx-junior` (the only one) | `docker-compose.yaml` — `services:` |
| container | `codx-junior-demo` | `docker-compose.yaml` — `container_name:`; `up.sh:23` exports it as `CONTAINER` |
| volume (settings + per-project `.codx`) | `codx-junior-settings` → `/home/codx-junior/.codx-junior` | `docker-compose.yaml` — volumes |
| volume (analytics, AI logs, API metrics) | `codx-junior-analytics` → `/home/codx-junior/analytics` | `docker-compose.yaml` — volumes |
| bind mount (your repositories) | `${CODX_JUNIOR_PROJECTS_PATH:-./projects}` → `/home/codx-junior-projects` | `docker-compose.yaml` — volumes |

Because the container name is fixed, `docker exec codx-junior-demo …` is the
portable form; `docker compose logs -f codx-junior` addresses the service.

## 2. Ports

| Port | Published? | Owner |
|---|---|---|
| `19981` | **yes** — `${CODX_JUNIOR_WEB_PORT:-19981}:19981` | nginx: SPA + reverse proxy to the API |
| `19980` | **no** — container-internal only | the API process |

Consequences:

* the UI is `http://localhost:19981` (or `CODX_JUNIOR_PUBLIC_URL` when a cloud
  script passes one, see `up.sh` lines 154-159);
* nothing on the host can reach `19980` — the compose healthcheck and the
  bootstrap call run *inside* the container against `127.0.0.1:19980`
  (`docker-compose.yaml` healthcheck, `up.sh` lines 256-263);
* changing the published port is `./up.sh --port 8080`, which writes
  `CODX_JUNIOR_WEB_PORT` into `.env`.

## 3. Health and readiness

* Healthcheck: `GET /api/health` on `127.0.0.1:19980`, `interval 10s`,
  `retries 30`, `start_period 30s`.
* `up.sh` polls `docker inspect` for that health status for up to 120 s, dumps
  the last 60 log lines and aborts on `unhealthy`/`exited`.
* The provider is applied afterwards through the bootstrap endpoint; the printed
  summary is the bootstrap response.

## 4. Workspace (webtop) preview — WebSocket requirements

Workspaces, browser/computer sessions and the infra panel are **not** available
in the demo: the host container-runtime socket is deliberately left commented out
in `docker-compose.yaml` (see the "NOT mounted on purpose" block, lines 47-51).

If that socket is mounted and the workspace preview (`…/preview`) is exposed, the
browser reaches the workbench over a **WebSocket upgrade**. Three conditions must
hold; a miss does not produce an error page, it produces a blank or constantly
reconnecting workbench:

| # | Requirement | Where |
|---|---|---|
| 1 | `Connection: Upgrade` and `Upgrade: websocket` request headers must reach the preview container | `codx-junior/workspaces/codx-junior-workspace-default/docker-compose.yaml` — `…-desktop-headers` middleware (lines 67-68) |
| 2 | `X-Forwarded-Proto: https` and `X-Forwarded-Port: 443` must be set | same middleware (lines 65-66) |
| 3 | `Referer` must be removed | same middleware (line 71) — KasmVNC aborts above its header-size limit |

Plus the load balancer side: `server.port=6901`, `server.scheme=https` and
`insecure-skip-verify@file`, because KasmVNC serves a self-signed certificate
(same file, lines 76-78). The `kasmvnc.yaml` counterpart declares
`websocket_port: 6901`.

For the code-server flavour the upgrade is rejected with `403` unless
`CODX_JUNIOR_DOMAIN` is set. Its origin check compares the forwarded port (`:80`
from Traefik's `web` entrypoint) against the browser `Origin` (`https://…`,
`:443`), and the workbench reports
`WebSocket close with status code 1006`. See
`codx-junior/workspaces/codx-junior-workspace-default/custom-services.d/02-coder-init.sh`
lines 27-38, which writes `trusted-origins` into `config.yaml` when the domain is
known.
