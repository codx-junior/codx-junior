"""
Build provenance of the running API — what ``GET /api/health`` reports.

An unattended deployment is verified from *outside* the box: the provisioning
pipeline polls ``/api/health`` and has to assert that the build it asked for is
the build that answered. Without a version/ref in the payload the probe can only
assert "something returned HTML", which cannot tell a successful upgrade from a
stale container or from a rollback that never happened.

The values are baked into the image at build time (see
``codx-junior-installer/demo/Dockerfile`` and ``.github/workflows/release.yml``)
and can be overridden from the environment, so the same binary reports the same
thing under ``docker run -e ...``.

Every key is always present: the shape of the readiness probe is part of the
contract, documented in the installer's ``cloud/`` folder.
"""

import functools
import json
import os
import subprocess

BUILD_INFO_PATH = os.environ.get("CODX_JUNIOR_BUILD_INFO", "/etc/codx-junior/build-info.json")

# Directories probed for a checkout when the image carries no build descriptor
# (docker compose up on a clone, a dev run, a self-hosted install from source).
GIT_PATH_CANDIDATES = [
    path for path in (os.environ.get("CODX_JUNIOR_PATH"), os.getcwd()) if path
]

GIT_TIMEOUT_SECONDS = 3

UNKNOWN = "unknown"


def _file_info() -> dict:
    """Read the build descriptor written at image build time (best effort)."""
    try:
        with open(BUILD_INFO_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _git(*args: str, path: str) -> str:
    """Run one read-only git command, returning "" when it is not applicable."""
    try:
        result = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@functools.lru_cache(maxsize=1)
def _git_info() -> dict:
    """Read the checkout the app runs from — the local / self-hosted case.

    An image built without the ``CODX_JUNIOR_BUILD_*`` args has no descriptor,
    and reporting "unknown" forever makes the readiness probe useless. Reading
    the checkout keeps it honest. Cached: the probe is polled every few seconds
    and must not fork git on every request.
    """
    for path in GIT_PATH_CANDIDATES:
        commit = _git("rev-parse", "HEAD", path=path)
        if not commit:
            continue
        describe = _git("describe", "--tags", "--always", path=path)
        return {
            "version": describe or UNKNOWN,
            "ref": describe or UNKNOWN,
            "commit": commit,
        }
    return {}


def build_info() -> dict:
    """
    Return ``{"version", "ref", "commit"}`` — never raises, always complete.

    Precedence: environment > build descriptor baked into the image > the
    checkout the app runs from > "unknown". Environment wins so a deployment
    can report the ref it was installed from even when the image was built once
    for a whole release line.
    """
    info = _file_info()
    git = _git_info()

    def value(env_name: str, key: str) -> str:
        return str(os.environ.get(env_name) or info.get(key) or git.get(key) or UNKNOWN)

    return {
        "version": value("CODX_JUNIOR_BUILD_VERSION", "version"),
        "ref": value("CODX_JUNIOR_BUILD_REF", "ref"),
        "commit": value("CODX_JUNIOR_BUILD_COMMIT", "commit"),
    }
