"""Tests for the unified board model.

The sprint board type was removed: every board is a regular board and any board
can be used as a sprint by the team that owns it. Legacy ``type: "sprint"``
documents must keep working and be normalized on read/write; boards carry the
owning team (the team lead is responsible for the board).
"""

from __future__ import annotations

import json
import os

from codx.junior.kanban.model import BOARD_TYPE_KANBAN, normalize_board_type
from codx.junior.kanban.store import KanbanStore


def test_normalize_board_type_collapses_legacy_sprint():
    assert normalize_board_type("sprint") == BOARD_TYPE_KANBAN
    assert normalize_board_type("KANBAN") == BOARD_TYPE_KANBAN
    assert normalize_board_type(None) == BOARD_TYPE_KANBAN


def _board_document(board_type: str) -> dict:
    return {
        "version": 2,
        "boards": {
            "Sprint 1": {
                "id": "board-1",
                "title": "Sprint 1",
                "type": board_type,
                "columns": [{"title": "Todo"}, {"title": "Doing"}],
            }
        },
        "tags": {},
    }


def test_saving_a_legacy_sprint_board_persists_a_regular_board(tmp_path):
    store = KanbanStore(str(tmp_path))

    store.save(_board_document("sprint"))

    board_file = os.path.join(str(tmp_path), "boards", "board-1", "board.json")
    with open(board_file, "r", encoding="utf-8") as handle:
        stored = json.load(handle)
    assert stored["type"] == BOARD_TYPE_KANBAN
    assert store.load()["boards"]["Sprint 1"]["type"] == BOARD_TYPE_KANBAN


def test_column_status_mapping_survives_a_save(tmp_path):
    """Columns used as a sprint keep their task-status mapping."""
    store = KanbanStore(str(tmp_path))
    document = _board_document("sprint")
    document["boards"]["Sprint 1"]["columns"] = [
        {"title": "To do", "status": "todo", "icon": "fa-solid fa-list"},
        {"title": "Doing", "status": "doing"},
    ]
    store.save(document)

    board = store.load()["boards"]["Sprint 1"]
    assert board["type"] == BOARD_TYPE_KANBAN
    assert board["columns"][0]["status"] == "todo"
    assert board["columns"][0]["icon"] == "fa-solid fa-list"
    assert board["columns"][1]["status"] == "doing"


def test_board_team_is_persisted_and_kept_on_partial_saves(tmp_path):
    store = KanbanStore(str(tmp_path))
    store.save(_board_document("kanban"))
    document = store.load()
    board = document["boards"]["Sprint 1"]
    board["team_id"] = "team-core"
    board["team_name"] = "Core Platform"
    store.save(document)

    reloaded = store.load()["boards"]["Sprint 1"]
    assert reloaded["type"] == BOARD_TYPE_KANBAN
    assert reloaded["team_id"] == "team-core"
    assert reloaded["team_name"] == "Core Platform"

    # A partial save (e.g. the board editor only changing its name) keeps the
    # stored owning team instead of wiping it.
    document = store.load()
    board = document["boards"].pop("Sprint 1")
    board["title"] = "Sprint 2"
    document["boards"]["Sprint 2"] = board
    store.save(document)
    assert store.load()["boards"]["Sprint 2"]["team_id"] == "team-core"


def test_board_flow_and_responsible_are_persisted_and_default_to_agile(tmp_path):
    store = KanbanStore(str(tmp_path))
    store.save(_board_document("kanban"))

    first = store.load()["boards"]["Sprint 1"]
    assert first["flow"] == "agile"
    assert first["responsible_name"] == ""

    document = store.load()
    board = document["boards"]["Sprint 1"]
    board["flow"] = "support"
    board["responsible_kind"] = "profile"
    board["responsible_name"] = "analyst"
    store.save(document)

    reloaded = store.load()["boards"]["Sprint 1"]
    assert reloaded["flow"] == "support"
    assert reloaded["responsible_kind"] == "profile"
    assert reloaded["responsible_name"] == "analyst"
