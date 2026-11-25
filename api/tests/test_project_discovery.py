"""
Tests for the project-discovery contract used by the harness.

``find_all_projects`` is documented to return a mapping. It used to return
``None`` when a cold scan had not finished inside the wait window; every caller
that did ``find_all_projects().values()`` then crashed (profile loading, the
orchestration worker, the harness watchdog, chat routing). A not-yet-discovered
list is empty, not an error.

The bounded scan also has to visit **every** root. The directory budget used to
be shared by the whole scan: a projects folder holding hundreds of checkouts
consumed it and the walk returned before reaching ``$HOME``, so the codx-junior
checkout stopped being discovered while it was still linked to a company. Every
operation resolving that project then failed with "Project '...' is not
available (not discovered)".

Run with::

    cd api && pytest tests/test_project_discovery.py -q
"""

import logging

import codx.junior.project.project_discover as discovery


def _project(folder):
    """Create a minimal ``<folder>/.codx/project.json`` project."""
    codx_path = folder / ".codx"
    codx_path.mkdir(parents=True)
    (codx_path / "project.json").write_text("{}", encoding="utf-8")
    return str(codx_path)


def test_find_all_projects_returns_an_empty_mapping_when_cold(monkeypatch):
    monkeypatch.setattr(discovery, "_ALL_PROJECTS", None)
    monkeypatch.setattr(discovery, "_ALL_PROJECTS_PROC", None)
    monkeypatch.setattr(discovery, "_SCAN_RUNNING", False)
    monkeypatch.setattr(discovery, "_start_scan_locked", lambda: None)

    projects = discovery.find_all_projects()

    assert projects == {}
    assert list(projects.values()) == []


def test_a_huge_root_cannot_hide_the_projects_of_the_next_root(
    tmp_path, monkeypatch, caplog
):
    """The directory budget is per root: ``$HOME`` is scanned after a big root."""
    big_root = tmp_path / "projects"
    _project(big_root / "app")
    # Enough empty folders to exhaust a tiny budget before the walk finishes.
    for index in range(10):
        (big_root / "filler" / f"d{index}").mkdir(parents=True)

    home = tmp_path / "home"
    workspace = _project(home / "workspace")

    monkeypatch.setattr(discovery, "MAX_SCAN_DIRS", 3)
    monkeypatch.setattr(
        discovery,
        "get_project_scan_roots",
        lambda: [str(big_root), str(home)],
    )

    with caplog.at_level(logging.WARNING):
        found = discovery._scan_codx_paths_roots()

    assert workspace in found, "the scan aborted in the first root and never walked $HOME"
    assert any(
        "stopped" in record.getMessage() and str(big_root) in record.getMessage()
        for record in caplog.records
    ), "a truncated root must say which root was cut"


def test_nested_roots_do_not_duplicate_a_project(tmp_path, monkeypatch):
    """A project under two scan roots is reported once."""
    home = tmp_path / "home"
    codx_path = _project(home / "projects" / "app")

    monkeypatch.setattr(
        discovery,
        "get_project_scan_roots",
        lambda: [str(home / "projects"), str(home)],
    )

    found = discovery._scan_codx_paths_roots()

    assert found.count(codx_path) == 1

