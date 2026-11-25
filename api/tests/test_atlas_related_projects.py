"""Atlas related projects: dependencies linked from the project settings.

A dependency is stored by name in ``project_dependencies``. The on-disk
resolution only finds a sibling folder, a folder inside the project or an
absolute path, so a project linked from the settings page that lives elsewhere
must be resolved through the project catalogue.
"""

import os

from codx.junior.atlas import related


class _Settings:
    def __init__(self, root, name="demo", dependencies=None, related_projects=None,
                 search_related=True):
        self.abs_project_path = root
        self.project_name = name
        self.project_id = name
        self.codx_path = os.path.join(root, ".codx")
        self.project_dependencies = ",".join(dependencies or [])
        self.search_related_projects = search_related
        self._related = related_projects or []

    def get_related_projects(self):
        return list(self._related)

    def get_dependency_names(self):
        return [
            name.strip()
            for name in (self.project_dependencies or "").split(",")
            if name.strip()
        ]


def _catalogue(monkeypatch, projects):
    monkeypatch.setattr(
        "codx.junior.project.project_discover.find_all_projects",
        lambda force=False: projects,
    )


def test_dependency_outside_the_tree_is_resolved_from_catalogue(tmp_path, monkeypatch):
    """A linked project living outside the tree is still reachable."""
    root = tmp_path / "workspace" / "demo"
    root.mkdir(parents=True)
    external = tmp_path / "codx-junior"
    external.mkdir()

    settings = _Settings(str(root), dependencies=["codx-junior"])
    linked = _Settings(str(external), name="codx-junior")
    _catalogue(monkeypatch, {"codx-junior": linked})
    related.clear_cache()

    rows = related.related_projects(settings)

    assert [(row["name"], row["kind"]) for row in rows] == [
        ("codx-junior", related.DEPENDENCY)
    ]
    assert related.find_related_project(settings, "codx-junior") is not None


def test_dependency_inside_the_tree_is_not_duplicated(tmp_path, monkeypatch):
    """A dependency already resolved on disk is not added twice."""
    root = tmp_path / "demo"
    child = root / "servers" / "codx-junior"
    child.mkdir(parents=True)

    settings = _Settings(str(root), dependencies=["codx-junior"])
    nested = _Settings(str(child), name="codx-junior")
    settings._related = [nested]
    _catalogue(monkeypatch, {"codx-junior": nested})
    related.clear_cache()

    rows = related.related_projects(settings)

    assert len(rows) == 1
    assert rows[0]["kind"] == related.CHILD


def test_related_search_disabled_keeps_every_search_local(tmp_path, monkeypatch):
    """``search_related_projects = False`` wins over a declared dependency."""
    root = tmp_path / "demo"
    root.mkdir()
    external = tmp_path / "codx-junior"
    external.mkdir()

    settings = _Settings(
        str(root), dependencies=["codx-junior"], search_related=False
    )
    _catalogue(monkeypatch, {"codx-junior": _Settings(str(external), name="codx-junior")})
    related.clear_cache()

    assert related.related_projects(settings) == []


def test_control_plane_fixture_is_not_a_related_project(tmp_path):
    """A pytest project under ``.codx`` is runtime data, not a sibling to search."""
    root = tmp_path / "demo"
    fixture = root / ".codx" / "tmp" / "pytest-1" / "project"
    root.mkdir(parents=True)
    fixture.mkdir(parents=True)

    settings = _Settings(
        str(root),
        related_projects=[_Settings(str(fixture), name="fixture")],
    )
    related.clear_cache()

    assert related.related_projects(settings) == []
