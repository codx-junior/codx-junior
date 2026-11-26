"""
Harness sessions: the DSH-like unit of work of the codx-junior harness.

A **harness session** is a task chat, owned by the project, executed by the
built-in ``codx-junior`` profile in ``task`` mode. It is the object the harness
mode UI lists on the left and follows on the right:

* the chat is the single copy of the conversation (messages, tool events,
  lifecycle events, cost) — exactly like any other task;
* :mod:`codx.junior.harness.prompts` appends the closed-loop contract (survey,
  reproduce, analyse, implement, test, verify, report) to the first message, so
  the same agent runtime becomes a coding harness without a second engine;
* this service only **composes** existing pieces: it creates the task through
  :class:`~codx.junior.chat_manager.ChatManager`, reads the task tree's isolated
  worktree through :class:`~codx.junior.task_workspace.TaskWorkspaceManager` and
  reports the diff of the branch.

Nothing here executes an AI turn. Starting the turn stays on the existing
socket path (``codx-junior-chat``), so a harness session has the same crash
recovery, cancellation, budget and streaming behavior as every other chat.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import prompts

logger = logging.getLogger(__name__)

#: Bounded git call (the API must answer even on a huge repository).
GIT_TIMEOUT_SECONDS = 20

#: Statuses a session summary can report.
SESSION_RUNNING = "running"
SESSION_DONE = "done"
SESSION_FAILED = "failed"
SESSION_IDLE = "idle"

#: Tools whose arguments name files written by the session (diff fallback).
_WRITE_TOOLS = {
    "project_write_file",
    "project_apply_file_changes",
    "apply_file_changes",
    "code_writer",
    "project_create_file",
    "project_delete_file",
    "restore_changes",
}

_PATH_KEYS = ("file_path", "path", "file", "filename", "filepath")


class HarnessSessionError(RuntimeError):
    """A harness session request is invalid or the session does not exist."""


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC instant as ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp (naive values are treated as UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds_between(start: Any, end: Any) -> int:
    """Return the non-negative number of seconds between two timestamps."""
    first, last = _parse_time(start), _parse_time(end)
    if first is None or last is None:
        return 0
    return max(0, int((last - first).total_seconds()))


# ---------------------------------------------------------------------------
# Git helpers (best effort: a diff is never required to open a session)
# ---------------------------------------------------------------------------


def _run_git(path: str, args: List[str]) -> Tuple[bool, str]:
    """Run one read-only git command; return ``(ok, stdout)``."""
    if not path or not os.path.isdir(path):
        return False, ""
    try:
        completed = subprocess.run(
            ["git", "--no-pager", "-c", "core.quotepath=false", *args],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        logger.debug("Harness session: git %s failed: %s", args, ex)
        return False, ""
    if completed.returncode != 0:
        return False, completed.stdout or ""
    return True, completed.stdout or ""


def _numstat(
    path: str, base: Optional[str], paths: Optional[Iterable[str]] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Return ``{file: {additions, deletions}}`` for ``base..HEAD``.

    Args:
        path: Checkout to run git in.
        base: Base reference; ``None`` compares the working tree with HEAD.
        paths: Optional path filter (used by the non-worktree fallback).
    """
    args = ["diff", "--numstat"]
    args.append(f"{base}..HEAD" if base else "HEAD")
    selected = [str(item) for item in (paths or []) if str(item).strip()]
    if selected:
        args += ["--", *selected]
    ok, output = _run_git(path, args)
    if not ok:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, file_path = parts[0], parts[1], parts[2]
        # Binary files report '-'; renames are "old => new": keep the new path.
        file_path = file_path.split("=>")[-1].strip()
        result[file_path] = {
            "additions": int(added) if added.isdigit() else 0,
            "deletions": int(removed) if removed.isdigit() else 0,
        }
    return result


def _name_status(
    path: str, base: Optional[str], paths: Optional[Iterable[str]] = None
) -> Dict[str, str]:
    """Return ``{file: status}`` for ``base..HEAD`` (A/M/D/R/T)."""
    args = ["diff", "--name-status"]
    args.append(f"{base}..HEAD" if base else "HEAD")
    selected = [str(item) for item in (paths or []) if str(item).strip()]
    if selected:
        args += ["--", *selected]
    ok, output = _run_git(path, args)
    if not ok:
        return {}
    statuses: Dict[str, str] = {}
    labels = {
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "typechange",
        "M": "modified",
    }
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = (parts[0] or "").strip()[:1]
        file_path = parts[-1].strip()
        if file_path:
            statuses[file_path] = labels.get(code, "modified")
    return statuses


def _working_tree_files(
    path: str, paths: Optional[Iterable[str]] = None
) -> List[str]:
    """Return the files with uncommitted changes (untracked included)."""
    args = ["status", "--porcelain"]
    selected = [str(item) for item in (paths or []) if str(item).strip()]
    if selected:
        args += ["--", *selected]
    ok, output = _run_git(path, args)
    if not ok:
        return []
    result = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        file_path = line[3:].strip().strip('"')
        if " -> " in file_path:
            file_path = file_path.split(" -> ")[-1].strip().strip('"')
        if file_path:
            result.append(file_path)
    return result


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class HarnessSessionService:
    """
    Create, list and inspect harness sessions of one project.

    Args:
        settings: Project settings (``codx_path``, ``project_id``...).
        chat_manager: Optional ``ChatManager`` (built lazily from *settings*).
        user: Optional username used for the created messages.
    """

    def __init__(
        self,
        settings: Any,
        chat_manager: Any = None,
        user: Optional[str] = None,
    ) -> None:
        self.settings = settings
        self.chat_manager = chat_manager
        self.user = str(user or "harness")

    # -- dependencies -----------------------------------------------------

    def _manager(self) -> Any:
        """Return (creating if needed) the project ChatManager."""
        if self.chat_manager is None:
            from codx.junior.chat_manager import ChatManager

            self.chat_manager = ChatManager(settings=self.settings)
        return self.chat_manager

    @property
    def codx_path(self) -> str:
        """The project's ``.codx`` folder (harness state lives there)."""
        return str(getattr(self.settings, "codx_path", "") or "")

    @property
    def project_id(self) -> Optional[str]:
        """The project id recorded on created sessions."""
        return getattr(self.settings, "project_id", None) or None

    # -- board ------------------------------------------------------------

    def ensure_board(self) -> None:
        """
        Create the ``Harness`` board on first use (best effort).

        The session lives in a normal board so it stays visible in the board
        view and in Follow-up; the harness mode is an alternative lens over the
        same task, never a second storage.
        """
        manager = self._manager()
        try:
            kanban = manager.load_kanban() or {}
            boards = kanban.get("boards")
            if not isinstance(boards, dict):
                boards = {}
                kanban["boards"] = boards
            existing = {
                str(name).strip().lower() for name in boards.keys()
            }
            if prompts.HARNESS_BOARD.lower() in existing:
                return
            boards[prompts.HARNESS_BOARD] = {
                "title": prompts.HARNESS_BOARD,
                "description": (
                    "Harness sessions: tasks executed end to end by the "
                    "codx-junior harness (survey, fix, test, report)."
                ),
                "type": "kanban",
                "columns": [
                    {"title": column["title"], "status": column["status"]}
                    for column in prompts.HARNESS_BOARD_COLUMNS
                ],
            }
            manager.save_kanban(kanban)
        except Exception as ex:  # pragma: no cover - a board must never block a session
            logger.warning("Harness session: cannot ensure the board: %s", ex)

    # -- classification ---------------------------------------------------

    @staticmethod
    def is_harness_chat(chat: Any) -> bool:
        """
        Tell whether *chat* is a harness session.

        Args:
            chat: A ``Chat`` (full or metadata projection).

        Returns:
            True when the chat carries the harness tag or the harness descriptor
            in its meta_data. The board is a display location, not identity: an
            orchestration step card or a manually moved task on the same board
            is not a harness session.
        """
        if chat is None:
            return False
        tags = getattr(chat, "tags", None) or []
        if prompts.HARNESS_TAG in tags:
            return True
        return prompts.is_harness_meta(getattr(chat, "meta_data", None))

    # -- summary ----------------------------------------------------------

    @staticmethod
    def _last_message(chat: Any) -> Optional[Any]:
        """Return the newest visible message of *chat*, when loaded."""
        messages = getattr(chat, "messages", None) or []
        for message in reversed(messages):
            if not getattr(message, "unsent", False):
                return message
        return None

    @staticmethod
    def _first_user_message(chat: Any) -> Optional[Any]:
        """Return the first user message of *chat*, when loaded."""
        for message in getattr(chat, "messages", None) or []:
            if getattr(message, "role", "") == "user":
                return message
        return None

    def _phase(self, chat: Any) -> str:
        """Resolve the UI phase of a chat (running/done/failed/idle)."""
        process_state = str(
            getattr(chat, "task_process_state", "") or ""
        ).strip().lower()
        pending = int(getattr(chat, "pending_count", 0) or 0)
        if process_state == "running" or pending > 0:
            return SESSION_RUNNING
        if process_state in {"error", "stopped"}:
            return SESSION_FAILED
        status = str(getattr(chat, "status", "") or "").strip().lower()
        if status in {"done", "rejected"} and process_state != "running":
            return SESSION_DONE
        messages = getattr(chat, "messages", None) or []
        answered = any(
            getattr(message, "role", "") == "assistant" for message in messages
        )
        if answered and all(getattr(message, "done", True) for message in messages):
            return SESSION_DONE
        return SESSION_IDLE

    def summary(self, chat: Any) -> Dict[str, Any]:
        """
        Build the compact session row shown by the harness sidebar.

        Args:
            chat: A ``Chat`` (metadata projection or full document).

        Returns:
            A JSON-safe summary with timings, phase, last activity and cost.
        """
        messages = getattr(chat, "messages", None) or []
        first = self._first_user_message(chat) if messages else None
        last = self._last_message(chat) if messages else None
        started_at = (
            getattr(chat, "created_at", "") or getattr(chat, "updated_at", "")
        )
        last_at = (
            getattr(last, "created_at", "")
            or getattr(chat, "updated_at", "")
            or getattr(chat, "created_at", "")
        )
        latest = getattr(chat, "task_latest_message", None) or None
        if not last_at and isinstance(latest, dict):
            last_at = latest.get("created_at") or ""
        preview = ""
        if isinstance(latest, dict):
            preview = str(latest.get("content") or "")[:280]
        elif last is not None:
            preview = str(getattr(last, "content", "") or "")[:280]
        if first is not None and getattr(first, "role", "") == "user":
            preview = preview or prompts.strip_session_brief(
                getattr(first, "content", "")
            )[:280]

        return {
            "session_id": str(getattr(chat, "id", "") or ""),
            "chat_id": str(getattr(chat, "id", "") or ""),
            "name": str(getattr(chat, "name", "") or "Harness session"),
            "description": str(getattr(chat, "description", "") or ""),
            "phase": self._phase(chat),
            "process_state": str(getattr(chat, "task_process_state", "") or ""),
            "process_error": getattr(chat, "task_process_error", None),
            "status": str(getattr(chat, "status", "") or ""),
            "board": str(getattr(chat, "board", "") or ""),
            "column": str(getattr(chat, "column", "") or ""),
            "profiles": list(getattr(chat, "profiles", None) or []),
            "model": str(getattr(chat, "llm_model", "") or ""),
            "created_at": started_at,
            "updated_at": str(getattr(chat, "updated_at", "") or ""),
            "last_activity_at": last_at,
            "duration_seconds": _seconds_between(started_at, last_at),
            "cost_cxjcoins": round(float(getattr(chat, "cost", 0.0) or 0.0), 6),
            "budget_cxjcoins": float(getattr(chat, "estimation_cost", 0.0) or 0.0),
            "preview": prompts.strip_session_brief(preview).strip(),
            "message_count": int(
                getattr(chat, "message_count", 0) or len(messages) or 0
            ),
            "run_id": getattr(chat, "run_id", None),
        }

    # -- create -----------------------------------------------------------

    def create(
        self,
        prompt: str,
        *,
        name: Optional[str] = None,
        profiles: Optional[Iterable[str]] = None,
        model: Optional[str] = None,
        budget_cxjcoins: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Create a harness session (chat + first user message) and persist it.

        Args:
            prompt: The user's request (the ticket).
            name: Optional title; defaults to the first line of *prompt*.
            profiles: Profiles for the session; ``codx-junior`` is always added.
            model: Optional LLM model (``Chat.llm_model``).
            budget_cxjcoins: Optional chat budget.

        Returns:
            The session summary of the created chat.

        Raises:
            HarnessSessionError: When the prompt is empty.
        """
        from codx.junior.db import Chat, Message

        text = str(prompt or "").strip()
        if not text:
            raise HarnessSessionError("The session prompt cannot be empty")

        requested = [
            str(profile or "").strip()
            for profile in (profiles or [])
            if str(profile or "").strip()
        ]
        # `codx-junior-max` already inherits `codx-junior`; adding both would
        # duplicate the profile content and the effort policy. Only add the
        # base harness profile when the caller did not name a harness tier.
        has_harness_tier = any(
            name == prompts.HARNESS_PROFILE or name.startswith(f"{prompts.HARNESS_PROFILE}-")
            for name in requested
        )
        if has_harness_tier:
            # The harness profile leads (it owns the behaviour); any extra
            # profile the caller asked for follows it.
            harness = [
                name for name in requested
                if name == prompts.HARNESS_PROFILE
                or name.startswith(f"{prompts.HARNESS_PROFILE}-")
            ]
            tiers = [
                name for name in harness
                if name.startswith(f"{prompts.HARNESS_PROFILE}-")
            ]
            # A tier already inherits the base profile: never compose both.
            harness = tiers or harness[:1]
            ordered = [*harness, *[name for name in requested if name not in harness]]
        else:
            ordered = [prompts.HARNESS_PROFILE, *requested]
        selected: List[str] = []
        for profile in ordered:
            if profile and profile not in selected:
                selected.append(profile)

        manager = self._manager()
        self.ensure_board()
        now = _now_iso()
        chat_id = str(uuid.uuid4())
        session_name = (str(name or "").strip() or prompts.session_name(text))[:160]
        budget = float(budget_cxjcoins or 0.0) or None

        message = Message(
            doc_id=str(uuid.uuid4()),
            role="user",
            content=prompts.session_brief(text),
            user=self.user,
            project_id=self.project_id,
            profiles=selected,
            created_at=now,
            updated_at=now,
            done=True,
            # The harness contract is instruction text, not project knowledge:
            # never feed it to the RAG index.
            disable_knowledge=True,
        )
        chat = Chat(
            id=chat_id,
            name=session_name,
            description=text[:4000],
            mode=prompts.HARNESS_MODE,
            board=prompts.HARNESS_BOARD,
            column=prompts.HARNESS_COLUMN,
            status="doing",
            project_id=self.project_id,
            owner_project_id=self.project_id,
            profiles=selected,
            llm_model=model or None,
            estimation_cost=budget,
            tags=[prompts.HARNESS_TAG],
            meta_data={
                prompts.HARNESS_META_KEY: {
                    "enabled": True,
                    "profile": prompts.HARNESS_PROFILE,
                    "engine": "chat",
                    "brief": "harness-v1",
                    "created_by": self.user,
                }
            },
            messages=[message],
            # Harness chats are flagged for auto-initialization so the chat
            # engine derives the name (and the organizer the board/column) on
            # the first turn, exactly like a chat created from the chat UI.
            auto_initialize=True,
            created_at=now,
            updated_at=now,
        )
        saved = manager.save_chat(chat=chat)
        return self.summary(saved or chat)

    # -- list -------------------------------------------------------------

    def list(self, *, limit: int = 50, phase: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List the project's harness sessions, newest first.

        Args:
            limit: Maximum number of sessions.
            phase: Optional phase filter (``running``, ``done``...).

        Returns:
            Session summaries.
        """
        manager = self._manager()
        try:
            chats = manager.list_chats()
        except Exception as ex:
            logger.warning("Harness session: cannot list chats: %s", ex)
            return []
        result = []
        for chat in chats:
            if not self.is_harness_chat(chat):
                continue
            summary = self.summary(chat)
            if phase and summary["phase"] != phase:
                continue
            result.append(summary)
            if limit and len(result) >= int(limit):
                break
        return result

    # -- detail -----------------------------------------------------------

    def _find(self, session_id: str) -> Any:
        """Load one harness chat or raise :class:`HarnessSessionError`."""
        chat_id = str(session_id or "").strip()
        if not chat_id:
            raise HarnessSessionError("A session id is required")
        try:
            chat = self._manager().find_by_id(chat_id)
        except Exception as ex:
            logger.warning("Harness session: cannot load '%s': %s", chat_id, ex)
            chat = None
        if chat is None:
            raise HarnessSessionError(f"Harness session '{chat_id}' not found")
        if not self.is_harness_chat(chat):
            raise HarnessSessionError(
                f"Chat '{chat_id}' is not a harness session"
            )
        return chat

    def get(self, session_id: str) -> Dict[str, Any]:
        """
        Return everything the harness view needs for one session.

        Args:
            session_id: Chat id of the session.

        Returns:
            ``{session, chat, metrics, workspace, diff, files, run, audit}``.
            Every computed block degrades to an empty value: a missing git
            repository or worktree never prevents opening the conversation.
        """
        chat = self._find(session_id)
        summary = self.summary(chat)
        messages = list(getattr(chat, "messages", None) or [])
        first = self._first_user_message(chat)
        last = self._last_message(chat)
        started_at = (
            getattr(first, "created_at", "")
            if first is not None
            else getattr(chat, "created_at", "")
        )
        finished_at = (
            getattr(last, "created_at", "")
            if last is not None
            else getattr(chat, "updated_at", "")
        )

        metrics = {
            "duration_seconds": _seconds_between(started_at, finished_at),
            "started_at": started_at,
            "finished_at": finished_at,
            "messages": len(messages),
            "tool_calls": sum(
                len(getattr(message, "tool_events", None) or [])
                for message in messages
            ),
            "errors": sum(
                1
                for message in messages
                for event in (getattr(message, "tool_events", None) or [])
                if getattr(event, "status", "") == "error"
            ),
            "input_tokens": sum(
                int(getattr(getattr(message, "cost_info", None), "input_tokens", 0) or 0)
                for message in messages
            ),
            "output_tokens": sum(
                int(getattr(getattr(message, "cost_info", None), "output_tokens", 0) or 0)
                for message in messages
            ),
        }

        workspace = self._workspace(chat)
        diff, diff_source = self._diff(chat, workspace, self._write_paths(messages))
        files = self._session_files(messages, diff)
        run = self._run(chat)
        audit = self._audit(chat)

        return {
            "session": summary,
            "chat": chat.model_dump(mode="json"),
            "metrics": metrics,
            "workspace": workspace,
            "diff": {**diff, "source": diff_source},
            "files": files,
            "run": run,
            "audit": audit,
        }

    def _workspace(self, chat: Any) -> Optional[Dict[str, Any]]:
        """Return the task worktree of the session, when isolation is on."""
        try:
            from codx.junior.task_workspace import TaskWorkspaceManager

            workspace = TaskWorkspaceManager(self.settings).find(chat)
            if workspace is None:
                return None
            payload = workspace.public()
            payload["git_dir"] = os.path.join(workspace.path, ".git")
            return payload
        except Exception as ex:
            logger.debug("Harness session: cannot read workspace: %s", ex)
            return None

    def _diff(
        self,
        chat: Any,
        workspace: Optional[Dict[str, Any]],
        tool_paths: Optional[Iterable[str]] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """
        Return the session branch diff and where it was read from.

        The run writes into the task worktree and commits there; the diff of
        that branch is the truthful "files changed" list. Without a worktree the
        turn runs in the project checkout, so the diff is restricted to the
        files the session's write tools actually named (never every unrelated
        uncommitted change of the checkout).
        """
        path = (workspace or {}).get("path") or ""
        base = (workspace or {}).get("base_ref") or ""
        source = "worktree" if path else "project"
        selected = [str(item) for item in (tool_paths or []) if str(item).strip()]
        if not path:
            path = str(getattr(self.settings, "abs_project_path", "") or "")
            if not selected:
                # Nothing was written: do not blame the checkout.
                return {
                    "files": [], "additions": 0, "deletions": 0,
                    "branch": "", "base_ref": "", "commit": "",
                }, source
        stats = _numstat(path, base or None, selected if not workspace else None)
        statuses = _name_status(path, base or None, selected if not workspace else None)
        # Untracked/new files do not appear in `git diff --numstat HEAD`.
        for file_path in _working_tree_files(path, selected if not workspace else None):
            if file_path not in stats:
                stats[file_path] = {"additions": 0, "deletions": 0}
                statuses.setdefault(file_path, "added" if not workspace else "modified")
        files = []
        for file_path, counters in sorted(stats.items()):
            files.append({
                "path": file_path,
                "additions": int(counters.get("additions") or 0),
                "deletions": int(counters.get("deletions") or 0),
                "status": statuses.get(file_path, "modified"),
            })
        return {
            "files": files,
            "additions": sum(item["additions"] for item in files),
            "deletions": sum(item["deletions"] for item in files),
            "branch": (workspace or {}).get("branch") or "",
            "base_ref": base,
            "commit": (workspace or {}).get("last_commit") or "",
        }, source

    @staticmethod
    def _request_paths(request: Any) -> List[str]:
        """Extract the file paths named by a tool request."""
        if not isinstance(request, dict):
            return []
        found: List[str] = []
        for key in _PATH_KEYS:
            value = request.get(key)
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
        changes = request.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    for key in _PATH_KEYS:
                        value = change.get(key)
                        if isinstance(value, str) and value.strip():
                            found.append(value.strip())
        return found

    def _write_paths(self, messages: List[Any]) -> List[str]:
        """Return the unique files named by successful write-tool calls."""
        found: List[str] = []
        for message in messages:
            for event in getattr(message, "tool_events", None) or []:
                if getattr(event, "tool", "") not in _WRITE_TOOLS:
                    continue
                if getattr(event, "status", "") == "error":
                    continue
                for file_path in self._request_paths(getattr(event, "request", None)):
                    if file_path not in found:
                        found.append(file_path)
        return found

    def _session_files(
        self, messages: List[Any], diff: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Return the files touched by the session.

        The branch diff wins (it carries the real line counts). A session that
        did not use a worktree falls back to the write tools' arguments, so the
        "files changed" block is never empty when the agent edited something.
        """
        known = {
            item["path"]: dict(item)
            for item in (diff.get("files") or [])
        }
        for message in messages:
            for event in getattr(message, "tool_events", None) or []:
                if getattr(event, "tool", "") not in _WRITE_TOOLS:
                    continue
                if getattr(event, "status", "") == "error":
                    continue
                for file_path in self._request_paths(getattr(event, "request", None)):
                    if file_path not in known:
                        known[file_path] = {
                            "path": file_path,
                            "additions": 0,
                            "deletions": 0,
                            "status": "modified",
                        }
        return list(known.values())

    def _run(self, chat: Any) -> Optional[Dict[str, Any]]:
        """Return the orchestration run attached to the chat, when any."""
        run_id = getattr(chat, "run_id", None)
        if not run_id:
            return None
        try:
            from codx.junior.orchestration.store import RunStore

            run = RunStore(self.codx_path).load(str(run_id))
            return {
                "run_id": run.run_id,
                "name": run.name,
                "objective": run.objective,
                "status": run.status,
                "progress": run.progress(),
                "counts": {
                    **run.counts(),
                    "total": len(run.steps),
                },
                "steps": [
                    {
                        "id": step.id,
                        "title": step.title,
                        "status": step.status,
                        "chat_id": step.chat_id,
                        "summary": (step.result.summary if step.result else ""),
                    }
                    for step in run.steps
                ],
            }
        except Exception as ex:
            logger.debug("Harness session: cannot read run '%s': %s", run_id, ex)
            return None

    def _audit(self, chat: Any) -> List[Dict[str, Any]]:
        """Return the audit journal entries of this session."""
        try:
            from codx.junior.harness.audit import HarnessAuditLog

            return HarnessAuditLog(self.codx_path).read(
                limit=100, chat_id=str(getattr(chat, "id", "") or "")
            )
        except Exception as ex:
            logger.debug("Harness session: cannot read the audit journal: %s", ex)
            return []

    # -- execution --------------------------------------------------------

    async def run_turn(self, session_id: str, engine_session: Any) -> None:
        """
        Execute the next turn of a session in the task's isolated worktree.

        The turn runs through :class:`~codx.junior.chat.chat_engine.ChatEngine`
        (the same engine every session call delegates to) with a clone of the
        project settings whose ``abs_project_path`` is the task worktree — the
        exact mechanism :class:`~codx.junior.orchestration.executor.StepExecutor`
        uses for run steps. The main checkout is never modified; the turn is
        committed on the task branch afterwards so the session detail can show a
        real diff and the Git view can review/merge it.

        Args:
            session_id: Chat id of the session.
            engine_session: The request ``CODXJuniorSession`` (event manager and
                user are reused; settings are cloned for the worktree).
        """
        chat = self._find(session_id)
        try:
            from codx.junior.task_workspace import TaskWorkspaceManager

            workspace_manager = TaskWorkspaceManager(self.settings)
            # `ensure` may create a git worktree (and symlink dependencies):
            # keep that synchronous work off the API event loop.
            workspace = await asyncio.to_thread(workspace_manager.ensure, chat)
        except Exception as ex:  # pragma: no cover - isolation is best effort
            logger.warning("Harness session: task workspace unavailable: %s", ex)
            workspace = None
            workspace_manager = None

        if workspace is None or workspace_manager is None:
            # No git isolation (non-git project / disabled): use the normal
            # session path, exactly like a chat turn.
            await engine_session.chat_with_project(chat_id=session_id)
            return

        try:
            from codx.junior.chat.chat_engine import ChatEngine

            run_settings = workspace_manager.settings_for(workspace, self.settings)
            engine = ChatEngine(
                settings=run_settings,
                event_manager=engine_session.event_manager,
                user=getattr(engine_session, "user", None),
            )
            run_chat, _documents = await engine.chat_with_project(chat=chat)
            chat = run_chat or chat
            # A completed turn is the delivery: move the card to Done so the
            # board agrees with the harness phase. A turn with an error stays
            # in Working; a follow-up reopens it explicitly.
            last = self._last_message(chat)
            if (
                last is not None
                and getattr(last, "role", "") == "assistant"
                and not getattr(last, "error", None)
                and str(getattr(last, "content", "") or "").strip()
            ):
                chat.status = "done"
                chat.column = "Done"
            # Mirror `ChatEngineActions.chat_with_project`: persist through the
            # project session too, so the final message/cost is stable before
            # the branch is committed.
            await engine_session.save_chat(chat)
        finally:
            # Commit whatever the turn produced, even after a failure: a dirty
            # worktree is reviewable, a lost change is not.
            result = await asyncio.to_thread(workspace_manager.commit, chat)
            if result.get("error"):
                logger.warning(
                    "Harness session: could not commit %s: %s",
                    workspace.branch,
                    result["error"],
                )

    # -- follow-up --------------------------------------------------------

    def prompt(
        self,
        session_id: str,
        content: str,
        *,
        profiles: Optional[Iterable[str]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Append a follow-up user message to the session.

        The API layer schedules the turn in the background with
        :meth:`run_turn`; this method only persists the message and reopens the
        card.

        Args:
            session_id: Chat id of the session.
            content: Message written by the user.
            profiles: Optional profile override for this message.
            model: Optional model override for the chat.

        Returns:
            The updated session detail.
        """
        from codx.junior.db import Message

        text = str(content or "").strip()
        if not text:
            raise HarnessSessionError("The message cannot be empty")
        chat = self._find(session_id)
        message = Message(
            doc_id=str(uuid.uuid4()),
            role="user",
            content=text,
            user=self.user,
            project_id=getattr(chat, "project_id", None) or self.project_id,
            profiles=[
                str(profile).strip()
                for profile in (profiles or [])
                if str(profile or "").strip()
            ],
            created_at=_now_iso(),
            updated_at=_now_iso(),
            done=True,
        )
        # A follow-up reopens the card on the board: the session is working
        # again until the new turn produces its report.
        chat.status = "doing"
        chat.column = prompts.HARNESS_COLUMN
        self._manager().add_message(chat=chat, message=message)
        if model:
            chat.llm_model = model
        self._manager().save_chat(chat=chat, chat_only=True)
        return self.get(session_id)
