# codx-junior installer

One installer, one application image, four deployment modes.

| You want to… | Mode | Command |
|---|---|---|
| try it on a laptop / CI | `local` | `./install.sh --mode local --provider deepseek --key sk-...` |
| a public server with Traefik + ACME | `server` | `sudo ./install.sh --mode server --domain x.example.com --acme-email ops@example.com --provider deepseek --key sk-...` |
| sit behind an existing primary proxy | `server` + `--traefik slave` | `sudo ./install.sh --mode server --traefik slave --domain x.example.com --proxy-network traefik --provider deepseek --key sk-...` |
| one isolated nested instance per container | `dind` | `sudo ./install.sh --mode dind --traefik primary --domain x.example.com --acme-email ops@example.com --provider deepseek --key sk-...` |
| DIND behind an existing primary | `dind` + `--traefik slave` | `sudo ./install.sh --mode dind --traefik slave --domain x.example.com --proxy-network traefik --provider deepseek --key sk-...` |
| hack on codx-junior itself, live | host mode + `--dev` | `./install.sh --mode local --dev --provider deepseek --key sk-...` |

Design, module audit and known issues:
[`docs/installer-refactor.md`](../docs/installer-refactor.md).

## What runs

One container:

* FastAPI API + background worker (supervisord);
* nginx serving the compiled SPA and proxying `/api` to the API process
  (`--dev` replaces nginx with the Vite dev server);
* embedded LanceDB for knowledge/RAG.

There is **no Milvus, LocalAI, LiteLLM, vLLM, Coolify, Prometheus, Caddy or
default workspace**. Coworking/browser/desktop sessions are optional and use the
Docker socket; the installer enables them with `--coworking`.

Every released image is **complete**: it includes the document/OCR tools
(pandoc, TeX Live, tesseract), ffmpeg, Playwright/Chromium, the local
embeddings stack, and the Docker CLI with the compose and buildx plugins
(stack actions, image builds and the admin container web shell all shell out to
`docker`). There is no slim variant.

## Interactive setup

Run the installer with no arguments on a terminal and it asks for every
deployment setting, showing the current value as the default:

```bash
sudo ./install.sh
```

It first asks for the **target folder** (default: the current directory), then
the **deployment mode** (`local` / `server` / `dind`), then whether
to enable the **development option** (`--dev`: `DEBUG=1`, live checkout, Vite
HMR), and then for an optional **domain**. A blank domain means localhost/plain
HTTP and no Traefik; a domain asks for the **Traefik mode** (`primary` / `slave`)
and the matching proxy/ACME settings. It also asks for the instance,
installation root, projects folder, port, AI provider/key, coworking and Sysbox. Press Enter to accept a default. This is what allows combinations
such as **dind + local + slave**: local access on `127.0.0.1:19981` plus a slave
Traefik behind an existing primary.
`--interactive` forces the wizard even when other flags are present;
`--non-interactive` (or `--yes`) always disables it.

## 0.0.1 feature profile (workspace mode by default)

The image is complete; the installer chooses which surfaces it exposes through
`CODX_JUNIOR_ACTIVE_FEATURES`.

| Flag | Profile | What the user gets |
|---|---|---|
| *(default)* | `core` | Project workspace: projects, chats, tasks, files, Git, knowledge, settings. The company OS and its first-run wizard are hidden. |
| `--full-featured` | `all` | The full product, including the company-first shell and the company wizard. |
| `--features <id[,id]>` | custom | An explicit subset; `core` and `all` may be combined with ids (`--features core,mcp`). |

The installer validates the ids against the 0.0.1 manifest: an unknown id fails
the install instead of silently disappearing. `codx-junior config` prints both
the requested profile and the resolved ids. The choice is written to the
instance settings file and preserved on upgrade; re-run with
`--full-featured` to turn the company OS back on.

`CODX_JUNIOR_ACTIVE_FEATURES` may also be set in the environment. An
**unset** value means `all` (the historical developer behaviour); the installer
always writes an explicit `core` for a new 0.0.1 installation.

## Development option

`--dev` is an option, not a deployment mode.

On a host install it runs the checkout you are editing:

```bash
cd ~/codx-junior
./install.sh --mode local --dev --provider deepseek --key-file ~/.config/codx-junior/deepseek.key
```

* the whole checkout is bind-mounted at `/home/codx-junior/codx-junior`;
* Python changes reload the API (`uvicorn --reload`);
* the SPA is served by the Vite dev server with HMR, not nginx;
* `/api` and Socket.IO are proxied to the API;
* `http://localhost:19981` is ready without rebuilding the image.

The first run builds `codxjuniorai/codx-junior:dev` from the published complete
image plus the client dependencies. Source edits need no rebuild. After changing
`api/pyproject.toml` or `client/package.json`, rerun with `--build`.

On DIND the source is baked into the image, so `--dev` only sets `DEBUG=1` for
the inner scripts/app; there is no live checkout or Vite HMR inside DIND.

API reload on a host restarts `uvicorn` on edits, which can interrupt an
in-flight agent turn; that is expected in development.

## Reusing the last selections

The installer saves the resolved selections before it builds or pulls the image:

```
<target>/installation.env
```

The target defaults to `$PWD`. The file is mode `600` because it may contain the
provider key. On the next run from the same target its values are used as the
wizard defaults, even if the previous install was interrupted before the
instance settings file was written. The instance settings file still wins over
this state file, and CLI flags win over both.

Override the location with `CODX_JUNIOR_INSTALLER_STATE=/path/to/file`; delete
the file to start from clean defaults. The old global
`~/.config/codx-junior/installer.env` is no longer read.

## One line: install or update

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
```

Without `--domain` this serves plain HTTP on the published port. Add
`--domain x.example.com --acme-email ops@example.com` for Traefik + ACME.
Re-running is the update path; projects and settings are kept.

The web UI has the same operation: **Settings → Updates** (admin) pulls the
image and runs `codx-junior update` from a helper container, so nobody has to
copy the installer to the host. The installer publishes
`<home>/data/settings/installer.json` for that; instances created before it are
discovered from their compose labels. See `api/docs/system-update.md`.

| Situation | Command |
|---|---|
| server with TLS | add `--domain x.example.com --acme-email ops@example.com` |
| localhost / CI | add `--mode local` |
| live codx-junior development | add `--dev` (host); DIND gets `DEBUG=1` only |
| company OS and company wizard | add `--full-featured` (default is workspace mode) |
| a specific feature subset | `--features core,mcp` (ids in `docs/product/features-0.0.1.json`) |
| hide codx-junior and its child projects | add `--hidden` (`--no-hidden` shows them) |
| behind an existing primary proxy | `--mode server --traefik slave --domain x.example.com --proxy-network traefik` |
| several instances on one host | `--mode dind --traefik primary\|slave --instance <name>` |
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

Tags, architectures, provenance and the git flow that publishes each tag are
documented in [docs/docker-hub.md](../docs/docker-hub.md). CI publishes `latest`
on every push to `main` and `vX.Y.Z` on every release tag; do not push those
image tags by hand. The Docker Hub listing (short description and README) is
published from [`../DOCKERHUB.md`](../DOCKERHUB.md) by the `dockerhub-page`
workflow.

Publish with the repository script (the break-glass path, or for the `dind`
wrapper):

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

Every DIND install/update pulls the latest
`codxjuniorai/codx-junior:dind` tag and fails instead of silently using a stale
local image. Use `--no-pull` only when you intentionally run a local image, or
`--build-dind` to build it. If the pulled digest changes, the wrapper is
force-recreated.

`--update` (or the `update` command) **always** recreates the DIND wrapper,
even when the DIND digest did not change: the wrapper entrypoint is what pulls
the inner app image and recreates the nested stack, and the app image is
published independently of the DIND image. `--update --image TAG` also rewrites
`CODX_JUNIOR_IMAGE` in the generated wrapper compose so the new tag reaches the
inner installer. The nested install log is in `docker logs <instance>` (and
`/var/log/inner-install.log` inside the wrapper).

The wrapper runs its own dockerd and the same runtime compose. Its inner
installation is always `--mode server`. Data on the host lives under
`<home>/data` plus `<instance>-dind` (nested image layers).

* `--traefik primary`: the nested Traefik terminates TLS and publishes `:80`/`:443`.
* `--traefik slave`: the outer DIND container joins the existing Traefik network
  and carries the edge labels; no host port is published. The nested Traefik
  runs normally as a slave and forwards to the inner server.

Before the wizard asks for final confirmation it creates editable files in the
target folder:

```text
<home>/docker-compose.yaml   # add services.dind.volumes mappings here
<home>/.env                  # instance settings; extra keys are preserved
```

Add host folders under `/data/projects/<name>` so the inner app sees them as
projects. The installer uses these files as-is, so edits made at the
confirmation prompt are applied. If either file already exists, the wizard asks
whether to overwrite it; answering no keeps the existing files and your edits.

`--proxy-network` is only used by the **outer** container in the slave case.
Inside DIND the stack uses its own Compose-managed `codx-proxy` network; the
inner installer never requires the host network.

## Operations

```bash
/opt/codx-junior/bin/codx-junior-compose ps
/opt/codx-junior/bin/codx-junior-compose logs -f
/opt/codx-junior/bin/codx-junior-compose down

sudo /opt/codx-junior/bin/codx-junior update         # latest image, recreate
sudo ./install.sh --update
sudo ./install.sh --update --image codxjuniorai/codx-junior:v1.2.3
sudo ./install.sh status|logs|down|restart|doctor|config|url|env|version
```

Remove an installation (the default keeps projects, data and images):

```bash
sudo ./install.sh remove                         # assistant + summary + confirmation
sudo ./install.sh remove --delete-files          # also .env, compose, installation.env, lib/, bin/
sudo ./install.sh remove --delete-files --confirm-delete   # automation
sudo ./install.sh remove --delete-files --dry-run          # preview only
```

If settings are missing, the remove assistant asks for the target, mode and
instance, using the current values as defaults. It then asks whether to keep the
installation files; the default is **keep** (`y`). The summary lists every file
and folder that will be deleted, or prints exactly:

```text
No files will be removed
```

For DIND, removal deletes the wrapper container and its `dind-storage` volume
(nested containers, images and layers). Host projects/data are kept unless you
delete them yourself. External Traefik networks are never removed. Removal is
irreversible and always asks for confirmation unless `--confirm-delete` is set.

`--update` is the only refresh command; `--upgrade` is not accepted.

On systemd hosts the installer registers `codx-junior.service` (or
`codx-junior-<instance>.service`), and installs a `codx-junior` CLI.

### Whole-repo security scans

Whole-repo scans: run them with `docker exec` into `codx-junior-api`; the repo
root is mounted at `/home/codx-junior-projects/codx-junior/codx-junior-platform`.
Fallback container: none verified — the api container is the only place that
root is mounted.

```bash
docker exec codx-junior-api bash -lc 'cd /home/codx-junior-projects/codx-junior/codx-junior-platform && git ls-files | grep -Ei "(^|/)\.env|\.pem$|\.key$|acme|secret"'
```

`git` walks up to the repository root, so the same command also works from
the `codx-junior-installer/` checkout. If `codx-junior-api` is down, start it
with `/opt/codx-junior/bin/codx-junior-compose up -d api` and repeat the scan;
there is no second container with the root mounted. The `project_*` file tools
are rooted at `codx-junior-installer/` and cannot read the parent tree — use the
command above instead of widening the index.

## Tests

One gate, one source of truth: [`.codx/qa_policy.json`](.codx/qa_policy.json)
declares every suite as an argv array, and `tests/qa_suite.sh` executes it.

```bash
npm test                                 # the "shell" suite (bash -n + shellcheck)
npm run test:check                       # parse the policy, verify every runner
npm run test:all                         # every declared suite, stop at first failure
bash tests/qa_suite.sh --list            # suites declared in the policy
bash tests/qa_suite.sh shell --json      # one suite, machine-readable verdict
```

Declared suites: `shell` (`tests/test_shell.sh` — `bash -n`, `shellcheck` and
`docker compose config` over the tracked shell files), `unit`
(`tests/test_install.sh`), `demo` (`demo/tests/test_up.sh`). The dispatcher runs
from the repository root, so it behaves the same from any directory. Exit codes:
`0` pass, `1` suite failed, `2` usage or policy error, `3` runner missing.
It needs `python3` to read the policy and `shellcheck` to run the `shell` suite;
`npm run test:check` reports a missing one as `runner-missing` or a policy error
instead of passing silently.

It exists because the platform QA engine does not read `qa_policy.suites`:
`qa_run_tests` answers a declared suite with `no-suite` and reports
`changed_files: 0` on a real diff. That defect is tracked on card `de08e8d9` and
lives outside this repository, so the gate here is invoked directly.

## Troubleshooting

| Symptom | Fix |
|---|---|
| HTTPS never becomes ready | DNS A record must point at the host; port 80 must be reachable. Check `docker logs -f <instance>-traefik`. |
| port 80/443 already in use | stop the other proxy, or use `--traefik slave` |
| UI opens, API disconnected | `bin/codx-junior-compose logs`; check the `api`/`api-background` programs |
| knowledge search is lexical only | configure an embeddings model |
| coworking sessions fail | reinstall with `--coworking --allow-privileged`; VMs also need `/dev/kvm` |
| pull access denied | publish the image to `codxjuniorai/codx-junior`, or use `--build` |
