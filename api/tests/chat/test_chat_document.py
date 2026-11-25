"""Tests for the incremental chat document codec (the storage hot path).

The canonical chat file must stay byte-identical to ``json.dumps(payload,
indent=2)``; the cache is only an accelerator and must never let an
unsanitised string reach the file.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from codx.junior.chat.chat_document import (
    ChatDocumentCache,
    dump_document,
    file_signature,
    write_document,
)

GITHUB_TOKEN = "ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8"

PAYLOADS = [
    {"id": "a", "messages": [], "meta_data": None, "cost": 1.5, "tags": []},
    {
        "id": "b",
        "messages": [
            {
                "doc_id": "m1",
                "content": "hello\nworld",
                "n": 1,
                "nested": {"a": [1, 2, {"b": True}]},
            }
        ],
        "name": "úñî",
    },
    {
        "messages": [
            {"doc_id": "1", "content": ""},
            {"doc_id": "2", "content": "x", "error": None},
        ],
        "id": "c",
        "meta_data": {"k": [None, False]},
    },
    {
        "id": "d",
        "messages": [
            {
                "doc_id": str(i),
                "content": "line ☃ " * 3,
                "tool_events": [{"output": "o", "n": i}],
            }
            for i in range(5)
        ],
    },
    {
        "id": "e",
        "messages": [
            {"doc_id": "secret", "content": f"token = {GITHUB_TOKEN}"},
            {"doc_id": "clean", "content": "nothing to see"},
        ],
    },
]


class ChatDocumentCodecTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = ChatDocumentCache(max_bytes=4 * 1024 * 1024)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "chat.json")

    def _encode_and_write(self, payload, signature=None):
        encoded = self.cache.encode(payload, self.path, signature)
        write_document(self.path, encoded)
        self.cache.remember_written(self.path, encoded, file_signature(self.path))
        return encoded

    def test_output_is_byte_identical_to_json_dumps(self):
        for payload in PAYLOADS:
            encoded = self.cache.encode(payload, self.path, None)
            self.assertEqual(
                dump_document(encoded.payload, encoded.fragments),
                json.dumps(encoded.payload, indent=2),
                payload.get("id"),
            )

    def test_written_file_is_valid_and_pretty(self):
        encoded = self._encode_and_write(PAYLOADS[3])
        with open(self.path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text, json.dumps(encoded.payload, indent=2))
        self.assertEqual(json.loads(text)["id"], "d")
        self.assertIn('\n    {\n      "doc_id": "0"', text)

    def test_secret_is_sanitised_and_never_written(self):
        encoded = self._encode_and_write(PAYLOADS[4])
        with open(self.path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(GITHUB_TOKEN, text)
        self.assertGreaterEqual(encoded.redactions, 1)
        # A second, identical save (raw reuse path) must stay sanitised.
        encoded2 = self._encode_and_write(PAYLOADS[4], signature=file_signature(self.path))
        with open(self.path, encoding="utf-8") as handle:
            text2 = handle.read()
        self.assertNotIn(GITHUB_TOKEN, text2)
        self.assertEqual(text, text2)
        self.assertEqual(encoded2.changed_messages, set())

    def test_unchanged_message_reuses_fragment_and_reports_no_change(self):
        payload = PAYLOADS[3]
        first = self._encode_and_write(payload)
        second_payload = json.loads(json.dumps(payload))
        second_payload["updated_at"] = "2026-01-01T00:00:00"
        second = self.cache.encode(
            second_payload, self.path, file_signature(self.path)
        )
        self.assertEqual(second.fragments, first.fragments)
        self.assertEqual(second.changed_messages, set())
        self.assertEqual(second.redactions, 0)

    def test_only_changed_message_is_reported(self):
        payload = json.loads(json.dumps(PAYLOADS[3]))
        first = self._encode_and_write(payload)
        edited = json.loads(json.dumps(payload))
        edited["messages"][2]["content"] = "changed"
        encoded = self.cache.encode(edited, self.path, file_signature(self.path))
        self.assertEqual(encoded.changed_messages, {"2"})
        # Unchanged neighbours keep their exact fragments.
        self.assertEqual(encoded.fragments[0], first.fragments[0])
        self.assertEqual(encoded.fragments[1], first.fragments[1])
        self.assertNotEqual(encoded.fragments[2], first.fragments[2])

    def test_external_change_invalidates_the_cache(self):
        payload = json.loads(json.dumps(PAYLOADS[0]))
        payload["messages"] = [{"doc_id": "m", "content": "one"}]
        self._encode_and_write(payload)
        cached = self.cache.payload(self.path, file_signature(self.path))
        self.assertIsNotNone(cached)
        # Simulate git/another worker rewriting the file.
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "a", "messages": []}, indent=2))
        self.assertIsNone(self.cache.payload(self.path, file_signature(self.path)))

    def test_loaded_payload_is_not_reused_for_writes(self):
        # A document read from disk (possibly edited outside the platform) is
        # cached for READS only: the next write must sanitise it again.
        payload = {"id": "z", "messages": [{"doc_id": "m", "content": GITHUB_TOKEN}]}
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2))
        signature = file_signature(self.path)
        self.cache.remember_loaded(self.path, payload, signature)
        self.assertIsNotNone(self.cache.payload(self.path, signature))
        encoded = self.cache.encode(payload, self.path, signature)
        self.assertNotIn(GITHUB_TOKEN, json.dumps(encoded.payload))
        # It cannot tell which messages changed: the write asks for a full sync.
        self.assertIsNone(encoded.changed_messages)


class ChatDocumentManagerIntegrationTest(unittest.TestCase):
    """The codec wired into ChatManager: save/load/update stay coherent."""

    def setUp(self) -> None:
        from types import SimpleNamespace

        from codx.junior.chat_manager import ChatManager

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.codx = os.path.join(self.tmp.name, ".codx")
        os.makedirs(os.path.join(self.codx, "tasks", "kanban", "tasks"))
        self.old_index = os.environ.get("CODX_JUNIOR_CHAT_INDEX")
        os.environ["CODX_JUNIOR_CHAT_INDEX"] = "off"
        self.addCleanup(self._restore_env)

        class _Events:
            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        self.manager = ChatManager(
            settings=SimpleNamespace(
                codx_path=self.codx, project_id="p-test", project_name="p-test"
            ),
            event_manager=_Events(),
        )

    def _restore_env(self) -> None:
        if self.old_index is None:
            os.environ.pop("CODX_JUNIOR_CHAT_INDEX", None)
        else:
            os.environ["CODX_JUNIOR_CHAT_INDEX"] = self.old_index

    def _chat(self, messages=3):
        from codx.junior.db import Chat, Message

        now = "2026-11-21T10:00:00"
        return Chat(
            id="chat-1",
            name="Integration",
            board="kanban",
            column="tasks",
            created_at=now,
            updated_at=now,
            messages=[
                Message(
                    doc_id=f"m{i}",
                    role="assistant" if i % 2 else "user",
                    user="alice",
                    content=f"message {i}",
                    created_at=now,
                    updated_at=now,
                )
                for i in range(messages)
            ],
        )

    def test_save_load_and_incremental_update(self):
        chat = self.manager.save_chat(chat=self._chat())
        with open(chat.file_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["id"], "chat-1")

        loaded = self.manager.find_by_id("chat-1")
        self.assertEqual(len(loaded.messages), 3)

        message = loaded.messages[1]
        message.content = "edited content"
        self.manager.update_message(chat=loaded, message=message)

        reloaded = self.manager.find_by_id("chat-1")
        self.assertEqual(reloaded.messages[1].content, "edited content")
        # Every other message survived the incremental write untouched.
        self.assertEqual([m.doc_id for m in reloaded.messages], ["m0", "m1", "m2"])
        self.assertEqual(reloaded.messages[0].content, "message 0")

    def test_loaded_chat_is_a_fresh_copy(self):
        chat = self._chat()
        chat.meta_data = {"browser": {"session": "one"}}
        chat.messages[0].meta_data = {"nested": {"value": 1}}
        self.manager.save_chat(chat=chat)
        path = self.manager.find_chat_path("chat-1")
        first = self.manager.load_chat_from_path(path)
        first.messages[0].content = "mutated in memory"
        first.meta_data["browser"]["session"] = "mutated"
        first.messages[0].meta_data["nested"]["value"] = 99
        second = self.manager.load_chat_from_path(path)
        self.assertEqual(second.messages[0].content, "message 0")
        self.assertEqual(second.meta_data["browser"]["session"], "one")
        self.assertEqual(second.messages[0].meta_data["nested"]["value"], 1)

    def test_batch_update_writes_once(self):
        self.manager.save_chat(chat=self._chat())
        loaded = self.manager.find_by_id("chat-1")
        for message in loaded.messages:
            message.content += " (batch)"
        self.manager.apply_messages(chat=loaded, messages=list(loaded.messages))
        reloaded = self.manager.find_by_id("chat-1")
        self.assertTrue(all("(batch)" in m.content for m in reloaded.messages))

    def test_stored_file_matches_legacy_serialisation(self):
        """The incremental writer must not change the on-disk format."""
        from codx.junior.security.ciso.payload import redact_payload

        chat = self._chat()
        chat.messages[0].content = f"token={GITHUB_TOKEN}"
        chat.messages[1].think = "reasoning about the token " + GITHUB_TOKEN
        saved = self.manager.save_chat(chat=chat)
        expected, _ = redact_payload(saved.model_dump())
        with open(saved.file_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text, json.dumps(expected, indent=2))
        self.assertNotIn(GITHUB_TOKEN, text)

    def test_restart_reuses_the_index_manifest(self):
        """After a restart only the changed message is sanitised again."""
        from unittest.mock import patch

        from codx.junior.chat import chat_document as document_module

        os.environ["CODX_JUNIOR_CHAT_INDEX"] = "on"
        # Build an EMPTY index first, so the save below is a trusted write and
        # no background rebuild resets the manifest.
        self.manager._start_chat_index_build()
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.manager._chat_index_ready():
                break
            time.sleep(0.02)
        self.assertTrue(self.manager._chat_index_ready())

        self.manager.save_chat(chat=self._chat())
        loaded = self.manager.find_by_id("chat-1")
        self.assertIsNotNone(self.manager._index_message_hashes("chat-1"))

        # Simulate a process restart: no in-process document cache.
        document_module.DOCUMENT_CACHE.clear()
        loaded = self.manager.find_by_id("chat-1")
        message = loaded.messages[0]
        message.content = "only this message changed"

        scanned = []
        original = document_module.redact_payload

        def counting(value, *args, **kwargs):
            if isinstance(value, dict) and value.get("doc_id") and "role" in value:
                scanned.append(value.get("doc_id"))
            return original(value, *args, **kwargs)

        with patch.object(document_module, "redact_payload", counting):
            self.manager.update_message(chat=loaded, message=message)

        self.assertEqual(scanned, ["m0"])
        with open(loaded.file_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["messages"][0]["content"], "only this message changed")
        self.assertEqual(stored["messages"][1]["content"], "message 1")

    def test_save_uses_stored_chat_instead_of_reloading(self):
        saved = self.manager.save_chat(chat=self._chat())
        calls = []

        def _fail(chat_id=None):
            calls.append(chat_id)
            raise AssertionError("find_by_id must not be used when stored_chat is given")

        original = self.manager.find_by_id
        self.manager.find_by_id = _fail
        try:
            self.manager.save_chat(chat=saved, stored_chat=saved)
        finally:
            self.manager.find_by_id = original
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
