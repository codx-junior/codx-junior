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
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from .model import ToolResponse

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "codxjuniorai"
HUB_API = "https://hub.docker.com/v2"
# Registry auth service: the endpoint `docker login` and `docker push` talk to.
AUTH_SERVICE = "https://auth.docker.io/token"
REGISTRY_SERVICE = "registry.docker.io"
PUBLISH_SCRIPT = "scripts/dockerhub_publish.sh"
TOKEN_NAME = "DOCKERHUB_TOKEN"
_TIMEOUT = 30
# A value pasted into Settings > Secrets sometimes keeps a few trailing
# characters from the clipboard or from the token screen. They are dropped only
# when the registry authenticates the shortened value (never blindly).
_STRIPPED_SUFFIX = 4


class HubError(RuntimeError):
    """A Docker Hub API failure.

    Deliberately a ``RuntimeError``: every existing caller catches that, so the
    finer subclasses below add information without changing old behaviour.
    """


class HubAuthError(HubError):
    """The value was sent and the registry refused it (HTTP 401/403)."""


class HubNetworkError(HubError):
    """The API could not be reached, so the value was never judged."""


class HubApiError(HubError):
    """The API answered with a status that is neither auth nor reachability."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def _namespace(namespace: Optional[str] = None) -> str:
    return (namespace or os.environ.get("DOCKERHUB_NAMESPACE") or DEFAULT_NAMESPACE).strip()


def _user() -> str:
    return (os.environ.get("DOCKERHUB_USER") or _namespace()).strip()


_ACTIVE_SETTINGS = None
_TOKEN_CACHE: Dict[str, str] = {}
# Why the vault could not provide the value (never the value itself): lets the
# caller tell "nothing stored" apart from "stored but unreadable".
_VAULT_ERROR: Dict[str, str] = {}


def _note_vault_error(error) -> None:
    """Remember the vault verdict for this lookup ("missing" means absent)."""
    _VAULT_ERROR.clear()
    code = str(getattr(error, "code", "") or "")
    if code and code != "missing":
        _VAULT_ERROR["code"] = code
        _VAULT_ERROR["detail"] = str(getattr(error, "detail", "") or code)


def _bind(settings) -> None:
    """Remember the settings of the running tool so _token() can reach the vault."""
    global _ACTIVE_SETTINGS
    if settings is not None and settings is not _ACTIVE_SETTINGS:
        _ACTIVE_SETTINGS = settings
        _TOKEN_CACHE.clear()


def _vault_token(settings=None) -> str:
    """Read DOCKERHUB_TOKEN from the project secret vault (value never logged).

    The policy is optional: a tool invoked outside a turn (or before the project
    resolved its CISO policy) still gets the vault default policy, so a missing
    policy degrades the source of the token, it does not lose it.
    """
    target = settings or _ACTIVE_SETTINGS
    policy = None
    try:
        from codx.junior.security.ciso.policy import resolve_policy

        policy = resolve_policy(target)
    except Exception:  # noqa: BLE001 - the vault ships a usable default policy
        logger.debug("CISO policy not resolvable for the vault", exc_info=True)
    try:
        from codx.junior.security.ciso import vault as secret_vault

        project_dir = _project_dir(target)
        try:
            vault = secret_vault.SecretVault(project_dir, policy)
        except TypeError:  # older signature without a policy argument
            vault = secret_vault.SecretVault(project_dir)
        # The source name must be the vault's own constant ("secret"). Passing
        # a literal "vault" made get_value() answer invalid_handle, so a value
        # stored in Settings -> Secrets silently read as "not configured".
        value, error = vault.get_value(secret_vault.SOURCE_VAULT, TOKEN_NAME)
        _note_vault_error(error)
        if error is not None:
            logger.debug("DOCKERHUB_TOKEN unavailable from the vault: %s", error.code)
            return ""
        return (value or "").strip()
    except Exception:  # noqa: BLE001 - the vault is optional for this tool
        logger.debug("DOCKERHUB_TOKEN not resolvable from the vault", exc_info=True)
        return ""


def _normalise_token(raw: str) -> str:
    """Return *raw* as the registry expects it: no wrappers, no invisible junk.

    The value travels through a clipboard, a JSON field and a password box
    before it reaches the vault, and each step can leave something behind
    (quotes, a ``Bearer`` prefix, CR/LF, a zero width character).
    """
    value = (raw or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    value = re.sub("[\u200b-\u200d\ufeff\x00\r\n]", "", value)
    return value.strip("\"'`<>,;")


def _token_variants(value: str) -> List[str]:
    """Candidate values for *value*, the stored one first.

    The shortened candidate exists because a value can arrive with trailing
    characters that no ``strip()`` removes (a suffix glued on by the UI or the
    clipboard). It is never used on a guess: the caller adopts it only when the
    registry authenticates it, so a healthy token is never truncated.
    """
    candidates = [value]
    if len(value) > _STRIPPED_SUFFIX + 8:
        shortened = value[:-_STRIPPED_SUFFIX]
        if shortened and shortened not in candidates:
            candidates.append(shortened)
    return candidates


def _token_with_origin() -> Tuple[str, str]:
    """Return ``(value, origin)`` where origin is ``env``, ``vault`` or ``""``."""
    value = _normalise_token(os.environ.get(TOKEN_NAME, ""))
    if value:
        _VAULT_ERROR.clear()
        return value, "env"
    if "vault" in _TOKEN_CACHE:
        return _TOKEN_CACHE["vault"], "vault"
    found = _normalise_token(_vault_token())
    if found:
        # Only a hit is cached. A miss must be re-read on the next call: a
        # secret added in Settings right after a failed attempt has to become
        # visible without restarting the API.
        _TOKEN_CACHE["vault"] = found
    return found, ("vault" if found else "")


def _token() -> str:
    """Token from the environment, falling back to the project secret vault.

    A value the registry accepted in a shortened form wins: ``_registry_verdict``
    caches it here, so every caller (API headers and the publish script) sends
    the same, working value.
    """
    return _TOKEN_CACHE.get("resolved") or _token_with_origin()[0]


def _registry_verdict() -> Tuple[str, str]:
    """Judge the value with the registry auth service (what `docker login` uses).

    ``hub.docker.com`` and the registry do not accept the same credentials: a
    token scoped to an organization or to a single repository is refused by the
    Docker Hub *API* (``/user/``) but authenticates normally for ``docker push``.
    Declaring such a value invalid blocks publishing for the wrong reason, so the
    registry exchange is the authoritative check before blaming the token.

    Returns:
        Tuple[str, str]: ``("ok" | "refused" | "unknown", detail)``. ``unknown``
        means nobody judged the value (network problem), so it must not block.
    """
    value = _token()
    if not value:
        return "unknown", "no value is configured"
    verdict, detail = _registry_check(value)
    if verdict != "refused":
        return verdict, detail
    # Refused as stored: try the value without the trailing characters a paste
    # or the token screen can leave behind. Only the variant the registry
    # authenticates is adopted, so a healthy value is never truncated.
    for candidate in _token_variants(value)[1:]:
        candidate_verdict, candidate_detail = _registry_check(candidate)
        if candidate_verdict == "unknown":
            break
        if candidate_verdict == "ok":
            logger.warning(
                "DOCKERHUB_TOKEN: refused as stored and accepted without its last "
                "%s character(s); using the accepted variant.", _STRIPPED_SUFFIX
            )
            _TOKEN_CACHE["resolved"] = candidate
            return "ok", (
                f"{candidate_detail}; the value is refused while it keeps its last "
                f"{_STRIPPED_SUFFIX} character(s)"
            )
    return verdict, detail


def _registry_check(value: str) -> Tuple[str, str]:
    """Judge one *value* against the registry auth service (the `docker login` path)."""
    if not value:
        return "unknown", "no value is configured"
    repository = f"{_namespace()}/codx-junior"
    query = urllib.parse.urlencode({
        "service": REGISTRY_SERVICE,
        "scope": f"repository:{repository}:pull,push",
    })
    credentials = base64.b64encode(f"{_user()}:{value}".encode()).decode()
    request = urllib.request.Request(
        f"{AUTH_SERVICE}?{query}",
        headers={"Authorization": f"Basic {credentials}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "refused", f"registry auth -> HTTP {exc.code} for {repository}"
        return "unknown", f"registry auth -> HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - a network problem never blames the value
        logger.warning("registry auth check skipped: %s", exc)
        return "unknown", f"registry auth unreachable: {exc}"
    if payload.get("token") or payload.get("access_token"):
        return "ok", "accepted by the registry auth service (docker login compatible)"
    return "refused", "the registry auth service returned no token"


def _diagnose_token() -> Dict[str, str]:
    """Return the state of the Docker Hub value, never the value itself.

    The distinctions matter because the operator has to do something different
    in each case: nothing configured (store a secret) is not the same as a
    value the registry refused (rotate it), and neither is the same as a hub
    that could not be reached (nobody's fault).

    Returns:
        Dict[str, str]: ``present`` (``true``/``false``), ``origin``
        (``env``/``vault``/``""``), ``verdict`` (``missing``, ``valid``,
        ``invalid``, ``unreachable``, ``unverified``) and ``detail``.
    """
    value, origin = _token_with_origin()
    if not value:
        if _VAULT_ERROR.get("code"):
            # Stored but unreadable (no key, wrong key): NOT the same problem as
            # "nobody configured it", and not fixable by storing the value again
            # until the key is restored.
            return {
                "present": "true",
                "origin": "vault",
                "verdict": "unreadable",
                "detail": _VAULT_ERROR.get("detail") or _VAULT_ERROR.get("code", ""),
            }
        return {
            "present": "false",
            "origin": "",
            "verdict": "missing",
            "detail": "no value is configured in the environment or the vault",
        }
    state = {"present": "true", "origin": origin or "unknown", "detail": ""}
    try:
        _api("user/")
    except HubAuthError as exc:
        registry_verdict, registry_detail = _registry_verdict()
        if registry_verdict == "ok":
            state.update({"verdict": "valid", "detail": registry_detail})
        elif registry_verdict == "refused":
            state.update({"verdict": "invalid", "detail": f"{exc}. {registry_detail}"})
        else:
            state.update({"verdict": "unverified", "detail": f"{exc}. {registry_detail}"})
    except HubNetworkError as exc:
        state.update({"verdict": "unreachable", "detail": str(exc)})
    except HubError as exc:
        state.update({"verdict": "unverified", "detail": str(exc)})
    else:
        state.update({"verdict": "valid", "detail": "accepted by the Docker Hub API"})
    return state


def _require_token(action: str) -> Optional[str]:
    """Explain, value-free, why *action* cannot authenticate.

    Args:
        action: What the caller is about to do ("publishing", "deleting ...").

    Returns:
        Optional[str]: The message to report, or None when the call may run.
    """
    state = _diagnose_token()
    verdict = state["verdict"]
    if verdict == "valid":
        return None
    if verdict in ("unreachable", "unverified"):
        # hub.docker.com is not the registry endpoint: an unreachable API does
        # not prove the value is wrong, so the preflight stays advisory.
        logger.warning("Docker Hub API not reachable, skipping the preflight: %s", state["detail"])
        return None
    if verdict == "unreadable":
        return (
            f"{TOKEN_NAME} is stored but the secret vault cannot read it "
            f"({state['detail']}). The deployment key (CODX_JUNIOR_VAULT_KEY or the "
            "project's vault key file) is missing or belongs to another key than the "
            "one the value was written with. Storing the value again does not help "
            "until the key is restored: the operator has to re-add it once the key "
            "is in place."
        )
    if verdict == "missing":
        return (
            f"{TOKEN_NAME} is not configured, so {action} cannot authenticate. "
            f"Store it in Settings > Secrets as {TOKEN_NAME} (scope: global or this "
            "project, allowed tools: dockerhub_*), or export it in the API "
            "environment. Nothing was sent to Docker Hub; dry_run=true previews "
            "the build without it."
        )
    return (
        f"{TOKEN_NAME} is configured (from the {state['origin']}) but Docker Hub "
        f"rejected it: {state['detail']}. The value is expired, revoked, or belongs "
        f"to another account than DOCKERHUB_USER ({_user()}). Rotate it out of band "
        f"(Settings > Secrets) or with `docker login`; the value is never printed."
    )


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
        message = f"Docker Hub API {method} {path} -> HTTP {exc.code}: {detail}"
        if exc.code in (401, 403):
            raise HubAuthError(message) from None
        raise HubApiError(message) from None
    except urllib.error.URLError as exc:
        raise HubNetworkError(f"Docker Hub API unreachable: {exc.reason}") from None

    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload}


def _project_dir(settings) -> str:
    """Return the project root that actually holds PUBLISH_SCRIPT.

    ``codx_path`` points at the control plane (``<project>/.codx``) and the API
    process runs with ``<project>/api`` as cwd, so neither is the repo root the
    publish script lives in: every candidate is checked against the script, and
    the repo root is derived from this file as a last resort.
    """
    candidates: List[str] = []
    for attribute in ("project_path", "project_dir", "codx_path"):
        value = getattr(settings, attribute, None)
        if not value:
            continue
        path = os.path.abspath(str(value))
        candidates += [path, os.path.dirname(path)]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here)))))
    candidates.append(os.getcwd())

    for path in candidates:
        if os.path.isfile(os.path.join(path, PUBLISH_SCRIPT)):
            return path
    for path in candidates:
        if os.path.isdir(path):
            return path
    return os.getcwd()


def _publish_log_path(project_dir: str) -> str:
    """Return the per-run log file that captures one publish (build + push).

    The file is written under ``<project>/.codx/logs`` so a long build can be
    followed while it runs and inspected after the tool call returned.
    """
    log_dir = os.path.join(project_dir, ".codx", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:  # pragma: no cover - read-only project, fall back to /tmp
        log_dir = os.path.join("/tmp", "codx-junior-logs")
        os.makedirs(log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(log_dir, f"dockerhub_publish-{stamp}.log")


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
        state = _diagnose_token()
        has_token = state["present"] == "true"
        lines = [
            f"namespace: {target}",
            f"user: {_user()}",
            f"token: {'present' if has_token else 'missing'}"
            + (f" ({state['origin']})" if state["origin"] else ""),
            f"hub: https://hub.docker.com/u/{target}",
        ]
        statuses = {
            "missing": "anonymous (nothing configured)",
            "valid": "token accepted",
            "invalid": f"token rejected ({state['detail']})",
            "unreadable": f"stored but the vault cannot read it ({state['detail']})",
            "unreachable": f"not checked ({state['detail']})",
            "unverified": f"not verified ({state['detail']})",
        }
        lines.append(f"auth: {statuses.get(state['verdict'], state['verdict'])}")
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
    timeout: Optional[int] = None,
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
        timeout: Optional hard limit in seconds. Omitted, ``0`` or a negative
            value means **no timeout**, so a cold multi-arch publish is allowed
            to run to completion. A positive value must be at least 60.
        **kwargs: settings / user injected by the engine.

    Returns:
        ToolResponse: the command, its output tail and the published references.
    """
    settings = kwargs.get("settings")
    _bind(settings)
    try:
        # A publish can legitimately take hours (cold cache, multi-arch), so the
        # timeout is OPTIONAL: None/0/negative waits as long as the build needs.
        if timeout is None:
            limit = 0
        else:
            if not isinstance(timeout, int):
                raise ValueError("timeout must be an integer or omitted")
            if 0 < timeout < 60:
                raise ValueError(
                    "timeout must be 0 (no limit) or at least 60 seconds"
                )
            limit = timeout if timeout > 0 else 0

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

        if push and not dry_run:
            # A dry run only prints the commands, so it must not require the
            # value at all (that wrongly failed `dry_run=true` before).
            token_problem = _require_token("publishing")
            if token_problem:
                return ToolResponse(
                    user_response=f"Cannot publish: {token_problem}",
                    llm_response=(
                        f"dockerhub_publish aborted before the build: {token_problem} "
                        "Nothing was pushed and nothing was sent to Docker Hub."
                    ),
                )

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
        token = _token()
        if token:
            # The value normally comes from the vault, so it is NOT in
            # os.environ: without this line the child script saw an empty
            # DOCKERHUB_TOKEN and silently skipped `docker login`.
            env[TOKEN_NAME] = token

        command = ["bash", script]
        refs = [f"{target_namespace}/{repository}:{tag}" for tag in tag_list]
        log_path = _publish_log_path(project_dir)
        logger.info(
            "Publishing %s/%s tags=%s limit=%s log=%s",
            target_namespace,
            repository,
            tag_list,
            limit or "none",
            log_path,
        )
        # The child writes straight to its own log file instead of a pipe, so a
        # long publish keeps making progress (and stays readable) even after the
        # tool call is abandoned by the agent loop.
        with open(log_path, "w", encoding="utf-8") as log_file:
            process = subprocess.Popen(  # noqa: S603 - fixed script, no shell
                command,
                cwd=project_dir,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                returncode = process.wait(timeout=limit or None)
            except subprocess.TimeoutExpired:
                return ToolResponse(
                    user_response=(
                        f"Publish still running after {limit}s: "
                        f"{', '.join(refs)}\n\n"
                        "The build was NOT killed. "
                        f"Follow it in `{log_path}`."
                    ),
                    llm_response=(
                        f"dockerhub_publish is still running after {limit}s; "
                        f"pid {process.pid}, log {log_path}. Nothing failed: "
                        "re-run with timeout=0 (or omit it) for no limit."
                    ),
                )

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                tail = log_file.read().strip()[-4000:]
        except OSError:  # pragma: no cover - the log always exists here
            tail = ""
        if returncode != 0:
            return ToolResponse(
                user_response=(
                    f"Publish failed for {', '.join(refs)} "
                    f"(exit {returncode})\n\n```\n{tail}\n```"
                ),
                llm_response=(
                    f"`{' '.join(shlex.quote(part) for part in command)}` exited "
                    f"{returncode}.\nOutput:\n{tail}"
                ),
            )

        headline = "Dry run (nothing pushed)" if dry_run else "Published"
        notes = ""
        if dry_run:
            value, origin = _token_with_origin()
            notes = (
                "\n\nDry run: nothing was built, pushed or logged in. "
                f"{TOKEN_NAME}: {'present' if value else 'not configured'}"
                + (f" ({origin})" if origin else " (not needed for a dry run)")
            )
        return ToolResponse(
            user_response=(
                f"{headline}: {', '.join(refs)}\n\n"
                f"https://hub.docker.com/r/{target_namespace}/{repository}/tags\n\n"
                f"```\n{tail[-1500:]}\n```{notes}"
            ),
            llm_response=f"{headline}: {', '.join(refs)}\nOutput tail:\n{tail}{notes}",
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
        token_problem = _require_token("deleting a published tag")
        if token_problem:
            raise ValueError(token_problem)
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
                    "description": (
                        "Optional hard limit in seconds. Omit it (or send 0) "
                        "for no limit, so a long publish runs to completion."
                    )
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
