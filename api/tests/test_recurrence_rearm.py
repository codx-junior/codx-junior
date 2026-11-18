"""Tests for the recurrence re-arm hook of the autonomy worker.

A recurring task is delivered by a shift, closed by the run and must then be
armed again to its next occurrence. ``ActorService._consume_agenda`` is the
single place where a wake is settled after a shift, so it is also the place
where a recurrence re-arms itself. These tests pin that behaviour:

* ``history`` reopens the SAME card (status, progress and home column reset) so
  the next run sees what the previous ones did.
* ``clean`` closes the delivered card and creates a NEW one for the next run
  (same name/description/board/profiles, no messages).
* An unreadable recurrence consumes the wake instead of making the worker spin.
* A one-shot task (no recurrence) is still consumed, and an open task keeps its
  wake so the next shift can finish it.
"""

import unittest
import uuid
from datetime import datetime, timezone as dt_timezone

from codx.junior.autonomy.recurrence import next_occurrence, parse_schedule
from codx.junior.autonomy.service import ActorService


class FakeChat:
    """Minimal stand-in for a task, with every field the re-arm touches."""

    def __init__(self, **fields):
        defaults = dict(
            id=str(uuid.uuid4()),
            name="Weekly review",
            description="Review the week changes and create a report",
            board="kanban",
            column="Done",
            status="done",
            progress=100,
            wake_at="",
            schedule=None,
            schedule_context="history",
            schedule_column=None,
            priority=None,
            profiles=["analyst"],
            file_list=["src/app.py"],
            tags=["report"],
            sprints=["Sprint 1"],
            project_id=None,
            owner_project_id=None,
            messages=[],
        )
        defaults.update(fields)
        self.__dict__.update(defaults)


class FakeManager:
    """Records the writes so the tests can inspect what was persisted."""

    def __init__(self, chats=None):
        self.chats = {chat.id: chat for chat in (chats or [])}
        self.saved = []
        self.created = []

    def find_by_id(self, chat_id=None, **_kwargs):
        return self.chats.get(chat_id)

    def save_chat(self, chat=None, **_kwargs):
        if getattr(chat, "id", None) in (None, ""):
            chat.id = str(uuid.uuid4())
            self.created.append(chat)
        self.chats[chat.id] = chat
        self.saved.append(chat)
        return chat


class Harness(ActorService):
    """ActorService with the real closing rule but no worker or LLM wiring."""

    def __init__(self, manager):
        self.chat_manager = manager
        self.manager = manager

    # Keep the production rule: a task is closed by its status or its column.
    _is_closed = staticmethod(ActorService._is_closed)


PAST = "2020-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"


def _as_aware(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt_timezone.utc)
    return moment


class RearmHistoryTest(unittest.TestCase):
    """``schedule_context='history'`` reopens the same card."""

    def test_reopens_the_same_card_and_keeps_its_id(self):
        chat = FakeChat(
            status="done",
            progress=100,
            column="Done",
            wake_at=PAST,
            schedule="every friday at 12:00",
            schedule_context="history",
            schedule_column="To Do",
        )
        manager = FakeManager([chat])
        Harness(manager)._consume_agenda([{"id": chat.id}])

        self.assertEqual(len(manager.created), 0, "history must not fork the card")
        self.assertEqual(chat.status, "todo")
        self.assertEqual(chat.progress, 0)
        self.assertEqual(chat.column, "To Do", "the card goes back to its home column")
        self.assertEqual(chat.schedule, "every friday at 12:00")
        self.assertGreater(
            _as_aware(chat.wake_at),
            datetime.now(dt_timezone.utc),
            "the next run must never be armed in the past",
        )

    def test_the_home_column_falls_back_to_the_current_one(self):
        chat = FakeChat(
            status="done",
            column="Review",
            wake_at=PAST,
            schedule="every day at 09:00",
            schedule_context="history",
            schedule_column=None,
        )
        Harness(FakeManager([chat]))._consume_agenda([{"id": chat.id}])
        self.assertEqual(chat.column, "Review")
        self.assertGreater(_as_aware(chat.wake_at), datetime.now(dt_timezone.utc))

    def test_every_run_is_armed_and_never_spins(self):
        for text in ("every 2 hours", "every day at 09:00", "every monday at 08:30"):
            with self.subTest(schedule=text):
                chat = FakeChat(
                    status="done", wake_at=PAST, schedule=text, schedule_context="history"
                )
                Harness(FakeManager([chat]))._consume_agenda([{"id": chat.id}])
                self.assertGreater(_as_aware(chat.wake_at), datetime.now(dt_timezone.utc))


class RearmCleanTest(unittest.TestCase):
    """``schedule_context='clean'`` hands the run over to a brand new card."""

    def test_creates_a_fresh_card_without_history(self):
        chat = FakeChat(
            status="done",
            wake_at=PAST,
            schedule="every friday at 12:00",
            schedule_context="clean",
            schedule_column="To Do",
        )
        manager = FakeManager([chat])
        Harness(manager)._consume_agenda([{"id": chat.id}])

        self.assertEqual(len(manager.created), 1, "clean must fork exactly one card")
        clone = manager.created[0]
        self.assertNotEqual(clone.id, chat.id)
        self.assertEqual(clone.name, chat.name)
        self.assertEqual(clone.description, chat.description)
        self.assertEqual(clone.board, chat.board)
        self.assertEqual(clone.column, "To Do")
        self.assertEqual(clone.status, "todo")
        self.assertEqual(clone.schedule, "every friday at 12:00")
        self.assertEqual(clone.schedule_context, "clean")
        self.assertEqual(clone.profiles, chat.profiles)
        self.assertEqual(clone.tags, chat.tags)
        self.assertGreater(_as_aware(clone.wake_at), datetime.now(dt_timezone.utc))
        # The new card starts with the instruction only: no previous messages.
        self.assertEqual(len(clone.messages), 1)
        self.assertEqual(clone.messages[0].role, "user")

    def test_the_delivered_card_stops_recurring_and_links_the_next_run(self):
        chat = FakeChat(
            status="done",
            wake_at=PAST,
            schedule="every friday at 12:00",
            schedule_context="clean",
        )
        manager = FakeManager([chat])
        Harness(manager)._consume_agenda([{"id": chat.id}])

        clone = manager.created[0]
        self.assertIsNone(chat.schedule, "the closed card no longer recurs")
        self.assertFalse(chat.wake_at, "the closed card must not wake again")
        self.assertIn(clone.id, chat.description)


class ConsumeAgendaTest(unittest.TestCase):
    """Everything that is not a readable recurrence behaves as before."""

    def test_unreadable_recurrence_consumes_the_wake(self):
        chat = FakeChat(status="done", wake_at=PAST, schedule="whenever I feel like it")
        manager = FakeManager([chat])
        Harness(manager)._consume_agenda([{"id": chat.id}])
        self.assertFalse(chat.wake_at)
        self.assertEqual(len(manager.created), 0)

    def test_one_shot_task_is_still_consumed(self):
        chat = FakeChat(status="done", wake_at=PAST, schedule=None)
        Harness(FakeManager([chat]))._consume_agenda([{"id": chat.id}])
        self.assertFalse(chat.wake_at)

    def test_an_open_task_keeps_its_wake(self):
        chat = FakeChat(
            status="doing", column="Doing", wake_at=PAST, schedule="every day at 09:00"
        )
        Harness(FakeManager([chat]))._consume_agenda([{"id": chat.id}])
        self.assertEqual(chat.wake_at, PAST, "the next shift must be able to finish it")
        self.assertEqual(chat.status, "doing")

    def test_a_wake_in_the_future_is_left_alone(self):
        chat = FakeChat(status="done", wake_at=FUTURE, schedule="every day at 09:00")
        Harness(FakeManager([chat]))._consume_agenda([{"id": chat.id}])
        self.assertEqual(chat.wake_at, FUTURE)

    def test_missing_tasks_are_ignored(self):
        manager = FakeManager([])
        # A card that vanished between the shift and the bookkeeping must not raise.
        Harness(manager)._consume_agenda([{"id": "does-not-exist"}])


class NextOccurrenceTest(unittest.TestCase):
    """The armed instant is always strictly in the future."""

    def test_recurrences_are_armed_in_the_future(self):
        for text in (
            "every friday at 12:00",
            "every day at 09:00",
            "every weekday at 09:00",
            "every 2 hours",
        ):
            with self.subTest(schedule=text):
                rule = parse_schedule(text)
                self.assertIsNotNone(rule, f"'{text}' must parse")
                armed = next_occurrence(rule)
                self.assertGreater(_as_aware(armed), datetime.now(dt_timezone.utc))


if __name__ == "__main__":
    unittest.main()
