"""
Tests for the "at most ONE response per chat" contract.

A chat turn creates its assistant response BEFORE the AI call and persists it
while it streams. Two overlapping turns therefore leave two (or more) messages
stuck on *"Processing request, please wait..."* for a SINGLE user message, both
streaming into the same transcript and both billing the chat.

The engine therefore:

* refuses to start a turn while a LIVE response owns the chat (does nothing),
* SEALS the response of a dead turn (stops it and closes its message) before
  starting a new one,
* stamps every response with the ``doc_id`` of the message it answers, so a
  response is never an orphan.

The tests use the real file-backed turn store in a temporary directory. No AI
provider, database or socket is involved.

Run with::

    cd api && pytest tests/chat/test_single_response_per_chat.py -q
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

# ``codx.junior.api.chat`` is imported FIRST on purpose: it loads
# ``codx.junior.engine``, which ``codx.junior.context`` needs to be resolved
# before the chat engine is imported (importing the chat engine on its own hits
# that circular import).
from codx.junior.api.chat import _seal_dead_responses, chat_cancel
from codx.junior.chat.chat_engine import ChatEngine
from codx.junior.chat.turn_store import get_turn_store, repair_incomplete_messages


def _chat(chat_id="chat-1", mode="chat", messages=None):
    return SimpleNamespace(
        id=chat_id,
        mode=mode,
        owner_project_id="project-1",
        messages=list(messages or []),
    )


def _open_response(doc_id="assistant-1", turn_id=None):
    return SimpleNamespace(
        role="assistant",
        doc_id=doc_id,
        content="* Processing request, please wait...*",
        done=False,
        hide=False,
        error=None,
        meta_data={"turn_id": turn_id} if turn_id else {},
    )


def _engine(store):
    """A ``ChatEngine`` bound to *store* without building a project session."""
    engine = object.__new__(ChatEngine)
    engine._turn_store_for_chat = lambda chat, turn_codx_path=None: store
    return engine


def _age_heartbeat(store, record, seconds=3600):
    """Backdate the heartbeat of *record* so the turn looks stalled."""
    record.heartbeat_at = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()
    store.save(record)


def _manager(store_path, chat=None):
    """Minimal ChatManager surface used by the repair and cancel paths."""
    return SimpleNamespace(
        settings=SimpleNamespace(codx_path=store_path),
        find_by_id=lambda chat_id: chat,
        update_message=lambda chat, message: None,
        save_chat=lambda chat: None,
    )


def _request(store_path, manager):
    """Minimal FastAPI request surface used by ``chat_cancel``."""
    session = SimpleNamespace(
        get_chat_manager=lambda: manager,
        settings=SimpleNamespace(codx_path=store_path),
    )
    return SimpleNamespace(
        state=SimpleNamespace(codx_junior_session=session),
        query_params={},
    )

def test_a_chat_without_turns_can_start_a_response(tmp_path):
    engine = _engine(get_turn_store(str(tmp_path)))

    assert engine._running_response_turns(_chat()) == (False, [])


def test_a_live_response_blocks_a_second_turn(tmp_path):
    store = get_turn_store(str(tmp_path))
    engine = _engine(store)
    record = store.begin(chat=_chat())
    store.mark_running(record)

    blocked, superseded = engine._running_response_turns(_chat())

    try:
        assert blocked is True
        assert superseded == []
        # Nothing was started and nothing was stopped: the live turn owns the
        # chat, so the second request is simply ignored.
        assert [item.turn_id for item in store.find_active("chat-1")] == [
            record.turn_id
        ]
        assert not store.cancel_requested(record.turn_id)
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_a_sealed_response_is_stopped_so_the_new_turn_can_run(tmp_path):
    store = get_turn_store(str(tmp_path))
    engine = _engine(store)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    _age_heartbeat(store, record)
    assert store.is_stale(record) is True

    blocked, superseded = engine._running_response_turns(_chat())

    try:
        assert blocked is False
        assert superseded == [record.turn_id]
        # The dead turn is stopped too, so a recovery sweep never resumes it
        # while the new response is being produced.
        assert store.cancel_requested(record.turn_id)
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_the_message_of_a_sealed_turn_is_closed_even_while_its_lock_is_held(tmp_path):
    """A stalled turn can never answer: its "processing" message is closed."""
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    _age_heartbeat(store, record)

    chat = _chat(messages=[_open_response(turn_id=record.turn_id)])
    blocked, superseded = _engine(store)._running_response_turns(chat)

    try:
        assert blocked is False
        # The lock is still held by the stalled owner, so the turn must be
        # repaired explicitly (``superseded_turn_ids``) instead of skipped.
        repaired = repair_incomplete_messages(
            chat,
            _manager(codx_path),
            note="",
            error="The turn was interrupted",
            skip_turn_ids=store.live_turn_ids(),
            superseded_turn_ids=superseded,
        )
        assert repaired == 1
        assert chat.messages[0].done is True
        assert chat.messages[0].hide is True
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_orchestration_owned_chats_keep_their_own_policy(tmp_path):
    store = get_turn_store(str(tmp_path))
    engine = _engine(store)
    record = store.begin(chat=_chat(mode="orchestrate"))
    store.mark_running(record)

    try:
        # The orchestration worker schedules these turns and applies the guard
        # itself, so the engine must not swallow a supervisor round.
        assert engine._running_response_turns(_chat(mode="orchestrate")) == (False, [])
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_a_response_is_associated_with_the_user_message_it_answers():
    response = ChatEngine._new_response_message(SimpleNamespace(doc_id="user-1"))

    assert response.role == "assistant"
    assert response.reply_to == "user-1"


def test_a_response_without_a_user_message_has_no_association():
    assert ChatEngine._new_response_message(None).reply_to is None


def test_the_request_of_a_turn_is_never_a_previous_response():
    user_message = SimpleNamespace(role="user", doc_id="user-1")
    open_response = SimpleNamespace(role="assistant", doc_id="assistant-2")

    assert ChatEngine._last_user_message([user_message, open_response]) is user_message


def test_reading_a_chat_closes_the_response_of_a_dead_turn(tmp_path):
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    # The process died: the record stays, the OS released the lock.
    store.release(record.turn_id)

    chat = _chat(messages=[_open_response(turn_id=record.turn_id)])

    repaired = _seal_dead_responses(
        chat, _manager(codx_path), SimpleNamespace(codx_path=codx_path)
    )

    assert repaired == 1
    assert chat.messages[0].done is True
    assert chat.messages[0].hide is True
    assert chat.messages[0].error == "The turn was interrupted"


def test_reading_a_chat_leaves_the_message_of_a_live_turn_alone(tmp_path):
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    chat = _chat(messages=[_open_response(turn_id=record.turn_id)])

    try:
        repaired = _seal_dead_responses(
            chat, _manager(codx_path), SimpleNamespace(codx_path=codx_path)
        )
        assert repaired == 0
        assert chat.messages[0].done is False
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_reading_a_finished_chat_never_probes_the_turn_store(tmp_path):
    chat = _chat(messages=[SimpleNamespace(role="assistant", done=True)])

    assert (
        _seal_dead_responses(chat, _manager(str(tmp_path)), SimpleNamespace(codx_path=str(tmp_path)))
        == 0
    )


def test_a_stalled_turn_holding_its_lock_is_not_live(tmp_path):
    """The lock says "a process owns it", the heartbeat says "it is dead"."""
    store = get_turn_store(str(tmp_path))
    record = store.begin(chat=_chat())
    store.mark_running(record)
    try:
        assert store.live_turn_ids() == [record.turn_id]

        # A hung AI request or a blocked tool keeps the lock while nothing
        # progresses: the turn must no longer be reported as live.
        _age_heartbeat(store, record)

        assert store.is_owned(record.turn_id) is True
        assert store.live_turn_ids() == []
        # Diagnostics still see the owner, and the record is kept so recovery
        # can stop/restart it.
        assert store.live_turn_ids(include_stale=True) == [record.turn_id]
        assert [item.turn_id for item in store.find_active("chat-1")] == [
            record.turn_id
        ]
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_reading_a_chat_closes_the_response_of_a_stalled_turn(tmp_path):
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    _age_heartbeat(store, record)

    chat = _chat(messages=[_open_response(turn_id=record.turn_id)])

    try:
        repaired = _seal_dead_responses(
            chat, _manager(codx_path), SimpleNamespace(codx_path=codx_path)
        )
        assert repaired == 1
        assert chat.messages[0].done is True
        assert chat.messages[0].hide is True
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_stopping_a_chat_closes_a_stalled_response_whose_lock_is_held(tmp_path):
    """Stop must win: nobody else would ever close that bubble."""
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    _age_heartbeat(store, record)

    chat = _chat(messages=[_open_response(turn_id=record.turn_id)])
    manager = _manager(codx_path, chat)

    try:
        result = chat_cancel(_request(codx_path, manager), {"chat_id": "chat-1"})

        assert result["cancelled"] is True
        assert result["repaired_messages"] == 1
        assert chat.messages[0].done is True
        assert chat.messages[0].hide is True
        assert chat.messages[0].error == "The turn was stopped by the user"
        # A recovery sweep must never resume the turn the user stopped.
        assert store.cancel_requested(record.turn_id) is True
    finally:
        store.finalize(record, "cancelled", error="test cleanup")


def test_stopping_a_chat_twice_is_idempotent(tmp_path):
    codx_path = str(tmp_path)
    store = get_turn_store(codx_path)
    record = store.begin(chat=_chat())
    store.mark_running(record)
    _age_heartbeat(store, record)

    chat = _chat(messages=[_open_response(turn_id=record.turn_id)])
    manager = _manager(codx_path, chat)
    request = _request(codx_path, manager)

    first = chat_cancel(request, {"chat_id": "chat-1"})
    assert first["cancelled"] is True
    assert first["repaired_messages"] == 1

    store.finalize(record, "cancelled", error="test cleanup")

    second = chat_cancel(request, {"chat_id": "chat-1"})

    # Nothing left to stop: still a success, with no work done.
    assert second["cancelled"] is True
    assert second["repaired_messages"] == 0
    assert second["stopped_turns"] == 0
    assert chat.messages[0].done is True