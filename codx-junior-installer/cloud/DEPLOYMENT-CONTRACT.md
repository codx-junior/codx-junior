# codx-junior — unattended deployment contract

Everything below is the *supported* way to install codx-junior on a machine nobody
touches: a cloud VM bootstrapped by cloud-init, a hosted workspace, a CI runner.
If a step here is not honoured by the code, that is a bug in this repository, not
a gap in your automation.

Status of each item is stated explicitly. `[verified]` = exercised by the code in
this tree; `[unverified]` = implemented but never run on a real VM from here.

```
settings file ──▶ install.sh ──▶ codx-junior-installer/cloud/bootstrap.sh ──▶ docker compose
   (root 0600)     (one entry         (host prep, clone, domain/TLS)          (pull only)
                     point)
```

---

## 1. The settings file

`/etc/codx-junior/settings.env`, root:root, mode `0600`, passed with
`--env-file`. Precedence, lowest first: **default < settings file < environment < flag**.

| key | honoured | meaning |
|---|---|---|
| `CODX_JUNIOR_AI_PROVIDER` | `[verified]` | `openai` `deepseek` `gemini` `openrouter` `ollama` `custom` |
| `CODX_JUNIOR_AI_API_KEY` | `[verified]` | provider key; omitted for `ollama` |
| `CODX_JUNIOR_AI_MODEL` | `[verified]` | override the preset model |
| `CODX_JUNIOR_AI_API_URL` | `[verified]` | OpenAI-compatible endpoint (required for `custom`) |
| `CODX_JUNIOR_WEB_PORT` | `[verified]` | published port, default `19981` |
| `CODX_JUNIOR_REF` | `[verified]` | branch **or tag** to install/update; default `main` |
| `CODX_JUNIOR_PROJECTS_PATH` | `[verified]` | data folder, keep it outside the clone; default `./projects` |
| `CODX_JUNIOR_PUBLIC_URL` | `[verified]` | the URL the instance believes it is served on (links, OAuth redirects, websockets) |
| `CODX_JUNIOR_DOMAIN` | `[verified]` | enables the bundled TLS front door (see §4) |
| `CODX_JUNIOR_NON_INTERACTIVE` | `[verified]` | `1` = never prompt (see §3) |
| `CODX_JUNIOR_ADMIN_USER` | `[verified]` | first admin username (see §5) |
| `CODX_JUNIOR_ADMIN_PASSWORD` | `[verified]` | first admin password, bcrypt-hashed, never logged |
| `CODX_JUNIOR_ADMIN_EMAIL` | `[verified]` | first admin email (optional) |
| `CODX_JUNIOR_IMAGE` | `[verified]` | force the container image; overrides the ref→tag mapping |
| `CODX_JUNIOR_BUILD_*` | `[verified]` | `VERSION`/`REF`/`COMMIT` reported by `/api/health`; set by the release build, not by you |

`CODX_JUNIOR_REF`, `CODX_JUNIOR_PROJECTS_PATH` and `CODX_JUNIOR_PUBLIC_URL` are
confirmed: keep writing them exactly as you do today.

**Upgrades never touch data.** `CODX_JUNIOR_PROJECTS_PATH` lives outside the
checkout, so re-running the installer upgrades code and leaves every project,
chat and database file untouched.

---

## 2. Install paths, minimum box, wall clock

| path | how | minimum box | wall clock | notes |
|---|---|---|---|---|
| **pull** (supported for unattended installs) | `install.sh --ref <tag>` | 2 vCPU / 4 GB / 40 GB | ~3-6 min (image pull dominates) | nothing is compiled on the box |
| **build** (developers, explicit) | `install.sh ... ` then `demo/up.sh --build` | 4 vCPU / 8 GB / 20 GB free | ~20 min | `--build` is the *only* way to compile |

A failed pull is **fatal** (`[verified]`, `demo/up.sh`): the installer prints the
tag, the `--build` alternative and "nothing was started", then exits non-zero.
There is no silent fallback to a build — on a paid 2 vCPU box that fallback used
to be a 20 minute compile with nobody watching.

### Pinning

```bash
install.sh --ref v1.2.3 --non-interactive </dev/null
```

* `vX.Y.Z` → image `codxjunior/codx-junior:vX.Y.Z` (`[verified]`)
* anything else (`main`, a branch name, a raw commit) → `codxjunior/codx-junior:demo`
* `--dev` (developers only) → `codxjunior/codx-junior:dev`, built from the checkout

The project is **pre-1.0**: the first release tag is `v0.0.1` and the installer
reports `install.sh 0.0.1`. Tags stay orderable (`v0.0.1` < `v0.1.0` < `v1.0.0`),
so pinning works exactly the same from the first one on — do not expect a `v1.x`
line before the API is frozen.

A release tag publishes its own image from `.github/workflows/release.yml`, so a
pinned ref is reproducible and rollbackable. **Do not track `main` in customer
infrastructure**: it is a moving branch and a broken commit breaks every new
workspace at once.

> Creating the tag itself is the repository owner's step (`git tag -a v1.2.3 -m ...`,
> `git push origin v1.2.3`). The workflow cannot tag on your behalf.

---

## 3. Non-interactive mode

```bash
sudo ./install.sh --env-file /etc/codx-junior/settings.env \
     --domain <slug>.example.com --non-interactive </dev/null
```

`--non-interactive`, `--yes`, `-y` or `CODX_JUNIOR_NON_INTERACTIVE=1`
(`[verified]`) guarantee:

* stdin is closed for the whole run, so a prompt becomes an **EOF failure**, never a hang;
* `DEBIAN_FRONTEND=noninteractive`, `NEEDRESTART_MODE=a` — apt never stops for a TTY;
* `GIT_TERMINAL_PROMPT=0` — a private or misspelled repo fails instead of asking for credentials.

A missing required setting is a non-zero exit with the name of the setting, not a
default silently chosen for you.

---

## 4. HTTPS

Two supported modes, pick one:

```bash
# A) the installer owns TLS (Caddy, automatic ACME, ports 80+443)
./install.sh --domain x.example.com --non-interactive

# B) you already have a TLS terminator (Cloudflare, ALB, another proxy)
./install.sh --tls=none --non-interactive
```

Mode **A** (`[verified]` in `cloud/bootstrap.sh`): Caddy is started with
`80/443 → 127.0.0.1:<port>`, waits up to 3 minutes for a *valid* certificate
(verified with `--resolve x.example.com:443:127.0.0.1`), and fails loudly with the
DNS/ACME cause if it never appears. Requirements:

* the `A` record must already answer with this machine's IP — **publish DNS before
  the install**, otherwise the first ACME order burns the Let's Encrypt rate limit;
* `proxied = false` on Cloudflare, or ACME challenges never reach the box;
* ports 80 and 443 open (ufw is opened automatically when a domain is used).

Mode **B** forwards plain HTTP from your terminator; no certificate is requested.

---

## 5. First admin

An unattended install has nobody to fill a setup form, so the admin comes from the
settings file (`[verified]`, `api/codx/junior/security/admin_bootstrap.py`, run on
startup):

```env
CODX_JUNIOR_ADMIN_USER=admin
CODX_JUNIOR_ADMIN_PASSWORD=<generated per workspace>
CODX_JUNIOR_ADMIN_EMAIL=admin@example.com
```

* bcrypt-hashed before storage; the plain password is never logged, never written
  to `db.json`, never echoed by the installer;
* it runs **once**: if any admin already exists the keys are ignored, so changing
  the password later is done from the UI and a stale `settings.env` cannot reset it;
* remove the key from the file after the first successful boot if your platform can
  rewrite it.

---

## 6. Readiness: `GET /api/health`

Unauthenticated, stable shape, always present keys — this is the probe to poll
instead of `/`:

```json
{
  "status": "ok",
  "version": "1.2.3",
  "ref": "v1.2.3",
  "commit": "9f1ab2c3d4e5",
  "uptime_seconds": 41.7,
  "event_loop_lag_ms": { "last_ms": 1.2, "max_ms": 18.0 },
  "projects_cached": 1,
  "api_log_pipeline": { "queue_depth": 0, "written_events": 12 }
}
```

`version` / `ref` / `commit` come from the image build (`[verified]`,
`api/codx/junior/version.py`); they answer `"unknown"` only for a local
`--build`, which is exactly what you want to detect.

Polling recipe (what the shipped `cloud/bootstrap.sh` does):

```bash
until curl -fsS "https://x.example.com/api/health" > /tmp/health.json; do sleep 5; done
python3 - <<'PY'
import json
h = json.load(open("/tmp/health.json"))
assert h["status"] == "ok"
assert h["ref"] == "v1.2.3", h          # the ref we asked for, not "something"
PY
```

---

## 7. What a successful install guarantees

1. the image was **pulled**, not compiled (`--build` absent);
2. the published port answers `GET /api/health` with `status=ok`;
3. `ref`/`commit` match the ref that was requested;
4. when `--domain` is used, `https://<domain>/api/health` answers with a valid certificate;
5. when the admin keys are set, `POST /api/users/login` accepts them.

Anything else is a failed install: the installer exits non-zero and starts nothing
(§2) or fails at the step that broke (§4).

---

## 8. Known gaps

* The release workflow has never run in this repository (no tag exists yet), so
  the first `v*` push is its real smoke test. Until then a pinned `--ref vX.Y.Z`
  fails at the pull — loudly, by design.
* `demo/up.sh --build` reports `"unknown"` build provenance; `--dev` and release
  images both carry a real `version`/`ref`/`commit` (from `git describe` and
  from the CI build args respectively).
* `.codx` is the control plane. `.dockerignore` drops only the **generated** data
  plane (chat turns, browser sessions, atlas, db, logs — 1.5 GB on a developer
  box) and keeps every tracked artifact (profiles, tasks, boards, views, teams,
  workspaces) in the context, so `./up.sh --dev` builds against the real control
  plane. The release image never `COPY`s `.codx`: it is created at first boot
  inside the `codx-junior-settings` volume. **Nothing is untracked from the
  repository** — no `git rm --cached`.
