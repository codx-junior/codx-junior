#!/usr/bin/env python3
"""Report card ids cited in the control plane that do not exist.

Kanban cards under ``.codx/tasks`` quote other card ids in their prose
(descriptions, comments, messages). Nothing validated those ids, so a gate card
that never existed (``8eded1c4``) survived several shifts as if it were real and
several reports were built on top of it.

This tool collects every id that actually exists in the control plane and then
extracts id-shaped tokens from the prose fields only. Any token that resolves to
no card/board is reported as a phantom reference and the exit code is 1, so it
can be used as a pre-commit or CI check.

Usage:
    python tools/validate_task_refs.py             # scan .codx/tasks
    python tools/validate_task_refs.py --root DIR  # scan another control plane
    python tools/validate_task_refs.py --quiet     # only print phantoms

The default control plane is discovered by walking up from this file until a
``.codx/tasks`` folder is found: the client lives in a sub-folder of the
repository that owns ``.codx``, so ``Path(__file__).parent.parent`` alone would
resolve to ``client/.codx/tasks`` and never find it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator, List, Set, Tuple

ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

# Only prose is validated: id-typed fields (project_id, board ids, ...) address
# objects from other catalogs and would produce noise.
PROSE_FIELDS = ("name", "description", "content", "text", "comment")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def discover_control_plane(start: Path) -> Path | None:
    """Return the closest ``.codx/tasks`` folder at or above ``start``."""
    for candidate in (start, *start.parents):
        control_plane = candidate / ".codx" / "tasks"
        if control_plane.is_dir():
            return control_plane
    return None


DEFAULT_ROOT = discover_control_plane(SCRIPT_DIR) or (PROJECT_ROOT / ".codx" / "tasks")


def iter_documents(root: Path) -> Iterator[Tuple[Path, Any]]:
    """Yield ``(path, payload)`` for every readable JSON file under ``root``."""
    for path in sorted(root.rglob("*.json")):
        try:
            yield path, json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue


def collect_ids(node: Any, found: Set[str]) -> None:
    """Collect every ``id`` value in ``node`` as an existing identifier."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "id" and isinstance(value, str):
                found.add(value.strip().lower())
            else:
                collect_ids(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_ids(item, found)


def collect_prose(node: Any, path: str = "") -> List[Tuple[str, str]]:
    """Return ``(location, text)`` for every prose field in ``node``."""
    found: List[Tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in PROSE_FIELDS and isinstance(value, str):
                found.append((here, value))
            else:
                found.extend(collect_prose(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(collect_prose(item, f"{path}[{index}]"))
    return found


def find_phantoms(root: Path) -> Tuple[int, int, List[Tuple[Path, str, str]]]:
    """Return ``(file_count, known_id_count, phantoms)`` for the control plane."""
    documents = list(iter_documents(root))
    known: Set[str] = set()
    for _, payload in documents:
        collect_ids(payload, known)

    phantoms: List[Tuple[Path, str, str]] = []
    seen: Set[Tuple[Path, str, str]] = set()
    for path, payload in documents:
        for where, text in collect_prose(payload):
            for match in ID_RE.findall(text):
                identifier = match.lower()
                if identifier in known:
                    continue
                entry = (path, where, identifier)
                if entry in seen:
                    continue
                seen.add(entry)
                phantoms.append(entry)
    return len(documents), len(known), phantoms


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help=(
            "control-plane folder to scan "
            f"(default: nearest .codx/tasks above this script, i.e. {DEFAULT_ROOT})"
        ),
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only the phantom references"
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: control plane not found: {root}", file=sys.stderr)
        return 2

    file_count, known_count, phantoms = find_phantoms(root)

    if not args.quiet:
        print(f"scanned {file_count} control-plane file(s) under {root}")
        print(f"{known_count} existing id(s) known")

    if not phantoms:
        print("OK - every cited card id resolves")
        return 0

    print(f"{len(phantoms)} phantom reference(s):")
    for path, where, identifier in phantoms:
        try:
            shown = path.relative_to(root)
        except ValueError:
            shown = path
        print(f"  {shown} :: {where} -> {identifier}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
