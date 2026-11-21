"""Unit tests for the content-addressed blob store (stdlib only)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from codx.junior.chat.blobs import BlobStore  # noqa: E402


class BlobStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tasks = os.path.join(self.tmp.name, "tasks")
        os.makedirs(self.tasks)
        self.store = BlobStore(self.tasks)

    def test_put_get_and_dedupe(self):
        first = self.store.put_text("a large tool output", redact=False)
        second = self.store.put_text("a large tool output", redact=False)
        self.assertIsNotNone(first)
        self.assertEqual(first["ref"], second["ref"])
        self.assertEqual(self.store.get_text(first["ref"]), "a large tool output")
        self.assertEqual(len(os.listdir(os.path.dirname(
            self.store.resolve(first["ref"])
        ))), 1)

    def test_reference_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.resolve("../../etc/passwd")
        with self.assertRaises(ValueError):
            self.store.resolve(".blobs/../../etc/passwd")
        self.assertIsNone(self.store.get_text(".blobs/../project.json"))

    def test_gc_keeps_only_referenced_blobs(self):
        kept = self.store.put_text("keep me", redact=False)
        dropped = self.store.put_text("drop me", redact=False)
        removed = self.store.gc([kept["ref"]])
        self.assertEqual(removed, 1)
        self.assertIsNotNone(self.store.get_text(kept["ref"]))
        self.assertIsNone(self.store.get_text(dropped["ref"]))

    def test_too_large_is_refused(self):
        store = BlobStore(self.tasks, max_bytes=4)
        self.assertIsNone(store.put_text("12345", redact=False))


if __name__ == "__main__":
    unittest.main()
