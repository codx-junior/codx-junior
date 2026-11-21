"""
Tests for the alias-independent project runtime identity.

The API process and the background worker can see the same project through
different mount aliases (``/home/codx-junior/codx-junior/.codx`` vs
``/home/codx-junior-projects/.../.codx``). Hashing the path string produced two
runtime folders, so the API never saw the worker heartbeat and the harness
reported "worker offline" forever. The identity must come from the project
document instead.

Run with::

    cd api && pytest tests/test_runtime.py -q
"""

import json
from pathlib import Path

from codx.junior.runtime import _project_slug, project_runtime_dir


def _project(root: Path, project_id: str = "project-uuid", name: str = "demo"):
    codx = root / ".codx"
    codx.mkdir(parents=True)
    (codx / "project.json").write_text(
        json.dumps({"project_id": project_id, "project_name": name}),
        encoding="utf-8",
    )
    return codx


def test_slug_is_stable_across_path_aliases(tmp_path):
    """Two paths pointing at the same folder must resolve to one runtime dir."""
    project = tmp_path / "demo-project"
    codx = _project(project)

    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)

    assert _project_slug(str(codx)) == _project_slug(str(alias / ".codx"))
    assert project_runtime_dir(str(codx)) == project_runtime_dir(str(alias / ".codx"))


def test_slug_uses_project_document_name_and_id(tmp_path):
    codx = _project(tmp_path / "folder-name", project_id="abc-123", name="real-name")
    slug = _project_slug(str(codx))

    assert slug.startswith("real-name-")
    # Same project id and name, different folder name: same runtime folder.
    other = _project(tmp_path / "different-folder", project_id="abc-123", name="real-name")
    assert _project_slug(str(other)) == slug


def test_slug_falls_back_to_inode_without_project_document(tmp_path):
    """A project without project.json still gets a stable, collision-free slug."""
    first = tmp_path / "first" / ".codx"
    second = tmp_path / "second" / ".codx"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    assert _project_slug(str(first)) != _project_slug(str(second))
    # Repeated calls (and alias paths) are stable.
    assert _project_slug(str(first)) == _project_slug(str(first))
    alias = tmp_path / "first-alias"
    alias.symlink_to(first.parent, target_is_directory=True)
    assert _project_slug(str(first)) == _project_slug(str(alias / ".codx"))
