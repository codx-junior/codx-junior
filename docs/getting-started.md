# Getting started

This page shows how to self-host codx-junior with Docker Compose. The engine is free and open source. If you would rather not run it yourself, see [Self-hosted or managed](/managed).

## One-line install (recommended)

On any Linux machine with internet access, one command installs codx-junior and
leaves it running with your provider already configured. It also **updates** an
existing installation and can install a branch:

```sh
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
# -> http://<server>:19981
```

Only the provider and its key are required. Useful extras:

```sh
  --branch feature-x     install or update to a branch or tag (default: main)
  --port 8080            publish on another port
  --projects /srv/repos  where your projects live
  --build                build from source instead of pulling the image
  --dry-run              show the command that would run, change nothing
```

The script installs Docker when it is missing, clones or updates the repository
on the requested branch, writes the settings and verifies the published port
from the host; exit code `0` means the health check passed. `install.sh --help`
lists everything. Running it again is the upgrade path: your projects and
settings are kept.

The manual paths below are for full control of the stack.

## Fastest path: try it first

If you only want to test codx-junior, do **not** clone or build anything. The
demo deployment pulls a single image, asks for one API key and is up in about
30 seconds:

```sh
cd codx-junior-installer/demo
./up.sh --provider deepseek --key sk-xxxxxxxx
# -> http://localhost:19981
```

It runs one container (API + background worker + web client), needs no domain,
no Traefik, no Milvus and no local model server, and codx-junior configures the
provider, the models and the model slots by itself. Every setting is documented
in [demo/README.md](https://github.com/gbrian/codx-junior/blob/main/codx-junior-installer/demo/README.md).
The rest of this page describes the full self-hosted stack.

## Requirements

* A Linux server or workstation with **Docker** and **Docker Compose**.
* Enough resources for the stack: Milvus, the API and, if you run models locally, LocalAI. 4 CPUs and 16 GB of RAM is a comfortable start; local models need more.
* An API key for an OpenAI-compatible provider, or a local model server (LocalAI, Ollama, vLLM).
* Optionally, a domain name pointing to the server if you want HTTPS.

## 1. Clone the repository

```sh
git clone https://github.com/gbrian/codx-junior.git
cd codx-junior/codx-junior-installer/codx-junior
```

## 2. Configure the environment

Copy the example file and edit it:

```sh
cp .env.example .env
```

```ini
USER_ID=911
GROUP_ID=911

# Used by the LiteLLM model manager, if you enable it
LITELLM_MASTER_KEY="sk-change-me"
LITELLM_SALT_KEY="sk-change-me"

# Domain Traefik answers on. Use localhost for a local install.
CODX_JUNIOR_DOMAIN="localhost"

# Email used for Let's Encrypt certificates
ACME_EMAIL="you@example.com"
```

See [Configuration](/configuration) for the full list of variables.

## 3. Build or pull the images

The stack uses the `codxjunior/codx-junior:api`, `codxjunior/codx-junior:client` and `codxjunior/codx-junior:debian` images. Build them from source with:

```sh
bash build.sh
```

## 4. Start codx-junior

```sh
docker compose up -d
```

This starts:

| Service | Purpose |
| --- | --- |
| `traefik-codx-junior` | Reverse proxy on ports 80 and 443 |
| `codx-junior-client` | Web application |
| `codx-junior-api` | API |
| `codx-junior-api-background` | Background jobs (indexing, watchers, mentions) |
| `milvus-codx-junior` | Vector store for knowledge |
| `localai-codx-junior` | Optional local models |

Projects are stored on the host under `/home/codx-junior-projects`, and global settings are kept in the `codx-junior` Docker volume.

## 5. Open the app

Go to `http://localhost` (or `https://<CODX_JUNIOR_DOMAIN>`). Continue with the [Initial setup](/initial-setup).

## Troubleshooting

* **The page loads but says "API disconnected!"**: check `docker logs codx-junior-api`.
* **Knowledge search returns nothing**: make sure `milvus-codx-junior` is healthy and that an embeddings model is configured.
* **Workspaces do not start**: the API needs access to `/var/run/docker.sock`, which the compose file mounts by default.
* **API reference**: the OpenAPI documentation is served at `/api/docs`.
