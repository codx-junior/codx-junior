# Updating codx-junior from the UI (admin only)

**Settings → Updates** keeps an installation current without SSH: it pulls the
application image and runs the installer's `upgrade`, which recreates the
container. Settings, projects and data are kept — only the running container is
replaced.

The same operation from the host is:

```bash
sudo /opt/codx-junior/bin/codx-junior update
```

---

## 1. The installer is the updater

There is no second update path. `codx-junior-installer/install.sh upgrade`
(the `codx-junior` CLI calls it with `--home`, `--instance` and `--env-file`
already filled in) does:

1. reload the deployment settings from `<home>/config/<instance>.env`;
2. `docker compose pull codx-junior`;
3. `docker compose up -d --remove-orphans` — recreates the container with the
   new image;
4. wait for the health check, re-apply the bootstrap;
5. rewrite the compose wrapper and the update descriptor.

It is idempotent: running it twice is not a reinstall, and running it when the
image is current only restarts the container.

---

## 2. Why the API needs a helper container

The API runs *inside* the container the installer recreates. It cannot stop and
replace itself, and an image built before the Docker CLI was bundled has no
`docker` binary to shell out to. The flow is therefore:

```text
browser ──POST /api/system/update──▶ API (old container)
                                     │ 1. pull image  (Docker Engine API)
                                     │ 2. create+start helper container
                                     │    (target image, socket + home mounted)
                                     ▼
                          helper ──▶ install.sh upgrade
                                     │ pull (no-op) / compose up -d
                                     ▼
                          API (new container) ◀── installer recreates it
                                     ▲
browser ──GET /api/system/update─────┘  (poll through the restart)
```

* **Pull through the Engine API**, not the CLI: the installs that need updating
  predate the bundled CLI. The daemon streams progress; registry/auth errors
  arrive inside a `200` response and are surfaced as `failed`.
* **Helper container**: created from the *target* image (which does have the
  CLI), `--network host` so the installer's health probe on `127.0.0.1:<port>`
  is meaningful, with `AutoRemove`, the Docker socket and the installation home
  bind-mounted at the same paths. It runs:

  ```bash
  bash <home>/bin/codx-junior update --non-interactive [--image <target>]
  ```

  The script first checks that `<home>/lib/install.sh` exists: Docker silently
  creates a missing bind source, and an empty directory would make the
  installer write its settings into the wrong place.
* **Shared log/state**: the settings folder is bind-mounted in both containers.
  The helper writes `<settings>/logs/update-<timestamp>.log` (the installer
  honours `CODX_JUNIOR_LOG`) and appends a
  `CODX_UPDATE_EXIT=<n>` marker, which is the authoritative result.

---

## 3. Finding the installation

The API only mounts `<data>/settings`, never the installation home, so the
installer publishes the facts an update needs in
**`<data>/settings/installer.json`**:

```json
{
  "source": "codx-junior-installer",
  "installer_version": "1.0.0",
  "home": "/opt/codx-junior",
  "instance": "codx-junior",
  "mode": "local",
  "container": false,
  "image": "codxjuniorai/codx-junior:latest",
  "env_file": "/opt/codx-junior/config/codx-junior.env",
  "data_dir": "/opt/codx-junior/data",
  "cli": "/opt/codx-junior/bin/codx-junior",
  "installer": "/opt/codx-junior/lib/install.sh",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

Installations that predate the descriptor are discovered from their
`com.docker.compose.*` labels:

| Label | Meaning |
| :-- | :-- |
| `com.docker.compose.project` | instance name |
| `com.docker.compose.project.working_dir` | `<home>/lib/runtime` (host) or `<home>` (DIND) |
| `com.docker.compose.project.config_files` | distinguishes the two layouts |

If neither source identifies a compose-managed installation, the page says so
and shows the host command instead of guessing.

---

## 4. API

Both routes require the `admin` role.

### `GET /api/system/update`

```json
{
  "supported": true,
  "reason": "",
  "manual_command": "/opt/codx-junior/bin/codx-junior update",
  "current": {
    "version": "1.4.0", "ref": "v1.4.0", "commit": "abc1234def56",
    "image": "codxjuniorai/codx-junior:latest",
    "instance": "codx-junior", "mode": "local",
    "container_id": "…", "container_name": "codx-junior",
    "installer_home": "/opt/codx-junior"
  },
  "installation": { "home": "…", "instance": "…", "image": "…", "layout": "host", "source": "descriptor" },
  "update": {
    "status": "completed", "message": "The new container is healthy.",
    "from_image_id": "sha256:…", "to_image_id": "sha256:…",
    "started_at_iso": "…", "finished_at_iso": "…", "exit_code": 0,
    "log": "…", "helper_running": false
  }
}
```

`supported: false` always carries `reason` and `manual_command`; the payload is
otherwise complete.

### `POST /api/system/update`

```json
{ "image": "codxjuniorai/codx-junior:v1.5.0", "force": false }
```

`image` is optional (default: the running image), `force` recreates the
container even when the pull did not change the image. Answers `202` with the
initial state; the UI polls `GET`.

### `GET /api/system/update/log?lines=200`

The current/last log tail; it survives the container restart.

---

## 5. Statuses

| Status | Meaning |
| :-- | :-- |
| `idle` | Never updated (or the state file was lost). |
| `pulling` | The API is pulling the image through the Engine API. |
| `updating` | The helper is running the installer; the container will restart. |
| `up_to_date` | The image did not change; nothing was restarted. |
| `completed` | The installer exited 0 and the new container is healthy. |
| `failed` | Pull/installer failed; the old container is still serving. |
| `unknown` | The container restarted before the helper reported a result. |

Error statuses: `400` invalid image reference, `403` not an admin, `409` cannot
update this instance (no compose installation, DIND, remote daemon) or an
update already runs, `503` Docker/registry failure.

---

## 6. Requirements and limits

* **Docker socket.** The in-app update needs the socket mounted in the API
  container (`--coworking`, or `--mode dind`). Without it the page shows the
  host command.
* **Local daemon only.** Remote hosts (`CODX_JUNIOR_INFRA_HOSTS`,
  `DOCKER_HOST=tcp://…`) must be updated on that host.
* **DIND** instances are updated by restarting the DIND wrapper: its entrypoint
  re-runs the installer and recreates the inner stack. The in-app updater only
  reports that command; on the host, `install.sh --update` (or `codx-junior
  upgrade`) recreates the wrapper even when the DIND digest did not change.
* **Private registries:** the Engine API pull is anonymous. A private image
  fails with a clear message; run the installer on the host (it uses the host's
  Docker credentials).
* **Host networking:** the helper uses `--network host` so the installer's
  health probe on `127.0.0.1:<port>` is meaningful. Linux hosts (Docker Desktop
  ≥ 4.34) support this; an unsupported daemon rejects the helper with a clear
  message.
* **File ownership:** the helper runs as root. Installations created by a
  non-root user may end up with root-owned files under `<home>` after an update
  from the UI.
* **Pinned tags do not move.** Update a pinned installation by entering the new
  tag in *Target image* (it is persisted as `CODX_JUNIOR_IMAGE`), or by
  re-running the installer with `--image`/`--ref`.

---

## 7. Tests

`api/tests/test_update.py` covers discovery (descriptor and labels), the
DIND/remote/unsupported decisions, image-reference validation, the helper
command and bind mounts, pull-and-start, `up_to_date`, forced recreation, the
exit-marker state machine, the exclusivity lock and the HTTP routes (including
the `409`/`400` mappings). No Docker daemon or installer is required.

The installer side is covered by
`codx-junior-installer/tests/test_install.sh` ("update descriptor names the
installation").

Run them with the API virtualenv:

```bash
pytest -q api/tests/test_update.py
bash codx-junior-installer/tests/test_install.sh
```

Made with ❤️ by codx-junior
