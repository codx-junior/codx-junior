"""
Harness sessions are bound to a branch and never create a worktree.

The harness new-ticket form no longer asks for a project: it asks for the branch
the ticket runs on. These tests pin the backend contract behind that selector:

* the created chat records the selected branch in its ``harness_mode`` metadata;
* the summary (what the left bar groups by) reports that branch;
* a session without a recorded branch reports the checkout's branch;
* selecting a branch different from the checked out one switches the checkout
  (no worktree is created);
* archiving by branch only touches the sessions of that branch.
"""

from codx.junior.harness import sessions as harness_sessions


class _Settings:
    """Minimal project settings of a harness session."""

    project_id = "p1"
    codx_path = "/tmp/codx"
    abs_project_path = "/tmp/project"


class _Manager:
    """ChatManager stand-in: keeps the saved chat and records archives."""

    def __init__(self):
        self.saved = None
        self.archived = []

    def list_chats(self):
        return []

    def find_by_id(self, chat_id):
        return None

    def save_chat(self, chat=None, chat_only=False):
        self.saved = chat
        return chat

    def archive_chat(self, chat_id=None, user=None):
        self.archived.append(chat_id)
        return {"chat_id": chat_id}


def _service(monkeypatch, selected="feature/branch-selector", active="main"):
    manager = _Manager()
    service = harness_sessions.HarnessSessionService(
        settings=_Settings(), chat_manager=manager
    )
    monkeypatch.setattr(service, "ensure_board", lambda: None)
    monkeypatch.setattr(harness_sessions, "current_branch", lambda path: active)
    monkeypatch.setattr(
        harness_sessions,
        "branch_exists",
        lambda path, name: name in {selected, active},
    )
    checkouts = []

    def _checkout(path, name):
        checkouts.append(name)
        return True, ""

    monkeypatch.setattr(harness_sessions, "checkout_branch", _checkout)
    return service, manager, checkouts


def test_create_binds_the_ticket_to_the_selected_branch(monkeypatch):
    service, manager, checkouts = _service(monkeypatch)

    summary = service.create("Fix the login", branch="feature/branch-selector")

    assert manager.saved.meta_data["harness_mode"]["branch"] == "feature/branch-selector"
    # No worktree: the ticket runs on that branch of the checkout.
    assert checkouts == ["feature/branch-selector"]
    assert summary["branch"] == "feature/branch-selector"


def test_create_defaults_to_the_current_branch_without_switching(monkeypatch):
    service, manager, checkouts = _service(monkeypatch)

    summary = service.create("Fix the login")

    assert checkouts == []
    assert summary["branch"] == "main"
    assert manager.saved.meta_data["harness_mode"]["branch"] == "main"


def test_a_failed_checkout_is_reported_instead_of_silently_using_another_branch(
    monkeypatch,
):
    service, manager, _ = _service(monkeypatch)
    monkeypatch.setattr(
        harness_sessions, "checkout_branch", lambda path, name: (False, "dirty tree")
    )

    try:
        service.create("Fix the login", branch="feature/branch-selector")
    except harness_sessions.HarnessSessionError as ex:
        assert "dirty tree" in str(ex)
    else:  # pragma: no cover - the checkout must not fail silently
        raise AssertionError("a refused checkout must raise HarnessSessionError")
    assert manager.saved is None


def test_archive_by_branch_only_touches_that_branch(monkeypatch):
    service, manager, _ = _service(monkeypatch)
    monkeypatch.setattr(
        service,
        "list",
        lambda limit=1000: [
            {"session_id": "a", "branch": "feature/branch-selector", "phase": "done"},
            {"session_id": "b", "branch": "main", "phase": "done"},
            {"session_id": "c", "branch": "feature/branch-selector", "phase": "running"},
        ],
    )

    result = service.archive(branch="feature/branch-selector")

    assert manager.archived == ["a", "c"]
    assert result["count"] == 2


def test_archive_done_only_touches_finished_sessions(monkeypatch):
    service, manager, _ = _service(monkeypatch)
    monkeypatch.setattr(
        service,
        "list",
        lambda limit=1000: [
            {"session_id": "a", "branch": "main", "phase": "done"},
            {"session_id": "b", "branch": "main", "phase": "running"},
        ],
    )

    service.archive(phase="done")

    assert manager.archived == ["a"]


def test_archive_all_sessions(monkeypatch):
    service, manager, _ = _service(monkeypatch)
    monkeypatch.setattr(
        service,
        "list",
        lambda limit=1000: [
            {"session_id": "a", "branch": "main", "phase": "done"},
            {"session_id": "b", "branch": "other", "phase": "running"},
        ],
    )

    result = service.archive(all_sessions=True)

    assert manager.archived == ["a", "b"]
    assert result["count"] == 2
