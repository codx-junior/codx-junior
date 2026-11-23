# Getting started

This page shows how to self-host codx-junior. The engine is free and open
source. If you would rather not run it yourself, see
[Self-hosted or managed](/managed).

The product runs as **one application container** (FastAPI + background worker +
nginx + the compiled SPA). Knowledge is embedded LanceDB, so there is no vector
database service. Optional model servers are external OpenAI-compatible
endpoints, not services the installer starts.

::: tip One installer, four modes
`local`, `server` (Traefik master), `slave` (behind a master proxy) and `dind`
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

Press Enter to accept each default. It covers the mode, domain, DIND inner mode,
master proxy network/resolver, instance, paths, provider and key, coworking and
Sysbox. `--interactive` forces it; `--non-interactive` disables it.

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
the upgrade path: your projects and settings are kept.

## Deployment modes

| Mode | What it is | Publishes | TLS |
|---|---|---|---|
| `local` | laptop, CI, single-user test | `127.0.0.1:19981` | no |
| `server` | standalone server with Traefik master | `:80`, `:443` | ACME (HTTP-01) |
| `server --no-tls` | standalone server without a domain | `:19981` | no |
| `slave` | instance behind an existing master proxy | nothing (master network) | terminated by the master |
| `dind` | one isolated nested stack per container | per `--dind-mode` | per `--dind-mode` |

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
this instance: a second instance on the same host must use `slave`.

For a server without a domain (plain HTTP on `:19981`):

```sh
sudo ./install.sh --mode server --no-tls --provider deepseek --key sk-xxxxxxxx
```

### Slave behind a master proxy

Use this when a central Traefik (or another edge) already owns DNS, ports 80/443
and certificates, and forwards traffic to this container:

```sh
sudo ./install.sh --mode slave --domain team.example.com \
  --proxy-network traefik --trusted-proxy 172.16.0.0/12 \
  --provider openai --key sk-xxxxxxxx
```

The slave publishes no host port. It runs Traefik on `:80` and trusts
`X-Forwarded-*` only from the given master CIDRs. The master needs a route
`Host(team.example.com) -> <container>:80`; if it watches Docker labels, the
installer's `<instance>-edge` labels provide it.

### Docker-in-Docker (multiple instances per host)

```sh
sudo ./install.sh --mode dind --dind-mode server --domain acme.example.com \
  --acme-email ops@example.com --provider deepseek --key sk-xxxxxxxx
```

Each instance is one wrapper container with its own nested Docker daemon,
containers, networks and volumes. Only one instance per host can be
`--dind-mode server` (ports 80/443); the others use `--dind-mode slave` and join
the master network. Persisted host data lives under `<home>/data` plus the
instance's `dind-storage` volume.

`--sysbox` uses `runtime: sysbox-runc` instead of `privileged: true` on hosts
that provide Sysbox.

To keep local access **and** publish through a master proxy, combine the inner
app mode with the Traefik mode:

```bash
sudo ./install.sh --mode dind --dind-mode local --traefik slave \
  --domain demo.example.com --proxy-network traefik \
  --provider deepseek --key sk-...
# 127.0.0.1:19981 locally + https://demo.example.com through the master
```

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

sudo ./install.sh --upgrade                        # source + image + recreate
sudo ./install.sh status|logs|down|doctor|config
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
  Stop it, or install this instance with `--mode slave` behind the existing one.
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
