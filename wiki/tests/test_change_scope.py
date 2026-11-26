"""Tests for ``scripts/change_scope.py`` -- stdlib ``unittest`` only.

Runnable without any installation, which is the point: this project currently has
no importable pytest (``python3 -m pytest --version`` exits 1 in 0.0 s) and no
``npm`` in PATH, so the QA gate cannot execute the pytest suite. This file needs
neither: no git, no network, no clock, no shared state.

    python3 -m unittest discover -s tests -t .
"""

import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"),
)

import change_scope  # noqa: E402  (path bootstrap must run first)


ENVELOPE = """
Applied the fix:

<file_change path="./.vitepress/config.mts" language="mts" change_type="update" encoding="text">
import { curateSidebar } from '../scripts/sidebar-curation.mjs';
<file_change/>

<file_change path="tests/test_change_scope.py" language="python" change_type="new" encoding="text">
...
<file_change/>

<file_change path="docs/removed.md" change_type="delete" encoding="text">
<file_change/>
"""


class NormalizePathTests(unittest.TestCase):
    def test_strips_relative_and_absolute_prefixes(self):
        self.assertEqual(change_scope.normalize_path("./tests/x.py"), "tests/x.py")
        self.assertEqual(change_scope.normalize_path("/tests//x.py"), "tests/x.py")
        self.assertEqual(change_scope.normalize_path("tests\\x.py"), "tests/x.py")

    def test_rejects_traversal(self):
        with self.assertRaises(ValueError):
            change_scope.normalize_path("../api/codx/junior/app.py")


class ParseClaimsTests(unittest.TestCase):
    def test_reads_path_and_change_type(self):
        claims = change_scope.parse_claims(ENVELOPE)

        self.assertEqual(
            [(claim.path, claim.change_type) for claim in claims],
            [
                (".vitepress/config.mts", "update"),
                ("tests/test_change_scope.py", "new"),
                ("docs/removed.md", "delete"),
            ],
        )

    def test_ignores_closing_tag_and_empty_input(self):
        self.assertEqual(change_scope.parse_claims("<file_change/>"), [])
        self.assertEqual(change_scope.parse_claims(""), [])

    def test_defaults_to_update_and_deduplicates(self):
        text = '<file_change path="a.py">' + '<file_change path="a.py" change_type="update">'

        claims = change_scope.parse_claims(text)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].change_type, "update")

    def test_rejects_a_traversing_claim(self):
        with self.assertRaises(ValueError):
            change_scope.parse_claims('<file_change path="../../etc/passwd">')


class ParsePorcelainTests(unittest.TestCase):
    def test_untracked_and_modified(self):
        data = "?? tests/test_change_scope.py\0 M README.md\0"

        self.assertEqual(
            change_scope.parse_porcelain_z(data),
            [("??", "tests/test_change_scope.py"), (" M", "README.md")],
        )

    def test_rename_reports_the_target_only(self):
        data = "R  new/name.md\0old/name.md\0"

        self.assertEqual(change_scope.parse_porcelain_z(data), [("R ", "new/name.md")])

    def test_empty_output(self):
        self.assertEqual(change_scope.parse_porcelain_z(""), [])


class StripRepoPrefixTests(unittest.TestCase):
    def test_rebases_a_nested_project_onto_its_root(self):
        # `git status --porcelain` prints `wiki/tests/x.py` because the git root
        # is the parent of this project. That is the defect: it must be re-based.
        self.assertEqual(change_scope.strip_repo_prefix("wiki/tests/x.py", "wiki/"), "tests/x.py")
        self.assertEqual(change_scope.strip_repo_prefix("wiki", "wiki/"), "")

    def test_drops_everything_outside_the_project(self):
        self.assertEqual(change_scope.strip_repo_prefix("api/codx/junior/app.py", "wiki/"), "")
        self.assertEqual(change_scope.strip_repo_prefix("client/src/main.ts", "wiki/"), "")

    def test_at_the_toplevel_the_path_is_kept(self):
        self.assertEqual(change_scope.strip_repo_prefix("tests/x.py", ""), "tests/x.py")


class ClassifyTests(unittest.TestCase):
    def test_update_with_identical_bytes_is_phantom(self):
        self.assertEqual(
            change_scope.classify("update", True, "sha-same", "sha-same"),
            change_scope.PHANTOM,
        )

    def test_update_with_different_bytes_is_persisted(self):
        self.assertEqual(
            change_scope.classify("update", True, "sha-new", "sha-old"),
            change_scope.PERSISTED,
        )

    def test_update_on_a_file_absent_from_head_is_persisted(self):
        self.assertEqual(
            change_scope.classify("update", True, "sha-new", None),
            change_scope.PERSISTED,
        )

    def test_missing_and_new(self):
        self.assertEqual(change_scope.classify("update", False, None, "sha-old"), change_scope.MISSING)
        self.assertEqual(change_scope.classify("new", False, None, None), change_scope.MISSING)
        self.assertEqual(change_scope.classify("new", True, "sha-new", None), change_scope.PERSISTED)

    def test_delete(self):
        self.assertEqual(change_scope.classify("delete", False, None, "sha-old"), change_scope.PERSISTED)
        self.assertEqual(change_scope.classify("delete", True, "sha-old", "sha-old"), change_scope.STALE)


class ProjectChangesTests(unittest.TestCase):
    """``project_changes`` must re-base git-root paths and drop foreign ones.

    ``run_git``/``repo_prefix`` are stubbed, so this runs without a git
    checkout: the assertion is about the re-basing, which is the defect itself
    (``git status`` prints ``wiki/tests/x.py`` because the repository root is the
    parent of this project).
    """

    STATUS = (
        "?? wiki/tests/new_test.py\0"
        " M wiki/scripts/change_scope.py\0"
        "?? api/codx/junior/app.py\0"
    )

    def _project_changes(self, paths=()):
        def fake_run_git(root, args):
            self.assertIn("status", args)
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=self.STATUS.encode("utf-8"), stderr=b""
            )

        with mock.patch.object(change_scope, "run_git", fake_run_git), mock.patch.object(
            change_scope, "repo_prefix", lambda root: "wiki/"
        ):
            return change_scope.project_changes("/project", paths)

    def test_rebases_to_the_project_and_drops_files_outside_it(self):
        entries = self._project_changes()

        self.assertEqual(
            [path for _code, path in entries],
            ["tests/new_test.py", "scripts/change_scope.py"],
        )
        for _code, path in entries:
            self.assertFalse(path.startswith("wiki/"), path)
            self.assertFalse(path.startswith("api/") or path.startswith("client/"), path)

    def test_path_filter_is_applied_after_the_rebasing(self):
        entries = self._project_changes(["tests"])

        self.assertEqual([path for _code, path in entries], ["tests/new_test.py"])

    def test_pins_the_porcelain_path_base(self):
        # ``status.relativePaths`` is user configuration: the re-basing must not
        # depend on it, so the flag is always requested explicitly.
        seen = []

        def fake_run_git(root, args):
            seen.append(list(args))
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=self.STATUS.encode("utf-8"), stderr=b""
            )

        with mock.patch.object(change_scope, "run_git", fake_run_git), mock.patch.object(
            change_scope, "repo_prefix", lambda root: "wiki/"
        ):
            change_scope.project_changes("/project")

        self.assertEqual(seen[0][:2], ["-c", "status.relativePaths=false"])
        self.assertIn("status", seen[0])


class CommandLineTests(unittest.TestCase):
    def test_default_root_is_the_project_root(self):
        root = change_scope.default_root()

        self.assertTrue(os.path.isfile(os.path.join(root, "scripts", "change_scope.py")))
        self.assertTrue(os.path.isfile(os.path.join(root, "pytest.ini")))

    @unittest.skipUnless(shutil.which("git") and os.path.isdir(os.path.join(change_scope.default_root(), ".git")), "needs a git checkout")
    def test_changed_returns_project_relative_paths_only(self):
        # The defect under test: a change set that contains api/ or client/ would
        # mean the base is the git root, not this project.
        entries = change_scope.project_changes(change_scope.default_root(), ["tests"])

        self.assertIsInstance(entries, list)
        for _code, path in entries:
            self.assertTrue(path.startswith("tests/"), path)
            self.assertFalse(path.startswith("api/") or path.startswith("client/"), path)

    def test_no_claim_in_the_input_is_not_a_pass(self):
        # An empty envelope input must exit 2, never a vacuous "allow"-style pass.
        self.assertEqual(change_scope.main(["claims", "--envelope", os.devnull]), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
