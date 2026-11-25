"""Dependency resolution: on-disk first, project catalogue as the fallback.

``project_dependencies`` stores names. The on-disk lookup only knows a sibling
folder, a folder inside the project or an absolute path, so a project linked
from the settings page and living elsewhere must resolve through the catalogue.
Every caller (knowledge, chat RAG, atlas) goes through
``CODXJuniorSettings.find_dependency_project``, so the fallback lives there.
"""

import json
import os

from codx.junior.settings import CODXJuniorSettings


def _settings(root, name):
    return CODXJuniorSettings(
        project_name=name,
        abs_project_path=str(root),
        codx_path=os.path.join(str(root), ".codx"),
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


def test_permission_lists_are_links_for_search(tmp_path, monkeypatch):
    """A project added from the linked-project editors is a search link too."""
    root = tmp_path / "workspace" / "site"
    root.mkdir(parents=True)
    docs = tmp_path / "docs"
    design = tmp_path / "design"
    for folder in (docs, design):
        folder.mkdir()

    settings = CODXJuniorSettings(
        project_name="site",
        abs_project_path=str(root),
        read_project_dependencies="docs",
        read_write_project_dependencies="design",
    )
    docs_project = _settings(docs, "docs")
    design_project = _settings(design, "design")
    _patch_catalogue(
        monkeypatch, {"docs": docs_project, "design": design_project}
    )

    assert settings.get_dependency_names() == ["docs", "design"]
    assert [
        project.project_name for project in settings.get_related_projects()
    ] == ["docs", "design"]


def test_permission_lists_grant_the_expected_file_access(tmp_path, monkeypatch):
    """Read-only links are readable but never writable; read/write links are both."""
    root = tmp_path / "workspace" / "site"
    root.mkdir(parents=True)
    docs = tmp_path / "docs"
    design = tmp_path / "design"
    for folder in (docs, design):
        folder.mkdir()

    settings = CODXJuniorSettings(
        project_name="site",
        abs_project_path=str(root),
        read_project_dependencies="docs",
        read_write_project_dependencies="design",
    )
    _patch_catalogue(
        monkeypatch,
        {
            "docs": _settings(docs, "docs"),
            "design": _settings(design, "design"),
        },
    )

    access = {
        project.project_name: can_write
        for project, can_write in settings.get_file_access_projects()
    }

    assert access == {"site": True, "docs": False, "design": True}
    assert settings.can_access_path(str(docs / "readme.md"))
    assert not settings.can_access_path(str(docs / "readme.md"), write=True)
    assert settings.can_access_path(str(design / "readme.md"), write=True)


def test_sub_projects_ignore_control_plane_and_vendor_folders(tmp_path):
    """Child discovery must not turn runtime/test fixtures into projects.

    ``.codx`` holds pytest projects and sessions, ``node_modules``/``dist`` hold
    dependencies. Walking them used to report dozens of "related projects" and
    made ``project_locate(project='all')`` search them all.
    """
    root = tmp_path / "demo"
    root.mkdir()
    real = root / "plugins" / "junior"
    nested = [
        real,
        root / ".codx" / "tmp" / "pytest-1" / "project",
        root / "node_modules" / "package",
        root / "dist" / "bundle",
    ]
    for folder in nested:
        (folder / ".codx").mkdir(parents=True)
        (folder / ".codx" / "project.json").write_text(
            json.dumps({"project_name": folder.name}), encoding="utf-8"
        )

    settings = CODXJuniorSettings(
        project_name="demo",
        abs_project_path=str(root),
        codx_path=str(root / ".codx"),
    )

    assert [project.project_name for project in settings.get_sub_projects()] == [
        "junior"
    ]
