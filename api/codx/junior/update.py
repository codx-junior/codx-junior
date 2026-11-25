"""
Update the codx-junior installation from the running API.

There is no separate updater: the supported way to update an instance is the
installer's ``update`` command (``<home>/bin/codx-junior-<instance> update``,
or ``install.sh --home … --instance … --env-file … update``). It reloads the
deployment settings, pulls the image and runs ``docker compose up -d``, which
recreates **this** container. The API therefore cannot run it in-process.

How the update is orchestrated
------------------------------
1. Discover the installation: ``<settings>/installer.json`` (written by the
   installer since this feature) or, for installations that predate it, the
   ``com.docker.compose.*`` labels of this container.
2. Pull the target image through the Engine API. This must not depend on the
   bundled Docker CLI: the very installs that need updating were built before
   the CLI shipped in the image.
3. Start a **helper container** from the target image (which does have the
   CLI), detached and with the installation home + the Docker socket bind
   mounted. It runs the installer's ``update``; the installer replaces this
   container while the helper keeps going.
4. Both containers share the settings folder, so the helper writes the log and
   an exit marker there and the *new* API reports the result. The browser keeps
   polling ``/api/system/update`` and reconnects when the new container
   answers.

The API process only performs steps 1-2 and then starts the helper, so a failed
pull leaves the current container (and the user's session) intact.

Environment
-----------
CODX_JUNIOR_CONFIG_FOLDER        Settings folder (default ``~/.codx-junior``)
CODX_JUNIOR_UPDATE_PULL_TIMEOUT  Seconds allowed for the image pull (3600)
CODX_JUNIOR_UPDATE_LOG_LINES     Log tail returned by the status endpoint (200)
CODX_JUNIOR_UPDATE_STALE_SECONDS Age after which a running state is "unknown"
CODX_JUNIOR_DOCKER_SOCKET        Docker socket (default ``/var/run/docker.sock``)
CODX_JUNIOR_SELF_CONTAINER       Container id override (default: hostname)

"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from codx.junior import version
from codx.junior.infra.docker_client import (
    DEFAULT_SOCKET_PATH,
    DockerAPIError,
    DockerClient,
    DockerUnavailable,
)

logger = logging.getLogger(__name__)

DESCRIPTOR_NAME = "installer.json"
STATE_NAME = "update.json"
LOG_FOLDER = "logs"
HELPER_LABEL = "codx-junior.update"
EXIT_MARKER = "CODX_UPDATE_EXIT="
DEFAULT_LOG_LINES = int(os.environ.get("CODX_JUNIOR_UPDATE_LOG_LINES", "200"))
PULL_TIMEOUT = float(os.environ.get("CODX_JUNIOR_UPDATE_PULL_TIMEOUT", "3600"))
#: Timeout for the short daemon calls (inspect/create/start). The default 10s of
#: the infra client is tuned for UI views; creating a container can be slower on
#: a loaded host.
DOCKER_TIMEOUT = float(os.environ.get("CODX_JUNIOR_UPDATE_DOCKER_TIMEOUT", "120"))
STALE_SECONDS = float(os.environ.get("CODX_JUNIOR_UPDATE_STALE_SECONDS", "7200"))

LABEL_PROJECT = "com.docker.compose.project"
LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"

#: Statuses that mean "the container is about to be (or was) replaced".
ACTIVE_STATUSES = ("pulling", "updating")

IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_THREAD: Optional[threading.Thread] = None
#: What the active thread is doing ("update" or "check"). Both share one slot:
#: a check already performs the pull, so overlapping the two would only
#: duplicate registry traffic and race on the state file.
_ACTIVE_KIND: str = ""
_STATE_LOCK = threading.Lock()


# ── Errors ────────────────────────────────────────────────────────────────────


class UpdateError(Exception):
    """Base error for update operations (mapped to HTTP by the router)."""

    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UpdateUnsupportedError(UpdateError):
    """This instance cannot be updated from the UI (409)."""

    status_code = 409


class UpdateConflictError(UpdateError):
    """An update is already running (409)."""

    status_code = 409


class UpdateDockerError(UpdateError):
    """Docker/daemon/registry problem (503)."""

    status_code = 503


# ── Small helpers ─────────────────────────────────────────────────────────────


def _now() -> float:
    return time.time()


def _iso(timestamp: Optional[float]) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def settings_folder() -> str:
    """Return the folder that holds settings (and the update bookkeeping)."""
    configured = os.environ.get("CODX_JUNIOR_CONFIG_FOLDER", "").strip()
    if configured:
        return configured
    return os.path.join(os.path.expanduser("~"), ".codx-junior")


def _is_under(path: str, parent: str) -> bool:
    """True when ``path`` is ``parent`` or inside it (both absolute)."""
    try:
        return os.path.commonpath(
            [os.path.abspath(path), os.path.abspath(parent)]
        ) == os.path.abspath(parent)
    except (ValueError, OSError):
        return False


def _safe_home(home: str) -> str:
    """
    Validate an installation home taken from the descriptor or the labels.

    It becomes a bind-mount source in the helper container, so ``/`` (or a
    relative path) must never reach Docker: that would expose the whole host.
    """
    value = os.path.normpath((home or "").strip())
    if not value.startswith("/") or value == "/":
        raise UpdateUnsupportedError(
            f"Invalid installation home in the update descriptor: {home!r}"
        )
    return value


def _safe_instance(instance: str) -> str:
    """Reject an instance slug that could escape the paths it is used in."""
    value = (instance or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise UpdateUnsupportedError(
            f"Invalid instance name in the installation: {value!r}"
        )
    return value


# ── Installation descriptor (written by the installer) ───────────────────────


def descriptor_path(folder: Optional[str] = None) -> str:
    return os.path.join(folder or settings_folder(), DESCRIPTOR_NAME)


def read_descriptor(folder: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Read ``<settings>/installer.json`` (best effort).

    Returns:
        The descriptor when it is a JSON object with an absolute ``home``,
        ``None`` otherwise. A broken file is not fatal: discovery falls back to
        the container labels.
    """
    path = descriptor_path(folder)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("home") or "").startswith("/"):
        return None
    return data


@dataclass(frozen=True)
class SelfContainer:
    """What this process knows about the container it runs in."""

    id: str
    name: str
    image: str
    labels: Dict[str, str] = field(default_factory=dict)
    #: destination path -> host source path for the bind mounts.
    mounts: Dict[str, str] = field(default_factory=dict)

    def mount_source(self, destination: str) -> str:
        return self.mounts.get(destination, "")

    def source_for(self, path: str) -> str:
        """
        Translate a path inside the container to its host path.

        The settings folder is normally its own bind mount, but an installation
        home mounted as a whole also covers it; the deepest mount wins.

        Returns:
            The host path, or ``""`` when no mount covers ``path``.
        """
        best = None
        for destination, source in self.mounts.items():
            if _is_under(path, destination) and (
                best is None or len(destination) > len(best[0])
            ):
                best = (destination, source)
        if best is None:
            return ""
        relative = os.path.relpath(os.path.abspath(path), os.path.abspath(best[0]))
        if relative == ".":
            return best[1]
        return os.path.normpath(os.path.join(best[1], relative))


@dataclass(frozen=True)
class Installation:
    """The deployment the running API belongs to."""

    home: str
    instance: str
    image: str
    layout: str = "host"      # host | dind
    deployment: str = ""      # local | server | dind (informational)
    cli: str = ""             # <home>/bin/<name>
    installer: str = ""       # <home>/lib/install.sh
    env_file: str = ""
    data_dir: str = ""
    source: str = ""          # descriptor | labels


# ── Docker access ─────────────────────────────────────────────────────────────


def local_endpoint() -> str:
    """
    Return the local Unix-socket endpoint, or raise when the daemon is remote.

    Updating a *remote* host from here is not supported: the installer (and the
    paths it rewrites) lives on that host, not on the API machine.
    """
    endpoint = (os.environ.get("DOCKER_HOST") or "").strip()
    if not endpoint:
        socket_path = os.environ.get("CODX_JUNIOR_DOCKER_SOCKET", DEFAULT_SOCKET_PATH)
        endpoint = (
            socket_path if socket_path.startswith("unix://") else f"unix://{socket_path}"
        )
    if endpoint.startswith("unix://"):
        return endpoint
    if endpoint.startswith("/"):
        return f"unix://{endpoint}"
    raise UpdateUnsupportedError(
        "The API uses a remote Docker daemon "
        f"({endpoint}). Run the installer on that host to update it: "
        "`sudo <install-home>/bin/codx-junior update`."
    )


def socket_path(endpoint: str) -> str:
    """Return the filesystem path of a ``unix://`` endpoint."""
    return endpoint[len("unix://"):] if endpoint.startswith("unix://") else endpoint


def docker_client(timeout: Optional[float] = None) -> DockerClient:
    """Create a client for the **local** daemon (raises when it is remote)."""
    return DockerClient(endpoint=local_endpoint(), timeout=timeout or DOCKER_TIMEOUT)


def _container_name(data: Dict[str, Any]) -> str:
    return str(data.get("Name") or "").lstrip("/")


def inspect_self(docker: DockerClient) -> SelfContainer:
    """
    Inspect the container this API runs in.

    Raises:
        UpdateUnsupportedError: The container cannot be identified (custom
            hostname / no access to the daemon).
        UpdateDockerError: The daemon could not be queried.
    """
    candidates = [
        os.environ.get("CODX_JUNIOR_SELF_CONTAINER", "").strip(),
        os.environ.get("HOSTNAME", "").strip(),
        socket.gethostname(),
    ]
    seen = set()
    last_error: Optional[Exception] = None
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            data = docker.get_json(f"/containers/{quote(candidate, safe='')}/json")
        except DockerAPIError as ex:
            last_error = ex
            if ex.status_code == 404:
                continue
            raise UpdateDockerError(
                f"Cannot inspect this container: {ex.message}"
            ) from ex
        except DockerUnavailable as ex:
            raise UpdateDockerError(str(ex)) from ex
        labels = (data.get("Config") or {}).get("Labels") or {}
        image = str((data.get("Config") or {}).get("Image") or "")
        mounts = {}
        for mount in data.get("Mounts") or []:
            destination = mount.get("Destination") or ""
            source = mount.get("Source") or ""
            if destination and source:
                mounts[destination] = source
        return SelfContainer(
            id=str(data.get("Id") or candidate),
            name=_container_name(data),
            image=image,
            labels={str(key): str(value) for key, value in labels.items()},
            mounts=mounts,
        )
    detail = getattr(last_error, "message", "") or "not found"
    raise UpdateUnsupportedError(
        "The API cannot identify its own container "
        f"({detail}). In-app updates need the Docker socket and the default "
        "container hostname (the installer sets both)."
    )


# ── Discovery ─────────────────────────────────────────────────────────────────


def _cli_name(instance: str) -> str:
    return "codx-junior" if instance == "codx-junior" else f"codx-junior-{instance}"


def _split_config_files(raw: str) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def installation_from_descriptor(
    descriptor: Dict[str, Any], self_container: SelfContainer
) -> Installation:
    home = _safe_home(str(descriptor.get("home") or ""))
    instance = _safe_instance(str(descriptor.get("instance") or ""))
    return Installation(
        home=home,
        instance=instance,
        image=str(descriptor.get("image") or self_container.image or ""),
        layout="dind" if descriptor.get("container") else "host",
        deployment=str(descriptor.get("mode") or ""),
        cli=str(descriptor.get("cli") or os.path.join(home, "bin", _cli_name(instance))),
        installer=str(
            descriptor.get("installer") or os.path.join(home, "lib", "install.sh")
        ),
        env_file=str(descriptor.get("env_file") or ""),
        data_dir=str(descriptor.get("data_dir") or ""),
        source="descriptor",
    )


def installation_from_labels(self_container: SelfContainer) -> Installation:
    """
    Recover the installation from the compose labels (pre-descriptor installs).

    The installer runs compose with ``--project-directory <home>/lib/runtime``
    for a host installation, so ``working_dir`` gives the home. A DIND instance
    uses ``<home>`` itself as project directory.

    Raises:
        UpdateUnsupportedError: The container is not a compose deployment of a
            codx-junior installation.
    """
    labels = self_container.labels
    project = str(labels.get(LABEL_PROJECT) or "").strip()
    working_dir = str(labels.get(LABEL_WORKING_DIR) or "").strip()
    if not project or not working_dir.startswith("/"):
        raise UpdateUnsupportedError(
            "This instance was not started by codx-junior-installer (no compose "
            "labels), so there is no installation for the installer to update."
        )
    instance = _safe_instance(project)
    config_files = _split_config_files(labels.get(LABEL_CONFIG_FILES, ""))
    working_dir = working_dir.rstrip("/")
    if working_dir.endswith("/lib/runtime"):
        home = _safe_home(os.path.dirname(os.path.dirname(working_dir)))
        layout = "host"
    elif os.path.join(working_dir, "docker-compose.yaml") in config_files:
        home = _safe_home(working_dir)
        layout = "dind"
    else:
        raise UpdateUnsupportedError(
            "The compose project of this container does not look like a "
            "codx-junior installation (unexpected project directory)."
        )
    return Installation(
        home=home,
        instance=instance,
        image=self_container.image,
        layout=layout,
        cli=os.path.join(home, "bin", _cli_name(instance)),
        installer=os.path.join(home, "lib", "install.sh"),
        env_file=os.path.join(home, "config", f"{instance}.env"),
        data_dir=os.path.join(home, "data"),
        source="labels",
    )


def discover(docker: DockerClient) -> Tuple[Installation, SelfContainer]:
    """
    Return ``(installation, self_container)`` for the running API.

    Raises:
        UpdateUnsupportedError: The instance cannot be identified.
    """
    self_container = inspect_self(docker)
    descriptor = read_descriptor()
    if descriptor:
        installation = installation_from_descriptor(descriptor, self_container)
    else:
        installation = installation_from_labels(self_container)
    if not installation.image:
        raise UpdateUnsupportedError("The running image name could not be determined.")
    return installation, self_container


# ── Support / manual instructions ────────────────────────────────────────────


@dataclass(frozen=True)
class Support:
    supported: bool
    reason: str = ""
    manual_command: str = ""


def manual_command(installation: Optional[Installation] = None) -> str:
    """The host command that performs the same update by hand."""
    prefix = "" if os.geteuid() == 0 else "sudo "
    if installation is None:
        return "sudo <install-home>/bin/codx-junior update"
    return f"{prefix}{shlex.quote(installation.cli)} update"


def support(installation: Installation, self_container: SelfContainer) -> Support:
    """Decide whether this instance can be updated from the UI."""
    if installation.layout == "dind":
        return Support(
            False,
            "This is a Docker-in-Docker instance. Restart the DIND wrapper on the "
            "host (or re-run the installer's `update`, which recreates it): its "
            "entrypoint pulls the new app image and recreates the inner stack.",
            f"docker restart {installation.instance}",
        )
    if not os.path.isabs(settings_folder()):
        return Support(False, "The settings folder is not an absolute path.")
    if not self_container.source_for(settings_folder()):
        return Support(
            False,
            "The settings folder is not a bind mount of this container, so a "
            "helper container cannot share the update log/state with it.",
            manual_command(installation),
        )
    return Support(True)


# ── Image pull / inspection ──────────────────────────────────────────────────


def split_image(image: str) -> Tuple[str, str]:
    """
    Split an image reference into ``(repository, tag)``.

    >>> split_image("codxjuniorai/codx-junior:latest")
    ('codxjuniorai/codx-junior', 'latest')
    >>> split_image("localhost:5000/team/app")
    ('localhost:5000/team/app', 'latest')
    >>> split_image("repo@sha256:abc")
    ('repo@sha256:abc', '')
    """
    value = (image or "").strip()
    if not value:
        raise UpdateError("An image name is required.")
    if "@" in value:
        return value, ""
    last = value.rsplit("/", 1)[-1]
    if ":" in last:
        repository, tag = value.rsplit(":", 1)
        return repository, tag
    return value, "latest"


def validate_image(image: str) -> str:
    """Reject an image reference that is not safe to pass to the helper."""
    value = (image or "").strip()
    if not IMAGE_RE.match(value):
        raise UpdateError(f"Invalid image reference: {value!r}")
    return value


def image_id(docker: DockerClient, image: str) -> Optional[str]:
    """Return the local image id, or ``None`` when the image is not present."""
    try:
        data = docker.get_json(f"/images/{quote(image, safe='')}/json")
    except DockerAPIError as ex:
        if ex.status_code == 404:
            return None
        raise UpdateDockerError(f"Cannot inspect image {image}: {ex.message}") from ex
    except DockerUnavailable as ex:
        raise UpdateDockerError(str(ex)) from ex
    return str(data.get("Id") or "") or None


def image_build_info(docker: DockerClient, image: str) -> Dict[str, Any]:
    """
    Read the provenance a pulled image reports (version/ref/commit).

    The release workflow bakes ``CODX_JUNIOR_BUILD_*`` into the image
    environment, so the *downloaded* image tells the UI which version an update
    would install before anything is recreated. Images built without those
    arguments report ``unknown``; the commit/digest fallbacks keep the panel
    useful for them.

    Raises:
        UpdateDockerError: The image is not available locally.
    """
    try:
        data = docker.get_json(f"/images/{quote(image, safe='')}/json")
    except DockerAPIError as ex:
        raise UpdateDockerError(f"Cannot inspect image {image}: {ex.message}") from ex
    except DockerUnavailable as ex:
        raise UpdateDockerError(str(ex)) from ex

    env: Dict[str, str] = {}
    for item in (data.get("Config") or {}).get("Env") or []:
        key, _, value = str(item).partition("=")
        env[key] = value
    digests = data.get("RepoDigests") or []
    digest = str(digests[0]).split("@", 1)[-1] if digests else ""
    return {
        "image": image,
        "image_id": str(data.get("Id") or ""),
        "version": env.get("CODX_JUNIOR_BUILD_VERSION") or version.UNKNOWN,
        "ref": env.get("CODX_JUNIOR_BUILD_REF") or version.UNKNOWN,
        "commit": env.get("CODX_JUNIOR_BUILD_COMMIT") or version.UNKNOWN,
        "digest": digest,
        "created": str(data.get("Created") or ""),
    }


def pull_image(docker: DockerClient, image: str, timeout: Optional[float] = None) -> None:
    """
    Pull ``image`` through the Engine API.

    The daemon streams newline-delimited JSON progress; registry and auth
    failures arrive as ``error``/``errorDetail`` lines **inside** a 200
    response, so they are inspected explicitly.

    Raises:
        UpdateDockerError: The daemon, the registry or the credentials failed.
    """
    repository, tag = split_image(image)
    params = {"fromImage": repository}
    if tag:
        params["tag"] = tag
    client = DockerClient(endpoint=local_endpoint(), timeout=timeout or PULL_TIMEOUT)
    try:
        last_error = ""
        for event in client.post_stream_lines("/images/create", params=params):
            error = event.get("error")
            if error:
                last_error = str(error)
                continue
            detail = (event.get("errorDetail") or {}).get("message")
            if detail:
                last_error = str(detail)
        if last_error:
            raise UpdateDockerError(f"Image pull failed for {image}: {last_error}")
    finally:
        client.close()


# ── State / log bookkeeping ──────────────────────────────────────────────────


def state_path(folder: Optional[str] = None) -> str:
    return os.path.join(folder or settings_folder(), STATE_NAME)


def log_folder(folder: Optional[str] = None) -> str:
    return os.path.join(folder or settings_folder(), LOG_FOLDER)


def empty_state() -> Dict[str, Any]:
    return {
        "status": "idle",
        "message": "",
        "image": "",
        "from_image_id": "",
        "to_image_id": "",
        "helper_id": "",
        "log_file": "",
        "container_id": "",
        "started_at": 0.0,
        "finished_at": 0.0,
        "exit_code": None,
        "force": False,
        # Version check: what the registry has for the target image. It is
        # separate from ``status`` so checking for a new version never looks
        # like an update in progress.
        "latest": {},
        "check_status": "",
        "check_message": "",
        "checked_at": 0.0,
    }


def read_state(folder: Optional[str] = None) -> Dict[str, Any]:
    """Read the persisted state, always returning the full shape."""
    state = empty_state()
    try:
        with open(state_path(folder), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return state
    if isinstance(data, dict):
        state.update({key: data[key] for key in state if key in data})
    return state


def write_state(state: Dict[str, Any], folder: Optional[str] = None) -> None:
    """Persist the state atomically (both containers read it)."""
    folder = folder or settings_folder()
    path = state_path(folder)
    temporary = f"{path}.{os.getpid()}.tmp"
    payload = {**empty_state(), **(state or {})}
    payload["updated_at"] = _now()
    with _STATE_LOCK:
        try:
            os.makedirs(folder, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as ex:
            raise UpdateError(f"Cannot write the update state: {ex}") from ex


def log_file_for(folder: Optional[str] = None) -> str:
    """Return the (host-side) path of a fresh update log file."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return os.path.join(log_folder(folder), f"update-{stamp}.log")


def tail_log(path: str, lines: int = DEFAULT_LOG_LINES) -> str:
    """Return the last ``lines`` lines of ``path`` (empty when unreadable)."""
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 512 * 1024))
            data = handle.read()
    except OSError:
        return ""
    chunks = data.decode("utf-8", "replace").splitlines()
    return "\n".join(chunks[-max(1, lines):])


def parse_exit_code(log_path: str) -> Optional[int]:
    """
    Read the helper's ``CODX_UPDATE_EXIT=<n>`` marker from the log.

    Returns:
        The exit code, or ``None`` when the helper has not reported yet.
    """
    code: Optional[int] = None
    for line in tail_log(log_path, 200).splitlines():
        line = line.strip()
        if line.startswith(EXIT_MARKER):
            try:
                code = int(line[len(EXIT_MARKER):].strip())
            except ValueError:
                continue
    return code


# ── Helper container ─────────────────────────────────────────────────────────


def helper_script(
    installation: Installation,
    image: str = "",
    self_id: str = "",
) -> str:
    """
    Build the shell script the helper runs.

    The script validates that the installation home really was mounted (Docker
    silently creates missing bind sources, which would make the installer write
    into an empty directory), runs the installer's ``update`` and records the
    exit code in the shared log so the new API can report the result.

    The log path arrives in ``CODX_JUNIOR_LOG`` (the installer honours the same
    variable for its own output, so both streams land in one file).
    """
    install = shlex.quote(installation.installer)
    cli = shlex.quote(installation.cli)
    upgrade = f"bash {installation.cli} update --non-interactive"
    if image:
        upgrade += f" --image {image}"
    lines = [
        "set -u",
        'mkdir -p "$(dirname "$CODX_JUNIOR_LOG")"',
        "{",
        '  echo "== codx-junior self-update $(date -u +%Y-%m-%dT%H:%M:%SZ) =="',
        f'  echo "container: {self_id[:12]}"',
        f"  echo \"image: {image or installation.image}\"",
        '} >> "$CODX_JUNIOR_LOG"',
        f"if [ ! -f {install} ]; then",
        '  echo "ERROR: the installation home was not mounted: '
        f'{installation.installer} is missing" >> "$CODX_JUNIOR_LOG"',
        f'  echo "{EXIT_MARKER}2" >> "$CODX_JUNIOR_LOG"',
        "  exit 2",
        "fi",
        f"if [ -f {cli} ]; then",
        f"  UPDATE_CMD={shlex.quote(upgrade)}",
        "else",
    ]
    fallback = [
        "bash",
        installation.installer,
        "--home",
        installation.home,
        "--instance",
        installation.instance,
    ]
    if installation.env_file:
        fallback += ["--env-file", installation.env_file]
    fallback += ["update", "--non-interactive"]
    if image:
        fallback += ["--image", image]
    lines.append(
        f"  UPDATE_CMD={shlex.quote(' '.join(shlex.quote(part) for part in fallback))}"
    )
    lines += [
        "fi",
        'echo "+ $UPDATE_CMD" >> "$CODX_JUNIOR_LOG"',
        'eval "$UPDATE_CMD"',
        "rc=$?",
        'echo "== codx-junior self-update finished exit=$rc ==" >> "$CODX_JUNIOR_LOG"',
        f'echo "{EXIT_MARKER}$rc" >> "$CODX_JUNIOR_LOG"',
        "exit $rc",
    ]
    return "\n".join(lines)


def helper_binds(
    installation: Installation,
    self_container: SelfContainer,
    endpoint: str,
) -> List[str]:
    """Bind mounts the helper needs to update this installation."""
    socket_file = socket_path(endpoint)
    binds = [f"{socket_file}:{socket_file}"]
    binds.append(f"{installation.home}:{installation.home}")
    settings = self_container.source_for(settings_folder())
    if settings and not _is_under(settings, installation.home):
        binds.append(f"{settings}:{settings}")
    return binds


def start_helper(
    docker: DockerClient,
    installation: Installation,
    self_container: SelfContainer,
    *,
    image: str,
    log_path: str,
) -> str:
    """
    Create and start the detached helper container.

    Returns:
        The helper container id.

    Raises:
        UpdateDockerError: The daemon rejected the helper.
    """
    endpoint = local_endpoint()
    name = f"{installation.instance}-updater-{int(time.time())}"
    body = {
        "Image": image,
        "Cmd": ["bash", "-ec", helper_script(installation, image, self_container.id)],
        "Env": [
            f"CODX_JUNIOR_LOG={log_path}",
            "HOME=/root",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ],
        "Labels": {
            HELPER_LABEL: "1",
            "codx-junior.update.container": self_container.id[:12],
            "codx-junior.update.instance": installation.instance,
        },
        "HostConfig": {
            "Binds": helper_binds(installation, self_container, endpoint),
            # Host networking keeps the installer's health probe on
            # 127.0.0.1:<port> meaningful (local mode publishes there, server
            # mode has Traefik on the host).
            "NetworkMode": "host",
            "AutoRemove": True,
            "RestartPolicy": {"Name": "no"},
        },
    }
    helper_id = ""
    try:
        created = docker.post("/containers/create", params={"name": name}, json_body=body)
        helper_id = str(created.get("Id") or "")
        if not helper_id:
            raise UpdateDockerError("The daemon did not return the updater container id.")
        docker.post(f"/containers/{quote(helper_id, safe='')}/start")
    except (DockerAPIError, DockerUnavailable, UpdateDockerError) as ex:
        # A created-but-not-started helper is dead weight; remove it. A helper
        # that did start is left alone (it may be mid-update).
        if helper_id:
            try:
                docker.delete(f"/containers/{quote(helper_id, safe='')}", params={"force": "1"})
            except (DockerAPIError, DockerUnavailable):
                logger.warning("Could not remove the failed updater container %s", helper_id[:12])
        message = getattr(ex, "message", None) or str(getattr(ex, "detail", "") or ex)
        raise UpdateDockerError(f"Cannot start the updater container: {message}") from ex
    return helper_id


def helper_exists(docker: DockerClient, helper_id: str) -> bool:
    """True while the helper container still exists (it is auto-removed)."""
    if not helper_id:
        return False
    try:
        docker.get_json(f"/containers/{quote(helper_id, safe='')}/json")
    except DockerAPIError as ex:
        if ex.status_code == 404:
            return False
        raise UpdateDockerError(
            f"Cannot inspect the updater container: {ex.message}"
        ) from ex
    except DockerUnavailable as ex:
        raise UpdateDockerError(str(ex)) from ex
    return True


# ── Orchestration ────────────────────────────────────────────────────────────


def run_update(
    *,
    folder: Optional[str] = None,
    image: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Pull the target image and, when it changed, start the helper.

    Runs in a background thread: pulling a release image can take minutes and
    the HTTP request must not hold the browser. Every step is recorded in the
    shared state file, so the UI can follow it and the *new* container can
    report the result after the restart.

    Returns:
        The final state (``up_to_date`` or ``updating``).

    Raises:
        UpdateError: Any discovery/pull/daemon failure (state is ``failed``).
    """
    folder = folder or settings_folder()
    state = empty_state()
    docker = None
    try:
        docker = docker_client()
        installation, self_container = discover(docker)
        decision = support(installation, self_container)
        if not decision.supported:
            raise UpdateUnsupportedError(decision.reason)
        target = validate_image(image or installation.image)
        state.update(
            {
                "status": "pulling",
                "image": target,
                "container_id": self_container.id,
                "started_at": _now(),
                "force": bool(force),
            }
        )
        write_state(state, folder)

        before = image_id(docker, target)
        pull_image(docker, target)
        after = image_id(docker, target)
        if not after:
            raise UpdateDockerError(
                f"The pulled image {target} is not available locally."
            )
        if before == after and not force:
            state.update(
                {
                    "status": "up_to_date",
                    "message": f"{target} is already up to date ({after[:12]}).",
                    "to_image_id": after,
                    "finished_at": _now(),
                }
            )
            write_state(state, folder)
            logger.info("Update check: %s is already current (%s)", target, after[:12])
            return state

        # The container path is what this API reads; the helper writes through
        # the host path of the same folder (they are the same directory).
        log_path = log_file_for(folder)
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
        except OSError as ex:
            raise UpdateDockerError(f"Cannot create the update log folder: {ex}") from ex
        settings_source = self_container.source_for(folder)
        if not settings_source:
            raise UpdateUnsupportedError(
                "The settings folder is not a bind mount of this container."
            )
        helper_log_path = os.path.join(
            settings_source, LOG_FOLDER, os.path.basename(log_path)
        )

        state.update(
            {
                "from_image_id": before or "",
                "to_image_id": after,
                "log_file": os.path.relpath(log_path, folder),
            }
        )
        # Persist the log location *before* the helper starts: the container can
        # be replaced before the helper id is written back.
        write_state(state, folder)

        helper_id = start_helper(
            docker,
            installation,
            self_container,
            image=target,
            log_path=helper_log_path,
        )
        state.update(
            {
                "status": "updating",
                "helper_id": helper_id,
                "message": "The installer is pulling and recreating the container.",
            }
        )
        write_state(state, folder)
        logger.info(
            "Update started: instance=%s image=%s helper=%s",
            installation.instance,
            target,
            helper_id[:12],
        )
        return state
    except Exception as ex:
        state.update(
            {
                "status": "failed",
                "message": str(getattr(ex, "detail", "") or ex),
                "finished_at": _now(),
            }
        )
        try:
            write_state(state, folder)
        except UpdateError:
            logger.exception("Cannot persist the failed update state")
        raise
    finally:
        if docker is not None:
            docker.close()


def start_update(
    *,
    folder: Optional[str] = None,
    image: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Start :func:`run_update` in a daemon thread and return the initial state.

    Raises:
        UpdateConflictError: Another update or version check is running.
    """
    global _ACTIVE_THREAD, _ACTIVE_KIND
    with _ACTIVE_LOCK:
        if _ACTIVE_THREAD is not None and _ACTIVE_THREAD.is_alive():
            if _ACTIVE_KIND == "check":
                raise UpdateConflictError("A version check is already running.")
            raise UpdateConflictError("An update is already running.")
        current = read_state(folder)
        if current.get("status") in ACTIVE_STATUSES and not _is_stale(current):
            raise UpdateConflictError("An update is already running.")

        # Fail fast with a readable 409 instead of a "failed" state the user has
        # to expand to understand (DIND, remote daemon, no settings mount).
        probe = docker_client()
        try:
            installation, self_container = discover(probe)
            decision = support(installation, self_container)
        finally:
            probe.close()
        if not decision.supported:
            raise UpdateUnsupportedError(decision.reason)
        target = validate_image(image or installation.image)

        state = {
            **empty_state(),
            "status": "pulling",
            "image": target,
            "started_at": _now(),
            "force": bool(force),
        }
        write_state(state, folder)
        thread = threading.Thread(
            target=_run_update_thread,
            kwargs={"folder": folder, "image": image, "force": force},
            name="codx-junior-update",
            daemon=True,
        )
        _ACTIVE_THREAD = thread
        _ACTIVE_KIND = "update"
        thread.start()
    return read_state(folder)


def run_check(*, folder: Optional[str] = None, image: str = "") -> Dict[str, Any]:
    """
    Pull the target image without recreating the container and record what it
    contains (version/ref/commit), so the UI can show the version an update
    would install.

    A pull when the image is already current is cheap (the daemon only checks
    the layers), which makes this safe to run when the Updates tab opens.

    Returns:
        The persisted state, including ``latest`` and ``check_status``.

    Raises:
        UpdateError: Discovery/daemon/registry failure (state is ``failed``).
    """
    folder = folder or settings_folder()
    state = read_state(folder)
    docker = None
    try:
        docker = docker_client()
        installation, _self_container = discover(docker)
        target = validate_image(image or installation.image)
        before = image_id(docker, target)
        pull_image(docker, target)
        after = image_id(docker, target)
        if not after:
            raise UpdateDockerError(
                f"The pulled image {target} is not available locally."
            )
        latest = image_build_info(docker, target)
        latest["available"] = before != after
        state.update(
            {
                "latest": latest,
                "check_status": "done",
                "check_message": (
                    "" if latest["available"] else f"{target} is up to date."
                ),
                "checked_at": _now(),
            }
        )
        logger.info(
            "Version check: %s available=%s (%s)",
            target,
            latest["available"],
            after[:12],
        )
        return state
    except UpdateError as ex:
        state.update(
            {
                "check_status": "failed",
                "check_message": ex.detail,
                "checked_at": _now(),
            }
        )
        raise
    finally:
        if docker is not None:
            docker.close()
        try:
            write_state(state, folder)
        except UpdateError:
            logger.exception("Cannot persist the version check state")


def start_check(*, folder: Optional[str] = None, image: str = "") -> Dict[str, Any]:
    """
    Start :func:`run_check` in a daemon thread and return the initial state.

    Raises:
        UpdateConflictError: An update or another check is already running.
    """
    global _ACTIVE_THREAD, _ACTIVE_KIND
    with _ACTIVE_LOCK:
        if _ACTIVE_THREAD is not None and _ACTIVE_THREAD.is_alive():
            if _ACTIVE_KIND == "update":
                raise UpdateConflictError("An update is already running.")
            raise UpdateConflictError("A version check is already running.")
        state = read_state(folder)
        state.update(
            {
                "check_status": "checking",
                "check_message": "",
                "checked_at": _now(),
            }
        )
        write_state(state, folder)
        thread = threading.Thread(
            target=_run_check_thread,
            kwargs={"folder": folder, "image": image},
            name="codx-junior-update-check",
            daemon=True,
        )
        _ACTIVE_THREAD = thread
        _ACTIVE_KIND = "check"
        thread.start()
    return read_state(folder)


def _run_check_thread(**kwargs) -> None:
    try:
        run_check(**kwargs)
    except Exception:  # noqa: BLE001 - the state file carries the message
        logger.exception("codx-junior version check failed")


def _active_kind() -> str:
    """Return ``"update"``/``"check"`` while that operation runs, else ``""``."""
    thread = _ACTIVE_THREAD
    if thread is not None and thread.is_alive():
        return _ACTIVE_KIND
    return ""


def _run_update_thread(**kwargs) -> None:
    try:
        run_update(**kwargs)
    except Exception:  # noqa: BLE001 - the state file carries the message
        logger.exception("codx-junior update failed")


def _is_stale(state: Dict[str, Any]) -> bool:
    started = float(state.get("started_at") or 0)
    return bool(started) and (_now() - started) > STALE_SECONDS


def describe_state(
    docker: Optional[DockerClient] = None, folder: Optional[str] = None
) -> Dict[str, Any]:
    """
    Return the persisted state with its *derived* status.

    A ``pulling``/``updating`` state is only "running" while its helper exists
    or the process is still working. After the container restart the helper is
    either still running (the installer waits for health) or already gone, in
    which case the exit marker in the log is the result.
    """
    folder = folder or settings_folder()
    state = read_state(folder)
    log_path = (
        os.path.join(folder, str(state.get("log_file")))
        if state.get("log_file")
        else ""
    )
    state["log"] = tail_log(log_path)
    state["log_path"] = log_path
    state["started_at_iso"] = _iso(state.get("started_at"))
    state["finished_at_iso"] = _iso(state.get("finished_at"))

    status = str(state.get("status") or "idle")
    if status in ACTIVE_STATUSES:
        exit_code = parse_exit_code(log_path) if log_path else None
        if exit_code is not None:
            state["exit_code"] = exit_code
            state["finished_at"] = state.get("finished_at") or _now()
            if exit_code == 0:
                status = "completed"
                state["message"] = "The new container is healthy."
            else:
                status = "failed"
                state["message"] = state.get("message") or (
                    f"The installer exited with code {exit_code}."
                )
            state["finished_at_iso"] = _iso(state.get("finished_at"))
        elif docker is not None and helper_exists(
            docker, str(state.get("helper_id") or "")
        ):
            status = "updating"
        elif _is_stale(state):
            status = "unknown"
            state["message"] = state.get("message") or (
                "The update did not report a result (the container may have been "
                "restarted while it ran)."
            )
        else:
            # The pull runs in this process; the helper is about to exist.
            status = "updating"
    state["status"] = status
    return state


# ── Status snapshot used by the API ──────────────────────────────────────────


def _check_summary(folder: Optional[str] = None) -> Dict[str, Any]:
    """Latest version check persisted in the state file, with a live status."""
    state = read_state(folder)
    latest = state.get("latest")
    if not isinstance(latest, dict):
        latest = {}
    active = _active_kind()
    status = "checking" if active == "check" else str(state.get("check_status") or "idle")
    message = str(state.get("check_message") or "")
    if status == "checking" and active != "check":
        # The process restarted (or the thread died) before the check reported:
        # never leave the UI spinning forever.
        status = "idle"
        message = ""
    checked_at = float(state.get("checked_at") or 0)
    return {
        "status": status,
        "message": message,
        "checked_at": checked_at,
        "checked_at_iso": _iso(checked_at),
        "latest": latest,
    }


def snapshot() -> Dict[str, Any]:
    """
    Build the ``GET /api/system/update`` payload.

    Discovery/support problems are reported instead of raised so the settings
    page can render the manual instructions.
    """
    check = _check_summary()
    payload: Dict[str, Any] = {
        "supported": False,
        "reason": "",
        "manual_command": manual_command(),
        "current": {**version.build_info()},
        "installation": {},
        "update": describe_state(),
        "latest": check["latest"],
        "check": {key: value for key, value in check.items() if key != "latest"},
    }
    try:
        docker = docker_client()
    except UpdateUnsupportedError as ex:
        payload["reason"] = ex.detail
        return payload
    except (DockerUnavailable, OSError) as ex:
        payload["reason"] = f"The Docker daemon is not reachable: {ex}"
        return payload
    try:
        installation, self_container = discover(docker)
        decision = support(installation, self_container)
        payload["supported"] = decision.supported
        payload["reason"] = decision.reason
        payload["manual_command"] = decision.manual_command or manual_command(installation)
        payload["current"].update(
            {
                "image": installation.image,
                "instance": installation.instance,
                "mode": installation.deployment or installation.layout,
                "layout": installation.layout,
                "container_id": self_container.id,
                "container_name": self_container.name,
                "installer_home": installation.home,
            }
        )
        payload["installation"] = {
            "home": installation.home,
            "instance": installation.instance,
            "image": installation.image,
            "layout": installation.layout,
            "deployment": installation.deployment,
            "source": installation.source,
        }
        state = describe_state(docker)
        state["helper_running"] = bool(
            state.get("helper_id") and helper_exists(docker, str(state["helper_id"]))
        )
        payload["update"] = state
        return payload
    except UpdateDockerError as ex:
        payload["reason"] = ex.detail
        payload["manual_command"] = manual_command()
        return payload
    except UpdateUnsupportedError as ex:
        payload["reason"] = ex.detail
        return payload
    finally:
        docker.close()


# Made with ❤️ by codx-junior
