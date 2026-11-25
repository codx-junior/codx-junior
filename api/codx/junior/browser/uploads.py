"""
Resolve files the human/AI wants to upload into a web page.

A browser session never exposes the project's whole filesystem.  The files an
upload may read are exactly the folders the session already shares (the
project folders bind-mounted into the container) plus its downloads folder::

    .codx/browser_downloads/<session_id>/    →  /downloads  (or a project path)

The agent names one of those files (``photo.jpg``, ``downloads/photo.jpg``,
``/home/<project>/docs/photo.jpg`` or the host absolute path) and the tool
streams its bytes to the browser's file input.  Streaming the content (instead
of handing the browser a path) is what makes uploads work with a *remote*
Chromium: Playwright and browser-use both send the bytes over CDP, so the file
does not need to exist inside the container, and a path from the API host is
enough.

Everything here is pure and testable: no Playwright, no browser, no Docker.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

#: Hard cap for one uploaded file. The whole payload travels over CDP and is
#: held in memory, so an accidental multi-GB file must fail before the browser.
DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_UPLOAD_BYTES_ENV = "CODX_BROWSER_MAX_UPLOAD_BYTES"

#: Folder aliases accepted in front of a relative path (``downloads/photo.jpg``).
DOWNLOAD_ALIASES = ("downloads", "download", "descargas", "descarga")

#: Default type when the extension says nothing (LinkedIn and most sites read it).
DEFAULT_MIME_TYPE = "application/octet-stream"


class UploadError(ValueError):
    """A requested upload file cannot be resolved or sent."""


@dataclass(frozen=True)
class UploadRoot:
    """One folder an upload may read, and where the browser sees it."""

    label: str
    host_path: Path
    browser_path: Optional[str] = None

    def describe(self) -> str:
        """Return one human/LLM readable ``label: path`` line."""
        where = f" (browser: {self.browser_path})" if self.browser_path else ""
        return f"{self.label}: {self.host_path}{where}"


@dataclass(frozen=True)
class UploadFile:
    """A resolved file ready to become a browser ``FilePayload``."""

    host_path: Path
    name: str
    size: int
    mime_type: str
    root: UploadRoot
    browser_path: Optional[str] = None

    def describe(self) -> str:
        """Return one short line for tool output (name, size, type)."""
        size = self.size
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                human = f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
                break
            size /= 1024.0
        return f"{self.name} ({human}, {self.mime_type})"


def max_upload_bytes() -> int:
    """Return the configured per-file upload cap (bytes)."""
    raw = os.environ.get(MAX_UPLOAD_BYTES_ENV)
    if not raw:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_UPLOAD_BYTES
    return max(1, value)


def _shared_folders(session: Any) -> List[Any]:
    """Return the session's shared folders (never fails on old sessions)."""
    folders = getattr(session, "shared_folders", None) or []
    return [folder for folder in folders if getattr(folder, "host_path", None)]


def upload_roots(session: Any) -> List[UploadRoot]:
    """
    Return the folders an upload may read from, in priority order.

    Args:
        session: BrowserSession (or any object with the same fields).

    Returns:
        List[UploadRoot]: downloads first, then every shared folder.
    """
    roots: List[UploadRoot] = []
    seen: set[str] = set()

    downloads = getattr(session, "downloads_path", None)
    if downloads:
        resolved = str(Path(downloads).expanduser())
        roots.append(UploadRoot(
            label="Downloads",
            host_path=Path(resolved),
            browser_path=getattr(session, "downloads_mount", None) or None,
        ))
        seen.add(os.path.normpath(resolved))

    for folder in _shared_folders(session):
        host = str(Path(str(folder.host_path)).expanduser())
        key = os.path.normpath(host)
        if key in seen:
            continue
        seen.add(key)
        roots.append(UploadRoot(
            label=getattr(folder, "name", None) or getattr(folder, "folder", None) or "shared",
            host_path=Path(host),
            browser_path=getattr(folder, "mount_path", None) or None,
        ))
    return roots


def describe_roots(session: Any) -> str:
    """Return the upload roots as a short bullet list (empty when none)."""
    roots = upload_roots(session)
    if not roots:
        return ""
    return "\n".join(f"  - {root.describe()}" for root in roots)


def available_files(session: Any, limit: int = 25) -> List[str]:
    """
    List a few uploadable files, for a helpful "did you mean" error.

    Args:
        session: BrowserSession.
        limit: Maximum names returned.

    Returns:
        List[str]: Host paths of the first files found, downloads first.
    """
    names: List[str] = []
    for root in upload_roots(session):
        try:
            for entry in sorted(root.host_path.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_file() and not entry.name.startswith("."):
                    names.append(str(entry))
                    if len(names) >= limit:
                        return names
        except OSError:
            continue
    return names


def _clean(spec: Any) -> str:
    """Normalise one path/filename from the model (quotes, spaces, slashes)."""
    text = str(spec or "").strip().strip("'\"")
    text = text.replace("\\ ", " ")
    return text


def _container_candidates(spec: str, roots: List[UploadRoot]) -> List[Path]:
    """Map a path *inside the browser* (``/downloads/x.png``) back to the host."""
    candidates: List[Path] = []
    for root in roots:
        browser_path = (root.browser_path or "").rstrip("/")
        if not browser_path:
            continue
        if spec == browser_path:
            candidates.append(root.host_path)
        elif spec.startswith(browser_path + "/"):
            relative = spec[len(browser_path) + 1:]
            candidates.append(root.host_path / relative)
    return candidates


def _candidates(spec: str, roots: List[UploadRoot]) -> List[Path]:
    """Return every host path a single file specification may refer to."""
    text = _clean(spec)
    if not text:
        return []
    if "\x00" in text:
        raise UploadError("Invalid file name")

    candidates: List[Path] = []
    if Path(text).is_absolute():
        candidates.append(Path(text))
        candidates.extend(_container_candidates(text, roots))
        return candidates

    # ``./downloads/photo.jpg`` and ``downloads/photo.jpg`` are the same file.
    while text.startswith("./"):
        text = text[2:]
    lowered = text.lower()
    for alias in DOWNLOAD_ALIASES:
        prefix = alias + "/"
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    # Never let a name walk out of its root (the resolve() check below is the
    # real guard, this keeps the error message honest).
    parts = text.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise UploadError(f"Invalid file name: {_clean(spec)}")
    for root in roots:
        candidates.append(root.host_path / text)
    return candidates


def _resolve_existing(path: Path) -> Optional[Path]:
    """Return the real path of an existing file, or None."""
    try:
        real = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real if real.is_file() else None


def _root_for(real: Path, roots: List[UploadRoot]) -> Optional[UploadRoot]:
    """Return the root that contains *real* (after resolving symlinks)."""
    for root in roots:
        try:
            base = root.host_path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if real == base or base in real.parents:
            return root
    return None


def _browser_path_for(real: Path, root: UploadRoot) -> Optional[str]:
    """Return the path of *real* inside the browser, when it has one."""
    mount = (root.browser_path or "").rstrip("/")
    if not mount:
        return None
    try:
        relative = real.relative_to(root.host_path.expanduser().resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return None
    return f"{mount}/{relative}".replace(os.sep, "/") if str(relative) != "." else mount


_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\[\]-]+")


def safe_filename(name: Optional[str]) -> str:
    """
    Return a filesystem-safe base name for an uploaded file.

    Args:
        name: Name sent by the browser/client (may include a path).

    Returns:
        str: A name without directories or control characters.
    """
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = _SAFE_NAME.sub("_", base).strip(" .") or "upload.bin"
    return base[:180]


def unique_path(folder: Any, name: str) -> Path:
    """
    Return a path inside *folder* that does not overwrite an existing file.

    Args:
        folder: Destination folder (created by the caller).
        name: Safe file name (see :func:`safe_filename`).

    Returns:
        Path: ``folder/name`` or ``folder/name (n).ext``.
    """
    base = Path(str(folder))
    candidate = base / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        candidate = base / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return base / f"{stem}-{os.urandom(4).hex()}{suffix}"


def resolve_session_file(session: Any, spec: Any, *, max_bytes: Optional[int] = None) -> UploadFile:
    """
    Resolve one file name/path into an uploadable file.

    Args:
        session: BrowserSession whose roots are allowed.
        spec: Relative name, root-relative path, browser path or host path.
        max_bytes: Optional size cap (default: :func:`max_upload_bytes`).

    Returns:
        UploadFile: The resolved file.

    Raises:
        UploadError: When the file is missing, outside the allowed roots, a
            directory, or larger than the cap.
    """
    roots = upload_roots(session)
    if not roots:
        raise UploadError(
            "This browser session shares no folder, so there is nothing to upload. "
            "Start it with a shared folder (or the project mounted)."
        )

    limit = max_upload_bytes() if max_bytes is None else max(1, int(max_bytes))
    candidates = _candidates(spec, roots)
    if not candidates:
        raise UploadError("A file name or path is required")

    real: Optional[Path] = None
    for candidate in candidates:
        resolved = _resolve_existing(candidate)
        if resolved is not None:
            real = resolved
            break

    if real is None:
        tried = ", ".join(str(candidate) for candidate in candidates[:4])
        hint = ""
        if "/" in _clean(spec) or os.sep in _clean(spec):
            hint = " Use a file name inside one of the shared folders."
        raise UploadError(
            f"File not found: {_clean(spec)} (tried {tried}).{hint}"
        )

    root = _root_for(real, roots)
    if root is None:
        raise UploadError(
            f"Refusing to upload {_clean(spec)}: it is outside the folders shared "
            f"with this browser session ({', '.join(str(r.host_path) for r in roots)})."
        )

    try:
        size = real.stat().st_size
    except OSError as ex:
        raise UploadError(f"Cannot read {real.name}: {ex}") from ex
    if size <= 0:
        raise UploadError(f"{real.name} is empty")
    if size > limit:
        raise UploadError(
            f"{real.name} is {size} bytes, over the {limit} byte upload limit "
            f"({MAX_UPLOAD_BYTES_ENV} raises it)."
        )

    mime_type = mimetypes.guess_type(real.name)[0] or DEFAULT_MIME_TYPE
    return UploadFile(
        host_path=real,
        name=real.name,
        size=size,
        mime_type=mime_type,
        root=root,
        browser_path=_browser_path_for(real, root),
    )


def file_payload(upload: UploadFile, *, max_bytes: Optional[int] = None) -> dict:
    """
    Read one resolved file into a Playwright/browser-use ``FilePayload``.

    Args:
        upload: Resolved upload file.
        max_bytes: Optional size cap (re-checked while reading).

    Returns:
        dict: ``{"name", "mimeType", "buffer"}`` (buffer is ``bytes``).

    Raises:
        UploadError: When the file cannot be read or grew past the cap.
    """
    limit = max_upload_bytes() if max_bytes is None else max(1, int(max_bytes))
    try:
        with open(upload.host_path, "rb") as handle:
            buffer = handle.read(limit + 1)
    except OSError as ex:
        raise UploadError(f"Cannot read {upload.name}: {ex}") from ex
    if len(buffer) > limit:
        raise UploadError(
            f"{upload.name} is larger than the {limit} byte upload limit"
        )
    return {"name": upload.name, "mimeType": upload.mime_type, "buffer": buffer}


def prepare_upload(
    session: Any,
    specs: Optional[List[Any]] = None,
    *,
    value: Any = None,
    max_bytes: Optional[int] = None,
) -> tuple[List[UploadFile], List[dict]]:
    """
    Turn the tool arguments into resolved files and browser payloads.

    Args:
        session: BrowserSession whose roots are allowed.
        specs: File names/paths from ``files``.
        value: The single-file spelling of ``specs`` (kept for models that only
            know the common ``value`` parameter).
        max_bytes: Optional size cap per file.

    Returns:
        tuple: ``(resolved_files, payloads)`` in the requested order.

    Raises:
        UploadError: With the available files appended, so the model can
            correct a guessed name in the next call.
    """
    if isinstance(specs, (str, bytes, os.PathLike)):
        # A model that sends one file as a bare string must not be iterated
        # character by character.
        specs = [specs]
    requested = [str(item) for item in (specs or []) if str(item or "").strip()]
    if value is not None and str(value).strip():
        requested.append(str(value))
    if not requested:
        raise UploadError(
            "files is required for upload (e.g. ['photo.jpg'] from the session's Downloads folder)"
        )
    try:
        resolved = [resolve_session_file(session, spec, max_bytes=max_bytes) for spec in requested]
        payloads = [file_payload(item, max_bytes=max_bytes) for item in resolved]
    except UploadError as ex:
        available = available_files(session)
        if available:
            hint = "\nFiles available:\n" + "\n".join(f"- {name}" for name in available)
            raise UploadError(f"{ex}{hint}") from ex
        raise
    return resolved, payloads


def file_payloads(session: Any, specs: List[Any], *, max_bytes: Optional[int] = None) -> List[dict]:
    """
    Resolve and read several upload specifications.

    Args:
        session: BrowserSession.
        specs: File names/paths.
        max_bytes: Optional size cap per file.

    Returns:
        List[dict]: Payloads, in the order requested.

    Raises:
        UploadError: On the first file that cannot be resolved or read.
    """
    payloads: List[dict] = []
    for spec in specs:
        payloads.append(file_payload(resolve_session_file(session, spec, max_bytes=max_bytes)))
    return payloads
