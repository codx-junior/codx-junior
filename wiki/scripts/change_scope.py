#!/usr/bin/env python3
"""Project-scoped change detection and claim verification for the wiki project.

Why this exists (cards ``7d3ffd19`` and ``fee1672f``)
----------------------------------------------------
The platform ``qa_*`` tools resolve the change set against the **git root**
(``.../codx-junior-platform``) while this project lives one level below it
(``.../codx-junior-platform/wiki``). Consequences measured on the board:

* ``qa_plan`` / ``qa_review_changes`` report ``api/codx/junior/**`` files that do
  not exist in this project, so ``mapped_sources`` is structurally 0;
* a path-filtered review can return ``decision: allow`` with
  ``changed_files: 0`` -- a false negative that looks like a pass;
* a delegated run that returns a ``<file_change>`` envelope may never have
  written the file (phantom delivery), and nothing detects it.

This module answers both questions against **one explicit base**: the project
root. It never reports a path that escapes it, it never uses the git toplevel,
and it can verify that a claimed envelope actually reached the working tree.

Zero dependencies: Python 3.7+ standard library only. Both subcommands are thin
wrappers over the pure helpers (``normalize_path``, ``parse_porcelain_z``,
``parse_claims``, ``classify``), which are unit-tested without git, without a
network and without pytest -- see ``tests/test_change_scope.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

PERSISTED = "persisted"
PHANTOM = "phantom"
MISSING = "missing"
STALE = "stale"

#: Every verdict that must make a verification run fail.
FAILING = (PHANTOM, MISSING, STALE)

_CLAIM_RE = re.compile(r"<file_change\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class Claim:
    """One ``<file_change path="..." change_type="...">`` assertion."""

    path: str
    change_type: str = "update"


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested, no I/O)
# --------------------------------------------------------------------------- #
def normalize_path(path: str) -> str:
    """Return ``path`` as a project-relative POSIX path.

    Raises :class:`ValueError` when the path escapes the project root, so a
    caller can never end up diffing the git root by accident (the defect).
    """
    candidate = (path or "").strip().replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    candidate = candidate.lstrip("/")

    parts: List[str] = []
    for part in candidate.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("path escapes the project root: %r" % path)
        parts.append(part)
    return "/".join(parts)


def parse_claims(text: str) -> List[Claim]:
    """Extract the claims of every ``<file_change ...>`` envelope in ``text``.

    The closing ``<file_change/>`` carries no attribute and is skipped.
    Duplicates (same path and change type) are collapsed, order is preserved.
    """
    claims: List[Claim] = []
    seen = set()
    for match in _CLAIM_RE.finditer(text or ""):
        attrs = dict(_ATTR_RE.findall(match.group(1) or ""))
        raw_path = (attrs.get("path") or "").strip()
        if not raw_path:
            continue
        kind = (attrs.get("change_type") or "update").strip().lower() or "update"
        claim = Claim(normalize_path(raw_path), kind)
        key = (claim.path, claim.change_type)
        if key in seen:
            continue
        seen.add(key)
        claims.append(claim)
    return claims


def parse_porcelain_z(data: str) -> List[Tuple[str, str]]:
    """Parse ``git status --porcelain=v1 -z`` output into ``(code, path)`` pairs.

    Paths are project-relative. For renames/copies git emits the target first
    and the source as the following NUL-separated field, which is skipped.
    """
    entries: List[Tuple[str, str]] = []
    fields = [field for field in (data or "").split("\0") if field]
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        if code[0] in ("R", "C"):
            if index < len(fields):
                index += 1
        entries.append((code, normalize_path(path)))
    return entries


def strip_repo_prefix(path: str, prefix: str) -> str:
    """Re-base a repository-root-relative path onto the project root.

    ``git status --porcelain`` prints paths relative to the repository root
    (``wiki/scripts/change_scope.py``), not to the working directory. A path that
    does not start with ``prefix`` lives outside this project and yields ``""``
    so the caller drops it: the tool must never report ``api/**`` or ``client/**``.
    """
    normalized = normalize_path(path)
    normalized_prefix = (prefix or "").strip("/")
    if not normalized_prefix:
        return normalized
    if normalized == normalized_prefix:
        return ""
    if normalized.startswith(normalized_prefix + "/"):
        return normalized[len(normalized_prefix) + 1 :]
    return ""


def classify(
    change_type: str,
    exists: bool,
    worktree_sha: Optional[str],
    head_sha: Optional[str],
) -> str:
    """Decide whether a claim reached the working tree.

    * ``delete``  -> ``persisted`` when the file is gone, ``stale`` otherwise;
    * ``new``     -> ``persisted`` when the file exists, ``missing`` otherwise;
    * ``update``  -> ``phantom`` when the bytes equal ``HEAD`` (the delegated
      edit was never applied), ``persisted`` when they differ or the file is new
      relative to ``HEAD``.
    """
    kind = (change_type or "update").strip().lower() or "update"

    if kind == "delete":
        return PERSISTED if not exists else STALE
    if not exists:
        return MISSING
    if kind == "new":
        return PERSISTED
    if head_sha is None:
        return PERSISTED
    return PHANTOM if worktree_sha == head_sha else PERSISTED


# --------------------------------------------------------------------------- #
# git-backed helpers
# --------------------------------------------------------------------------- #
def run_git(root: str, args: Sequence[str]) -> "subprocess.CompletedProcess":
    return subprocess.run(
        ["git", "-C", root] + list(args),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def repo_prefix(root: str) -> str:
    """Path from the repository toplevel down to ``root`` (``"wiki/"`` here, ``""`` at the toplevel)."""
    proc = run_git(root, ["rev-parse", "--show-prefix"])
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip() or "git rev-parse failed"
        raise RuntimeError(message)
    return proc.stdout.decode("utf-8", "replace").strip()


def project_changes(root: str, paths: Sequence[str] = ()) -> List[Tuple[str, str]]:
    """Changed files **relative to ``root``**, optionally filtered by prefix.

    Entries whose path lies outside the project are dropped (see
    :func:`strip_repo_prefix`), so the output can never contain the phantom
    ``api/codx/junior/**`` paths that broke the ``qa_*`` change set.
    """
    # ``--porcelain`` reports repository-root paths, but ``status.relativePaths``
    # is user configuration; pin it so the re-basing below never depends on a
    # developer's git config.
    proc = run_git(
        root,
        [
            "-c",
            "status.relativePaths=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
    )
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip() or "git status failed"
        raise RuntimeError(message)

    prefix = repo_prefix(root)
    entries = [
        (code, project_path)
        for code, repo_path in parse_porcelain_z(proc.stdout.decode("utf-8", "replace"))
        for project_path in (strip_repo_prefix(repo_path, prefix),)
        if project_path
    ]
    wanted = [normalize_path(path) for path in paths if path]
    if wanted:
        entries = [
            (code, path)
            for code, path in entries
            if any(path == item or path.startswith(item + "/") for item in wanted)
        ]
    return entries


def worktree_sha256(root: str, path: str) -> Optional[str]:
    full_path = os.path.join(root, *normalize_path(path).split("/"))
    try:
        with open(full_path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def head_sha256(root: str, path: str, prefix: str = "") -> Optional[str]:
    """SHA-256 of ``HEAD:<path>``, where ``path`` is project-relative."""
    repo_path = normalize_path(path)
    normalized_prefix = (prefix or "").strip("/")
    if normalized_prefix:
        repo_path = normalized_prefix + "/" + repo_path

    proc = run_git(root, ["show", "HEAD:" + repo_path])
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def verify_claim(root: str, claim: Claim) -> str:
    sha = worktree_sha256(root, claim.path)
    return classify(claim.change_type, sha is not None, sha, head_sha256(root, claim.path, repo_prefix(root)))


def default_root() -> str:
    """The project root: the parent of the ``scripts/`` folder holding this file."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def _cmd_changed(args: argparse.Namespace) -> int:
    entries = project_changes(args.root, args.path)
    if args.json:
        print(json.dumps([{"status": code, "path": path} for code, path in entries], indent=2))
    else:
        print("# base: %s" % args.root)
        for code, path in entries:
            print("%s\t%s" % (code, path))
        if not entries:
            print("(no changes)")
    return 0


def _cmd_claims(args: argparse.Namespace) -> int:
    if args.envelope and args.envelope != "-":
        with open(args.envelope, "r", encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = sys.stdin.read()

    claims = parse_claims(text)
    if not claims:
        print("no <file_change> claim found in the input", file=sys.stderr)
        return 2

    results = [(verify_claim(args.root, claim), claim) for claim in claims]
    if args.json:
        print(
            json.dumps(
                [
                    {"verdict": verdict, "path": claim.path, "change_type": claim.change_type}
                    for verdict, claim in results
                ],
                indent=2,
            )
        )
    else:
        print("# base: %s" % args.root)
        for verdict, claim in results:
            print("%s\t%s\t%s" % (verdict, claim.path, claim.change_type))

    failed = [claim.path for verdict, claim in results if verdict in FAILING]
    if failed:
        print(
            "FAIL - %d of %d claimed edits are not in the working tree: %s"
            % (len(failed), len(results), ", ".join(failed)),
            file=sys.stderr,
        )
        return 1
    print("PASS - %d claimed edit(s) verified against the working tree" % len(results))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="change_scope.py",
        description="Project-relative change detection and <file_change> claim verification.",
    )
    parser.add_argument("--root", default=default_root(), help="project root (default: this project)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    changed = subparsers.add_parser("changed", help="list changed files relative to the project root")
    changed.add_argument("--path", action="append", default=[], help="restrict to a project-relative prefix")
    changed.add_argument("--json", action="store_true", help="machine-readable output")
    changed.set_defaults(func=_cmd_changed)

    claims = subparsers.add_parser("claims", help="verify that <file_change> envelopes reached disk")
    claims.add_argument("--envelope", default="-", help="file holding the envelope text ('-' = stdin)")
    claims.add_argument("--json", action="store_true", help="machine-readable output")
    claims.set_defaults(func=_cmd_claims)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:  # path traversal / hostile claim
        print("error: %s" % error, file=sys.stderr)
        return 2
    except RuntimeError as error:  # git unavailable or not a repository
        print("error: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
