"""
Content-addressed storage for oversized chat payloads.

Tool output can be megabytes (one task in this repository has a 9.2 MB tool
event). Storing it inline makes the canonical task JSON huge, so every list,
save and redaction pass pays for bytes nobody reads. This module keeps the full
text in ``<tasks>/.blobs/<sha[:2]>/<sha>.txt`` and lets the task JSON keep a
short preview plus ``output_ref``.

Properties:
    * content-addressed and de-duplicated (same output = same file);
    * atomic write (temp + fsync + ``os.replace``);
    * text is CISO-redacted **before** it is written (a blob is not a secret
      escape hatch);
    * path traversal is rejected on read;
    * the directory is gitignored (regenerable, not project knowledge).

Made with ❤️ by codx-junior
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

#: Prefix of every reference stored in the task JSON.
REF_PREFIX = ".blobs/"


class BlobStore:
    """Store and retrieve large text payloads for one project.

    Args:
        tasks_path: Project ``.codx/tasks`` folder. Blobs live in
            ``<tasks_path>/.blobs``.
        max_bytes: Refuse payloads larger than this (default 64 MB). A tool
            output above the limit is left inline by the caller.
    """

    def __init__(self, tasks_path: str, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.tasks_path = os.path.abspath(str(tasks_path))
        self.root = os.path.join(self.tasks_path, ".blobs")
        self.max_bytes = int(max_bytes or 0)
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def put_text(self, text: str, redact: bool = True) -> Optional[Dict[str, object]]:
        """Store *text* and return ``{ref, sha256, bytes, replacements}``.

        Returns ``None`` when the text is empty, too large or cannot be
        written; callers must then keep the payload inline.
        """
        payload = str(text or "")
        if not payload:
            return None
        if self.max_bytes and len(payload.encode("utf-8", "ignore")) > self.max_bytes:
            logger.warning(
                "BlobStore: payload too large (%d bytes > %d)",
                len(payload.encode("utf-8", "ignore")),
                self.max_bytes,
            )
            return None
        replacements = 0
        if redact:
            try:
                from codx.junior.security.ciso.secrets import redact_text

                payload, replacements = redact_text(payload)
            except Exception as ex:  # pragma: no cover - CISO is optional in tests
                # Fail closed: never write a blob that was not sanitised. The
                # caller keeps the output inline and store_chat redacts it.
                logger.warning("BlobStore: redaction unavailable (%s)", ex)
                return None
        digest = hashlib.sha256(payload.encode("utf-8", "ignore")).hexdigest()
        relative = f"{REF_PREFIX}{digest[:2]}/{digest}.txt"
        target = os.path.join(self.tasks_path, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not os.path.isfile(target):
            tmp = f"{target}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, target)
            except OSError as ex:
                logger.warning("BlobStore: cannot write %s: %s", target, ex)
                return None
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        return {
            "ref": relative,
            "sha256": digest,
            "bytes": len(payload.encode("utf-8", "ignore")),
            "replacements": replacements,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def resolve(self, ref: str) -> str:
        """Resolve a reference to an absolute path, rejecting traversal."""
        value = str(ref or "").strip()
        if not value.startswith(REF_PREFIX):
            raise ValueError("invalid blob reference")
        candidate = os.path.realpath(os.path.join(self.tasks_path, *value.split("/")))
        root = os.path.realpath(self.root)
        if not (candidate == root or candidate.startswith(root + os.sep)):
            raise ValueError("blob reference escapes the project")
        return candidate

    def get_text(self, ref: str) -> Optional[str]:
        """Read a blob as UTF-8 text (None when missing/invalid)."""
        try:
            path = self.resolve(ref)
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, ValueError) as ex:
            logger.warning("BlobStore: cannot read %s: %s", ref, ex)
            return None

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def iter_refs(self) -> Iterable[Tuple[str, str]]:
        """Yield ``(ref, absolute_path)`` for every stored blob."""
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for name in filenames:
                if not name.endswith(".txt"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, self.tasks_path).replace(os.sep, "/")
                yield f"{REF_PREFIX}{rel[len(REF_PREFIX):]}", path

    def gc(self, referenced: Iterable[str]) -> int:
        """Delete blobs not present in *referenced*; return the deleted count."""
        keep = {
            os.path.realpath(self.resolve(ref))
            for ref in referenced
            if str(ref or "").startswith(REF_PREFIX)
        }
        removed = 0
        for _ref, path in list(self.iter_refs()):
            if os.path.realpath(path) in keep:
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        return removed
