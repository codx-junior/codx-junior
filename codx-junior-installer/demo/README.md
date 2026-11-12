# codx-junior — demo deployment

One container, one command, one API key.

```bash
cd codx-junior-installer/demo
./up.sh --provider deepseek --key sk-xxxxxxxxxxxx
# -> http://localhost:19981
```

This is the **tier 1** deployment: what you give somebody who wants to try
codx-junior. It is pull-only (nothing is compiled on their machine), it does not
run Milvus, Traefik, LocalAI, a second API container, an init container or a
Node server, and restarting it installs nothing.

> Until `codxjunior/codx-junior:demo` is published to Docker Hub, `up.sh` warns and
> falls back to a local build (a few minutes). Pass `--build` to make that explicit.
> How to deploy the other tiers: [`../README.md`](../README.md).

| | Full deployment (`codx-junior/docker-compose.yaml`) | Demo (this folder) |
|---|---|---|
| Containers | 8 | **1** (+1 optional) |
| Image size | several GB | **~1 GB** |
| First start | build + `pip install torch/vLLM` + `npm i` | **pull + 30 s** |
| Install steps at boot | `chown -R $HOME`, `uv sync` | **none** |
| Ports to publish | 80, 443 (+ routing) | **19981** |
| Needs a domain / TLS | yes (Traefik + ACME) | **no** |
| What you must configure | domain, ACME, LiteLLM keys, Milvus, LocalAI models | **one provider + one key** |

---

## 1. What you must pass

Only two settings are mandatory. Everything else has a working default.

| Setting | Required | Example | Purpose |
|---|---|---|---|
| `CODX_JUNIOR_AI_PROVIDER` | **yes** | `deepseek` | Which OpenAI-compatible provider to use |
| `CODX_JUNIOR_AI_API_KEY` | **yes** | `sk-…` | Key of that provider |
| `CODX_JUNIOR_AI_API_URL` | only for `custom` | `https://gw.acme.com/v1` | Override the preset endpoint |
| `CODX_JUNIOR_AI_MODEL` | no | `deepseek-chat` | Override the preset chat model |
| `CODX_JUNIOR_PROJECTS_PATH` | no | `~/my-projects` | Host folder with your repositories (default `./projects`) |
| `CODX_JUNIOR_WEB_PORT` | no | `8080` | Web UI port (default `19981`) |

You can pass them on the command line (`./up.sh --provider … --key …`), or write
them in `.env` (copy `.env.example`), or export them in your shell.

### Provider presets

`CODX_JUNIOR_AI_PROVIDER` selects a preset; codx-junior fills in the base URL,
the chat model, the wiki model and the embeddings model for you.

| Provider | Base URL used | Default chat model | Embeddings model | Get a key |
|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `gpt-4.1-mini` (`gpt-4.1` for wiki) | `text-embedding-3-small` (1536) | platform.openai.com/api-keys |
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-chat` | *none* → RAG disabled | platform.deepseek.com/api_keys |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.0-flash` | `text-embedding-004` (768) | aistudio.google.com/app/apikey |
| `openrouter` | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` | *none* → RAG disabled | openrouter.ai/keys |
| `mistral` | `https://api.mistral.ai/v1` | `mistral-large-latest` | `mistral-embed` (1024) | console.mistral.ai/api-keys |
| `groq` | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | *none* → RAG disabled | console.groq.com/keys |
| `ollama` | `http://localhost:11434/v1` | `llama3.1` | `nomic-embed-text` (768) | local, no key |
| `custom` | `CODX_JUNIOR_AI_API_URL` | `CODX_JUNIOR_AI_MODEL` | `CODX_JUNIOR_AI_EMBEDDINGS_MODEL` | — |

Notes:

* **Everything goes through the `openai` client.** Any gateway exposing
  `/chat/completions` + `/embeddings` with a `/v1` path works as `custom`
  (LiteLLM, Portkey, vLLM, Ollama, LM Studio, Azure OpenAI proxy…).
* **Anthropic** has no OpenAI-compatible endpoint. Run LiteLLM in front of it
  and use `--provider custom --url http://litellm:4000/v1`.
* Providers without an embeddings endpoint keep **knowledge/RAG search disabled**
  (the rest of the app works). Add an embeddings model later from *Global
  settings → AI models*.

---

## 2. What codx-junior configures by itself

On the first start the API reads the settings above and, **only if no provider
has been configured yet**, it:

1. creates the `AIProvider` (name = the provider you chose) with its base URL
   and key;
2. creates the models `knowledge` (chat/coding/RAG/guardrail) and `embeddings`
   (plus a `wiki` entry when the preset uses a different model for the wiki);
3. points the five global model slots (`llm_model`, `rag_model`, `wiki_model`,
   `guardrail_model`, `embeddings_model`) at them;
4. writes a summary to `$CODX_JUNIOR_CONFIG_FOLDER/bootstrap.json`.

Result: you open the UI, pick a project, and the chat works. No settings page,
no model import, no "no model configured" dead end.

It is **idempotent and non-destructive**: an installation already configured
from the UI is never touched. To re-apply (e.g. after rotating a key):

```bash
docker exec codx-junior curl -fsS -X POST http://127.0.0.1:19980/api/bootstrap/apply
docker exec codx-junior curl -fsS http://127.0.0.1:19980/api/bootstrap   # inspect
```

`CODX_JUNIOR_AI_FORCE=1` makes every start re-apply it;
`CODX_JUNIOR_AI_BOOTSTRAP=0` disables it entirely.

The key itself is **not** written to the settings files: the provider stores
`$CODX_JUNIOR_AI_API_KEY` and it is expanded from the environment at use time
(`global_settings.get_provider_settings`).

---

## 3. What this deployment leaves out

| Component of the full stack | Why it is not here | How to get it back |
|---|---|---|
| **client** container (Node + `vite preview`) | the SPA is compiled in a builder stage and served by nginx | never needed |
| **api-background** container | runs as a supervisord program in the same container | never needed |
| **traefik** | same-origin: nginx proxies `/api` to the API | add a proxy in front, or use the server tier |
| **localai** | replaced by "bring your own provider key" | `codx-junior/docker-compose.yaml` |
| **milvus** | not needed: the vector store is embedded LanceDB, running in the API process | never needed (point at your own Milvus only if you have data in it) |
| **analytics-init / codx-junior-init** | their `mkdir`/`chown` work is done by the entrypoint | — |
| torch / sentence-transformers / vLLM / Playwright / TeX / Tesseract / Firefox | runtime deps of optional features; now `pip` extras (`.[local-models,browser]`) | full deployment installs them |

Features **not available** in the demo without extra work: local (offline)
embeddings, document → PDF/DOCX conversion, Tesseract OCR, browser/computer
sessions and workspaces (they need `/var/run/docker.sock`).

### Knowledge / RAG: built in, no vector database to run

Knowledge is a **built-in** feature of this deployment: the vector store is
**LanceDB**, embedded on disk and running in the API process. There is no Milvus
container, no `--knowledge` flag and no extra RAM.

- **One switch**: `CODX_JUNIOR_KNOWLEDGE_BACKEND` — `lancedb` (default,
  embedded), `milvus` (your external server, needs
  `pip install 'codx-junior[milvus]'`) or `none`.
- **Native hybrid search**: BM25 full-text + dense vectors fused with RRF — the
  same result shape as the Milvus backend, so the UI is identical.
- **RAG also needs an embeddings model.** `deepseek`, `openrouter` and `groq` do
  not provide one, so on those keys search stays **lexical** (full-text) instead
  of failing; `openai`, `gemini`, `mistral` and `ollama` configure one
  automatically.
- **It degrades, never breaks.** If the embeddings stack cannot load, the dense
  leg is switched off with a single log warning and the lexical leg keeps
  answering; knowledge never stops the API from booting.

So there is nothing to enable: the chat, the projects, the agent, the file tools
and knowledge search all work with one container.
To enable the docker-dependent features, uncomment the socket mount in
`docker-compose.yaml`. Be aware that it gives the container control of the host
Docker daemon.

---

## 4. Operating it

```bash
./up.sh                      # start (idempotent)
./up.sh --build              # build the image from source instead of pulling
docker compose logs -f codx-junior
docker compose down          # stop (volumes are kept)
docker compose down -v       # stop and delete settings/analytics
```

Ports and volumes:

| | |
|---|---|
| `19981` | Web UI (published) |
| `19980` | API (internal, proxied by nginx) |
| volume `codx-junior-settings` | global settings, per-project `.codx`, presets |
| volume `codx-junior-analytics` | token usage, AI logs, API metrics |
| bind `${CODX_JUNIOR_PROJECTS_PATH}` | your repositories |

The health check is `GET /api/health`. `docker compose ps` shows `healthy` when
the API answers.

---

## 5. Development deployment (unchanged)

The watch/reload deployment is `codx-junior/docker-compose.yaml` (+ the
`docker-compose.build.yaml` overlay when the images still have to be built). There
is **no `docker-compose.dev.yaml`**: the source mount and `DEBUG=1` are hardcoded in
that compose file, `scripts/run_api.sh` syncs the environment on start, and the web
client only runs the Vite dev server when you switch its service to `DEBUG=1`
(it ships with `DEBUG=0`, i.e. `npm run preview`). Use it when you hack on
codx-junior itself, not to show it to a user.

Two knobs make the source deployment behave like this one:

* `CODX_JUNIOR_SKIP_INSTALL=1` — skip the recursive `chown` and `uv sync` that
  `scripts/run_api.sh` runs on every start.
* `CODX_JUNIOR_API_EXTRAS` — extras installed by `scripts/install_api.sh`
  (default `local-models,browser`, empty for a slim install).

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| UI loads, chat answers "no model configured" | the bootstrap did not run: check `docker exec codx-junior curl -s localhost:19980/api/bootstrap` and that `CODX_JUNIOR_AI_API_KEY` is set in `.env`. |
| `401` / `invalid api key` in the logs | wrong key, or `CODX_JUNIOR_AI_API_URL` missing `/v1`. |
| Chat works, "knowledge search" finds nothing | check the embeddings model for the preset (see §3): `deepseek`, `openrouter` and `groq` have none, so search stays lexical |
| Container is `unhealthy` | `docker compose logs --tail 100 codx-junior`; the API log is `api.log` inside `$CODX_SUPERVISOR_LOG_FOLDER`. |
| Port 19981 already in use | `./up.sh --port 8080`. |
| Projects folder empty / permission denied | `./up.sh --projects /absolute/path`; the demo container runs as root so it can write to host folders. |

Deeper analysis of the original deployment and the full change list:
[`../FAST-DEPLOYMENT.md`](../FAST-DEPLOYMENT.md).
