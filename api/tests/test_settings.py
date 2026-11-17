"""Dependency resolution: on-disk first, project catalogue as the fallback.

``project_dependencies`` stores names. The on-disk lookup only knows a sibling
folder, a folder inside the project or an absolute path, so a project linked
from the settings page and living elsewhere must resolve through the catalogue.
Every caller (knowledge, chat RAG, atlas) goes through
``CODXJuniorSettings.find_dependency_project``, so the fallback lives there.
"""

import os

from codx.junior.settings import CODXJuniorSettings


def _settings(root, name):
    return CODXJuniorSettings(
        project_name=name,
        abs_project_path=str(root),
    )


def _patch_catalogue(monkeypatch, projects):
    monkeypatch.setattr(
        "codx.junior.project.project_discover.find_project_by_name",
        lambda name: projects.get(name),
    )


def test_dependency_outside_the_tree_uses_the_catalogue(tmp_path, monkeypatch):
    root = tmp_path / "workspace" / "demo"
    root.mkdir(parents=True)
    external = tmp_path / "codx-junior"
    external.mkdir()

    settings = _settings(root, "demo")
    linked = _settings(external, "codx-junior")
    _patch_catalogue(monkeypatch, {"codx-junior": linked})

    assert settings.find_dependency_project("codx-junior") is linked


def test_disk_resolution_wins_over_the_catalogue(tmp_path, monkeypatch):
    """A project sitting next to this one is loaded from disk, no catalogue scan."""
    root = tmp_path / "workspace" / "demo"
    sibling = tmp_path / "workspace" / "codx-junior"
    for folder in (root, sibling):
        folder.mkdir(parents=True)
        (folder / ".codx").mkdir()
        (folder / ".codx" / "project.json").write_text("{}", encoding="utf-8")

    settings = _settings(root, "demo")
    _patch_catalogue(monkeypatch, {"codx-junior": None})

    project = settings.find_dependency_project("codx-junior")

    assert project is not None
    assert os.path.samefile(project.abs_project_path, sibling)


def test_unknown_name_resolves_to_none(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    root.mkdir()
    _patch_catalogue(monkeypatch, {})

    settings = _settings(root, "demo")

    assert settings.find_dependency_project("does-not-exist") is None
    assert settings.find_dependency_project("   ") is None
