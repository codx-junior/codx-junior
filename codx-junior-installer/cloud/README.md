# codx-junior on a cloud VM — unattended install

One script, no questions, no interactive prompt: a fresh VM becomes a running
codx-junior. This is the automated path on top of the
[demo installer](../demo/README.md) — it installs Docker, downloads the
repository, writes the settings and then runs the same `demo/up.sh` a human
would run, so there is only one place that knows how the stack starts.

```bash
sudo ./bootstrap.sh --provider deepseek --key sk-xxxxxxxx
```

```
==> codx-junior unattended install
==>   provider : deepseek
==>   port     : 19981
==>   projects : /opt/codx-junior/projects
==> Downloading the repository (shallow clone of main)
==> Installing codx-junior (this pulls or builds the image: 1-2 min pulled, 5-10 min built)
...
CODX_JUNIOR_READY http://203.0.113.10:19981
```

Exit code `0` means the health check passed **from the host**, on the published
port. Open the URL: the provider and its models are already configured, so the
chat works immediately.

| File | What it is |
|---|---|
| `bootstrap.sh` | the unattended installer (this is the entry point) |
| `codx-junior` | operations CLI, installed at `/usr/local/bin/codx-junior` |
| `cloud-init.yaml` | user-data for any cloud-init provider (Hetzner, DigitalOcean, OpenStack, Scaleway, …) |
| `aws-user-data.sh` | EC2 user-data |
| `gcp-startup-script.sh` | GCE `startup-script` metadata |

## 1. What you need

| | |
|---|---|
| VM | 2 vCPU, **4 GB RAM**, 20 GB disk, x86_64 (Ubuntu 22.04+/Debian 12+, or Amazon Linux 2023) |
| AI key | one key from OpenAI, DeepSeek, Gemini, OpenRouter, Mistral or Groq (or an Ollama endpoint you own) |
| Network | inbound TCP `19981` — from your IP, or 80/443 if you put a TLS proxy in front (see §6) |
| Root | the script needs `sudo` (it installs Docker) |

Nothing else has to be pre-installed: Docker, git and the compose plugin are
handled.

> **ARM (Hetzner CAX, Graviton, Ampere):** works, but the published image is x86
> only today, so the first boot compiles it on the VM (10-20 min on 2 vCPU). Use
> x86 (`cx22`, `cpx11`, `t3.medium`) if you want the fast path.

## 2. Hetzner Cloud

```bash
# settings first: provider + key (+ anything optional)
$EDITOR codx-junior-installer/cloud/cloud-init.yaml   # write_files: section

hcloud server create \
  --name codx-junior \
  --type cx22 \
  --image ubuntu-24.04 \
  --location nbg1 \
  --ssh-key ~/.ssh/id_ed25519.pub \
  --user-data-from-file codx-junior-installer/cloud/cloud-init.yaml

hcloud server ip codx-junior     # -> http://<ip>:19981 in 3-8 minutes
```

Then open port `19981` for your IP (Hetzner Cloud Firewall → add rule
`tcp/19981`, source = your IP), or pass `CODX_JUNIOR_OPEN_FIREWALL=1` in the
settings to let the installer enable `ufw` on the machine itself.

Watch the boot:

```bash
ssh root@<ip> tail -f /var/log/codx-junior-install.log
```

## 3. DigitalOcean

```bash
doctl compute droplet create codx-junior \
  --image ubuntu-24-04-x64 \
  --size s-2vcpu-4gb \
  --region ams3 \
  --ssh-keys "$(doctl compute ssh-key list --format ID --no-header | head -1)" \
  --user-data-file codx-junior-installer/cloud/cloud-init.yaml \
  --wait
```

DigitalOcean Cloud Firewalls: allow `tcp/19981` (or use `ufw` as above).

## 4. AWS EC2

```bash
# 1. security group: inbound tcp/19981 (and 22) from your IP
# 2. launch with the user-data script (edit its four settings first)
aws ec2 run-instances \
  --image-id ami-0xxxxxxxxxxxxxxx \
  --instance-type t3.medium \
  --key-name mykey \
  --security-group-ids sg-xxxxxxxx \
  --user-data file://codx-junior-installer/cloud/aws-user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=codx-junior}]'
```

`aws-user-data.sh` works on Amazon Linux 2023 and on Ubuntu AMIs.

## 5. Google Compute Engine

```bash
gcloud compute instances create codx-junior \
  --machine-type e2-medium \
  --image-family ubuntu-2404-lts-amd64 \
  --image-project ubuntu-os-cloud \
  --zone europe-west1-b \
  --tags codx-junior \
  --metadata-from-file startup-script=codx-junior-installer/cloud/gcp-startup-script.sh

gcloud compute firewall-rules create codx-junior-web \
  --allow tcp:19981 --target-tags codx-junior
```

The GCE script runs on every boot, which is fine: the installer is idempotent
and only reconciles. It writes `/etc/codx-junior/settings.env` only once, so a
rotated key survives a reboot.

## 6. Anything else (VPS, Proxmox, LXD, bare metal)

Any machine where you have a root shell:

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/codx-junior-installer/cloud/bootstrap.sh \
  -o /tmp/codx-junior.sh
sudo bash /tmp/codx-junior.sh --provider openai --key sk-xxxxxxxx
```

### HTTPS with a domain

The installer publishes plain HTTP on `19981`. To serve a real domain, put any
reverse proxy in front — Caddy is two lines on the host:

```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
codx.example.com {
    reverse_proxy 127.0.0.1:19981
}
EOF
sudo systemctl reload caddy
```

Caddy gets the certificate automatically and passes websockets through. Set
`CODX_JUNIOR_PUBLIC_URL=https://codx.example.com` in the settings so the install
summary prints that address, and close `19981` in the cloud firewall.

## 7. Settings

Everything lives in `/etc/codx-junior/settings.env` — one `KEY=value` per line.
Precedence: **default < settings file < environment < command line flags**.

| Setting | Default | Meaning |
|---|---|---|
| `CODX_JUNIOR_AI_PROVIDER` | `deepseek` | `openai` `deepseek` `gemini` `openrouter` `mistral` `groq` `ollama` `custom` — it also decides the models |
| `CODX_JUNIOR_AI_API_KEY` | — | **required** (not for a local `ollama`) |
| `CODX_JUNIOR_AI_API_KEY_FILE` | — | read the key from a file instead (keeps it out of `ps`/argv) |
| `CODX_JUNIOR_AI_API_URL` | preset | OpenAI-compatible endpoint; required for `custom` |
| `CODX_JUNIOR_AI_MODEL` | preset | override the chat model |
| `CODX_JUNIOR_PROJECTS_PATH` | `/opt/codx-junior/projects` | where your projects live (bind mount) |
| `CODX_JUNIOR_WEB_PORT` | `19981` | published web port |
| `CODX_JUNIOR_PUBLIC_URL` | auto-detected | address printed at the end of the install |
| `CODX_JUNIOR_HOME` | `/opt/codx-junior` | everything the installer creates lives here |
| `CODX_JUNIOR_REPO` / `CODX_JUNIOR_REF` | GitHub `main` | what to deploy (pin a tag for a fixed version) |
| `CODX_JUNIOR_BUILD` | `0` | `1` = build the image on the VM instead of pulling it |
| `CODX_JUNIOR_INSTALL_DOCKER` | `1` | `0` = fail instead of installing Docker |
| `CODX_JUNIOR_OPEN_FIREWALL` | `0` | `1` = `ufw allow` ssh + the web port |

Flags of the same name exist for everything (`--provider`, `--key`, `--port`,
`--projects`, `--ref`, `--build`, `--open-firewall`, `--env-file`, …); run
`bootstrap.sh --help`.

### Where things end up

| Path | Contents |
|---|---|
| `/opt/codx-junior/src` | git checkout of this repository (upgrade with `codx-junior upgrade`) |
| `/opt/codx-junior/src/codx-junior-installer/demo/.env` | the settings `up.sh` wrote (mode 600 — **it holds the API key**) |
| `/opt/codx-junior/projects` | your projects |
| `/etc/codx-junior/settings.env` | the input settings (mode 600) |
| `/etc/default/codx-junior` | home + port for the `codx-junior` CLI |
| `/var/log/codx-junior-install.log` | full install log |
| docker volumes `codx-junior-settings`, `codx-junior-analytics` | global settings, AI logs, token usage |

The key is stored twice on purpose: `settings.env` is what you edit,
`demo/.env` is what the container reads. Both are `600 root`.

## 8. Verify

```bash
codx-junior status                 # container state, health, URL
curl -fsS http://localhost:19981/  # nginx answers
docker exec codx-junior-demo curl -fsS http://127.0.0.1:19980/api/bootstrap
```

The last command prints the provider and the models the installer configured
(`{"status":"ok","bootstrap":{...}}`). If `reason` says the configuration was
incomplete, the key was rejected — check the provider and key, then:

```bash
codx-junior logs --tail 50
```

## 9. Day-2 operations

The installer also puts a small CLI on the machine:

| Command | Does |
|---|---|
| `codx-junior status` | container state, health and URL |
| `codx-junior url` | the URL this instance answers on |
| `codx-junior logs [-f] [--tail N]` | container logs |
| `codx-junior restart` | restart the container |
| `codx-junior up` | start / recreate (run it after editing `.env`) |
| `codx-junior down` | stop and remove the container (volumes are kept) |
| `codx-junior upgrade` | `git pull` + pull/build the image + recreate |
| `codx-junior env` | show the settings with the API key masked |

**Upgrade:** `codx-junior upgrade`, or re-run `bootstrap.sh` (same effect,
idempotent; projects and settings are untouched).

**Change the provider later:** edit `/etc/codx-junior/settings.env`, then
`codx-junior up` — `up.sh` re-applies the configuration. You can also pick a
different provider in the UI (Settings → AI); the preset models come from the
same catalogue the installer uses.

**Restart on reboot** is handled by Docker (`restart: unless-stopped`).

## 10. Cost

The demo fits comfortably in the smallest x86 instance of every provider
(2 vCPU / 4 GB), roughly **€4-8 / month** on Hetzner, **$12-18 / month** on
DigitalOcean, ~**$30 / month** on `t3.medium` (spot/graviton is cheaper). It
scales down to 2 GB once the image is pulled if you do not build on the VM.

## 11. Uninstall

```bash
codx-junior down
docker volume rm codx-junior-settings codx-junior-analytics
sudo rm -rf /opt/codx-junior /etc/codx-junior /etc/default/codx-junior /usr/local/bin/codx-junior
```

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `permission denied while trying to connect to the Docker daemon` | the script did not finish installing Docker; run `sudo systemctl start docker` and re-run |
| install log stops after "Downloading the repository" | no outbound network / GitHub blocked — set `CODX_JUNIOR_REPO` to your mirror |
| stops at "pull access denied" | `codxjunior/codx-junior:demo` is **not published yet**; the installer falls back to a local build. On a small VM set `CODX_JUNIOR_BUILD=1` so it does not retry the pull, and give it 4 GB RAM |
| killed during the build (`exit 137`) | out of memory while building: use a 4 GB instance, or a machine with swap |
| URL unreachable from your laptop | the port is blocked: cloud firewall (Hetzner/DO/AWS) or `ufw status` on the host |
| page loads but chats fail | the provider key was rejected: `docker exec codx-junior-demo curl -fsS http://127.0.0.1:19980/api/bootstrap` and `codx-junior logs` |
| `docker compose` not found | the compose plugin install failed; `bootstrap.sh` retries it — check `/var/log/codx-junior-install.log` |

## 13. What this tier does **not** include

Single container, single user, no TLS of its own:

- no workspaces, browser/computer sessions or the infra panel — those need
  `docker.sock` and are part of the [server deployment](../README.md#6-server-deployment-tls-workspaces-local-models)
- no local models (LocalAI/Ollama are not started; bring your own endpoint via
  `custom`)
- no vector database container — knowledge/RAG runs on embedded LanceDB
- no automatic domain or certificate — the Caddy snippet in §6 is the whole story
