# Getting started

This page shows how to self-host codx-junior. The engine is free and open
source. If you would rather not run it yourself, see
[Self-hosted or managed](/managed).

The product runs as **one application container** (FastAPI + background worker +
nginx + the compiled SPA). Knowledge is embedded LanceDB, so there is no vector
database service. Optional model servers are external OpenAI-compatible
endpoints, not services the installer starts.

The release artifact is the Docker Hub image
[`codxjuniorai/codx-junior`](https://hub.docker.com/r/codxjuniorai/codx-junior).
Tags, architectures, provenance and the branch/tag flow that publishes it are
documented in [Docker Hub image](/docker-hub).

::: tip One installer, four modes
`local`, `server` (normally Traefik primary) and `dind`; add `--dev` for the development option (host: live checkout; DIND: DEBUG=1)
(one isolated nested instance per container). The full rationale, module audit
and known issues are in
[the installer refactor review](/installer-refactor).
:::

## Interactive install

Run the installer with no arguments on a terminal and it asks for the settings,
using the current values as defaults:

```bash
sudo ./install.sh
```

Press Enter to accept each default. The first question is the target folder
(default: the current directory); it covers the mode, domain, DIND proxy network/resolver, instance, paths,
provider and key, coworking and Sysbox. `--interactive` forces it; `--non-interactive` disables it.

## One-line install (recommended)

On any Linux machine with internet access, one command installs codx-junior and
leaves it running with your provider already configured. It also **updates** an
existing installation and can install a branch:

```sh
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
# -> http://<server>:19981
```

Without `--domain` this is plain HTTP on the published port (the historical
default). Add a domain and an e-mail to get HTTPS through Traefik:

```sh
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --mode server --domain codx.example.com \
      --acme-email ops@example.com --provider deepseek --key sk-xxxxxxxx
# -> https://codx.example.com
```

Only the provider and its key are required. Useful extras:

```sh
  --mode local           laptop/CI: 127.0.0.1 only, no TLS, no proxy
  --branch feature-x     install or update to a branch or tag (default: main)
  --port 8080            publish on another port (local / plain HTTP)
  --projects /srv/repos  where your projects live
  --coworking            mount the Docker socket (browser/desktops)
  --build                build the image from source instead of pulling it
  --dry-run              show what would run, change nothing
```

The installer installs Docker when it is missing, clones or updates the runtime
assets, writes the settings and verifies the health endpoint. Exit code `0`
means the check passed. `install.sh --help` lists everything. Running it again is
the update path: your projects and settings are kept. From the web UI,
**Settings → Updates** (admin) runs the same update from a helper container, so
nobody needs SSH access; see `api/docs/system-update.md`.

## Deployment modes

| Mode | What it is | Publishes | TLS |
|---|---|---|---|
| `local` | laptop, CI, single-user test | `127.0.0.1:19981` | no |
| `server` | standalone server with Traefik primary | `:80`, `:443` | ACME (HTTP-01) |
| `server --no-tls` | standalone server without a domain | `:19981` | no |
| `server --traefik slave` | instance behind an existing primary proxy | nothing (primary network) | terminated by the primary |
| `dind` | one isolated nested stack per container; inner install is always `server` | per `--traefik` | per `--traefik` |

### Localhost / CI

```sh
./install.sh --mode local --provider deepseek --key sk-xxxxxxxx
# -> http://localhost:19981
```

The port is bound to `127.0.0.1` only. Use `--coworking` if you need browser or
desktop sessions (the API needs the Docker socket for that).

### Server with Traefik

```sh
sudo ./install.sh --mode server --domain codx.example.com \
  --acme-email ops@example.com --provider deepseek --key sk-xxxxxxxx
```

Create the DNS A record first. Traefik obtains the certificate with HTTP-01 and
the installer waits for `https://<domain>/api/health`. Ports 80/443 are owned by
this instance: a second instance on the same host must use `--traefik slave`.

For a server without a domain (plain HTTP on `:19981`):

```sh
sudo ./install.sh --mode server --no-tls --provider deepseek --key sk-xxxxxxxx
```

### Slave behind an existing primary

Use this when a central Traefik (or another edge) already owns DNS, ports 80/443
and certificates, and forwards traffic to this container:

```sh
sudo ./install.sh --mode server --traefik slave --domain team.example.com \
  --proxy-network traefik --trusted-proxy 172.16.0.0/12 \
  --provider openai --key sk-xxxxxxxx
```

The slave publishes no host port. It runs Traefik on `:80` and trusts
`X-Forwarded-*` only from the given primary CIDRs. The primary needs a route
`Host(team.example.com) -> <container>:80`; if it watches Docker labels, the
installer's `<instance>-edge` labels provide it.

### Docker-in-Docker (multiple instances per host)

```sh
sudo ./install.sh --mode dind --traefik primary --domain acme.example.com \
  --acme-email ops@example.com --provider deepseek --key sk-xxxxxxxx
```

Each install/update pulls the latest `codxjuniorai/codx-junior:dind` image and
fails if the pull fails. Use `--no-pull` to use a local image intentionally.

Each instance is one wrapper container with its own nested Docker daemon,
containers, networks and volumes. The inner installation is always `server`.
Only one instance per host can use `--traefik primary` (ports 80/443); the
others use `--traefik slave`, which joins the existing Traefik network and
publishes no host port. Persisted host data lives under `<home>/data` plus the
instance's `dind-storage` volume.

`--sysbox` uses `runtime: sysbox-runc` instead of `privileged: true` on hosts
that provide Sysbox.

Before the final confirmation the wizard creates in the target folder:

```text
<target>/docker-compose.yaml
<target>/.env
```

Edit `docker-compose.yaml` to add folder mappings under
`services.dind.volumes`. The installer uses the file as-is. If either file
already exists, the wizard asks whether to overwrite it; answering no preserves
your edits. Map project folders under `/data/projects/<name>` so the inner app
sees them as projects.

For an existing primary, the DIND container joins its network:

```bash
sudo ./install.sh --mode dind --traefik slave \
  --domain demo.example.com --proxy-network traefik \
  --provider deepseek --key sk-...
# https://demo.example.com through the existing primary
```

`--proxy-network` is consumed only by the outer DIND container in this slave
case. The inner stack creates its own nested `codx-proxy` network.

### Development option

Use `--dev` to enable development mode. On a host install it runs the checkout you are editing; on DIND it sets `DEBUG=1` inside the packaged image:

```bash
cd ~/codx-junior
./install.sh --mode local --dev --provider deepseek --key-file ~/.config/codx-junior/deepseek.key
# http://localhost:19981
```

The checkout is mounted live. The API uses `uvicorn --reload` and the SPA is
served by Vite with HMR (nginx is not used). Edit `api/` or `client/src/` and
refresh/hot-reload; no image rebuild. Rebuild with `--build` after changing
`pyproject.toml` or `package.json`.

On a host, the development option can still be combined with `--traefik slave` for
local access plus a public URL through an existing primary.

### Reusing the last selections

The installer writes the resolved selections to
`${XDG_CONFIG_HOME:-$HOME/.config}/codx-junior/installer.env` (mode `600`) before building or pulling the image. If an install is interrupted, the next run uses those values as
the wizard defaults. The instance settings file and explicit CLI flags still
take precedence. Override the path with `CODX_JUNIOR_INSTALLER_STATE` or delete
the file to reset the defaults.

## Requirements

* A Linux server or workstation with **Docker** and the **compose v2 plugin**.
* 2 vCPU / 4 GB RAM / 30 GB disk for the single-container tier; more for heavy
  coworking desktops or local models.
* An API key for an OpenAI-compatible provider (`openai`, `deepseek`, `gemini`,
  `openrouter`, `mistral`, `groq` or `custom`).
* Optionally, a domain name pointing to the server if you want HTTPS.

## Operations

```sh
# from the installation root (default /opt/codx-junior)
/opt/codx-junior/bin/codx-junior-compose ps
/opt/codx-junior/bin/codx-junior-compose logs -f
/opt/codx-junior/bin/codx-junior-compose down     # projects/settings kept

sudo /opt/codx-junior/bin/codx-junior update        # source + image + recreate
sudo ./install.sh --update                         # same, from the checkout
sudo ./install.sh status|logs|down|doctor|config

sudo ./install.sh remove                           # summary + confirmation
sudo ./install.sh remove --delete-files            # also generated env/compose files
sudo ./install.sh remove --delete-files --confirm-delete  # automation
```

On systemd hosts the installer registers `codx-junior.service` (or
`codx-junior-<instance>.service`). The generated wrapper is the same compose
invocation, so the unit can be inspected and run by hand.

## Troubleshooting

* **The UI opens but the API is disconnected**: `bin/codx-junior-compose logs`
  and check the `api`/`api-background` programs inside the container.
* **HTTPS never becomes ready**: the domain's A record must point at the machine
  and port 80 must be reachable for ACME. `docker logs -f <instance>-traefik`
  shows the order. Use `--acme-ca staging` while rehearsing.
* **A port is already in use**: another proxy (Caddy, nginx, Traefik) owns 80/443.
  Stop it, or install this instance with `--traefik slave` behind the existing one.
* **Knowledge search returns nothing**: configure an embeddings model. Providers
  without an embeddings endpoint (`deepseek`, `openrouter`, `groq`) fall back to
  lexical search.
* **Coworking sessions fail to start**: the API needs
  `/var/run/docker.sock`; reinstall with `--coworking --allow-privileged`.
  Windows/macOS VMs additionally need `/dev/kvm` on the host.
* **API reference**: the OpenAPI documentation is served at `/api/docs`.

## Legacy stack

The old multi-container server stack (`codx-junior-installer/codx-junior/`) and
the old DIND wrapper (`codx-junior-dind/`) were removed with Milvus, LocalAI,
LiteLLM and Coolify. There is nothing to migrate to: the unified installer is
the only supported path. See
[the installer refactor review](/installer-refactor) for the history.
