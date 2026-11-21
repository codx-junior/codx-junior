"""Integration tests for the index-backed ChatManager read paths.

These exercise the real ChatManager (files + SQLite index) on a throwaway
project. They are intentionally independent from the HTTP layer.

Run with::

    PYTHONPATH=api python -m pytest api/tests/chat/test_chat_manager_index.py -q
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from codx.junior.chat_manager import ChatManager
from codx.junior.db import Chat, Message


class _Events:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _chat(chat_id: str, name: str, content: str, user: str = "alice") -> Chat:
    now = "2026-11-21T10:00:00"
    return Chat(
        id=chat_id,
        name=name,
        description=f"about {name}",
        board="kanban",
        column="tasks",
        mode="task",
        users=[user],
        created_at=now,
        updated_at=now,
        messages=[
            Message(
                doc_id="m1",
                role="user",
                user=user,
                content=content,
                created_at=now,
                updated_at=now,
                done=True,
            )
        ],
    )


class ChatManagerIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.codx = os.path.join(self.tmp.name, ".codx")
        self.tasks = os.path.join(self.codx, "tasks", "kanban", "tasks")
        os.makedirs(self.tasks)
        self.old_mode = os.environ.get("CODX_JUNIOR_CHAT_INDEX")
        os.environ["CODX_JUNIOR_CHAT_INDEX"] = "on"
        self.addCleanup(self._restore_mode)
        self.manager = ChatManager(
            settings=SimpleNamespace(
                codx_path=self.codx, project_id="p-test", project_name="p-test"
            ),
            event_manager=_Events(),
        )

    def _restore_mode(self) -> None:
        if self.old_mode is None:
            os.environ.pop("CODX_JUNIOR_CHAT_INDEX", None)
        else:
            os.environ["CODX_JUNIOR_CHAT_INDEX"] = self.old_mode

    def _save(self, chat: Chat) -> Chat:
        return self.manager.save_chat(chat=chat)

    def _wait_ready(self, timeout: float = 10.0) -> None:
        self.manager._start_chat_index_build()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.manager._chat_index_ready():
                return
            time.sleep(0.05)
        self.fail("chat index did not become ready")

    def test_list_search_recent_and_id_are_index_backed(self):
        alpha = self._save(_chat("a", "Alpha", "deployment pipeline"))
        self._save(_chat("b", "Beta", "rollback procedure", user="bob"))
        self._wait_ready()

        chats = self.manager.list_chats()
        self.assertEqual({chat.id for chat in chats}, {"a", "b"})
        # List rows carry only the preview, not the conversation.
        self.assertEqual(len(chats[0].messages), 1)
        self.assertEqual(chats[0].message_count, 1)

        found = self.manager.find_by_id("a")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "Alpha")
        self.assertEqual(self.manager.find_chat_path("a"), alpha.file_path)

        result = self.manager.search_chats(query="deployment")
        self.assertGreaterEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["chat"]["id"], "a")

        recent = self.manager.get_recent_chats(user_id="bob")
        self.assertEqual(recent["total"], 1)
        self.assertEqual(recent["chats"][0].name, "Beta")

    def test_save_updates_index_immediately(self):
        chat = self._save(_chat("a", "Alpha", "first"))
        self._wait_ready()
        chat.messages.append(
            Message(
                doc_id="m2",
                role="assistant",
                user="agent",
                content="brand new answer",
                created_at="2026-11-21T11:00:00",
                updated_at="2026-11-21T11:00:00",
                done=True,
            )
        )
        self._save(chat)
        result = self.manager.search_chats(query="brand new answer")
        self.assertGreaterEqual(result["total"], 1)

    def test_per_user_drafts_do_not_rewrite_the_chat(self):
        chat = self._save(_chat("a", "Alpha", "hello"))
        self._wait_ready()
        before = os.stat(chat.file_path).st_mtime_ns
        first = self.manager.save_chat_draft("a", "alice", "alice draft")
        second = self.manager.save_chat_draft("a", "alice", "alice draft 2")
        self.assertEqual(first["doc_id"], second["doc_id"])
        self.assertIsNone(self.manager.get_chat_draft("a", "bob"))
        self.assertEqual(os.stat(chat.file_path).st_mtime_ns, before)


    def test_archive_and_restore_update_the_index(self):
        chat = self._save(_chat("a", "Alpha", "archive me"))
        self._wait_ready()
        entry = self.manager.archive_chat("a", user="alice")
        self.assertEqual(entry.get("chat_id") or entry.get("id"), "a")
        self.assertNotIn("a", [row.id for row in self.manager.list_chats()])
        self.assertIsNone(self.manager.find_by_id("a"))
        self.assertIn("a", [row.id for row in self.manager.archived_chats()])

        restored = self.manager.restore_chat("a", user="alice")
        self.assertIsNotNone(restored)
        self.assertIn("a", [row.id for row in self.manager.list_chats()])
    def test_reconcile_picks_up_external_edit(self):
        chat = self._save(_chat("a", "Alpha", "hello"))
        self._wait_ready()
        with open(chat.file_path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["description"] = "changed outside the API"
        with open(chat.file_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        self.manager._reconcile_chat_index()
        self.assertEqual(self.manager.find_by_id("a").description, "changed outside the API")


if __name__ == "__main__":
    unittest.main()
