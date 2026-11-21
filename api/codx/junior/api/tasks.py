"""
REST surface of the cross-project task table.

Route map
---------
```text
GET /api/tasks          paged latest tasks of every board/project (filters)
GET /api/tasks/boards   boards (with owning team + responsible lead) and teams
GET /api/tasks/schedule resolve a schedule expression (non-working-hours, …)
```

Why a dedicated router
----------------------
``GET /api/kanban/search`` is project-scoped and keyed by board title; the new
Tasks screen needs **one table across every visible project**, with the board
and column as first-class columns and enough data to navigate to the board.
The former management dashboard also aggregated tasks, but it was removed as a
product surface, so the table gets a stable endpoint of its own.

The endpoint is read-only. Mutations keep using the existing project endpoints
(``PUT /api/chats`` for card fields, ``/api/kanban`` for board documents).

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from codx.junior.model.model import CodxUser
from codx.junior.model.tasks import GlobalTaskPage
from codx.junior.security.user_management import get_authenticated_user
from codx.junior.task_board import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    TaskBoardService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


class BoardUpdateRequest(BaseModel):
    """Management declaration of one board (``PUT /api/tasks/boards``)."""

    board_id: str = Field(
        description="Stable board identity '<project_id>::<board title>'"
    )
    flow: Optional[str] = Field(
        default=None,
        description="Board flow; default 'agile' (todo, doing, done, rejected, onhold)",
    )
    responsible_kind: Optional[str] = Field(
        default=None, description="'user' or 'profile' (empty clears)"
    )
    responsible_name: Optional[str] = Field(
        default=None, description="Display name of the responsible"
    )
    propagate_children: bool = Field(
        default=False,
        description="Also assign this responsible to descendant boards without one",
    )


def _as_bool(value: Optional[str], default: bool = True) -> bool:
    """Parse a query-string boolean."""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _as_list(value: Optional[str]) -> list:
    """Parse a comma separated query-string list."""
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _as_positive_int(value: Optional[str], default: int, maximum: int) -> int:
    """Parse and clamp a query-string positive integer."""
    try:
        number = int(str(value)) if value is not None and str(value).strip() else default
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


@router.get("", response_model=GlobalTaskPage)
@router.get("/", response_model=GlobalTaskPage, include_in_schema=False)
def api_tasks(
    q: str = Query("", description="Free text (all terms must match)"),
    project_id: str = Query("", description="Restrict to one project"),
    project_ids: str = Query(
        "",
        description="Comma separated project ids (active project + children)",
    ),
    board: str = Query("", description="Restrict to one board title"),
    board_ids: str = Query(
        "", description="Comma separated board ids ('<project_id>::<board>')"
    ),
    column: str = Query("", description="Restrict to one column title"),
    columns: str = Query("", description="Comma separated column titles"),
    status: str = Query("", description="Restrict to one task status"),
    statuses: str = Query("", description="Comma separated task statuses"),
    priority: str = Query("", description="Restrict to one priority"),
    priorities: str = Query("", description="Comma separated priorities"),
    assignee: str = Query("", description="Restrict to one assignee"),
    assignees: str = Query("", description="Comma separated assignees"),
    team_id: str = Query("", description="Restrict to boards of one team"),
    from_date: str = Query(
        "",
        description="Only tasks with activity on/after this date (YYYY-MM-DD)",
    ),
    to_date: str = Query(
        "",
        description="Only tasks with activity on/before this date (YYYY-MM-DD)",
    ),
    include_done: Optional[str] = Query(
        None, description="Include closed tasks (default: true)"
    ),
    page: Optional[str] = Query(None, description="Page number (default: 1)"),
    page_size: Optional[str] = Query(
        None, description=f"Rows per page (max: {MAX_PAGE_SIZE})"
    ),
    sort: str = Query(
        "updated_at",
        description="updated_at | created_at | name | due_date | priority | status | board | project",
    ),
    direction: str = Query("desc", description="asc | desc"),
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    Return one page of the latest tasks across every visible project.

    The default order is *most recently updated first*, which is what the
    "last tasks" table means. Filters and pagination are applied server-side,
    so ``total`` is the true number of matching tasks, never a client-side
    reduction over one page.
    """
    try:
        scope = [value.strip() for value in (project_ids or "").split(",") if value.strip()]
        return TaskBoardService(user=user).tasks(
            q=q,
            project_id=project_id,
            project_ids=scope,
            board=board,
            board_ids=_as_list(board_ids),
            column=column,
            columns=_as_list(columns),
            status=status,
            statuses=_as_list(statuses),
            priority=priority,
            priorities=_as_list(priorities),
            assignee=assignee,
            assignees=_as_list(assignees),
            team_id=team_id,
            from_date=from_date,
            to_date=to_date,
            include_done=_as_bool(include_done, True),
            page=_as_positive_int(page, 1, 10_000_000),
            page_size=_as_positive_int(page_size, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE),
            sort=sort,
            direction=direction,
        )
    except HTTPException:
        raise
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("api_tasks failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Task table failed: {ex}")


@router.get("/schedule")
def api_task_schedule(
    when: str = Query(
        ...,
        description=(
            "'non-working-hours', an ISO instant/date, or a relative expression "
            "('+2d', '3 hours')"
        ),
    ),
    timezone: str = Query("", description="IANA zone for relative expressions"),
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    Resolve a scheduling expression to the instant stored in ``Chat.wake_at``.

    The New-chat modal uses this for the "non-working hours" option: the human
    working window is an installation setting, so the client never has to
    duplicate the timezone/window logic. Tools resolve the same expression
    server-side through :mod:`codx.junior.working_hours`.
    """
    try:
        from codx.junior.working_hours import (
            next_non_working_start,
            resolve_when,
            working_hours_description,
        )

        if str(when or "").strip().lower() in {"non-working-hours", "non_working_hours", "off-hours", "after-hours"}:
            wake_at = next_non_working_start()
        else:
            wake_at = resolve_when(when, timezone_name=timezone)
        return {
            "when": when,
            "wake_at": wake_at,
            "working_hours": working_hours_description(),
        }
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("api_task_schedule failed: %s", ex)
        raise HTTPException(status_code=400, detail=f"Cannot resolve schedule: {ex}")


@router.get("/boards")
def api_task_boards(
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    Return the boards visible to the user (with team/lead) and the teams.

    The Tasks table uses this to render its board filter and the board header
    uses the same ownership data: a board is managed by a team and the team
    lead is responsible for it.
    """
    try:
        service = TaskBoardService(user=user)
        return {
            "boards": [board.model_dump() for board in service.board_refs()],
            "teams": service.team_refs(),
        }
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("api_task_boards failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Cannot list boards: {ex}")


@router.put("/boards")
def api_update_task_board(
    payload: BoardUpdateRequest,
    user: CodxUser = Depends(get_authenticated_user),
):
    """
    Assign the leader/responsible and the flow of one board.

    The responsible of a board also manages its descendant boards that do not
    define their own responsible (the effective responsible is inherited).
    """
    try:
        service = TaskBoardService(user=user)
        board = service.update_board(
            payload.board_id,
            flow=payload.flow,
            responsible_kind=payload.responsible_kind,
            responsible_name=payload.responsible_name,
            propagate_children=payload.propagate_children,
        )
        if board is None:
            raise HTTPException(status_code=404, detail="Board not found after update")
        return {"board": board.model_dump()}
    except HTTPException:
        raise
    except ValueError as ex:
        raise HTTPException(status_code=404, detail=str(ex))
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("api_update_task_board failed: %s", ex)
        raise HTTPException(status_code=500, detail=f"Cannot update the board: {ex}")


# Made with ❤️ by codx-junior
