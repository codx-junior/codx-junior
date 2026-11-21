"""Background create / delete of a co-desktop (coworking session).

Creating one pulls an image and runs a container, deleting one stops it: both
take seconds and must never block the request that accepts the order. The
record is persisted first (``creating`` / ``deleting``) and the slow half runs
behind the response, so the client keeps its card and follows the status.
"""

import time
from unittest.mock import Mock

import pytest

from codx.junior.browser.browser_manager import BrowserManager


class _FakeDockerManager:
    """Docker double recording the containers it was asked to start/stop."""

    MAX_SESSIONS = 10

    def __init__(self):
        self.started = []
        self.stopped = []

    def configure_share(self, session, **kwargs):
        # Same shape the real manager produces for a project-root share
        session.home_path = "/home/my-project"
        session.downloads_folder = f".codx/browser_downloads/{session.id}"
        session.downloads_mount = f"/home/my-project/.codx/browser_downloads/{session.id}"
        session.downloads_own_mount = False
        return session

    def start_container(self, session):
        self.started.append(session.id)
        session.status = "running"
        session.container_id = "container-1"
        return session

    def stop_container(self, session, remove=False):
        self.stopped.append((session.id, remove))


def _manager(tmp_path, monkeypatch):
    """A manager whose records are real and whose Docker is a double."""
    manager = BrowserManager.__new__(BrowserManager)
    manager.sessions_path = str(tmp_path / "sessions")
    manager.settings = Mock(
        project_id="project-1", abs_project_path=str(tmp_path / "project")
    )
    manager.docker_manager = _FakeDockerManager()
    monkeypatch.setattr(manager, "list_sessions", lambda: [])
    monkeypatch.setattr(manager, "log_activity", lambda *args, **kwargs: {})
    monkeypatch.setattr(manager, "_open_keep_alive_tab", lambda session: None)
    return manager


def test_defer_start_persists_the_record_without_starting_the_machine(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch)

    session = manager.create_session("Co-desktop", defer_start=True)

    assert session.status == "creating"
    assert manager.docker_manager.started == []
    # The record is readable at once: the client renders the card from it
    assert manager.get_session(session.id).status == "creating"


def test_start_session_finishes_the_background_creation(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    created = manager.create_session("Co-desktop", defer_start=True)

    started = manager.start_session(created.id)

    assert manager.docker_manager.started == [created.id]
    assert started.status == "running"
    assert manager.get_session(created.id).status == "running"


def test_start_session_persists_the_error_and_raises(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    created = manager.create_session("Co-desktop", defer_start=True)

    def boom(session):
        raise RuntimeError("docker is not reachable")

    monkeypatch.setattr(manager.docker_manager, "start_container", boom)

    with pytest.raises(RuntimeError):
        manager.start_session(created.id)

    # The card never stays stuck in "creating"
    assert manager.get_session(created.id).status == "error"


def test_mark_session_deleting_persists_the_status_and_deletes_in_background(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch)
    created = manager.create_session("Co-desktop", defer_start=True)
    started = manager.start_session(created.id)

    removed = []
    monkeypatch.setattr(manager, "delete_session", lambda session_id: removed.append(session_id))

    manager.mark_session_deleting(started)

    # The order is persisted synchronously, the removal continues in background
    assert manager.get_session(started.id).status == "deleting"

    deadline = time.time() + 5
    while not removed and time.time() < deadline:
        time.sleep(0.05)

    assert removed == [started.id]
