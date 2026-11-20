"""CISO scan service: run project analyses and answer the security view.

The detectors and the policy engine already decide *what* is a finding. This
module adds the missing operational layer of a CISO screen:

* **Scans** run in the background, are persisted as JSON in the project data
  plane (``.codx/ciso/scans`` by default, or wherever
  ``CODX_JUNIOR_CODX_DATA_PATH`` points) and can be cancelled.
* **Posture** aggregates the latest result, the gate journal
  (``ciso_verdict`` entries of the harness audit) and the repository hygiene
  checks into the numbers the overview renders.
* **Policy** reads and writes the project ``ciso_policy`` block with the same
  validation the gates use, so a rule toggled in the UI is effective for the
  very next file write — no restart and no second source of truth.

Design constraints
    * Nothing here imports the application dependency tree: the service runs in
      the API and is exercised directly by tests with a plain settings stub.
    * Every write is atomic (temporary file + ``os.replace``) and bounded
      (retention keeps the newest scans).
    * A scan never blocks an API worker: the request creates the record and a
      daemon thread executes it. A cross-process ``flock`` guarantees one scan
      per project even when the API runs with several workers.
    * Results are generated data: they live in the data plane and are ignored
      by git, never committed.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

from codx.junior.runtime import DATA_PLANE_ENTRIES, data_dir
from codx.junior.security.ciso import models as ciso_models
from codx.junior.security.ciso import rules as ciso_rules
from codx.junior.security.ciso import scanner
from codx.junior.security.ciso import policy as ciso_policy
from codx.junior.security.ciso import vault as ciso_vault
from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityAction,
    SecurityCategory,
    SecurityDomain,
    SecuritySeverity,
    SecurityVerdict,
    parse_severity,
    severity_rank,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCAN_STATUS_QUEUED = "queued"
SCAN_STATUS_RUNNING = "running"
SCAN_STATUS_COMPLETED = "completed"
SCAN_STATUS_FAILED = "failed"
SCAN_STATUS_CANCELLED = "cancelled"
SCAN_STATUSES = (
    SCAN_STATUS_QUEUED,
    SCAN_STATUS_RUNNING,
    SCAN_STATUS_COMPLETED,
    SCAN_STATUS_FAILED,
    SCAN_STATUS_CANCELLED,
)

SCOPE_PROJECT = "project"
SCOPE_CHANGES = "changes"
SCOPE_STAGED = "staged"
SCOPE_PATHS = "paths"
SCAN_SCOPES = (SCOPE_PROJECT, SCOPE_CHANGES, SCOPE_STAGED, SCOPE_PATHS)

#: Rows of history kept per project (newest first). Scans carry findings, so the
#: cap is also the storage bound.
MAX_STORED_SCANS = 25

#: Scan records older than this are pruned even below ``MAX_STORED_SCANS``.
MAX_SCAN_AGE_DAYS = 30

#: Findings persisted per scan. Counters are always complete; only the list is
#: truncated so one pathological file cannot make the history unbounded.
MAX_STORED_FINDINGS = 2000

#: File cap of one scan.
MAX_SCAN_FILES = 5000
DEFAULT_SCAN_FILES = 500

#: A running record with a live pid is considered alive for this long without a
#: heartbeat. The runner writes a heartbeat every few files.
SCAN_HEARTBEAT_SECONDS = 60

#: How often the runner writes a heartbeat. It runs independently of the scan
#: progress, so a long single-file/diff analysis still proves it is alive and a
#: process killed mid-scan is detected within a minute.
SCAN_HEARTBEAT_SAVE_SECONDS = 10

#: A queued record older than this never started (its worker was killed before
#: the thread picked it up, or the process died between save and start). Unlike a
#: running scan, a queued one has no legitimate long-lived state.
SCAN_QUEUED_STALE_SECONDS = 2 * 60

#: Window used by the overview for gate statistics.
GATE_WINDOW_HOURS = 7 * 24

#: Journal entries read for the activity panel / gate stats.
AUDIT_READ_LIMIT = 2000
ACTIVITY_DEFAULT_LIMIT = 100

#: Directory under the data plane holding this project's scan records.
SCAN_DIR_NAME = "ciso"
SCAN_SUBDIR = "scans"

_GIT_TIMEOUT_SECONDS = 60
_PROGRESS_SAVE_INTERVAL_SECONDS = 1.0

#: Serialise start/stop of runners inside one process.
_RUNNERS: Dict[str, "_ScanRunner"] = {}
_RUNNERS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class CisoServiceError(Exception):
    """Base error of the CISO service (mapped to HTTP 400 by the router)."""


class ScanBusyError(CisoServiceError):
    """A scan is already running for this project."""


class ScanNotFound(CisoServiceError):
    """The requested scan record does not exist."""


class UnknownRuleError(CisoServiceError):
    """The requested rule id is not part of the detector catalogue."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    """Return *value* (default: now) as an ISO-8601 UTC string."""
    return (value or _utcnow()).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp, returning ``None`` when it is not parseable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _codx_path(settings: Any) -> str:
    """Return the project ``.codx`` path or raise a service error."""
    path = str(getattr(settings, "codx_path", "") or "").strip()
    if not path:
        raise CisoServiceError("The project has no codx_path: cannot store security scans")
    return path


def _project_path(settings: Any) -> str:
    """Return the absolute project path or raise a service error."""
    path = str(getattr(settings, "abs_project_path", "") or "").strip()
    if not path:
        raise CisoServiceError("The project has no abs_project_path: cannot scan it")
    return os.path.abspath(path)


def _project_name(settings: Any) -> str:
    """Return the human project name (fallback: folder name)."""
    name = str(getattr(settings, "project_name", "") or "").strip()
    return name or os.path.basename(_project_path(settings))


def scans_dir(settings: Any) -> str:
    """Return (creating it) the folder holding this project's scan records."""
    codx_path = _codx_path(settings)
    path = data_dir(codx_path, SCAN_DIR_NAME, SCAN_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _new_scan_id() -> str:
    """Return a sortable, unique scan id."""
    return f"scan-{_utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _pid_alive(pid: Any) -> bool:
    """Return True when *pid* is a live process (best effort)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _as_int(value: Any, default: int) -> int:
    """Coerce *value* to an int inside a safe range (never raises)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_paths(project_path: str, paths: Optional[Iterable[str]]) -> List[str]:
    """Validate and normalise the explicit paths of a scan.

    Paths are returned project-relative with ``/`` separators. A path that
    escapes the project (``..``, an absolute path outside the root) is rejected:
    a project scan must never read the host because a request asked for it.

    Args:
        project_path: Absolute project root.
        paths: Paths supplied by the caller.

    Returns:
        list: Project-relative paths.

    Raises:
        CisoServiceError: When a path is absolute and outside the project, or
            when it escapes the root through ``..``.
    """
    root = os.path.realpath(project_path)
    normalised: List[str] = []
    for raw in paths or []:
        value = str(raw or "").strip()
        if not value:
            continue
        candidate = value if os.path.isabs(value) else os.path.join(root, value)
        candidate = os.path.realpath(candidate)
        if candidate != root and not candidate.startswith(root + os.sep):
            raise CisoServiceError(f"Path is outside the project: {value}")
        relative = os.path.relpath(candidate, root).replace(os.sep, "/")
        if relative == ".":
            relative = ""
        if relative:
            normalised.append(relative)
    return normalised


def _git(project_path: str, args: List[str]) -> tuple:
    """Run a read-only git command in *project_path*.

    Returns:
        tuple: ``(ok, stdout, stderr)``. ``ok`` is False when git is missing,
        the command timed out or it returned a non-zero status.
    """
    command = ["git", "--no-pager", "-c", "core.quotepath=false", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        return False, "", str(ex)
    return completed.returncode == 0, completed.stdout, completed.stderr


def _git_diff(project_path: str, staged: bool, scope: str) -> str:
    """Return the unified diff of the requested scope (empty when clean)."""
    if scope == SCOPE_STAGED:
        ok, stdout, _ = _git(project_path, ["diff", "--cached", "--no-color", "-U0"])
        return stdout if ok else ""
    # Working tree scope includes both staged and unstaged changes.
    ok, stdout, _ = _git(project_path, ["diff", "HEAD", "--no-color", "-U0"])
    if ok:
        return stdout
    # A repository without commits (or an invalid HEAD): fall back to the two
    # halves independently so the analysis still shows what is staged/working.
    parts = []
    for args in (["diff", "--cached", "--no-color", "-U0"], ["diff", "--no-color", "-U0"]):
        ok, stdout, _ = _git(project_path, args)
        if ok and stdout:
            parts.append(stdout)
    return "\n".join(parts)


def _untracked_files(project_path: str, limit: int) -> List[str]:
    """Return project-relative untracked files, bounded by *limit*."""
    ok, stdout, _ = _git(project_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    if not ok or not stdout:
        return []
    files = [item for item in stdout.split("\x00") if item]
    return files[: max(0, limit)]


def _verdict_summary(verdict: SecurityVerdict, files_scanned: int = 0) -> Dict[str, Any]:
    """Return the aggregate counters the view renders for one verdict."""
    counts = {severity.value: 0 for severity in SecuritySeverity}
    categories = {category.value: 0 for category in SecurityCategory}
    for finding in verdict.findings:
        counts[finding.severity.value] = counts.get(finding.severity.value, 0) + 1
        categories[finding.category.value] = (
            categories.get(finding.category.value, 0) + 1
        )
    return {
        "risk_score": verdict.risk_score,
        "action": verdict.action.value,
        "highest_severity": (
            verdict.highest_severity.value if verdict.highest_severity else None
        ),
        "blocked": verdict.blocked,
        "requires_approval": verdict.requires_approval,
        "allowed": verdict.allowed,
        "total_findings": len(verdict.findings),
        "counts": counts,
        "categories": categories,
        "rules_evaluated": verdict.rules_evaluated,
        "truncated": verdict.truncated,
        "files_scanned": files_scanned,
        "summary": verdict.summary,
    }


def _finding_order(finding: Any) -> tuple:
    """Sort key: highest severity first, then path and line."""
    return (
        -severity_rank(finding.severity),
        str(finding.path or ""),
        int(finding.line or 0),
        str(finding.rule_id or ""),
    )


def _finding_rows(verdict: SecurityVerdict) -> List[Dict[str, Any]]:
    """Return the findings as JSON-safe dictionaries, ordered for review."""
    ordered = sorted(verdict.findings, key=_finding_order)
    return [finding.to_dict() for finding in ordered]


def _merge_verdicts(
    verdicts: List[SecurityVerdict],
    *,
    domain: SecurityDomain,
    policy: CisoPolicy,
    files_scanned: int = 0,
) -> SecurityVerdict:
    """Merge several verdicts into one using the same policy engine."""
    findings = []
    truncated = False
    evaluated = 0
    for verdict in verdicts:
        findings.extend(verdict.findings)
        truncated = truncated or verdict.truncated
        evaluated += verdict.rules_evaluated or 0
    merged = ciso_policy.evaluate(
        findings,
        domain=domain,
        policy=policy,
        rules_evaluated=evaluated or scanner.RULE_COUNT,
        truncated=truncated,
    )
    if files_scanned:
        merged.summary = f"{merged.summary} | {files_scanned} file(s) scanned"
    return merged


def _posture_from_summary(summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive the posture score/grade from the latest scan summary."""
    if not summary:
        return {
            "available": False,
            "score": None,
            "grade": None,
            "risk_score": None,
            "highest_severity": None,
            "action": None,
            "total_findings": 0,
            "counts": {severity.value: 0 for severity in SecuritySeverity},
            "categories": {category.value: 0 for category in SecurityCategory},
        }
    risk = float(summary.get("risk_score") or 0.0)
    # The score is deliberately simple and explainable: 100 is clean and every
    # risk point subtracts one point (one CRITICAL finding is 25 points).
    score = max(0, min(100, round(100 - risk)))
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"
    return {
        "available": True,
        "score": score,
        "grade": grade,
        "risk_score": risk,
        "highest_severity": summary.get("highest_severity"),
        "action": summary.get("action"),
        "total_findings": int(summary.get("total_findings") or 0),
        "counts": summary.get("counts") or {},
        "categories": summary.get("categories") or {},
    }


# ---------------------------------------------------------------------------
# Scan record + store
# ---------------------------------------------------------------------------
@dataclass
class ScanRecord:
    """One persisted security scan.

    The record is the single source of truth for the UI: status transitions,
    progress, the policy snapshot used by the scan and the (bounded) findings.
    """

    scan_id: str
    status: str = SCAN_STATUS_QUEUED
    scope: str = SCOPE_PROJECT
    paths: List[str] = field(default_factory=list)
    max_files: int = DEFAULT_SCAN_FILES
    min_severity: Optional[str] = None
    created_at: str = field(default_factory=_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    progress_done: int = 0
    progress_total: int = 0
    heartbeat_at: Optional[str] = None
    pid: Optional[int] = None
    summary: Optional[Dict[str, Any]] = None
    findings: List[Dict[str, Any]] = field(default_factory=list)
    findings_truncated: bool = False
    error: str = ""
    cancel_requested: bool = False
    policy: Dict[str, Any] = field(default_factory=dict)
    policy_enabled: bool = True
    project_name: str = ""
    project_path: str = ""

    def to_dict(self, include_findings: bool = True) -> Dict[str, Any]:
        """Return the JSON-safe record.

        Args:
            include_findings: Include the findings array (the list endpoint
                omits it; the findings endpoint pages it separately).

        Returns:
            dict: The record.
        """
        payload = {
            "scan_id": self.scan_id,
            "status": self.status,
            "scope": self.scope,
            "paths": list(self.paths),
            "max_files": self.max_files,
            "min_severity": self.min_severity,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "progress": {
                "done": self.progress_done,
                "total": self.progress_total,
                "percent": (
                    round(self.progress_done * 100 / self.progress_total)
                    if self.progress_total
                    else (100 if self.status == SCAN_STATUS_COMPLETED else 0)
                ),
            },
            "heartbeat_at": self.heartbeat_at,
            "pid": self.pid,
            "summary": self.summary,
            "findings_count": len(self.findings),
            "findings_truncated": self.findings_truncated,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "policy": self.policy,
            "policy_enabled": self.policy_enabled,
            "project_name": self.project_name,
        }
        if include_findings:
            payload["findings"] = list(self.findings)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScanRecord":
        """Build a record from persisted JSON (tolerating older files)."""
        known = set(cls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
        payload = {key: value for key, value in (data or {}).items() if key in known}
        return cls(**payload)


class ScanStore:
    """Persistence of the scan records of one project."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.directory = scans_dir(settings)
        self.lock_path = os.path.join(self.directory, ".scan.lock")

    def _path(self, scan_id: str) -> str:
        """Return the record path of *scan_id* (validated id)."""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(scan_id or ""))
        if not safe or safe != str(scan_id):
            raise ScanNotFound(f"Invalid scan id: {scan_id}")
        return os.path.join(self.directory, f"{safe}.json")

    def _cancel_path(self, scan_id: str) -> str:
        """Return the cancellation marker path of *scan_id*."""
        return os.path.join(self.directory, f"{self._path(scan_id).rsplit('.', 1)[0]}.cancel")

    def save(self, record: ScanRecord) -> None:
        """Write the record atomically (never leaves a half-written JSON file)."""
        path = self._path(record.scan_id)
        temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(record.to_dict(include_findings=True), handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except OSError:
                pass

    def load(self, scan_id: str) -> ScanRecord:
        """Load one record or raise :class:`ScanNotFound`."""
        path = self._path(scan_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as ex:
            raise ScanNotFound(f"Scan not found: {scan_id}") from ex
        except (OSError, ValueError) as ex:
            raise ScanNotFound(f"Scan record is unreadable: {scan_id}") from ex
        return ScanRecord.from_dict(data)

    def delete(self, scan_id: str) -> bool:
        """Delete one record (and its cancellation marker)."""
        removed = False
        for path in (self._path(scan_id), self._cancel_path(scan_id)):
            try:
                os.remove(path)
                removed = True
            except FileNotFoundError:
                continue
            except OSError as ex:
                logger.warning("CISO: could not remove %s: %s", path, ex)
        return removed

    def list(self, limit: int = MAX_STORED_SCANS, offset: int = 0) -> List[ScanRecord]:
        """Return records newest first (bounded)."""
        entries: List[ScanRecord] = []
        try:
            names = [name for name in os.listdir(self.directory) if name.endswith(".json")]
        except OSError:
            return []
        for name in sorted(names, reverse=True):
            try:
                with open(os.path.join(self.directory, name), "r", encoding="utf-8") as handle:
                    entries.append(ScanRecord.from_dict(json.load(handle)))
            except (OSError, ValueError, TypeError):
                continue
        entries.sort(key=lambda record: record.created_at, reverse=True)
        offset = max(0, _as_int(offset, 0))
        limit = max(1, min(_as_int(limit, MAX_STORED_SCANS), MAX_STORED_SCANS))
        return entries[offset : offset + limit]

    def count(self) -> int:
        """Return the number of stored records."""
        try:
            return len([name for name in os.listdir(self.directory) if name.endswith(".json")])
        except OSError:
            return 0

    def request_cancel(self, scan_id: str) -> None:
        """Create the cancellation marker watched by the runner."""
        path = self._cancel_path(scan_id)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_iso())

    def cancel_requested(self, scan_id: str) -> bool:
        """Return True when a cancellation marker exists."""
        return os.path.exists(self._cancel_path(scan_id))

    def clear_cancel(self, scan_id: str) -> None:
        """Remove the cancellation marker (best effort)."""
        try:
            os.remove(self._cancel_path(scan_id))
        except OSError:
            pass

    def prune(self) -> None:
        """Enforce retention: newest ``MAX_STORED_SCANS`` and age bound."""
        entries = self.list(limit=MAX_STORED_SCANS, offset=0)
        keep = {record.scan_id for record in entries}
        cutoff = _utcnow() - timedelta(days=MAX_SCAN_AGE_DAYS)
        try:
            names = [name for name in os.listdir(self.directory) if name.endswith(".json")]
        except OSError:
            return
        for name in names:
            scan_id = name[: -len(".json")]
            path = os.path.join(self.directory, name)
            remove = scan_id not in keep
            if not remove:
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        created = _parse_iso(json.load(handle).get("created_at"))
                    remove = bool(created and created < cutoff)
                except (OSError, ValueError):
                    remove = False
            if remove:
                try:
                    os.remove(path)
                except OSError:
                    continue
                self.clear_cancel(scan_id)

    def reconcile(self) -> None:
        """Mark scans interrupted by a restart as failed.

        A scan whose pid is still alive and whose heartbeat is fresh belongs to
        another worker of the same installation: leave it alone.
        """
        cutoff = _utcnow() - timedelta(seconds=SCAN_HEARTBEAT_SECONDS)
        # A worker that has just persisted the queued record has not started its
        # thread yet: do not declare it dead during that (tiny) window.
        spawn_grace = _utcnow() - timedelta(seconds=120)
        for record in self.list(limit=MAX_STORED_SCANS):
            if record.status not in (SCAN_STATUS_QUEUED, SCAN_STATUS_RUNNING):
                continue
            created = _parse_iso(record.created_at)
            if (
                record.status == SCAN_STATUS_QUEUED
                and not record.pid
                and created
                and created >= spawn_grace
            ):
                continue
            if record.status == SCAN_STATUS_QUEUED:
                stale = _utcnow() - timedelta(seconds=SCAN_QUEUED_STALE_SECONDS)
                if created and created < stale:
                    # The pid may have been reused by a new API process; a
                    # queued record has no legitimate reason to wait this long.
                    record.status = SCAN_STATUS_FAILED
                    record.error = "The scan never started (worker restarted)"
                    record.finished_at = _iso()
                    record.heartbeat_at = record.finished_at
                    self.save(record)
                    continue
            heartbeat = _parse_iso(record.heartbeat_at or record.started_at)
            # The heartbeat, not the pid, is the authority: a pid can be reused
            # after a restart (or be unsignalable from this container), while a
            # live runner writes a heartbeat every few seconds even during a
            # long diff.
            if heartbeat and heartbeat >= cutoff:
                continue
            record.status = SCAN_STATUS_FAILED
            record.error = record.error or "The scan stopped sending heartbeats"
            record.finished_at = record.finished_at or _iso()
            record.heartbeat_at = record.finished_at
            self.save(record)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class _ScanRunner:
    """Execute one scan in a separate process and persist its lifecycle.

    Scanning a large working tree is CPU-bound (regular expressions over every
    added line) and would otherwise hold the GIL and freeze the API worker that
    serves the chat UI. The scan therefore runs in a child process; the API only
    supervises it, reads the record file and serves progress. A daemon thread in
    the parent watches the child, releases the project lock and removes the
    in-process registry entry.
    """

    def __init__(self, settings: Any, record: ScanRecord, store: ScanStore) -> None:
        self.settings = settings
        self.record = record
        self.store = store
        self.project_path = _project_path(settings)
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._lock_handle = None
        self._last_save = 0.0
        self._process = None
        self._monitor_thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Acquire the project scan lock and start the child process."""
        self._lock_handle = self._acquire_lock()
        if self._lock_handle is None:
            raise ScanBusyError("A security scan is already running for this project")
        context = multiprocessing.get_context("spawn")
        self._process = context.Process(
            target=_run_scan_process,
            args=(
                str(getattr(self.settings, "codx_path", "")),
                self.project_path,
                self.record.to_dict(include_findings=True),
            ),
            name=f"ciso-scan-{self.record.scan_id}",
            daemon=True,
        )
        try:
            self._process.start()
        except Exception as ex:
            self._release_lock()
            raise ScanBusyError(f"Could not start the security scan process: {ex}") from ex
        self._monitor_thread = threading.Thread(
            target=self._monitor_process,
            name=f"ciso-monitor-{self.record.scan_id}",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_process(self) -> None:
        """Wait for the child, then release the lock and the registry entry."""
        process = self._process
        try:
            if process is not None:
                process.join()
        finally:
            self._release_lock()
            with _RUNNERS_LOCK:
                _RUNNERS.pop(self.record.scan_id, None)

    def _acquire_lock(self):
        """Open and flock the project scan lock (None when taken)."""
        try:
            import fcntl  # Unix only; the deployment is Linux containers
        except ImportError:  # pragma: no cover - Windows
            return True
        try:
            handle = open(self.store.lock_path, "a+", encoding="utf-8")
        except OSError as ex:
            logger.warning("CISO: cannot open scan lock %s: %s", self.store.lock_path, ex)
            return True
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    def cancel(self) -> None:
        """Cancel the scan and terminate the child process.

        The marker is written first so a runner in another worker sees the
        request at its next file boundary; this process terminates its own
        child immediately because a single huge diff cannot be interrupted
        cooperatively.
        """
        self._cancel.set()
        self.store.request_cancel(self.record.scan_id)
        self.record.cancel_requested = True
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=3)
        self.record.status = SCAN_STATUS_CANCELLED
        self.record.finished_at = _iso()
        self.record.error = self.record.error or "Scan cancelled by the user"
        try:
            self.store.save(self.record)
            self.store.clear_cancel(self.record.scan_id)
        except Exception as ex:  # pragma: no cover
            logger.debug("CISO: could not persist cancellation: %s", ex)

    # -- progress ----------------------------------------------------------
    def _heartbeat_loop(self) -> None:
        """Write a heartbeat until the scan finishes.

        This is deliberately independent from the progress callback: a large
        diff may spend minutes inside one scanner call, and the operator (and
        the reconciliation on restart) must still be able to tell a live scan
        from a dead one.
        """
        while not self._done.wait(SCAN_HEARTBEAT_SAVE_SECONDS):
            self.record.heartbeat_at = _iso()
            try:
                self.store.save(self.record)
            except Exception as ex:  # pragma: no cover - persistence is best effort
                logger.debug("CISO: heartbeat write failed: %s", ex)

    def _heartbeat(self) -> None:
        """Persist progress/heartbeat at most once per interval."""
        now = time.monotonic()
        if now - self._last_save < _PROGRESS_SAVE_INTERVAL_SECONDS:
            return
        self._last_save = now
        self.record.heartbeat_at = _iso()
        try:
            self.store.save(self.record)
        except Exception as ex:  # pragma: no cover - persistence is best effort here
            logger.debug("CISO: could not persist scan progress: %s", ex)

    def _progress(self, done: int, total: int) -> None:
        """Progress callback passed to the scanner."""
        self.record.progress_done = int(done)
        self.record.progress_total = int(total)
        self._heartbeat()

    def _should_cancel(self) -> bool:
        """Cancellation predicate passed to the scanner."""
        return self._cancel.is_set() or self.store.cancel_requested(self.record.scan_id)

    # -- execution ---------------------------------------------------------
    def _run(self) -> None:
        """Child-process body: run the scan and persist the terminal state."""
        record = self.record
        record.status = SCAN_STATUS_RUNNING
        record.started_at = _iso()
        record.heartbeat_at = record.started_at
        record.pid = os.getpid()
        # Persist RUNNING before the expensive part. A working-tree diff on a
        # large repository can take minutes; without this write the record stays
        # "queued" on disk and a concurrent poller may classify it as stale.
        try:
            self.store.save(record)
        except Exception as ex:  # pragma: no cover - persistence is best effort
            logger.warning("CISO: could not persist scan start: %s", ex)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"ciso-heartbeat-{record.scan_id}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        started = time.monotonic()
        try:
            policy = ciso_policy.policy_from_dict(record.policy)
            verdict, files_scanned = self._execute(policy)
            record.summary = _verdict_summary(verdict, files_scanned=files_scanned)
            rows = _finding_rows(verdict)
            record.findings_truncated = len(rows) > MAX_STORED_FINDINGS
            record.findings = rows[:MAX_STORED_FINDINGS]
            record.status = (
                SCAN_STATUS_CANCELLED if self._should_cancel() else SCAN_STATUS_COMPLETED
            )
            if record.status == SCAN_STATUS_CANCELLED:
                record.error = "Scan cancelled by the user"
        except scanner.ScanCancelled as ex:
            record.status = SCAN_STATUS_CANCELLED
            record.error = str(ex) or "Scan cancelled by the user"
        except Exception as ex:  # pragma: no cover - defensive
            logger.exception("CISO scan %s failed: %s", record.scan_id, ex)
            record.status = SCAN_STATUS_FAILED
            record.error = f"{type(ex).__name__}: {ex}"
        finally:
            self._done.set()
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=2)
            record.finished_at = _iso()
            record.duration_seconds = round(time.monotonic() - started, 3)
            if record.status == SCAN_STATUS_COMPLETED:
                record.progress_done = record.progress_total or record.progress_done
            record.heartbeat_at = record.finished_at
            try:
                self.store.save(record)
                self.store.clear_cancel(record.scan_id)
                self.store.prune()
            except Exception as ex:  # pragma: no cover
                logger.warning("CISO: could not persist scan %s: %s", record.scan_id, ex)

    def _release_lock(self) -> None:
        """Release the project scan lock."""
        handle = self._lock_handle
        self._lock_handle = None
        if handle in (None, True):
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:  # pragma: no cover - closing releases the lock anyway
            pass
        try:
            handle.close()
        except Exception:  # pragma: no cover
            pass

    def _expand_paths(self, policy: CisoPolicy) -> List[str]:
        """Expand the explicit paths of a ``paths`` scan.

        A path may name a folder (the common case in the UI). The scanner reads
        files, not folders, so directories are expanded through the same bounded
        walker used by a project scan, and the result is capped by ``max_files``.
        """
        targets: List[str] = []
        for relative in self.record.paths:
            absolute = os.path.join(self.project_path, relative)
            if os.path.isdir(absolute):
                targets.extend(
                    scanner.iter_project_files(
                        absolute,
                        max_files=max(0, self.record.max_files - len(targets)),
                        policy=policy,
                    )
                )
            elif os.path.isfile(absolute):
                targets.append(relative)
            if len(targets) >= self.record.max_files:
                break
        return targets[: self.record.max_files]

    def _execute(self, policy: CisoPolicy) -> tuple:
        """Run the scan for the configured scope.

        Returns:
            tuple: ``(verdict, files_scanned)``.
        """
        record = self.record
        if record.scope in (SCOPE_PROJECT, SCOPE_PATHS):
            domain = SecurityDomain.FILE_READ
            paths = self._expand_paths(policy) if record.scope == SCOPE_PATHS else None
            if record.scope == SCOPE_PATHS:
                record.progress_total = len(paths or [])
                if not paths:
                    clean = ciso_policy.evaluate(
                        [], domain=domain, policy=policy, rules_evaluated=0
                    )
                    clean.summary = "No file found in the selected paths"
                    return clean, 0
            verdict = scanner.scan_project(
                self.project_path,
                max_files=record.max_files,
                domain=domain,
                policy=policy,
                paths=paths,
                progress=self._progress,
                should_cancel=self._should_cancel,
            )
            return verdict, record.progress_done

        domain = SecurityDomain.GIT_COMMIT
        diff = _git_diff(self.project_path, record.scope == SCOPE_STAGED, record.scope)
        if self._should_cancel():
            raise scanner.ScanCancelled("cancelled before the diff analysis")
        verdicts = [
            scanner.scan_diff(diff, domain=domain, policy=policy)
        ] if diff.strip() else []

        untracked_scanned = 0
        if record.scope == SCOPE_CHANGES:
            untracked = _untracked_files(self.project_path, record.max_files)
            record.progress_total = len(untracked)
            if untracked:
                untracked_verdict = scanner.scan_project(
                    self.project_path,
                    max_files=record.max_files,
                    domain=domain,
                    policy=policy,
                    paths=untracked,
                    progress=self._progress,
                    should_cancel=self._should_cancel,
                )
                verdicts.append(untracked_verdict)
                untracked_scanned = record.progress_done

        if not verdicts:
            clean = ciso_policy.evaluate([], domain=domain, policy=policy, rules_evaluated=0)
            clean.summary = "No changes to analyze"
            return clean, untracked_scanned
        merged = _merge_verdicts(
            verdicts, domain=domain, policy=policy, files_scanned=untracked_scanned
        )
        return merged, untracked_scanned


def _run_scan_process(codx_path: str, project_path: str, record_payload: Dict[str, Any]) -> None:
    """Child-process entry point: execute one scan.

    Args:
        codx_path: Project control plane (where the record lives).
        project_path: Checkout to analyse.
        record_payload: Serializable snapshot of the queued record.
    """
    settings = SimpleNamespace(codx_path=codx_path, abs_project_path=project_path)
    store = ScanStore(settings)
    record = ScanRecord.from_dict(record_payload)
    runner = _ScanRunner(settings, record, store)
    runner._run()


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------
def start_scan(
    settings: Any,
    *,
    scope: str = SCOPE_PROJECT,
    paths: Optional[Iterable[str]] = None,
    max_files: int = DEFAULT_SCAN_FILES,
    min_severity: Optional[str] = None,
) -> Dict[str, Any]:
    """Create and start a security scan.

    Args:
        settings: Project settings (``codx_path`` + ``abs_project_path``).
        scope: ``project``, ``changes``, ``staged`` or ``paths``.
        paths: Project-relative files or folders for the ``paths`` scope.
        max_files: File cap of the scan.
        min_severity: Optional severity floor recorded with the scan (the view
            also filters findings by it).

    Returns:
        dict: The queued scan record.

    Raises:
        CisoServiceError: Invalid scope/paths or a missing project path.
        ScanBusyError: A scan is already running for the project.
    """
    scope = str(scope or SCOPE_PROJECT).strip().lower()
    if scope not in SCAN_SCOPES:
        raise CisoServiceError(
            f"Unknown scan scope '{scope}'. Use one of: {', '.join(SCAN_SCOPES)}"
        )
    project_path = _project_path(settings)
    normalised = _normalise_paths(project_path, paths)
    if scope == SCOPE_PATHS and not normalised:
        raise CisoServiceError("The 'paths' scope requires at least one path")
    floor = parse_severity(min_severity) if min_severity else None
    if min_severity and floor is None:
        raise CisoServiceError(f"Unknown severity '{min_severity}'")
    max_files = max(1, min(_as_int(max_files, DEFAULT_SCAN_FILES), MAX_SCAN_FILES))

    store = ScanStore(settings)
    store.reconcile()

    with _RUNNERS_LOCK:
        policy = ciso_policy.resolve_policy(settings)
        record = ScanRecord(
            scan_id=_new_scan_id(),
            status=SCAN_STATUS_QUEUED,
            scope=scope,
            paths=normalised,
            max_files=max_files,
            min_severity=floor.value if floor else None,
            policy=policy.to_dict(),
            policy_enabled=bool(policy.enabled),
            project_name=_project_name(settings),
            project_path=project_path,
        )
        store.save(record)
        runner = _ScanRunner(settings, record, store)
        # Register before starting: a very fast scan can finish (and pop itself)
        # before ``start()`` returns.
        _RUNNERS[record.scan_id] = runner
        try:
            runner.start()
        except ScanBusyError:
            _RUNNERS.pop(record.scan_id, None)
            store.delete(record.scan_id)
            raise
    return record.to_dict(include_findings=False)


def cancel_scan(settings: Any, scan_id: str) -> Dict[str, Any]:
    """Request cancellation of a scan.

    Args:
        settings: Project settings.
        scan_id: Scan to cancel.

    Returns:
        dict: The updated record.

    Raises:
        ScanNotFound: When the record does not exist.
    """
    store = ScanStore(settings)
    record = store.load(scan_id)
    if record.status in (SCAN_STATUS_COMPLETED, SCAN_STATUS_FAILED, SCAN_STATUS_CANCELLED):
        return record.to_dict(include_findings=False)
    record.cancel_requested = True
    store.request_cancel(scan_id)
    with _RUNNERS_LOCK:
        runner = _RUNNERS.get(scan_id)
    if runner is None:
        # The owning worker persists its own status; this saved flag is what the
        # polling UI reads until it does.
        store.save(record)
    if runner is not None:
        runner.cancel()
    elif not _pid_alive(record.pid):
        # No runner here and no live owner: the record is stale. Mark it
        # cancelled; if another worker owns the scan it will observe the marker
        # and persist the cancellation itself.
        record.status = SCAN_STATUS_CANCELLED
        record.finished_at = _iso()
        record.error = record.error or "Scan cancelled by the user"
        store.save(record)
    return store.load(scan_id).to_dict(include_findings=False)


def delete_scan(settings: Any, scan_id: str) -> bool:
    """Delete a scan record (a running scan is cancelled first)."""
    store = ScanStore(settings)
    record = store.load(scan_id)
    if record.status in (SCAN_STATUS_QUEUED, SCAN_STATUS_RUNNING):
        try:
            cancel_scan(settings, scan_id)
        except CisoServiceError:
            pass
    return store.delete(scan_id)


def list_scans(settings: Any, *, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """Return the scan history summaries (newest first)."""
    store = ScanStore(settings)
    store.reconcile()
    records = store.list(limit=limit, offset=offset)
    return {
        "scans": [record.to_dict(include_findings=False) for record in records],
        "total": store.count(),
        "limit": limit,
        "offset": offset,
    }


def get_scan(settings: Any, scan_id: str, include_findings: bool = True) -> Dict[str, Any]:
    """Return one scan record."""
    store = ScanStore(settings)
    store.reconcile()
    return store.load(scan_id).to_dict(include_findings=include_findings)


def scan_findings(
    settings: Any,
    scan_id: str,
    *,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    query: str = "",
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return a filtered, paged view of the findings of one scan.

    Filtering happens here (not in the browser) so the payload stays bounded:
    a project scan can produce thousands of findings and the UI only needs one
    page plus the aggregate counters.

    Raises:
        CisoServiceError: When the severity/category filter is invalid.
    """
    floor = parse_severity(severity) if severity else None
    if severity and floor is None:
        raise CisoServiceError(f"Unknown severity '{severity}'")
    wanted_category = None
    if category:
        try:
            wanted_category = SecurityCategory(str(category).strip().lower())
        except ValueError as ex:
            raise CisoServiceError(f"Unknown category '{category}'") from ex

    store = ScanStore(settings)
    record = store.load(scan_id)
    needle = str(query or "").strip().lower()
    findings = list(record.findings or [])
    counts = {entry.value: 0 for entry in SecuritySeverity}
    categories = {entry.value: 0 for entry in SecurityCategory}
    for finding in findings:
        counts[finding.get("severity", "info")] = (
            counts.get(finding.get("severity", "info"), 0) + 1
        )
        categories[finding.get("category", "policy")] = (
            categories.get(finding.get("category", "policy"), 0) + 1
        )

    filtered = []
    for finding in findings:
        finding_severity = parse_severity(finding.get("severity"))
        if floor is not None and severity_rank(finding_severity) < severity_rank(floor):
            continue
        if wanted_category is not None and finding.get("category") != wanted_category.value:
            continue
        if needle:
            haystack = " ".join(
                str(finding.get(key) or "")
                for key in ("rule_id", "title", "path", "reason", "evidence", "remediation")
            ).lower()
            if needle not in haystack:
                continue
        filtered.append(finding)
    filtered.sort(
        key=lambda item: (
            -severity_rank(parse_severity(item.get("severity"))),
            str(item.get("path") or ""),
            int(item.get("line") or 0),
        )
    )
    total = len(filtered)
    limit = max(1, min(_as_int(limit, 100), 500))
    offset = max(0, _as_int(offset, 0))
    return {
        "scan": record.to_dict(include_findings=False),
        "findings": filtered[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
        "counts": counts,
        "categories": categories,
        "rules": sorted({finding.get("rule_id") for finding in findings if finding.get("rule_id")}),
    }


def policy_state(settings: Any) -> Dict[str, Any]:
    """Return the effective policy, the stored overrides and the UI options."""
    policy = ciso_policy.resolve_policy(settings)
    stored = _stored_policy(settings)
    return {
        "policy": policy.to_dict(),
        "stored": stored,
        "defaults": CisoPolicy().to_dict(),
        "ignored_rules": list(policy.ignore_rules or []),
        "mandatory_rules": list(ciso_models.MANDATORY_RULE_PREFIXES),
        "silenced_mandatory_rules": policy.silenced_mandatory_rules(),
        "rules": ciso_rules.counts(policy),
        "options": {
            "severities": [severity.value for severity in SecuritySeverity],
            "categories": [category.value for category in SecurityCategory],
            "domains": [domain.value for domain in SecurityDomain],
            "actions": [action.value for action in SecurityAction],
        },
    }



def _validate_policy_changes(changes: Dict[str, Any]) -> None:
    """Reject enum values the policy model would silently drop.

    ``policy_from_dict`` is tolerant on purpose (a settings file written for a
    newer version must not break an old install), but the UI must not be told
    that an invalid value was applied. Validation therefore happens here, once,
    before the merge.

    Raises:
        CisoServiceError: When an enum/threshold value is not recognised.
    """
    for field_name, enum_cls in (
        ("block_severities", SecuritySeverity),
        ("block_categories", SecurityCategory),
        ("strict_domains", SecurityDomain),
        ("enabled_domains", SecurityDomain),
    ):
        value = changes.get(field_name)
        if value is None:
            continue
        if not isinstance(value, list):
            raise CisoServiceError(f"'{field_name}' must be a list")
        invalid = []
        for item in value:
            try:
                enum_cls(item)
            except (TypeError, ValueError):
                invalid.append(str(item))
        if invalid:
            raise CisoServiceError(
                f"Invalid '{field_name}' value(s): {', '.join(invalid)}"
            )
    for field_name in ("block_above", "approval_above", "warn_above"):
        value = changes.get(field_name)
        if value is not None and parse_severity(value) is None:
            raise CisoServiceError(f"Invalid severity for '{field_name}': {value}")
    if "secrets" in changes and changes["secrets"] is not None:
        if not isinstance(changes["secrets"], dict):
            raise CisoServiceError("'secrets' must be an object")
    for field_name in ("enabled", "redact_secrets", "fail_closed"):
        if field_name in changes and changes[field_name] is not None:
            if not isinstance(changes[field_name], bool):
                raise CisoServiceError(f"'{field_name}' must be a boolean")
    for field_name in ("ignore_paths", "ignore_rules", "allow_secret_files"):
        if field_name in changes and changes[field_name] is not None:
            if not isinstance(changes[field_name], list):
                raise CisoServiceError(f"'{field_name}' must be a list")


def _stored_policy(settings: Any) -> Dict[str, Any]:
    """Return the project's raw ``ciso_policy`` block (empty when unset)."""
    direct = getattr(settings, "ciso_policy", None)
    if isinstance(direct, dict):
        return dict(direct)
    # Compatibility with call sites/test doubles that keep the block inside a
    # generic ``settings`` dict.
    configured = getattr(settings, "settings", None)
    if isinstance(configured, dict):
        candidate = configured.get(ciso_policy.POLICY_SETTINGS_KEY)
        if isinstance(candidate, dict):
            return dict(candidate)
    return {}


def _project_settings_path(settings: Any) -> str:
    """Return the ``project.json`` path of the project."""
    getter = getattr(settings, "get_project_settings_file", None)
    if callable(getter):
        try:
            path = getter()
        except Exception:
            path = None
        if path:
            return str(path)
    return os.path.join(_codx_path(settings), "project.json")


def _persist_policy(settings: Any, policy_payload: Dict[str, Any]) -> None:
    """Persist the validated policy into ``project.json`` atomically.

    The settings object is also updated in memory so the next gate in this
    process resolves the new policy without a restart. The full
    ``settings.save_project()`` is deliberately avoided: it rewrites every
    field of the model and clears ``abs_project_path`` in some layouts.
    """
    path = _project_settings_path(settings)
    data: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError) as ex:
            raise CisoServiceError(f"Cannot read project settings: {ex}") from ex
    data[ciso_policy.POLICY_SETTINGS_KEY] = policy_payload
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
    try:
        settings.ciso_policy = policy_payload
    except Exception:  # pragma: no cover - read-only test doubles
        pass
    configured = getattr(settings, "settings", None)
    if isinstance(configured, dict):
        configured[ciso_policy.POLICY_SETTINGS_KEY] = policy_payload


def update_policy(settings: Any, changes: Dict[str, Any], *, reset: bool = False) -> Dict[str, Any]:
    """Merge *changes* into the project policy, validate and persist it.

    Args:
        settings: Project settings.
        changes: Partial policy dictionary (same keys as ``CisoPolicy``).
        reset: Ignore the stored overrides and start from the defaults plus
            *changes*.

    Returns:
        dict: The new :func:`policy_state`.

    Raises:
        CisoServiceError: When the changes are not a dictionary or the settings
            file cannot be written.
    """
    if not isinstance(changes, dict):
        raise CisoServiceError("The policy payload must be a JSON object")
    allowed = set(CisoPolicy.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise CisoServiceError(f"Unknown policy field(s): {', '.join(unknown)}")
    _validate_policy_changes(changes)
    stored = {} if reset else _stored_policy(settings)
    merged = dict(stored)
    merged.update(changes)
    validated = ciso_policy.policy_from_dict(merged)
    _persist_policy(settings, validated.to_dict())
    return policy_state(settings)


def reset_policy(settings: Any) -> Dict[str, Any]:
    """Remove the project policy overrides (back to the defaults)."""
    path = _project_settings_path(settings)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as ex:
            raise CisoServiceError(f"Cannot read project settings: {ex}") from ex
        if isinstance(data, dict):
            data.pop(ciso_policy.POLICY_SETTINGS_KEY, None)
            temporary = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
            try:
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                try:
                    if os.path.exists(temporary):
                        os.remove(temporary)
                except OSError:
                    pass
    try:
        settings.ciso_policy = None
    except Exception:  # pragma: no cover - read-only test doubles
        pass
    configured = getattr(settings, "settings", None)
    if isinstance(configured, dict):
        configured.pop(ciso_policy.POLICY_SETTINGS_KEY, None)
    return policy_state(settings)


def set_rule_enabled(settings: Any, rule_id: str, enabled: bool) -> Dict[str, Any]:
    """Enable or silence one catalogue rule.

    Silencing a rule adds it to ``ignore_rules``; enabling removes it. Unknown
    rule ids are rejected so a typo cannot silently silence nothing.

    Raises:
        UnknownRuleError: When *rule_id* is not in the detector catalogue.
    """
    descriptor = ciso_rules.get_rule(rule_id)
    if descriptor is None:
        raise UnknownRuleError(f"Unknown rule: {rule_id}")
    policy = ciso_policy.resolve_policy(settings)
    ignored = [rid for rid in (policy.ignore_rules or []) if rid != rule_id]
    if not enabled:
        ignored.append(rule_id)
    stored = _stored_policy(settings)
    merged = dict(stored)
    merged["ignore_rules"] = sorted(set(ignored))
    validated = ciso_policy.policy_from_dict(merged)
    _persist_policy(settings, validated.to_dict())
    return {
        "rule": descriptor.to_dict(validated),
        "protected": str(rule_id).startswith(ciso_models.MANDATORY_RULE_PREFIXES),
        "policy": policy_state(settings),
    }


def rule_catalog(settings: Any, *, include_disabled: bool = True) -> Dict[str, Any]:
    """Return the detector rule catalogue with the policy state."""
    policy = ciso_policy.resolve_policy(settings)
    entries = ciso_rules.catalog(policy)
    if not include_disabled:
        entries = [entry for entry in entries if entry["enabled"]]
    return {
        "rules": entries,
        "total": len(entries),
        "counts": ciso_rules.counts(policy),
        "ignored_rules": list(policy.ignore_rules or []),
    }


# ---------------------------------------------------------------------------
# Overview: posture + gates + secrets + repository hygiene
# ---------------------------------------------------------------------------
def _read_gate_entries(
    codx_path: str,
    *,
    max_entries: int = AUDIT_READ_LIMIT,
    max_bytes: int = 4 * 1024 * 1024,
) -> List[Dict[str, Any]]:
    """Read the newest ``ciso_verdict`` entries from the audit journal.

    ``HarnessAuditLog.read`` scans the whole file because it supports arbitrary
    filters. The CISO overview polls, and a long-lived project can have a
    multi-megabyte journal; reading a bounded tail keeps the request cheap
    without changing the journal format.
    """
    from codx.junior.harness.audit import audit_path

    path = audit_path(codx_path)
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    try:
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                chunk = handle.read()
                lines = chunk.split(b"\n")[1:]  # drop the partial first line
            else:
                lines = handle.read().split(b"\n")
    except OSError:
        return []
    entries: List[Dict[str, Any]] = []
    for raw in reversed(lines):
        if len(entries) >= max_entries:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(entry, dict) and entry.get("event") == "ciso_verdict":
            entries.append(entry)
    return entries


def _gate_activity(settings: Any, limit: int = ACTIVITY_DEFAULT_LIMIT) -> Dict[str, Any]:
    """Aggregate the ``ciso_verdict`` entries of the harness audit journal."""
    codx_path = _codx_path(settings)
    entries = _read_gate_entries(codx_path)
    cutoff = _utcnow() - timedelta(hours=GATE_WINDOW_HOURS)
    actions = {action.value: 0 for action in SecurityAction}
    severity_counts = {severity.value: 0 for severity in SecuritySeverity}
    domains: Dict[str, int] = {}
    rules: Dict[str, int] = {}
    blocked = 0
    total = 0
    last_event_at = None
    recent: List[Dict[str, Any]] = []
    for entry in entries:
        timestamp = _parse_iso(entry.get("ts"))
        if timestamp and timestamp < cutoff:
            continue
        total += 1
        action = str(entry.get("action") or "allow")
        actions[action] = actions.get(action, 0) + 1
        if entry.get("blocked"):
            blocked += 1
        severity = entry.get("highest_severity")
        if severity in severity_counts:
            severity_counts[severity] += 1
        domain = str(entry.get("domain") or "")
        if domain:
            domains[domain] = domains.get(domain, 0) + 1
        for finding in entry.get("findings") or []:
            rule = str(finding.get("rule") or "")
            if rule:
                rules[rule] = rules.get(rule, 0) + 1
        if last_event_at is None:
            last_event_at = entry.get("ts")
        if len(recent) < limit:
            recent.append(
                {
                    "ts": entry.get("ts"),
                    "action": action,
                    "domain": domain,
                    "subject": entry.get("subject"),
                    "blocked": bool(entry.get("blocked")),
                    "highest_severity": severity,
                    "risk_score": entry.get("risk_score"),
                    "findings": entry.get("findings") or [],
                }
            )
    return {
        "window_hours": GATE_WINDOW_HOURS,
        "total": total,
        "blocked": blocked,
        "actions": actions,
        "severities": severity_counts,
        "domains": dict(sorted(domains.items())),
        "top_rules": [
            {"rule_id": rule, "count": count}
            for rule, count in sorted(rules.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "last_event_at": last_event_at,
        "recent": recent,
    }


def _secrets_summary(settings: Any) -> Dict[str, Any]:
    """Return a value-free inventory of the project secrets."""
    try:
        entries = ciso_vault.dashboard_entries(settings)
    except Exception as ex:  # pragma: no cover - a broken vault is reported, not fatal
        logger.warning("CISO overview: could not read the secret inventory: %s", ex)
        return {"available": False, "total": 0, "ready": 0, "pending": 0, "error": str(ex)}
    statuses: Dict[str, int] = {}
    scopes: Dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        scope = str(entry.get("scope") or "project")
        scopes[scope] = scopes.get(scope, 0) + 1
    return {
        "available": True,
        "total": len(entries),
        "ready": statuses.get("ready", 0),
        "pending": statuses.get("pending", 0),
        "statuses": statuses,
        "scopes": scopes,
    }



def _git_ignored_paths(project_path: str, candidates: List[str]) -> tuple:
    """Ask git which of *candidates* are ignored.

    ``git check-ignore`` exits with 1 when none of the paths match, which is a
    valid answer rather than an error; only an exit code above 1 is reported as
    a failure by this helper.

    Returns:
        tuple: ``(matched_paths, error)``.
    """
    if not candidates:
        return set(), ""
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-pager",
                "check-ignore",
                "--stdin",
            ],
            cwd=project_path,
            input="\n".join(candidates) + "\n",
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        return set(), str(ex)
    if completed.returncode not in (0, 1):
        return set(), (completed.stderr or "git check-ignore failed").strip()
    return {
        line.strip() for line in (completed.stdout or "").splitlines() if line.strip()
    }, ""


def _repo_hygiene(settings: Any) -> Dict[str, Any]:
    """Check that the generated data plane is ignored and untracked.

    This is the read-only half of ``api/tests/security/ciso_repo_check.py``:
    the view must be able to show the same truth in one request without shelling
    out for every entry.
    """
    project_path = _project_path(settings)
    result = {
        "checked": False,
        "tracked_data_plane": [],
        "ignore_rules_ok": None,
        "missing_rules": [],
        "errors": [],
    }
    ok, stdout, stderr = _git(project_path, ["rev-parse", "--is-inside-work-tree"])
    if not ok or "true" not in stdout:
        result["errors"].append(stderr.strip() or "Not a git repository")
        return result
    result["checked"] = True

    ignore_text = ""
    ignore_paths = [os.path.join(project_path, ".gitignore")]
    ok_root, root_out, _ = _git(project_path, ["rev-parse", "--show-toplevel"])
    if ok_root and root_out.strip():
        root_ignore = os.path.join(root_out.strip(), ".gitignore")
        if root_ignore not in ignore_paths:
            ignore_paths.insert(0, root_ignore)
    for path in ignore_paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                ignore_text += "\n" + handle.read()
        except OSError:
            continue
    missing = [
        entry
        for entry in DATA_PLANE_ENTRIES
        if f".codx/{entry}" not in ignore_text
    ]
    # A broad ``.codx/`` rule covers every entry; when the text check is not
    # enough, ask git itself (the authority on whether a path is ignored).
    if missing:
        candidates = []
        for entry in missing:
            candidates.append(f".codx/{entry}")
            if "." not in entry:
                candidates.append(f".codx/{entry}/probe")
        matched, ignore_error = _git_ignored_paths(project_path, candidates)
        if ignore_error:
            result["errors"].append(ignore_error)
        else:
            missing = [
                entry
                for entry in missing
                if f".codx/{entry}" not in matched
                and f".codx/{entry}/probe" not in matched
            ]
    result["ignore_rules_ok"] = not missing
    result["missing_rules"] = missing

    ok_files, files_out, files_err = _git(project_path, ["ls-files", "-z", "--", ".codx"])
    if not ok_files:
        result["errors"].append(files_err.strip() or "git ls-files failed")
        return result
    tracked = [path for path in files_out.split("\x00") if path]
    offenders = [
        path
        for path in tracked
        if any(
            path == f".codx/{entry}" or path.startswith(f".codx/{entry.rstrip('/')}/")
            for entry in DATA_PLANE_ENTRIES
        )
    ]
    result["tracked_data_plane"] = offenders[:50]
    result["tracked_data_plane_count"] = len(offenders)
    return result


def _trend(records: List[ScanRecord]) -> List[Dict[str, Any]]:
    """Return the last completed scans as a score trend."""
    points = []
    for record in reversed(records):
        if record.status != SCAN_STATUS_COMPLETED or not record.summary:
            continue
        posture = _posture_from_summary(record.summary)
        points.append(
            {
                "scan_id": record.scan_id,
                "created_at": record.created_at,
                "score": posture["score"],
                "risk_score": posture["risk_score"],
                "findings": posture["total_findings"],
                "highest_severity": posture["highest_severity"],
            }
        )
    return points[-12:]


def overview(settings: Any) -> Dict[str, Any]:
    """Return the security posture shown by the CISO overview.

    The payload is deliberately self-contained: policy summary, latest scan and
    trend, gate activity, secret inventory and repository hygiene. The UI can
    render the dashboard with this single request.
    """
    store = ScanStore(settings)
    store.reconcile()
    records = store.list(limit=MAX_STORED_SCANS)
    policy = ciso_policy.resolve_policy(settings)
    completed = [record for record in records if record.status == SCAN_STATUS_COMPLETED]
    latest = records[0] if records else None
    latest_completed = completed[0] if completed else None
    running = [
        record.to_dict(include_findings=False)
        for record in records
        if record.status in (SCAN_STATUS_QUEUED, SCAN_STATUS_RUNNING)
    ]
    posture = _posture_from_summary(
        latest_completed.summary if latest_completed else None
    )
    return {
        "generated_at": _iso(),
        "project": {
            "name": _project_name(settings),
            "path": _project_path(settings),
            "codx_path": _codx_path(settings),
        },
        "posture": posture,
        "trend": _trend(records),
        "latest_scan": latest.to_dict(include_findings=False) if latest else None,
        "latest_completed_scan": (
            latest_completed.to_dict(include_findings=False) if latest_completed else None
        ),
        "running_scans": running,
        "scans": {"total": store.count(), "running": len(running)},
        "policy": {
            "enabled": policy.enabled,
            "block_above": policy.block_above.value,
            "approval_above": policy.approval_above.value,
            "warn_above": policy.warn_above.value,
            "redact_secrets": policy.redact_secrets,
            "fail_closed": policy.fail_closed,
            "ignore_rules": list(policy.ignore_rules or []),
            "ignore_paths": list(policy.ignore_paths or []),
            "mandatory_rules": list(ciso_models.MANDATORY_RULE_PREFIXES),
            "silenced_mandatory_rules": policy.silenced_mandatory_rules(),
            "block_categories": [category.value for category in policy.block_categories],
            "strict_domains": [domain.value for domain in policy.strict_domains],
            "rules": ciso_rules.counts(policy),
        },
        "gates": _gate_activity(settings),
        "secrets": _secrets_summary(settings),
        "repo": _repo_hygiene(settings),
    }


def activity(settings: Any, *, limit: int = ACTIVITY_DEFAULT_LIMIT) -> Dict[str, Any]:
    """Return the CISO gate journal (newest first) plus aggregate stats."""
    gate_activity = _gate_activity(settings, limit=limit)
    return {
        "entries": gate_activity["recent"],
        "stats": {key: gate_activity[key] for key in (
            "window_hours", "total", "blocked", "actions", "severities",
            "domains", "top_rules", "last_event_at",
        )},
    }


__all__ = [
    "CisoServiceError",
    "MAX_STORED_SCANS",
    "SCAN_SCOPES",
    "SCAN_STATUSES",
    "SCOPE_CHANGES",
    "SCOPE_PATHS",
    "SCOPE_PROJECT",
    "SCOPE_STAGED",
    "ScanBusyError",
    "ScanNotFound",
    "ScanRecord",
    "ScanStore",
    "UnknownRuleError",
    "activity",
    "cancel_scan",
    "delete_scan",
    "get_scan",
    "list_scans",
    "overview",
    "policy_state",
    "reset_policy",
    "rule_catalog",
    "scan_findings",
    "scans_dir",
    "set_rule_enabled",
    "start_scan",
    "update_policy",
]
