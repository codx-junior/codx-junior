"""
Docker Hub management tools.

Registry-side companion of ``docker_tools``: the docker tools build and run
images locally, these ones publish them to Docker Hub and drive the repository
catalogue (tags, digests, deletions) through the Docker Hub API v2.

Configuration is environment driven, exactly like ``scripts/dockerhub_publish.sh``:

    DOCKERHUB_NAMESPACE   organisation/user (default: codxjuniorai)
    DOCKERHUB_USER        registry user (default: the namespace)
    DOCKERHUB_TOKEN       access token; required for anything that writes
    DOCKERHUB_REGISTRY    registry host (default: docker.io)

Read-only calls work anonymously for public repositories; pushing and deleting
always require the token, which is never echoed in a response.

Made with <3 by codx-junior
"""

import base64
import json
import logging
import os
import re
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .model import ToolResponse

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "codxjuniorai"
HUB_API = "https://hub.docker.com/v2"
PUBLISH_SCRIPT = "scripts/dockerhub_publish.sh"
_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def _namespace(namespace: Optional[str] = None) -> str:
    return (namespace or os.environ.get("DOCKERHUB_NAMESPACE") or DEFAULT_NAMESPACE).strip()


def _user() -> str:
    return (os.environ.get("DOCKERHUB_USER") or _namespace()).strip()


def _token() -> str:
    return os.environ.get("DOCKERHUB_TOKEN", "").strip()


def _auth_header() -> Optional[str]:
    """Basic auth header for the Docker Hub API, or None when anonymous."""
    token = _token()
    if not token:
        return None
    raw = f"{_user()}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _validate(value: str, field: str, pattern: str = r"^[a-zA-Z0-9_\-\.]+$") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    value = value.strip()
    if not re.match(pattern, value):
        raise ValueError(f"invalid {field}: {value!r}")
    return value


def _api(
    path: str,
    method: str = "GET",
    body: Optional[Dict] = None,
    query: Optional[Dict] = None,
) -> Dict:
    """Call the Docker Hub API and return the decoded JSON payload."""
    url = f"{HUB_API}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    authorization = _auth_header()
    if authorization:
        headers["Authorization"] = authorization

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Docker Hub API {method} {path} -> HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Docker Hub API unreachable: {exc.reason}")

    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload}


def _project_dir(settings) -> str:
    for attribute in ("project_path", "project_dir", "codx_path"):
        value = getattr(settings, attribute, None)
        if value and os.path.isdir(str(value)):
            return str(value)
    return os.getcwd()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def dockerhub_whoami(namespace: Optional[str] = None, **kwargs) -> ToolResponse:
    """
    Report how the Docker Hub integration is configured and whether the token works.

    Checks the namespace, the login user, whether a token is present (it is
    never printed) and whether the token is accepted by the registry API.

    Args:
        namespace: Docker Hub namespace to inspect (default: ``DOCKERHUB_NAMESPACE``).
        **kwargs: settings / user injected by the engine.

    Returns:
        ToolResponse: configuration summary plus the token status.
    """
    try:
        target = _namespace(namespace)
        has_token = bool(_token())
        lines = [
            f"namespace: {target}",
            f"user: {_user()}",
            f"token: {'present' if has_token else 'missing'}",
            f"hub: https://hub.docker.com/u/{target}",
        ]
        status = "anonymous"
        if has_token:
            try:
                _api("user/")
                status = "token accepted"
            except RuntimeError as exc:
                status = f"token rejected ({exc})"
        lines.append(f"auth: {status}")
        return ToolResponse(
            user_response="Docker Hub configuration\n" + "\n".join(f"- {line}" for line in lines),
            llm_response="Docker Hub configuration:\n" + "\n".join(lines),
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        logger.exception("dockerhub_whoami failed")
        return ToolResponse(
            user_response=f"Docker Hub check failed: {exc}",
            llm_response=f"dockerhub_whoami error: {exc}",
        )


def dockerhub_publish(
    tags: Optional[List[str]] = None,
    image_name: Optional[str] = None,
    namespace: Optional[str] = None,
    platforms: Optional[str] = None,
    dockerfile: Optional[str] = None,
    context: Optional[str] = None,
    build_args: Optional[Dict[str, str]] = None,
    push: bool = True,
    no_cache: bool = False,
    dry_run: bool = False,
    timeout: int = 1800,
    **kwargs,
) -> ToolResponse:
    """
    Build the project image and publish it to Docker Hub.

    Runs ``scripts/dockerhub_publish.sh`` with the environment variables it
    expects, so the AI path and the CI path publish exactly the same way.

    Args:
        tags: Tags to push for the same build (e.g. ``["1.4.0", "latest"]``).
        image_name: Repository name (default: ``IMAGE_NAME`` or ``codx-junior``).
        namespace: Docker Hub organisation (default: ``DOCKERHUB_NAMESPACE``).
        platforms: buildx platforms (default: ``linux/amd64,linux/arm64``).
        dockerfile: Dockerfile path relative to the project.
        context: Build context relative to the project.
        build_args: ``--build-arg`` pairs.
        push: Push to the registry (requires ``DOCKERHUB_TOKEN``).
        no_cache: Disable the build cache.
        dry_run: Only print the docker commands.
        timeout: Seconds before the build is killed (60-3600).
        **kwargs: settings / user injected by the engine.

    Returns:
        ToolResponse: the command, its output tail and the published references.
    """
    settings = kwargs.get("settings")
    try:
        if not isinstance(timeout, int) or timeout < 60 or timeout > 3600:
            raise ValueError("timeout must be an integer between 60-3600 seconds")

        project_dir = _project_dir(settings)
        script = os.path.join(project_dir, PUBLISH_SCRIPT)
        if not os.path.isfile(script):
            raise ValueError(f"publish script not found: {PUBLISH_SCRIPT}")

        target_namespace = _validate(_namespace(namespace), "namespace")
        repository = _validate(
            image_name or os.environ.get("IMAGE_NAME") or "codx-junior", "image_name"
        )
        tag_list = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if not tag_list:
            tag_list = [os.environ.get("IMAGE_TAG", "latest")]

        if push and not _token():
            raise ValueError("DOCKERHUB_TOKEN is required to push (or set push=False)")

        env = dict(os.environ)
        env.update(
            {
                "DOCKERHUB_NAMESPACE": target_namespace,
                "DOCKERHUB_USER": _user(),
                "IMAGE_NAME": repository,
                "IMAGE_TAGS": ",".join(tag_list),
                "PUSH": "true" if push else "false",
                "NO_CACHE": "true" if no_cache else "false",
                "DRY_RUN": "true" if dry_run else "false",
            }
        )
        if platforms:
            env["PLATFORMS"] = str(platforms)
        if dockerfile:
            env["DOCKERFILE"] = str(dockerfile)
        if context:
            env["CONTEXT"] = str(context)
        if build_args:
            env["BUILD_ARGS"] = " ".join(
                f"{key}={value}" for key, value in build_args.items()
            )

        command = ["bash", script]
        logger.info("Publishing %s/%s tags=%s", target_namespace, repository, tag_list)
        completed = subprocess.run(  # noqa: S603 - fixed script, no shell
            command,
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        refs = [f"{target_namespace}/{repository}:{tag}" for tag in tag_list]
        tail = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()[-4000:]
        if completed.returncode != 0:
            return ToolResponse(
                user_response=(
                    f"Publish failed for {', '.join(refs)} "
                    f"(exit {completed.returncode})\n\n```\n{tail}\n```"
                ),
                llm_response=(
                    f"`{' '.join(shlex.quote(part) for part in command)}` exited "
                    f"{completed.returncode}.\nOutput:\n{tail}"
                ),
            )

        return ToolResponse(
            user_response=(
                f"Published {', '.join(refs)}\n\n"
                f"https://hub.docker.com/r/{target_namespace}/{repository}/tags\n\n"
                f"```\n{tail[-1500:]}\n```"
            ),
            llm_response=f"Published: {', '.join(refs)}\nOutput tail:\n{tail}",
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        logger.exception("dockerhub_publish failed")
        return ToolResponse(
            user_response=f"Publish failed: {exc}",
            llm_response=f"dockerhub_publish error: {exc}",
        )


def dockerhub_tags(
    repository: str,
    namespace: Optional[str] = None,
    page_size: int = 25,
    name_filter: Optional[str] = None,
    **kwargs,
) -> ToolResponse:
    """
    List the tags published for one repository, with digests and last update.

    Args:
        repository: Repository name (e.g. ``codx-junior``).
        namespace: Docker Hub organisation (default: ``DOCKERHUB_NAMESPACE``).
        page_size: Tags per page (1-100).
        name_filter: Only keep tags whose name contains this text.
        **kwargs: settings / user injected by the engine.

    Returns:
        ToolResponse: table of tags with last pushed date and digest prefix.
    """
    try:
        target = _validate(_namespace(namespace), "namespace")
        repo = _validate(repository, "repository")
        page_size = max(1, min(int(page_size), 100))

        payload = _api(
            f"repositories/{target}/{repo}/tags",
            query={"page_size": page_size, "ordering": "last_updated"},
        )
        results = payload.get("results") or []
        if name_filter:
            needle = name_filter.lower()
            results = [tag for tag in results if needle in str(tag.get("name", "")).lower()]

        if not results:
            return ToolResponse(
                user_response=f"No tags found for {target}/{repo}.",
                llm_response=f"No tags for {target}/{repo} (filter={name_filter!r}).",
            )

        rows = []
        for tag in results:
            digest = str(tag.get("digest") or "")
            rows.append(
                {
                    "name": tag.get("name"),
                    "digest": digest[:19],
                    "size_mb": round((tag.get("full_size") or 0) / (1024 * 1024), 1),
                    "updated": str(tag.get("last_updated") or "")[:19],
                    "architectures": ",".join(
                        sorted(
                            {
                                image.get("architecture", "")
                                for image in (tag.get("images") or [])
                                if image.get("architecture")
                            }
                        )
                    ),
                }
            )

        table = "\n".join(
            f"| {row['name']} | {row['updated']} | {row['size_mb']} MB | "
            f"{row['architectures'] or '-'} | `{row['digest']}` |"
            for row in rows
        )
        return ToolResponse(
            user_response=(
                f"Tags for {target}/{repo}\n\n"
                "| tag | updated | size | arch | digest |\n|---|---|---|---|---|\n" + table
            ),
            llm_response=(
                f"Tags for {target}/{repo}: "
                + json.dumps([row["name"] for row in rows])
                + f"\n\n{table}"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        logger.exception("dockerhub_tags failed")
        return ToolResponse(
            user_response=f"Could not list tags: {exc}",
            llm_response=f"dockerhub_tags error: {exc}",
        )


def dockerhub_repositories(
    namespace: Optional[str] = None,
    page_size: int = 25,
    **kwargs,
) -> ToolResponse:
    """
    List the repositories of the Docker Hub organisation.

    Args:
        namespace: Docker Hub organisation (default: ``DOCKERHUB_NAMESPACE``).
        page_size: Repositories per page (1-100).
        **kwargs: settings / user injected by the engine.

    Returns:
        ToolResponse: repositories with pull count, last updated and visibility.
    """
    try:
        target = _validate(_namespace(namespace) if namespace else _user(), "namespace")
        page_size = max(1, min(int(page_size), 100))

        payload = _api(
            f"repositories/{target}/",
            query={"page_size": page_size, "ordering": "last_updated"},
        )
        results = payload.get("results") or []
        if not results:
            return ToolResponse(
                user_response=f"No repositories visible for {target}.",
                llm_response=f"No repositories for {target}.",
            )

        rows = []
        for repo in results:
            rows.append(
                {
                    "name": repo.get("name"),
                    "private": bool(repo.get("is_private")),
                    "pulls": repo.get("pull_count", 0),
                    "updated": str(repo.get("last_updated") or "")[:19],
                }
            )
        table = "\n".join(
            f"| {row['name']} | {'private' if row['private'] else 'public'} | "
            f"{row['pulls']} | {row['updated']} |"
            for row in rows
        )
        return ToolResponse(
            user_response=(
                f"Repositories in {target}\n\n"
                "| repository | visibility | pulls | updated |\n|---|---|---|---|\n" + table
            ),
            llm_response=f"Repositories in {target}:\n{table}",
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        logger.exception("dockerhub_repositories failed")
        return ToolResponse(
            user_response=f"Could not list repositories: {exc}",
            llm_response=f"dockerhub_repositories error: {exc}",
        )


def dockerhub_delete_tag(
    repository: str,
    tag: str,
    namespace: Optional[str] = None,
    confirm: bool = False,
    **kwargs,
) -> ToolResponse:
    """
    Delete one published tag from Docker Hub (destructive, requires confirm).

    Args:
        repository: Repository name (e.g. ``codx-junior``).
        tag: Tag to delete (``latest`` is refused: retag first).
        namespace: Docker Hub organisation (default: ``DOCKERHUB_NAMESPACE``).
        confirm: Must be True, otherwise nothing is deleted.
        **kwargs: settings / user injected by the engine.

    Returns:
        ToolResponse: the deletion outcome.
    """
    try:
        if not confirm:
            raise ValueError("confirm=True is required to delete a published tag")
        if not _token():
            raise ValueError("DOCKERHUB_TOKEN is required to delete a tag")
        if str(tag).strip().lower() in ("latest", "stable"):
            raise ValueError(f"refusing to delete the floating tag {tag!r}")

        target = _validate(_namespace(namespace), "namespace")
        repo = _validate(repository, "repository")
        clean_tag = _validate(tag, "tag", r"^[a-zA-Z0-9_\-.]+$")

        _api(f"repositories/{target}/{repo}/tags/{clean_tag}/", method="DELETE")
        return ToolResponse(
            user_response=f"Deleted {target}/{repo}:{clean_tag} from Docker Hub.",
            llm_response=f"Deleted tag {clean_tag} of {target}/{repo}.",
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        logger.exception("dockerhub_delete_tag failed")
        return ToolResponse(
            user_response=f"Could not delete tag: {exc}",
            llm_response=f"dockerhub_delete_tag error: {exc}",
        )


# Tool JSON definitions

DOCKERHUB_WHOAMI_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "dockerhub_whoami",
        "description": (
            "Report the Docker Hub configuration (namespace, user, token present) "
            "and whether DOCKERHUB_TOKEN is accepted by the registry API."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Docker Hub namespace (default: DOCKERHUB_NAMESPACE)."
                }
            },
            "required": []
        }
    }
}

DOCKERHUB_PUBLISH_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "dockerhub_publish",
        "description": (
            "Build the project image and publish it to Docker Hub by running "
            "scripts/dockerhub_publish.sh with the configured environment "
            "(DOCKERHUB_NAMESPACE, DOCKERHUB_TOKEN, PLATFORMS...)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags pushed for the same build, e.g. ['1.4.0','latest']."
                },
                "image_name": {
                    "type": "string",
                    "description": "Repository name (default: codx-junior)."
                },
                "namespace": {
                    "type": "string",
                    "description": "Docker Hub organisation (default: codxjuniorai)."
                },
                "platforms": {
                    "type": "string",
                    "description": "buildx platforms (default: linux/amd64,linux/arm64)."
                },
                "dockerfile": {
                    "type": "string",
                    "description": "Dockerfile path relative to the project."
                },
                "context": {
                    "type": "string",
                    "description": "Build context relative to the project."
                },
                "build_args": {
                    "type": "object",
                    "description": "--build-arg key/value pairs."
                },
                "push": {
                    "type": "boolean",
                    "description": "Push to the registry (requires DOCKERHUB_TOKEN).",
                    "default": True
                },
                "no_cache": {
                    "type": "boolean",
                    "description": "Disable the build cache.",
                    "default": False
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Only print the docker commands.",
                    "default": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before the build is killed (60-3600).",
                    "default": 1800
                }
            },
            "required": []
        }
    }
}

DOCKERHUB_TAGS_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "dockerhub_tags",
        "description": (
            "List the tags published for a Docker Hub repository with digest, "
            "size, architectures and last update."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Repository name (e.g. codx-junior)."
                },
                "namespace": {
                    "type": "string",
                    "description": "Docker Hub organisation (default: codxjuniorai)."
                },
                "page_size": {
                    "type": "integer",
                    "description": "Tags per page (1-100, default 25).",
                    "default": 25
                },
                "name_filter": {
                    "type": "string",
                    "description": "Only keep tags containing this text."
                }
            },
            "required": ["repository"]
        }
    }
}

DOCKERHUB_REPOSITORIES_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "dockerhub_repositories",
        "description": (
            "List the repositories of the Docker Hub organisation with "
            "visibility, pull count and last update."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Docker Hub organisation (default: DOCKERHUB_NAMESPACE)."
                },
                "page_size": {
                    "type": "integer",
                    "description": "Repositories per page (1-100, default 25).",
                    "default": 25
                }
            },
            "required": []
        }
    }
}

DOCKERHUB_DELETE_TAG_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "dockerhub_delete_tag",
        "description": (
            "Delete one published tag from Docker Hub. Requires confirm=true and "
            "DOCKERHUB_TOKEN; floating tags like latest are refused."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Repository name (e.g. codx-junior)."
                },
                "tag": {
                    "type": "string",
                    "description": "Tag to delete."
                },
                "namespace": {
                    "type": "string",
                    "description": "Docker Hub organisation (default: codxjuniorai)."
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to actually delete.",
                    "default": False
                }
            },
            "required": ["repository", "tag", "confirm"]
        }
    }
}

DOCKERHUB_TOOLS = {
    "dockerhub_whoami": dockerhub_whoami,
    "dockerhub_publish": dockerhub_publish,
    "dockerhub_tags": dockerhub_tags,
    "dockerhub_repositories": dockerhub_repositories,
    "dockerhub_delete_tag": dockerhub_delete_tag,
}

__all__ = list(DOCKERHUB_TOOLS.keys()) + [
    "DOCKERHUB_TOOLS",
    "DOCKERHUB_WHOAMI_TOOL_JSON",
    "DOCKERHUB_PUBLISH_TOOL_JSON",
    "DOCKERHUB_TAGS_TOOL_JSON",
    "DOCKERHUB_REPOSITORIES_TOOL_JSON",
    "DOCKERHUB_DELETE_TAG_TOOL_JSON",
]
