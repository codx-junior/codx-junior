"""
Offline tests of the harness sessions service.

The service is a *composition* layer: it creates a normal task chat with the
harness contract and reads the existing task worktree/diff. These tests use a
fake ChatManager, so they exercise the contract (metadata, prompt brief, list
filtering, follow-ups) without an AI provider, a database or git.

Run with::

    cd api && pytest tests/test_harness_sessions.py -q
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from codx.junior.db import Chat, Message
from codx.junior.harness import prompts
from codx.junior.harness.sessions import (
    SESSION_DONE,
    SESSION_IDLE,
    HarnessSessionError,
    HarnessSessionService,
)


class FakeSettings:
    """The subset of ``CODXJuniorSettings`` the service reads."""

    def __init__(self, tmp_path):
        self.codx_path = str(tmp_path / ".codx")
        self.project_id = "project-1"
        self.project_name = "Project One"
        self.abs_project_path = str(tmp_path)


class FakeChatManager:
    """In-memory ChatManager with the methods the service uses."""

    def __init__(self):
        self.chats = {}
        self.kanban = {"version": 2, "boards": {}, "tags": {}}
        self.saved = []

    # storage -----------------------------------------------------------
    def save_chat(self, chat, chat_only=False):
        self.chats[chat.id] = chat
        self.saved.append(chat.id)
        return chat

    def find_by_id(self, chat_id):
        return self.chats.get(str(chat_id))

    def list_chats(self):
        return sorted(
            self.chats.values(),
            key=lambda chat: str(chat.updated_at or ""),
            reverse=True,
        )

    def add_message(self, chat, message):
        chat.messages.append(message)
        self.chats[chat.id] = chat
        return chat

    # catalogue ---------------------------------------------------------
    def load_kanban(self):
        return copy.deepcopy(self.kanban)

    def save_kanban(self, kanban):
        self.kanban = copy.deepcopy(kanban)


@pytest.fixture()
def service(tmp_path):
    """A service bound to a fake project and an in-memory chat manager."""
    return HarnessSessionService(
        settings=FakeSettings(tmp_path),
        chat_manager=FakeChatManager(),
        user="tester",
    )


# A composer attachment exactly as the browser posts it (client
# `ChatAttachment.toJSON()`): the harness composer pastes screenshots and
# uploads files, so the first message and every follow-up must store them.
SCREENSHOT = {
    "file_name": "screenshot.png",
    "file_type": "image/png",
    "file_size": 8,
    "base64_data": "iVBORw0KGgo=",
}


def test_create_stores_the_composer_attachments(service):
    """A pasted/uploaded file travels with the ticket message."""
    summary = service.create("What is wrong in this screenshot?", attachments=[SCREENSHOT])

    chat = service.chat_manager.chats[summary["session_id"]]
    message = chat.messages[0]
    assert len(message.attachments) == 1
    assert message.attachments[0].file_name == "screenshot.png"
    assert message.attachments[0].file_type == "image/png"
    assert message.attachments[0].base64_data == "iVBORw0KGgo="


def test_create_accepts_an_image_without_text(service):
    """Pasting only an image is a valid ticket (no empty-prompt error)."""
    summary = service.create("", attachments=[SCREENSHOT])

    chat = service.chat_manager.chats[summary["session_id"]]
    assert len(chat.messages[0].attachments) == 1
    assert summary["session_id"]


def test_create_still_rejects_an_empty_request(service):
    """Without text AND without attachments the prompt stays invalid."""
    with pytest.raises(HarnessSessionError):
        service.create("   ", attachments=[])


def test_prompt_stores_the_composer_attachments(service):
    """A follow-up image/fed file lands on the new user message."""
    summary = service.create("First ticket")

    service.prompt(summary["session_id"], "", attachments=[SCREENSHOT])

    chat = service.chat_manager.chats[summary["session_id"]]
    follow_up = chat.messages[-1]
    assert follow_up.role == "user"
    assert len(follow_up.attachments) == 1
    assert follow_up.attachments[0].file_name == "screenshot.png"


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------


def test_brief_keeps_the_request_and_appends_the_contract():
    """The first message keeps the ticket verbatim and appends the brief."""
    message = prompts.session_brief("Fix the empty board export")
    assert message.startswith("Fix the empty board export")
    assert prompts.HARNESS_BRIEF_MARKER in message
    assert "## What happened" in message
    assert "## What I did" in message
    assert "## Demo" in message
    assert "## Next tasks" in message
    assert "qa_run_tests" in message
    assert "Do not hand the turn back with a question" in message
    assert prompts.strip_session_brief(message) == "Fix the empty board export"


def test_strip_brief_is_a_no_op_for_a_regular_message():
    assert prompts.strip_session_brief("hello") == "hello"


def test_session_name_uses_the_first_meaningful_line():
    assert prompts.session_name("## Fix the export\n\ndetails") == "Fix the export"
    assert prompts.session_name("") == "New harness session"
    assert prompts.session_name("x" * 200).endswith("…")


# ---------------------------------------------------------------------------
# Create / list / detail
# ---------------------------------------------------------------------------


def test_create_persists_a_harness_task(service):
    """A session is a task chat: Harness board, task mode, harness profile."""
    summary = service.create("Fix the empty board CSV export")

    assert summary["session_id"]
    assert summary["name"] == "Fix the empty board CSV export"
    assert summary["phase"] == SESSION_IDLE

    chat = service.chat_manager.chats[summary["session_id"]]
    assert isinstance(chat, Chat)
    assert chat.board == prompts.HARNESS_BOARD
    assert chat.column == prompts.HARNESS_COLUMN
    assert chat.mode == "task"
    assert chat.profiles == [prompts.HARNESS_PROFILE]
    assert prompts.HARNESS_TAG in chat.tags
    assert chat.meta_data[prompts.HARNESS_META_KEY]["enabled"] is True

    assert len(chat.messages) == 1
    message = chat.messages[0]
    assert isinstance(message, Message)
    assert message.role == "user"
    assert message.content.startswith("Fix the empty board CSV export")
    assert prompts.HARNESS_BRIEF_MARKER in message.content
    assert message.user == "tester"


def test_create_saves_the_ticket_branch_in_the_chat(service):
    """The ticket branch lives in the chat model the harness tree groups by."""
    summary = service.create("Fix the empty board CSV export", branch="feature/export")

    chat = service.chat_manager.chats[summary["session_id"]]
    assert chat.branch == "feature/export"
    # The descriptor keeps a copy so older chats stay readable.
    assert chat.meta_data[prompts.HARNESS_META_KEY]["branch"] == "feature/export"
    assert summary["branch"] == "feature/export"
    assert [item["branch"] for item in service.list()] == ["feature/export"]


def test_a_chat_without_a_branch_is_unknown(service, monkeypatch):
    """An unrecorded branch is unknown: it is never guessed from the checkout."""
    summary = service.create("Legacy ticket")
    chat = service.chat_manager.chats[summary["session_id"]]
    chat.branch = ""
    chat.meta_data = {prompts.HARNESS_META_KEY: {"enabled": True}}
    monkeypatch.setattr(
        "codx.junior.harness.sessions.current_branch", lambda path: "main"
    )

    assert service.summary(chat)["branch"] == ""


def test_create_always_adds_the_harness_profile_and_dedupes(service):
    summary = service.create(
        "Add the export header",
        profiles=["software_developer", "codx-junior"],
        model="deepseek-chat",
        budget_cxjcoins=2.5,
    )
    chat = service.chat_manager.chats[summary["session_id"]]
    assert chat.profiles == ["codx-junior", "software_developer"]
    assert chat.llm_model == "deepseek-chat"
    assert chat.estimation_cost == 2.5


def test_create_does_not_duplicate_a_harness_tier(service):
    summary = service.create(
        "Deep task",
        profiles=["codx-junior-max", "software_developer"],
    )
    chat = service.chat_manager.chats[summary["session_id"]]
    # max already inherits codx-junior: adding the base again would duplicate it.
    assert chat.profiles == ["codx-junior-max", "software_developer"]


def test_create_rejects_an_empty_prompt(service):
    with pytest.raises(HarnessSessionError):
        service.create("   ")


def test_create_ensures_the_harness_board(service):
    service.create("First session")
    boards = service.chat_manager.kanban["boards"]
    assert prompts.HARNESS_BOARD in boards
    columns = {column["title"] for column in boards[prompts.HARNESS_BOARD]["columns"]}
    assert columns == {"Backlog", "Working", "Done"}


def test_list_only_returns_harness_sessions(service):
    created = service.create("First session")
    # A normal task on another board, not a session.
    service.chat_manager.save_chat(
        Chat(
            id="regular-chat",
            name="Regular card",
            board="kanban",
            column="tasks",
            mode="task",
            messages=[Message(doc_id="m1", role="user", content="hi")],
        )
    )
    sessions = service.list()
    assert [item["session_id"] for item in sessions] == [created["session_id"]]


def test_list_filters_by_phase(service):
    done = service.create("Done session")
    chat = service.chat_manager.chats[done["session_id"]]
    chat.messages.append(
        Message(doc_id="assistant-1", role="assistant", content="Delivered", done=True)
    )
    assert service.list(phase=SESSION_DONE)[0]["session_id"] == done["session_id"]
    assert service.list(phase=SESSION_IDLE) == []


def test_get_returns_the_chat_metrics_and_empty_extras(service):
    created = service.create("Investigate the crash")
    detail = service.get(created["session_id"])

    assert detail["session"]["session_id"] == created["session_id"]
    assert detail["chat"]["id"] == created["session_id"]
    assert detail["metrics"]["messages"] == 1
    assert detail["metrics"]["tool_calls"] == 0
    assert detail["workspace"] is None
    assert detail["diff"]["files"] == []
    assert detail["files"] == []
    assert detail["run"] is None
    assert isinstance(detail["audit"], list)


def test_get_refuses_a_regular_chat(service):
    service.chat_manager.save_chat(
        Chat(id="regular-chat", name="Card", board="kanban", column="tasks")
    )
    with pytest.raises(HarnessSessionError):
        service.get("regular-chat")


def test_get_refuses_an_unknown_session(service):
    with pytest.raises(HarnessSessionError):
        service.get("missing")


# ---------------------------------------------------------------------------
# Follow-up
# ---------------------------------------------------------------------------


def test_prompt_appends_a_user_message(service):
    created = service.create("Fix it")
    detail = service.prompt(created["session_id"], "Also add a regression test")

    assert detail["metrics"]["messages"] == 2
    chat = service.chat_manager.chats[created["session_id"]]
    assert chat.messages[-1].role == "user"
    assert chat.messages[-1].content == "Also add a regression test"
    # The follow-up is stored verbatim: the contract only belongs to the ticket.
    assert prompts.HARNESS_BRIEF_MARKER not in chat.messages[-1].content


def test_prompt_rejects_an_empty_message(service):
    created = service.create("Fix it")
    with pytest.raises(HarnessSessionError):
        service.prompt(created["session_id"], "  ")


def test_phase_reports_running_and_failed(service):
    created = service.create("Run me")
    chat = service.chat_manager.chats[created["session_id"]]

    chat.task_process_state = "running"
    assert service.summary(chat)["phase"] == "running"

    chat.task_process_state = "error"
    chat.task_process_error = "provider exploded"
    assert service.summary(chat)["phase"] == "failed"

    chat.task_process_state = "idle"
    assert service.summary(chat)["phase"] == SESSION_IDLE

# ---------------------------------------------------------------------------
# Effort: switching the tier while continuing a session
# ---------------------------------------------------------------------------


def test_prompt_updates_the_effort_tier_of_the_session(service):
    """A follow-up sent as Deep work persists the max tier on the chat."""
    created = service.create("Fix it", profiles=["codx-junior-max", "software_developer"])

    service.prompt(
        created["session_id"],
        "Continue with the full suite",
        profiles=["codx-junior", "software_developer"],
    )

    chat = service.chat_manager.chats[created["session_id"]]
    assert chat.profiles == ["codx-junior", "software_developer"]
    assert chat.messages[-1].profiles == ["codx-junior", "software_developer"]


def test_prompt_without_profiles_keeps_the_session_tier(service):
    created = service.create("Fix it", profiles=["codx-junior-max"])

    service.prompt(created["session_id"], "Continue")

    chat = service.chat_manager.chats[created["session_id"]]
    assert chat.profiles == ["codx-junior-max"]
    assert chat.messages[-1].profiles == []
    # The engine resolves the tier from the chat when the message has none.
    assert service.get(created["session_id"])["session"]["profiles"] == ["codx-junior-max"]

def test_get_repairs_archived_transcripts(service):
    """Legacy task-mode cleanup archived prior turns; opening repairs them."""
    created = service.create("Fix the export")
    chat = service.chat_manager.chats[created["session_id"]]
    chat.messages.append(
        Message(doc_id="assistant-1", role="assistant", content="First answer", hide=True)
    )

    detail = service.get(created["session_id"])

    # The conversation is shown in full and the repair is persisted, so the
    # next turn also receives the previous work as history.
    assert detail["chat"]["messages"][1]["hide"] is False
    assert service.chat_manager.chats[created["session_id"]].messages[1].hide is False
