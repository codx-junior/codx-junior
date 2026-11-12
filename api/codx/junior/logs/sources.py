"""
Discovery and bounded reading of application log files for the Log Explorer.

The explorer reads the very same folder the legacy ``/api/system/logs``
endpoints list (``CODX_JUNIOR_API_LOGS``, exported as
:data:`codx.junior.globals.LOGS_FOLDER`), but it never loads a whole file:
every read is a **bounded byte window taken from the END of the file**, so a
multi-hundred-MB log costs the same as a small one.

Line counts are estimated from a sample of the tail and cached per
``(path, size, mtime)``, which keeps :func:`list_sources` cheap even when the
folder holds dozens of growing files.

Diagram:
--------
```mermaid
flowchart LR
    folder["LOGS_FOLDER<br/>CODX_JUNIOR_API_LOGS"] --> list[list_sources]
    list --> cache[("(path, size, mtime)<br/>line stats cache")]
    list --> model[LogSource]
    tail[read_tail / read_range] -->|seek + chunked read| window[bounded byte window]
    window --> service[logs/service.py]
```
"""

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from codx.junior.globals import LOGS_FOLDER

logger = logging.getLogger(__name__)

#: Environment variable that overrides the folder listed by ``/api/system/logs``.
LOGS_FOLDER_ENV = "CODX_JUNIOR_API_LOGS"

#: Bytes sampled from the end of a file to estimate its line count.
ESTIMATE_SAMPLE_BYTES = 256 * 1024

#: Chunk size used by the bounded readers: the memory ceiling of a single read.
READ_CHUNK_BYTES = 1024 * 1024

#: Default byte window returned by :func:`read_tail`.
DEFAULT_TAIL_BYTES = 8 * 1024 * 1024

#: Suffixes of rotating archives: never parsed as text.
BINARY_SUFFIXES = (".gz", ".bz2", ".xz", ".zip", ".7z", ".zst", ".lz4", ".br")

#: Source kinds reported in :attr:`LogSource.kind`.
KIND_JSONL = "jsonl"
KIND_LOG = "log"
KIND_TEXT = "text"
#: A running docker container, read with ``docker logs`` instead of a file.
KIND_DOCKER = "docker"

#: Maximum number of sources returned by :func:`list_sources`.
MAX_SOURCES = 500

#: Environment variable that overrides the container list cache TTL (seconds).
CONTAINERS_TTL_ENV = "CODX_JUNIOR_LOG_EXPLORER_CONTAINERS_TTL"

#: Default TTL of the docker container list cache, in seconds.
DEFAULT_CONTAINERS_TTL = 5.0

#: Timeout of a ``docker`` command, in seconds.
DOCKER_TIMEOUT_SECONDS = 15

#: Lines requested from ``docker logs`` for one bounded window.
DEFAULT_CONTAINER_TAIL_LINES = 5000


class LogSource(BaseModel):
    """
    A readable application log file.

    ``id`` is the value the explorer expects in its ``source`` query
    parameter: it is the plain file name, which also makes the ids stable
    across API restarts and easy to read in the UI.

    Attributes:
        id:               Source identifier (file name), used as ``source=``.
        name:             Display name (same as ``id``).
        path:             Absolute path of the file on disk.
        size_bytes:       File size in bytes.
        mtime:            Last modification time, epoch seconds.
        kind:             ``jsonl``, ``log`` or ``text`` (from the suffix).
        entries_estimate: Cheap estimate of the number of lines in the file.
    """

    id: str = Field(description="Source identifier (file name), used as the 'source' param.")
    name: str = Field(description="Display name of the source.")
    path: str = Field(description="Absolute path of the log file.")
    size_bytes: int = Field(default=0, description="File size in bytes.")
    mtime: float = Field(default=0.0, description="Last modification time, epoch seconds.")
    kind: str = Field(default=KIND_LOG, description="Source kind: jsonl | log | text.")
    entries_estimate: int = Field(
        default=0,
        description="Estimated number of lines (sampled from the file tail).",
    )


@dataclass(frozen=True)
class TailRead:
    """
    Result of a bounded tail read.

    Attributes:
        lines:        Decoded lines, oldest first (a leading partial line is dropped).
        start_offset: Byte offset where ``lines`` start in the file.
        bytes_read:   Number of bytes actually read.
        total_bytes:  Size of the file when it was read.
        truncated:    ``True`` when the beginning of the file was skipped.
    """

    lines: List[str]
    start_offset: int
    bytes_read: int
    total_bytes: int
    truncated: bool


@dataclass(frozen=True)
class _FileStats:
    """Line statistics of a file, tagged with the ``(size, mtime)`` they describe."""

    size: int
    mtime: float
    lines: int
    avg_line_bytes: float


_stats_cache: Dict[str, _FileStats] = {}
_stats_lock = threading.Lock()


# ── Folder resolution ─────────────────────────────────────────────────────────


def resolve_logs_folder() -> str:
    """
    Return the folder the legacy ``/api/system/logs`` endpoints list.

    The environment variable is re-read on every call (instead of relying only
    on the import-time :data:`codx.junior.globals.LOGS_FOLDER` constant) so the
    folder can be redirected at runtime and in tests.

    Returns:
        The log folder path (``""`` when nothing is configured).
    """
    return os.environ.get(LOGS_FOLDER_ENV) or LOGS_FOLDER or ""


# ── Bounded reading ───────────────────────────────────────────────────────────


def read_tail_bytes(path: str, max_bytes: int) -> Tuple[bytes, int]:
    """
    Read at most *max_bytes* from the END of *path*, chunk by chunk.

    Args:
        path:      File to read.
        max_bytes: Maximum number of bytes to read (``<= 0`` reads nothing).

    Returns:
        ``(data, start_offset)`` — the bytes read and the file offset where the
        returned data starts. ``start_offset > 0`` means the head of the file
        was skipped.

    Raises:
        OSError: When the file cannot be stat'ed or read.
    """
    size = os.path.getsize(path)
    limit = max(0, int(max_bytes))
    start = max(0, size - limit)
    remaining = size - start
    parts: List[bytes] = []
    with open(path, "rb") as file_obj:
        file_obj.seek(start)
        while remaining > 0:
            chunk = file_obj.read(min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
    return b"".join(parts), start


def read_range(path: str, start_offset: int, length: int) -> bytes:
    """
    Read *length* bytes starting at *start_offset*, chunk by chunk.

    Args:
        path:         File to read.
        start_offset: Byte offset to start from (negatives are clamped to 0).
        length:       Number of bytes to read.

    Returns:
        The bytes read (fewer than *length* at the end of the file).

    Raises:
        OSError: When the file cannot be read.
    """
    start = max(0, int(start_offset))
    remaining = max(0, int(length))
    parts: List[bytes] = []
    with open(path, "rb") as file_obj:
        file_obj.seek(start)
        while remaining > 0:
            chunk = file_obj.read(min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
    return b"".join(parts)


def split_log_lines(data: bytes, drop_first_partial: bool = False) -> List[str]:
    """
    Decode a byte window into lines, dropping the trailing newline artefact.

    Args:
        data:               Raw bytes of the window.
        drop_first_partial: Drop everything up to the first newline — the first
                            line of a window that does not start at offset 0 is
                            usually cut in half.

    Returns:
        The decoded lines (no line terminators), oldest first.
    """
    if not data:
        return []
    text = data.decode("utf-8", errors="replace")
    if drop_first_partial:
        if "\n" not in text:
            return []
        text = text.split("\n", 1)[1]
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_tail(path: str, max_bytes: int = DEFAULT_TAIL_BYTES) -> TailRead:
    """
    Read the last *max_bytes* of *path* as lines.

    Args:
        path:      File to read.
        max_bytes: Size of the byte window taken from the end of the file.

    Returns:
        A :class:`TailRead`; ``truncated`` is ``True`` when the file is bigger
        than the window.
    """
    size = os.path.getsize(path)
    data, start = read_tail_bytes(path, max_bytes)
    return TailRead(
        lines=split_log_lines(data, drop_first_partial=start > 0),
        start_offset=start,
        bytes_read=len(data),
        total_bytes=size,
        truncated=start > 0,
    )


# ── Line statistics (cached) ──────────────────────────────────────────────────


def _file_stats(path: str) -> _FileStats:
    """
    Return the line statistics of *path*, recomputing them only when it changed.

    The estimate comes from a bounded sample of the tail: the average line
    length of the sample is extrapolated to the whole file, so even a huge file
    is never read completely.

    Args:
        path: File to inspect.

    Returns:
        The cached (or freshly computed) :class:`_FileStats`.

    Raises:
        OSError: When the file cannot be stat'ed.
    """
    stat = os.stat(path)
    size, mtime = int(stat.st_size), float(stat.st_mtime)
    cached = _stats_cache.get(path)
    if cached and cached.size == size and cached.mtime == mtime:
        return cached

    if size == 0:
        stats = _FileStats(size=size, mtime=mtime, lines=0, avg_line_bytes=1.0)
    else:
        try:
            data, _start = read_tail_bytes(path, ESTIMATE_SAMPLE_BYTES)
        except OSError as ex:
            logger.debug("Cannot sample %s for a line estimate: %s", path, ex)
            stats = _FileStats(size=size, mtime=mtime, lines=0, avg_line_bytes=1.0)
        else:
            sampled = len(data) or 1
            # The first line of a sampled window may be cut: counting it as a
            # line keeps the average slightly conservative, never zero.
            line_count = max(1, data.count(b"\n"))
            avg_line_bytes = sampled / line_count
            stats = _FileStats(
                size=size,
                mtime=mtime,
                lines=max(0, int(size / avg_line_bytes)),
                avg_line_bytes=avg_line_bytes,
            )

    with _stats_lock:
        _stats_cache[path] = stats
    return stats


def estimate_lines(path: str) -> int:
    """
    Return the estimated number of lines in *path* (cached per file state).

    Args:
        path: File to inspect.

    Returns:
        The estimated line count (``0`` when the file cannot be read).
    """
    try:
        return _file_stats(path).lines
    except OSError as ex:
        logger.debug("Cannot estimate the lines of %s: %s", path, ex)
        return 0


def estimate_line_index(path: str, offset: int) -> int:
    """
    Estimate how many lines precede byte *offset* of *path*.

    Used to keep entry ids meaningful when a query only scans the tail of a
    large file without counting the whole prefix line by line. The result is a
    pure function of the (cached) average line length, so it is stable while
    the file does not change — which is exactly the lifetime of an entry id
    sent to ``/entry`` or ``/context``.

    Args:
        path:   File the offset belongs to.
        offset: Byte offset inside the file.

    Returns:
        The estimated number of preceding lines (``0`` for the file start).
    """
    if offset <= 0:
        return 0
    try:
        stats = _file_stats(path)
    except OSError as ex:
        logger.debug("Cannot estimate the line index of %s@%s: %s", path, offset, ex)
        return 0
    if stats.avg_line_bytes <= 0:
        return 0
    return max(0, int(offset / stats.avg_line_bytes))


# ── Discovery ─────────────────────────────────────────────────────────────────


def _kind_for(name: str) -> str:
    """Return the source kind (``jsonl``/``log``/``text``) for a file name."""
    lowered = name.lower()
    if lowered.endswith(".jsonl"):
        return KIND_JSONL
    if lowered.endswith(".log"):
        return KIND_LOG
    return KIND_TEXT


def _is_readable_file(candidate: Path) -> bool:
    """Return ``True`` for regular, non-archived, non-hidden files."""
    name = candidate.name
    if name.startswith(".") or name.lower().endswith(BINARY_SUFFIXES):
        return False
    try:
        return candidate.is_file()
    except OSError:
        return False


def run_command(command: Sequence[str], timeout: int = DOCKER_TIMEOUT_SECONDS) -> str:
    """
    Run *command* without a shell and return its stdout.

    This is the single indirection used to talk to the docker CLI, so tests can
    monkeypatch :data:`command_runner` instead of faking a subprocess.

    Args:
        command: Arguments of the command (never a shell string).
        timeout: Seconds before the command is killed.

    Returns:
        The decoded standard output.

    Raises:
        OSError:         When the executable cannot be started.
        RuntimeError:    When the command exits with a non-zero status.
        subprocess.TimeoutExpired: When it takes longer than *timeout*.
    """
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or "").strip() or f"exit status {completed.returncode}"
        )
    return completed.stdout


#: Indirection so tests can replace the docker CLI (monkeypatch this attribute).
command_runner = run_command


class _ContainersCache:
    """Short-lived cache of the running container names."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.at: float = 0.0
        self.names: List[str] = []
        self.valid: bool = False
        self.warned: bool = False


_containers = _ContainersCache()


def _containers_ttl() -> float:
    """
    Return the container list cache TTL in seconds.

    Read from ``CODX_JUNIOR_LOG_EXPLORER_CONTAINERS_TTL`` on every call so it can
    be tuned at runtime; negative or invalid values fall back to the default.

    Returns:
        The TTL in seconds (``0`` disables the cache).
    """
    raw = os.environ.get(CONTAINERS_TTL_ENV)
    if not raw:
        return DEFAULT_CONTAINERS_TTL
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, using %ss", CONTAINERS_TTL_ENV, raw, DEFAULT_CONTAINERS_TTL)
        return DEFAULT_CONTAINERS_TTL


def docker_container_names(force: bool = False) -> List[str]:
    """
    Return the names of the running docker containers.

    Uses the same command as the legacy ``/api/system/logs`` endpoint
    (``sudo docker ps --format {{.Names}}``). A missing CLI, an unreachable
    socket or a timeout is logged once and yields an empty list: discovering
    sources must never break the explorer.

    Args:
        force: Bypass the TTL cache and query docker right now.

    Returns:
        Container names, sorted, without duplicates.
    """
    now = time.monotonic()
    ttl = _containers_ttl()
    with _containers.lock:
        if not force and _containers.valid and (now - _containers.at) < ttl:
            return list(_containers.names)

    names: List[str] = []
    try:
        try:
            output = command_runner(["sudo", "docker", "ps", "--format", "{{.Names}}"])
        except (FileNotFoundError, OSError):
            # No sudo in this image: the docker socket may still be reachable.
            output = command_runner(["docker", "ps", "--format", "{{.Names}}"])
        names = sorted({line.strip() for line in output.splitlines() if line.strip()})
        _containers.warned = False
    except (OSError, RuntimeError, subprocess.SubprocessError) as ex:
        if not _containers.warned:
            logger.warning(
                "Log explorer: cannot list docker containers (%s); only log files are listed", ex
            )
            _containers.warned = True
        names = []

    with _containers.lock:
        _containers.at = now
        _containers.names = list(names)
        _containers.valid = True
    logger.debug("Log explorer: %d docker containers", len(names))
    return names


def read_container_tail(name: str, max_lines: int = DEFAULT_CONTAINER_TAIL_LINES) -> List[str]:
    """
    Read the last *max_lines* of a container log.

    Args:
        name:      Container name.
        max_lines: Number of lines requested from ``docker logs``.

    Returns:
        The decoded log lines, oldest first (empty when the container is gone).
    """
    limit = max(1, int(max_lines))
    try:
        try:
            output = command_runner(["sudo", "docker", "logs", "--tail", str(limit), name])
        except (FileNotFoundError, OSError):
            output = command_runner(["docker", "logs", "--tail", str(limit), name])
    except (OSError, RuntimeError, subprocess.SubprocessError) as ex:
        logger.warning("Log explorer: cannot read container %s: %s", name, ex)
        return []
    return split_log_lines(output.encode("utf-8", errors="replace"))


def list_docker_sources() -> List[LogSource]:
    """
    List the running docker containers as log sources.

    Returns:
        One :class:`LogSource` per container, with ``kind="docker"`` and the id
        ``docker:<name>`` (the prefix guarantees it can never collide with a log
        file name).
    """
    return [
        LogSource(
            id=f"{KIND_DOCKER}:{name}",
            name=name,
            path=name,
            size_bytes=0,
            mtime=0.0,
            kind=KIND_DOCKER,
            entries_estimate=0,
        )
        for name in docker_container_names()
    ]


def list_all_sources(folder: Optional[str] = None) -> List[LogSource]:
    """
    List every queryable source: log files first, then docker containers.

    This mirrors the legacy ``/api/system/logs`` endpoint, which returned both
    ``🗃️:<file>`` and ``🐋:<container>`` entries.

    Args:
        folder: Folder to scan; defaults to :func:`resolve_logs_folder`.

    Returns:
        Files (newest first) followed by containers (sorted by name).
    """
    return list_sources(folder) + list_docker_sources()


def is_docker_source(source: Optional[LogSource]) -> bool:
    """Return ``True`` when *source* is a docker container, not a file."""
    return bool(source) and source.kind == KIND_DOCKER


def container_name(source: LogSource) -> str:
    """Return the container name of a docker *source*."""
    return source.path or source.id.split(":", 1)[-1]


def list_sources(folder: Optional[str] = None) -> List[LogSource]:
    """
    List the log files of *folder* (newest first).

    Args:
        folder: Folder to scan; defaults to :func:`resolve_logs_folder`.

    Returns:
        The available :class:`LogSource` objects, most recently modified first
        and capped at :data:`MAX_SOURCES`.
    """
    directory = Path(folder or resolve_logs_folder())
    if not directory.is_dir():
        logger.warning("Log explorer: log folder not found: %s", directory)
        return []

    try:
        candidates = list(directory.iterdir())
    except OSError as ex:
        logger.error("Log explorer: cannot list %s: %s", directory, ex)
        return []

    sources: List[LogSource] = []
    for candidate in candidates:
        if not _is_readable_file(candidate):
            continue
        try:
            stat = candidate.stat()
        except OSError as ex:
            logger.debug("Log explorer: skipping %s: %s", candidate, ex)
            continue
        sources.append(
            LogSource(
                id=candidate.name,
                name=candidate.name,
                path=str(candidate),
                size_bytes=int(stat.st_size),
                mtime=float(stat.st_mtime),
                kind=_kind_for(candidate.name),
                entries_estimate=estimate_lines(str(candidate)),
            )
        )

    sources.sort(key=lambda source: source.mtime, reverse=True)
    logger.debug("Log explorer: %d sources in %s", len(sources), directory)
    return sources[:MAX_SOURCES]


def get_source(source_id: Optional[str], folder: Optional[str] = None) -> Optional[LogSource]:
    """
    Resolve a source id (file name) to a :class:`LogSource`.

    Only files listed by :func:`list_sources` can be resolved, so a caller can
    never read an arbitrary path through the API.

    Args:
        source_id: Source id, i.e. the file name (``None``/``""`` never matches).
        folder:    Folder to scan; defaults to :func:`resolve_logs_folder`.

    Returns:
        The matching source, or ``None`` when it does not exist (any more).
    """
    if not source_id:
        return None
    for source in list_all_sources(folder):
        if source.id == source_id:
            return source
    logger.debug("Log explorer: unknown source %r", source_id)
    return None
