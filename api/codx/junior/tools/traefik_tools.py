"""
Traefik tools for DevOps support in codx-junior.

codx-junior leans on Traefik for almost everything: the SPA, the API, the auth
middleware, the workspace apps, the model manager and every custom route. The
routing table itself is *generated* by codx-junior and published through the
Traefik HTTP provider (``GET /api/traefik/config``), so when "the app is not
reachable" the answer usually lives in one of three places:

* the routing table codx-junior builds (:mod:`codx.junior.traefik.manager`),
* the Traefik container itself (state, command line, restarts and logs — the
  access logs are written to stdout, JSON encoded),
* the static configuration file mounted in the platform
  (``/traefik/traefik.yaml`` in the installer layout).

:func:`traefik` exposes those three sources through a single read-only tool, so
a DevOps profile can diagnose a route without reading the source code, opening
the dashboard or asking the user for a screenshot.

Execution model:
    - **Read only.** No action changes routes, containers or files.
    - Docker commands run with a fixed argument list and never through a shell,
      so no user input can reach a shell. ``search`` is a plain text filter
      applied in Python.
    - Every result is bounded (routes, log lines, config characters) so a large
      installation cannot flood the model context.

Tool Scope: "chat" (grant it to the DevOps profiles)

Made with ❤️ by codx-junior
"""

import json
import logging
import os
import re
import subprocess
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from codx.junior.settings import CODXJuniorSettings
from codx.junior.model.model import CodxUser
from codx.junior.traefik import manager as traefik_manager
from .model import ToolResponse

# Configure logging
logger = logging.getLogger(__name__)

# Container holding Traefik. The installer names it ``traefik-codx-junior`` and
# a custom deployment can point the tool anywhere with the env var.
CONTAINER_ENV = "CODX_JUNIOR_TRAEFIK_CONTAINER"
DEFAULT_CONTAINER = "traefik-codx-junior"

# Static configuration file. The API container mounts ``./traefik`` at
# ``/traefik`` in the installer layout; the other paths cover a compose stack
# that mounts the traefik folder somewhere else.
CONFIG_PATH_ENV = "CODX_JUNIOR_TRAEFIK_CONFIG"
STATIC_CONFIG_PATHS = (
    "/traefik/traefik.yaml",
    "/config/traefik.yaml",
    "/etc/traefik/traefik.yaml",
)

ACTIONS = ("status", "routes", "config", "logs")
CONFIG_SOURCES = ("generated", "file")

MAX_ROUTES = 40
MAX_ISSUES = 15
MAX_LOG_LINES = 200
MAX_LOG_CHARS = 20000
MAX_CONFIG_CHARS = 20000
MAX_SEARCH_CHARS = 200

# Values that must never reach the model when a configuration file is shown.
SECRET_LINE_RE = re.compile(
    r"(?i)^(\s*[\w.\-\"']*(?:password|passwd|secret|token|api[_-]?key|apikey)\b[^:\n]*:\s*).*$",
    re.MULTILINE,
)

# Credentials Traefik keeps in the access log when the config sets
# ``fields.headers.defaultMode: keep`` (the installer does). They must never
# reach the model context.
LOG_SECRET_RE = re.compile(
    r'(?i)("[^"]*(?:auth|authorization|authentication|cookie|token|secret|api[_-]?key)[^"]*"\s*:\s*")([^"]*)(")'
)
BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-]+")
# Access logs are JSON when ``accessLog.format=json``; these markers let the
# Access logs are JSON when ``accessLog.format=json``; these markers let the
# tool highlight the lines a human would look for first.
NOISY_LOG_KEY_RE = re.compile(
    r'"(?:request_|downstream_|origin_)[^"]*":"(?:[^"\\]|\\.)*",?'
)
FAILURE_MARKERS = (
    "level=error",
    '"level":"error"',
    "err=",
    " 404 ",
    " 500 ",
    " 502 ",
    " 503 ",
    " 504 ",
    '"downstreamstatus":404',
    '"downstreamstatus":500',
    '"downstreamstatus":502',
    '"downstreamstatus":503',
    '"downstreamstatus":504',
)


class TraefikCommandResult(NamedTuple):
    """Outcome of a Docker subprocess execution."""

    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        """Return True when the command exited with status 0."""
        return self.returncode == 0


# ---------------------------------------------------------------------------
# Docker access (read only)
# ---------------------------------------------------------------------------


def _docker(args: List[str], timeout: int = 20) -> TraefikCommandResult:
    """
    Run one ``docker`` command with a fixed argument list.

    Args:
        args: Arguments after the ``docker`` binary (no shell evaluation).
        timeout: Command timeout in seconds.

    Returns:
        TraefikCommandResult: Captured streams and exit code.
    """
    command = ["docker"] + [str(arg) for arg in args]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return TraefikCommandResult(
            "", f"`{' '.join(command)}` timed out after {timeout}s", 124
        )
    except FileNotFoundError:
        return TraefikCommandResult(
            "", "The docker CLI is not available in this container", 127
        )
    except Exception as ex:  # pragma: no cover - defensive
        return TraefikCommandResult("", f"Failed to run docker: {ex}", 1)
    return TraefikCommandResult(
        result.stdout or "", result.stderr or "", result.returncode
    )


def _candidate_containers() -> List[str]:
    """
    Return the container names to probe, most specific first.

    ``CODX_JUNIOR_TRAEFIK_CONTAINER`` wins; otherwise the Docker daemon is
    asked for a container whose name or image contains ``traefik`` and the
    installer's default name is used as the last resort.
    """
    configured = (os.environ.get(CONTAINER_ENV) or "").strip()
    if configured:
        return [configured]

    names: List[str] = []
    result = _docker(["ps", "-a", "--format", "{{.Names}}\t{{.Image}}"])
    for line in (result.stdout or "").splitlines():
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 2:
            continue
        name, image = parts[0], parts[1]
        if "traefik" in name.lower() or "traefik" in image.lower():
            names.append(name)
    if DEFAULT_CONTAINER not in names:
        names.append(DEFAULT_CONTAINER)
    return names


def _inspect(name: str) -> Optional[Dict[str, Any]]:
    """Return the ``docker inspect`` payload of one container, or None."""
    result = _docker(["inspect", name])
    if not result.ok or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        logger.warning("docker inspect %s returned invalid JSON", name)
        return None
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def _resolve_container() -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return ``(name, inspect_payload)`` of the Traefik container, or ``(None, None)``."""
    for name in _candidate_containers():
        info = _inspect(name)
        if info is not None:
            return (info.get("Name") or name).lstrip("/") or name, info
    return None, None


def _state_summary(info: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a ``docker inspect`` payload to the fields the report shows."""
    state = info.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    config = info.get("Config") or {}
    labels = config.get("Labels") or {}
    env = [
        item for item in (config.get("Env") or [])
        if str(item).upper().startswith("TRAEFIK")
    ]
    return {
        "name": (info.get("Name") or "").lstrip("/"),
        "image": config.get("Image") or "",
        "status": state.get("Status") or "unknown",
        "health": health or "-",
        "started_at": state.get("StartedAt") or "",
        "restarts": info.get("RestartCount"),
        "command": [str(arg) for arg in (config.get("Cmd") or [])],
        "env": env,
        "labels": {key: value for key, value in labels.items() if key.startswith("traefik.")},
        "mounts": [
            f"{mount.get('Source') or ''} -> {mount.get('Destination') or ''}"
            for mount in (info.get("Mounts") or [])
        ],
    }


def _command_facts(command: List[str]) -> Dict[str, Any]:
    """Extract the provider endpoints, config file and entrypoints from ``Cmd``."""
    facts: Dict[str, Any] = {"configFile": "", "providers": [], "entrypoints": []}
    for arg in command or []:
        if arg.startswith("--configFile="):
            facts["configFile"] = arg.split("=", 1)[1]
        elif arg.startswith("--providers.") and "endpoint" in arg:
            facts["providers"].append(arg.split("=", 1)[-1])
        elif arg.startswith("--entryPoints.") and ".address" in arg:
            facts["entrypoints"].append(arg.split("=", 1)[-1])
    return facts


# ---------------------------------------------------------------------------
# Configuration files
# ---------------------------------------------------------------------------


def _config_candidates(mounts: List[str]) -> List[str]:
    """Return the static-config paths to try, derived from env, defaults and mounts."""
    paths: List[str] = []
    configured = (os.environ.get(CONFIG_PATH_ENV) or "").strip()
    if configured:
        paths.append(configured)
    paths.extend(STATIC_CONFIG_PATHS)
    for mount in mounts or []:
        destination = mount.split("->")[-1].strip()
        if not destination:
            continue
        base = destination.rstrip("/")
        for name in ("traefik.yaml", "traefik.yml"):
            candidate = f"{base}/{name}"
            if candidate not in paths:
                paths.append(candidate)
    return paths


def _read_text(path: str, limit: int = MAX_CONFIG_CHARS) -> Tuple[Optional[str], str]:
    """
    Read a text file, bounded.

    Returns:
        Tuple ``(text, error)``. ``text`` is None when the file cannot be read.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(limit + 1)
    except OSError as ex:
        return None, str(ex)
    if len(text) > limit:
        text = text[:limit] + "\n… (truncated)"
    return text, ""


def _redact(text: str) -> str:
    """Mask the value of secret-looking YAML/JSON keys."""
    return SECRET_LINE_RE.sub(r"\1***", text or "")


def _redact_log_line(line: str, limit: int = 2000) -> str:
    """
    Prepare one log line for the model context.

    Traefik keeps every request/response header when the access log uses
    ``fields.headers.defaultMode: keep`` (the installer does), so a raw line is
    both huge and full of credentials. The header dump is dropped, the
    remaining secret-looking values are masked and the length is bounded.
    """
    text = line or ""
    if text.lstrip().startswith("{"):
        text = NOISY_LOG_KEY_RE.sub("", text)
    text = LOG_SECRET_RE.sub(r"\1***\3", text)
    text = BEARER_RE.sub(r"\1***", text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _bounded_log_lines(lines: List[str]) -> List[str]:
    """Keep the newest lines that fit in the character budget."""
    kept: List[str] = []
    total = 0
    for line in reversed(lines):
        total += len(line) + 1
        if total > MAX_LOG_CHARS:
            break
        kept.append(line)
    kept.reverse()
    return kept


def _static_config(mounts: List[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Find and read the Traefik static configuration.

    Returns:
        Tuple ``(path, text, tried_paths)``; ``path``/``text`` are None when no
        candidate could be read.
    """
    tried = _config_candidates(mounts)
    for path in tried:
        text, _ = _read_text(path)
        if text is not None:
            return path, text, tried
    return None, None, tried


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    """Render a markdown table, escaping pipes and newlines in the cells."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        cells = [
            str(cell).replace("|", "\\|").replace("\n", " ").strip()
            for cell in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _project_id(settings: Optional[CODXJuniorSettings]) -> Optional[str]:
    """Return the project id of the current settings, if any."""
    return getattr(settings, "project_id", None) or None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _status_report(settings: Optional[CODXJuniorSettings]) -> str:
    """Container state, effective command line and route statistics."""
    name, info = _resolve_container()
    lines = ["# Traefik", ""]

    if info is None:
        lines.append(
            "⚠️ No Traefik container found on this Docker daemon. Set "
            f"`{CONTAINER_ENV}` to the container name when Traefik runs with a "
            "different one."
        )
        summary: Dict[str, Any] = {}
        static_path, static_text = None, None
        facts: Dict[str, Any] = {}
    else:
        summary = _state_summary(info)
        facts = _command_facts(summary["command"])
        static_path, static_text, _tried = _static_config(summary["mounts"])
        status = f"**{summary['status']}**"
        if summary["health"] != "-":
            status += f" ({summary['health']})"
        lines.append(
            _markdown_table(
                ["Property", "Value"],
                [
                    ["Container", f"`{summary['name']}`"],
                    ["Image", f"`{summary['image']}`"],
                    ["Status", status],
                    ["Restarts", str(summary["restarts"] if summary["restarts"] is not None else "-")],
                    ["Started", summary["started_at"] or "-"],
                    [
                        "Static config",
                        f"`{static_path}`" if static_path else "not readable from this container (use `config` with `source=file` after mounting it)",
                    ],
                    ["Config file (command)", f"`{facts['configFile']}`" if facts.get("configFile") else "-"],
                    ["HTTP provider", "<br>".join(f"`{p}`" for p in facts.get("providers") or []) or "-"],
                    ["Entrypoints", "<br>".join(f"`{e}`" for e in facts.get("entrypoints") or []) or "-"],
                    ["Dashboard", "`/traefik` (`api@internal`, protected by the auth middleware)"],
                ],
            )
        )

    try:
        view = traefik_manager.build_view(project_id=_project_id(settings))
        stats = view.get("stats") or {}
        lines += [
            "",
            (
                "**Routes published by codx-junior**: "
                f"{stats.get('total', 0)} total · "
                f"{stats.get('enabled', 0)} enabled · "
                f"{stats.get('disabled', 0)} disabled · "
                f"{stats.get('issues', 0)} issue(s)"
            ),
        ]
        issues = list(view.get("issues") or [])
        if issues:
            lines += ["", "**Open issues**"]
            lines += [f"- {issue}" for issue in issues[:MAX_ISSUES]]
            if len(issues) > MAX_ISSUES:
                lines.append(
                    f"- … {len(issues) - MAX_ISSUES} more (use action `routes`)"
                )
    except Exception as ex:
        logger.warning("Could not build the Traefik route view: %s", ex)
        lines += ["", f"⚠️ Could not build the route table: {ex}"]

    if summary.get("command"):
        lines += [
            "",
            "**Command**",
            "```text",
            "\n".join(summary["command"]),
            "```",
        ]
    if summary.get("env"):
        lines += ["", "**Traefik environment**", "```text", "\n".join(summary["env"]), "```"]
    return "\n".join(lines)


def _routes_report(settings: Optional[CODXJuniorSettings], search: Optional[str]) -> str:
    """Merged routing table (workspace, global and project routes) + issues."""
    view = traefik_manager.build_view(project_id=_project_id(settings))
    rows = list(view.get("routes") or [])
    total = len(rows)
    needle = (search or "").strip().lower()

    if needle:
        rows = [
            row for row in rows
            if needle in " ".join(
                str(row.get(key) or "")
                for key in (
                    "id", "name", "description", "source", "scope", "rule",
                    "target", "target_url", "managed_by", "project_name",
                )
            ).lower()
        ]

    stats = view.get("stats") or {}
    issues = list(view.get("issues") or [])
    lines = [
        "# Traefik routes",
        "",
        (
            f"**{len(rows)} of {total} route(s)**"
            + (f" matching `{search}`" if needle else "")
            + f" · {stats.get('enabled', 0)} enabled · {stats.get('disabled', 0)} disabled"
            + f" · {stats.get('issues', 0)} issue(s)"
        ),
        "",
    ]

    if not rows:
        lines.append("_No route matched._")
    else:
        shown = rows[:MAX_ROUTES]
        lines.append(
            _markdown_table(
                ["Route", "Source", "Rule", "Target", "Managed by", "On", "Issues"],
                [
                    [
                        f"`{row.get('id')}`",
                        str(row.get("source") or "-"),
                        f"`{row.get('rule')}`" if row.get("rule") else "-",
                        str(row.get("target") or "-"),
                        str(row.get("managed_by") or "-"),
                        "yes" if row.get("enabled") else "**no**",
                        str(len(row.get("issues") or [])),
                    ]
                    for row in shown
                ],
            )
        )
        if len(rows) > len(shown):
            lines.append("")
            lines.append(
                f"_… {len(rows) - len(shown)} more route(s); narrow the list with `search`._"
            )

    if issues:
        lines += ["", "## Issues", ""]
        lines += [f"- {issue}" for issue in issues[:MAX_ISSUES]]
        if len(issues) > MAX_ISSUES:
            lines.append(f"- … {len(issues) - MAX_ISSUES} more")

    domain = (view.get("defaults") or {}).get("domain")
    if domain:
        lines += ["", f"Traefik answers on `{domain}`."]
    return "\n".join(lines)


def _config_report(
    settings: Optional[CODXJuniorSettings],
    source: str,
    search: Optional[str] = None,
) -> str:
    """Generated dynamic config (what Traefik polls) or the static config file."""
    source = (source or "generated").strip().lower()
    if source not in CONFIG_SOURCES:
        return f"❌ Unknown config source `{source}`. Use `generated` or `file`."

    if source == "file":
        _name, info = _resolve_container()
        mounts = _state_summary(info)["mounts"] if info else []
        path, text, tried = _static_config(mounts)
        if text is None:
            return (
                "⚠️ The Traefik static configuration is not readable from this "
                "container. Tried: "
                + ", ".join(f"`{item}`" for item in tried)
                + f". Set `{CONFIG_PATH_ENV}` to its path (the installer mounts "
                "`./traefik` at `/traefik`)."
            )
        return "\n".join([
            "# Traefik static configuration",
            "",
            f"File: `{path}`",
            "",
            "```yaml",
            _redact(text).rstrip(),
            "```",
        ])

    config = traefik_manager.build_config()
    payload = json.dumps(config, indent=2, sort_keys=False)
    if len(payload) > MAX_CONFIG_CHARS:
        payload = payload[:MAX_CONFIG_CHARS] + "\n… (truncated)"
    http = (config or {}).get("http") or {}
    lines = [
        "# Traefik dynamic configuration",
        "",
        (
            "This is exactly what `GET /api/traefik/config` returns and Traefik "
            "polls every 10s: "
            f"{len((http.get('routers') or {}))} router(s), "
            f"{len((http.get('services') or {}))} service(s), "
            f"{len((http.get('middlewares') or {}))} middleware(s)."
        ),
        "",
        "```json",
        payload,
        "```",
    ]
    needle = (search or "").strip().lower()
    if needle:
        matching = [
            name for name in (http.get("routers") or {})
            if needle in name.lower()
        ]
        lines += ["", f"Routers matching `{search}`: " + (", ".join(f"`{m}`" for m in matching) or "_none_")]
    return "\n".join(lines)


def _logs_report(
    search: Optional[str],
    tail: int,
    settings: Optional[CODXJuniorSettings],
) -> str:
    """Traefik container logs (access logs included, they go to stdout)."""
    name, info = _resolve_container()
    if not name:
        return (
            "⚠️ No Traefik container found on this Docker daemon. Set "
            f"`{CONTAINER_ENV}` to the container name when it differs from "
            f"`{DEFAULT_CONTAINER}`."
        )

    result = _docker(["logs", "--tail", str(tail), name], timeout=30)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if not result.ok and not output.strip():
        return f"❌ Could not read the logs of `{name}`: {result.stderr.strip()}"

    lines = [_redact_log_line(line) for line in output.splitlines() if line.strip()]
    needle = (search or "").strip().lower()
    matched = [line for line in lines if needle in line.lower()] if needle else lines
    failures = [
        line for line in matched
        if any(marker in line.lower() for marker in FAILURE_MARKERS)
    ]

    window = matched[-MAX_LOG_LINES:]
    body = _bounded_log_lines(window)

    header = [
        f"# Traefik logs — `{name}`",
        "",
        (
            f"Last {tail} line(s) requested · {len(lines)} read · "
            f"{len(matched)} shown"
            + (f" matching `{search}`" if needle else "")
            + f" · {len(failures)} with an error/status marker"
            + (
                f" · {len(window) - len(body)} older line(s) omitted (size limit)"
                if len(window) > len(body) else ""
            )
            + " · credentials in headers are masked"
        ),
    ]
    if info is None:
        header.insert(1, "⚠️ The container could not be inspected (is it running?)")
    return "\n".join(
        header
        + ["", "```text", "\n".join(body) if body else "(no matching line)", "```"]
    )


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------


def traefik(
    action: str = "status",
    search: Optional[str] = None,
    tail: int = 100,
    source: str = "generated",
    **kwargs,
) -> ToolResponse:
    """
    Inspect the Traefik proxy codx-junior routes everything through.

    Args:
        action: What to read:
            - ``status`` (default): container state, effective command line,
              static-config location and route statistics with open issues.
            - ``routes``: the merged routing table (workspace, global and
              project routes) with rule, target and per-route issues.
            - ``config``: the dynamic configuration Traefik polls, or the
              static configuration file when ``source="file"``.
            - ``logs``: the container logs (access logs included).
        search: Optional case-insensitive text filter (routes, routers, log lines).
        tail: Log lines to read for ``action="logs"`` (1-200, default 100).
        source: For ``action="config"``: ``generated`` (default) or ``file``.
        **kwargs: Injected runtime arguments:
            - settings (CODXJuniorSettings): Project settings.
            - user (CodxUser): Current user (audit logging).

    Returns:
        ToolResponse: Markdown report in ``user_response`` and ``llm_response``.

    Example:
        >>> traefik(action="routes", search="/api")
        >>> traefik(action="logs", search="codx-junior-api", tail=50)
    """
    settings: Optional[CODXJuniorSettings] = kwargs.get("settings")
    user: Optional[CodxUser] = kwargs.get("user")

    action = (action or "status").strip().lower()
    if action not in ACTIONS:
        return ToolResponse(
            user_response=(
                f"❌ Unknown action `{action}`. Use one of: {', '.join(ACTIONS)}."
            ),
            llm_response=(
                f"Unknown traefik action {action!r}; valid actions are "
                f"{', '.join(ACTIONS)}."
            ),
        )

    if search is not None and len(str(search)) > MAX_SEARCH_CHARS:
        return ToolResponse(
            user_response=f"❌ `search` is too long (max {MAX_SEARCH_CHARS} characters).",
            llm_response="The traefik tool search filter is too long.",
        )

    try:
        tail = int(tail or 100)
    except (TypeError, ValueError):
        return ToolResponse(
            user_response="❌ `tail` must be a number.",
            llm_response="Invalid tail argument for the traefik tool.",
        )
    tail = max(1, min(tail, MAX_LOG_LINES))

    try:
        if action == "status":
            text = _status_report(settings)
        elif action == "routes":
            text = _routes_report(settings, search)
        elif action == "config":
            text = _config_report(settings, source, search)
        else:
            text = _logs_report(search, tail, settings)
    except ValueError as ex:
        logger.warning("traefik tool validation error (action=%s): %s", action, ex)
        return ToolResponse(
            user_response=f"❌ Traefik `{action}` failed: {ex}",
            llm_response=f"traefik(action={action}) failed: {ex}",
        )
    except Exception as ex:
        logger.exception("traefik tool failed (action=%s): %s", action, ex)
        return ToolResponse(
            user_response=f"❌ Traefik `{action}` failed: {ex}",
            llm_response=f"traefik(action={action}) failed: {ex}",
        )

    logger.info("traefik action=%s by user %s", action, user.username if user else "unknown")
    return ToolResponse(user_response=text, llm_response=text)


TRAEFIK_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "traefik",
        "description": (
            "Inspect the Traefik proxy codx-junior routes everything through "
            "(SPA, API, auth middleware, workspace apps and custom routes). "
            "Read-only. Use it before guessing why a URL returns 404/502 or why a "
            "route is missing: it returns the routing table codx-junior generates, "
            "the container state and command line, the static/dynamic configuration "
            "and the container logs (access logs included)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(ACTIONS),
                    "description": (
                        "'status' (container state, command line, config location and "
                        "route statistics), 'routes' (merged routing table with rule, "
                        "target and issues), 'config' (dynamic config Traefik polls, or "
                        "the static file with source='file'), 'logs' (container logs, "
                        "access logs included). Defaults to 'status'."
                    ),
                    "default": "status",
                },
                "search": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive text filter applied to route names, "
                        "rules and targets (action='routes'), router names "
                        "(action='config') or log lines (action='logs')."
                    ),
                },
                "tail": {
                    "type": "integer",
                    "description": "Log lines to read for action='logs' (1-200, default 100).",
                    "default": 100,
                },
                "source": {
                    "type": "string",
                    "enum": list(CONFIG_SOURCES),
                    "description": (
                        "For action='config': 'generated' (the dynamic config codx-junior "
                        "publishes, default) or 'file' (the static traefik.yaml)."
                    ),
                    "default": "generated",
                },
            },
            "required": [],
        },
    },
}
