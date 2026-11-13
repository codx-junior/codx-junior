"""Persistence layer for the Project's Channel media pipeline.

The store owns everything that lives under ``<project>/.codx/media``:

    inbox/          dropped ``*.episode.json`` files waiting to be ingested
    inbox/done/     accepted inbox files (kept for traceability)
    jobs/           one ``<job_id>.job.json`` per MediaJob (leases + retries)
    episodes/       one ``<episode_id>.episode.json`` per EpisodeIR
    assets/ab/      content addressed assets, sharded by the first 2 hash chars
    tmp/            staging area that makes every write atomic

Pure python: stdlib + ``media.model`` only. No app settings, no network and no
module level state, so callers (and tests) can point it at any directory. A
project store is built with ``MediaStore.for_project(project_path)``; tests pass
a ``tmp_path``.
"""

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from pydantic import ValidationError

from .model import EpisodeIR, MediaAsset, MediaJob, utc_now

JOBS_DIRNAME = "jobs"
INBOX_DIRNAME = "inbox"
EPISODES_DIRNAME = "episodes"
ASSETS_DIRNAME = "assets"
TMP_DIRNAME = "tmp"
DONE_DIRNAME = "done"

JOB_SUFFIX = ".job.json"
EPISODE_SUFFIX = ".episode.json"

QUEUED = "queued"
LEASED = "leased"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
# States whose job holds a lease and can be reclaimed when the lease expires
LEASE_STATES = (LEASED, RUNNING)

# Deterministic mime -> extension fallback (``mimetypes`` misses several of these)
MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "text/vtt": ".vtt",
    "application/pdf": ".pdf",
}


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise a timestamp to UTC; naive values are assumed to be UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_ext(ext: str, mime: str) -> str:
    """Return a safe file extension (with the leading dot) for an asset."""
    value = (ext or "").strip().lower()
    if not value and mime:
        base_mime = (mime or "").split(";")[0].strip().lower()
        value = MIME_EXTENSIONS.get(base_mime) or mimetypes.guess_extension(base_mime) or ""
    if value and not value.startswith("."):
        value = "." + value
    return re.sub(r"[^.a-z0-9]", "", value)


class MediaStore:
    """Filesystem store for media jobs, episodes and content addressed assets."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.inbox_dir = self.root / INBOX_DIRNAME
        self.inbox_done_dir = self.inbox_dir / DONE_DIRNAME
        self.jobs_dir = self.root / JOBS_DIRNAME
        self.episodes_dir = self.root / EPISODES_DIRNAME
        self.assets_dir = self.root / ASSETS_DIRNAME
        self.tmp_dir = self.root / TMP_DIRNAME
        for directory in (
            self.inbox_dir,
            self.jobs_dir,
            self.episodes_dir,
            self.assets_dir,
            self.tmp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_project(cls, project_path: Path) -> "MediaStore":
        """Store of a project: ``<project>/.codx/media``."""
        return cls(Path(project_path) / ".codx" / "media")

    # ------------------------------------------------------------------ writes
    def write_atomic(self, target: Path, data: bytes) -> Path:
        """Write ``data`` to ``tmp/`` first and atomically move it to ``target``."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(
            dir=str(self.tmp_dir), prefix="write-", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            with suppress(OSError):
                tmp_path.unlink()
            raise
        return target

    def _write_json(self, target: Path, payload: dict) -> Path:
        data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        return self.write_atomic(target, data.encode("utf-8") + b"\n")

    def _read_json(self, path: Path) -> Optional[dict]:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -------------------------------------------------------------------- jobs
    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}{JOB_SUFFIX}"

    def put_job(self, job: MediaJob) -> MediaJob:
        """Persist a job, generating an id when the job has none yet."""
        if not job.job_id:
            job.job_id = f"job-{utc_now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        job.updated_at = utc_now()
        self._write_json(self._job_path(job.job_id), job.model_dump(mode="json"))
        return job

    def get_job(self, job_id: str) -> Optional[MediaJob]:
        """Load a job by id; ``None`` when it is unknown or unreadable."""
        if not job_id:
            return None
        payload = self._read_json(self._job_path(job_id))
        if payload is None:
            return None
        try:
            return MediaJob.model_validate(payload)
        except ValidationError:
            return None

    def list_jobs(self, state: Optional[str] = None) -> List[MediaJob]:
        """All known jobs (optionally filtered by state), oldest first."""
        jobs: List[MediaJob] = []
        for path in self.jobs_dir.glob(f"*{JOB_SUFFIX}"):
            payload = self._read_json(path)
            if payload is None:
                continue
            try:
                job = MediaJob.model_validate(payload)
            except ValidationError:
                continue
            if state is not None and job.state != state:
                continue
            jobs.append(job)
        jobs.sort(key=lambda item: (_as_aware(item.created_at), item.job_id))
        return jobs

    def _lease_expired(self, job: MediaJob, now: datetime, ttl_seconds: int) -> bool:
        reference = _as_aware(job.heartbeat_at or job.updated_at)
        if reference is None:
            return True
        return reference <= now - timedelta(seconds=max(0, int(ttl_seconds)))

    def claim_next(self, worker_id: str, ttl_seconds: int = 300) -> Optional[MediaJob]:
        """Lease the oldest claimable job for ``worker_id``.

        Claimable jobs are queued ones and running ones whose lease expired
        (crash recovery), which is how a restart resumes interrupted work.
        """
        now = utc_now()
        for job in self.list_jobs():
            if job.state == QUEUED:
                claimable = True
            elif job.state in LEASE_STATES:
                claimable = self._lease_expired(job, now, ttl_seconds)
            else:
                claimable = False
            if not claimable:
                continue
            if job.attempts >= job.max_attempts:
                # Already exhausted its retries: never claim it again
                job.state = FAILED
                job.worker_id = None
                job.heartbeat_at = None
                job.error = job.error or "max attempts reached"
                self.put_job(job)
                continue
            job.state = RUNNING
            job.worker_id = worker_id
            job.heartbeat_at = now
            job.attempts += 1
            job.updated_at = now
            return self.put_job(job)
        return None

    def heartbeat(self, job_id: str) -> bool:
        """Refresh the lease of a claimed job; ``False`` when it is not active."""
        job = self.get_job(job_id)
        if job is None or job.state not in LEASE_STATES:
            return False
        job.heartbeat_at = utc_now()
        self.put_job(job)
        return True

    def finish(
        self,
        job_id: str,
        assets: Optional[Iterable[MediaAsset]] = None,
        cost: float = 0.0,
    ) -> Optional[MediaJob]:
        """Mark a job done with its produced assets and accumulated cost."""
        job = self.get_job(job_id)
        if job is None:
            return None
        job.state = DONE
        job.assets = list(assets or [])
        job.cost = float(job.cost or 0.0) + float(cost or 0.0)
        job.error = None
        job.heartbeat_at = None
        return self.put_job(job)

    def fail(self, job_id: str, error: str) -> Optional[MediaJob]:
        """Record an error: requeue while retries are left, otherwise fail."""
        job = self.get_job(job_id)
        if job is None:
            return None
        job.error = str(error)
        job.worker_id = None
        job.heartbeat_at = None
        if job.attempts < job.max_attempts:
            job.state = QUEUED
        else:
            job.state = FAILED
        return self.put_job(job)

    # ------------------------------------------------------------------ assets
    def put_asset(
        self,
        content: bytes,
        kind: str,
        mime: str,
        provider: str = "",
        model: str = "",
        ext: str = "",
    ) -> MediaAsset:
        """Store bytes content addressed by sha256, idempotently.

        The same bytes always map to the same asset id and path, so a repeated
        call never writes a duplicate file (content addressed caching).
        """
        if isinstance(content, (bytearray, memoryview)):
            content = bytes(content)
        if not isinstance(content, bytes):
            raise TypeError("put_asset expects bytes")
        digest = hashlib.sha256(content).hexdigest()
        relative = Path(ASSETS_DIRNAME) / digest[:2] / f"{digest}{_normalize_ext(ext, mime)}"
        target = self.root / relative
        if not target.exists():
            self.write_atomic(target, content)
        return MediaAsset(
            asset_id=digest,
            kind=kind,
            path=relative.as_posix(),
            sha256=digest,
            mime=mime or "application/octet-stream",
            bytes=len(content),
            provider=provider or "",
            model=model or None,
        )

    # ---------------------------------------------------------------- episodes
    def _episode_path(self, episode_id: str) -> Path:
        return self.episodes_dir / f"{episode_id}{EPISODE_SUFFIX}"

    def get_episode(self, episode_id: str) -> Optional[EpisodeIR]:
        """Load an episode ir; ``None`` when it is unknown or unreadable."""
        if not episode_id:
            return None
        payload = self._read_json(self._episode_path(episode_id))
        if payload is None:
            return None
        try:
            return EpisodeIR.model_validate(payload)
        except ValidationError:
            return None

    def put_episode(self, ir: EpisodeIR) -> EpisodeIR:
        """Persist an episode ir as ``episodes/<episode_id>.episode.json``."""
        if not ir.episode_id:
            ir.episode_id = f"ep-{utc_now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self._write_json(self._episode_path(ir.episode_id), ir.model_dump(mode="json"))
        return ir

    # ------------------------------------------------------------------- inbox
    def inbox(self) -> List[Path]:
        """Dropped episode files waiting to be ingested, oldest name first."""
        if not self.inbox_dir.exists():
            return []
        return sorted(
            path
            for path in self.inbox_dir.glob(f"*{EPISODE_SUFFIX}")
            if path.is_file()
        )

    def take_inbox(self) -> Tuple[List[Path], List[EpisodeIR]]:
        """Ingest the inbox, keeping every accepted file under ``inbox/done/``.

        Unreadable files stay where they are so nothing is lost silently.
        """
        self.inbox_done_dir.mkdir(parents=True, exist_ok=True)
        paths: List[Path] = []
        episodes: List[EpisodeIR] = []
        for path in self.inbox():
            payload = self._read_json(path)
            if payload is None:
                continue
            try:
                ir = EpisodeIR.model_validate(payload)
            except ValidationError:
                continue
            done_path = self.inbox_done_dir / path.name
            os.replace(path, done_path)
            paths.append(path)
            episodes.append(ir)
        return paths, episodes
