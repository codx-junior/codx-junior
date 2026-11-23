# codx-junior installer

One installer, one application image, four deployment modes.

| You want to… | Mode | Command |
|---|---|---|
| try it on a laptop / CI | `local` | `./install.sh --mode local --provider deepseek --key sk-...` |
| a public server with Traefik + ACME | `server` | `sudo ./install.sh --mode server --domain x.example.com --acme-email ops@example.com --provider deepseek --key sk-...` |
| sit behind an existing master proxy | `slave` | `sudo ./install.sh --mode slave --domain x.example.com --proxy-network traefik --provider deepseek --key sk-...` |
| one isolated nested instance per container | `dind` | `sudo ./install.sh --mode dind --dind-mode server --domain x.example.com --acme-email ops@example.com --provider deepseek --key sk-...` |
| DIND with local access **and** a slave proxy | `dind` + `local` + `slave` | `sudo ./install.sh --mode dind --dind-mode local --traefik slave --domain x.example.com --proxy-network traefik --provider deepseek --key sk-...` |

Design, module audit and known issues:
[`docs/installer-refactor.md`](../docs/installer-refactor.md).

## What runs

One container:

* FastAPI API + background worker (supervisord);
* nginx serving the compiled SPA and proxying `/api` to the API process;
* embedded LanceDB for knowledge/RAG.

There is **no Milvus, LocalAI, LiteLLM, vLLM, Coolify, Prometheus, Caddy or
default workspace**. Coworking/browser/desktop sessions are optional and use the
Docker socket; the installer enables them with `--coworking`.

Every released image is **complete**: it includes the document/OCR tools
(pandoc, TeX Live, tesseract), ffmpeg, Playwright/Chromium and the local
embeddings stack. There is no slim variant.

## Interactive setup

Run the installer with no arguments on a terminal and it asks for every
deployment setting, showing the current value as the default:

```bash
sudo ./install.sh
```

It always asks for the **Traefik setup** (`none` / `master` / `slave`) in
addition to the mode (`local` / `server` / `slave` / `dind`), domain, DIND inner
mode, master proxy network/resolver/entrypoint, instance, installation root,
projects folder, port, AI provider and key, coworking and Sysbox. Press Enter to
accept a default. This is what allows combinations such as **dind + local +
slave**: local access on `127.0.0.1:19981` plus a slave Traefik for a master.
`--interactive` forces the wizard even when other flags are present;
`--non-interactive` (or `--yes`) always disables it.

## One line: install or update

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
```

Without `--domain` this serves plain HTTP on the published port. Add
`--domain x.example.com --acme-email ops@example.com` for Traefik + ACME.
Re-running is the upgrade path; projects and settings are kept.

| Situation | Command |
|---|---|
| server with TLS | add `--domain x.example.com --acme-email ops@example.com` |
| localhost / CI | add `--mode local` |
| behind a master proxy | `--mode slave --domain x.example.com --proxy-network traefik` |
| several instances on one host | `--mode dind --dind-mode server\|slave --instance <name>` |
| branch or tag | add `--branch my-feature` |
| see before acting | add `--dry-run` |
| no internet | `git clone`, then `./install.sh --provider … --key …` |

Only `--provider` and `--key` are required. Providers: `openai`, `deepseek`,
`gemini`, `openrouter`, `mistral`, `groq`, `custom`. Local model servers are not
installed by codx-junior: run Ollama or any other OpenAI-compatible server in a
coworking desktop, then configure it as a `custom` provider.

## Requirements

| | |
|---|---|
| Docker | 20.10+ with the `compose` v2 plugin |
| RAM | 4 GB free; more for coworking desktops or local models |
| Disk | ~8–12 GB for the complete image |
| AI provider | one API key for an OpenAI-compatible provider |

## Image

Images are published under **`codxjuniorai/codx-junior`**:

| Tag | Content |
|---|---|
| `latest` | complete application image (main) |
| `vX.Y.Z` | pinned release |
| `dind` | Docker-in-Docker wrapper (contains the installer and runtime files) |

Publish with the repository script:

```bash
DOCKERHUB_TOKEN=... IMAGE_TAGS="latest" \
  DOCKERFILE=codx-junior-installer/demo/Dockerfile CONTEXT=. \
  ./scripts/dockerhub_publish.sh
```

Or build locally with `./install.sh --build`.

## Knowledge / RAG

LanceDB is embedded in the API process. There is no external service:

| `CODX_JUNIOR_KNOWLEDGE_BACKEND` | What runs |
|---|---|
| `lancedb` (default) | embedded, no service |
| `none` | knowledge disabled |

Dense search needs an embeddings model. `deepseek`, `openrouter` and `groq` do
not provide one, so search runs lexical only on those providers. A missing or
broken embeddings stack degrades to lexical search; it never prevents boot.

## Development

```bash
./dev-install.sh --provider deepseek --key sk-xxxxxxxx
# -> http://localhost:19981
```

Builds the current checkout, mounts it into the same single-container stack and
supports `--reload`, `--build`, `--logs`, `--down` and `--purge`.

## DIND

The wrapper runs its own dockerd and the same runtime compose. Data on the host
lives under `<home>/data` plus `<instance>-dind` (nested image layers). Only one
instance per host can use `--traefik master`; the rest use `--traefik slave`.

`--dind-mode local --traefik slave` is the hybrid: the app is published on
`127.0.0.1:<port>` for local access and the same stack joins the master network
through a slave Traefik.

## Operations

```bash
/opt/codx-junior/bin/codx-junior-compose ps
/opt/codx-junior/bin/codx-junior-compose logs -f
/opt/codx-junior/bin/codx-junior-compose down

sudo ./install.sh --upgrade
sudo ./install.sh status|logs|down|restart|doctor|config|url|env|version
```

On systemd hosts the installer registers `codx-junior.service` (or
`codx-junior-<instance>.service`), and installs a `codx-junior` CLI.

## Troubleshooting

| Symptom | Fix |
|---|---|
| HTTPS never becomes ready | DNS A record must point at the host; port 80 must be reachable. Check `docker logs -f <instance>-traefik`. |
| port 80/443 already in use | stop the other proxy, or use `--mode slave` |
| UI opens, API disconnected | `bin/codx-junior-compose logs`; check the `api`/`api-background` programs |
| knowledge search is lexical only | configure an embeddings model |
| coworking sessions fail | reinstall with `--coworking --allow-privileged`; VMs also need `/dev/kvm` |
| pull access denied | publish the image to `codxjuniorai/codx-junior`, or use `--build` |
