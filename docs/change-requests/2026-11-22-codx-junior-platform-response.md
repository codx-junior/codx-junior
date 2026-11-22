# Response — workspace API for hosted deployments

**In reply to:** `codx-junior-cloud/website/docs/change-requests/2026-11-22-codx-junior-platform.md`
**From:** codx-junior platform team (`codx-junior/codx-junior`)
**Date:** 2026-11-22
**Status:** UPR-2 … UPR-7 implemented in this tree. UPR-1 confirmed as yours and already fixed on your side.

---

## 1. Answers first

**Q1 — Bootstrap keys.** `CODX_JUNIOR_ADMIN_USER` / `CODX_JUNIOR_ADMIN_PASSWORD` /
`CODX_JUNIOR_ADMIN_EMAIL` **are the stable first-boot contract** (`[verified]`,
`api/codx/junior/security/admin_bootstrap.py`). Keep writing them. Write nothing
called `CODX_JUNIOR_OWNER_EMAIL`: it is not read anywhere in this repository and
is documented as removed (`codx-junior-installer/cloud/DEPLOYMENT-CONTRACT.md` §1).

**Q2 — Scoped credential.** `CODX_JUNIOR_WORKSPACE_TOKEN` authenticates **as the
workspace owner**: `CODX_JUNIOR_ADMIN_USER` when that account exists, otherwise
the first enabled admin, otherwise a `workspace-service` principal with the
`admin` role (so a workspace with an empty user store is still usable). It is an
opaque API key, compared in constant time, unrelated to the interactive password,
and it never returns the owner's session JWT or API key. Rotation = write the new value to
the settings file and recreate the container; the environment is read per request,
so the old value stops working with the new container.

**Q3 — Task path.** Both are accepted, now and long-term: `<project>` (the project
root) and `<project>/.codx`. `GET /api/projects` keeps returning the canonical
`codx_path` (`<project>/.codx`) and remains the recommended value to cache. A
path that is not a project answers `422 codx_path_not_found`; an absent path with
no project answers `422 codx_path_required`. Neither is a `500`.

**Q4 — Default project.** A fresh install does **not** create a project
implicitly: writing projects into a customer's data folder as a side effect of a
readiness check is not a good default. The supported sequence is: create the
first project with `POST /api/projects?project_path=<folder>` (or pass a git URL
to clone), then `POST /api/chats/tasks` **without** `codx_path` uses it as the
default. That is the only project, or deterministically the first one under
`CODX_JUNIOR_PROJECTS_PATH`. `workspace_api.default_codx_path` on `/api/health`
tells you whether a default exists.

**Q5 — Auth header.** `Authorization: Bearer …` is the primary header, accepted
everywhere. `authentication: Bearer …` is kept as a compatibility alias for the
bundled UI. When both are present **`Authorization` wins**. Documented in
`DEPLOYMENT-CONTRACT.md` §7b and published in the health block.

---

## 2. What was done, per finding

| ID | Owner | Resolution |
| --- | --- | --- |
| **UPR-1** | You | Confirmed: `CODX_JUNIOR_ADMIN_*` is the contract. We do not adopt `CODX_JUNIOR_OWNER_EMAIL`; it is documented as removed. `CODX_JUNIOR_ADMIN_EMAIL` is a login identifier (UPR-6), so the customer can sign in with the email they registered. |
| **UPR-2** | Platform | `CODX_JUNIOR_WORKSPACE_TOKEN` is accepted as a bearer on every protected route, including `GET /api/users/me/refresh`, `POST /api/chats/tasks` and `GET /api/projects`. Constant-time comparison; rotating it invalidates the old value; absent means `401`. |
| **UPR-3** | Platform | `get_authenticated_user` now **raises `401`** instead of returning `None`, so a protected route can never run anonymously and a handler can no longer read `None.username` and answer `500`. `POST /api/chats/tasks` requires a credential; the payload contract is unchanged. Public routes opt out explicitly through `get_optional_authenticated_user`. |
| **UPR-4** | Platform | `Authorization: Bearer` is accepted as the primary form, `authentication` as the alias, with `Authorization` precedence. `Bearer undefined` / `Bearer null` are treated as "no token", which is what the bundled UI sends before login. |
| **UPR-5** | Platform | `codx_path` accepts the project root, the `.codx` folder or `project.json`; the projects root resolves to the default project. Missing `codx_path` uses the default project. Missing/unknown paths answer `422` with `code` + actionable `message` (never `500`). Task creation needs no filesystem knowledge once a project exists. |
| **UPR-6** | Platform | Login resolves the identifier against `username` first, then `email`, case-insensitively. Wrong password and unknown email return the **same** `401 {"detail": {"code": "invalid_credentials", …}}`. An identifier matching several accounts returns `409 ambiguous_login` instead of guessing. Email login never creates a missing credential. |
| **UPR-7** | Platform | `GET /api/health` publishes a versioned, additive `workspace_api` block (see below). It is computed from the same constants the endpoints use, so it cannot drift. |

Regression tests: `api/tests/test_workspace_api_contract.py` (33 tests) covers the
header precedence, the scoped token (including both header spellings creating a
task), rotation, `401`/`422` instead of `500`, email login and the health block.

---

## 3. The contract, as implemented

Only two corrections to your §5 table:

* `GET /api/projects` answers `{"projects": [...], "workspaces": [...]}`, not a
  bare list. Each project carries `codx_path`.
* `POST /api/chats/tasks` returns the created task object (`200 {id, name, …}`).

Everything else matches. The error bodies:

```json
{"detail": {"code": "missing_credentials", "message": "Authentication is required: send 'Authorization: Bearer <token>' (the legacy 'authentication' header is also accepted)."}}
{"detail": {"code": "invalid_credentials", "message": "Invalid or expired credentials."}}
{"detail": {"code": "invalid_credentials", "message": "Invalid username or password."}}
{"detail": {"code": "ambiguous_login", "message": "That email matches several accounts; sign in with the username instead."}}
{"detail": {"code": "codx_path_required", "message": "A project is required: pass ?codx_path=<project> or ?codx_path=<project>/.codx, or create a project with POST /api/projects?project_path=<folder> first. GET /api/projects lists the codx_path of every project."}}
{"detail": {"code": "codx_path_not_found", "message": "codx_path does not point to a codx-junior project. Pass either the project folder or its .codx folder; GET /api/projects returns the codx_path of every project."}}
```

`/api/health` now includes:

```json
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
```

`default_codx_path` is `null` until a project exists.
`scoped_token_configured` says whether the key is present on *this* instance
(`scoped_token` says the build supports it), so you can detect a
mis-provisioned instance as well as an old image.

---

## 4. Verification recipe

```bash
B=https://<slug>.codx-junior.ai
W=$CODX_JUNIOR_WORKSPACE_TOKEN

# UPR-7 / readiness + capability block
curl -fsS $B/api/health | jq .workspace_api

# UPR-2 / scoped credential, standard header
curl -fsS -o /dev/null -w '%{http_code}\n' \
  $B/api/users/me/refresh -H "Authorization: Bearer $W"          # 200

# UPR-2 / without it
curl -s -o /dev/null -w '%{http_code}\n' $B/api/users/me/refresh  # 401

# UPR-4 / legacy alias
curl -fsS -o /dev/null -w '%{http_code}\n' \
  $B/api/users/me/refresh -H "authentication: Bearer $W"          # 200

# UPR-5 / project root is accepted, and no codx_path is accepted
curl -fsS -X POST "$B/api/chats/tasks?codx_path=/home/codx-junior-projects/demo" \
  -H "Authorization: Bearer $W" -H 'Content-Type: application/json' \
  -d '{"name":"root path","description":"accepted"}'

curl -fsS -X POST "$B/api/chats/tasks" \
  -H "Authorization: Bearer $W" -H 'Content-Type: application/json' \
  -d '{"name":"default project","description":"accepted"}'

# UPR-3 / anonymous write
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$B/api/chats/tasks" \
  -H 'Content-Type: application/json' \
  -d '{"name":"anonymous","description":"must be refused"}'        # 401

# UPR-6 / email login, wrong password, unknown email
curl -s -o /dev/null -w '%{http_code}\n' -X POST $B/api/users/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"'"$CODX_JUNIOR_ADMIN_EMAIL"'","password":"'"$CODX_JUNIOR_ADMIN_PASSWORD"'"}'  # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST $B/api/users/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"'"$CODX_JUNIOR_ADMIN_EMAIL"'","password":"wrong"}'                            # 401
```

---

## 5. Notes and limits

* **Scope of "no anonymous writes".** Every route that depends on the shared
  authentication dependency now requires a credential. Endpoints with their own
  authentication mechanism (the signed DSH webhook) keep it; genuinely public
  routes (login, health, the Traefik forward-auth probe) opt out explicitly.
  This change does not retrofit authentication onto legacy routes that never
  declared it — that is a larger audit and is tracked separately.
* **Ordering.** UPR-2 and UPR-3 landed together, as requested. There is no
  intermediate build where auth is enforced but the scoped token is not accepted.
* **Client compatibility.** The bundled UI still sends `authentication`; it keeps
  working. Because a rejected session now answers `401` (instead of the old
  `200 null`), the UI treats a `401` while restoring a stored session as "not
  logged in" and shows the login form instead of an error toast.
* **Rotation** of the scoped token requires recreating the container (the value
  is read from the environment per request). That is the same operation as
  rotating the admin password in the settings file.
