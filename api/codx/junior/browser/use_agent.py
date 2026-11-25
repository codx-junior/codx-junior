"""
Run browser-use's own agent on the browser the human is watching.

Why this module exists
----------------------
``browse_action`` is a *tool loop*: SmolAgent decides one action, runs it, reads
the result, decides again.  That is cheap and predictable, but a multi-step
site flow (attach a picture and publish a post, fill a long form, book a slot)
costs many rounds and rebuilds the page state in the model's context each time.

`browser-use <https://github.com/browser-use/browser-use>`_ brings its own
agent: its model writes Python cells against raw CDP in a persistent REPL, keeps
the page state itself and returns one final answer.  SmolAgent stays the
assistant that talks to the human; this module is the bridge between them:

* it **attaches to the same Chromium** (``session.debug_url``) the human sees in
  the live view - one page, one set of cookies, no second browser;
* it uses the **project's own LLM** (``CODXJuniorSettings.get_llm_settings``)
  through browser-use's ``ChatOpenAI`` (any OpenAI-compatible server), so there
  is no second provider/key to configure and no Browser Use account required;
* the session's **Downloads and shared folders become the agent workspace**, so
  it attaches real files with ``upload_file(...)`` and its downloads land in the
  folder the human already has open;
* the run is written to the session's **activity trail** and returned as a
  normal tool result (final answer, steps, tokens, cost, downloads).

Process boundary
----------------
browser-use pins its own pydantic/pydantic-ai/MCP versions, which are not the
ones the API runs on.  It therefore runs in a **separate interpreter** - the
worker in ``api/scripts/browser_use_worker.py`` - instead of being imported into
this process: ``/opt/browser-use/bin/python`` in the released image (the
installer creates that venv), or any interpreter named by
``CODX_BROWSER_USE_PYTHON``, or this one when ``browser_use`` is importable
here.  The API process never pays for the dependency, and a browser-use release
can be upgraded without touching the API's dependency tree.

The tool keeps working when browser-use is absent: it says so and the native
``browse_action`` path (including ``action='upload'``) keeps driving the page.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from codx.junior.ai import llm_gateway

logger = logging.getLogger(__name__)

#: Optional dependency; never imported into the API process.
BROWSER_USE_PACKAGE = "browser-use"
BROWSER_USE_PIN = "browser-use==1.0.8"
BROWSER_USE_INSTALL = f"pip install '{BROWSER_USE_PIN}'"

#: Interpreter that owns the browser-use installation.
ENV_WORKER_PYTHON = "CODX_BROWSER_USE_PYTHON"
#: Path to the worker script (the image ships it next to the API sources).
ENV_WORKER_SCRIPT = "CODX_BROWSER_USE_WORKER"
#: Image default: the isolated venv the installer builds.
DEFAULT_WORKER_PYTHON = "/opt/browser-use/bin/python"

#: Environment switches.
ENV_AGENT_ENABLED = "CODX_BROWSER_AGENT"          # browser-use | native (default: auto)
ENV_MAX_STEPS = "CODX_BROWSER_AGENT_MAX_STEPS"   # model requests per run
ENV_TIMEOUT = "CODX_BROWSER_AGENT_TIMEOUT"       # seconds for the agent loop
ENV_MODEL = "CODX_BROWSER_AGENT_MODEL"           # LLM override for browser-use
ENV_JUDGE = "CODX_BROWSER_AGENT_JUDGE"           # "true" enables browser-use's judge

DEFAULT_MAX_STEPS = 30
MAX_STEPS_CAP = 200
#: browser-use's own default is 1700 s; the tool timeout usually bites first.
MAX_TIMEOUT_SECONDS = 900
MIN_TIMEOUT_SECONDS = 30
#: Extra seconds the worker gets on top of the agent-loop timeout.
WORKER_GRACE_SECONDS = 90

#: The workspace folder name inside a browser session folder.
WORKSPACE_FOLDER = "browser_use"

#: How often a run checks whether the human took the live view back.
LEASE_POLL_SECONDS = 1.0
#: Seconds of human input that count as "the human is driving" (browser-use
#: runs are long; a stray click must not kill the run, a typing human must).
HUMAN_QUIET_SECONDS = 1.0
#: How many recent co-browsing actions are shown to the agent.
ACTIVITY_CONTEXT_LIMIT = 8

#: The worker script next to the API package (``api/scripts/browser_use_worker.py``).
WORKER_SCRIPT_NAME = "browser_use_worker.py"


class BrowserUseError(RuntimeError):
    """browser-use is missing, disabled, or the run failed."""


class BrowserUsePaused(BrowserUseError):
    """The run stopped because the human took over the live view."""


@dataclass
class BrowserUseResult:
    """One browser-use run, in the shape the tools/chat need."""

    task: str
    final_result: Optional[str] = None
    success: Optional[bool] = None
    status: Optional[str] = None
    steps: int = 0
    duration_s: float = 0.0
    usage: Dict[str, int] = field(default_factory=dict)
    cost_usd: Optional[float] = None
    downloads: List[str] = field(default_factory=list)
    model: str = ""
    workspace: str = ""
    worker_python: str = ""
    #: cxjcoins charged by the codx-junior LLM gateway for this run (0 when the
    #: gateway is disabled, in which case the spend is not accounted).
    cost_cxj: float = 0.0
    #: True when the model calls went through the codx-junior LLM gateway.
    gateway: bool = False
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        """Return the prompt+completion tokens reported by browser-use."""
        return int(self.usage.get("input", 0) or 0) + int(self.usage.get("output", 0) or 0)

    def summary_line(self) -> str:
        """Return one compact status line (steps, tokens, cost, downloads)."""
        parts = [f"{self.steps} step(s)"]
        if self.total_tokens:
            parts.append(f"{self.total_tokens} tokens")
        if self.cost_cxj:
            parts.append(f"{self.cost_cxj:.4f} cxj")
        if self.cost_usd is not None:
            parts.append(f"${self.cost_usd:.4f}")
        if self.downloads:
            parts.append(f"{len(self.downloads)} download(s)")
        if self.status:
            parts.append(self.status)
        return " · ".join(parts)


#: One browser-use run per session at a time (two agents on one Chrome fight).
_SESSION_LOCKS: Dict[str, asyncio.Lock] = {}


def _lock_for(session_id: str) -> asyncio.Lock:
    """Return the per-session run lock."""
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[session_id] = lock
    return lock


def worker_script() -> Optional[Path]:
    """Return the worker script path (configured, or next to the API package)."""
    configured = os.environ.get(ENV_WORKER_SCRIPT)
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    candidate = Path(__file__).resolve().parents[3] / "scripts" / WORKER_SCRIPT_NAME
    return candidate if candidate.is_file() else None


def _can_import_browser_use(python: str) -> bool:
    """Return True when *python* can import browser_use (cheap spec check)."""
    if not python:
        return False
    if Path(python).resolve() == Path(sys.executable).resolve():
        try:
            return importlib.util.find_spec("browser_use") is not None
        except (ImportError, ValueError):
            return False
    try:
        import subprocess

        result = subprocess.run(
            [python, "-c", "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('browser_use') else 1)"],
            capture_output=True,
            timeout=20,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_counter: Dict[str, Any] = {"checked": False, "python": None}


def worker_python(*, refresh: bool = False) -> Optional[str]:
    """
    Return the interpreter that runs browser-use, or None when unavailable.

    Order: ``CODX_BROWSER_USE_PYTHON``, the image venv, this interpreter (when
    browser-use is installed here).  The result is cached: the check spawns a
    process, and the browser tool must stay fast.
    """
    if _counter["checked"] and not refresh:
        return _counter["python"]

    candidates: List[str] = []
    configured = (os.environ.get(ENV_WORKER_PYTHON) or "").strip()
    if configured:
        candidates.append(configured)
    if DEFAULT_WORKER_PYTHON not in candidates and Path(DEFAULT_WORKER_PYTHON).exists():
        candidates.append(DEFAULT_WORKER_PYTHON)
    candidates.append(sys.executable)

    found: Optional[str] = None
    for candidate in candidates:
        if candidate == sys.executable or shutil.which(candidate) or Path(candidate).exists():
            if _can_import_browser_use(candidate):
                found = candidate
                break
    _counter["checked"], _counter["python"] = True, found
    return found


def browser_use_available() -> bool:
    """Return True when some interpreter can run browser-use."""
    return worker_python() is not None and worker_script() is not None


def browser_use_version() -> Optional[str]:
    """Return the installed browser-use version (None when missing)."""
    python = worker_python()
    if not python:
        return None
    try:
        import subprocess

        result = subprocess.run(
            [python, "-c", "from importlib.metadata import version; print(version('browser-use'))"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def agent_mode() -> str:
    """
    Return which browser agent the deployment wants: ``browser-use`` or ``native``.

    ``CODX_BROWSER_AGENT`` wins when set; otherwise browser-use is used when the
    worker is available (the tool still falls back with a clear message).
    """
    choice = (os.environ.get(ENV_AGENT_ENABLED) or "").strip().lower()
    if choice in ("native", "internal", "off", "false", "0"):
        return "native"
    if choice in ("browser-use", "browseruse", "browser_use", "auto", "on", "true", "1"):
        return "browser-use"
    return "browser-use" if browser_use_available() else "native"


def _env_int(name: str, default: int) -> int:
    """Read a positive integer environment value."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return default


def _clamp_timeout(value: Any) -> int:
    """Clamp a run timeout into browser-use's usable range."""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        seconds = _env_int(ENV_TIMEOUT, 300)
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, seconds))


def _mkdir_link(link: Path, target: Path, *, is_dir: bool = True) -> None:
    """Create (or refresh) a symlink; ignore platforms/filesystems that refuse."""
    try:
        if link.is_symlink() or link.exists():
            if link.is_symlink() or (is_dir and link.is_dir()) or (not is_dir and link.is_file()):
                return
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError as ex:
        logger.debug("Could not link %s -> %s: %s", link, target, ex)


class BrowserUseRunner:
    """Bridge one browser session to a browser-use ``Agent`` run."""

    def __init__(
        self,
        *,
        settings: Any,
        session: Any,
        manager: Any = None,
        chat_id: Optional[str] = None,
        user: Any = None,
    ) -> None:
        """
        Args:
            settings: CODXJuniorSettings (LLM configuration + project paths).
            session: The shared BrowserSession the human sees.
            manager: BrowserManager owning the session (for activity/lease).
            chat_id: Chat that asked for the run (activity attribution).
            user: CodxUser asking for the run (wallet check + analytics author).
        """
        self.settings = settings
        self.session = session
        self.manager = manager
        self.chat_id = chat_id
        self.user = user
        self.session_id = str(getattr(session, "id", "") or "")

    # -- configuration -----------------------------------------------------
    def _session_folder(self) -> Path:
        """Return the session folder (manager-owned, with a path fallback)."""
        if self.manager is not None and hasattr(self.manager, "_session_folder_path"):
            try:
                return Path(self.manager._session_folder_path(self.session_id))
            except Exception:  # pragma: no cover - defensive
                pass
        downloads = getattr(self.session, "downloads_path", None)
        if downloads:
            return Path(downloads).parent
        return Path(getattr(self.settings, "codx_path", None) or ".") / "browser_sessions" / self.session_id

    def workspace(self) -> Path:
        """
        Build (once) the folder browser-use sees as its workspace.

        ``downloads/`` is the session's real downloads folder (a symlink), so a
        file the agent downloads is immediately available to the human and vice
        versa: the agent uploads from the same folder the user saved the picture
        into.  Every shared project folder is exposed under ``shared/``.
        """
        root = self._session_folder() / WORKSPACE_FOLDER
        root.mkdir(parents=True, exist_ok=True)

        downloads = getattr(self.session, "downloads_path", None)
        if downloads:
            target = Path(str(downloads))
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as ex:
                logger.debug("Could not create downloads folder %s: %s", target, ex)
            else:
                _mkdir_link(root / "downloads", target)

        shared_root = root / "shared"
        shared_root.mkdir(parents=True, exist_ok=True)
        for share in getattr(self.session, "shared_folders", None) or []:
            host = getattr(share, "host_path", None)
            if not host:
                continue
            try:
                path = Path(str(host))
            except (TypeError, ValueError):
                continue
            if not path.is_dir():
                continue
            label = str(
                getattr(share, "name", None)
                or getattr(share, "folder", None)
                or path.name
                or "shared"
            )
            label = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label).strip("-") or "shared"
            _mkdir_link(shared_root / label, path)
        return root

    def workspace_manifest(self, workspace: Path) -> str:
        """Describe the workspace (and the upload/download rules) for the model."""
        lines = [
            "You drive the SAME Chromium the human is watching in codx-junior's live view.",
            "One shared browser: do not clear cookies, log out, or close the human's tabs.",
            f"Workspace: {workspace}",
            "- downloads/ is the session downloads folder shared with the human.",
            "- shared/ holds the project folders mounted in this session.",
        ]
        downloads = workspace / "downloads"
        try:
            names = sorted(p.name for p in downloads.iterdir() if p.is_file())[:40]
        except OSError:
            names = []
        if names:
            lines.append(
                "Files already in downloads/ (attach one with upload_file(selector, "
                f"'{downloads / names[0]}') or any absolute path below):"
            )
            lines.extend(f"- {downloads / name}" for name in names)
        lines.append(
            "To attach a local file to a page use upload_file(selector, path) with an "
            "absolute path inside the workspace; never invent a file path."
        )
        current = getattr(self.session, "current_url", None)
        if current and current not in ("about:blank", "chrome://newtab/"):
            lines.append(f"The browser is currently on: {current}")

        lines.append(
            "Co-browsing rules: the human watches and can control this same page in the "
            "live view. They always win: if they take control or start typing, codx-junior "
            "pauses your run - stop, summarise what you were doing and what is left. "
            "If you need the human (login, MFA, captcha, payment) stop and say exactly "
            "what they must do; never fill credentials, OTPs or payment data."
        )
        activity = self.recent_activity()
        if activity:
            lines.append("Recent co-browsing activity (newest last; 'user' is the human):")
            lines.extend(self._activity_line(entry) for entry in activity[-5:])
        return "\n".join(lines)

    def _llm_settings(self, model: Optional[str] = None):
        """Resolve the project LLM settings (optionally overriding the model)."""
        llm_model = (model or os.environ.get(ENV_MODEL) or "").strip() or None
        return self.settings.get_llm_settings(llm_model=llm_model)

    def _human_took_over(self) -> bool:
        """
        True when the human owns the page or is actively using the live view.

        Co-browsing is cooperative: the human always wins.  A browser-use run is
        therefore stopped as soon as the lease moves or the human starts
        clicking/typing, instead of the agent fighting the human for the page.
        """
        session = self.session
        try:
            if getattr(session, "handoff_reason", None):
                return True
            if str(getattr(session, "controller", "ai") or "ai") != "ai":
                return True
            checker = getattr(session, "ai_may_act_now", None)
            if callable(checker):
                return not bool(checker(HUMAN_QUIET_SECONDS))
        except Exception as ex:  # pragma: no cover - defensive
            logger.debug("Could not read the control lease: %s", ex)
        return False

    def recent_activity(self, limit: int = ACTIVITY_CONTEXT_LIMIT) -> List[Dict[str, Any]]:
        """Return the last co-browsing actions (human and AI) for the agent."""
        manager = self.manager
        if manager is None or not hasattr(manager, "_activity_log"):
            return []
        try:
            entries = manager._activity_log(self.session).read(limit)
        except Exception as ex:  # pragma: no cover - best effort
            logger.debug("Could not read browser activity: %s", ex)
            return []
        return [entry for entry in entries if isinstance(entry, dict)][-limit:]

    @staticmethod
    def _activity_line(entry: Dict[str, Any]) -> str:
        """Format one activity entry compactly for the agent's system message."""
        params = entry.get("params") or {}
        target = params.get("target") if isinstance(params, dict) else None
        detail = ""
        if isinstance(target, dict):
            detail = target.get("text") or target.get("selector") or target.get("name") or ""
        elif isinstance(target, str):
            detail = target
        if not detail and isinstance(params, dict):
            detail = params.get("url") or params.get("task") or ""
        stamp = str(entry.get("ts") or "")[11:19]
        return (
            f"- {stamp} {entry.get('actor') or '?'}: {entry.get('action') or '?'}"
            + (f" · {str(detail)[:120]}" if detail else "")
        )

    def _cdp_url(self) -> str:
        """Return the CDP endpoint of the shared container browser."""
        url = getattr(self.session, "debug_url", None)
        if not url:
            raise BrowserUseError(
                "The browser session has no DevTools endpoint yet "
                f"(status: {getattr(self.session, 'status', '?')}). Start it with start_session."
            )
        return str(url)

    def payload(
        self,
        task: str,
        *,
        max_steps: Optional[int] = None,
        timeout: Optional[int] = None,
        model: Optional[str] = None,
        llm_run: Optional[llm_gateway.LlmRun] = None,
    ) -> Dict[str, Any]:
        """
        Build the JSON request the worker consumes.

        With a gateway run the worker receives the **run token** as its API key
        and the codx-junior gateway as base URL; it never sees the provider key.
        Without it (gateway disabled, or no loopback API) it falls back to the
        project endpoint directly.
        """
        llm = self._llm_settings(model)
        steps = int(max_steps if max_steps is not None else _env_int(ENV_MAX_STEPS, DEFAULT_MAX_STEPS))
        steps = max(1, min(MAX_STEPS_CAP, steps))
        seconds = _clamp_timeout(timeout)
        workspace = self.workspace()

        if llm_run is not None:
            base_url = llm_gateway.gateway_base_url() or ""
            api_key = llm_run.token
            model_name = llm_run.model or getattr(llm, "model", "") or ""
        else:
            base_url = getattr(llm, "api_url", "") or ""
            api_key = getattr(llm, "api_key", "") or ""
            model_name = getattr(llm, "model", "") or ""

        return {
            "task": task,
            "cdp_url": self._cdp_url(),
            "workspace": str(workspace),
            "system_message": self.workspace_manifest(workspace),
            "model": model_name,
            "base_url": base_url,
            "api_key": api_key,
            "max_steps": steps,
            "timeout": seconds,
            "judge": (os.environ.get(ENV_JUDGE, "").strip().lower() in ("1", "true", "yes", "on")),
            # Informational for logs/debugging: model calls go through the
            # codx-junior gateway (run token) or straight to the provider.
            "gateway": llm_run is not None,
        }

    # -- run ---------------------------------------------------------------
    async def run(
        self,
        task: str,
        *,
        max_steps: Optional[int] = None,
        timeout: Optional[int] = None,
        model: Optional[str] = None,
        runtime: Any = None,
    ) -> BrowserUseResult:
        """
        Run one browser-use task on the shared browser.

        Args:
            task: Natural-language goal for the browser-use agent.
            max_steps: Model requests allowed (default 30, max 200).
            timeout: Seconds for the agent loop (default 300, max 900).
            model: Optional LLM override (default: the project model).
            runtime: The session's BrowserRuntime, when the caller already has it
                (used to take/release the human/AI control lease).

        Returns:
            BrowserUseResult: Final answer, steps, usage, cost and downloads.

        Raises:
            BrowserUseError: When browser-use is unavailable or the run fails.
        """
        goal = (task or "").strip()
        if not goal:
            raise BrowserUseError("A task is required")

        python = worker_python(refresh=True)
        script = worker_script()
        if not python or not script:
            raise BrowserUseError(
                f"browser-use is not available ({BROWSER_USE_INSTALL}); "
                f"set {ENV_WORKER_PYTHON} to an interpreter that has it. "
                "browse_action keeps working and can upload files (action='upload')."
            )

        seconds_for_token = _clamp_timeout(timeout) + WORKER_GRACE_SECONDS + 120
        llm_run: Optional[llm_gateway.LlmRun] = None
        if llm_gateway.gateway_enabled() and llm_gateway.gateway_base_url():
            try:
                llm_run = llm_gateway.open_run(
                    self.settings,
                    chat_id=self.chat_id,
                    browser_session_id=self.session_id,
                    username=getattr(self.user, "username", None),
                    model=model,
                    ttl=seconds_for_token,
                )
            except Exception as ex:
                # Never block browsing because accounting could not start: the
                # run goes direct and the result says it was not accounted.
                logger.warning("Could not start the LLM gateway run: %s", ex)
                llm_run = None

        request = self.payload(
            goal, max_steps=max_steps, timeout=timeout, model=model, llm_run=llm_run
        )
        started = time.monotonic()

        # One browser-use agent per browser at a time.
        async with _lock_for(self.session_id):
            if runtime is not None and hasattr(runtime, "begin_external_ai"):
                await runtime.begin_external_ai()
            if self.manager is not None:
                self.manager.log_activity(
                    self.session,
                    actor="ai",
                    action="browser_use_start",
                    chat_id=self.chat_id,
                    params={
                        "task": goal[:2000],
                        "max_steps": request["max_steps"],
                        "timeout": request["timeout"],
                    },
                )
            try:
                data = await self._run_worker(python, script, request)
            except BrowserUsePaused as ex:
                # The human took the page back: an expected hand-off, not a
                # failure. The activity trail says who interrupted the agent.
                self._attach_cost(ex, llm_run)
                if self.manager is not None:
                    self.manager.log_activity(
                        self.session,
                        actor="user",
                        action="browser_use_pause",
                        chat_id=self.chat_id,
                        params={"task": goal[:2000]},
                        duration_ms=(time.monotonic() - started) * 1000,
                        result={"reason": str(ex)},
                    )
                raise
            except Exception as ex:
                # Partial spend (the engine may have made several model calls
                # before failing) must still reach the chat.
                self._attach_cost(ex, llm_run)
                if self.manager is not None:
                    self.manager.log_activity(
                        self.session,
                        actor="ai",
                        action="browser_use",
                        chat_id=self.chat_id,
                        success=False,
                        params={"task": goal[:2000]},
                        duration_ms=(time.monotonic() - started) * 1000,
                        error=str(ex),
                    )
                raise
            finally:
                if runtime is not None and hasattr(runtime, "end_external_ai"):
                    try:
                        await runtime.end_external_ai()
                    except Exception as ex:  # pragma: no cover - best effort
                        logger.debug("Releasing the browser lease failed: %s", ex)

            result = self._result(goal, data, request, python)
            result.duration_s = result.duration_s or (time.monotonic() - started)
            if llm_run is not None:
                totals = llm_gateway.usage_totals(llm_run.ledger_path)
                result.gateway = True
                result.cost_cxj = float(totals.get("cost_cxj") or 0.0)
                result.usage = {
                    "input": int(totals.get("input_tokens") or 0),
                    "output": int(totals.get("output_tokens") or 0),
                    "requests": int(totals.get("calls") or 0),
                }

            if self.manager is not None:
                self.manager.log_activity(
                    self.session,
                    actor="ai",
                    action="browser_use",
                    chat_id=self.chat_id,
                    success=result.error is None and bool(result.success),
                    params={"task": goal[:2000], "steps": result.steps},
                    duration_ms=(time.monotonic() - started) * 1000,
                    error=result.error,
                    result={
                        "final_result": (result.final_result or "")[:2000],
                        "status": result.status,
                        "usage": result.usage,
                        "cost_usd": result.cost_usd,
                        "downloads": result.downloads[:20],
                    },
                )
            if result.error:
                raise BrowserUseError(result.error)
            return result

    @staticmethod
    def _attach_cost(error: Exception, llm_run: Optional[llm_gateway.LlmRun]) -> None:
        """Attach the gateway spend so far to a failed/paused run exception."""
        if llm_run is None:
            return
        try:
            totals = llm_gateway.usage_totals(llm_run.ledger_path)
            error.cost_cxj = float(totals.get("cost_cxj") or 0.0)  # type: ignore[attr-defined]
        except Exception as ex:  # pragma: no cover - best effort
            logger.debug("Could not read the run cost: %s", ex)

    async def _watch_lease(self, proc: asyncio.subprocess.Process) -> Optional[str]:
        """
        Stop the worker when the human takes the live view back.

        browser-use has no step callback in 1.0, so the co-browsing lease is
        enforced from the outside: poll the session every second and terminate
        the worker's process group on the first human input/control change.
        """
        while True:
            await asyncio.sleep(LEASE_POLL_SECONDS)
            if proc.returncode is not None:
                return None
            if self._human_took_over():
                self._terminate(proc)
                return (
                    "The human took control of the live view, so browser-use stopped. "
                    "Whatever the agent had done until then is on the page."
                )

    @staticmethod
    def _terminate(proc: asyncio.subprocess.Process) -> None:
        """Terminate the worker and everything it started (best effort)."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            return
        except Exception:
            pass
        with contextlib.suppress(Exception):
            proc.terminate()

    async def _run_worker(self, python: str, script: Path, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the worker once and parse its JSON answer."""
        env = {
            **os.environ,
            # browser-use must not phone home from a product deployment.
            "ANONYMIZED_TELEMETRY": "false",
            "BROWSER_USE_SETUP_LOGGING": "true",
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                python,
                str(script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                # Its own process group: the worker's harness daemon is a child,
                # and a pause/timeout must stop the whole group, not just the
                # worker (a live daemon would keep driving the page).
                start_new_session=True,
            )
        except OSError as ex:
            raise BrowserUseError(
                f"Could not start the browser-use worker with {python}: {ex}"
            ) from ex
        payload = json.dumps(request).encode("utf-8")
        deadline = int(request["timeout"]) + WORKER_GRACE_SECONDS
        monitor = asyncio.create_task(self._watch_lease(proc))
        pause_reason: Optional[str] = None
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=deadline)
        except asyncio.TimeoutError:
            self._terminate(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=10)
            raise BrowserUseError(
                f"browser-use did not finish within {deadline}s "
                f"(the agent loop was allowed {request['timeout']}s)."
            )
        finally:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                pause_reason = await monitor
        if pause_reason:
            raise BrowserUsePaused(pause_reason)

        output = (stdout or b"").decode("utf-8", "replace").strip()
        errors = (stderr or b"").decode("utf-8", "replace").strip()
        if errors:
            logger.debug("browser-use worker stderr:\n%s", errors[-4000:])

        data: Optional[Dict[str, Any]] = None
        for line in reversed(output.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if isinstance(candidate, dict) and "ok" in candidate:
                data = candidate
                break
        if data is None:
            tail = (output or errors or "")[-1500:]
            raise BrowserUseError(
                f"The browser-use worker returned no result (exit {proc.returncode}). {tail}"
            )
        if not data.get("ok"):
            raise BrowserUseError(str(data.get("error") or "browser-use failed"))
        return data

    @staticmethod
    def _result(
        goal: str,
        data: Dict[str, Any],
        request: Dict[str, Any],
        python: str,
    ) -> BrowserUseResult:
        """Map the worker's JSON answer onto :class:`BrowserUseResult`."""
        usage_raw = data.get("usage") or {}
        try:
            usage = {str(key): int(value or 0) for key, value in dict(usage_raw).items()}
        except Exception:
            usage = {}
        cost = data.get("cost_usd")
        try:
            cost_usd = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_usd = None
        return BrowserUseResult(
            task=goal,
            final_result=(str(data.get("final_result")) if data.get("final_result") is not None else None),
            success=data.get("success"),
            status=(str(data.get("status")) if data.get("status") else None),
            steps=int(data.get("steps") or 0),
            duration_s=float(data.get("duration_s") or 0.0),
            usage=usage,
            cost_usd=cost_usd,
            downloads=[str(item) for item in (data.get("downloads") or [])],
            model=str(request.get("model") or ""),
            workspace=str(request.get("workspace") or ""),
            worker_python=python,
        )
