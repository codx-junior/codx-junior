"""Unit tests for the per-project chat index (stdlib only).

Run with::

    python -m unittest api/tests/chat/test_chat_index.py

The index must stay importable without pydantic/app dependencies: these tests
exercise it directly with plain dictionaries.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# Make ``api/`` importable when the test is run from the repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from codx.junior.chat.chat_index import ChatIndexStore  # noqa: E402


def chat(
    chat_id: str,
    name: str,
    content: str,
    board: str = "kanban",
    column: str = "tasks",
    **extra,
):
    """Build a minimal chat document."""
    now = datetime.now().isoformat()
    return {
        "id": chat_id,
        "name": name,
        "description": extra.pop("description", f"about {name}"),
        "board": board,
        "column": column,
        "status": extra.pop("status", "todo"),
        "mode": "task",
        "created_at": now,
        "updated_at": now,
        "messages": [
            {
                "doc_id": "m1",
                "role": "user",
                "user": "alice",
                "content": content,
                "created_at": now,
                "updated_at": now,
                "done": True,
                "hide": False,
                "unsent": False,
            }
        ],
        **extra,
    }


class ChatIndexTest(unittest.TestCase):
    """Core index behaviour against a temporary tasks tree."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks = os.path.join(self.tmp.name, "tasks")
        os.makedirs(os.path.join(self.tasks, "kanban", "tasks"))
        self.addCleanup(self.tmp.cleanup)
        self.store = ChatIndexStore(
            self.tasks,
            db_path=os.path.join(self.tmp.name, "index", "chats.db"),
        )
        self.addCleanup(self.store.close)

    def _path(self, chat_id: str) -> str:
        path = os.path.join(self.tasks, "kanban", "tasks", f"task.{chat_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        return path

    def _rebuild(self, documents):
        return self.store.rebuild((self._path(doc["id"]), doc) for doc in documents)

    def test_rebuild_list_and_id_lookup(self):
        count = self._rebuild([
            chat("a", "Alpha", "first chat about deployment"),
            chat("b", "Beta", "second chat about testing"),
        ])
        self.assertEqual(count, 2)
        self.assertTrue(self.store.is_ready())
        self.assertFalse(self.store.is_building())
        rows = self.store.list_chats()
        self.assertEqual({row["id"] for row in rows}, {"a", "b"})
        self.assertEqual(self.store.find_path("a"), self._path("a"))
        self.assertEqual(self.store.count_chats(), 2)
        # The projection carries the last-message preview, not messages.
        row = self.store.get_chat("a")
        self.assertEqual(row["last_message"]["content"], "first chat about deployment")
        self.assertEqual(row["message_count"], 1)
        self.assertNotIn("messages", row)

    def test_branch_is_projected_for_the_harness_tree(self):
        self._rebuild([chat("a", "Alpha", "ticket", branch="feature/export")])
        self.assertEqual(self.store.get_chat("a")["branch"], "feature/export")
        # A chat that never recorded a branch projects an empty value, so the
        # harness tree can place it in its "Unknown" node.
        self._rebuild([chat("b", "Beta", "task")])
        self.assertEqual(self.store.get_chat("b")["branch"], "")

    def test_search_finds_content_and_updates(self):
        self._rebuild([chat("a", "Alpha", "deployment pipeline")])
        result = self.store.search("deployment")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["chat"]["id"], "a")
        self.assertIn("content", result["results"][0]["matched_fields"])
        self.assertIn("deployment", result["results"][0]["snippet"])

        # New version replaces the old search text.
        updated = chat("a", "Alpha", "rollback procedure")
        updated["messages"][0]["doc_id"] = "m1"
        self.store.upsert_chat(updated, self._path("a"))
        self.assertEqual(self.store.search("deployment")["total"], 0)
        self.assertEqual(self.store.search("rollback")["total"], 1)


    def test_search_filters_limit_the_fields(self):
        self._rebuild([chat("a", "Alpha", "deployment pipeline")])
        name_only = {
            "search_name": True,
            "search_description": False,
            "search_messages": False,
            "search_history": False,
            "search_files": False,
            "search_message_metadata": False,
            "search_model": False,
            "search_status": False,
            "search_mode": False,
        }
        self.assertEqual(self.store.search("Alpha", filters=name_only)["total"], 1)
        # The content field is excluded by the filter, so the term is not found.
        self.assertEqual(self.store.search("deployment", filters=name_only)["total"], 0)
    def test_unread_cursor(self):
        doc = chat("a", "Alpha", "hello")
        doc["messages"].append({
            "doc_id": "m2",
            "role": "assistant",
            "user": "agent",
            "content": "second",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "done": True,
            "hide": False,
            "unsent": False,
        })
        self._rebuild([doc])
        self.assertEqual(self.store.unread_count("a", "alice"), 1)
        self.store.mark_read("a", "alice", "m2")
        self.assertEqual(self.store.unread_count("a", "alice"), 0)
        self.store.set_read("a", "alice", -1)
        self.assertEqual(self.store.unread_count("a", "alice"), 1)
        # A message by the reader never counts as unread.
        self.assertEqual(self.store.unread_count("a", "agent"), 0)

    def test_drafts_are_per_user(self):
        self._rebuild([chat("a", "Alpha", "hello")])
        self.store.save_draft("a", "alice", "alice draft")
        self.store.save_draft("a", "bob", "bob draft")
        self.assertEqual(self.store.get_draft("a", "alice")["content"], "alice draft")
        self.assertEqual(self.store.get_draft("a", "bob")["content"], "bob draft")
        self.store.delete_draft("a", "alice")
        self.assertIsNone(self.store.get_draft("a", "alice"))
        self.assertIsNotNone(self.store.get_draft("a", "bob"))

    def test_user_filter_uses_participants(self):
        doc = chat("a", "Alpha", "hello")
        doc["users"] = ["alice", "bob"]
        self._rebuild([doc])
        self.assertEqual(len(self.store.list_chats(user_id="alice")), 1)
        self.assertEqual(len(self.store.list_chats(user_id="carol")), 0)


    def test_legacy_read_and_draft_are_imported_once(self):
        doc = chat("a", "Alpha", "hello")
        doc["messages"][0]["read_by"] = ["alice"]
        doc["messages"].append({
            "doc_id": "draft1",
            "role": "user",
            "user": "bob",
            "content": "typing on the file",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "done": True,
            "hide": False,
            "unsent": True,
        })
        self._rebuild([doc])
        self.assertEqual(self.store.unread_count("a", "alice"), 0)
        self.assertEqual(self.store.get_draft("a", "bob")["content"], "typing on the file")

    def test_count_and_pagination(self):
        self._rebuild([
            chat("a", "Alpha", "one"),
            chat("b", "Beta", "two"),
            chat("c", "Gamma", "three"),
        ])
        self.assertEqual(self.store.count_chats(), 3)
        page = self.store.list_chats(limit=1, offset=1)
        self.assertEqual(len(page), 1)
        self.assertEqual(self.store.signatures(), {
            self._path("a"): self.store.signatures()[self._path("a")],
            self._path("b"): self.store.signatures()[self._path("b")],
            self._path("c"): self.store.signatures()[self._path("c")],
        })
    def test_delete_is_idempotent(self):
        self._rebuild([chat("a", "Alpha", "hello")])
        self.store.delete_chat("a")
        self.assertEqual(self.store.count_chats(), 0)
        self.store.delete_chat("a")
        self.assertIsNone(self.store.get_chat("a"))

    def test_metadata_mode_still_lists(self):
        store = ChatIndexStore(
            self.tasks,
            db_path=os.path.join(self.tmp.name, "index", "meta.db"),
            search_mode="metadata",
        )
        self.addCleanup(store.close)
        store.rebuild([(self._path("a"), chat("a", "Alpha", "hello"))])
        self.assertEqual(len(store.list_chats()), 1)
        # LIKE fallback finds the name, including date/user filters.
        self.assertEqual(store.search("Alpha")["total"], 1)
        self.assertEqual(store.search("Alpha", user_id="alice")["total"], 1)
        self.assertEqual(
            store.search("Alpha", from_date="2020-01-01T00:00:00")["total"], 1
        )

    def test_rebuild_is_deterministic_and_preserves_user_state(self):
        self._rebuild([chat("a", "Alpha", "hello")])
        self.store.mark_read("a", "alice", "m1")
        self.store.save_draft("a", "alice", "draft")
        self._rebuild([chat("a", "Alpha", "hello again")])
        self.assertEqual(self.store.get_draft("a", "alice")["content"], "draft")
        self.assertEqual(self.store.health()["counts"]["chats"], 1)
        self.assertEqual(self.store.unread_count("a", "alice"), 0)


if __name__ == "__main__":
    unittest.main()
