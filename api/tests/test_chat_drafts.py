"""Tests for the chat composer draft (``Message.unsent``) and its invisibility.

Covers:
* the ``unsent`` marker on :class:`codx.junior.db.Message`;
* ``_visible_messages`` stripping every draft from a reader;
* ``upsert_unsent_message`` keeping ONE draft per chat with a stable ``doc_id``;
* ``get_unsent_message`` / ``delete_unsent_message`` semantics;
* the send path (``POST /chats/message``) consuming the draft;
* the draft never reaching the conversation serialisation used by
  ``GET /chats`` (``_conversation_messages``) nor the LLM prompt
  (:meth:`ChatPromptBuilder.collect_prior_messages`).

Everything is self-contained: the chat store is an in-memory stub, so there is
no network, no Mongo and no LLM call.
"""

import asyncio
import types

from codx.junior.api import chat as chat_api
from codx.junior.chat.chat_prompt_builder import ChatPromptBuilder
from codx.junior.db import (
    Chat,
    Message,
    _visible_messages,
    delete_unsent_message,
    get_unsent_message,
    upsert_unsent_message,
)

DRAFT_TEXT = "half a thought about the login timeout"


class _StubChatManager:
    """Minimal ``ChatManager`` surface used by the draft helpers and routes."""

    def __init__(self, *chats):
        self.chats = {chat.id: chat for chat in chats}
        self.added = []
        self.updated = []
        self.removed = []

    def find_by_id(self, chat_id=None, id=None):
        return self.chats.get(chat_id or id)

    def add_message(self, chat, message):
        message.doc_id = message.doc_id or f"msg-{len(chat.messages) + 1}"
        chat.messages.append(message)
        self.added.append(message)
        return chat

    def update_message(self, chat, message):
        for index, current in enumerate(chat.messages):
            if current.doc_id == message.doc_id:
                chat.messages[index] = message
                break
        else:
            chat.messages.append(message)
        self.updated.append(message)
        return chat

    def remove_message(self, chat, message_doc_id):
        chat.messages = [m for m in chat.messages if m.doc_id != message_doc_id]
        self.removed.append(message_doc_id)
        return chat


class _FakeRequest:
    """Request double: awaited ``json()``, ``state`` session and query params."""

    def __init__(self, payload=None, manager=None, query_params=None, user=None):
        self._payload = payload or {}
        self.query_params = query_params or {}
        self.state = types.SimpleNamespace(
            codx_junior_session=types.SimpleNamespace(
                get_chat_manager=lambda: manager
            ),
            user=user,
        )

    async def json(self):
        return self._payload


def _new_chat(chat_id="chat-1"):
    """Return a (manager, persisted chat) pair backed by the in-memory stub."""
    chat = Chat(id=chat_id, name="Draft chat")
    return _StubChatManager(chat), chat


def test_message_unsent_defaults_to_false():
    assert Message().unsent is False
    assert Message(content="hello").unsent is False
    assert Message(unsent=True).unsent is True


def test_visible_messages_strips_every_unsent_message():
    first = Message(role="user", content="hello", doc_id="m1")
    draft = Message(role="user", content=DRAFT_TEXT, doc_id="m2", unsent=True)
    answer = Message(role="assistant", content="hi there", doc_id="m3")

    visible = _visible_messages([first, draft, answer])

    assert visible == [first, answer]
    assert draft not in visible
    assert all(not message.unsent for message in visible)
    # Defensive inputs never blow up.
    assert _visible_messages(None) == []
    assert _visible_messages([]) == []


def test_upsert_keeps_one_draft_with_stable_doc_id():
    manager, chat = _new_chat()

    first = upsert_unsent_message(
        chat_id=chat.id,
        content=DRAFT_TEXT,
        author="ana",
        chat_manager=manager,
    )
    assert first.unsent is True
    assert first.content == DRAFT_TEXT
    assert first.user == "ana"
    assert first.doc_id
    assert len(manager.added) == 1

    second = upsert_unsent_message(
        chat_id=chat.id,
        content=DRAFT_TEXT + " (updated)",
        chat_manager=manager,
    )

    # Same row, latest content: the client keeps a stable key while typing.
    assert second.doc_id == first.doc_id
    assert second.content == DRAFT_TEXT + " (updated)"
    # No new insert, one update, and the author is preserved when omitted.
    assert manager.added == [first]
    assert manager.updated == [second]
    assert second.user == "ana"
    assert len(chat.messages) == 1
    assert [m for m in chat.messages if m.unsent] == [second]


def test_get_and_delete_unsent_message():
    manager, chat = _new_chat()

    assert get_unsent_message(chat.id, chat_manager=manager) is None

    draft = upsert_unsent_message(chat.id, DRAFT_TEXT, chat_manager=manager)
    stored = get_unsent_message(chat.id, chat_manager=manager)
    assert stored is not None
    assert stored.doc_id == draft.doc_id
    assert stored.content == DRAFT_TEXT

    assert delete_unsent_message(chat.id, chat_manager=manager) is True
    assert delete_unsent_message(chat.id, chat_manager=manager) is False
    assert get_unsent_message(chat.id, chat_manager=manager) is None
    assert chat.messages == []
    # An unknown chat is not an error either.
    assert get_unsent_message("missing", chat_manager=manager) is None
    assert delete_unsent_message("missing", chat_manager=manager) is False


def test_upsert_requires_a_known_chat():
    manager, _chat = _new_chat()
    try:
        upsert_unsent_message("missing", DRAFT_TEXT, chat_manager=manager)
    except ValueError as ex:
        assert "missing" in str(ex)
    else:  # pragma: no cover - the assertion below would be the failure
        raise AssertionError("upsert_unsent_message must reject unknown chats")


def test_sending_a_message_consumes_the_draft():
    manager, chat = _new_chat()
    draft = upsert_unsent_message(chat.id, DRAFT_TEXT, chat_manager=manager)

    request = _FakeRequest(
        payload={"chat_id": chat.id, "message": {"content": DRAFT_TEXT, "role": "user"}},
        manager=manager,
    )
    updated = asyncio.run(chat_api.api_add_message(request))

    assert [message.content for message in updated.messages] == [DRAFT_TEXT]
    assert [m for m in updated.messages if m.unsent] == []
    # The draft row was the one deleted, and nothing is left behind.
    assert manager.removed == [draft.doc_id]
    assert get_unsent_message(chat.id, chat_manager=manager) is None


def test_sending_the_draft_itself_keeps_it():
    """A payload that IS the draft must not delete it (``unsent`` in payload)."""
    manager, chat = _new_chat()
    upsert_unsent_message(chat.id, DRAFT_TEXT, chat_manager=manager)

    request = _FakeRequest(
        payload={
            "chat_id": chat.id,
            "message": {"content": DRAFT_TEXT, "role": "user", "unsent": True},
        },
        manager=manager,
    )
    asyncio.run(chat_api.api_add_message(request))

    assert manager.removed == []
    assert get_unsent_message(chat.id, chat_manager=manager) is not None


def test_conversation_serialisation_hides_the_draft():
    """``GET /chats`` uses ``_conversation_messages``: it must return no draft."""
    manager, chat = _new_chat()
    upsert_unsent_message(chat.id, DRAFT_TEXT, chat_manager=manager)

    # The chat DOES store the draft ...
    assert len(chat.messages) == 1
    assert chat.messages[0].unsent is True

    # ... but every listing/reader sees an empty conversation.
    assert chat_api._conversation_messages(chat) == []

    # The composer that owns it still reads it through the dedicated route.
    draft_payload = chat_api.api_get_chat_draft(
        _FakeRequest(manager=manager, query_params={"chat_id": chat.id})
    )
    assert draft_payload["content"] == DRAFT_TEXT

    # A real message is returned as before.
    chat.messages.append(Message(role="assistant", content="answer", doc_id="m2"))
    visible = chat_api._conversation_messages(chat)
    assert [message.doc_id for message in visible] == ["m2"]


def test_prompt_history_skips_the_draft():
    """The LLM history never carries the composer draft."""
    manager, chat = _new_chat()
    chat.messages = [
        Message(role="user", content="real question", doc_id="m1"),
    ]
    upsert_unsent_message(chat.id, DRAFT_TEXT, chat_manager=manager)
    chat.messages.append(
        Message(role="assistant", content="partial answer", doc_id="m3")
    )

    builder = ChatPromptBuilder(None, None)
    prior = builder.collect_prior_messages(chat=chat, include_parent=False)

    assert [message.doc_id for message in prior] == ["m1"]
    assert all(DRAFT_TEXT not in message.content for message in prior)
