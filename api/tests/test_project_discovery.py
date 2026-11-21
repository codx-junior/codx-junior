"""
Tests for the project-discovery contract used by the harness.

``find_all_projects`` is documented to return a mapping. It used to return
``None`` when a cold scan had not finished inside the wait window; every caller
that did ``find_all_projects().values()`` then crashed (profile loading, the
orchestration worker, the harness watchdog, chat routing). A not-yet-discovered
list is empty, not an error.

Run with::

    cd api && pytest tests/test_project_discovery.py -q
"""

import codx.junior.project.project_discover as discovery


def test_find_all_projects_returns_an_empty_mapping_when_cold(monkeypatch):
    monkeypatch.setattr(discovery, "_ALL_PROJECTS", None)
    monkeypatch.setattr(discovery, "_ALL_PROJECTS_PROC", None)
    monkeypatch.setattr(discovery, "_SCAN_RUNNING", False)
    monkeypatch.setattr(discovery, "_start_scan_locked", lambda: None)

    projects = discovery.find_all_projects()

    assert projects == {}
    assert list(projects.values()) == []
