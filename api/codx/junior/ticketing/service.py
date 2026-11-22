"""
Ticketing service: place, follow, answer and withdraw cross-project tickets.

The service is the single implementation behind the LLM tools
(``ticket_create`` / ``ticket_update`` / ``ticket_query`` /
``ticket_abandon``), the HTTP API (``/api/tickets``) and any future automation.
It owns three things:

1. **Who may be ticketed** — :meth:`TicketService.targets` resolves the projects
   this one can place tickets on: its **child projects**, **every linked
   project** (the legacy ``project_dependencies`` list, read-only links and
   read/write links) and the projects that **contain it** (its parents). A ticket
   is a request, not a direct edit, so a read-only link may receive one: the
   target owns the chat and decides what to do with the request.
2. **Where the ticket lives** — the ticket chat is stored by the TARGET project
   (``Chat.owner_project_id``) on its ``tickets`` board, so the target owns the
   queue and processes it with the tooling it already has (boards, profiles,
   autonomy, orchestration).
3. **The requester's ledger** — ``.codx/tickets/tickets.json`` in the SOURCE
   project keeps one entry per placed ticket (id + target + last known status),
   so the requester can list and re-open its requests without scanning other
   projects. The chat remains the source of truth: every read refreshes the
   ledger from it when the target project is reachable.

Ownership of the decisions
--------------------------
The two ends of a ticket have different powers, and the service enforces it:

==========================  ==========================  ======================
Action                      Source (requester)         Target (queue owner)
==========================  ==========================  ======================
comment                     yes                        yes
rename / kind / priority    yes                        yes
accept / decline / done     no (it is not its call)    yes
abandon                     yes (withdraws the ask)    no
==========================  ==========================  ======================

```mermaid
flowchart LR
    subgraph Source project
        T1[ticket_create] --> L[.codx/tickets/tickets.json]
    end
    subgraph Target project
        C[Chat mode=ticket<br/>board=tickets]
    end
    T1 -->|ChatManager of the target| C
    C -->|column = Inbox| Q[Ticket queue board]
    Q -->|accept / decline / done| C
    C -->|refresh on read| L
    L --> TQ[ticket_query]
```

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from codx.junior.chat_manager import ChatManager
from codx.junior.db import Chat, Message
from codx.junior.ticketing.model import (
    ChatTicket,
    MAX_TICKET_NAME_CHARS,
    TICKET_ACTION_ABANDONED,
    TICKET_ACTION_COMMENTED,
    TICKET_ACTION_PLACED,
    TICKET_ACTION_STATUS,
    TICKET_ACTION_UPDATED,
    TICKET_AUTONOMY_QUEUED,
    TICKET_BOARD,
    TICKET_BOARD_DESCRIPTION,
    TICKET_DEFAULT_KIND,
    TICKET_KINDS,
    TICKET_MODE,
    TICKET_RELATION_CHILD,
    TICKET_RELATION_LINKED,
    TICKET_RELATION_PARENT,
    TICKET_SIDE_SOURCE,
    TICKET_SIDE_TARGET,
    TICKET_SOURCE_STATUSES,
    TICKET_STATUSES,
    TICKET_STATUS_ABANDONED,
    TICKET_STATUS_OPEN,
    TICKET_TARGET_STATUSES,
    TICKET_TERMINAL_STATUSES,
    TicketAutonomy,
    TicketRecord,
    can_transition,
    column_for_status,
    normalize_kind,
    normalize_status,
    now_iso,
    status_for_column,
    ticket_columns,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from codx.junior.settings import CODXJuniorSettings

logger = logging.getLogger(__name__)

# Ledger location inside the SOURCE project's control plane.
TICKETS_FOLDER = "tickets"
TICKETS_LEDGER_FILE = "tickets.json"
LEDGER_VERSION = 1

# Default number of tickets a query returns.
DEFAULT_QUERY_LIMIT = 20
MAX_QUERY_LIMIT = 200


class TicketError(ValueError):
    """Raised when a ticket operation is not allowed or the input is invalid."""


def _clean(value: Any) -> str:
    """Return *value* as a stripped string (``None`` becomes an empty string)."""
    return str(value or "").strip()


def _write_json(file_path: str, payload: Any) -> None:
    """
    Write *payload* as JSON atomically (``tmp`` + ``os.replace``).

    Args:
        file_path: Destination file.
        payload: JSON-serialisable payload.
    """
    folder = os.path.dirname(file_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handler:
        json.dump(payload, handler, indent=2, ensure_ascii=False)
    os.replace(tmp_path, file_path)


def _read_json(file_path: str, default: Any = None) -> Any:
    """
    Read a JSON file, returning *default* when it is missing or invalid.

    Args:
        file_path: File to read.
        default: Value returned when the file cannot be read.

    Returns:
        The parsed payload or *default*.
    """
    if not file_path or not os.path.isfile(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as handler:
            return json.load(handler)
    except (OSError, ValueError) as ex:
        logger.warning("ticketing: cannot read '%s': %s", file_path, ex)
        return default


class TicketService:
    """
    Cross-project ticketing for one project.

    One instance is created per operation (tool call, HTTP request) and holds
    the project that is acting as ``self``; the other end of a ticket is always
    resolved explicitly, so the same code serves both "I place a ticket" and "a
    ticket was placed here".

    Attributes:
        settings: Settings of the project that is acting.
        chat_manager: Its :class:`~codx.junior.chat_manager.ChatManager`.
        user: Optional user/agent acting on behalf of the project.
    """

    def __init__(
        self,
        settings: "CODXJuniorSettings",
        chat_manager: Optional[ChatManager] = None,
        user: Optional[str] = None,
    ) -> None:
        """
        Initialise the service for one project.

        Args:
            settings: Settings of the acting project.
            chat_manager: Optional manager of that project (built on demand).
            user: Optional user/agent that places or answers tickets.

        Raises:
            TicketError: When the project has no control-plane path (``.codx``),
                because neither the ledger nor the ticket chats could be stored.
        """
        if settings is None:
            raise TicketError("ticketing requires the project settings")
        self.settings = settings
        self._chat_manager = chat_manager
        self.user = _clean(user) or None
        self._user_managers: Dict[str, ChatManager] = {}
        if not _clean(getattr(settings, "codx_path", "")):
            raise TicketError(
                "ticketing requires a project with a control plane (.codx path)"
            )

    # ------------------------------------------------------------------
    # Chat managers
    # ------------------------------------------------------------------

    def own_manager(self) -> ChatManager:
        """Return the :class:`ChatManager` of the acting project."""
        if self._chat_manager is not None:
            return self._chat_manager
        self._chat_manager = ChatManager(settings=self.settings)
        return self._chat_manager

    def manager_for(self, project: "CODXJuniorSettings") -> ChatManager:
        """
        Return a :class:`ChatManager` bound to *project* (cached per ``.codx``).

        The manager is built from the resolved settings instead of being looked
        up in the global catalogue: a ticket must land in the right project even
        when the target is not registered in it.

        Args:
            project: Settings of the project to manage.

        Returns:
            The manager of that project.
        """
        key = _clean(getattr(project, "codx_path", ""))
        if not key:
            raise TicketError("the target project has no control plane path")
        manager = self._user_managers.get(key)
        if manager is None:
            is_self = _clean(getattr(project, "project_id", "")) == _clean(
                getattr(self.settings, "project_id", "")
            )
            manager = (
                ChatManager(settings=project, event_manager=self.own_manager().event_manager)
                if not is_self
                else self.own_manager()
            )
            self._user_managers[key] = manager
        return manager

    # ------------------------------------------------------------------
    # Targets
    # ------------------------------------------------------------------

    def _ancestor_projects(self) -> List["CODXJuniorSettings"]:
        """
        Return the projects whose control plane contains this project.

        A child project often needs to ask the parent for a change, and the
        parent is linked to it by the same tree relation that makes it a parent.
        The ancestors are resolved from disk (one ``.codx/project.json`` per
        containing folder), never from the global catalogue.

        Returns:
            The ancestor project settings, nearest first. Missing or broken
            manifests are skipped so a ticket is never blocked by one folder.
        """
        from codx.junior.settings import CODXJuniorSettings

        root = _clean(getattr(self.settings, "abs_project_path", "")) or os.path.dirname(
            _clean(getattr(self.settings, "codx_path", ""))
        )
        if not root:
            return []
        try:
            current = os.path.dirname(os.path.normpath(os.path.abspath(root)))
        except (OSError, ValueError):  # pragma: no cover - defensive
            return []

        projects: List["CODXJuniorSettings"] = []
        visited = set()
        while current and current not in visited:
            visited.add(current)
            project_file = os.path.join(current, ".codx", "project.json")
            if os.path.isfile(project_file):
                try:
                    projects.append(
                        CODXJuniorSettings.from_project_file(project_file)
                    )
                except Exception as ex:  # pragma: no cover - never break a ticket
                    logger.warning(
                        "ticketing: cannot read ancestor project '%s': %s",
                        project_file,
                        ex,
                    )
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        return projects

    def _target_candidates(
        self,
    ) -> List[Tuple["CODXJuniorSettings", str, bool]]:
        """
        Return every project this one may place a ticket on, with its relation.

        A ticket is a request, not a direct edit, so the file-level write
        permission of a link does **not** decide whether it can receive one. The
        candidates are, in priority order:

        * **child** projects nested anywhere in this project's tree;
        * **linked** projects from every dependency list (legacy
          ``project_dependencies``, read-only ``read_project_dependencies`` and
          read/write ``read_write_project_dependencies``);
        * **parent** projects, the ones whose control plane contains this one.

        Returns:
            ``(settings, relationship, writable)`` tuples, deduplicated by
            control-plane path and without the current project. ``writable`` is
            the file-tool permission of the relation, not a ticket restriction.
        """
        own_path = _clean(getattr(self.settings, "codx_path", ""))
        candidates: List[Tuple["CODXJuniorSettings", str, bool]] = []
        seen = {own_path}

        def _add(project: Any, relationship: str, writable: bool) -> None:
            path = _clean(getattr(project, "codx_path", ""))
            if not path or path in seen:
                return
            seen.add(path)
            candidates.append((project, relationship, bool(writable)))

        try:
            for project in self.settings.get_sub_projects() or []:
                _add(project, TICKET_RELATION_CHILD, True)
        except Exception as ex:  # pragma: no cover - discovery must never break
            logger.warning("ticketing: cannot read child projects: %s", ex)

        write_paths = set()
        try:
            write_paths = {
                _clean(getattr(project, "codx_path", ""))
                for project in self.settings.get_read_write_dependency_projects() or []
            }
        except Exception as ex:  # pragma: no cover - detection must never break
            logger.warning(
                "ticketing: cannot read read/write linked projects: %s", ex
            )

        try:
            for project in self.settings.get_dependency_projects() or []:
                _add(
                    project,
                    TICKET_RELATION_LINKED,
                    _clean(getattr(project, "codx_path", "")) in write_paths,
                )
        except Exception as ex:  # pragma: no cover - detection must never break
            logger.warning("ticketing: cannot read linked projects: %s", ex)

        try:
            for project in self._ancestor_projects() or []:
                _add(project, TICKET_RELATION_PARENT, False)
        except Exception as ex:  # pragma: no cover - detection must never break
            logger.warning("ticketing: cannot read parent projects: %s", ex)

        return candidates

    def targets(self) -> List[Dict[str, Any]]:
        """
        Return the projects this one may place a ticket on.

        Allowed targets are the **child projects** (every
        ``.codx/project.json`` nested inside this project), **every linked
        project** (legacy, read-only or read/write dependency) and the **parent
        projects** that contain this one. A ticket is a request, so a read-only
        link may receive one even though the file tools cannot write there.

        Returns:
            A list of ``{project_id, project_name, relationship, write, path}``
            dicts, deduplicated by control-plane path and without the current
            project. ``relationship`` is ``child``, ``linked`` or ``parent``;
            ``write`` reports the file-tool permission of the relation (it does
            not restrict ticketing).
        """
        result: List[Dict[str, Any]] = []
        for project, relationship, writable in self._target_candidates():
            path = _clean(getattr(project, "codx_path", ""))
            result.append(
                {
                    "project_id": _clean(getattr(project, "project_id", "")) or None,
                    "project_name": _clean(getattr(project, "project_name", ""))
                    or os.path.basename(
                        _clean(getattr(project, "abs_project_path", "")) or path
                    ),
                    "relationship": relationship,
                    "write": writable,
                    "path": _clean(getattr(project, "abs_project_path", "")) or None,
                }
            )
        return result

    def target_names(self) -> List[str]:
        """Return the names/ids of the allowed targets (for error messages)."""
        names: List[str] = []
        for target in self.targets():
            label = target["project_name"]
            if target["project_id"]:
                label = f"{label} ({target['project_id']})"
            names.append(label)
        return names

    def resolve_target(self, reference: Optional[str]) -> "CODXJuniorSettings":
        """
        Resolve a ticket target from its name, id or folder name.

        Args:
            reference: Target project name, id or folder name (case
                insensitive).

        Returns:
            The settings of the target project.

        Raises:
            TicketError: When no target was given, it is the current project, or
                it is not a child, linked or parent project of this one (the
                message lists the valid targets).
        """
        wanted = _clean(reference).lower()
        if not wanted:
            raise TicketError(
                "a target project is required; valid targets: "
                + (
                    ", ".join(self.target_names())
                    or "none (no child, linked or parent project)"
                )
            )

        own_names = {
            _clean(getattr(self.settings, "project_id", "")).lower(),
            _clean(getattr(self.settings, "project_name", "")).lower(),
            os.path.basename(_clean(getattr(self.settings, "abs_project_path", ""))).lower(),
        }
        own_names.discard("")
        if wanted in own_names:
            raise TicketError(
                "a project cannot place a ticket on itself: handle the change "
                "directly instead"
            )

        for candidate, _relationship, _writable in self._target_candidates():
            names = {
                _clean(getattr(candidate, "project_id", "")).lower(),
                _clean(getattr(candidate, "project_name", "")).lower(),
                os.path.basename(
                    _clean(getattr(candidate, "abs_project_path", "")) or ""
                ).lower(),
            }
            names.discard("")
            if wanted in names:
                return candidate

        raise TicketError(
            f"unknown ticket target '{reference}'; valid targets: "
            + (", ".join(self.target_names()) or "none")
        )

    def _target_settings(self, available: List[Dict[str, Any]]) -> List["CODXJuniorSettings"]:
        """
        Return the settings of the allowed targets, in ``targets()`` order.

        Args:
            available: Output of :meth:`targets`.

        Returns:
            Matching settings, resolved from the same candidates ``targets()``
            uses (children, every linked project and parents).
        """
        wanted_paths = {
            _clean(target.get("path")) for target in available if target.get("path")
        }
        wanted_ids = {
            _clean(target.get("project_id"))
            for target in available
            if target.get("project_id")
        }
        if not wanted_paths and not wanted_ids:
            return []
        projects: List["CODXJuniorSettings"] = []
        for project, _relationship, _writable in self._target_candidates():
            path = _clean(getattr(project, "abs_project_path", ""))
            project_id = _clean(getattr(project, "project_id", ""))
            if path in wanted_paths or (project_id and project_id in wanted_ids):
                projects.append(project)
        return projects

    # ------------------------------------------------------------------
    # Ledger (requester side)
    # ------------------------------------------------------------------

    @property
    def ledger_path(self) -> str:
        """Absolute path of the requester's ticket ledger."""
        return os.path.join(
            _clean(getattr(self.settings, "codx_path", "")),
            TICKETS_FOLDER,
            TICKETS_LEDGER_FILE,
        )

    def load_ledger(self) -> List[Dict[str, Any]]:
        """
        Read the requester's ledger.

        Returns:
            The list of ledger entries (``[]`` when the project never placed a
            ticket, or the file is unreadable).
        """
        payload = _read_json(self.ledger_path, default=None)
        entries = payload.get("tickets") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    def save_ledger(self, entries: List[Dict[str, Any]]) -> None:
        """
        Persist the requester's ledger.

        Args:
            entries: Ledger entries to store.
        """
        _write_json(
            self.ledger_path,
            {"version": LEDGER_VERSION, "tickets": list(entries or [])},
        )

    def ledger_entry(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the ledger entry of *ticket_id*, when the project placed it.

        Args:
            ticket_id: Ticket id (== chat id).

        Returns:
            The entry, or ``None`` when it is not in the ledger.
        """
        wanted = _clean(ticket_id)
        for entry in self.load_ledger():
            if _clean(entry.get("ticket_id") or entry.get("chat_id")) == wanted:
                return entry
        return None

    def upsert_ledger(self, record: TicketRecord) -> None:
        """
        Insert or replace the ledger entry of *record*.

        Args:
            record: The record to store (its id identifies the entry).
        """
        wanted = _clean(record.ticket_id)
        if not wanted:
            return
        entries = [
            entry
            for entry in self.load_ledger()
            if _clean(entry.get("ticket_id") or entry.get("chat_id")) != wanted
        ]
        entries.append(record.to_ledger())
        entries.sort(key=lambda entry: _clean(entry.get("created_at")), reverse=True)
        self.save_ledger(entries)

    # ------------------------------------------------------------------
    # Ticket board
    # ------------------------------------------------------------------

    def ensure_ticket_board(self, manager: ChatManager) -> None:
        """
        Create the ``tickets`` board (with its status columns) when missing.

        The board is the queue the target sees, so it must exist in the target's
        kanban catalogue before the first ticket lands there; otherwise the
        ticket file would live in a folder the UI does not render.

        Args:
            manager: Chat manager of the project that owns the queue.
        """
        store = manager.kanban_store
        kanban = store.load() or {}
        boards = kanban.get("boards")
        if not isinstance(boards, dict):
            boards = {}
        if TICKET_BOARD.lower() in {str(title).strip().lower() for title in boards}:
            return

        import uuid

        boards[TICKET_BOARD] = {
            "id": str(uuid.uuid4()),
            "title": TICKET_BOARD,
            "description": TICKET_BOARD_DESCRIPTION,
            "branch": "",
            "type": "kanban",
            "parent_id": None,
            "columns": [
                {"id": str(uuid.uuid4()), "title": title, "color": ""}
                for title in ticket_columns()
            ],
            "active": False,
            "last_update": now_iso(),
        }
        kanban["boards"] = boards
        kanban.setdefault("version", 2)
        kanban.setdefault("tags", {})
        store.save(kanban)

        for column in ticket_columns():
            try:
                os.makedirs(
                    os.path.join(manager.chat_path, TICKET_BOARD, column),
                    exist_ok=True,
                )
            except OSError as ex:  # pragma: no cover - defensive
                logger.warning(
                    "ticketing: cannot create folder '%s/%s': %s", TICKET_BOARD, column, ex
                )
        logger.info(
            "ticketing: board '%s' created for project '%s'",
            TICKET_BOARD,
            _clean(getattr(manager.settings, "project_name", "")),
        )

    # ------------------------------------------------------------------
    # Ticket chat access
    # ------------------------------------------------------------------

    @staticmethod
    def is_ticket(chat: Optional[Chat]) -> bool:
        """
        Tell whether *chat* is a ticket conversation.

        Args:
            chat: Chat to inspect.

        Returns:
            True when the chat carries its :class:`ChatTicket` payload.
        """
        return chat is not None and getattr(chat, "ticket", None) is not None

    def _sync_status_from_column(
        self, chat: Chat, manager: ChatManager, persist: bool = True
    ) -> bool:
        """
        Reconcile ``chat.ticket.status`` with the column the card lives in.

        The column is what a human moves on the board, so it wins: a target that
        drags the card to ``Accepted`` has accepted the ticket. Columns that are
        not ticket columns (the target moved the card to a board of its own) leave
        the stored status untouched.

        Args:
            chat: The ticket chat (mutated in place).
            manager: Manager owning the chat.
            persist: Write the chat back when the status changed.

        Returns:
            True when the status was updated.
        """
        ticket = getattr(chat, "ticket", None)
        if ticket is None:
            return False
        column_status = status_for_column(getattr(chat, "column", ""))
        if not column_status or column_status == ticket.status:
            return False
        previous = ticket.status
        ticket.status = column_status
        ticket.append_event(
            action=TICKET_ACTION_STATUS,
            project_id=ticket.target_project_id,
            project_name=ticket.target_project_name,
            user=self.user,
            note=f"moved to column '{getattr(chat, 'column', '')}'",
            from_status=previous,
            status=column_status,
        )
        if persist:
            try:
                manager.save_chat(chat=chat)
            except Exception as ex:  # pragma: no cover - never break a read
                logger.warning(
                    "ticketing: cannot persist the status of ticket '%s': %s",
                    getattr(chat, "id", ""),
                    ex,
                )
        return True

    def _own_ticket_chats(self, limit: Optional[int] = None) -> List[Chat]:
        """
        Return the tickets stored in THIS project (the incoming queue).

        The board folder is resolved through the kanban catalogue and every chat
        file of it is read, so a ticket is found even when the derived chat index
        does not know the ``ticket`` field yet.

        Args:
            limit: Optional maximum number of chats (newest first).

        Returns:
            Ticket chats sorted by ticket update time (newest first).
        """
        manager = self.own_manager()
        try:
            board_folder, _column = manager.kanban_store.resolve_folders(
                board=TICKET_BOARD, column=None
            )
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("ticketing: cannot resolve the tickets folder: %s", ex)
            return []

        base = os.path.join(manager.chat_path, board_folder)
        if not os.path.isdir(base):
            return []

        chats: List[Chat] = []
        for root, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(".json"):
                    continue
                file_path = os.path.join(root, name)
                try:
                    chat = manager.load_chat_from_path(
                        chat_file=file_path, chat_only=False
                    )
                except (OSError, ValueError, KeyError) as ex:
                    logger.warning(
                        "ticketing: cannot read ticket file '%s': %s", file_path, ex
                    )
                    continue
                if self.is_ticket(chat):
                    chats.append(chat)

        chats.sort(
            key=lambda chat: _clean(getattr(chat.ticket, "updated_at", "")),
            reverse=True,
        )
        if limit and limit > 0:
            return chats[:limit]
        return chats

    def _resolve_ticket_chat(
        self, ticket_id: str
    ) -> Tuple[Optional[Chat], Optional[ChatManager], Optional["CODXJuniorSettings"]]:
        """
        Load a ticket chat and the project that stores it.

        The ledger is tried first (it names the target project), then this
        project's own ticket queue (the ticket was placed here). A ticket placed
        by another project and NOT in this project's ledger is invisible on
        purpose: the requester owns the reference.

        Args:
            ticket_id: Ticket id (== chat id).

        Returns:
            ``(chat, manager, target_settings)``; ``(None, ...)`` when the ticket
            does not exist any more (the ledger entry is then returned as-is).
        """
        wanted = _clean(ticket_id)
        if not wanted:
            raise TicketError("a ticket id is required")

        entry = self.ledger_entry(wanted)
        if entry is not None:
            target_reference = _clean(
                entry.get("target_project_id") or entry.get("target_project_name")
            )
            try:
                target = self.resolve_target(target_reference)
            except TicketError as ex:
                logger.info(
                    "ticketing: target '%s' of ticket '%s' is not reachable: %s",
                    target_reference,
                    wanted,
                    ex,
                )
                return None, None, None
            manager = self.manager_for(target)
            chat = manager.find_by_id(wanted)
            if chat is not None and not self.is_ticket(chat):
                logger.warning(
                    "ticketing: chat '%s' is not a ticket, ignoring the ledger entry",
                    wanted,
                )
                return None, None, None
            return chat, manager, target

        manager = self.own_manager()
        chat = manager.find_by_id(wanted)
        if chat is not None and self.is_ticket(chat):
            chat.owner_project_id = _clean(getattr(self.settings, "project_id", "")) or None
            return chat, manager, self.settings
        return None, None, None

    def _assert_may_access(self, chat: Chat, manager: ChatManager) -> str:
        """
        Return the side of the caller, or refuse the access.

        Args:
            chat: The ticket chat.
            manager: Manager owning the chat.

        Returns:
            :data:`TICKET_SIDE_TARGET` or :data:`TICKET_SIDE_SOURCE`.

        Raises:
            TicketError: When the caller is neither the source nor the target.
        """
        project_id = _clean(getattr(self.settings, "project_id", ""))
        side = getattr(chat.ticket, "side_of", lambda _pid: None)(project_id)
        if side is None:
            raise TicketError(
                f"ticket '{_clean(chat.id)}' belongs to projects "
                f"'{_clean(chat.ticket.source_project_id)}' -> "
                f"'{_clean(chat.ticket.target_project_id)}'; this project has no access"
            )
        return side

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        target: Optional[str],
        name: str,
        description: str,
        kind: Optional[str] = None,
        priority: Optional[str] = None,
        reference: Optional[str] = None,
        profiles: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        content: Optional[str] = None,
        autonomous: bool = False,
        autonomy_profile: Optional[str] = None,
        auto_accept: bool = False,
    ) -> TicketRecord:
        """
        Place a ticket on another project and return its id.

        Args:
            target: Target project (name, id or folder) — must be a child,
                a linked project (read-only or read/write) or a parent project
                (see :meth:`targets`).
            name: Short title of the request.
            description: Complete request (context, expected behaviour,
                acceptance criteria). This is what the target reads.
            kind: ``bug`` / ``feature`` / ``change`` / ``question``.
            priority: Optional priority hint (``high`` / ``medium`` / ``low``).
            reference: Optional id of the request in THIS project.
            profiles: Optional profiles the target may use (a suggestion).
            files: Optional files the request refers to, in the TARGET project.
            tags: Optional tags for triage.
            assignees: Optional usernames of the target project.
            content: First message; defaults to the description.
            autonomous: Ask the target to work the ticket autonomously. The
                target still owns the validation gate; this only enables the
                queue so the worker picks it up.
            autonomy_profile: Optional profile (agent) hint for the run.
            auto_accept: Let the autonomous runtime accept the ticket on its
                first run. Defaults to False: the target project accepts the
                request (card move or `ticket_autonomy` enable/queue) before any
                code runs, which is the safe gate for cross-project requests.
                This is an internal/target-side flag: the source-facing
                ``ticket_create`` tool and ``POST /api/tickets`` never expose it,
                so a requester cannot bypass the target's consent.

        Returns:
            The created :class:`TicketRecord` (its ``ticket_id`` is the id to
            quote in every later call).

        Raises:
            TicketError: When the target or the data is invalid.
        """
        title = _clean(name)
        body = _clean(description)
        if not title:
            raise TicketError("a ticket requires a 'name'")
        if not body:
            raise TicketError("a ticket requires a 'description'")
        if len(title) > MAX_TICKET_NAME_CHARS:
            title = title[:MAX_TICKET_NAME_CHARS]

        resolved_kind = normalize_kind(kind) or TICKET_DEFAULT_KIND
        target_settings = self.resolve_target(target)
        manager = self.manager_for(target_settings)
        self.ensure_ticket_board(manager)

        source_project_id = _clean(getattr(self.settings, "project_id", "")) or None
        target_project_id = _clean(getattr(target_settings, "project_id", "")) or None
        source_name = _clean(getattr(self.settings, "project_name", "")) or source_project_id
        target_name = _clean(getattr(target_settings, "project_name", "")) or target_project_id

        chat_id = None
        ticket = ChatTicket(
            source_project_id=source_project_id,
            source_project_name=source_name,
            target_project_id=target_project_id,
            target_project_name=target_name,
            status=TICKET_STATUS_OPEN,
            kind=resolved_kind,
            reference=_clean(reference) or None,
            requested_by=self.user,
        )
        # The ticket id is the chat id: one identity, usable as a chat id.
        chat_id = str(uuid.uuid4())
        ticket.id = chat_id
        ticket.append_event(
            action=TICKET_ACTION_PLACED,
            project_id=source_project_id,
            project_name=source_name,
            user=self.user,
            note=f"ticket placed on '{target_name}'",
            status=TICKET_STATUS_OPEN,
        )
        if autonomous:
            # The requester can only ask for autonomy; the target owns the
            # profile, the validation gate and the decision to run it.
            ticket.autonomy = TicketAutonomy(
                enabled=True,
                profile=_clean(autonomy_profile) or None,
                auto_accept=bool(auto_accept),
                state=TICKET_AUTONOMY_QUEUED,
                queued_at=now_iso(),
                requested_by=self.user,
                updated_at=now_iso(),
            )
            ticket.append_event(
                action=TICKET_ACTION_UPDATED,
                project_id=source_project_id,
                project_name=source_name,
                user=self.user,
                note="autonomous work requested by the requester",
                status=TICKET_STATUS_OPEN,
            )

        message = Message(
            doc_id=str(uuid.uuid4()),
            role="user",
            content=_clean(content) or body,
            user=self.user,
            project_id=target_project_id,
            created_at=now_iso(),
            updated_at=now_iso(),
            done=True,
        )

        chat = Chat(
            id=chat_id,
            name=title,
            description=body,
            mode=TICKET_MODE,
            board=TICKET_BOARD,
            column=column_for_status(TICKET_STATUS_OPEN),
            status="todo",
            project_id=target_project_id,
            owner_project_id=target_project_id,
            priority=_clean(priority) or None,
            profiles=[_clean(profile) for profile in (profiles or []) if _clean(profile)],
            tags=[_clean(tag) for tag in (tags or []) if _clean(tag)],
            file_list=[_clean(path) for path in (files or []) if _clean(path)],
            assignees=[_clean(user) for user in (assignees or []) if _clean(user)],
            ticket=ticket,
            messages=[message],
            created_at=now_iso(),
            updated_at=now_iso(),
        )

        saved = manager.save_chat(chat=chat)
        record = TicketRecord.from_chat(saved, direction=TICKET_SIDE_SOURCE)
        self.upsert_ledger(record)

        logger.info(
            "ticketing: ticket '%s' placed on '%s' by '%s' (kind=%s)",
            record.ticket_id,
            target_name,
            source_name,
            resolved_kind,
        )
        return record

    # ------------------------------------------------------------------
    # Update / comment
    # ------------------------------------------------------------------

    def update(
        self,
        ticket_id: str,
        comment: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        priority: Optional[str] = None,
        name: Optional[str] = None,
        profiles: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
        note: Optional[str] = None,
    ) -> TicketRecord:
        """
        Update a ticket: comment it, change its metadata or move its status.

        **Commenting** appends a message to the ticket conversation, which is the
        channel between the two projects. **Status** changes are role-checked:
        ``accepted``/``declined``/``done`` belong to the target project and
        ``abandoned`` to the requester (use :meth:`abandon`).

        Args:
            ticket_id: Ticket id (== chat id).
            comment: Message appended to the conversation.
            status: New ticket status (validated against the allowed transitions).
            kind: New kind.
            priority: New priority.
            name: New title.
            profiles: Profiles the target should use (replaces the list).
            assignees: Usernames of the target project (replaces the list).
            note: Optional reason stored in the ticket history.

        Returns:
            The updated :class:`TicketRecord` (``direction`` states the side).

        Raises:
            TicketError: When the ticket does not exist, the caller has no access,
                the transition is not allowed or the caller is not allowed to make
                it.
        """
        chat, manager, _target = self._resolve_ticket_chat(ticket_id)
        if chat is None or manager is None:
            raise TicketError(
                f"ticket '{_clean(ticket_id)}' not found "
                "(it may have been deleted in the target project)"
            )
        side = self._assert_may_access(chat, manager)
        self._sync_status_from_column(chat=chat, manager=manager)

        ticket = chat.ticket
        changed = False
        project_id = _clean(getattr(self.settings, "project_id", "")) or None
        project_name = _clean(getattr(self.settings, "project_name", "")) or project_id

        wanted_status = normalize_status(status)
        if wanted_status:
            previous = ticket.status
            if previous in TICKET_TERMINAL_STATUSES and wanted_status != previous:
                raise TicketError(
                    f"ticket '{ticket.id}' is {previous} and can no longer change to "
                    f"'{wanted_status}'"
                )
            if not can_transition(previous, wanted_status):
                raise TicketError(
                    f"ticket '{ticket.id}' cannot move from '{previous}' to "
                    f"'{wanted_status}'"
                )
            if wanted_status in TICKET_SOURCE_STATUSES:
                raise TicketError(
                    "abandoning a ticket is done with ticket_abandon "
                    "(the requester withdraws its own request)"
                )
            if wanted_status in TICKET_TARGET_STATUSES and side != TICKET_SIDE_TARGET:
                raise TicketError(
                    f"only the target project can set '{wanted_status}'; "
                    f"'{chat.ticket.target_project_name or chat.ticket.target_project_id}' "
                    "owns the ticket queue"
                )
            ticket.status = wanted_status
            chat.column = column_for_status(wanted_status)
            if wanted_status == "done":
                chat.status = "done"
            ticket.append_event(
                action=TICKET_ACTION_STATUS,
                project_id=project_id,
                project_name=project_name,
                user=self.user,
                note=note,
                from_status=previous,
                status=wanted_status,
            )
            changed = True

        wanted_kind = normalize_kind(kind)
        if wanted_kind and wanted_kind != ticket.kind:
            ticket.kind = wanted_kind
            changed = True

        if priority is not None:
            clean_priority = _clean(priority).lower() or None
            if clean_priority != (chat.priority or None):
                chat.priority = clean_priority
                changed = True

        if name is not None and _clean(name) and _clean(name) != chat.name:
            chat.name = _clean(name)[:MAX_TICKET_NAME_CHARS]
            changed = True

        if profiles is not None:
            chat.profiles = [_clean(profile) for profile in profiles if _clean(profile)]
            changed = True

        if assignees is not None:
            chat.assignees = [_clean(user) for user in assignees if _clean(user)]
            changed = True

        if changed:
            ticket.append_event(
                action=TICKET_ACTION_UPDATED,
                project_id=project_id,
                project_name=project_name,
                user=self.user,
                note=note,
                status=ticket.status,
            )

        text = _clean(comment)
        if text:
            chat.messages.append(
                Message(
                    doc_id=str(uuid.uuid4()),
                    role="user",
                    content=text,
                    user=self.user,
                    project_id=chat.project_id,
                    created_at=now_iso(),
                    updated_at=now_iso(),
                    done=True,
                )
            )
            ticket.append_event(
                action=TICKET_ACTION_COMMENTED,
                project_id=project_id,
                project_name=project_name,
                user=self.user,
                note=text[:280],
                status=ticket.status,
            )
            changed = True

        if not changed:
            raise TicketError(
                "nothing to update: send a comment, a status, a kind, a priority, "
                "a name, profiles or assignees"
            )

        saved = manager.save_chat(chat=chat)
        record = TicketRecord.from_chat(
            saved,
            direction=TICKET_SIDE_SOURCE if side == TICKET_SIDE_SOURCE else TICKET_SIDE_TARGET,
        )
        if side == TICKET_SIDE_SOURCE:
            self.upsert_ledger(record)
        else:
            self._refresh_ledger_of_source(saved)
        return record

    def abandon(
        self,
        ticket_id: str,
        reason: Optional[str] = None,
    ) -> TicketRecord:
        """
        Withdraw a ticket placed by this project.

        The ticket is not deleted: it moves to the ``Abandoned`` column of the
        target's board and both sides keep the conversation, so the target can
        see the request was called off. Only the requesting project may abandon.

        Args:
            ticket_id: Ticket id (== chat id).
            reason: Optional reason stored in the ticket history and the chat.

        Returns:
            The updated :class:`TicketRecord`.

        Raises:
            TicketError: When the ticket does not exist, this project is not the
                requester, or the ticket is already closed.
        """
        chat, manager, _target = self._resolve_ticket_chat(ticket_id)
        if chat is None or manager is None:
            raise TicketError(
                f"ticket '{_clean(ticket_id)}' not found "
                "(it may have been deleted in the target project)"
            )
        side = self._assert_may_access(chat, manager)
        if side != TICKET_SIDE_SOURCE:
            raise TicketError(
                f"only the requesting project can abandon a ticket; "
                f"'{_clean(chat.ticket.source_project_name or chat.ticket.source_project_id)}' "
                "placed it (the target declines or closes it instead)"
            )

        self._sync_status_from_column(chat=chat, manager=manager)
        ticket = chat.ticket
        if ticket.status in TICKET_TERMINAL_STATUSES:
            raise TicketError(
                f"ticket '{ticket.id}' is already {ticket.status}; nothing to abandon"
            )

        project_id = _clean(getattr(self.settings, "project_id", "")) or None
        project_name = _clean(getattr(self.settings, "project_name", "")) or project_id
        clean_reason = _clean(reason) or None

        previous = ticket.status
        ticket.status = TICKET_STATUS_ABANDONED
        chat.column = column_for_status(TICKET_STATUS_ABANDONED)
        ticket.append_event(
            action=TICKET_ACTION_ABANDONED,
            project_id=project_id,
            project_name=project_name,
            user=self.user,
            note=clean_reason,
            from_status=previous,
            status=TICKET_STATUS_ABANDONED,
        )
        chat.messages.append(
            Message(
                doc_id=str(uuid.uuid4()),
                role="user",
                content=(
                    "Ticket abandoned by "
                    f"{project_name}" + (f": {clean_reason}" if clean_reason else "")
                ),
                user=self.user,
                project_id=chat.project_id,
                created_at=now_iso(),
                updated_at=now_iso(),
                done=True,
            )
        )

        saved = manager.save_chat(chat=chat)
        record = TicketRecord.from_chat(saved, direction=TICKET_SIDE_SOURCE)
        self.upsert_ledger(record)
        logger.info(
            "ticketing: ticket '%s' abandoned by '%s'", record.ticket_id, project_name
        )
        return record

    def _refresh_ledger_of_source(self, chat: Chat) -> None:
        """
        Push the new status of a ticket into the requester's ledger.

        The target project cannot write the requester's ledger (it is another
        project's control plane), so this is best effort: the ledger entry is
        only refreshed when the requester is the current project.

        Args:
            chat: The ticket chat that just changed.
        """
        ticket = getattr(chat, "ticket", None)
        if ticket is None:
            return
        if _clean(ticket.source_project_id) != _clean(
            getattr(self.settings, "project_id", "")
        ):
            return
        self.upsert_ledger(TicketRecord.from_chat(chat, direction=TICKET_SIDE_SOURCE))

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        ticket_id: Optional[str] = None,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        target: Optional[str] = None,
        limit: Optional[int] = None,
        include_closed: bool = True,
    ) -> List[TicketRecord]:
        """
        List tickets, or return one of them.

        Args:
            ticket_id: When given, only that ticket is returned (the list is
                empty when it does not exist).
            direction: ``outgoing`` (placed by this project), ``incoming``
                (placed here) or ``all`` (default).
            status: Only tickets in this status.
            target: Only tickets whose target project matches this reference.
            limit: Maximum number of records (default
                :data:`DEFAULT_QUERY_LIMIT`).
            include_closed: Include ``done``/``abandoned`` tickets (default True).

        Returns:
            Records sorted by ticket update time (newest first).

        Raises:
            TicketError: When ``direction`` or ``status`` is not a known value.
        """
        wanted_status = normalize_status(status)
        wanted_direction = _clean(direction).lower() or "all"
        if wanted_direction not in (TICKET_SIDE_SOURCE, TICKET_SIDE_TARGET, "all"):
            raise TicketError(
                f"unknown direction '{direction}' "
                f"(valid: {TICKET_SIDE_SOURCE}, {TICKET_SIDE_TARGET}, all)"
            )
        max_records = max(1, min(int(limit or DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT))

        if _clean(ticket_id):
            chat, manager, _target = self._resolve_ticket_chat(ticket_id)
            if chat is None or manager is None:
                entry = self.ledger_entry(ticket_id)
                if entry is None:
                    return []
                return [TicketRecord.from_ledger(entry, direction=TICKET_SIDE_SOURCE)]
            side = self._assert_may_access(chat, manager)
            self._sync_status_from_column(chat=chat, manager=manager)
            record = TicketRecord.from_chat(chat, direction=side)
            if side == TICKET_SIDE_SOURCE:
                self.upsert_ledger(record)
            return [record]

        records: List[TicketRecord] = []

        if wanted_direction in (TICKET_SIDE_SOURCE, "all"):
            records.extend(self._outgoing_records())

        if wanted_direction in (TICKET_SIDE_TARGET, "all"):
            records.extend(self._incoming_records())

        wanted_target = _clean(target).lower()
        filtered: List[TicketRecord] = []
        for record in records:
            if wanted_status and record.status != wanted_status:
                continue
            if not include_closed and record.status in TICKET_TERMINAL_STATUSES:
                continue
            if wanted_target:
                names = {
                    _clean(record.target_project_id).lower(),
                    _clean(record.target_project_name).lower(),
                }
                names.discard("")
                if wanted_target not in names:
                    continue
            filtered.append(record)

        filtered.sort(key=lambda record: _clean(record.updated_at), reverse=True)
        return filtered[:max_records]

    def _outgoing_records(self) -> List[TicketRecord]:
        """
        Return the tickets this project placed (requester side).

        Every ledger entry is refreshed from the target project when it is
        reachable, so a status changed by the target is visible here; when the
        target cannot be resolved (link removed) the ledger value is returned and
        the record is flagged ``available=False``.

        Returns:
            One record per ledger entry.
        """
        records: List[TicketRecord] = []
        for entry in self.load_ledger():
            ticket_id = _clean(entry.get("ticket_id") or entry.get("chat_id"))
            if not ticket_id:
                continue
            try:
                target = self.resolve_target(
                    _clean(entry.get("target_project_id") or entry.get("target_project_name"))
                )
            except TicketError:
                records.append(TicketRecord.from_ledger(entry))
                continue

            manager = self.manager_for(target)
            chat = manager.find_by_id(ticket_id)
            if chat is None or not self.is_ticket(chat):
                records.append(TicketRecord.from_ledger(entry))
                continue
            self._sync_status_from_column(chat=chat, manager=manager)
            record = TicketRecord.from_chat(chat, direction=TICKET_SIDE_SOURCE)
            records.append(record)
            if _clean(record.updated_at) != _clean(entry.get("updated_at")) or _clean(
                record.status
            ) != _clean(entry.get("status")):
                self.upsert_ledger(record)
        return records

    def _incoming_records(self) -> List[TicketRecord]:
        """
        Return the tickets placed on THIS project (target side).

        Returns:
            One record per ticket chat stored in this project's ``tickets`` board.
        """
        records: List[TicketRecord] = []
        manager = self.own_manager()
        for chat in self._own_ticket_chats():
            self._sync_status_from_column(chat=chat, manager=manager)
            records.append(TicketRecord.from_chat(chat, direction=TICKET_SIDE_TARGET))
        return records

    # ------------------------------------------------------------------
    # Reporting helper
    # ------------------------------------------------------------------

    @staticmethod
    def render(record: TicketRecord) -> str:
        """
        Render a record as the markdown block the tools show to the user.

        Args:
            record: The record to render.

        Returns:
            A compact markdown block.
        """
        target = record.target_project_name or record.target_project_id or "?"
        source = record.source_project_name or record.source_project_id or "?"
        lines = [
            f"**{record.name}** — `{record.ticket_id}`",
            f"- {record.kind} · **{record.status}** · {source} → {target}",
        ]
        if record.priority:
            lines.append(f"- priority: {record.priority}")
        if record.reference:
            lines.append(f"- reference: {record.reference}")
        if record.column:
            lines.append(f"- board: {record.board or TICKET_BOARD} / {record.column}")
        autonomy = record.autonomy or {}
        if autonomy.get("enabled"):
            detail = f"- autonomy: **{autonomy.get('state') or 'off'}**"
            if autonomy.get("rounds"):
                detail += f" · {autonomy['rounds']} round(s)"
            if autonomy.get("validation_passed"):
                detail += " · validated"
            if autonomy.get("last_error"):
                detail += f" · last error: {autonomy['last_error']}"
            lines.append(detail)
        if record.last_message:
            lines.append(f"- last message: {record.last_message}")
        return "\n".join(lines)


def ticket_kinds_help() -> str:
    """Return the accepted ticket kinds as a comma separated string."""
    return ", ".join(TICKET_KINDS)


def ticket_statuses_help() -> str:
    """Return the accepted ticket statuses as a comma separated string."""
    return ", ".join(TICKET_STATUSES)


def ticket_datetime() -> str:
    """Return the current timestamp (helper kept for symmetry with the chats)."""
    return datetime.now().isoformat()

# Made with ❤️ by codx-junior
