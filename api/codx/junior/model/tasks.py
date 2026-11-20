"""
API models of the cross-project task table.

The **Tasks** screen is no longer a board: it is one paged table over the most
recent tasks of every board of every project the user can see. Each row carries
the project, board and column needed to navigate back to the board, plus the
team that owns the board (its lead is the person responsible for it).

Keeping the response model here (instead of reusing the management dashboard
models) is intentional: the dashboard was removed as a product surface, while
the task table is a first-class screen that must stay stable.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LatestMessage(BaseModel):
    """Newest visible message of a task (Follow-up preview)."""

    doc_id: Optional[str] = None
    role: str = Field(default="")
    user: str = Field(default="")
    content: str = Field(default="")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    done: Optional[bool] = True
    error: Optional[str] = None
    read_by: List[str] = Field(default_factory=list)


class TaskBoardRef(BaseModel):
    """A board of one project, as referenced from the task table."""

    id: str = Field(description="Stable board identity '<project_id>::<board>'")
    project_id: str = Field(default="")
    project_name: str = Field(default="")
    title: str = Field(default="", description="Board title (the navigation id)")
    project_color: Optional[str] = None
    parent_id: str = Field(default="", description="Parent board id/title, if any")
    parent_title: str = Field(default="")
    team_id: str = Field(default="")
    team_name: str = Field(default="")
    lead_kind: str = Field(default="", description="'user' or 'profile'")
    lead_name: str = Field(default="", description="Team lead responsible for the board")
    responsible_kind: str = Field(default="", description="'user' or 'profile'")
    responsible_name: str = Field(default="", description="Effective responsible for the board")
    own_responsible_kind: str = Field(default="", description="Responsible declared on this board")
    own_responsible_name: str = Field(default="", description="Responsible declared on this board")
    responsible_source: str = Field(
        default="",
        description="board | parent | assignee | team | default",
    )
    flow: str = Field(default="agile", description="Board management flow")
    column_count: int = Field(default=0)
    task_count: int = Field(default=0)
    open_tasks: int = Field(default=0)
    overdue_tasks: int = Field(default=0)
    unassigned_tasks: int = Field(default=0)
    last_activity_at: Optional[str] = None


class GlobalTask(BaseModel):
    """One task row of the cross-project table."""

    id: str
    name: str = Field(default="")
    description: Optional[str] = None

    # ── Navigation ─────────────────────────────────────────────────────────
    project_id: str = Field(default="")
    owner_project_id: str = Field(
        default="",
        description="Project that stores the task; used to load the full chat",
    )
    project_name: str = Field(default="")
    project_color: Optional[str] = None
    board: str = Field(default="", description="Board title")
    board_id: str = Field(default="", description="'<project_id>::<board>'")
    column: str = Field(default="", description="Column the task lives in")

    # ── Card data ──────────────────────────────────────────────────────────
    status: str = Field(default="todo")
    priority: str = Field(default="low")
    due_date: Optional[str] = None
    wake_at: Optional[str] = Field(
        default=None,
        description="Instant when the autonomy worker must process the task",
    )
    progress: int = Field(default=0)
    assignees: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    mode: str = Field(default="")
    run_status: Optional[str] = None
    created_at: str = Field(default="")
    updated_at: str = Field(default="")
    latest_message: Optional[LatestMessage] = Field(
        default=None, description="Newest visible message of the task"
    )
    unread_count: int = Field(
        default=0, description="Unread assistant replies for the requesting user"
    )
    process_state: str = Field(
        default="idle", description="idle | running | stopped | error"
    )
    process_error: Optional[str] = Field(
        default=None, description="Why the task process stopped/failed"
    )
    last_activity_at: Optional[str] = Field(
        default=None, description="When the newest message was created"
    )
    age_days: int = Field(default=0, description="Days since the last activity")
    is_old: bool = Field(
        default=False, description="Task has been inactive for OLD_TASK_DAYS"
    )

    # ── Board ownership ────────────────────────────────────────────────────
    team_id: str = Field(default="")
    team_name: str = Field(default="")
    lead_kind: str = Field(default="")
    lead_name: str = Field(default="")
    responsible_kind: str = Field(default="")
    responsible_name: str = Field(default="")
    responsible_source: str = Field(default="")
    responsible: str = Field(
        default="",
        description="Display name of the effective board responsible",
    )
    flow: str = Field(default="agile", description="Board management flow")


class TaskMetrics(BaseModel):
    """Headline numbers of the filtered task table (Follow-up style cards)."""

    total: int = 0
    active: int = 0
    done: int = 0
    on_hold: int = 0
    overdue: int = 0
    due_today: int = 0
    unassigned: int = 0
    completion: int = 0


class GlobalTaskPage(BaseModel):
    """One page of the task table plus the data its filters need."""

    tasks: List[GlobalTask] = Field(default_factory=list)
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)
    total: int = 0
    page: int = 1
    page_size: int = 25
    total_pages: int = 1
    has_next: bool = False
    has_prev: bool = False
    projects: List[Dict[str, Any]] = Field(default_factory=list)
    boards: List[TaskBoardRef] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    assignees: List[str] = Field(default_factory=list)
    statuses: List[str] = Field(default_factory=list)
    priorities: List[str] = Field(default_factory=list)
    sort: str = Field(default="updated_at")
    direction: str = Field(default="desc")


# Made with ❤️ by codx-junior
