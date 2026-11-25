# Installer refactor — one installer, four deployment modes

Status: **review + implementation.** The new installer and compose templates
are in the tree (`codx-junior-installer/install.sh`,
`codx-junior-installer/runtime/`, `codx-junior-installer/dind/`). The root
`install.sh` and `cloud/bootstrap.sh` are now thin shims to it, so the entry
point is unified. The **legacy compose tree** (`codx-junior/`, `demo/up.sh`,
`codx-junior-dind/`, Milvus and the old model sidecars) has been deleted; the
new installer is the only path.

Scope: how codx-junior is installed, which modules are real, the installer
design, the four supported use cases, the Traefik master/slave model, DIND, the
migration plan and the remaining issues.

---

## 1. Executive summary

1. **There is one application, not four.** `demo/Dockerfile` already builds the
   product: FastAPI + background worker + nginx + SPA in one container. Every
   deployment mode should use that image. The legacy `codx-junior/` compose is a
   different, heavier product (api + api-background + client + milvus + localai +
   traefik + two init containers) and should not survive as a second definition
   of the stack.
2. **"Install all modules" is the wrong axis.** The modules are *inside* the
   image. What the installer chooses is (a) one image, (b) one proxy mode,
   (c) whether the Docker socket is mounted, and (d) whether the instance is
   nested (DIND). Optional model servers and vector databases are **external**
   OpenAI-compatible endpoints, not services to install.
3. **Recommended shape:** one `install.sh` with `--mode local|server|slave|dind`,
   one runtime compose file plus four small overlays, and one Traefik config
   split into `master` (owns :80/:443 + ACME) and `slave` (plain :80, trusts a
   master's `X-Forwarded-*`). That covers all requested cases without a second
   compose definition.
4. **Removed from the runtime and the code:** Milvus, LocalAI, LiteLLM, vLLM,
   Coolify, Prometheus, the two init containers, Caddy and workspaces. Knowledge
   is LanceDB only; the `pymilvus` dependency and the `knowledge_db.py` backend
   are gone. Local model servers are external OpenAI-compatible endpoints run by
   the admin (for example inside a coworking desktop).
5. **Do not delete the workspaces code yet.** The installer can stop deploying a
   default workspace in an afternoon; removing the API/client module (55 source
   files, `engine/session.py` imports it, default apps depend on it, Task
   workspaces are a different concept) is a separate code project. Coworking is
   independent of workspaces and only needs the Docker socket.
6. **Biggest risks:** Docker-socket privilege, DIND's privileged/sysbox
   requirement, ACME + DNS ordering on first boot, `X-Forwarded-Proto` being
   rewritten by the app's nginx, and per-instance name collisions on a shared
   master proxy.

---

## 2. What exists today

| Path | Role | Verdict |
|---|---|---|
| root `install.sh` | curl-friendly front door; now resolves `codx-junior-installer/install.sh` and forwards flags | **done / thin shim** |
| `cloud/bootstrap.sh` | old unattended installer (Docker, clone, `demo/up.sh`, Caddy, CLI) | **compatibility shim** to the unified installer |
| `cloud/cloud-init.yaml`, `aws-user-data.sh`, `gcp-startup-script.sh` | provider wrappers around bootstrap | keep, point at the new installer |
| `demo/Dockerfile` + `demo/docker-compose.yaml` | the complete application image and the developer compose | **kept**; `demo/up.sh` deleted (the installer is the entry point) |
| `dev-install.sh` + `demo/docker-compose.dev.yaml` | developer path: build and mount the checkout | keep as `--dev`/separate command |
| `codx-junior/` | legacy 8-service server stack | **deleted** |
| `codx-junior-dind/` | old DIND wrapper + default workspace + Coolify | **deleted**; new `dind/` replaces it |
| `scripts/install_*.sh` | in-image build helpers used by the legacy Dockerfile | keep for `--build`, not at runtime |
| `scripts/run_*.sh` | process launchers used by supervisord | keep; remove the deprecated ones |
| `vllm/`, `litellm/`, `localai/`, `milvus/`, root `traefik/` | sidecar configs/scripts | **deleted** |
| `codx-junior/.env`, `codx-junior/traefik/letsencrypt/acme.json` | untracked local secrets; ACME key is real-looking | **deleted** with the legacy directory; rotate any key that was copied |

The old `cloud/bootstrap.sh` had good validation, idempotency and health checks.
The new installer keeps those ideas but removes the Caddy path and the
`demo/up.sh` hop.

---

## 3. Module inventory and verdict

### 3.1 Deployment modules (what the installer decides)

| Module | What it is | Needed today? | Verdict |
|---|---|---|---|
| **app image** (`demo/Dockerfile`) | API + background worker + nginx + SPA, one complete container | yes | **keep**, published as `codxjuniorai/codx-junior:latest`/semver |
| **LanceDB** | embedded vector store in the API process | yes, default | **keep** (no service) |
| **Milvus** | external vector DB | no | **removed** from the installer and the code |
| **Traefik** | reverse proxy, TLS, forward-auth, custom routes | yes on servers | **keep**, split master/slave |
| **Caddy** | old `--domain` front door | no | **drop**; one proxy (Traefik) or none |
| **LocalAI** | local model server | no code import | **drop**; any OpenAI-compatible endpoint works |
| **LiteLLM** | model proxy/UI | no code import; only docs/config | **drop**; use an external LiteLLM only if someone needs Anthropic |
| **vLLM** | local inference | deprecated module; only a script | **drop** script + extra; use an external endpoint |
| **Ollama** | local inference | external endpoint only | **preset removed**; run it yourself and use `custom` |
| **Coolify** | app platform installed inside DIND | not part of the product | **drop** |
| **Prometheus** | metrics scrape config pointing at LiteLLM | no | **drop** |
| **analytics-init** | one-shot chown of a volume | replaced by bind mounts/entrypoint | **drop** |
| **codx-junior-init** | one-shot Ollama model pull; references a service that is not in the compose | broken | **drop** |
| **workspaces** | dev containers + default workspace template | replaced by coworking; installer does not need it | **drop from install/runtime**, code removal later |
| **coworking / browser / computer** | sibling containers for Chromium, Linux desktops, Windows/macOS VMs | yes, the replacement for workspaces | **keep optional** via `/var/run/docker.sock`; VMs additionally need `/dev/kvm` |
| **DIND** | nested Docker, one isolated instance per container | yes for multi-tenant hosting | **keep**, rebuilt on the single runtime compose |
| **development option (`--dev`)** | host: checkout mounted + Vite + DEBUG=1; DIND: DEBUG=1 | yes for development | **keep** |
| **systemd registration** | boot + lifecycle | requested | **add** (templated per instance) |

Evidence for the drops: `api/` has **no `litellm`, `localai` or `coolify`
import**; `vllm_cpu_ai.py` is explicitly `## DEPRECATED`; `prometheus.yml`
scrapes a `litellm:4000` target that is not in any compose; `initial-setup.sh`
pulls from `ollama-codx-junior`, a service absent from the compose. The Milvus
backend (`knowledge_db.py`, `pymilvus`) and the Ollama presets/code are removed.

### 3.2 Product modules (inside the image)

These do not need installer work; they matter for "is it really needed?" and for
what the complete image contains.

| Area | Modules | Install impact / verdict |
|---|---|---|
| Core | `engine`, `chat`, `agents`, `ai`, `sio`, `api`, `security`, `project`, `settings`, `model` | keep; the product |
| Work management | `kanban`, `board_manager`, `task_manager`, `orchestration`, `autonomy`, `efforts`, `profiles`, `harness` | keep |
| Knowledge | `knowledge` (LanceDB only), `search`, `atlas`, `organizer`, `mentions` | keep; embeddings provider needed for dense RAG |
| Collaboration | `teams`, `company`, `meets`, `calendar`, `media`, `channel/views`, `voice`, `whisper` | keep |
| Build/run | `computer`, `browser`, `infra`, `tools`, `custom_tools`, `file_manager`, `changes`, `reviews`, `git` API | keep; `computer`/`browser`/`infra` need the Docker socket |
| Governance | `ciso`, `security/ciso`, `quality`, `guardrails`, `analytics`, `metrics`, `api_logs`, `logs` | keep |
| Extensibility | `mcp`, `plugins`, `integrations`, `ticketing`, `starter`, `prompts` | keep |
| **Workspaces** | `api/codx/junior/workspaces/*`, `api/api/workspaces.py`, `api/model/workspace.py`, client `WorkspaceView`, workspace menus/routes, templates in `codx-junior/workspaces/` | **remove later**: 55 source files, imported by `engine/session.py`; installer stops deploying it now |

Image-level gaps to decide:

* `demo/Dockerfile` has **no tesseract, pandoc, TeX Live, Playwright/Chromium or
  torch**. Coworking OCR (`computer/ocr.py`) needs the `tesseract` binary;
  document conversion needs pandoc/TeX; `web/browser_fetch.py` needs Playwright
  when it renders JS. A `full` image variant (or a tools sidecar) is needed if
  those features are sold as "all modules".
* `WEB_CONCURRENCY=1` is required by the in-process Socket.IO state. Do not
  "scale" by adding uvicorn workers; scale by adding instances behind the master.

### Why nginx if the edge is Traefik?

Traefik is the **ingress**: it owns DNS, TLS, ACME, forwarded headers and the
route table. It cannot serve static files. nginx is an **internal** web server
inside the application container: it serves the compiled SPA, applies cache
headers, accepts large uploads and proxies `/api` + socket.io to the single
uvicorn worker on loopback. That keeps static-asset traffic off the same worker
that streams agent turns.

It is not a second ingress and it is never published directly. Removing it would
mean moving SPA serving into FastAPI (`StaticFiles` + SPA fallback) and putting
static traffic on the single uvicorn worker; that is a reasonable follow-up, but
not a requirement of the current design.

---

## 4. Target architecture

### 4.1 One installer

```
install.sh                         # user-facing entry (curl-friendly)
└── codx-junior-installer/install.sh
    ├── runtime/docker-compose.yaml            app (one container)
    │   ├── docker-compose.local.yaml          publish 127.0.0.1:19981
    │   ├── docker-compose.master.yaml         Traefik master :80/:443 + ACME
    │   ├── docker-compose.slave.yaml          Traefik slave :80, trusted headers
    │   ├── docker-compose.coworking.yaml      /var/run/docker.sock
    │   └── docker-compose.build.yaml          --build
    └── dind/
        ├── Dockerfile                         docker:dind + installer + runtime
        ├── entrypoint.sh                      nested dockerd + install.sh --container
        └── docker-compose*.yaml               outer wrapper (local/master/slave)
```

Properties:

* **Idempotent:** re-running installs/updates; settings and projects are kept.
* **One settings file** per instance, mode 600, parsed as data (never sourced).
* **One compose project per instance** (`--instance`), so containers, networks,
  volumes, routers and middleware names cannot collide.
* **Systemd:** `codx-junior.service` (default instance) or
  `codx-junior-<instance>.service`, running a generated compose wrapper. On
  systems without systemd, `restart: unless-stopped` is the fallback.
* **Health contract:** container health + `GET /api/health`; server mode also
  verifies `https://<domain>/api/health` with `--resolve`.

### 4.2 Mode matrix

| | local | server | slave | dind |
|---|---|---|---|---|
| App mode | local | server | server-like | server |
| Traefik | `--traefik none` | `--traefik primary` | `--traefik slave` | independent `--traefik` |
| Who terminates TLS | nobody | this instance | the primary | primary, or none |
| Ports published | 127.0.0.1:19981 | 80, 443 | none | per proxy + local access |
| ACME | no | HTTP-01 | no | primary only |
| Docker network | internal | internal + `codx-proxy` | internal + external primary network | nested |
| Docker socket | `--coworking` | `--coworking` | `--coworking` | always (nested socket) |
| Domain required | only if a proxy is chosen | yes for primary | yes | yes for primary/slave |
| Systemd | off by default | on | on | on (outer container) |

The app mode and the proxy mode are independent. The DIND inner installation is
always `server`. With `--traefik slave` the outer DIND container joins the
existing Traefik network; the nested Traefik runs as a normal slave in front of
the inner server.

The first wizard question is the target folder (default `$PWD`). Before the
final confirmation it writes editable `<target>/docker-compose.yaml` and
`<target>/.env`. The installer uses those files,
so `services.dind.volumes` mappings can be added before the first run. If the
files already exist, the wizard asks before overwriting them.

Any mode can add `--dev`. On a host it mounts the checkout, runs Vite with HMR
instead of nginx, and enables `uvicorn --reload`. On DIND the source is baked, so
it only sets `DEBUG=1` for the inner scripts. `--dev` is not a deployment mode.

### 4.3 Use cases

**A. Localhost / CI**

```bash
./install.sh --mode local --provider deepseek --key sk-...
# -> http://localhost:19981, no domain, no proxy, no TLS
```

Use `--coworking` only if you need browser/desktop sessions. `--projects` points
at the folder to open. This is the "try it / test the installer" path.

**B. Server with Traefik master**

```bash
sudo ./install.sh --mode server --domain codx.example.com \
  --acme-email ops@example.com --provider deepseek --key sk-...
```

The installer writes `CODX_DOMAIN_RULE`, starts Traefik with HTTP-01 ACME,
waits for `https://codx.example.com/api/health`, and registers the unit. Ports
80/443 are owned by this instance; a second instance on the same host must use
slave mode. Pre-create the A record before running; ACME retries in the
background if DNS is late, but the installer's bounded wait will fail.

**C. Server with a slave Traefik behind a master**

```bash
sudo ./install.sh --mode slave --domain team.example.com \
  --proxy-network traefik --trusted-proxy 172.16.0.0/12 \
  --provider openai --key sk-...
```

The master owns DNS/TLS and forwards plain HTTP plus `X-Forwarded-*`. The slave:

* joins the external `traefik` network, publishes **no** host port;
* runs Traefik on `:80` only, with
  `--entrypoints.web.forwardedheaders.trustedips=<master CIDRs>`;
* exposes a Docker label router (`<instance>-edge`) for a Docker-aware master;
* keeps its own router to the app, so the app UI and the API custom routes work.

If the master is not Docker-aware, create the same Host → `<container>:80` route
by hand in its config; the labels are inert.

**D. DIND**

```bash
sudo ./install.sh --mode dind --traefik primary --domain acme.example.com \
  --acme-email ops@example.com --provider deepseek --key sk-...

# behind an existing primary: add the outer DIND container to its network
sudo ./install.sh --mode dind --traefik slave \
  --domain demo.example.com --proxy-network traefik \
  --provider deepseek --key sk-...
```

Outer wrapper: one `privileged` container (or `--sysbox` with `sysbox-runc`) with
`/data` and a `dind-storage` volume. Inside: the same installer runs with
`--container`, starts the nested daemon and the same runtime compose. Persisted
host data: `projects/`, `settings/`, `analytics/`, `traefik/`, `docker/`.

Multiple instances on one host:

* each instance has a unique `--instance` (container, compose project, volume,
  router/middleware names);
* only one can be `dind-mode=server` (ports 80/443); the rest are `slave` and
  join the master's network;
* the inner slave uses a **nested** `codx-proxy` network and publishes `:80` to
  the wrapper; the outer container joins the host's master network and carries
  the edge labels. Do not try to join a host network from inside the nested
  daemon.

---

## 5. Traefik master/slave details

### Master

* Entrypoints `web :80`, `websecure :443`.
* Docker provider (`exposedByDefault=false`, `network=codx-proxy`) + HTTP
  provider (`http://codx-junior:19980/api/traefik/config`).
* ACME HTTP-01, storage `/acme/acme.json` (mode 600, directory 700).
* Router labels on the app: `Host(...)` → `websecure`, TLS + cert resolver;
  a separate `web` router redirects to HTTPS. The redirect is deliberately not a
  global entrypoint redirect: ACME HTTP-01 challenges must not be redirected.
* Dashboard off. Add it behind auth or bind it to an internal network if needed.
* Docker socket read-only is still root-equivalent for reading; acceptable for a
  single-tenant server, not for an untrusted multi-tenant host.

### Slave

* Entrypoint `web :80` only; no ACME, no `:443`, no host port.
* `forwardedheaders.trustedips=<master CIDR list>` (default private ranges +
  loopback) so the app sees `https` and the real client IP. `insecure=true` is an
  explicit last resort; it lets any peer spoof the headers.
* Docker provider for the `forwardAuth` middleware and the app router; HTTP
  provider for custom/workspace routes.
* Edge labels for the master (`<instance>-edge`, service port 80).
* The app's nginx currently overwrites `X-Forwarded-Proto` with its own
  `$scheme` (http). **This must be fixed** for slave mode (preserve the incoming
  header when present), otherwise absolute URLs/OAuth callbacks can be wrong.
  The change is in `demo/nginx.conf` and requires an image rebuild.

### Multi-instance naming

Everything that Traefik sees must be instance-scoped:

* router/service/middleware: `${CODX_INSTANCE}` / `${CODX_INSTANCE}-auth`;
* certificate resolver name is global per Traefik: one master, one resolver;
* `CODX_JUNIOR_TRAEFIK_AUTH_MIDDLEWARE` must equal `<instance>-auth@docker`;
* labels with a fixed name (`codx-junior-auth`, `redirectscheme`) collide when two
  instances share a master. The templates use the instance name for this reason.

---

## 6. Potential issues and decisions

### Blocking / high

1. **Docker socket = host root.** Coworking/browser/infra need it. Default is
   off; server modes require `--allow-privileged`. For multi-tenant, use DIND
   (nested socket) or a dedicated host. There is no sandbox between the API and
   the host once the socket is mounted.
2. **DIND needs `--privileged` or sysbox.** Without sysbox, a nested `dockerd`
   is root-equivalent. Sysbox support depends on the host kernel/distro; test it
   before promising it. Nested Docker also needs enough disk and a working
   overlay2 on the volume.
3. **ACME first boot is a race.** HTTP-01 needs DNS + port 80 reachable. The
   installer waits up to 5 minutes and fails with the Traefik logs. Use staging
   while rehearsing (`--acme-ca staging`) to avoid rate limits. A master with
   several domains requests one certificate for all names; one bad DNS record
   blocks the whole order.
4. **`X-Forwarded-Proto` is overwritten by nginx.** In slave mode the app is
   reached as `http` inside the container; nginx sets `X-Forwarded-Proto:
   $scheme` and the API never sees `https`. Fix the nginx map before claiming
   slave works end-to-end. `CODX_JUNIOR_PUBLIC_URL` is only informational today;
   it is not consumed by the API.
5. **Forward-auth is not authentication.** `/api/forward-auth` always returns
   `ok`. If the master is expected to authenticate users and pass
   `X-Forwarded-User`, the app does not consume it; that is a code change, not an
   installer flag.
6. **Legacy migration is not automatic.** The old installs use container
   `codx-junior-demo`, Caddy `codx-junior-tls`, named volumes
   `codx-junior-settings`/`-analytics`, and a `.env` in `demo/`. The new
   installer uses bind mounts under `<home>/data` and a new instance name. A
   migration must read the old `.env`, stop the old containers, and copy the
   volumes; otherwise the user loses settings/history. Do not run the new
   installer on a host with the old Caddy still bound to 80/443.

### Medium

7. **Workspaces removal is a code project.** 55 source files, an import from
   `engine/session.py`, client routes/menus, and templates. Removing the default
   workspace from the compose is easy; deleting the API module is not. Task
   workspaces (`task_workspace.py`) are different and must not be removed with
   it.
8. **Coworking VM features need `/dev/kvm`.** Windows/macOS sessions require
   nested virtualization; many VPS providers do not expose it. Linux desktops
   and browser sessions work without it.
9. **The complete image is large.** Tesseract, pandoc, TeX Live, Playwright/
   Chromium and torch are now included (no slim variant). Expect ~8–12 GB and a
   long first build/pull; steady-state RAM is unchanged. Knowledge dense search
   still needs an embeddings model: `deepseek`, `openrouter` and `groq` have
   none, so search degrades to lexical on those providers.
10. **Socket.IO is single-worker.** Keep `WEB_CONCURRENCY=1`; scale with
    instances, not workers. A shared master must use sticky sessions if it
    balances one domain across several app containers.
11. **UID/GID and bind mounts.** The application image runs as root precisely so bind
    mounts work with any host UID. A non-root image needs prepared directories
    and a docker-group decision. DIND adds a second UID namespace.
12. **Systemd vs restart policy.** The new unit is `Type=oneshot`,
    `RemainAfterExit=yes`; `restart: unless-stopped` also runs. Decide which one
    owns recovery. The unit is a boot-order + audit convenience, not a process
    supervisor.
13. **Port 80/443 conflicts.** A leftover Caddy/nginx/Traefik makes the master
    fail. The installer checks with `ss`; add `--force`/`--stop-old` to clean up
    rather than aborting mid-review.
14. **Name collisions on a shared master.** Fixed Traefik labels from the old
    compose (`codx-junior-auth`, `redirectscheme`) collide. The templates scope
    them by instance; any custom rule from the UI must do the same.
15. **`CODX_JUNIOR_DOMAIN` is a single string in the API.** Multiple public
    domains are supported by the Traefik rule, but `traefik/builder.py` uses the
    first domain for generated custom routes. Document this or extend the
    builder.
16. **DIND backup/restore.** Everything is inside the outer container's
    `dind-storage` volume and `/data`. Back up both; `docker compose down -v`
    destroys the nested images (data under `/data` survives if it is a bind
    mount, which the templates use).
17. **Image tags.** `:latest` is a rolling tag. Use a semver tag in production
    and pin `--image`/`--ref`; `--update` with `:latest` is not reproducible.
18. **ARM.** The published image is x86 only today; on ARM the install falls
    back to a local build (slow) or fails. Publish multi-arch or detect and warn.

### Low / cleanup

19. **Secrets in the working tree.** `codx-junior-installer/codx-junior/.env`
    (fake LiteLLM keys) and `.../traefik/letsencrypt/acme.json` (real-looking
    ACME account key) are present although git-ignored. Delete them from
    developer machines and rotate the ACME account if it was ever copied
    anywhere. The new installer stores settings under `<home>/config` with mode
    600 and never writes the key to a log.
20. **Dead env vars (`LITELLM_*`, `VLLM_*`, `CODX_JUNIOR_LLMFACTORY_*`) were
    removed from `set_env.sh` with the modules.** Keep the cleanup in mind when
    old deployment notes are copied into a new environment.
21. **Docs drift.** Some historical pages still describe the old 8-service
    stack. The knowledge/getting-started/installer pages are updated for
    LanceDB-only; the workspace and legacy deployment pages need the same pass
    when that code is removed.
22. **Tests.** The legacy `demo/tests` scripts were removed with `demo/up.sh`.
    **Done:** 58 checks in `codx-junior-installer/tests/test_install.sh` and a
    12-combination `docker compose config` matrix. The full image build itself
    still needs a CI/publish run (it is large).
23. **The DevOps `traefik` tool still expects the old container name and a
    static config file.** The templates set `CODX_JUNIOR_TRAEFIK_CONTAINER` to
    `<instance>-traefik`, but the new Traefik is configured with CLI flags, so
    `traefik(action="config", source="file")` has no `/traefik/traefik.yaml`.
    `status`, `routes` and `logs` work. Either generate a read-only snapshot
    into `${CODX_DATA_DIR}/traefik/` or teach the tool to read the effective
    command line.
24. **DIND logs are two-level.** `codx-junior logs` follows the wrapper
    container, not the nested API. To follow the inner stack:
    `docker exec -it <instance> docker compose -f /opt/codx-junior/runtime/docker-compose.yaml logs -f`.
    A `--nested` flag or a small `codx-junior app-logs` command would help.
25. **`CODX_JUNIOR_PUBLIC_URL` is informational.** The API does not use it for
    OAuth or absolute links; `X-Forwarded-*` (and the nginx map added here) are
    the actual mechanism. If a future feature needs the canonical URL, wire it
    into settings rather than relying on the summary.
26. **Image namespace/tag scheme (resolved).** All images are
    `codxjuniorai/codx-junior`: `:latest` on `main`, `vX.Y.Z` for a tag and
    `:dind` for the wrapper. The release workflow, publish script, installer
    defaults and DIND build script use that namespace.
27. **DIND needs a published application image (resolved).** The wrapper can be
    built with `--build-dind`, but the nested daemon can only *pull* the app
    image. `codxjuniorai/codx-junior:latest` and `:dind` are now published, so
    the DIND entrypoint can pull the app image. A private/air-gapped host still
    needs an image tarball and `docker load` in the entrypoint.

---

## 7. Migration plan

Ordered, each step independently shippable:

1. **Add** the new installer + runtime compose + DIND templates (done in this
   change). Do not delete anything yet.
2. ~~**Make root `install.sh` a thin shim** to the new installer; keep
   `cloud/bootstrap.sh` for one release with a deprecation warning.~~ **Done.**
3. **Add `--import-env` / `--adopt`** to read the old `demo/.env`, stop
   `codx-junior-demo` + `codx-junior-tls`, and copy the old named volumes into
   `<home>/data` (or keep the named volumes on first migration).
4. **Remove from the runtime path:** `codx-junior/docker-compose.yaml`,
   `codx-junior/Dockerfile`, `api/`+`client/`+`debian/` Dockerfiles, LocalAI,
   LiteLLM, vLLM, Prometheus, init scripts, Coolify,
   default workspace templates, Caddy.
5. **Remove workspaces from the product** (separate project): API module, client
   routes/views/menus, projector/settings fields, Traefik workspace routes,
   default apps. Verify coworking covers the use cases first.
6. **Publish** the app image (`:latest` + semver, multi-arch) and the DIND image;
   make pull the default, build the fallback.
7. **Update docs** and the cloud wrappers to the new flags; add the four
   runbooks to `docs/getting-started.md`.

Definition of done:

* `install.sh --mode local` on a clean laptop → UI in < 2 min, no proxy.
* `--mode server` on a fresh VM with DNS → HTTPS + health in one command.
* `--mode slave` behind a master → health through the master, no host port.
* `--mode dind` → two instances on one host, distinct names/volumes, one master
  and one slave.
* Re-running any of them is a no-op/update, never data loss.
* `docker compose config` is valid for every mode and `shellcheck` is clean.

---

## Appendix A — complete API package inventory

Counts are top-level `.py` files in `api/codx/junior/<package>/` (the API
discovers every `api/*.py` router automatically, so a package that is no longer
needed also has a router to remove).

| Package | Files | What it does | Verdict |
|---|---:|---|---|
| `agents` | 3 | agent definitions/registry | keep |
| `ai` | 8 | OpenAI client, prompts, smol agent, embeddings | keep; local-model managers removed |
| `analytics` | 6 | token/cost accounting, wallets | keep |
| `api` | 56 | FastAPI routers (chat, git, files, users, …) | keep; remove `workspaces.py` with the module |
| `api_logs` | 4 | request log store | keep |
| `atlas` | 8 | project/dependency map | keep |
| `autonomy` | 8 | autonomous actor shifts | keep |
| `board_manager` | 5 | board/column CRUD | keep |
| `browser` | 17 | Chromium sessions, CDP, recording, downloads | keep (needs Docker socket) |
| `calendar` | 4 | calendar/events | keep |
| `changes` | 2 | change tracking | keep |
| `chat` | 12 | chat engine, turn store, recovery, knowledge | keep |
| `company` | 9 | company-first shell, advisors | keep |
| `computer` | 12 | co-desktops, VNC, OCR, images | keep (needs Docker socket; VMs need KVM) |
| `custom_tools` | 0 | namespace only | harmless |
| `efforts` | 2 | effort tiers | keep |
| `engine` | 16 | session/project engine, knowledge engine | keep; remove the `workspaces` import |
| `events` | 2 | event bus | keep |
| `file_manager` | 1 | file operations | keep |
| `guardrails` | 5 | policy/guardrail checks | keep |
| `harness` | 3 | background harness/watchdog | keep |
| `infra` | 8 | Docker/infra panel | keep (needs Docker socket) |
| `integrations` | 6 | external integrations | keep |
| `kanban` | 5 | kanban model | keep |
| `knowledge` | 21 | LanceDB + loaders | keep; no external vector service |
| `logs` | 7 | log explorer/store | keep |
| `mcp` | 3 | MCP client/connections | keep |
| `media` | 9 | media generation/providers | keep |
| `meets` | 10 | meetings, transcripts, minutes | keep |
| `mentions` | 1 | `@codx` mentions | keep |
| `metrics` | 3 | metrics | keep |
| `migrations` | 2 | settings/data migrations | keep |
| `misc` | 2 | helpers | keep |
| `model` | 16 | pydantic models (settings, providers, workspace) | keep; `workspace.py` goes with workspaces |
| `orchestration` | 9 | orchestrated runs/executor | keep |
| `organizer` | 7 | project organizing | keep |
| `plugins` | 1 | plugin loader | keep |
| `profiles` | 3 | built-in profiles (codx-junior, max, qa, ciso) | keep |
| `profiling` | 1 | profiling helpers | keep |
| `project` | 2 | project discovery/settings | keep |
| `prompts` | 1 | prompt loading | keep |
| `quality` | 1 | QA/quality gate | keep |
| `reviews` | 1 | code reviews | keep |
| `search` | 1 | search helpers | keep |
| `security` | 4 | users, JWT, OAuth, forward-auth | keep |
| `sio` | 4 | Socket.IO server | keep (single-process state) |
| `starter` | 0 | namespace only | harmless |
| `teams` | 4 | teams/channels/DMs | keep |
| `ticketing` | 3 | ticketing integration | keep |
| `tools` | 40 | agent tool registry | keep |
| `traefik` | 6 | route builder, Docker discovery, rules | keep; entrypoints now env-driven |
| `utils` | 6 | shared helpers | keep |
| `views` | 3 | saved views | keep |
| `voice` | 5 | voice conversation | keep |
| `web` | 7 | web fetch/render (optional Playwright) | keep; Playwright extra optional |
| `whisper` | 1 | transcription | keep |
| `wiki` | 8 | AI-maintained docs | keep |
| `workspaces` | 5 | dev-container workspaces + templates | **remove later** (installer already skips it) |

`api/codx/junior/api/workspaces.py`, `api/codx/junior/model/workspace.py`,
`project.get_project_workspaces`, the client `WorkspaceView`, the workspace
menus/routes and the `codx-junior/workspaces/` templates belong to the same
removal. `task_workspace.py` and `task_workspaces/` are **not** workspaces: they
are per-task git worktrees used by the harness and must stay.

### UI areas (client/src) — installer impact

`views/` + `components/` cover Chats, Kanban, Coworking/Browsers, Workspaces,
Teams/Messenger, Meets, Calendar, CISO, Knowledge, Wiki, Harness, Analytics,
Global/Project settings, Profiles, Desktops, File explorer, Infra, Traefik
rules. They are all served from the same compiled SPA; none of them needs a
separate container. Only Coworking/Browsers/Infra need the Docker socket, and
Workspaces is the area to remove with its API module.

---

## 8. What this change adds

| File | Purpose |
|---|---|
| `codx-junior-installer/install.sh` | the unified installer: modes, settings, validation, systemd, health, update/remove, DIND |
| root `install.sh` | thin front door (checkout, download or git fallback) |
| `cloud/bootstrap.sh` | compatibility shim; old cloud-init files keep working with plain-HTTP defaults |
| `tests/test_install.sh` | 58 stub-docker checks for help, validation, dry-run, settings, wrapper and modes |
| `runtime/docker-compose.yaml` | the single app definition |
| `runtime/docker-compose.local.yaml` | loopback publish |
| `runtime/docker-compose.master.yaml` | Traefik master + ACME + app routers |
| `runtime/docker-compose.slave.yaml` | Traefik slave + trusted forwarded headers |
| `runtime/docker-compose.coworking.yaml` | Docker socket |
| `runtime/docker-compose.build.yaml` | `--build` overlay |
| `runtime/docker-compose.dind-*.yaml` | inner publish for local/master/slave |
| `dind/Dockerfile`, `dind/entrypoint.sh` | new DIND image on the same runtime |
| `dind/docker-compose*.yaml` | outer wrapper for local/master/slave/sysbox |

The entry points (`install.sh`, `cloud/bootstrap.sh`) now delegate to the
unified installer. The legacy compose/DIND trees are untouched on purpose:
review the risks above, then delete them in a dedicated commit after the
migration in §7.
