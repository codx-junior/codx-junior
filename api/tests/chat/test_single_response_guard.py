"""
Tests for the "one live response per chat" rule.

A response is produced FOR one user message and a chat must have at most ONE
response in flight. Without the rule a single request ends up with several
assistant messages stuck on "Processing request, please wait..." because
overlapping turns and resumed turns each created their own bubble.

The rule under test:

* a LIVE response owns the chat -> a new turn is ignored (nothing is started),
* a SEALED (finished, dead or stalled) response can never answer -> it is
  stopped and closed, and the new turn starts,
* a RESUMED turn reuses its own open message instead of adding a second one,
* orchestration-owned chats keep their own scheduling policy.

Run with::

    cd api && pytest tests/chat/test_single_response_guard.py -q
"""

from types import SimpleNamespace

# Import the app package BEFORE the chat engine: the engine pulls
# ``codx.junior.context``, which imports ``codx.junior.engine`` back, so the
# reverse order hits a partially initialised module (same order the app uses).
import codx.junior.engine  # noqa: F401,E402

from codx.junior.chat.chat_engine import ChatEngine  # noqa: E402
from codx.junior.chat.turn_store import (
    ChatTurnRecord,
    STATUS_RUNNING,
    get_turn_store,
    repair_incomplete_messages,
)
from codx.junior.db import Chat, Message

STALE_STAMP = "2020-01-01T00:00:00+00:00"


def _chat(chat_id="chat-1", mode="chat", **extra):
    """Return a minimal chat surface (id, mode and orchestration markers)."""
    return SimpleNamespace(id=chat_id, mode=mode, owner_project_id="project-1", **extra)


def _engine(store):
    """Return a ChatEngine bound to *store* without running its constructor."""
    engine = ChatEngine.__new__(ChatEngine)
    engine.settings = SimpleNamespace(codx_path=store.codx_path, project_id="project-1")
    engine._turn_store_for_chat = lambda chat, turn_codx_path=None: store
    return engine


def _live_turn(store, chat_id, turn_id="turn-live"):
    record = ChatTurnRecord(
        turn_id=turn_id,
        chat_id=chat_id,
        codx_path=store.codx_path,
        status=STATUS_RUNNING,
    )
    store.save(record)
    assert store.claim(record)
    return record


def _stalled_turn(store, chat_id, turn_id="turn-stalled"):
    record = ChatTurnRecord(
        turn_id=turn_id,
        chat_id=chat_id,
        codx_path=store.codx_path,
        status=STATUS_RUNNING,
        heartbeat_at=STALE_STAMP,
        started_at=STALE_STAMP,
        created_at=STALE_STAMP,
    )
    store.save(record)
    # A stalled turn still HOLDS its lock (hung AI request, blocked tool).
    assert store.claim(record)
    return record


# --------------------------------------------------------------- association
def test_a_response_is_associated_with_the_message_it_answers():
    response = ChatEngine._new_response_message(Message(role="user", doc_id="doc-user"))
    assert response.reply_to == "doc-user"
    assert Message(role="assistant").reply_to is None


# ------------------------------------------------------ live response probe
def test_live_response_sees_a_running_turn_and_ignores_a_stalled_one(tmp_path):
    store = get_turn_store(str(tmp_path))
    record = _live_turn(store, "chat-1")

    assert store.live_response("chat-1").turn_id == record.turn_id
    assert store.live_response("chat-2") is None

    # A turn that stopped making progress is dead even while it holds the lock.
    record.heartbeat_at = STALE_STAMP
    store.save(record)
    assert store.live_response("chat-1") is None


# ------------------------------------------------- engine: start / do nothing
def test_engine_ignores_a_new_turn_while_a_response_is_live(tmp_path):
    store = get_turn_store(str(tmp_path))
    _live_turn(store, "chat-1")
    engine = _engine(store)

    blocked, superseded = engine._running_response_turns(_chat("chat-1"))

    assert blocked is True
    assert superseded == []


def test_engine_allows_a_turn_on_an_idle_chat(tmp_path):
    engine = _engine(get_turn_store(str(tmp_path)))

    assert engine._running_response_turns(_chat("chat-1")) == (False, [])


def test_engine_seals_a_stalled_turn_and_starts_a_new_one(tmp_path):
    store = get_turn_store(str(tmp_path))
    _stalled_turn(store, "chat-1")
    engine = _engine(store)

    blocked, superseded = engine._running_response_turns(_chat("chat-1"))

    assert blocked is False
    assert superseded == ["turn-stalled"]
    # The stalled owner is asked to unwind through the cancellation watcher.
    assert store.cancel_requested("turn-stalled") is True


def test_orchestration_owned_chats_keep_their_own_policy(tmp_path):
    store = get_turn_store(str(tmp_path))
    _live_turn(store, "chat-1")
    engine = _engine(store)

    assert engine._running_response_turns(_chat("chat-1", run_id="run-1")) == (False, [])
    assert engine._running_response_turns(_chat("chat-1", mode="orchestrate")) == (False, [])


# ------------------------------------------------------- engine: resume re-use
def test_a_resumed_turn_reuses_its_open_response_message():
    chat = Chat(id="chat-1")
    message = Message(role="assistant", content="half answer", done=False)
    message.meta_data = {"turn_id": "turn-r"}
    chat.messages = [Message(role="user", content="go", doc_id="doc-user"), message]

    adopted = ChatEngine._open_response_message(chat, "turn-r")

    assert adopted is not None and adopted.doc_id == message.doc_id
    assert ChatEngine._open_response_message(chat, None) is None
    assert ChatEngine._open_response_message(chat, "turn-other") is None

    message.done = True
    assert ChatEngine._open_response_message(chat, "turn-r") is None


# ------------------------------------------------ sealing the message itself
def test_repair_skips_a_live_turn_but_closes_a_sealed_one(tmp_path):
    store = get_turn_store(str(tmp_path))
    _stalled_turn(store, "chat-1")

    chat = Chat(id="chat-1")
    message = Message(role="assistant", content="Processing request, please wait...", done=False)
    message.meta_data = {"turn_id": "turn-stalled"}
    chat.messages = [message]

    class _Manager:
        def update_message(self, chat=None, message=None):  # pragma: no cover - trivial
            return None

    live_turn_ids = {"turn-stalled"}
    # While the turn is considered live its message stays open.
    assert repair_incomplete_messages(
        chat, _Manager(), note="", skip_turn_ids=live_turn_ids
    ) == 0
    assert message.done is False

    # Sealed: the same message is closed even though its turn is in the skip set.
    assert repair_incomplete_messages(
        chat,
        _Manager(),
        note="",
        skip_turn_ids=live_turn_ids,
        superseded_turn_ids={"turn-stalled"},
    ) == 1
    assert message.done is True
    assert "processing request" not in message.content.lower()
    assert (message.meta_data or {}).get("interrupted") is True
