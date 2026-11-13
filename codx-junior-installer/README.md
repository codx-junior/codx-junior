# codx-junior installer — how to deploy

Three deployments, one command each. Pick the one that matches what you are doing:

| You want to… | Deployment | Where | Containers |
|---|---|---|---|
| **try codx-junior**, show it to somebody, evaluate it | **demo** | [`demo/`](demo/) | **1** |
| hack on codx-junior itself (watch + hot reload) | **dev** | `codx-junior/` + `docker-compose.dev.yaml` | 2 + tools |
| run it for a team (TLS, workspaces, local models) | **server** | `codx-junior/` + profiles | up to 8 |

Start with the demo. It is the only path that needs no domain, no TLS, no vector
database and no build.

- Settings reference for the demo: [`demo/README.md`](demo/README.md)
- Why the deployment looks like this, and what is still open: [`FAST-DEPLOYMENT.md`](FAST-DEPLOYMENT.md)

---

## 0. One line: install or update

The entry point for a machine you do not control yet — or for updating one you
already run — is `install.sh` in the repository root:

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
```

It resolves the installer of the requested branch, installs Docker when it is
missing, clones or **updates** the repository and ends by verifying the
published port from the host. Exit code `0` means the health check passed.

| Situation | Command |
|---|---|
| first install | `curl … \| sudo bash -s -- --provider deepseek --key sk-…` |
| second run = upgrade | same command again (projects and settings are kept) |
| test a branch or tag | add `--branch my-feature` |
| see before acting | add `--dry-run` |
| no internet on the host | `git clone`, then `./install.sh --provider … --key …` |

Only `--provider` and `--key` are required (`ollama` needs no key). Every other
value has a default; `install.sh --help` is the full reference, and
[`cloud/README.md`](cloud/README.md) covers the unattended cloud-VM path.

---

## 1. Requirements

| | |
|---|---|
| Docker | 20.10+ with the `compose` v2 plugin (`docker compose`, not `docker-compose`) |
| RAM | 2 GB free for the demo (the full stack wants ~8 GB) |
| Disk | ~1.5 GB for the demo image |
| AI provider | one API key (OpenAI, DeepSeek, Gemini, OpenRouter, Mistral, Groq or a local Ollama) |

## 2. Quick start — demo deployment

```bash
git clone https://github.com/codx-junior/codx-junior.git
cd codx-junior/codx-junior-installer/demo
```

**Step 1 — choose a provider.** This prints every preset together with the models
codx-junior will configure for it, so you know what you are getting before you pay
for anything:

```bash
./up.sh --list-providers
```

**Step 2 — start it** with that provider and your key:

```bash
./up.sh --provider deepseek --key sk-xxxxxxxxxxxxxxxx
```

```
Projects folder: /home/you/codx-junior/codx-junior-installer/demo/projects
Pulling the demo image...
Starting codx-junior...
Waiting for the API to become healthy (first start ~30s)...
Configuring the AI provider from the environment...
=============================================================================
 codx-junior is running

   Web UI : http://localhost:19981
   Projects: /home/you/.../demo/projects
   Settings: /home/you/.../demo/.env
=============================================================================
```

**Step 3 — open <http://localhost:19981>**, create or pick a project and chat. The
provider, the models and the five global model slots are already configured, so
there is no settings page to fill in.

### What those two settings are

| Setting | Required | Example |
|---|---|---|
| `CODX_JUNIOR_AI_PROVIDER` | **yes** — `openai` `deepseek` `gemini` `openrouter` `mistral` `groq` `ollama` `custom` | `deepseek` |
| `CODX_JUNIOR_AI_API_KEY` | **yes** (not for `ollama`) | `sk-…` |
| `CODX_JUNIOR_AI_API_URL` | only for `custom` | `https://gw.acme.com/v1` |
| `CODX_JUNIOR_AI_MODEL` | no — the preset already picks one | `deepseek-chat` |
| `CODX_JUNIOR_PROJECTS_PATH` | no — default `./projects` | `/srv/projects` |
| `CODX_JUNIOR_WEB_PORT` | no — default `19981` | `8080` |

Flags, `.env` and environment variables are equivalent; `up.sh` writes the values
into `demo/.env` (mode 600, git-ignored) so later runs need no flags.

### Without a git clone

The image is self-contained, so once `codxjunior/codx-junior:demo` is published:

```bash
docker run -d --name codx-junior -p 19981:19981 \
  -v "$HOME/codx-projects:/home/codx-junior-projects" \
  -v codx-junior-settings:/home/codx-junior/.codx-junior \
  -e CODX_JUNIOR_AI_PROVIDER=deepseek \
  -e CODX_JUNIOR_AI_API_KEY=sk-xxxxxxxxxxxxxxxx \
  codxjunior/codx-junior:demo
```

> **Until the image is published** `docker compose pull` fails and `up.sh` falls
> back to a local build (a few minutes). Pass `--build` to make that explicit, and
> see ["publishing the image"](#7-publishing-the-image) below.

### On a cloud VM — one command, fully unattended

On a remote server you do not need to clone anything by hand. This installer
installs Docker, downloads the repository, writes the settings, starts the
container, waits for the health check and prints the URL:

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/codx-junior-installer/cloud/bootstrap.sh -o /tmp/codx-junior.sh
sudo bash /tmp/codx-junior.sh --provider deepseek --key sk-xxxxxxxx
# -> CODX_JUNIOR_READY http://<server-ip>:19981
```

It is idempotent (re-running upgrades) and it also installs a small operations
CLI: `codx-junior status | logs | restart | upgrade | down`. Hetzner,
DigitalOcean, AWS and GCE examples, the full settings reference, HTTPS with a
domain and the troubleshooting table are in **[cloud/README.md](cloud/README.md)**.

## 3. What the demo includes — and what it leaves out

The demo is **one container**: FastAPI + the background worker + nginx serving the
built SPA, published on `19981`. There is no Traefik, no LocalAI, no second API
container, no init container, no Node at runtime and nothing is installed when the
container restarts.

| Included | Left out (and how to get it) |
|---|---|
| chat, agent turns, project/file browsing, the web UI | **Traefik / TLS / custom domain** → server tier |
| AI provider + models configured on first start | **LocalAI** → server tier, `--profile local-models` |
| knowledge/RAG **built in** — embedded LanceDB, nothing extra to run (see §4) | **workspaces, browser/computer sessions** → need `/var/run/docker.sock`, commented in `docker-compose.yaml` |
| document editing, git operations | **PDF/DOCX export, OCR** → `.[tex]` / tesseract are not in the slim image |

Full detail, per-component, in [`demo/README.md`](demo/README.md).

## 4. Knowledge / RAG — built in, no extra service

Knowledge is a **built-in** feature, not an optional profile. The vector store is
**LanceDB**, embedded (on-disk, one process) inside the API container: there is no
Milvus to start, no extra container, no `--knowledge` flag and no RAM to reserve.

The backend is chosen with one variable:

| `CODX_JUNIOR_KNOWLEDGE_BACKEND` | What runs | When |
|---|---|---|
| `lancedb` *(default)* | embedded, no service | demo, laptop, single VM |
| `milvus` | your external Milvus (`pip install 'codx-junior[milvus]'`) | existing data, large corpora |
| `none` | nothing | knowledge switched off |

- **Hybrid search is native**: BM25 (full-text) + dense vectors fused with RRF,
  the same shape the Milvus backend returns — the API and the UI do not know which
  backend is in use.
- **RAG also needs an embeddings model.** `deepseek`, `openrouter` and `groq` do not
  provide one; on those presets search runs **lexical only** instead of failing.
  `openai`, `gemini`, `mistral` and `ollama` presets configure one automatically.
- **It degrades, never breaks.** If the embeddings stack cannot load, the dense leg
  is switched off (one warning in the log) and the lexical leg keeps answering.
  Knowledge never prevents the API from booting.

To reuse an existing Milvus instead of the embedded store:

```bash
# .env
CODX_JUNIOR_KNOWLEDGE_BACKEND=milvus
CODX_JUNIOR_MILVUS_URL=http://my-milvus:19530
```

## 5. Development deployment (watch + hot reload)

Use this when you change codx-junior itself, not to show it to users.

There is **no `docker-compose.dev.yaml`**: the source mount and `DEBUG` are hardcoded
in `docker-compose.yaml`, where the api and api-background services run with `DEBUG=1`
and mount `../../.` over the image. That compose file *is* the dev setup.

```bash
cd codx-junior-installer/codx-junior
cp .env.example .env
docker compose -f docker-compose.yaml up -d              # images must exist already
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

What it gives you: the source tree mounted over the image, `DEBUG=1` on the API,
`uv sync` + the recursive `chown` on every start, and the API watching when
`CODX_JUNIOR_API_RELOAD=1`. Every project folder under
`CODX_JUNIOR_PROJECTS_PATH` is visible at once.

> The **web client** runs `npm run preview` (a production build in a Node server)
> because its service sets `DEBUG=0`. Set `DEBUG=1` on `codx-junior-client` in
> `docker-compose.yaml` to get the Vite dev server and HMR instead.

That deployment is deliberately generous with resources. To stop paying for the
installs on every restart, and to make the images slimmer, set:

| Variable | Effect |
|---|---|
| `CODX_JUNIOR_SKIP_INSTALL=1` | skip the recursive `chown` + `uv sync` that `scripts/run_api.sh` runs on every start |
| `CODX_JUNIOR_API_EXTRAS` | extras installed by `scripts/install_api.sh` (default `local-models,browser`, empty for a slim install) |
| `CODX_JUNIOR_API_RELOAD=1` | uvicorn `--reload`; off by default because a reload interrupts in-flight agent turns |

If you only need the **full feature set** (local embeddings, Playwright, TeX, OCR)
without editing the source, that is the server tier below.

## 6. Server deployment (TLS, workspaces, local models)

Same compose project as the dev deployment, without the build overlay:

```bash
cd codx-junior-installer/codx-junior
cp .env.example .env          # set CODX_JUNIOR_DOMAIN and the ACME email
docker compose -f docker-compose.yaml up -d
```

The default `up` still brings the whole 8-service stack (api, api-background, client,
milvus, localai, traefik and two init containers). The profile split — `proxy`,
`local-models`, `dev`, with production as the default — is item 5 of
["still open" in `FAST-DEPLOYMENT.md`](FAST-DEPLOYMENT.md) §7.5. Knowledge is no
longer one of those profiles: it is built into the API (see §4).

`codx-junior-dind/` wraps this stack in docker-in-docker (Sysbox) for the
one-VM-per-customer product and can additionally install Coolify; it is a separate
deliverable and not part of the trial path.

## 7. Publishing the image

`demo/up.sh` is pull-first. Publish once from a machine that has the repo:

```bash
docker build -t codxjunior/codx-junior:demo \
  -f codx-junior-installer/demo/Dockerfile .
docker push codxjunior/codx-junior:demo
```

The build context is the **repository root** (the Dockerfile copies `client/`,
`api/`, `scripts/`), which is why the command is run from the root and why the root
`.dockerignore` matters.

## 8. Operating the demo

```bash
cd codx-junior-installer/demo
./up.sh                       # start (idempotent, re-applies nothing)
./up.sh --build               # build from source instead of pulling
./up.sh --port 8080           # another web port
./up.sh --projects /srv/projects
docker compose logs -f codx-junior
docker compose down           # stop; settings and projects are kept
docker compose down -v        # stop and delete settings + analytics
```

Upgrading: `CODX_JUNIOR_IMAGE=codxjunior/codx-junior:demo` in `.env`, then
`docker compose pull && docker compose up -d`. The two named volumes
(`codx-junior-settings`, `codx-junior-analytics`) survive the upgrade; the projects
folder is a bind mount and is never touched.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `ERROR: codx-junior needs an AI provider before it can do anything` | you did not pass `--provider` + `--key` (and `.env` has no values yet) |
| Container is `unhealthy` / the script prints logs and exits | `docker compose logs --tail 100 codx-junior`; the API log is `api.log` in `$CODX_SUPERVISOR_LOG_FOLDER` |
| UI opens, chat says "no model configured" | the bootstrap did not run: `docker exec codx-junior-demo curl -s localhost:19980/api/bootstrap` |
| `401 / invalid api key` in the logs | wrong key, or a `custom` URL without the `/v1` suffix |
| Chat works, knowledge search finds nothing | check the embeddings model for the preset (see §4): `deepseek`, `openrouter` and `groq` have none, so search stays lexical |
| Port 19981 already in use | `./up.sh --port 8080` |
| Projects folder empty / permission denied | `./up.sh --projects /absolute/path`; the container runs as root so it can write to host folders |
| `pull access denied` for `codxjunior/codx-junior:demo` | the tag is not published yet — add `--build` |

## 10. Architecture

```
codx-junior                          the application image
  ├── FastAPI (19980)                api + background worker, supervisord
  ├── nginx (19981)                  serves the built SPA, proxies /api + socket.io
  └── volumes                        settings, analytics

demo/                                tier 1: that image, one container
codx-junior/                         tier 2/3: compose stack (api, client, milvus,
                                     localai, traefik, workspaces)
codx-junior-dind/                    docker-in-docker wrapper (Sysbox) + Coolify
vllm/, litellm/, localai/            optional model-serving sidecars
```

RAG internally is one `KnowledgeDB` interface with two interchangeable backends,
selected by `CODX_JUNIOR_KNOWLEDGE_BACKEND` in
`api/codx/junior/knowledge/knowledge_backend.py`: **LanceDB** (default, embedded,
`knowledge_lancedb.py`) and **Milvus** (`knowledge_milvus.py`, imported lazily so a
LanceDB install never pulls `pymilvus`). The LLM layer has no local model manager: it talks to any
OpenAI-compatible endpoint (`/chat/completions` + `/embeddings`), configured from the
provider presets in `api/codx/junior/bootstrap.py`.
