"""
Task archiving: compress finished tasks instead of keeping every chat live.

Problem solved
--------------
``.codx/tasks`` only grows: each turn appends messages to one JSON file and
nothing ever removes a task, so a busy project easily reaches hundreds of
megabytes. Every listing (``ChatManager.list_chats``, ``/api/chats``, the board
view, ``search_tasks``) then parses **all** of those files, and the board gets
buried under tasks nobody will open again.

Archiving takes one task out of the live tree and stores it compressed:

* the task disappears from every listing, search, cost roll-up and board,
  because :meth:`ChatManager.chat_paths` only discovers real chat files,
* it occupies a fraction of the space (gzip on an indented JSON chat is
  typically 10-20x smaller),
* it can be restored later, byte for byte, into the same board/column.

Layout
------
::

    .codx/tasks/
      <board>/<column>/<slug>.<chat_id>.json   live tasks
      .archive/
        index.json                             catalogue (metadata only)
        <chat_id>.tar.gz                       the archived task (chat.json)
        .archive.lock                          cross process write lock

``.archive`` lives **inside** ``tasks`` on purpose: archiving never crosses a
filesystem, and the project stays self contained (copy the ``.codx`` folder and
the archived tasks travel with it). The folder is hidden and excluded from chat
discovery by :func:`codx.junior.kanban.model.is_store_file`.

Why a tar.gz and not a single gzip
----------------------------------
A ``tar.gz`` keeps the original **file name** inside the archive. Restoring the
task therefore needs no guessing: the extracted name is the same
``<slug>.<chat_id>.json`` the chat manager produced, so a restored task is
indistinguishable from one that was never archived.

Index vs. payload
-----------------
``index.json`` holds only the metadata needed to *list* an archived task
(name, board, column, status, dates, sizes...). Listing archived tasks never
opens a payload, which is what makes ``include_archived=true`` cheap even with
thousands of archived tasks.

Concurrency
-----------
Every mutation happens while holding an ``fcntl`` lock (the same approach the
kanban store uses), so the API process and the background worker can archive
and restore concurrently without losing an index entry. Index writes are atomic
(``tmp`` + ``os.replace``), and a partially written payload never replaces a
finished one.

Made with ❤️ by codx-junior
"""

import json
import logging
import os
import tarfile

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

try:  # POSIX only; the container always has it.
    import fcntl
except ImportError:  # pragma: no cover - non POSIX fallback
    fcntl = None

from codx.junior.kanban.model import (
    ARCHIVE_DIR,
    ARCHIVE_INDEX_FILE,
    ARCHIVE_LOCK_FILE,
    ARCHIVE_SUFFIX,
    ARCHIVE_VERSION,
)

logger = logging.getLogger(__name__)

# Chat files the archiver is allowed to pack (a chat is always JSON/YAML).
CHAT_SUFFIXES = (".json", ".yaml")


def chat_id_from_filename(file_path: str) -> Optional[str]:
    """
    Extract the chat id from a chat file name.

    Chat files are always ``<slugified name>.<chat_id>.json`` (see
    :meth:`codx.junior.chat_manager.ChatManager.get_chat_file`), so the id is
    the last dot separated token, validated as a UUID.

    Args:
        file_path: Path (or file name) of a chat file.

    Returns:
        The chat id, or None when the name does not follow the convention.

    Example:
        >>> chat_id_from_filename("tasks/To do/fix-login.6f1c2b90-6d0a-4a2b-9c11-2f8d7e5a3b10.json")
        '6f1c2b90-6d0a-4a2b-9c11-2f8d7e5a3b10'
    """
    name = os.path.basename(str(file_path or ""))
    for suffix in CHAT_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = name.split(".")
    if len(parts) < 2:
        return None
    candidate = parts[-1]
    if len(candidate) == 36 and candidate.count("-") == 4:
        return candidate
    return None


class TaskArchive:
    """
    Compressed storage of archived tasks of one project.

    Attributes:
        tasks_path: Absolute ``<codx_path>/tasks`` folder.
        archive_path: Absolute ``<tasks>/.archive`` folder.
    """

    def __init__(self, tasks_path: str) -> None:
        """
        Args:
            tasks_path: Absolute path of the project's ``tasks`` folder.
        """
        self.tasks_path = os.path.abspath(str(tasks_path))
        self.archive_path = os.path.join(self.tasks_path, ARCHIVE_DIR)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    @property
    def index_file(self) -> str:
        """Absolute path of the archive catalogue."""
        return os.path.join(self.archive_path, ARCHIVE_INDEX_FILE)

    @property
    def lock_file(self) -> str:
        """Absolute path of the archive write lock."""
        return os.path.join(self.archive_path, ARCHIVE_LOCK_FILE)

    def archive_file(self, chat_id: str) -> str:
        """
        Absolute path of the payload of *chat_id*.

        Args:
            chat_id: Id of the archived task.

        Returns:
            Path of the ``<chat_id>.tar.gz`` file.
        """
        return os.path.join(self.archive_path, f"{chat_id}{ARCHIVE_SUFFIX}")

    # ------------------------------------------------------------------
    # Low level IO
    # ------------------------------------------------------------------
    @contextmanager
    def locked(self) -> Iterator[None]:
        """
        Hold the archive lock (cross process).

        Serialises archiving and restoring between the API process and the
        background worker. Degrades to a no-op when ``fcntl`` is unavailable.
        """
        os.makedirs(self.archive_path, exist_ok=True)
        handle = open(self.lock_file, "a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _read_index(self) -> Dict[str, Any]:
        """Read the catalogue, returning an empty one when missing or broken."""
        if not os.path.isfile(self.index_file):
            return {"version": ARCHIVE_VERSION, "tasks": {}}
        try:
            with open(self.index_file, "r", encoding="utf-8") as handle:
                data = json.loads(handle.read())
        except (OSError, ValueError) as ex:
            logger.warning("archive: cannot read '%s': %s", self.index_file, ex)
            return {"version": ARCHIVE_VERSION, "tasks": {}}
        if not isinstance(data, dict):
            return {"version": ARCHIVE_VERSION, "tasks": {}}
        if not isinstance(data.get("tasks"), dict):
            data["tasks"] = {}
        data.setdefault("version", ARCHIVE_VERSION)
        return data

    def _write_index(self, data: Dict[str, Any]) -> None:
        """Write the catalogue atomically so a crash never truncates it."""
        os.makedirs(self.archive_path, exist_ok=True)
        tmp_file = f"{self.index_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2))
        os.replace(tmp_file, self.index_file)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def entries(self) -> Dict[str, Dict[str, Any]]:
        """
        Return every archived task, indexed by chat id.

        Returns:
            ``{chat_id: entry}`` copied from the catalogue.
        """
        return dict(self._read_index().get("tasks") or {})

    def list_entries(self) -> List[Dict[str, Any]]:
        """
        Return the archived tasks, most recently archived first.

        Returns:
            List of catalogue entries (metadata only, no chat content).
        """
        entries = list(self.entries().values())
        return sorted(entries, key=lambda entry: str(entry.get("archived_at") or ""), reverse=True)

    def get(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the catalogue entry of one archived task.

        Args:
            chat_id: Id of the task.

        Returns:
            The entry, or None when the task is not archived.
        """
        return self.entries().get(str(chat_id or "").strip())

    def is_archived(self, chat_id: str) -> bool:
        """
        Tell whether a task is archived.

        Args:
            chat_id: Id of the task.

        Returns:
            True when the catalogue knows the task.
        """
        return self.get(chat_id) is not None

    def stats(self) -> Dict[str, Any]:
        """
        Summarise the archive (count and space saved).

        Returns:
            ``{"count", "archived_bytes", "original_bytes", "saved_bytes",
            "ratio"}`` where ``ratio`` is ``original_bytes / archived_bytes``.

        Example:
            >>> TaskArchive("/proj/.codx/tasks").stats()["count"]
            3
        """
        entries = self.entries().values()
        archived_bytes = sum(int(entry.get("size") or 0) for entry in entries)
        original_bytes = sum(int(entry.get("original_size") or 0) for entry in entries)
        return {
            "count": len(self.entries()),
            "archived_bytes": archived_bytes,
            "original_bytes": original_bytes,
            "saved_bytes": max(0, original_bytes - archived_bytes),
            "ratio": round(original_bytes / archived_bytes, 1) if archived_bytes else 0,
        }

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def _compress(self, chat_file: str, target_file: str) -> int:
        """
        Pack *chat_file* into *target_file* as a single member ``tar.gz``.

        The member keeps the original file name so a restore needs no guessing.
        The payload is written to a ``.tmp`` file first and moved into place, so
        an interrupted compression never leaves a corrupt archive behind.

        Args:
            chat_file: Chat file to compress.
            target_file: Destination ``.tar.gz`` path.

        Returns:
            Size in bytes of the written archive.
        """
        tmp_file = f"{target_file}.tmp"
        info = tarfile.TarInfo(name=os.path.basename(chat_file))
        info.size = os.path.getsize(chat_file)
        info.mtime = int(os.path.getmtime(chat_file))
        info.mode = 0o644
        with open(chat_file, "rb") as source, tarfile.open(tmp_file, "w:gz") as tar:
            tar.addfile(info, source)
        os.replace(tmp_file, target_file)
        return os.path.getsize(target_file)

    def _extract(self, archive_file: str, target_dir: str) -> str:
        """
        Extract the single member of *archive_file* into *target_dir*.

        The member name is reduced with ``os.path.basename`` and the resulting
        path is verified to stay directly inside *target_dir*, so a crafted
        archive can never write outside the tasks tree.

        Args:
            archive_file: Path of the ``.tar.gz`` payload.
            target_dir: Folder that receives the chat file.

        Returns:
            Absolute path of the extracted chat file.

        Raises:
            ValueError: When the archive is empty, is not a chat file, or tries
                to escape *target_dir*.
        """
        target_root = os.path.abspath(target_dir)
        os.makedirs(target_root, exist_ok=True)
        with tarfile.open(archive_file, "r:gz") as tar:
            member = tar.next()
            if member is None or not member.isfile():
                raise ValueError(f"'{archive_file}' does not contain a chat file")
            if member.name != os.path.basename(member.name):
                raise ValueError(
                    f"refusing to extract '{member.name}': a chat archive holds a "
                    "single file name, never a path"
                )
            name = os.path.basename(member.name)
            if not name.lower().endswith(CHAT_SUFFIXES):
                raise ValueError(f"'{archive_file}' does not contain a chat file ('{name}')")
            target_file = os.path.abspath(os.path.join(target_root, name))
            if os.path.dirname(target_file) != target_root:
                raise ValueError(f"refusing to extract '{name}' outside '{target_root}'")
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read the chat file of '{archive_file}'")
            with open(target_file, "wb") as out:
                out.write(source.read())
        return target_file

    def archive(
        self,
        chat_file: str,
        chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        archived_by: Optional[str] = None,
        original_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Compress a chat file and remove it from the live tree.

        The operation is idempotent: archiving an already archived task returns
        the stored entry instead of failing (the file is already gone).

        Args:
            chat_file: Absolute path of the chat file to archive.
            chat_id: Id of the chat; derived from the file name when omitted.
            metadata: Listing metadata stored in the catalogue (name, board,
                column, status, dates...). Only identifying/listing values:
                the payload already holds everything else.
            archived_by: User or profile that archived the task.
            original_path: Path relative to the tasks folder, kept for traces.

        Returns:
            The catalogue entry written for the task.

        Raises:
            FileNotFoundError: When *chat_file* does not exist and the task is not
                archived yet (archiving the same task twice is harmless).
            ValueError: When the chat id cannot be resolved.

        Example:
            >>> TaskArchive("/proj/.codx/tasks").archive(
            ...     "/proj/.codx/tasks/Bugs/To do/fix-login.<uuid>.json",
            ...     metadata={"board": "Bugs", "column": "To do"},
            ... )
        """
        chat_file = os.path.abspath(str(chat_file or ""))
        chat_id = (chat_id or chat_id_from_filename(chat_file) or "").strip()
        if not chat_id:
            raise ValueError(f"cannot resolve the chat id of '{chat_file}'")

        with self.locked():
            index = self._read_index()
            entries = index.setdefault("tasks", {})
            stored = entries.get(chat_id)
            if stored is not None:
                # Already archived (a retry, or two clients racing): keep the
                # stored entry and drop the stray live file.
                if os.path.isfile(chat_file):
                    os.remove(chat_file)
                return dict(stored)

            if not os.path.isfile(chat_file):
                raise FileNotFoundError(f"chat file not found: '{chat_file}'")

            target_file = self.archive_file(chat_id)
            original_size = os.path.getsize(chat_file)
            size = self._compress(chat_file, target_file)
            os.remove(chat_file)

            relative = os.path.relpath(chat_file, self.tasks_path)
            entry = {
                "chat_id": chat_id,
                "archive_file": os.path.basename(target_file),
                "original_file": original_path or relative,
                "archived_at": datetime.now().isoformat(),
                "archived_by": archived_by,
                "size": size,
                "original_size": original_size,
                **{key: value for key, value in (metadata or {}).items() if value is not None},
            }
            entries[chat_id] = entry
            self._write_index(index)

        logger.info(
            "archive: task '%s' archived (%d -> %d bytes, %.1fx)",
            chat_id,
            entry["original_size"],
            entry["size"],
            entry["original_size"] / entry["size"] if entry["size"] else 1,
        )
        return dict(entry)

    def restore(self, chat_id: str, target_dir: str) -> str:
        """
        Extract an archived task back into *target_dir* and drop the archive.

        Args:
            chat_id: Id of the archived task.
            target_dir: Folder that must receive the chat file (the board and
                column folders are resolved by the caller, which knows the
                catalogue).

        Returns:
            Absolute path of the restored chat file.

        Raises:
            FileNotFoundError: When the task is not archived or its payload is
                missing.
            ValueError: When the payload is not a readable chat archive.
        """
        chat_id = str(chat_id or "").strip()
        with self.locked():
            index = self._read_index()
            entries = index.setdefault("tasks", {})
            entry = entries.get(chat_id)
            if entry is None:
                raise FileNotFoundError(f"task '{chat_id}' is not archived")

            archive_file = self.archive_file(chat_id)
            if not os.path.isfile(archive_file):
                raise FileNotFoundError(
                    f"the archive payload of task '{chat_id}' is missing: '{archive_file}'"
                )
            restored_file = self._extract(archive_file, target_dir)
            os.remove(archive_file)
            entries.pop(chat_id, None)
            self._write_index(index)

        logger.info("archive: task '%s' restored to '%s'", chat_id, restored_file)
        return restored_file

    def forget(self, chat_id: str) -> bool:
        """
        Delete an archived task permanently (payload + catalogue entry).

        Args:
            chat_id: Id of the archived task.

        Returns:
            True when the task was archived and has been removed.
        """
        chat_id = str(chat_id or "").strip()
        with self.locked():
            index = self._read_index()
            entries = index.setdefault("tasks", {})
            if entries.pop(chat_id, None) is None:
                return False
            archive_file = self.archive_file(chat_id)
            if os.path.isfile(archive_file):
                try:
                    os.remove(archive_file)
                except OSError as ex:
                    logger.warning("archive: cannot remove '%s': %s", archive_file, ex)
            self._write_index(index)

        logger.info("archive: task '%s' permanently deleted", chat_id)
        return True

# Made with ❤️ by codx-junior
