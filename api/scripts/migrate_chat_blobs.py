#!/usr/bin/env python3
"""
Move oversized tool output out of chat JSON files into the project blob store.

The ChatEventBridge already externalizes new output, but tasks written before
that change keep megabytes of `tool_events[].output` inline. This is the
one-time/occasional maintenance tool for those files.

It is intentionally conservative:

* **dry-run by default** — it only reports what would change;
* `--apply` writes an atomic replacement and keeps a copy of every original
  under `tasks/.blobs-migration-backup/` (gitignored);
* text is CISO-redacted by :class:`codx.junior.chat.blobs.BlobStore` before it is
  stored, and the inline preview is taken from the redacted blob;
* it never touches `boards/`, `.archive/`, the archive index or catalogue files.

Usage::

    PYTHONPATH=api python api/scripts/migrate_chat_blobs.py \
        --tasks /path/to/project/.codx/tasks            # report only
    PYTHONPATH=api python api/scripts/migrate_chat_blobs.py \
        --tasks /path/to/project/.codx/tasks --apply    # rewrite files

After `--apply`, delete/rebuild the chat index (or let the reconciler run):
the files remain the source of truth and a rebuild is always safe.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from codx.junior.chat.blobs import BlobStore  # noqa: E402

STORE_FILES = {"index.json", "kanban.json", "boards.json", "kanban.v1.backup.json"}
STORE_DIRS = {"boards", ".archive", ".blobs", ".locks", ".blobs-migration-backup"}
DEFAULT_THRESHOLD = 32 * 1024
PREVIEW_CHARS = 4000


def chat_files(tasks: Path) -> List[Path]:
    """Return real chat files (same rule as ``ChatManager.chat_paths``)."""
    result: List[Path] = []
    for path in tasks.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(tasks).parts
        if len(parts) != 3 or parts[0] in STORE_DIRS or parts[-1] in STORE_FILES:
            continue
        if path.suffix in (".json", ".yaml"):
            result.append(path)
    return sorted(result)


def migrate_file(
    path: Path,
    tasks: Path,
    blobs: BlobStore,
    threshold: int,
    apply: bool,
) -> Dict[str, Any]:
    """Externalize the large tool events of one chat; return a report."""
    report = {"path": str(path), "events": 0, "bytes": 0, "error": None}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        report["error"] = str(ex)
        return report
    changed = False
    for message in document.get("messages") or []:
        for event in message.get("tool_events") or []:
            output = event.get("output")
            if not output or event.get("output_ref"):
                continue
            if len(str(output)) <= threshold:
                continue
            if not apply:
                # Dry-run must not create blobs: report the expected saving
                # only (preview + reference is roughly PREVIEW_CHARS).
                report["events"] += 1
                report["bytes"] += max(0, len(str(output)) - PREVIEW_CHARS)
                changed = True
                continue
            stored = blobs.put_text(str(output))
            if not stored:
                continue
            redacted = blobs.get_text(str(stored["ref"])) or ""
            preview = redacted[:PREVIEW_CHARS].rstrip()
            event["output"] = (
                f"{preview}\n\n… [{stored['bytes']} bytes; "
                f"full output: {stored['ref']}]"
            )
            event["output_ref"] = stored["ref"]
            event["output_bytes"] = stored["bytes"]
            event["output_sha256"] = stored["sha256"]
            event["output_truncated"] = True
            report["events"] += 1
            report["bytes"] += len(str(output)) - len(str(event["output"]))
            changed = True
    if changed and apply:
        # The rewrite bypasses ChatManager.store_chat, so run the same CISO
        # sanitisation before writing the file.
        try:
            from codx.junior.security.ciso.payload import redact_payload

            document, _ = redact_payload(document)
        except Exception as ex:
            report["error"] = f"CISO redaction failed, not writing: {ex}"
            return report
        backup = tasks / ".blobs-migration-backup" / path.relative_to(tasks)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(path, backup)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(document, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return report


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", default=".codx/tasks", help="project tasks folder")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"inline size limit in bytes (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--apply", action="store_true", help="rewrite the files")
    args = parser.parse_args(argv)

    tasks = Path(args.tasks).expanduser().resolve()
    if not tasks.is_dir():
        parser.error(f"not a directory: {tasks}")
    blobs = BlobStore(str(tasks))
    started = time.perf_counter()
    files = chat_files(tasks)
    changed = events = bytes_saved = 0
    errors = 0
    for path in files:
        report = migrate_file(path, tasks, blobs, args.threshold, args.apply)
        if report["error"]:
            errors += 1
            print(f"! {path}: {report['error']}")
            continue
        if report["events"]:
            changed += 1
            events += report["events"]
            bytes_saved += report["bytes"]
            print(
                f"{'APPLIED' if args.apply else 'WOULD CHANGE'} {path}: "
                f"{report['events']} event(s), {report['bytes'] / 1024:.0f} KB saved"
            )
    print(
        f"\n{'applied' if args.apply else 'dry-run'}: {len(files)} chats scanned, "
        f"{changed} file(s), {events} event(s), {bytes_saved / 1048576:.1f} MB "
        f"{'saved' if args.apply else 'would be saved'}, {errors} error(s), "
        f"{time.perf_counter() - started:.1f}s"
    )
    if not args.apply and changed:
        print("Re-run with --apply to rewrite the files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
