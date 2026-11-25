"""Scanners: run every detector over the artefacts of the project.

The scanner is the entry point used by the tools and by the gates. It knows
*how to read* an artefact (a string, a file, a unified diff, a directory) and
delegates *what to look for* to the detectors:

    secrets.py    credentials and tokens
    malicious.py  malicious / obfuscated / destructive code
    injection.py  stored prompt-injection payloads

Design constraints
    * Bounded: every read is capped by ``policy.max_scan_bytes`` and every
      directory walk by ``max_files``, so scanning a monorepo cannot hang a run.
    * Binary-safe: binary files are skipped instead of being decoded.
    * Diff-aware: a pull request is scanned on the **added** lines only, with
      the new line numbers, so a review points at the code being introduced.

Made with ❤️ by codx-junior
"""

import logging
import os
import re
import subprocess
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from codx.junior.security.ciso import context as ciso_context
from codx.junior.security.ciso import injection, malicious, secrets
from codx.junior.security.ciso.models import (
    CisoPolicy,
    SecurityCategory,
    SecurityDomain,
    SecurityFinding,
    SecuritySeverity,
    SecurityVerdict,
)
from codx.junior.security.ciso.policy import evaluate

logger = logging.getLogger(__name__)

#: Extensions never decoded as text.
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo", ".o", ".a", ".lib",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".wav",
    ".avi", ".mov", ".sqlite", ".db", ".bin", ".class", ".jar",
})

#: Files whose content is skipped entirely (vendored/generated).
DEFAULT_SKIP_NAMES = frozenset({
    "package-lock.json", "poetry.lock", "yarn.lock", "pnpm-lock.yaml",
    "Pipfile.lock", "composer.lock", "go.sum", "Cargo.lock",
})

#: Folders never walked: control plane, vendored dependencies, tool caches.
SKIP_DIR_NAMES = frozenset({
    ".git", ".codx", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
})

#: Seconds allowed to ask git for the file list (a hung git never hangs a scan).
GIT_LIST_TIMEOUT_SECONDS = 20

#: Number of rules the detectors apply (reported in every verdict).
RULE_COUNT = (
    len(secrets._TOKEN_PATTERNS)
    + 3  # assignment, url credentials, entropy
    + len(malicious._ALL_RULES)
    + 11  # injection rule families
)

#: Rule families that describe *executable* behaviour. They are matched against
#: raw text, so on a non-executable path they match quotations instead.
CODE_RULE_PREFIXES = (
    "supply_chain.", "dangerous.", "obfuscation.", "exfiltration.", "command.",
    "malicious.", "destructive.",
)

#: Backwards-compatible aliases. Path classification lives in
#: :mod:`codx.junior.security.ciso.context` so the detectors and the scanner
#: cannot drift.
DATA_PATH_SEGMENTS = ciso_context.DATA_PATH_SEGMENTS
PROSE_EXTENSIONS = ciso_context.PROSE_EXTENSIONS

#: Total characters one diff scan may inspect. A 550-file diff (measured:
#: +131 202 lines) ran past the tool timeout, so the gate returned no verdict at
#: all; a bounded scan always produces one and says it was truncated.
DIFF_SCAN_BUDGET_CHARS = int(
    os.environ.get("CODX_JUNIOR_CISO_DIFF_BUDGET_CHARS", str(4 * 1024 * 1024))
)

# Unified diff hunk header: @@ -a,b +c,d @@
_HUNK_HEADER = re.compile(r"^@@\s+-(?P<old>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new>\d+)(?:,(?P<new_count>\d+))?\s+@@")


class ScanCancelled(Exception):
    """Raised by :func:`scan_project` when the caller asks it to stop.

    Cancellation is cooperative: the scan checks the predicate before reading
    each file, so it stops in bounded time without leaving a half-written
    result. The caller (the CISO scan service) records the cancelled state.
    """


# -----------------------------------------------------------------------------
# Text helpers
# -----------------------------------------------------------------------------
def is_binary_path(path: Optional[str]) -> bool:
    """Return True when *path* has a binary extension.

    Args:
        path: File path.

    Returns:
        bool: True for images, archives, compiled artefacts, ...
    """
    if not path:
        return False
    return os.path.splitext(path.lower())[1] in BINARY_EXTENSIONS


def read_text_file(path: str, max_bytes: int) -> Tuple[Optional[str], bool]:
    """Read at most *max_bytes* of a text file.

    Args:
        path: Absolute path.
        max_bytes: Read cap.

    Returns:
        tuple: ``(content, truncated)``; ``content`` is ``None`` when the file
        is binary, too large by name or unreadable.
    """
    if is_binary_path(path):
        return None, False
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except (OSError, IOError) as ex:
        logger.debug("CISO: cannot read %s: %s", path, ex)
        return None, False

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    if b"\x00" in raw:
        return None, False
    return raw.decode("utf-8", errors="replace"), truncated


def _deduplicate(findings: Sequence[SecurityFinding]) -> List[SecurityFinding]:
    """Drop duplicated ``(rule, path, line, evidence)`` findings, keeping order."""
    seen = set()
    unique: List[SecurityFinding] = []
    for finding in findings:
        key = (finding.rule_id, finding.path, finding.line, finding.evidence)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _line_offset(text: str, line: Optional[int]) -> Optional[int]:
    """Return the character offset of a 1-based *line* in *text*."""
    if not text or not line or line < 1:
        return None
    offset = 0
    for _ in range(line - 1):
        next_newline = text.find("\n", offset)
        if next_newline == -1:
            return None
        offset = next_newline + 1
    return offset


def apply_file_context(
    finding: SecurityFinding,
    full_text: Optional[str],
    path: Optional[str],
) -> SecurityFinding:
    """Lower a finding that sits in a docstring/comment in the full file.

    A diff hunk contains only the changed lines, so a line inside a multi-line
    docstring or a comment can lose the delimiter that proved it is
    documentation. The pre-commit gate can supply the staged file content; this
    helper restores the context without widening what is reported (the finding
    still comes from an added line).

    Args:
        finding: Finding produced from a diff hunk.
        full_text: Complete new file content, or ``None``.
        path: File path of the finding.

    Returns:
        SecurityFinding: The same finding, possibly downgraded to ``low``.
    """
    if not full_text or finding is None:
        return finding
    if ciso_context.is_prose_path(path):
        # Prose detection is already handled by the detector: a README may
        # carry a real instruction, so "the file is Markdown" is not context
        # that excuses it.
        return finding
    offset = _line_offset(full_text, finding.line)
    if offset is None:
        return finding
    if not ciso_context.is_documentation_position(full_text, offset, path):
        return finding
    finding.severity = SecuritySeverity.LOW
    reason = finding.reason or "Pattern matched"
    finding.reason = f"{reason} (documentation/comment context in the file)"
    return finding


def is_data_path(path: Optional[str]) -> bool:
    """Return True when *path* is agent/runtime data instead of source code.

    Args:
        path: File path.

    Returns:
        bool: True for a path under a ``.codx`` control-plane folder.
    """
    return ciso_context.is_data_path(path)


def is_prose_path(path: Optional[str]) -> bool:
    """Return True when *path* is documentation, i.e. never executed.

    Args:
        path: File path.

    Returns:
        bool: True for Markdown and other long-form text files.
    """
    return ciso_context.is_prose_path(path)


def is_detector_corpus_path(path: Optional[str]) -> bool:
    """Return True when *path* is the CISO detector package or its fixtures."""
    return ciso_context.is_detector_corpus_path(path)


def is_code_rule(rule_id: str) -> bool:
    """Return True when *rule_id* belongs to an executable-behaviour family."""
    return (rule_id or "").startswith(CODE_RULE_PREFIXES)


def scope_code_findings(
    findings: Sequence[SecurityFinding],
    path: Optional[str],
) -> List[SecurityFinding]:
    """Adjust the *code-shaped* findings for a non-executable path.

    The detectors read raw text, so they cannot tell an instruction from a
    sentence about that instruction. That is how a Markdown troubleshooting row
    mentioning ``curl`` and a transcript field saying *"the shell shim is clean:
    no curl|sh"* were both reported CRITICAL and kept the diff gate red.

    The rules stay identical for executable paths. For runtime data the code
    families are dropped (the payload replays what an agent read, not a command
    that ran); for documentation they are downgraded to ``low`` so the reviewer
    still sees them and nothing blocks.

    ``secret.*`` findings are never touched here and the injection detector runs
    separately: both stay active on every path, with no exception.

    Args:
        findings: Merged findings of one payload.
        path: File the payload belongs to.

    Returns:
        List[SecurityFinding]: The findings to report.
    """
    if not findings:
        return []
    drop = is_data_path(path)
    downgrade = (not drop) and is_prose_path(path)
    if not drop and not downgrade:
        # The detector corpus is handled per finding by ``context.py`` (rule
        # tables, docstrings and fixtures are low; a real executable line is
        # not blanket-excused).
        return list(findings)

    scoped: List[SecurityFinding] = []
    for finding in findings:
        # Category, not just the id: ``malicious.agent-instruction`` is an
        # injection finding even though its rule lives in the malicious table.
        injection = (
            finding.category == SecurityCategory.INJECTION
            or (finding.rule_id or "").startswith("injection.")
        )
        if drop and injection:
            # A stored transcript *quotes* the payload it adjudicated. The
            # enforcement point for a stored payload is ``guard_knowledge`` on
            # ingestion, not the diff gate reading the file at rest; it stays
            # reported (medium) so it is never invisible, but it cannot block a
            # change that did not write it.
            finding.severity = SecuritySeverity.MEDIUM
            finding.reason = (
                "Stored injection payload on a data path (enforced on "
                f"ingestion). {finding.reason or 'Pattern matched'}"
            )
            scoped.append(finding)
            continue
        if not is_code_rule(finding.rule_id) or injection:
            # Documentation can carry an actual instruction: a README that
            # says "ignore previous instructions" is not a quotation just
            # because it is Markdown. Only the description markers above
            # lower it; an injection finding keeps its detector severity.
            scoped.append(finding)
            continue
        if drop:
            continue
        finding.severity = SecuritySeverity.LOW
        reason = finding.reason or "Pattern matched"
        finding.reason = f"Quoted in documentation, not executed. {reason}"
        scoped.append(finding)
    return scoped


# -----------------------------------------------------------------------------
# Detector orchestration
# -----------------------------------------------------------------------------
def collect_findings(
    text: str,
    path: Optional[str] = None,
    policy: Optional[CisoPolicy] = None,
    detectors: Optional[Iterable[str]] = None,
    include_injection: bool = True,
) -> Tuple[List[SecurityFinding], bool]:
    """Run every detector over *text* and return the merged findings.

    Args:
        text: Payload to scan.
        path: Source of the payload.
        policy: Effective policy.
        detectors: Optional subset of ``{"secrets", "malicious", "injection"}``.
        include_injection: Run the stored-injection detector (default: True).

    Returns:
        tuple: ``(findings, truncated)``.
    """
    policy = policy or CisoPolicy()
    selected = set(detectors) if detectors else {"secrets", "malicious", "injection"}

    findings: List[SecurityFinding] = []
    truncated = False

    if "secrets" in selected:
        secret_findings, _redacted, secret_truncated = secrets.scan_text(
            text, path=path, policy=policy
        )
        findings += secret_findings
        truncated = truncated or secret_truncated

    if "malicious" in selected:
        # Content is not a command line. The ``command.*`` rules match shell
        # syntax that also appears in comments, documentation and code samples
        # ("shutdown", "env", "crontab"), which turned every project scan into
        # thousands of false positives. Commands are inspected by
        # ``malicious.scan_command`` at the command gate, where the payload is
        # actually a command.
        code_findings, code_truncated = malicious.scan_text(
            text, path=path, policy=policy, include_command_rules=False
        )
        findings += code_findings
        truncated = truncated or code_truncated

    if include_injection and "injection" in selected:
        injection_findings, injection_truncated = injection.scan_text(
            text, path=path, policy=policy
        )
        findings += injection_findings
        truncated = truncated or injection_truncated

    return _deduplicate(scope_code_findings(findings, path)), truncated


# -----------------------------------------------------------------------------
# Public scanners
# -----------------------------------------------------------------------------
def scan_content(
    text: str,
    path: Optional[str] = None,
    domain: SecurityDomain = SecurityDomain.FILE_WRITE,
    policy: Optional[CisoPolicy] = None,
    detectors: Optional[Iterable[str]] = None,
    include_injection: bool = True,
) -> SecurityVerdict:
    """Scan a payload that is about to be written, committed or returned.

    Args:
        text: Payload.
        path: Target path (relative to the project).
        domain: Integration point.
        policy: Effective policy.
        detectors: Optional detector subset.
        include_injection: Run the stored-injection detector.

    Returns:
        SecurityVerdict: Decision plus the redacted payload when secrets were
        found and ``policy.redact_secrets`` is enabled.

    Example:
        >>> verdict = scan_content('API_KEY = "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0fI"')
        >>> verdict.blocked
        True
    """
    policy = policy or CisoPolicy()
    if not policy.is_domain_enabled(domain):
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)
    if policy.ignores_path(path):
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)

    findings, truncated = collect_findings(
        text, path=path, policy=policy, detectors=detectors,
        include_injection=include_injection,
    )

    redacted: Optional[str] = None
    if policy.redact_secrets and any(f.category == SecurityCategory.SECRET for f in findings):
        redacted, _count = secrets.redact_text(text, policy)

    return evaluate(
        findings,
        domain=domain,
        policy=policy,
        redacted_text=redacted,
        rules_evaluated=RULE_COUNT,
        truncated=truncated,
    )


def scan_file(
    path: str,
    project_path: Optional[str] = None,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
    policy: Optional[CisoPolicy] = None,
) -> SecurityVerdict:
    """Scan one file on disk.

    Args:
        path: Absolute path (or relative to *project_path*).
        project_path: Project root used to build the reported relative path.
        domain: Integration point.
        policy: Effective policy.

    Returns:
        SecurityVerdict: Decision for the file.
    """
    policy = policy or CisoPolicy()
    absolute = path
    if project_path and not os.path.isabs(path):
        absolute = os.path.join(project_path, path)
    relative = path
    if project_path and absolute.startswith(project_path):
        relative = os.path.relpath(absolute, project_path)

    if policy.ignores_path(relative) or os.path.basename(relative) in DEFAULT_SKIP_NAMES:
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)

    content, truncated = read_text_file(absolute, policy.max_scan_bytes)
    if content is None:
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)

    verdict = scan_content(content, path=relative, domain=domain, policy=policy)
    verdict.truncated = verdict.truncated or truncated

    # A file that is *supposed* to hold secrets is reported once, as a policy
    # finding, so the reviewer sees the file itself and not 40 noisy lines.
    if (
        secrets.is_secret_file(relative)
        and relative not in policy.allow_secret_files
        and not secrets.is_example_file(relative)
    ):
        verdict.findings.append(SecurityFinding(
            rule_id="configuration.secret-file",
            title="Credential-bearing file in the project",
            severity=SecuritySeverity.MEDIUM,
            category=SecurityCategory.CONFIGURATION,
            reason="The file name is conventionally used to store credentials",
            path=relative,
            remediation=(
                "Keep the file out of the repository (`.gitignore`), commit a "
                "`.env.example` with placeholders instead and store the real "
                "values in the project secret store."
            ),
        ))
        verdict.summary = verdict.summary or "credential-bearing file"
    return verdict


def scan_diff(
    patch: str,
    domain: SecurityDomain = SecurityDomain.GIT_COMMIT,
    policy: Optional[CisoPolicy] = None,
    context_provider: Optional[Callable[[str], Optional[str]]] = None,
) -> SecurityVerdict:
    """Scan the **added** lines of a unified diff.

    This is the pull-request gate: it inspects what the change introduces and
    reports the new line numbers, so a reviewer can jump straight to the
    offending line.

    Args:
        patch: Unified diff text (``git diff`` / ``git show`` output).
        domain: Integration point.
        policy: Effective policy.
        context_provider: Optional callback returning the complete new content
            of a path. It restores docstring/comment context that a ``-U0``
            hunk omits; findings still come only from added lines.

    Returns:
        SecurityVerdict: Decision for the whole change.
    """
    policy = policy or CisoPolicy()
    if not policy.is_domain_enabled(domain):
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)

    findings: List[SecurityFinding] = []
    current_path: Optional[str] = None
    new_line = 0
    added: List[str] = []
    truncated = False
    scanned_chars = 0
    file_context: Dict[str, Optional[str]] = {}

    def _flush() -> None:
        """Scan the buffered added lines of the current hunk."""
        nonlocal added, scanned_chars, truncated
        if not current_path or not added:
            added = []
            return
        payload = "\n".join(added)
        if scanned_chars >= DIFF_SCAN_BUDGET_CHARS:
            # Budget exhausted: stop inspecting instead of running past the
            # caller's timeout. The verdict is marked truncated, so a partial
            # scan is reported as partial -- never as a clean one.
            truncated = True
            added = []
            return
        scanned_chars += len(payload)
        hunk_findings, hunk_truncated = collect_findings(
            payload, path=current_path, policy=policy
        )
        full_text: Optional[str] = None
        if context_provider is not None and current_path:
            if current_path not in file_context:
                try:
                    file_context[current_path] = context_provider(current_path)
                except Exception:
                    logger.debug(
                        "CISO: context provider failed for %s", current_path,
                        exc_info=True,
                    )
                    file_context[current_path] = None
            full_text = file_context[current_path]
        for finding in hunk_findings:
            # Re-base the line number onto the new file coordinates.
            if finding.line:
                # `added` already advanced `new_line` past the buffered lines, so
                # the first buffered line sits at new_line - len(added) and the
                # finding line is 1-based inside that payload.
                finding.line = max(1, new_line - len(added) + finding.line - 1)
            if full_text:
                apply_file_context(finding, full_text, current_path)
            findings.append(finding)
        if hunk_truncated:
            truncated = True
        added = []

    for raw_line in (patch or "").splitlines():
        if raw_line.startswith("diff --git"):
            _flush()
            current_path = None
            continue

        if raw_line.startswith("+++ "):
            _flush()
            candidate = raw_line[4:].strip()
            if candidate in ("/dev/null", ""):
                current_path = None
            else:
                current_path = candidate[2:] if candidate.startswith("b/") else candidate
                if policy.ignores_path(current_path) or os.path.basename(current_path) in DEFAULT_SKIP_NAMES:
                    current_path = None
            continue

        if raw_line.startswith("@@ "):
            _flush()
            match = _HUNK_HEADER.match(raw_line)
            new_line = int(match.group("new")) if match else 0
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.append(raw_line[1:])
            new_line += 1
            continue

        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue

        # "\ No newline at end of file" markers carry no content.
        if raw_line.startswith("\\"):
            continue

        # Context line inside a hunk: close the pending added block and advance.
        if added:
            _flush()
        if new_line:
            new_line += 1

    _flush()

    redacted: Optional[str] = None
    if policy.redact_secrets and any(f.category == SecurityCategory.SECRET for f in findings):
        redacted, _count = secrets.redact_text(patch or "", policy)

    return evaluate(
        _deduplicate(findings),
        domain=domain,
        policy=policy,
        redacted_text=redacted,
        rules_evaluated=RULE_COUNT,
        truncated=truncated,
    )


def _git(project_path: str, args: Sequence[str]) -> Tuple[int, str]:
    """Run a read-only git command in *project_path*.

    Args:
        project_path: Working directory of the command.
        args: Arguments after ``git --no-pager``.

    Returns:
        tuple: ``(returncode, stdout)``; ``returncode`` is -1 when git is
        missing, cannot start or times out.
    """
    command = ["git", "--no-pager", "-c", "core.quotepath=false", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=GIT_LIST_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        logger.debug("CISO: git %s failed: %s", " ".join(args), ex)
        return -1, ""
    return completed.returncode, completed.stdout


def _git_listed_files(project_path: str) -> Optional[List[str]]:
    """Return the files git would ship for *project_path*, or ``None``.

    ``--cached`` is what is tracked and ``--others --exclude-standard`` the new
    files git does **not** ignore: together they are exactly the content that
    can reach a commit. A file git discards is *produced* by the project (a
    scratch ``tmp/``, a build output, a downloaded artefact), so a credential
    found there is not something the repository can fix and it eats the file
    budget of the scan.

    ``None`` means "no git working tree here": the scanner also runs inside
    slim containers and on plain folders, where the filesystem walk is used.

    Args:
        project_path: Folder to list (project root or one of its subfolders).

    Returns:
        Optional[List[str]]: Absolute paths, or ``None`` without a repository.
    """
    code, stdout = _git(project_path, ["rev-parse", "--show-toplevel"])
    toplevel = os.path.abspath(stdout.strip()) if stdout.strip() else ""
    if code != 0 or not toplevel or not os.path.isdir(toplevel):
        return None
    relative_root = os.path.relpath(os.path.abspath(project_path), toplevel)
    if relative_root.startswith(os.pardir):
        return None
    args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if relative_root not in (".", ""):
        # An explicitly selected folder that git ignores holds files no commit
        # can carry: the caller pointed at it on purpose, so walk it instead.
        ignored, _ = _git(toplevel, ["check-ignore", "-q", "--", relative_root])
        if ignored == 0:
            return None
        args += ["--", relative_root]
    code, stdout = _git(toplevel, args)
    if code != 0:
        return None
    files: List[str] = []
    for item in stdout.split("\x00"):
        if not item:
            continue
        absolute = os.path.abspath(os.path.join(toplevel, item))
        if os.path.isfile(absolute):
            files.append(absolute)
    return files


def _walk_project_files(project_path: str) -> Iterable[str]:
    """Yield every file of a plain folder, skipping vendored/cache folders."""
    for root, dirs, names in os.walk(project_path):
        dirs[:] = sorted(
            d for d in dirs
            if d not in SKIP_DIR_NAMES
            and not d.startswith(".cache")
            and not d.startswith("pytest-of-")
            and not ciso_context.is_generated_path(os.path.relpath(
                os.path.join(root, d), project_path
            ))
        )
        for name in sorted(names):
            yield os.path.abspath(os.path.join(root, name))


def _is_scannable(relative: str, policy: CisoPolicy) -> bool:
    """Return True when *relative* is project content the scan must read."""
    name = os.path.basename(relative)
    for segment in relative.replace(os.sep, "/").split("/")[:-1]:
        if segment in SKIP_DIR_NAMES:
            return False
        if segment.startswith(".cache") or segment.startswith("pytest-of-"):
            return False
    if ciso_context.is_generated_path(relative):
        return False
    if policy.ignores_path(relative) or name in DEFAULT_SKIP_NAMES:
        return False
    return not is_binary_path(name)


def iter_project_files(
    project_path: str,
    max_files: int = 500,
    policy: Optional[CisoPolicy] = None,
) -> List[str]:
    """List the text files of a project in a bounded, deterministic order.

    Only the files git would ship are listed: tracked files plus new ones that
    are not ignored. Walking the raw tree instead reports ignored artefacts
    (``tmp/``, a build output, an extracted package) and spends the file budget
    on files the repository deliberately discards; the plain filesystem walk is
    reserved for folders outside a git working tree.

    Args:
        project_path: Project root (or one of its folders).
        max_files: Maximum number of files returned.
        policy: Effective policy (skip rules).

    Returns:
        list: Absolute paths, sorted, git-ignored files excluded.
    """
    policy = policy or CisoPolicy()
    listed = _git_listed_files(project_path)
    candidates = listed if listed is not None else _walk_project_files(project_path)
    files: List[str] = []
    for absolute in candidates:
        relative = os.path.relpath(absolute, project_path)
        if not _is_scannable(relative, policy):
            continue
        files.append(absolute)
        if len(files) >= max_files:
            break
    files.sort()
    return files


def scan_project(
    project_path: str,
    max_files: int = 500,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
    policy: Optional[CisoPolicy] = None,
    paths: Optional[Sequence[str]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> SecurityVerdict:
    """Scan a whole project (or a subset of paths).

    Args:
        project_path: Project root.
        max_files: Maximum number of files read.
        domain: Integration point.
        policy: Effective policy.
        paths: Optional explicit list of files (absolute or relative).
        progress: Optional ``(done, total)`` callback called after every file.
            The CISO view uses it to show progress for a long project scan.
        should_cancel: Optional predicate polled before every file. When it
            returns True the scan stops by raising :class:`ScanCancelled`.

    Returns:
        SecurityVerdict: Aggregated decision for the scanned files.

    Raises:
        ScanCancelled: When *should_cancel* returns True.
    """
    policy = policy or CisoPolicy()
    if not policy.is_domain_enabled(domain):
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)

    targets = list(paths) if paths else iter_project_files(
        project_path, max_files=max_files, policy=policy
    )
    targets = targets[:max_files]

    findings: List[SecurityFinding] = []
    truncated = False
    scanned = 0
    total = len(targets)

    for index, target in enumerate(targets):
        if should_cancel is not None and should_cancel():
            raise ScanCancelled(f"scan cancelled after {scanned} file(s)")
        absolute = target if os.path.isabs(target) else os.path.join(project_path, target)
        relative = os.path.relpath(absolute, project_path) if os.path.isabs(absolute) else target
        if not policy.ignores_path(relative):
            content, file_truncated = read_text_file(absolute, policy.max_scan_bytes)
            if content is not None:
                scanned += 1
                file_findings, file_truncated_scan = collect_findings(
                    content, path=relative, policy=policy
                )
                findings += file_findings
                truncated = truncated or file_truncated or file_truncated_scan
                if (
                    secrets.is_secret_file(relative)
                    and relative not in policy.allow_secret_files
                    and not secrets.is_example_file(relative)
                ):
                    findings.append(SecurityFinding(
                        rule_id="configuration.secret-file",
                        title="Credential-bearing file in the project",
                        severity=SecuritySeverity.MEDIUM,
                        category=SecurityCategory.CONFIGURATION,
                        reason="The file name is conventionally used to store credentials",
                        path=relative,
                        remediation=(
                            "Keep the file out of the repository and commit a "
                            "`.env.example` with placeholders instead."
                        ),
                    ))
        if progress is not None:
            try:
                progress(index + 1, total)
            except Exception:  # pragma: no cover - a progress sink never breaks a scan
                logger.debug("CISO: scan progress callback failed", exc_info=True)

    verdict = evaluate(
        _deduplicate(findings),
        domain=domain,
        policy=policy,
        rules_evaluated=RULE_COUNT * max(scanned, 1),
        truncated=truncated,
    )
    verdict.summary = f"{verdict.summary} | {scanned} file(s) scanned"
    return verdict


def findings_table(findings: Sequence[SecurityFinding], max_findings: int = 100) -> str:
    """Render *findings* as a compact Markdown table.

    Args:
        findings: Findings to render.
        max_findings: Maximum rows.

    Returns:
        str: Markdown table (or a "no findings" line).
    """
    if not findings:
        return "_No findings._"
    lines = ["| Severity | Rule | Location | Evidence |", "| --- | --- | --- | --- |"]
    for finding in findings[:max_findings]:
        location = finding.path or "-"
        if finding.line:
            location = f"{location}:{finding.line}"
        evidence = (finding.evidence or "").replace("|", "\\|")
        lines.append(
            f"| {finding.severity.value} | `{finding.rule_id}` | `{location}` | {evidence} |"
        )
    if len(findings) > max_findings:
        lines.append(f"| … | | | {len(findings) - max_findings} more |")
    return "\n".join(lines)
