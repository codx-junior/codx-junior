# Fast deployment analysis — codx-junior

Status: **partly implemented.** The demo tier (§7) is in the tree and on `main`;
the rest of the change list is still a proposal — see §7.5 for what is open.

Scope: `codx-junior-installer/*`, `scripts/install_*.sh`, `scripts/run_*.sh`,
`api/pyproject.toml`, `api/codx/junior/bootstrap.py`.

Goal: make `codx-junior` **cheap to build, cheap to pull and one command to run**, so
people can try it in minutes instead of waiting for a multi-gigabyte build.

How to deploy: [`README.md`](README.md) (quick start) and [`demo/README.md`](demo/README.md)
(settings reference).

---

## 0. Decisions taken

| Question | Decision | Status |
|---|---|---|
| Vector store | **LanceDB replaces Milvus** as the default for the trial/local tiers. Embedded: no container, still native BM25 + dense + RRF. Milvus only for the server tier. | ✅ implemented — `knowledge_lancedb.py` behind `CODX_JUNIOR_KNOWLEDGE_BACKEND` (default `lancedb`), 15 tests |
| LocalAI | Replaced by "bring a provider key": `bootstrap.py` presets fill the base URL, the models and the five global slots. `localai` stays as an opt-in profile for offline demos | ✅ implemented |
| Traefik | Opt-in `--profile proxy` only; the demo serves the SPA through nginx in-process instead | ⏳ demo done, server compose not split yet |
| DEV deployment | Kept — as an overlay/profile, never the default | ⏳ DEV is still the only compose that mounts the source |
| Client container | Gone: builder stage + nginx inside the single image | ✅ implemented in the demo tier |

> **Knowledge is built in.** The vector store is LanceDB, embedded in the API
> process: no container, no profile, no flag. Milvus is now a *choice* for
> installations that already hold data in it or that outgrow an embedded index
> (`CODX_JUNIOR_KNOWLEDGE_BACKEND=milvus`, `pip install 'codx-junior[milvus]'`).
> §1 and §2 describe the stack we deploy *today* and still list Milvus, which is
> exactly what the demo tier removes.

## 1. What we deploy today

`codx-junior-installer/codx-junior/docker-compose.yaml` starts **8 services**
(2 of them one-shot init containers):

| # | Service | Image | Why it is there | Verdict |
|---|---------|-------|-----------------|---------|
| 1 | `codx-junior-api` | `codxjunior/codx-junior:api` (built) | FastAPI, `shm_size: 4gb`, mounts host `docker.sock`, mounts the **source tree** over the image | keep (slim) |
| 2 | `codx-junior-api-background` | same image | second full Python stack (`CODX_JUNIOR_API_BACKGROUND=1`), `shm_size: 4gb` | collapse into #1 |
| 3 | `codx-junior-client` | `codxjunior/codx-junior:client` (built) | Node + nvm + `node_modules`, runs `npm run preview` | replace |
| 4 | `milvus-codx-junior` | `milvusdb/milvus:v2.5.11` | vector store for knowledge/RAG (embedded etcd) | **drop it**: the default backend is now embedded LanceDB (§6.1). Keep only for existing data / large corpora |
| 5 | `localai-codx-junior` | built `FROM localai/localai:latest` | "optional local models", but started by default, `shm_size: 20gb` | move to a profile |
| 6 | `traefik-codx-junior` | `traefik:latest` | TLS + `forwardAuth` + workspace routing, ports 80/443, `docker.sock:ro`, ACME | move to a profile |
| 7 | `codx-junior-analytics-init` | `codxjunior/codx-junior:debian` | one-shot `mkdir` + `chown` of a volume | delete |
| 8 | `codx-junior-init` | `codxjunior/codx-junior:debian` | one-shot `initial-setup.sh`, `depends_on: localai` | delete |

The stack is wrapped again in `codx-junior-dind` (dockerd inside a container,
`runtime: sysbox-runc`), which re-runs the whole compose on boot and can
optionally download and install the **Coolify** stack (`coolify-install.sh`).

### What actually makes it slow and heavy

1. **Python dependency set is far bigger than the code needs.**
   `api/pyproject.toml` declares `vllm>=0.5.0`, `torch>=2.4.0`,
   `sentence-transformers>=6.2.0` and `playwright` as mandatory dependencies.
   * `codx/junior/ai/vllm_cpu_ai.py` is literally headed `## DEPRECATED: Using vllm docker openai compatible`.
   * `sentence_transformers` is imported **lazily** with a friendly `ImportError`
     (`knowledge/embeddings.py:80-86`).
   * No `import torch` exists anywhere in the API.
   These packages are installed on every build (`install_api.sh`
   → `uv pip install -e . --extra-index-url https://pytorch.org`) and are the
   single biggest contributor to image size and build time.
2. **OS package set is far bigger than the code needs.**
   `debian/Dockerfile.debian` installs `texlive-xetex` + `texlive-fonts-recommended`;
   the monolithic `codx-junior/Dockerfile` installs the whole `texlive` metapackage,
   plus `firefox-esr`, `tesseract-ocr`, `pandoc`, `fuse3`, `fuse-overlayfs`.
   TeX Live alone is commonly 1.5–2 GB compressed-to-layered. PDF/OCR conversion is
   a per-project, on-demand feature (`doc_converter`, `explain_image`), not a
   requirement to read a chat.
3. **The web client is built inside the runtime image.**
   `scripts/install_client.sh` does `rm -rf node_modules`, `nvm install v24.12.0`,
   `npm i`, `npm run build-only`. No layer caching (the `COPY`s run first), Node is
   re-downloaded on every build, and the final image keeps the full toolchain plus
   `node_modules` even though the container only runs `npm run preview`.
4. **`run_api.sh` installs at runtime.** On every container start it runs
   `sudo chown -R $HOME`, and `uv sync` (unlocked resolution, network) — and if the
   venv is missing it calls `install_api.sh`, i.e. a **cold container installs torch
   and vllm while the user waits**. `api/uv.lock` exists but is not used.
5. **No root `.dockerignore`.** The build context is the repository root
   (`context: ../..`), so `client/node_modules`, `.codx/`, `wiki/`,
   `docs/node_modules`, `design/`, `.git` are shipped to the daemon on every build.
6. **Duplicated layers in the monolithic Dockerfile.** `base` copies
   `client/ api/ scripts/ codx-junior/ …`, then the next stage copies them all
   again; two full copies end up in the image.
7. **Two uvicorn stacks × 8 workers.** `run_api.sh` defaults to
   `--workers ${WEB_CONCURRENCY:-8}`; the API and the background worker are
   separate containers with separate 4 GB `shm`, so a default install holds up to
   16 Python workers, each importing langchain/llama-index.
8. **Production compose mounts the source tree** (`../../.:/home/codx-junior/codx-junior`),
   which forces the dev layout on every install and shadows the image content.

Estimated effect (order of magnitude, to be measured): image pull ~6–9 GB and a
first build of tens of minutes, versus ~0.6–1.2 GB and a few minutes for the slim
path; idle RAM from several GB down to well under 2 GB.

---

## 2. Do we need all the services?

| Service | Needed to try the product? | Lighter alternative |
|---|---|---|
| API | yes | slim image, one process tree |
| API background worker | yes (indexing, orchestration worker) | run it as a `supervisor.conf` program **inside the API container** — `supervisor.conf` already defines `codx-junior-api`, `codx-junior-api-background` and `codx-junior-client` |
| Client | yes (the UI) | **static build** (multi-stage `node` builder) served by `nginx:alpine` (~10 MB) *or* by FastAPI `StaticFiles` → 0 extra containers |
| Milvus | **no** — LanceDB is embedded and built in | removed from the default path: **LanceDB** is the default backend behind the same `KnowledgeDB` interface (§6.1), no container and no profile. Milvus remains as an explicit choice (`CODX_JUNIOR_KNOWLEDGE_BACKEND=milvus`) and as the server-tier backend |
| Traefik | no for a trial (`localhost:19981` is enough) | profile `proxy`: needed only for TLS, custom domains and workspace routing |
| LocalAI | no | profile `local-models`; users bring OpenAI / Ollama / vLLM |
| analytics-init | no | create the volume in the entrypoint (`mkdir -p` + `chown` before dropping privileges) or use a bind mount prepared by the installer |
| codx-junior-init | no | fold into the API entrypoint / remove `depends_on: localai` |
| DIND wrapper | only for the hosted/"one VM per customer" product | keep as a separate deliverable, not on the trial path |
| Coolify | no | it is already opt-in (`COOLIFY_ENABLED`), keep it out of the default image |

---

## 3. Target: three tiers, one command each

### Tier 1 — Trial (super simple, what we advertise)

```sh
docker run -d --name codx-junior -p 19981:19981 \
  -v "$HOME/codx-projects:/home/codx-junior-projects" \
  -v codx-junior:/data \
  -e CODX_JUNIOR_DOMAIN=localhost \
  codxjunior/codx-junior:latest
```

* one pulled image, no build, no clone, no `.env`, no DNS, no certificate
* `supervisor` starts API + background worker + static client inside it
* **no vector database at all**: with no reachable Milvus the API still boots
  (import-time connect is wrapped in `try/except`) and only knowledge *queries*
  fail. RAG is opt-in, never an installation prerequisite
* the user passes one provider + one API key; `bootstrap.py` creates the provider,
  the models and the five global slots — plus a project folder

### Tier 2 — Local compose (developers / self-host, no TLS)

api (+ worker in-process) + **knowledge built in** (embedded LanceDB, no
service), published ports, no Traefik, no LocalAI, no client container, no init
containers. `docker compose up -d` on a repo checkout, ready in minutes.

### Tier 3 — Server (full, current behaviour)

Tier 2 **plus** `--profile proxy` (Traefik: TLS, `forwardAuth`, workspace routes),
`--profile local-models` (LocalAI/Ollama), workspaces and DIND. Same compose file,
opt-in profiles instead of a second file to maintain.

---

## 4. Change list, ranked by impact / effort

Cheap and high impact (do first):

1. **Root `.dockerignore`** (context is the repo root):
   `client/node_modules`, `**/node_modules`, `**/dist`, `.git`, `.codx`,
   `wiki`, `docs`, `design`, `*.pdf`, `*.png`, `.venv`, `__pycache__`.
   Removes GBs of context transfer from every build.
2. **Split `api/pyproject.toml` extras.**
   * base: keep the runtime web/RAG stack;
   * `local-embeddings = ["sentence-transformers", "torch"]` (CPU index);
   * `browser = ["playwright"]`;
   * delete `vllm` (deprecated path) — or move it to `[project.optional-dependencies].vllm` and keep the docker-based flow that the code already documents.
   Build with `uv sync --frozen --no-dev` and **never** `uv sync` at container start.
3. **Trim the base image.** Drop `texlive*`, `firefox-esr`, `fuse3`,
   `fuse-overlayfs`, `tesseract-ocr`, `pandoc` from the default image; install them
   in an optional `codx-junior:tools` image or on demand. Use `python:3.11-slim` as
   the base instead of `debian:12 + apt python3.11`.
4. **Multi-stage client build.** `node:24-alpine` builder → `vite build` → static
   output served by `nginx:alpine` (profile) or mounted by FastAPI (`StaticFiles`,
   add a `client_dist` mount + SPA fallback). Delete `install_client.sh` from the
   runtime path and `run_client.sh`'s `nvm install`.
5. **Stop doing work at container start.** In `run_api.sh` remove `sudo chown -R $HOME`
   and `uv sync`; do permissions once in the entrypoint (targeted `chown` on the
   data volume only). Same for `run_client.sh`.
6. **Single-process group.** Use the existing `supervisor.conf` inside the API
   container for `api` + `api-background`; keep `WEB_CONCURRENCY` at 2–4 by default
   with an env override; drop `shm_size: 4gb` unless Playwright/browser work needs it.

Medium:

7. **Delete the init containers.** `codx-junior-analytics-init` → entrypoint
   `mkdir/chown`; `codx-junior-init` → onboarding in the API or a documented step;
   drop `depends_on: localai`.
8. **Profiles instead of a monolith.** `proxy` (traefik), `local-models` (localai),
   `workspaces`. Default `up` should start only what is needed to see the UI.
9. **Remove the dev bind mount** `../../.:/home/codx-junior/codx-junior` from the
   production compose (keep it in an override file for development).
10. **Real installer script.** `installer.sh` (root) currently is 3 commands with no
    error handling: `docker-compose up codx-junior-build` (foreground, old binary
    name), then `docker network create` (fails if it exists), then `up -d`.
    Replace with: preflight (docker/compose version, ports, RAM), `.env` bootstrap
    from `.env.example`, `pull` by default / `--build` opt-in, `docker network create … || true`,
    `up -d --wait`, then print the URL. `install.sh`/`install_api.sh`/`install_client.sh`
    become build-time only.

Later (needs design + tests):

11. ~~**Make knowledge optional at runtime**~~ — **done**: the API boots and serves
    the UI with no vector store; a missing/broken backend degrades to lexical
    search (`NullKnowledgeDB` when disabled).
12. ~~**Embedded vector store** (LanceDB)~~ — **done**: `knowledge_lancedb.py`,
    default backend, behind the existing `KnowledgeDB` interface.
13. **Single-image release** (`codxjunior/codx-junior:latest`) published to a registry
    so Tier 1 is pull-only, and a `docker compose` file that only *references* it.

---

## 5. Definition of done for the trial tier

* `docker run … codxjunior/codx-junior:latest` → UI on `http://localhost:19981`
  in under a minute on a laptop, without cloning the repo.
* Image size ≤ ~1.2 GB and no `torch`/`vllm`/TeX/Node inside it.
* Cold `docker compose up` from a clean cache ≤ ~5 min.
* `docker compose up` with no `--profile` starts the API (and worker) only; adding
  `--profile proxy` and `--profile local-models` adds the rest. Knowledge is built
  in and needs no profile.
* Restarting a container does no installs and no recursive `chown`.
* Documented in `docs/getting-started.md` as the first path, with the full stack
  described as the "server" profile.

---

## 6. Review answers (three open points)

### 6.1 Are sqlite-vec / LanceDB equal to Milvus?

**Decision: LanceDB.** It is the default target for the trial and local tiers;
Milvus is kept for the server tier only. Status: not implemented yet — see the
end of this section.

What the code actually uses (`api/codx/junior/knowledge/knowledge_db.py`):

| Capability used today | Where | Milvus | LanceDB | sqlite-vec |
|---|---|---|---|---|
| Public surface callers need | `:272-280` (`index_documents`, `delete_documents`, `search`, `search_sparse`, `search_vector`, `search_hybrid`, `raw_search`, `get_all_sources`, `get_all_categoties`) | ✅ | ✅ | ✅ (hand-written) |
| BM25 lexical leg | `Function(name="text_bm25_emb", …)` `:419` | ✅ built-in | ✅ native FTS (Tantivy) | ⚠️ FTS5, different tokenizer |
| Dense leg, 384d MiniLM | `CODX_JUNIOR_DENSE_DIM` `:65` | ✅ | ✅ (IVF-PQ/HNSW) | ⚠️ brute force only |
| Sparse+dense fusion in one call | `AnnSearchRequest` + `RRFRanker`/`WeightedRanker` + `hybrid_search` `:1207-1223` | ✅ | ✅ `search(query_type="hybrid")` + RRF | ❌ write RRF yourself (~50 LOC) |
| Metadata filters (`source != ""`), JSON field | `NON_EMPTY_SOURCE_FILTER`, `FIELD_METADATA` | ✅ | ✅ `where=` | ⚠️ manual |
| Runs as its own service | `connect_milvus_client()` **at import** `:86` | required | none (embedded) | none (file) |

Verdict:

* **LanceDB ≈ equal (better for us)** for a single node up to millions of chunks:
  both search legs, RRF and filters exist natively, and it removes the container
  *and* the import-time connection (`:86`) that currently prevents the API from
  booting without Milvus. Different API, so it is an adapter, not a config flag.
* **sqlite-vec is lighter but not equal**: it is vector-only. BM25 would come from
  FTS5 (weaker code tokenizer) and the hybrid fusion is ours to write. Not worth it
  while lexical search is a core feature; it stays the "zero dependency" fallback.
* **Milvus is a choice, not the default.** LanceDB is what runs unless you ask
  otherwise; Milvus stays for the server tier (multi-project, big corpora, existing
  data) via `CODX_JUNIOR_KNOWLEDGE_BACKEND=milvus`.
* **Implementation status: done.** `api/codx/junior/knowledge/knowledge_lancedb.py`
  implements the full `KnowledgeDB` surface (index/delete/search/hybrid/raw_search/
  sources/categories/metrics/reset) on a real LanceDB dataset — no server, no
  network. `knowledge_backend.py` holds the single selector
  (`CODX_JUNIOR_KNOWLEDGE_BACKEND`: `lancedb` · `milvus` · `none`) and a
  `NullKnowledgeDB`, and imports the Milvus module **lazily**, so a LanceDB install
  never pulls `pymilvus` (now an optional extra, `codx-junior[milvus]`).
* **15 tests** in `api/tests/test_knowledge_lancedb.py` cover indexing, native FTS
  (`INVERTED`/BM25), hybrid fusion, the Milvus filter syntax, dedup on reindex,
  metrics parity, dimension change ⇒ rebuild, and degradation when the embeddings
  stack is broken.
* **Embedding dimension** is stored in `index_meta.json` per table and a dimension
  change rebuilds the table, so switching from the 384d local model to (say) 1536d
  provider embeddings reindexes instead of corrupting the index.

### 6.2 Replace localai with provider key presets

Facts: there is **no `litellm` and no `anthropic` import** anywhere in `api/`; the
`OpenAISettings` / `AnthropicAISettings` / `MistralAISettings` classes in
`model/model.py:141-157` are vestigial. Everything goes through the `openai` client
(`openai>=2.24` in `pyproject.toml`) using `AIProvider(api_url, api_key)` +
`AIModel` entries of `GlobalSettings` (`model/model.py:305-325`), resolved by
`global_settings.get_provider_settings()`, which expands `$VAR` in `api_url`/`api_key`
(`global_settings.py:195`) — so secrets can stay in the environment.
The defaults (`model/ai_model.py:135-153`) point at `llmfactory`/Ollama env vars, i.e.
a fresh install has no usable model unless localai/ollama is running.

Proposal — first-run "connect a provider", one key, zero files:

* Presets are plain OpenAI-compatible base URLs, no new code path:
  `openai` `https://api.openai.com/v1`, `deepseek` `https://api.deepseek.com/v1`,
  `gemini` `https://generativelanguage.googleapis.com/v1beta/openai`,
  plus openrouter/mistral/groq.
* `anthropic` has no OpenAI-compatible endpoint → only via the optional `litellm`
  proxy (`--profile anthropic`) or a dedicated adapter; keep it out of the trial default.
* On save: validate the key with `GET /models`, create the `AIProvider` + its `AIModel`s
  and fill the five global slots (`llm`, `rag`, `wiki`, `guardrail`, `embeddings`) plus
  `image_generation_model` / `image_vision_model` when the provider offers them.
* Embeddings must come from a provider when torch is absent (local MiniLM needs
  `sentence-transformers`, lazy-imported at `knowledge/embeddings.py:80-103`):
  `text-embedding-3-small` / `text-embedding-004`. Dim goes 384 → 1536 ⇒ reindex.
* `localai` remains as `--profile local-models`: the offline demo for people with no keys,
  just not started by default.

### 6.3 Keep a DEV deployment (all projects, watch, refresh)

Facts: DEBUG is not a profile today — the "production" compose already mounts the source
tree over the image (`- ../../.:/home/codx-junior/codx-junior` in the api *and* client
services, commented "Custom overrides (DEBUG)"), `run_api.sh` runs `uv sync` on every
start, and `run_client.sh` only runs `npm install && npm run dev` when `DEBUG` is set
(otherwise `npm run preview`). API reload is opt-in through `CODX_JUNIOR_API_RELOAD`
and intentionally not the default: every edit would restart uvicorn and interrupt the
in-flight agent turns.

Design — one compose base, additive overlays:

* **default (`prod`)**: pull-only image, source *not* mounted, no Node, no install on start.
* **`dev`** (`docker-compose.dev.yaml` — **not created yet: today the source mount and `DEBUG=1` are hardcoded in `docker-compose.yaml`**): source mounted, `DEBUG=1`, Vite dev server with
  HMR (`server.watch` already ignores `**/.codx/**`), `CODX_JUNIOR_API_RELOAD=1` per
  service choice, `node_modules`/venv in named volumes (or a `dev` build target) so
  restarts never reinstall, several projects mounted at once (multi-project watch),
  `docker.sock` for workspaces.
* **`proxy`**, **`local-models`**, **`anthropic`**: additive.

So the DEV stack keeps every project, watcher and hot reload, while the trial path
never pays for it.

---

## 7. What is implemented (demo tier)

Everything below is in the tree now; nothing here requires the user to build
anything.

### 7.1 New: `codx-junior-installer/demo/` — the trial deployment

| File | Purpose |
|---|---|
| `demo/up.sh` | one command: validates the settings, creates `.env`, pulls (or `--build`s) the image, starts the stack, waits for health, applies the AI provider |
| `demo/docker-compose.yaml` | **one** service on port 19981, and **no** Milvus: knowledge is the embedded LanceDB inside the API. No Traefik, no LocalAI, no client container, no init containers, no `docker.sock` by default |
| `demo/Dockerfile` | two stages: `node:22-slim` compiles the SPA, `python:3.11-slim` runs it. No torch/vLLM/Playwright/TeX/Tesseract/Firefox, no Node at runtime |
| `demo/nginx.conf` | serves the built SPA and proxies `/api` (+ socket.io websocket) to `127.0.0.1:19980` — this is what removes Traefik and the second web server |
| `demo/supervisord.conf` | `api` + `api-background` + nginx in a single container (the two API containers of the old compose) |
| `demo/entrypoint.sh` | creates the folders, warns about missing settings, execs supervisord. **Installs nothing** |
| `demo/.env.example` | the whole settings surface, grouped required/optional |
| `demo/README.md` | the documentation: what you must pass, provider presets, what is auto-configured, what is left out, operations, troubleshooting |

### 7.2 Zero-touch AI configuration

* `api/codx/junior/bootstrap.py` (new) — provider presets (openai, deepseek,
  gemini, openrouter, mistral, groq, ollama, custom), builds the `AIProvider` +
  `AIModel` entries and fills the five global model slots. Idempotent (skips an
  already configured installation), non-destructive, offline, never fatal.
* `api/codx/junior/api/bootstrap.py` (new router, auto-discovered) —
  `GET /api/bootstrap` (state, dry run) and `POST /api/bootstrap/apply`
  (loopback only unless `CODX_JUNIOR_BOOTSTRAP_ALLOW_REMOTE=1`).
* `api/codx/junior/app.py` — calls the bootstrap on startup inside a try/except,
  so a broken environment cannot prevent the API from booting.

### 7.3 Build cost

* `.dockerignore` at the **repository root** (the build context is the repo root):
  excludes `node_modules`, `dist`, `.codx`, `.git`, `api/wiki`, `api/docs`,
  `design`, `docs`, `wiki`, tests and the installer-only assets. Markdown is *not*
  blanket-excluded because the runtime loads Markdown prompts.
* `api/pyproject.toml` — `torch`, `sentence-transformers`, `playwright` and
  `vllm` moved from `dependencies` to the extras `local-models`, `browser`,
  `vllm` and `full`. Verified: `torch` has **no** import in the code, `playwright`
  is referenced only by a test, `vllm_cpu_ai.py` is deprecated and
  `sentence_transformers` is already imported lazily with a friendly message.
* `scripts/install_api.sh` — installs `.[local-models,browser]` by default, so the
  full deployment keeps today's behaviour; a slim image passes
  `CODX_JUNIOR_API_EXTRAS=""`.
* `scripts/run_api.sh` — the recursive `chown` and `uv sync` now only run when
  `CODX_JUNIOR_SKIP_INSTALL != 1`. This was the 30 s+ cost of every container start.

### 7.4 Documentation

* `demo/README.md` — the settings reference (required vs optional, presets table,
  what codx-junior configures itself, what is left out, dev deployment,
  troubleshooting).
* `docs/getting-started.md` — the demo is now the first path; the full stack is
  described as the self-hosted deployment.

### 7.5 Still open (in the order I would do it)

1. ~~**`knowledge_lancedb.py` adapter (§6.1)**~~ — **done**: the embedded backend,
   the `CODX_JUNIOR_KNOWLEDGE_BACKEND` selector (default `lancedb`, Milvus imported
   lazily so a LanceDB install never pulls `pymilvus`), the `NullKnowledgeDB`
   fallback and 15 tests in `api/tests/test_knowledge_lancedb.py`. The `--knowledge`
   flag is gone from `demo/up.sh`.
2. Publish `codxjunior/codx-junior:demo` from CI so `demo/up.sh` is pull-only for
   real (`--build` exists as a fallback until then).
3. ~~Embedding dimension stored in the index metadata~~ — **done**: the dimension
   and the embeddings model are written to `index_meta.json` on table creation, and
   a dimension change rebuilds the table (reindex) instead of corrupting it.
4. Image models (`image_generation_model` / `image_vision_model`) in the bootstrap
   presets, for the providers that support them.
5. Server tier: apply the same profile split (`proxy`, `local-models`, `knowledge`,
   `dev`) to `codx-junior/docker-compose.yaml`.

