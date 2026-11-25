---
title: Install codx-junior
description: One container, one provider, one project — the 0.0.1 quick start.
---

# Install codx-junior

codx-junior 0.0.1 runs as **one application container**: FastAPI, the background
worker and nginx (the compiled SPA). Knowledge is embedded LanceDB. There is no
vector database or model server to run.

## Requirements

| | |
| --- | --- |
| Host | Linux with Docker 20.10+ and the Compose v2 plugin |
| Minimum | 2 vCPU / 4 GB RAM / 40 GB disk |
| Recommended | 4 vCPU / 8 GB RAM |
| Model | one API key for an OpenAI-compatible provider |

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
# -> http://<server>:19981
```

The installer writes the settings, starts the container and waits for the health
check. Re-running the same command is the update path; your projects and
settings are kept.

## Local-only

```bash
./install.sh --mode local --provider deepseek --key sk-xxxxxxxx
# -> http://localhost:19981
```

The UI is bound to `127.0.0.1` only. This is the recommended first run: no DNS,
no TLS, no public exposure.

## Public server with HTTPS

```bash
sudo ./install.sh --mode server --domain codx.example.com \
  --acme-email ops@example.com --provider deepseek --key sk-xxxxxxxx
# -> https://codx.example.com
```

Create the DNS `A` record first. The installer starts the bundled Traefik and
waits for the certificate.

## First boot

1. Open the URL.
2. Sign in as `admin` and **set a strong password immediately**.
3. Finish the setup: workspace, project and model.
4. Add a repository: a Git URL or a folder on the server.
5. Open **AI Models**, add the provider and pick the default model.

::: warning Exposing the instance
Complete the admin password and setup **before** making the port reachable from
the internet. On a shared or public host, a one-time setup token or a
pre-provisioned admin is the safe pattern.
:::

## Useful options

```bash
--mode local            laptop / CI: 127.0.0.1 only, no TLS
--mode server           public host: Traefik + ACME
--port 8080             publish on another port
--projects /srv/repos   where your projects live
--coworking             mount the Docker socket (roadmap features)
--build                 build the image from source
--dry-run               show what would run, change nothing
```

Full reference: the
[installer README](https://github.com/codx-junior/codx-junior/blob/main/codx-junior-installer/README.md).

## Update

Re-run the installer. It refreshes the image and recreates the container while
keeping the projects and settings volumes.

## Where things live

| | |
| --- | --- |
| Projects | the host folder you chose (`--projects`) |
| Settings | a Docker volume mounted at `/home/codx-junior/.codx-junior` |
| Analytics / logs | a Docker volume mounted at `/home/codx-junior/analytics` |
| Knowledge | embedded LanceDB inside the settings volume |

## Troubleshooting

```bash
# health
curl -fsS http://localhost:19981/api/health

# logs
docker logs -f <instance>

# stop / start
docker compose -p <instance> down
docker compose -p <instance> up -d
```

More: [Setup and operations](/setup-and-installation) and
[Getting started](https://github.com/codx-junior/codx-junior/blob/main/docs/getting-started.md).

---

**Next:** [What ships in 0.0.1](/0.0.1) · [Feature reference](/codx-junior.features) · [Home](/home)
