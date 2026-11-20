"""Tests for the cross-project task table read model.

The task table is the replacement of the removed Management view: latest tasks
of every board, with board + column columns and the owning team lead. These
tests exercise the projection, filters and pagination without a real project
(tasks are injected directly, which is exactly what the service caches).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from codx.junior.task_board import TaskBoardService


def fake_chat(
    chat_id,
    *,
    name=None,
    board="Board A",
    column="Todo",
    status="todo",
    priority="medium",
    sprints=None,
    updated_at=None,
    description="",
):
    """Build the subset of ``Chat`` the table projection reads."""
    now = updated_at or datetime(2026, 1, 1, 10, 0, 0)
    return SimpleNamespace(
        id=chat_id,
        name=name or f"Task {chat_id}",
        description=description,
        board=board,
        column=column,
        status=status,
        priority=priority,
        due_date="",
        progress=0,
        assignees=[],
        tags=[],
        mode="task",
        run_status=None,
        is_template=False,
        created_at=now,
        updated_at=now,
        sprints=sprints or [],
    )


@pytest.fixture()
def service():
    """Task board service with a single project and an injected board catalog."""
    svc = TaskBoardService(user=None)
    svc._projects = [SimpleNamespace(project_id="p1", project_name="Project One", codx_path="/tmp/p1")]
    svc._boards = {
        "p1": {
            "board a": {
                "title": "Board A",
                "columns": [{"title": "Todo"}, {"title": "Doing"}],
                "team_id": "team-1",
            }
        }
    }
    svc._company_teams = {
        "team-1": {
            "id": "team-1",
            "name": "Core",
            "lead_kind": "user",
            "lead_name": "alice",
        }
    }
    svc._project_teams = {}
    return svc


def test_projection_carries_board_column_and_team_lead(service):
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])

    task = service._task_from_chat(fake_chat("t1"), project, boards)

    assert task["board"] == "Board A"
    assert task["board_id"] == "p1::Board A"
    assert task["column"] == "Todo"
    assert task["team_name"] == "Core"
    assert task["responsible"] == "alice"


def test_legacy_sprint_membership_is_shown_on_its_board(service):
    """Tasks from the removed sprint boards keep their board/column."""
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    chat = fake_chat("t2", board="", column="", status="doing", sprints=["Board A"])

    task = service._task_from_chat(chat, project, boards)

    assert task["board"] == "Board A"
    # No column on the old task: the status matches the "Doing" column.
    assert task["column"] == "Doing"


def test_tasks_are_paginated_newest_first_with_filters(service):
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    now = datetime(2026, 1, 1, 10, 0, 0)
    candidates = [
        fake_chat("old", name="Old bug", updated_at=now),
        fake_chat("new", name="New feature", board="Board A", column="Doing", updated_at=now + timedelta(hours=1)),
        fake_chat("other", name="Other board", board="Board B", column="Backlog", updated_at=now + timedelta(hours=2)),
    ]
    service._tasks = [
        service._task_from_chat(chat, project, boards) for chat in candidates
    ]

    page = service.tasks(page=1, page_size=2)

    assert page.total == 3
    assert page.total_pages == 2
    assert [task.id for task in page.tasks] == ["other", "new"]

    filtered = service.tasks(board="Board A")
    assert filtered.total == 2
    assert [task.id for task in filtered.tasks] == ["new", "old"]

    searched = service.tasks(q="bug")
    assert searched.total == 1
    assert searched.tasks[0].id == "old"

    by_column = service.tasks(column="Backlog")
    assert by_column.total == 1
    assert by_column.tasks[0].column == "Backlog"


def test_closed_tasks_are_excluded_when_requested(service):
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    service._tasks = [
        service._task_from_chat(fake_chat("open", status="todo"), project, boards),
        service._task_from_chat(fake_chat("done", status="done"), project, boards),
    ]

    assert service.tasks(include_done=True).total == 2
    assert service.tasks(include_done=False).total == 1
    assert service.tasks(include_done=False).tasks[0].id == "open"


def test_metrics_and_project_scope(service):
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    service._tasks = [
        service._task_from_chat(fake_chat("todo", status="todo", priority="high"), project, boards),
        service._task_from_chat(fake_chat("done", status="done"), project, boards),
        service._task_from_chat(fake_chat("other", board="Board B"), {
            "id": "p2", "name": "Project Two", "color": "#000"
        }, {}),
    ]

    page = service.tasks(project_ids=["p1"])

    assert page.total == 2
    assert page.metrics.total == 2
    assert page.metrics.active == 1
    assert page.metrics.done == 1
    assert page.metrics.completion == 50
    assert page.metrics.unassigned == 2
    assert {task.id for task in page.tasks} == {"done", "todo"}


def test_team_filter_uses_the_board_owner(service):
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    service._tasks = [
        service._task_from_chat(fake_chat("t1"), project, boards),
    ]

    assert service.tasks(team_id="team-1").total == 1
    assert service.tasks(team_id="team-2").total == 0


def test_followup_projection_carries_latest_message_unread_process_and_age(service):
    """The Follow-up row needs activity, unread and error signals."""
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    now = datetime(2026, 1, 1, 10, 0, 0)
    chat = fake_chat("t1", updated_at=now)
    chat.task_latest_message = {
        "role": "assistant",
        "user": "analyst",
        "content": "The login fix is blocked by the token TTL.",
        "created_at": "2026-01-01T10:00:00",
        "done": True,
    }
    chat.task_unread_count = 3
    chat.task_process_state = "error"
    chat.task_process_error = "run status: stalled"
    chat.task_last_activity_at = (datetime.utcnow() - timedelta(days=10)).isoformat()

    task = service._task_from_chat(chat, project, boards)

    assert task["latest_message"]["content"].startswith("The login fix")
    assert task["unread_count"] == 3
    assert task["process_state"] == "error"
    assert task["process_error"] == "run status: stalled"
    assert task["is_old"] is True
    assert task["age_days"] >= 10


def test_children_inherit_the_parent_board_responsible(service):
    boards = {
        "parent": {
            "id": "parent-id",
            "title": "Parent",
            "columns": [],
            "responsible_kind": "profile",
            "responsible_name": "analyst",
            "flow": "agile",
        },
        "child": {
            "id": "child-id",
            "title": "Child",
            "parent_id": "parent-id",
            "columns": [],
        },
        "own": {
            "id": "own-id",
            "title": "Own",
            "parent_id": "parent-id",
            "columns": [],
            "responsible_kind": "user",
            "responsible_name": "alice",
        },
    }

    child = service._board_owner("p1", boards["child"], boards)
    assert child["responsible_name"] == "analyst"
    assert child["responsible_source"] == "parent"
    assert child["flow"] == "agile"

    own = service._board_owner("p1", boards["own"], boards)
    assert own["responsible_name"] == "alice"
    assert own["responsible_source"] == "board"


def test_every_board_has_an_effective_responsible(service):
    board = {"id": "b1", "title": "Orphan", "columns": []}
    owner = service._board_owner("p1", board, {"orphan": board})

    assert owner["responsible_name"]
    assert owner["responsible_source"] in {"team", "default"}


def test_chat_annotation_computes_unread_and_process_state():
    from codx.junior.chat_manager import _annotate_task_activity

    messages = [
        SimpleNamespace(
            role="assistant",
            user="analyst",
            content="First answer",
            created_at="2026-01-01T10:00:00",
            updated_at="2026-01-01T10:00:00",
            done=True,
            error=None,
            read_by=["bob"],
            hide=False,
            unsent=False,
            doc_id="m1",
        ),
        SimpleNamespace(
            role="assistant",
            user="analyst",
            content="Second answer",
            created_at="2026-01-01T10:01:00",
            updated_at="2026-01-01T10:01:00",
            done=True,
            error=None,
            read_by=[],
            hide=False,
            unsent=False,
            doc_id="m2",
        ),
    ]
    chat = SimpleNamespace(
        messages=messages,
        run_status="running",
        harness_status=None,
        external_status=None,
        harness_metadata={},
        updated_at="2026-01-01T10:01:00",
        task_latest_message=None,
        task_unread_count=0,
        task_process_state="",
        task_process_error=None,
        task_last_activity_at=None,
    )

    _annotate_task_activity(chat, unread_for="bob")

    assert chat.task_unread_count == 1
    assert chat.task_latest_message["content"] == "Second answer"
    assert chat.task_process_state == "running"
    assert chat.task_last_activity_at == "2026-01-01T10:01:00"


def test_multi_select_filters_accept_lists(service):
    project = {"id": "p1", "name": "Project One", "color": "#fff"}
    boards = service._board_catalog(service._projects[0])
    now = datetime(2026, 1, 1, 10, 0, 0)
    first = fake_chat("first", board="Board A", column="Todo", status="todo", updated_at=now)
    first.assignees = ["alice"]
    second = fake_chat("second", board="Board A", column="Doing", status="doing", updated_at=now + timedelta(hours=1))
    second.assignees = ["bob"]
    third = fake_chat("third", board="Board B", column="Backlog", status="done", updated_at=now + timedelta(hours=2))
    service._tasks = [
        service._task_from_chat(chat, project, boards)
        for chat in (first, second, third)
    ]

    assert service.tasks(board_ids=["p1::Board A"]).total == 2
    assert service.tasks(board_ids=["p1::Board A", "p1::Board B"]).total == 3
    assert service.tasks(columns=["Todo", "Backlog"]).total == 2
    assert service.tasks(statuses=["todo", "done"]).total == 2
    assert service.tasks(assignees=["alice", "carol"]).total == 1
