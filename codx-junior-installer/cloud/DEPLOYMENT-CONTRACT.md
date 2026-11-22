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
| `CODX_JUNIOR_AI_PROVIDER` | `[verified]` | `openai` `deepseek` `gemini` `openrouter` `mistral` `groq` `custom` |
| `CODX_JUNIOR_AI_API_KEY` | `[verified]` | provider key (any non-empty value for a local server that does not check it) |
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
| `CODX_JUNIOR_ADMIN_EMAIL` | `[verified]` | first admin email (optional, usable as a login identifier) |
| `CODX_JUNIOR_WORKSPACE_TOKEN` | `[verified]` | scoped per-instance API key for machine clients (see §5b) |
| `CODX_JUNIOR_IMAGE` | `[verified]` | force the container image; overrides the ref→tag mapping |
| `CODX_JUNIOR_BUILD_*` | `[verified]` | `VERSION`/`REF`/`COMMIT` reported by `/api/health`; set by the release build, not by you |

`CODX_JUNIOR_REF`, `CODX_JUNIOR_PROJECTS_PATH`, `CODX_JUNIOR_PUBLIC_URL` and
`CODX_JUNIOR_WORKSPACE_TOKEN` are
confirmed: keep writing them exactly as you do today.

> **`CODX_JUNIOR_OWNER_EMAIL` is not a platform key.** It is not read anywhere
> in this repository and must not be written: the first-boot admin contract is
> `CODX_JUNIOR_ADMIN_USER` / `CODX_JUNIOR_ADMIN_PASSWORD` /
> `CODX_JUNIOR_ADMIN_EMAIL` (§5).

**Upgrades never touch data.** `CODX_JUNIOR_PROJECTS_PATH` lives outside the
checkout, so re-running the installer upgrades code and leaves every project,
chat and database file untouched.

---

## 2. Install paths, minimum box, wall clock

| path | how | minimum box | wall clock | notes |
|---|---|---|---|---|
| **pull** (supported for unattended installs) | `install.sh --ref <tag>` | 2 vCPU / 4 GB / 40 GB | ~3-6 min (image pull dominates) | nothing is compiled on the box |
| **build** (developers, explicit) | `install.sh ... --build` | 4 vCPU / 8 GB / 30 GB free | ~20 min | `--build` is the *only* way to compile |

A failed pull is **fatal** (`[verified]`, `install.sh`): the installer prints the
tag, the `--build` alternative and "nothing was started", then exits non-zero.
There is no silent fallback to a build — on a paid 2 vCPU box that fallback used
to be a 20 minute compile with nobody watching.

### Pinning

```bash
install.sh --ref v1.2.3 --non-interactive </dev/null
```

* `vX.Y.Z` → image `codxjuniorai/codx-junior:vX.Y.Z` (`[verified]`)
* anything else (`main`, a branch name, a raw commit) → `codxjuniorai/codx-junior:latest`
* `--dev` (developers only) → `codxjuniorai/codx-junior:dev`, built from the checkout

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

`CODX_JUNIOR_ADMIN_*` is the **stable bootstrap contract** for hosted installs.
`CODX_JUNIOR_ADMIN_EMAIL` is stored on the account and works as a login
identifier (§7b), so the customer can sign in with the email they registered.

---

## 5b. Scoped workspace credential

A console that provisions a workspace should not have to hold the owner's
password. Write a per-instance random secret:

```env
CODX_JUNIOR_WORKSPACE_TOKEN=<random, at least 32 bytes recommended>
```

The API accepts it as a bearer on every protected route (`[verified]`,
`api/codx/junior/security/workspace_api.py`):

```bash
curl -fsS https://x.example.com/api/users/me/refresh \
     -H "Authorization: Bearer $CODX_JUNIOR_WORKSPACE_TOKEN"
```

* it authenticates **as the workspace owner** (`CODX_JUNIOR_ADMIN_USER`, else the
  first admin, else a `workspace-service` principal with the `admin` role), so it
  can create tasks and open support flows;
* it is compared in constant time and never logged;
* it is independent of the interactive password: changing the customer password
  does not invalidate it, and rotating the token does not touch the account;
* **rotation** = write the new value to the settings file and recreate the
  container (`docker compose up -d`). The environment is read per request, so the
  old value stops working as soon as the new container starts;
* when it is absent the whole feature is off and such a bearer answers `401`.

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
  "api_log_pipeline": { "queue_depth": 0, "written_events": 12 },
  "workspace_api": {
    "contract_version": 1,
    "auth_headers": ["authorization", "authentication"],
    "auth_header_precedence": "authorization",
    "scoped_token": true,
    "scoped_token_configured": true,
    "scoped_token_env": "CODX_JUNIOR_WORKSPACE_TOKEN",
    "login_by_email": true,
    "login_endpoint": "/api/users/login",
    "session_endpoint": "/api/users/me/refresh",
    "projects_endpoint": "/api/projects",
    "tasks_endpoint": "/api/chats/tasks",
    "codx_path_accepts_project_root": true,
    "tasks_default_project": true,
    "default_codx_path": "/data/projects/<project>/.codx"
  }
}
```

`workspace_api` is the machine-client contract: a pipeline asserts it instead of
hard-coding assumptions, and an image that predates it simply omits the block.
`default_codx_path` is `null` while no project has been created yet.

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

## 7b. The machine-client API contract

This is the exact surface a provisioning console codes against (`[verified]`,
`api/codx/junior/security/workspace_api.py`). Every route below accepts either
`Authorization: Bearer <token>` (standard, and what a gateway/APM proxy
preserves) or `authentication: Bearer <token>` (the bundled UI's historical
spelling). When both are present `Authorization` wins.

| Operation | Method + path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| Readiness | `GET /api/health` | none | — | `{status, ref, commit, workspace_api}` |
| Owner login | `POST /api/users/login` | none | `{username: <email or username>, password}` | `200 {token, …}` or `401` |
| Session check | `GET /api/users/me/refresh` | bearer | — | `200 {username, …}` or `401` |
| List projects | `GET /api/projects` | bearer | — | `{projects: [{codx_path, …}], workspaces}` |
| Create task | `POST /api/chats/tasks[?codx_path=…]` | bearer | `{name, description, board?, column?, …}` | `200 {id, …}` |
| Any protected route | — | bearer | — | `401` when the credential is absent/invalid |

Rules the API guarantees:

* **No anonymous writes.** A protected route receives a user or answers `401`
  with a stable body: `{"detail": {"code": "missing_credentials" | "invalid_credentials", "message": …}}`.
  `GET /api/users/me/refresh` answers `401`, never the `500` it used to.
* **Login by email.** The login identifier is matched against `username` first,
  then against the account email, case-insensitively. A wrong password and an
  unknown email return the SAME `401` body (no account enumeration). An
  identifier that matches several accounts answers `409` with
  `code: "ambiguous_login"`.
* **`codx_path` discovery.** `codx_path` accepts either the project folder or its
  `.codx` folder, and `GET /api/projects` returns the canonical `.codx` value for
  every project. When `codx_path` is omitted, `POST /api/chats/tasks` uses the
  workspace's default project (the only project, or the first discovered one).
  When no project exists yet, create one first:
  `POST /api/projects?project_path=<folder>`. A path that is not a project
  answers `422` with `code: "codx_path_not_found"`; a missing path with no
  default project answers `422` with `code: "codx_path_required"`. Neither
  answers `500`.

```bash
B=https://x.example.com
T=$(curl -fsS -X POST $B/api/users/login -H 'Content-Type: application/json' \
      -d '{"username":"'$CODX_JUNIOR_ADMIN_EMAIL'","password":"'$CODX_JUNIOR_ADMIN_PASSWORD'"}' \
    | jq -r .token)

# Scoped console credential (no password):
curl -fsS $B/api/users/me/refresh -H "Authorization: Bearer $CODX_JUNIOR_WORKSPACE_TOKEN"

# First task: the client does not need to know the layout.
curl -fsS -X POST "$B/api/chats/tasks" -H "Authorization: Bearer $CODX_JUNIOR_WORKSPACE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"First task","description":"Created by the hosted console"}'
```

A fresh install creates **no project implicitly**: the console creates the first
one with `POST /api/projects?project_path=<folder>` (or clones a repository by
passing its URL) and then reads its `codx_path` from the response.

---

## 8. Known gaps

* The release workflow has never run in this repository (no tag exists yet), so
  the first `v*` push is its real smoke test. Until then a pinned `--ref vX.Y.Z`
  fails at the pull — loudly, by design.
* `install.sh --build` reports `"unknown"` build provenance; `--dev` and release
  images both carry a real `version`/`ref`/`commit` (from `git describe` and
  from the CI build args respectively).
* `.codx` is the control plane. `.dockerignore` drops only the **generated** data
  plane (chat turns, browser sessions, atlas, db, logs — 1.5 GB on a developer
  box) and keeps every tracked artifact (profiles, tasks, boards, views, teams,
  workspaces) in the context, so `./dev-install.sh` builds against the real control
  plane. The release image never `COPY`s `.codx`: it is created at first boot
  inside the `codx-junior-settings` volume. **Nothing is untracked from the
  repository** — no `git rm --cached`.
