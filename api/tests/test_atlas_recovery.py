"""The atlas is a rebuildable cache: a corrupt database must self-heal.

SQLite can leave ``database disk image is malformed`` behind after a crash or a
truncated copy of the runtime folder. The tools used to surface that message to
the model and stop answering discovery questions. The expected behaviour is:
delete the unreadable file and rebuild it (no user data lives there).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from codx.junior.atlas import ProjectAtlas, atlas_db_path, atlas_ready


class AtlasCorruptionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Keep the temporary project inside the repository: some CI sandboxes
        # disallow creating files in the system temp folder.
        self.temp = tempfile.TemporaryDirectory(
            prefix="atlas-corrupt-", dir=os.getcwd()
        )
        self.root = self.temp.name
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(
            os.path.join(self.root, "src", "wallet.py"), "w", encoding="utf-8"
        ) as handle:
            handle.write(
                "class Wallet:\n"
                "    def __init__(self):\n"
                "        self.balance = 0\n"
            )
        self.settings = SimpleNamespace(
            project_id="atlas-corrupt-test",
            project_name="atlas-corrupt-test",
            abs_project_path=self.root,
            codx_path=os.path.join(self.root, ".codx"),
            project_atlas_enabled=True,
            project_atlas_max_files=100,
            project_atlas_ttl_seconds=0,
        )
        self.db_path = atlas_db_path(self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build(self) -> None:
        with ProjectAtlas(self.settings) as atlas:
            packet = atlas.locate("Wallet")
        self.assertTrue(packet.evidence)
        self.assertTrue(os.path.isfile(self.db_path))

    def _corrupt(self) -> None:
        with open(self.db_path, "wb") as handle:
            handle.write(b"this is not a sqlite database, not even close")
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                os.remove(f"{self.db_path}{suffix}")
            except FileNotFoundError:
                pass

    def test_locate_rebuilds_a_malformed_database(self):
        self._build()
        self._corrupt()

        with ProjectAtlas(self.settings) as atlas:
            packet = atlas.locate("Wallet")

        self.assertTrue(packet.evidence)
        self.assertEqual(packet.evidence[0].path, "src/wallet.py")

    def test_query_time_corruption_triggers_a_rebuild_and_retry(self):
        """Corruption can surface on a query, after the index opened cleanly."""
        from codx.junior.atlas import store as store_module

        self._build()
        original_stats = store_module.AtlasStore.stats
        calls = {"count": 0}

        def flaky_stats(store):
            calls["count"] += 1
            if calls["count"] == 1:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return original_stats(store)

        store_module.AtlasStore.stats = flaky_stats
        try:
            with ProjectAtlas(self.settings) as atlas:
                packet = atlas.map()
        finally:
            store_module.AtlasStore.stats = original_stats

        self.assertIn(packet.intent, ("map", "locate"))
        self.assertGreaterEqual(calls["count"], 2)

    def test_atlas_ready_rejects_and_removes_a_malformed_database(self):
        self._build()
        self._corrupt()

        self.assertFalse(atlas_ready(self.settings, refresh=True))
        self.assertFalse(os.path.exists(self.db_path))

        # The next call can build a clean index again.
        self._build()
        self.assertTrue(atlas_ready(self.settings, refresh=True))


if __name__ == "__main__":
    unittest.main()
