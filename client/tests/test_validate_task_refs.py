"""Tests for ``tools/validate_task_refs.py`` (card 82802eda).

Run with: ``python -m pytest tests/test_validate_task_refs.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import validate_task_refs as vtr  # noqa: E402

EXISTING = "6bde8a8c-b48e-4104-848a-1e3d55acc383"
PHANTOM = "8eded1c4-0000-4000-8000-000000000000"


def write_card(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_phantom_reference_in_description_is_reported(tmp_path, capsys):
    write_card(
        tmp_path,
        "Working/gate.json",
        {"id": EXISTING, "name": "gate", "description": f"blocked by {PHANTOM}"},
    )

    code = vtr.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 1
    assert PHANTOM in out
    assert "1 phantom reference(s)" in out


def test_existing_reference_is_accepted(tmp_path, capsys):
    write_card(
        tmp_path,
        "Working/gate.json",
        {"id": EXISTING, "name": "gate", "description": "see 6bde8a8c-b48e-4104-848a-1e3d55acc383"},
    )

    code = vtr.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "OK - every cited card id resolves" in out


def test_phantom_inside_message_content_is_reported(tmp_path, capsys):
    write_card(
        tmp_path,
        "Working/gate.json",
        {
            "id": EXISTING,
            "name": "gate",
            "messages": [
                {"role": "user", "content": f"the decision card {PHANTOM} is due today"}
            ],
        },
    )

    code = vtr.main(["--root", str(tmp_path), "--quiet"])
    out = capsys.readouterr().out

    assert code == 1
    assert f"messages[0].content -> {PHANTOM}" in out


def test_ids_of_boards_are_known_references(tmp_path, capsys):
    board_id = "219eb079-30ba-40b9-9d12-eef81bf5d6f9"
    write_card(tmp_path, "boards/" + board_id + "/board.json", {"id": board_id, "title": "IT team"})
    write_card(
        tmp_path,
        "Working/gate.json",
        {"id": EXISTING, "name": "gate", "description": f"card on board {board_id}"},
    )

    code = vtr.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "2 existing id(s) known" in out


def test_missing_root_is_an_error(tmp_path, capsys):
    code = vtr.main(["--root", str(tmp_path / "does-not-exist")])

    assert code == 2
    assert "control plane not found" in capsys.readouterr().err


def test_discovery_walks_up_from_a_nested_tool_folder(tmp_path):
    """The tool lives in a sub-folder of the repo that owns ``.codx``."""
    control_plane = tmp_path / ".codx" / "tasks"
    control_plane.mkdir(parents=True)
    nested = tmp_path / "client" / "tools"
    nested.mkdir(parents=True)

    assert vtr.discover_control_plane(nested) == control_plane
    assert vtr.discover_control_plane(tmp_path / "client") == control_plane


def test_default_root_is_a_real_control_plane():
    """Regression: the default root must not be ``client/.codx/tasks``."""
    default_root = Path(vtr.DEFAULT_ROOT)

    assert default_root.is_dir(), f"default control plane not found: {default_root}"
    assert default_root.name == "tasks"
    assert default_root.parent.name == ".codx"


def test_discovery_returns_none_when_there_is_no_control_plane(tmp_path):
    assert vtr.discover_control_plane(tmp_path) is None
