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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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

#: Number of rules the detectors apply (reported in every verdict).
RULE_COUNT = (
    len(secrets._TOKEN_PATTERNS)
    + 3  # assignment, url credentials, entropy
    + len(malicious._ALL_RULES)
    + 11  # injection rule families
)

# Unified diff hunk header: @@ -a,b +c,d @@
_HUNK_HEADER = re.compile(r"^@@\s+-(?P<old>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new>\d+)(?:,(?P<new_count>\d+))?\s+@@")


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
        code_findings, code_truncated = malicious.scan_text(
            text, path=path, policy=policy
        )
        findings += code_findings
        truncated = truncated or code_truncated

    if include_injection and "injection" in selected:
        injection_findings, injection_truncated = injection.scan_text(
            text, path=path, policy=policy
        )
        findings += injection_findings
        truncated = truncated or injection_truncated

    return _deduplicate(findings), truncated


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
        >>> verdict = scan_content('API_KEY = "sk-proj-1234567890abcdefghijkl"')
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
    if secrets.is_secret_file(relative) and relative not in policy.allow_secret_files:
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
) -> SecurityVerdict:
    """Scan the **added** lines of a unified diff.

    This is the pull-request gate: it inspects what the change introduces and
    reports the new line numbers, so a reviewer can jump straight to the
    offending line.

    Args:
        patch: Unified diff text (``git diff`` / ``git show`` output).
        domain: Integration point.
        policy: Effective policy.

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

    def _flush() -> None:
        """Scan the buffered added lines of the current hunk."""
        nonlocal added
        if not current_path or not added:
            added = []
            return
        payload = "\n".join(added)
        hunk_findings, hunk_truncated = collect_findings(
            payload, path=current_path, policy=policy
        )
        for finding in hunk_findings:
            # Re-base the line number onto the new file coordinates.
            if finding.line:
                # `added` already advanced `new_line` past the buffered lines, so
                # the first buffered line sits at new_line - len(added) and the
                # finding line is 1-based inside that payload.
                finding.line = max(1, new_line - len(added) + finding.line - 1)
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


def iter_project_files(
    project_path: str,
    max_files: int = 500,
    policy: Optional[CisoPolicy] = None,
) -> List[str]:
    """List the text files of a project in a bounded, deterministic order.

    Args:
        project_path: Project root.
        max_files: Maximum number of files returned.
        policy: Effective policy (skip rules).

    Returns:
        list: Absolute paths, sorted, hidden/ignored folders excluded.
    """
    policy = policy or CisoPolicy()
    skip_dirs = {
        ".git", ".codx", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
    }
    files: List[str] = []
    for root, dirs, names in os.walk(project_path):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith(".cache"))
        for name in sorted(names):
            absolute = os.path.join(root, name)
            relative = os.path.relpath(absolute, project_path)
            if policy.ignores_path(relative) or name in DEFAULT_SKIP_NAMES:
                continue
            if is_binary_path(name):
                continue
            files.append(absolute)
            if len(files) >= max_files:
                return files
    return files


def scan_project(
    project_path: str,
    max_files: int = 500,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
    policy: Optional[CisoPolicy] = None,
    paths: Optional[Sequence[str]] = None,
) -> SecurityVerdict:
    """Scan a whole project (or a subset of paths).

    Args:
        project_path: Project root.
        max_files: Maximum number of files read.
        domain: Integration point.
        policy: Effective policy.
        paths: Optional explicit list of files (absolute or relative).

    Returns:
        SecurityVerdict: Aggregated decision for the scanned files.
    """
    policy = policy or CisoPolicy()
    if not policy.is_domain_enabled(domain):
        return evaluate([], domain=domain, policy=policy, rules_evaluated=0)

    targets = list(paths) if paths else iter_project_files(
        project_path, max_files=max_files, policy=policy
    )

    findings: List[SecurityFinding] = []
    truncated = False
    scanned = 0

    for target in targets[:max_files]:
        absolute = target if os.path.isabs(target) else os.path.join(project_path, target)
        relative = os.path.relpath(absolute, project_path) if os.path.isabs(absolute) else target
        if policy.ignores_path(relative):
            continue
        content, file_truncated = read_text_file(absolute, policy.max_scan_bytes)
        if content is None:
            continue
        scanned += 1
        file_findings, file_truncated_scan = collect_findings(
            content, path=relative, policy=policy
        )
        findings += file_findings
        truncated = truncated or file_truncated or file_truncated_scan
        if secrets.is_secret_file(relative) and relative not in policy.allow_secret_files:
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
