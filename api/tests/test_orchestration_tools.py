"""
Offline tests for the ``orchestrator`` tool helpers.

The end-to-end tool behaviour (start → plan in the same turn) lives in
``tests/test_orchestration.py``; this module covers the run-resolution helper
that makes it work.
"""

from tests.test_orchestration import FakeChat, _load_orchestration_tools


class FakeService:
    """Minimal service double exposing only the run lookup used by the tool."""

    def __init__(self, run=None):
        self.run = run
        self.searched = []

    def find_by_main_chat(self, chat_id):
        self.searched.append(chat_id)
        return self.run


def _resolver():
    module = _load_orchestration_tools()
    return module._resolve_run_id


def test_resolve_run_id_prefers_the_chat_binding():
    resolve = _resolver()
    chat = FakeChat("main", run_id="run-1")
    service = FakeService()

    assert resolve(service, chat) == "run-1"
    # A bound chat never triggers the disk lookup.
    assert service.searched == []


def test_resolve_run_id_falls_back_to_the_main_task_and_heals_the_chat():
    """The binding is in-memory only until the chat engine saves the turn."""
    resolve = _resolver()
    chat = FakeChat("main")
    run = type("Run", (), {"run_id": "run-2", "status": "running"})()
    service = FakeService(run=run)

    assert resolve(service, chat) == "run-2"
    assert service.searched == ["main"]
    assert chat.run_id == "run-2"
    assert chat.run_status == "running"


def test_resolve_run_id_is_empty_without_a_binding_or_a_run():
    resolve = _resolver()
    chat = FakeChat("main")

    assert resolve(FakeService(), chat) == ""
    assert resolve(FakeService(), FakeChat("")) == ""


def test_resolve_run_id_survives_a_failing_lookup():
    resolve = _resolver()

    class Boom:
        def find_by_main_chat(self, chat_id):
            raise RuntimeError("store unavailable")

    assert resolve(Boom(), FakeChat("main")) == ""
