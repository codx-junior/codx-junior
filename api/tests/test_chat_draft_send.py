"""
Regression tests for the message-send path of a chat with an unsent draft.

The composer persists what the user is typing as an ``unsent`` message. When the
user finally sends it, two things must hold:

* the chat returned by the message endpoint NEVER carries that draft. The client
  merges the messages of the payload into the conversation, so a draft left in
  the list came back as a bubble and the same text appeared twice.
* sending the same message twice (the composer echoes it locally while the
  request is in flight) stores it once, keyed by ``doc_id``.

Run with::

    PYTHONPATH=api python -m pytest api/tests/test_chat_draft_send.py -q
"""

from types import SimpleNamespace

from codx.junior.chat_manager import ChatManager
from codx.junior.db import (
    Chat,
    Message,
    conversation_response,
    delete_unsent_message,
    get_unsent_message,
    upsert_unsent_message,
)


def _manager(codx_path: str) -> ChatManager:
    """Build a ChatManager on a throwaway codx folder (no project needed)."""
    settings = SimpleNamespace(
        codx_path=codx_path,
        project_id="p-test",
        project_name="draft-send-test",
    )
    return ChatManager(
        settings=settings,
        event_manager=SimpleNamespace(
            chat_event=lambda **kwargs: None,
            message_event=lambda **kwargs: None,
            message_removed_event=lambda **kwargs: None,
        ),
    )


def _chat_with_draft(tmp_path):
    manager = _manager(str(tmp_path))
    chat = manager.save_chat(chat=Chat(name="Status?", messages=[]))
    upsert_unsent_message(chat.id, "Status?", chat_manager=manager)
    return manager, manager.find_by_id(chat.id)


def test_conversation_response_strips_the_draft(tmp_path):
    manager, chat = _chat_with_draft(tmp_path)
    assert [m.content for m in chat.messages] == ["Status?"]
    assert chat.messages[0].unsent is True

    payload = conversation_response(chat)

    assert payload is chat
    assert payload.messages == []
    # A dict (error payload) and None are returned untouched
    assert conversation_response({"error": "boom"}) == {"error": "boom"}
    assert conversation_response(None) is None


def test_send_leaves_no_draft_in_the_response(tmp_path):
    """The exact sequence of ``POST /chats/message``."""
    manager, chat = _chat_with_draft(tmp_path)

    updated_chat = manager.add_message(
        chat=chat, message=Message(role="user", content="Status?", user="gbrian")
    )
    # The draft is still on the in-memory chat at this point: this is exactly
    # what the response used to carry, and the client rendered it as a second
    # "Status?" bubble.
    assert any(m.unsent for m in updated_chat.messages)
    assert delete_unsent_message(chat.id, chat_manager=manager) is True

    response = conversation_response(updated_chat)

    assert [m.content for m in response.messages] == ["Status?"]
    assert all(not m.unsent for m in response.messages)
    # The draft is gone from disk as well: only the real message remains.
    stored = manager.find_by_id(chat.id)
    assert [m.content for m in stored.messages] == ["Status?"]
    assert get_unsent_message(chat.id, chat_manager=manager) is None


def test_local_echo_and_server_store_share_one_message(tmp_path):
    """The optimistic message the client renders is the one the server stores."""
    manager, chat = _chat_with_draft(tmp_path)
    message = Message(
        doc_id="0f5cbfa0-1c0e-4f2a-9f2f-2d6f8ba5c111",
        role="user",
        content="Status?",
        user="gbrian",
    )

    manager.add_message(chat=chat, message=message)
    # The client retries / the socket echoes it back: still ONE message.
    manager.add_message(chat=manager.find_by_id(chat.id), message=message)

    stored = manager.find_by_id(chat.id)
    assert [m.doc_id for m in stored.messages] == [message.doc_id]
    assert conversation_response(stored).messages[0].content == "Status?"
