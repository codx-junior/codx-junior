"""
Durable storage of meetings.

A meeting is more than a live WebRTC room: it owns a transcript, notes,
minutes, action items and a recording. Those artifacts must survive the end of
the call and a process restart, so they are written next to the project's
``.codx`` folder::

    <codx_path>/meetings/<meet_id>.json       full meeting document
    <codx_path>/meetings/index.json           latest-meeting catalogue
    <codx_path>/meetings/recordings/<id>.<ext>

The project scope is intentional: tasks created from the minutes belong to the
project and the meeting chat is stored in the same project. A meeting is
cross-project only while it is live; the durable copy belongs to the project
that started it.

Writes are atomic (temporary sibling + ``os.replace``) so a reader never sees a
half-written JSON document. The index is a convenience catalogue: when it is
missing or corrupt, :meth:`MeetStore.list` falls back to scanning the folder.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading

from pathlib import Path
from typing import Any, Dict, List, Optional

from codx.junior.meets.model import Meet, MeetRecording, MeetStatus

logger = logging.getLogger(__name__)

#: Folder under ``<codx_path>`` that holds the meeting documents.
MEETINGS_FOLDER = "meetings"
INDEX_FILE = "index.json"
RECORDINGS_FOLDER = "recordings"

#: Meet ids are used as file names: accept UUIDs, 3-4-3 codes and seeded test
#: ids, but never a path separator or a traversal sequence.
SAFE_MEET_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: Index entries kept in ``index.json``. The full transcript is not copied
#: there: the summary page loads the meeting document.
MAX_INDEX_ENTRIES = 500


class MeetStoreError(RuntimeError):
    """Raised when a meeting document cannot be read or written."""


class MeetStore:
    """Persistence of one project's meetings."""

    def __init__(self, root: str):
        """
        Args:
            root: Absolute folder that holds the meeting documents (usually
                ``<codx_path>/meetings``).
        """
        self.root = str(root)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def for_settings(cls, settings: Any) -> "MeetStore":
        """Build the store of the project described by ``settings``."""
        codx_path = str(getattr(settings, "codx_path", "") or "").strip()
        if not codx_path:
            raise MeetStoreError("the project has no codx_path")
        return cls(os.path.join(codx_path, MEETINGS_FOLDER))

    @classmethod
    def default(cls) -> "MeetStore":
        """Store next to the runtime folder (used when no project is available)."""
        from codx.junior.globals import RUNTIME_FOLDER

        return cls(os.path.join(RUNTIME_FOLDER, MEETINGS_FOLDER))

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_meet_id(meet_id: str) -> str:
        """Return a safe meet id or raise ``MeetStoreError``."""
        value = str(meet_id or "").strip()
        if not SAFE_MEET_ID.match(value):
            raise MeetStoreError(f"invalid meet id '{meet_id}'")
        if ".." in value:
            raise MeetStoreError(f"invalid meet id '{meet_id}'")
        return value

    def meet_path(self, meet_id: str) -> str:
        """Absolute path of the meeting document."""
        return os.path.join(self.root, f"{self._validate_meet_id(meet_id)}.json")

    def index_path(self) -> str:
        """Absolute path of the catalogue."""
        return os.path.join(self.root, INDEX_FILE)

    def recording_path(self, meet_id: str, filename: str) -> str:
        """Absolute path of a recording file (the name is sanitized)."""
        safe_id = self._validate_meet_id(meet_id)
        clean_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or ""))
        if not clean_name:
            clean_name = f"{safe_id}.webm"
        if not os.path.splitext(clean_name)[1]:
            clean_name += ".webm"
        return os.path.join(self.root, RECORDINGS_FOLDER, clean_name)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def save(self, meet: Meet) -> Meet:
        """Write ``meet`` atomically and refresh the catalogue."""
        safe_id = self._validate_meet_id(meet.meet_id)
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            # Signalling data is live-only and can be large; everything else is
            # the durable meeting record.
            payload = meet.model_dump(mode="json", exclude={"signals"})
            self._atomic_write_json(self.meet_path(safe_id), payload)
            self._update_index(meet)
        return meet

    def load(self, meet_id: str) -> Optional[Meet]:
        """Load one meeting document, ``None`` when it does not exist."""
        path = self.meet_path(meet_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return Meet.model_validate(payload)
        except (OSError, ValueError) as ex:
            logger.warning("meetings: cannot load '%s': %s", path, ex)
            return None

    def list(
        self, limit: int = 50, project_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Return the latest meetings as compact summaries, newest first.

        The summary carries only what a history list needs (id, name, status,
        dates, participants, minutes status, recording status), not the full
        transcript.
        """
        limit = max(1, min(int(limit or 50), 200))
        with self._lock:
            entries = self._read_index()
            if not entries:
                entries = self._scan_index()
        if project_id:
            entries = [
                entry
                for entry in entries
                if not entry.get("project_id")
                or entry.get("project_id") == project_id
            ]
        entries.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
        return entries[:limit]

    def summaries(self, **kwargs) -> List[Dict[str, Any]]:
        """Alias kept for callers that prefer the explicit name."""
        return self.list(**kwargs)

    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------
    def save_recording(
        self,
        meet_id: str,
        content: bytes,
        filename: str,
        mime: str = "audio/webm",
        duration_seconds: float = 0.0,
    ) -> MeetRecording:
        """Store the recording file and return its recording document."""
        safe_id = self._validate_meet_id(meet_id)
        base_name = os.path.basename(str(filename or "")) or f"{safe_id}.webm"
        # Prefix with the meet id so two meetings never overwrite a recording
        # that happens to have the same original file name.
        if not base_name.startswith(f"{safe_id}-"):
            base_name = f"{safe_id}-{base_name}"
        path = self.recording_path(safe_id, base_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self._lock:
            self._atomic_write_bytes(path, content)
        return MeetRecording(
            status="ready",
            filename=os.path.basename(path),
            mime=mime or "audio/webm",
            size=len(content),
            duration_seconds=float(duration_seconds or 0.0),
            started_at=None,
            ended_at=None,
        )

    def recording_file(self, meet: Meet) -> Optional[str]:
        """Return the absolute path of the meeting recording, if it exists."""
        filename = (meet.recording or MeetRecording()).filename
        if not filename:
            return None
        path = os.path.join(self.root, RECORDINGS_FOLDER, os.path.basename(filename))
        return path if os.path.isfile(path) else None

    # ------------------------------------------------------------------
    # Index / atomic helpers
    # ------------------------------------------------------------------
    def _summary(self, meet: Meet) -> Dict[str, Any]:
        """Build the compact catalogue entry of a meeting."""
        return {
            "meet_id": meet.meet_id,
            "name": meet.name,
            "status": meet.status.value
            if isinstance(meet.status, MeetStatus)
            else str(meet.status),
            "initiator": meet.initiator,
            "project_id": meet.project_id,
            "source": meet.source,
            "channel_name": meet.channel_name,
            "created_at": meet.created_at,
            "updated_at": meet.updated_at,
            "ended_at": meet.ended_at,
            "agenda": meet.agenda,
            "participants": meet.participant_usernames(),
            "profiles": meet.profile_usernames(),
            "minutes_status": meet.minutes.status,
            "recording_status": meet.recording.status,
            "has_transcript": bool(meet.transcript),
        }

    def _read_index(self) -> List[Dict[str, Any]]:
        """Read ``index.json``; a missing/corrupt index yields an empty list."""
        try:
            with open(self.index_path(), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return []
        entries = payload.get("meetings") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict) and entry.get("meet_id")]

    def _scan_index(self) -> List[Dict[str, Any]]:
        """Rebuild the catalogue by reading the meeting documents."""
        entries: List[Dict[str, Any]] = []
        try:
            names = sorted(Path(self.root).glob("*.json"))
        except OSError:
            return entries
        for path in names:
            if path.name == INDEX_FILE:
                continue
            meet = self.load(path.stem)
            if meet is not None:
                entries.append(self._summary(meet))
        return entries

    def _update_index(self, meet: Meet) -> None:
        """Insert/replace the summary of ``meet`` in the catalogue."""
        entries = [
            entry
            for entry in self._read_index()
            if entry.get("meet_id") != meet.meet_id
        ]
        entries.insert(0, self._summary(meet))
        entries.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
        try:
            self._atomic_write_json(
                self.index_path(), {"meetings": entries[:MAX_INDEX_ENTRIES]}
            )
        except OSError as ex:  # pragma: no cover - a broken catalogue is recoverable
            logger.warning("meetings: cannot update the index: %s", ex)

    @staticmethod
    def _atomic_write_json(path: str, payload: Any) -> None:
        """Write JSON to ``path`` atomically."""
        MeetStore._atomic_write_bytes(
            path,
            json.dumps(payload, indent=2, default=str).encode("utf-8"),
        )

    @staticmethod
    def _atomic_write_bytes(path: str, content: bytes) -> None:
        """Write bytes to ``path`` atomically (sibling temp + replace)."""
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".meeting-", suffix=".tmp", dir=folder
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
