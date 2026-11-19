"""File tools must reach the projects linked to the active one.

A project can link another one that lives outside its own tree (declared in
``project_dependencies`` or as a child). ``_validate_path`` used to accept only
the project root and the ``.codx`` root, so ``read_folder`` answered
``Invalid path parameter: Path '...' is not within project root ...`` for a
linked project.

The helpers are imported by name (not ``tools.project_structure``) because the
package re-exports a *function* called ``project_structure`` that shadows the
submodule attribute.
"""

import importlib
import os
from types import SimpleNamespace

import pytest

from codx.junior.tools.project_structure import (
    _build_structure,
    _related_project_settings,
    _resolve_linked_path,
    _validate_path,
)

# ``codx.junior.tools`` re-exports a *function* called ``project_structure``, so
# importing the dotted path through the package binds the function. Resolve the
# real module explicitly before monkeypatching one of its helpers.
project_structure_module = importlib.import_module(
    "codx.junior.tools.project_structure"
)


def _settings(root, related):
    return SimpleNamespace(
        project_name=os.path.basename(str(root)),
        abs_project_path=str(root),
        abs_codx_path=None,
        codx_path=os.path.join(str(root), ".codx"),
        search_related_projects=True,
        get_sub_projects=lambda: [],
        get_related_projects=lambda: related,
    )


def test_validate_path_accepts_a_linked_project_root(tmp_path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    target = linked / "src"
    for folder in (main, target):
        folder.mkdir(parents=True)

    resolved = _validate_path(str(target), str(main), None, [str(linked)])

    assert str(resolved) == str(target.resolve())


def test_validate_path_still_rejects_unknown_roots(tmp_path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    outside = tmp_path / "outside"
    for folder in (main, linked, outside):
        folder.mkdir()

    with pytest.raises(ValueError, match="not within project root"):
        _validate_path(str(outside), str(main), None, [str(linked)])


def test_related_project_settings_skips_projects_without_root():
    related = [
        SimpleNamespace(abs_project_path="/tmp/linked"),
        SimpleNamespace(abs_project_path="   "),
    ]

    found = _related_project_settings(_settings("/tmp/main", related))

    assert [project.abs_project_path for project in found] == ["/tmp/linked"]


def test_build_structure_reads_a_linked_project_tree(tmp_path, monkeypatch):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    (linked / "src").mkdir(parents=True)
    main.mkdir()
    (linked / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    related = _settings(linked, [])
    settings = _settings(main, [related])

    def fake_collect(settings):
        root = os.path.abspath(settings.abs_project_path)
        return ["src/main.py"] if root == str(linked) else []

    monkeypatch.setattr(
        project_structure_module,
        "_collect_project_files",
        fake_collect,
    )

    result = _build_structure(settings, path=str(linked / "src"))

    assert "main.py" in result


def test_build_structure_resolves_a_linked_project_prefix(tmp_path, monkeypatch):
    """``<linked-project>/folder`` is read from the linked project root."""
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    (linked / "src").mkdir(parents=True)
    main.mkdir()
    (linked / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    related = _settings(linked, [])
    settings = _settings(main, [related])

    def fake_collect(settings):
        root = os.path.abspath(settings.abs_project_path)
        return ["src/main.py"] if root == str(linked) else []

    monkeypatch.setattr(
        project_structure_module, "_collect_project_files", fake_collect
    )

    result = _build_structure(settings, path="linked/src")

    assert "main.py" in result
    assert str(linked) in result


def test_resolve_linked_path_handles_bare_and_prefixed_names(tmp_path):
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    for folder in (main, linked):
        folder.mkdir()

    related = _settings(linked, [])
    settings = _settings(main, [related])

    assert _resolve_linked_path(settings, "linked") == (related, "")
    assert _resolve_linked_path(settings, "linked/src") == (related, "src")
    assert _resolve_linked_path(settings, "unknown/src") is None
