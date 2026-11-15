#!/usr/bin/env python
"""
Audit the API for synchronous work running inside ``async def``.

The API is served by a SINGLE uvicorn worker (``--workers 1``), so every
``async def`` endpoint shares one event loop with Socket.IO and every other
client: a blocking call inside it freezes the whole platform, not just the
request that made it. That is exactly the failure mode this project suffered
from (a ``find /`` inside an ``async def`` blocked every client for ~10 s).

The tool reports three findings:

``async-no-await``
    An ``async def`` endpoint that never awaits. It cannot overlap with
    anything, so it should be a plain ``def`` endpoint and let FastAPI run it
    in its worker thread pool.

``blocking-call``
    An ``async def`` that calls a known heavy helper (project discovery, kanban
    or chat JSON store rewrite, subprocess, synchronous HTTP, ``time.sleep``)
    without off-loading it with ``asyncio.to_thread`` / a thread pool.

``blocking-io``
    Direct filesystem work (``open``, ``os.walk``, ``Path.read_text``...).
    Blocking too, but cheap enough to be advisory: reported, never gated.

Usage::

    python scripts/audit_async_blocking.py               # report everything
    python scripts/audit_async_blocking.py --only blocking-call
    python scripts/audit_async_blocking.py --strict      # exit 1 on findings

``--strict`` is what a CI job should run: it fails the build as soon as a new
blocking call creeps back onto the loop.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple

FINDING_NO_AWAIT = "async-no-await"
FINDING_BLOCKING = "blocking-call"
FINDING_IO = "blocking-io"
FINDING_KINDS = (FINDING_NO_AWAIT, FINDING_BLOCKING, FINDING_IO)

# ── Blocking helpers that must never run directly on the event loop ───────────
# Direct call names, matched through a module (``subprocess.run`` → "run").
BLOCKING_CALLS = {
    # process spawning
    "run",
    "Popen",
    "check_output",
    "check_call",
    "call",
    # blocking waits
    "sleep",
    # synchronous HTTP clients
    "get",
    "post",
    "put",
    "delete",
    "request",
    "urlopen",
}

# Heavy helpers: they walk the filesystem, spawn processes or re-parse a whole
# JSON store. Matched regardless of the receiver, so ``session.load_kanban()``
# is caught too.
HEAVY_CALLS = {
    "find_all_projects",
    "find_all_user_projects",
    "create_project",
    "load_kanban",
    "save_chat",
    "list_all_profiles",
    "list_profiles",
}

# Direct filesystem calls: blocking as well, but ubiquitous and usually cheap.
IO_CALLS = {
    "open",
    "walk",
    "listdir",
    "scandir",
    "read_text",
    "write_text",
    "read_bytes",
    "write_bytes",
    "makedirs",
    "copy",
    "copyfile",
    "rmtree",
}

# Modules whose sync functions block the loop.
BLOCKING_MODULES = {"subprocess", "requests", "urllib", "http", "socket", "shutil"}

# Calls that already moved the work off the loop.
SAFE_CALLS = {
    "to_thread",
    "create_task",
    "gather",
    "wait_for",
    "shield",
    "submit",
    "run_in_executor",
}


class Finding(NamedTuple):
    kind: str
    path: Path
    line: int
    func: str
    detail: str


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _call_module(node: ast.Call) -> str:
    """Return the left-most module name of an attribute call, if any."""
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return func.value.id
        if isinstance(func.value, ast.Attribute):
            return func.value.attr
    return ""


def _is_offloaded(node: ast.Call) -> bool:
    """True when the call already moved the work off the event loop."""
    if _call_name(node) in SAFE_CALLS:
        return True
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in SAFE_CALLS


def _has_await(func: ast.AsyncFunctionDef) -> bool:
    """True when *func* awaits something (nested scopes excluded)."""
    stack: List[ast.AST] = [func]
    while stack:
        current = stack.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
                # A nested scope does not make the outer function cooperative.
                continue
            if isinstance(child, ast.Await):
                return True
            stack.append(child)
    return False


def _iter_calls(func: ast.AsyncFunctionDef) -> Iterable[ast.Call]:
    stack: List[ast.AST] = [func]
    while stack:
        current = stack.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                yield child
            stack.append(child)


def _awaited_call_ids(func: ast.AsyncFunctionDef) -> set:
    """Ids of the calls wrapped in ``await``: coroutines are not blocking."""
    awaited = set()
    for child in ast.walk(func):
        if isinstance(child, ast.Await) and isinstance(child.value, ast.Call):
            awaited.add(id(child.value))
    return awaited


def _classify(call: ast.Call) -> tuple:
    """Return ``(kind, detail)`` for a call inside an ``async def``, or None."""
    if _is_offloaded(call):
        return None

    name = _call_name(call)
    module = _call_module(call)

    if module == "time" and name == "sleep":
        return (
            FINDING_BLOCKING,
            "time.sleep() inside async def: use `await asyncio.sleep()`",
        )
    if name in HEAVY_CALLS:
        return (
            FINDING_BLOCKING,
            f"blocking {name}() inside async def: wrap it with "
            f"`await asyncio.to_thread(...)`",
        )
    if module in BLOCKING_MODULES and name in BLOCKING_CALLS:
        return (
            FINDING_BLOCKING,
            f"blocking {module}.{name}() inside async def: wrap it with "
            f"`await asyncio.to_thread(...)`",
        )
    if name in IO_CALLS and (module in {"os", "shutil", "json", "pathlib", ""}):
        return (
            FINDING_IO,
            f"blocking {name}() inside async def: prefer "
            f"`await asyncio.to_thread(...)` or an async client",
        )
    return None


def audit_file(path: Path) -> List[Finding]:
    """Return the findings of a single Python file."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as ex:
        print(f"cannot read {path}: {ex}", file=sys.stderr)
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as ex:
        print(f"cannot parse {path}: {ex}", file=sys.stderr)
        return []

    findings: List[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or not node.body:
            continue
        if not _has_await(node):
            findings.append(
                Finding(
                    FINDING_NO_AWAIT,
                    path,
                    node.lineno,
                    node.name,
                    "async def without await: make it a sync def so FastAPI "
                    "runs it in its thread pool",
                )
            )
        awaited = _awaited_call_ids(node)
        for call in _iter_calls(node):
            if id(call) in awaited:
                # ``await session.save_chat(...)`` is cooperative already.
                continue
            classified = _classify(call)
            if classified is None:
                continue
            kind, detail = classified
            findings.append(
                Finding(kind, path, call.lineno, node.name, detail)
            )
    return findings


def iter_targets(*roots: Path) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            yield from sorted(root.rglob("*.py"))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report synchronous work inside async def."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to audit (default: the API package)",
    )
    parser.add_argument(
        "--only",
        choices=list(FINDING_KINDS),
        help="Report a single finding kind",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with 1 when blocking calls are found (CI gate)",
    )
    args = parser.parse_args(argv)

    api_root = Path(__file__).resolve().parents[1] / "codx" / "junior"
    targets = [Path(p) for p in args.paths] or [api_root / "api", api_root / "app.py"]

    findings: List[Finding] = []
    for path in iter_targets(*targets):
        findings.extend(audit_file(path))

    if args.only:
        findings = [f for f in findings if f.kind == args.only]

    findings.sort(key=lambda f: (f.kind, str(f.path), f.line))
    for finding in findings:
        location = finding.path
        try:
            location = finding.path.relative_to(api_root.parent)
        except ValueError:
            pass
        print(f"{finding.kind:16} {location}:{finding.line}  {finding.func}()")
        print(f"{'':17}{finding.detail}")

    blocking = [f for f in findings if f.kind == FINDING_BLOCKING]
    no_await = [f for f in findings if f.kind == FINDING_NO_AWAIT]
    io_findings = [f for f in findings if f.kind == FINDING_IO]
    print()
    print(
        f"{len(blocking)} blocking call(s) on the event loop, "
        f"{len(no_await)} async def without await, "
        f"{len(io_findings)} blocking io call(s)."
    )

    if args.strict and blocking:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
