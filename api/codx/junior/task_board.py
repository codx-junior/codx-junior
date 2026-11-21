"""
Cross-project task table service.

The **Tasks** screen used to open a single board. Product-wise the task list is
now the general working queue of the installation: the latest tasks of **every
board of every project** the user can see, in one paged table. Each row carries
the board and column it belongs to, so the table itself is the navigation
surface: clicking a board opens that board in its project.

Board ownership
---------------
Boards are managed by teams. A board may carry ``team_id`` explicitly (set from
the board settings); when it does not, the owning team of the project is used.
The team lead is the person responsible for the board, exactly as the product
rule states, and is resolved live from the company document so a lead change is
reflected immediately.

The service is intentionally read-only and independent from the removed
management dashboard: it does not compute budgets, forecasts or attention
queues, it just serves the table.
"""

from __future__ import annotations

import logging

from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from codx.junior.model.tasks import (
    GlobalTask,
    GlobalTaskPage,
    TaskBoardRef,
    TaskMetrics,
)

logger = logging.getLogger(__name__)

#: Stable colours used by the project chip of the table.
PROJECT_COLORS = (
    "#e2419f",
    "#6366f1",
    "#0ea5e9",
    "#10b981",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#14b8a6",
)

#: Default page size of the task table.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

#: A task inactive for this many days is clearly marked as old in Follow-up.
OLD_TASK_DAYS = 7


def _clean(value: Any) -> str:
    """Return *value* as a trimmed string."""
    return "" if value is None else str(value).strip()


def _iso(value: Any) -> str:
    """Render a datetime/string task timestamp as an ISO-ish string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return _clean(value)


def _timestamp(value: Any) -> float:
    """Parse a task timestamp into a comparable number (0 when invalid)."""
    if isinstance(value, datetime):
        return value.timestamp()
    text = _clean(value)
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        except ValueError:
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                return 0.0


def _date(value: Any) -> Optional[date]:
    """Parse a date-ish value (or None)."""
    text = _clean(value)[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class TaskBoardService:
    """
    Read model of the cross-project task table.

    Args:
        user: Authenticated user. Non-admins only see their projects.
    """

    def __init__(self, user: Any = None):
        self.user = user
        self._projects: Optional[List[Any]] = None
        self._company_teams: Optional[Dict[str, Dict[str, Any]]] = None
        self._project_teams: Optional[Dict[str, str]] = None
        self._boards: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._tasks: Optional[List[Dict[str, Any]]] = None

    # ------------------------------------------------------------------
    # Catalogs
    # ------------------------------------------------------------------
    def _load_projects(self) -> List[Any]:
        """Resolve the projects visible to the user (admin = every project)."""
        if self._projects is not None:
            return self._projects
        try:
            # Lazy import: the project/settings stack pulls the AI configuration,
            # and the read model must stay importable from small tests and
            # scripts that only exercise the table.
            from codx.junior.project.project_discover import (
                find_all_projects,
                find_all_user_projects,
            )

            if self.user is not None and getattr(self.user, "role", "") != "admin":
                projects = list(find_all_user_projects(self.user))
            else:
                projects = list((find_all_projects() or {}).values())
        except Exception as ex:  # noqa: BLE001 - discovery must never break the table
            logger.exception("task table: project discovery failed: %s", ex)
            projects = []
        self._projects = [
            settings for settings in projects
            if getattr(settings, "codx_path", None) and getattr(settings, "project_id", None)
        ]
        return self._projects

    def project_dicts(self) -> List[Dict[str, Any]]:
        """Projects offered by the table filters."""
        return [
            {
                "id": settings.project_id,
                "name": getattr(settings, "project_name", "") or settings.project_id,
                "color": PROJECT_COLORS[index % len(PROJECT_COLORS)],
            }
            for index, settings in enumerate(self._load_projects())
        ]

    def _load_company_catalog(self) -> None:
        """
        Load team names/leads and the owning team of each company project.

        The company is the top-level entity, so this is where a board's
        responsibility comes from. Failures are tolerated: a task table without
        company data is still usable.
        """
        if self._company_teams is not None:
            return
        teams: Dict[str, Dict[str, Any]] = {}
        project_teams: Dict[str, str] = {}
        try:
            from codx.junior.company.manager import CompanyManager

            manager = CompanyManager()
            for company_id in manager.store.list_ids():
                try:
                    company = manager.get_company(company_id)
                except Exception:
                    continue
                for team in company.teams:
                    teams.setdefault(
                        team.id,
                        {
                            "id": team.id,
                            "name": team.name,
                            "lead_kind": team.lead_kind,
                            "lead_name": team.lead_name,
                            "company_id": company.id,
                            "company_name": company.name,
                        },
                    )
                for project in company.projects:
                    if project.team_id and project.project_id not in project_teams:
                        project_teams[project.project_id] = project.team_id
        except Exception as ex:  # noqa: BLE001 - optional context
            logger.debug("task table: company catalog unavailable: %s", ex)
        self._company_teams = teams
        self._project_teams = project_teams

    def _board_catalog(self, settings: Any) -> Dict[str, Dict[str, Any]]:
        """Load the board documents of one project, keyed by lower-case title."""
        project_id = settings.project_id
        cached = self._boards.get(project_id)
        if cached is not None:
            return cached
        boards: Dict[str, Dict[str, Any]] = {}
        try:
            from codx.junior.chat_manager import ChatManager

            manager = ChatManager(settings=settings)
            kanban = manager.load_kanban() or {}
            for title, entry in (kanban.get("boards") or {}).items():
                if not isinstance(entry, dict) or entry is None:
                    continue
                board = {**entry, "title": _clean(entry.get("title") or title)}
                boards[board["title"].lower()] = board
        except Exception as ex:  # noqa: BLE001 - a broken catalogue must not hide tasks
            logger.warning(
                "task table: cannot load boards of '%s': %s",
                settings.project_id,
                ex,
            )
        self._boards[project_id] = boards
        return boards

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------
    @staticmethod
    def _find_board(
        boards: Optional[Dict[str, Dict[str, Any]]], reference: Any
    ) -> Optional[Dict[str, Any]]:
        """Find a board by id, title or folder (case insensitive)."""
        wanted = _clean(reference).lower()
        if not wanted or not boards:
            return None
        direct = boards.get(wanted)
        if direct is not None:
            return direct
        for board in boards.values():
            if not isinstance(board, dict):
                continue
            keys = {
                _clean(board.get("id")).lower(),
                _clean(board.get("title")).lower(),
                _clean(board.get("folder")).lower(),
                _clean(board.get("name")).lower(),
            }
            if wanted in keys:
                return board
        return None

    def _responsible_for(
        self,
        board: Optional[Dict[str, Any]],
        boards: Optional[Dict[str, Dict[str, Any]]],
        lead_kind: str = "",
        lead_name: str = "",
        seen: Optional[set] = None,
    ) -> Dict[str, str]:
        """
        Resolve the effective responsible of a board.

        Order:
        1. the board's own ``responsible_kind``/``responsible_name``,
        2. the nearest ANCESTOR board with a responsible (children without an
           explicit responsible are managed by their parent's responsible),
        3. the legacy board assistant (``assignee``, a profile),
        4. the owning team lead,
        5. the default ``ProjectManager`` profile (so every board always has a
           responsible, as the product rule requires).
        """
        board = board or {}
        seen = set(seen or set())
        identity = _clean(board.get("id") or board.get("title") or id(board))
        if identity in seen:
            return {}
        seen.add(identity)

        own_name = _clean(board.get("responsible_name"))
        if own_name:
            return {
                "responsible_kind": _clean(board.get("responsible_kind")) or "profile",
                "responsible_name": own_name,
                "responsible_source": "board",
            }

        parent = self._find_board(boards, board.get("parent_id"))
        if parent is not None:
            inherited = self._responsible_for(
                parent, boards, seen=seen
            )
            if inherited.get("responsible_name"):
                inherited["responsible_source"] = "parent"
                return inherited

        assignee = _clean(board.get("assignee"))
        if assignee:
            return {
                "responsible_kind": "profile",
                "responsible_name": assignee,
                "responsible_source": "assignee",
            }

        if lead_name:
            return {
                "responsible_kind": lead_kind or "profile",
                "responsible_name": lead_name,
                "responsible_source": "team",
            }

        return {
            "responsible_kind": "profile",
            "responsible_name": "ProjectManager",
            "responsible_source": "default",
        }

    def _board_owner(
        self,
        project_id: str,
        board: Optional[Dict[str, Any]],
        boards: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, str]:
        """Resolve the owning team, team lead and effective responsible."""
        self._load_company_catalog()
        teams = self._company_teams or {}
        project_teams = self._project_teams or {}
        board = board or {}
        team_id = _clean(board.get("team_id")) or project_teams.get(project_id, "")
        team = teams.get(team_id) or {}
        lead_kind = _clean(team.get("lead_kind"))
        lead_name = _clean(team.get("lead_name"))
        responsible = self._responsible_for(
            board, boards, lead_kind=lead_kind, lead_name=lead_name
        )
        return {
            "team_id": team_id,
            "team_name": _clean(team.get("name") or board.get("team_name")),
            "lead_kind": lead_kind,
            "lead_name": lead_name,
            "responsible": _clean(responsible.get("responsible_name") or lead_name),
            "responsible_kind": _clean(responsible.get("responsible_kind")),
            "responsible_name": _clean(responsible.get("responsible_name")),
            "own_responsible_kind": _clean(board.get("responsible_kind")),
            "own_responsible_name": _clean(board.get("responsible_name")),
            "responsible_source": _clean(responsible.get("responsible_source")),
            "flow": _clean(board.get("flow")) or "agile",
            "parent_id": _clean(board.get("parent_id")),
        }

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @staticmethod
    def _is_task(chat: Any) -> bool:
        """Whether a chat belongs in the task table."""
        if getattr(chat, "is_template", False):
            return False
        if _clean(getattr(chat, "mode", "")).lower() == "channel":
            return False
        if _clean(getattr(chat, "mode", "")).lower() == "task":
            return True
        if (
            _clean(getattr(chat, "board", ""))
            or _clean(getattr(chat, "column", ""))
            or bool(getattr(chat, "sprints", None))
        ):
            return True
        return bool(
            _clean(getattr(chat, "status", ""))
            or _clean(getattr(chat, "priority", ""))
            or _clean(getattr(chat, "due_date", ""))
            or (getattr(chat, "assignees", None) or [])
        )

    def _project_meta(self) -> Dict[str, Dict[str, Any]]:
        """Index the project refs by id."""
        return {project["id"]: project for project in self.project_dicts()}

    def _task_from_chat(
        self,
        chat: Any,
        project: Dict[str, Any],
        boards: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Build one row of the table from a chat."""
        task_id = _clean(getattr(chat, "id", ""))
        if not task_id:
            return None

        board_title = _clean(getattr(chat, "board", ""))
        legacy_sprints = [
            _clean(value)
            for value in (getattr(chat, "sprints", None) or [])
            if _clean(value)
        ]
        # Legacy data: a task created by the removed "sprint board" feature may
        # only carry its board in ``sprints``. It is shown on the first board
        # instead of being hidden after the concept is gone.
        legacy_board = ""
        if not board_title and legacy_sprints:
            legacy_board = legacy_sprints[0]
            board_title = legacy_board

        board = boards.get(board_title.lower()) if board_title else None
        column = _clean(getattr(chat, "column", ""))
        if not column and legacy_board:
            # The removed sprint boards grouped tasks by status. Map that status
            # to the real board column (case-insensitive) so the table shows the
            # column of the converted board, not the raw status token.
            status_key = _clean(getattr(chat, "status", "")).lower()
            columns = board.get("columns") if board else []
            matched = next(
                (
                    item for item in columns or []
                    if isinstance(item, dict) and (
                        _clean(item.get("title")).lower() == status_key
                        or _clean(item.get("status")).lower() == status_key
                    )
                ),
                None,
            )
            column = _clean((matched or {}).get("title")) or _clean(
                getattr(chat, "status", "")
            )
        if not column and board:
            first_columns = board.get("columns") or []
            if first_columns and isinstance(first_columns[0], dict):
                column = _clean(first_columns[0].get("title"))
        owner = self._board_owner(project["id"], board, boards)

        status = _clean(getattr(chat, "status", "")) or "todo"
        priority = _clean(getattr(chat, "priority", "")).lower() or "low"
        assignees = [
            _clean(value)
            for value in (getattr(chat, "assignees", None) or [])
            if _clean(value)
        ]

        latest_message = getattr(chat, "task_latest_message", None)
        if not latest_message:
            visible = [
                message for message in (getattr(chat, "messages", None) or [])
                if not getattr(message, "hide", False)
                and not getattr(message, "unsent", False)
            ]
            if visible:
                message = visible[-1]
                content = " ".join(str(getattr(message, "content", "") or "").split())
                latest_message = {
                    "doc_id": getattr(message, "doc_id", None),
                    "role": getattr(message, "role", "") or "",
                    "user": getattr(message, "user", "") or "",
                    "content": content[:280],
                    "created_at": getattr(message, "created_at", None),
                    "updated_at": getattr(message, "updated_at", None),
                    "done": getattr(message, "done", True),
                    "error": getattr(message, "error", None),
                    "read_by": list(getattr(message, "read_by", None) or []),
                }
        last_activity_at = (
            getattr(chat, "task_last_activity_at", None)
            or (latest_message or {}).get("created_at")
            or (latest_message or {}).get("updated_at")
            or _clean(getattr(chat, "updated_at", ""))
        )
        activity_date = _date(last_activity_at)
        age_days = max(0, (date.today() - activity_date).days) if activity_date else 0

        return {
            "id": task_id,
            "name": _clean(getattr(chat, "name", "")),
            "description": _clean(getattr(chat, "description", ""))[:1000] or None,
            "project_id": project["id"],
            "owner_project_id": project["id"],
            "project_name": project["name"],
            "project_color": project["color"],
            "board": board_title,
            "board_id": f"{project['id']}::{board_title}" if board_title else "",
            "column": column,
            "status": status,
            "priority": priority,
            "due_date": _clean(getattr(chat, "due_date", "")) or None,
            "wake_at": _iso(getattr(chat, "wake_at", "")) or None,
            "progress": int(getattr(chat, "progress", 0) or 0),
            "assignees": assignees,
            "tags": [_clean(value) for value in (getattr(chat, "tags", None) or []) if _clean(value)],
            "mode": _clean(getattr(chat, "mode", "")),
            "run_status": _clean(getattr(chat, "run_status", "")) or None,
            "process_state": _clean(getattr(chat, "task_process_state", "")) or "idle",
            "process_error": _clean(getattr(chat, "task_process_error", "")) or None,
            "latest_message": latest_message,
            "unread_count": int(getattr(chat, "task_unread_count", 0) or 0),
            "last_activity_at": _iso(last_activity_at),
            "age_days": age_days,
            "is_old": age_days >= OLD_TASK_DAYS,
            "created_at": _iso(getattr(chat, "created_at", "")),
            "updated_at": _iso(getattr(chat, "updated_at", "")),
            **owner,
        }

    def load_tasks(self) -> List[Dict[str, Any]]:
        """Load every visible task, newest first."""
        if self._tasks is not None:
            return self._tasks
        project_meta = self._project_meta()
        tasks: List[Dict[str, Any]] = []
        for settings in self._load_projects():
            project = project_meta.get(settings.project_id)
            if project is None:
                continue
            boards = self._board_catalog(settings)
            try:
                from codx.junior.chat_manager import ChatManager

                unread_for = getattr(self.user, "username", None)
                chats = ChatManager(settings=settings).list_chats(
                    unread_for=unread_for
                ) or []
            except Exception as ex:  # noqa: BLE001 - one project must not win
                logger.warning(
                    "task table: cannot list tasks of '%s': %s",
                    settings.project_id,
                    ex,
                )
                continue
            for chat in chats:
                if not self._is_task(chat):
                    continue
                task = self._task_from_chat(chat, project, boards)
                if task:
                    tasks.append(task)
        tasks.sort(
            key=lambda task: (
                _timestamp(task.get("updated_at")),
                task.get("id") or "",
            ),
            reverse=True,
        )
        self._tasks = tasks
        logger.info("task table: loaded %d task(s)", len(tasks))
        return tasks

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @staticmethod
    def _matches(task: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        """Apply the table filters (case-insensitive, exact where suitable)."""
        project_ids = filters.get("project_ids") or []
        if project_ids and task.get("project_id") not in project_ids:
            return False
        board_ids = [str(value) for value in (filters.get("board_ids") or []) if str(value)]
        if board_ids and str(task.get("board_id") or "") not in board_ids:
            return False
        for key, values in (
            ("column", filters.get("columns")),
            ("status", filters.get("statuses")),
            ("priority", filters.get("priorities")),
        ):
            wanted_values = {str(value).strip().lower() for value in (values or []) if str(value).strip()}
            if wanted_values and str(task.get(key) or "").strip().lower() not in wanted_values:
                return False
        wanted_assignees = {
            str(value).strip().lower()
            for value in (filters.get("assignees") or [])
            if str(value).strip()
        }
        if wanted_assignees:
            current = {
                str(name).strip().lower() for name in (task.get("assignees") or [])
            }
            if not current.intersection(wanted_assignees):
                return False
        assignee = (filters.get("assignee") or "").strip()
        if assignee and not any(
            assignee.lower() == str(name).strip().lower()
            for name in (task.get("assignees") or [])
        ):
            return False
        from_date = _date(filters.get("from_date"))
        to_date = _date(filters.get("to_date"))
        if from_date or to_date:
            activity = _date(task.get("last_activity_at") or task.get("updated_at"))
            if activity is None:
                return False
            if from_date and activity < from_date:
                return False
            if to_date and activity > to_date:
                return False
        query = (filters.get("q") or "").strip().lower()
        if query:
            haystack = " ".join(
                [
                    task.get("name") or "",
                    task.get("description") or "",
                    task.get("board") or "",
                    task.get("column") or "",
                    " ".join(task.get("tags") or []),
                    " ".join(task.get("assignees") or []),
                ]
            ).lower()
            if not all(term in haystack for term in query.split()):
                return False
        for key in ("project_id", "board", "column", "status", "priority", "team_id"):
            wanted = (filters.get(key) or "").strip()
            if not wanted:
                continue
            value = task.get(key)
            if isinstance(value, str) and value.lower() != wanted.lower():
                return False
        if not filters.get("include_done", True):
            if (task.get("status") or "").lower() in {"done", "rejected", "cancelled"}:
                return False
        return True

    def tasks(
        self,
        *,
        q: str = "",
        project_id: str = "",
        project_ids: Optional[List[str]] = None,
        board: str = "",
        board_ids: Optional[List[str]] = None,
        column: str = "",
        columns: Optional[List[str]] = None,
        status: str = "",
        statuses: Optional[List[str]] = None,
        priority: str = "",
        priorities: Optional[List[str]] = None,
        assignee: str = "",
        assignees: Optional[List[str]] = None,
        team_id: str = "",
        from_date: str = "",
        to_date: str = "",
        include_done: bool = True,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        sort: str = "updated_at",
        direction: str = "desc",
    ) -> GlobalTaskPage:
        """
        Return one page of the cross-project task table.

        Args:
            q: Free text; every whitespace-separated term must match.
            project_id: Restrict to one project.
            board: Restrict to one board title (case-insensitive).
            column: Restrict to one column title.
            status: Restrict to one task status.
            priority: Restrict to one priority.
            team_id: Restrict to the boards owned by one team.
            from_date: Only tasks with activity on/after this date (YYYY-MM-DD).
            to_date: Only tasks with activity on/before this date (YYYY-MM-DD).
            include_done: Include closed tasks (default true).
            page: 1-based page number.
            page_size: Rows per page (max :data:`MAX_PAGE_SIZE`).
            sort: ``updated_at`` (default), ``created_at``, ``name``,
                ``due_date``, ``priority``, ``status``, ``board`` or
                ``project``.
            direction: ``asc`` or ``desc``.

        Returns:
            A :class:`~codx.junior.model.tasks.GlobalTaskPage`.
        """
        rows = self.load_tasks()
        scope_ids = [
            _clean(value) for value in (project_ids or []) if _clean(value)
        ]
        if not scope_ids and project_id:
            scope_ids = [project_id]
        filters = {
            "q": q,
            "project_id": "" if scope_ids else project_id,
            "project_ids": scope_ids,
            "board": board,
            "board_ids": board_ids or [],
            "column": column,
            "columns": columns or [],
            "status": status,
            "statuses": statuses or [],
            "priority": priority,
            "priorities": priorities or [],
            "assignee": assignee,
            "assignees": assignees or [],
            "team_id": team_id,
            "from_date": from_date,
            "to_date": to_date,
            "include_done": include_done,
        }
        filtered = [task for task in rows if self._matches(task, filters)]

        sort_field = (sort or "updated_at").strip().lower()
        reverse = (direction or "desc").strip().lower() != "asc"
        sort_keys = {
            "updated_at": lambda task: (_timestamp(task.get("updated_at")), task.get("id") or ""),
            "created_at": lambda task: (_timestamp(task.get("created_at")), task.get("id") or ""),
            "name": lambda task: (task.get("name") or "").lower(),
            "due_date": lambda task: (_date(task.get("due_date")) or date.max, task.get("id") or ""),
            "priority": lambda task: (task.get("priority") or ""),
            "status": lambda task: (task.get("status") or ""),
            "board": lambda task: (task.get("board") or "").lower(),
            "project": lambda task: (task.get("project_name") or "").lower(),
        }
        key = sort_keys.get(sort_field, sort_keys["updated_at"])
        filtered.sort(key=key, reverse=reverse)

        safe_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        total = len(filtered)
        total_pages = max(1, (total + safe_size - 1) // safe_size)
        safe_page = max(1, min(int(page or 1), total_pages))
        start = (safe_page - 1) * safe_size
        page_rows = filtered[start:start + safe_size]

        return GlobalTaskPage(
            tasks=[GlobalTask(**task) for task in page_rows],
            metrics=self.metrics(filtered),
            total=total,
            page=safe_page,
            page_size=safe_size,
            total_pages=total_pages,
            has_next=safe_page < total_pages,
            has_prev=safe_page > 1,
            projects=self.project_dicts(),
            boards=self.board_refs(),
            columns=_unique(task.get("column") for task in rows),
            assignees=_unique(
                assignee for task in rows for assignee in (task.get("assignees") or [])
            ),
            statuses=_unique(task.get("status") for task in rows),
            priorities=_unique(task.get("priority") for task in rows),
            sort=sort_field,
            direction="desc" if reverse else "asc",
        )

    @staticmethod
    def metrics(tasks: List[Dict[str, Any]]) -> TaskMetrics:
        """Compute the Follow-up style headline numbers for *tasks*."""
        today = date.today()
        total = len(tasks)
        done = sum(1 for task in tasks if (task.get("status") or "").lower() in {"done", "released"})
        active = sum(
            1 for task in tasks
            if (task.get("status") or "").lower() in {"todo", "doing", "in-progress", "running"}
        )
        on_hold = sum(1 for task in tasks if (task.get("status") or "").lower() in {"onhold", "blocked", "paused"})
        overdue = sum(
            1 for task in tasks
            if (task.get("status") or "").lower() not in {"done", "rejected", "cancelled"}
            and (due := _date(task.get("due_date"))) is not None
            and due < today
        )
        due_today = sum(
            1 for task in tasks
            if (task.get("status") or "").lower() not in {"done", "rejected", "cancelled"}
            and _date(task.get("due_date")) == today
        )
        unassigned = sum(1 for task in tasks if not (task.get("assignees") or []))
        return TaskMetrics(
            total=total,
            active=active,
            done=done,
            on_hold=on_hold,
            overdue=overdue,
            due_today=due_today,
            unassigned=unassigned,
            completion=round((done / total) * 100) if total else 0,
        )

    def board_refs(self) -> List[TaskBoardRef]:
        """Return every visible board with its management responsibility."""
        refs: List[TaskBoardRef] = []
        seen = set()
        project_meta = self._project_meta()
        today = date.today()
        tasks_by_board: Dict[str, List[Dict[str, Any]]] = {}
        for task in self.load_tasks():
            board_id = _clean(task.get("board_id"))
            if board_id:
                tasks_by_board.setdefault(board_id, []).append(task)
        for settings in self._load_projects():
            project = project_meta.get(settings.project_id)
            if project is None:
                continue
            boards = self._board_catalog(settings)
            for board in boards.values():
                title = _clean(board.get("title"))
                if not title:
                    continue
                board_id = f"{settings.project_id}::{title}"
                key = board_id.lower()
                if key in seen:
                    continue
                seen.add(key)
                owner = self._board_owner(settings.project_id, board, boards)
                columns = board.get("columns") or []
                board_tasks = tasks_by_board.get(board_id, [])
                open_tasks = [
                    task for task in board_tasks
                    if _clean(task.get("status")).lower() not in {"done", "closed", "completed", "rejected", "cancelled"}
                ]
                overdue = [
                    task for task in open_tasks
                    if (due := _date(task.get("due_date"))) is not None and due < today
                ]
                unassigned = [
                    task for task in open_tasks if not (task.get("assignees") or [])
                ]
                last_activity = max(
                    (_clean(task.get("last_activity_at")) for task in board_tasks),
                    default="",
                ) or ""
                parent = self._find_board(boards, board.get("parent_id"))
                refs.append(
                    TaskBoardRef(
                        id=board_id,
                        project_id=settings.project_id,
                        project_name=project["name"],
                        title=title,
                        project_color=project["color"],
                        parent_title=_clean((parent or {}).get("title")),
                        column_count=len(columns) if isinstance(columns, list) else 0,
                        task_count=len(board_tasks),
                        open_tasks=len(open_tasks),
                        overdue_tasks=len(overdue),
                        unassigned_tasks=len(unassigned),
                        last_activity_at=last_activity,
                        **owner,
                    )
                )
        refs.sort(
            key=lambda ref: (
                (ref.project_name or "").lower(),
                (ref.title or "").lower(),
            )
        )
        return refs

    def update_board(
        self,
        board_id: str,
        *,
        flow: Optional[str] = None,
        responsible_kind: Optional[str] = None,
        responsible_name: Optional[str] = None,
        propagate_children: bool = False,
    ) -> TaskBoardRef:
        """
        Update the management declaration of one board.

        Args:
            board_id: ``<project_id>::<board title>`` (the id returned by
                :meth:`board_refs`).
            flow: Board flow; ``None`` keeps the stored value.
            responsible_kind: ``user``, ``profile`` or empty to clear.
            responsible_name: Display name of the responsible.
            propagate_children: Also write the same responsibility to every
                descendant board that has no explicit responsible. Inheritance
                already covers them; this is for teams that prefer explicit
                assignments.

        Returns:
            The refreshed :class:`TaskBoardRef`.
        """
        raw_id = _clean(board_id)
        if "::" not in raw_id:
            raise ValueError("board_id must be '<project_id>::<board title>'")
        project_id, title = raw_id.split("::", 1)
        project_id, title = _clean(project_id), _clean(title)
        if not project_id or not title:
            raise ValueError("board_id must be '<project_id>::<board title>'")

        from codx.junior.chat_manager import ChatManager
        from codx.junior.project.project_discover import find_project_by_id

        settings = find_project_by_id(project_id)
        if settings is None:
            raise ValueError(f"project '{project_id}' not found")
        manager = ChatManager(settings=settings)
        kanban = manager.load_kanban() or {}
        boards = kanban.get("boards") or {}
        board_key = None
        board = None
        for key, value in boards.items():
            if not isinstance(value, dict):
                continue
            if (
                _clean(key).lower() == title.lower()
                or _clean(value.get("title")).lower() == title.lower()
                or _clean(value.get("id")).lower() == title.lower()
            ):
                board_key, board = key, value
                break
        if board is None:
            raise ValueError(f"board '{title}' not found in project '{project_id}'")

        board = dict(board)
        if flow is not None:
            value = _clean(flow).lower() or "agile"
            board["flow"] = value[:40]
        if responsible_kind is not None or responsible_name is not None:
            kind = _clean(responsible_kind).lower()
            name = _clean(responsible_name)
            if name and kind not in {"user", "profile"}:
                kind = "profile"
            board["responsible_kind"] = kind if name else ""
            board["responsible_name"] = name
        boards[board_key] = board

        if propagate_children:
            def _walk(parent: Dict[str, Any], seen: set) -> None:
                parent_id = _clean(parent.get("id") or parent.get("title"))
                if not parent_id or parent_id in seen:
                    return
                seen.add(parent_id)
                for key, child in boards.items():
                    if not isinstance(child, dict):
                        continue
                    reference = _clean(child.get("parent_id"))
                    if reference.lower() not in {
                        parent_id.lower(),
                        _clean(parent.get("title")).lower(),
                    }:
                        continue
                    child = dict(child)
                    if not _clean(child.get("responsible_name")):
                        child["responsible_kind"] = board.get("responsible_kind", "")
                        child["responsible_name"] = board.get("responsible_name", "")
                        child["flow"] = board.get("flow", "agile")
                        boards[key] = child
                    _walk(child, seen)
            _walk(board, set())

        kanban["boards"] = boards
        manager.save_kanban(kanban)
        # The read model caches the board catalogue and the task rows; drop
        # both so the response reflects the write immediately.
        self._boards.pop(project_id, None)
        self._tasks = None
        try:
            from codx.junior.events.collection_events import (
                ACTION_UPDATED,
                COLLECTION_BOARDS,
                emit_collection_event,
            )
            emit_collection_event(
                COLLECTION_BOARDS,
                ACTION_UPDATED,
                {"project_id": project_id, "board": title},
                codx_path=getattr(settings, "codx_path", ""),
                project_id=project_id,
                source="board-management",
            )
        except Exception:  # noqa: BLE001 - a UI refresh hint is optional
            logger.debug("task table: cannot emit the board update", exc_info=True)

        refs = self.board_refs()
        return next(
            (ref for ref in refs if ref.id == f"{project_id}::{title}"),
            next((ref for ref in refs if ref.project_id == project_id and ref.title == title), None),
        )

    def team_refs(self) -> List[Dict[str, Any]]:
        """Teams offered by the task table filter."""
        self._load_company_catalog()
        return sorted(
            (self._company_teams or {}).values(),
            key=lambda team: (team.get("name") or "").lower(),
        )


def _unique(values: Iterable[Any]) -> List[str]:
    """Return unique, non-empty strings preserving the first spelling."""
    seen = set()
    result: List[str] = []
    for value in values:
        text = _clean(value)
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        result.append(text)
    return result


# Made with ❤️ by codx-junior
