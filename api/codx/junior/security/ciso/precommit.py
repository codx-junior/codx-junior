"""Pre-commit secret gate.

Run it from a git hook::

    PYTHONPATH=<repo>/api python -m codx.junior.security.ciso.precommit

Exit codes
    0   the staged diff is clean (or has nothing to scan),
    1   a credential was found: the commit must be stopped,
    3   the scanner could not run (missing dependencies, no git): the hook
        decides whether that is fatal (``CISO_PRECOMMIT_STRICT=1``).

Runs anywhere
    This module and everything it imports are stdlib-only, so the gate works
    on a bare ``python3``: a fresh clone, a PR worktree (``api/.venv`` is
    never committed), a container image or CI. Needing the application
    dependency tree (pydantic, fastapi, the LLM clients) would make the gate
    die with an ImportError precisely at commit time, which is how it used to
    break; ``tests/security/ciso_import_check.py`` locks that property in.

What may block
    A credential and nothing else: the blob *is* the leak, wherever it lands.
    A commit stores content and executes nothing, and a human is reading the
    diff, so destructive or exfiltrating *content* is reported without blocking.
    Blocking on it would turn the hook into an obstacle — the project's own
    installer pipes a download into ``bash`` — and an obstacle is what teaches
    ``--no-verify``, which disables the check for the one finding that matters.

Approving one commit
    A finding is sometimes content that must be committed: a quotation inside a
    task card, a fixture, a documented example. There is deliberately NO path
    allowlist: an allowlist grants a file permission forever, so a card that
    later gains a real token would be committed silently. ``--approve`` instead
    fingerprints the staged content (mode and blob sha of every flagged file),
    stores that fingerprint inside the git dir and lets ONE commit through: the
    record is spent when used, and any later edit changes the fingerprint, so the
    same card is reviewed again. ``--revoke`` cancels it, ``--status`` shows it
    and ``--ttl`` bounds its lifetime.

Why the check belongs here
    Remote push protection fires *after* the commit exists: the secret is
    already a blob in the local object database, reachable from any branch and
    from the reflog, and stopping it then costs a history rewrite plus a
    credential rotation. This gate runs before the object is created, needs no
    network, works with any remote (GitHub, GitLab, self-hosted) and scans
    source files, not only committed history.

Made with ❤️ by codx-junior
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Any, List, Optional

#: Exit code when the staged changes are clean.
EXIT_OK = 0
#: Exit code when a finding blocks the commit.
EXIT_BLOCKED = 1
#: Exit code when the gate could not run at all.
EXIT_UNAVAILABLE = 3

#: ``--unified=0`` keeps the diff small; ``scan_diff`` reads added lines only.
DIFF_COMMAND = ["git", "diff", "--cached", "--unified=0", "--no-color"]

#: Where a one-time commit approval is stored. It lives inside the GIT DIR, so
#: it is never tracked, never committed and never shared with a clone: an
#: approval is a decision about local content, not a policy in the repository.
APPROVAL_FILE_NAME = "ciso-commit-approval.json"
#: How long an approval stays usable. It is spent by the first commit that uses
#: it; the deadline only stops a forgotten approval from lingering.
DEFAULT_APPROVAL_TTL = 1800

#: Paths whose content is prose or data, not executable code. A credential
#: blocks here too (see :func:`blocking_findings`); only the executable
#: families are downgraded to a report.
PROSE_PREFIXES = (
    ".codx/tasks/",
    ".codx/chat_turns/",
    ".codx/wiki/",
    ".codx/profiles/",
    "wiki/",
    "docs/",
)
#: Extensions that hold documentation or configuration data.
PROSE_SUFFIXES = (
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
)


def staged_diff() -> str:
    """Return the staged patch (``git diff --cached``).

    Returns:
        str: Unified diff of the changes the next commit would include.

    Raises:
        OSError: ``git`` is not available.
        subprocess.CalledProcessError: the command failed (not a repository).
    """
    completed = subprocess.run(
        DIFF_COMMAND,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout or ""


def git_dir() -> str:
    """Return the repository's git directory, where approvals are stored.

    Returns:
        str: Absolute path of ``.git`` (or of the worktree's git dir).

    Raises:
        OSError: ``git`` is not available.
        subprocess.CalledProcessError: not a git repository.
    """
    completed = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    return (completed.stdout or "").strip()


def index_fingerprint(findings: List) -> str:
    """Fingerprint the flagged files exactly as they are staged right now.

    The digest covers mode, blob sha, stage and path of every flagged file, so
    it changes whenever the content that would be committed changes. A task card
    rewritten by the running API invalidates the approval and the change is
    reviewed again — the permission is per commit, never per file or per path.
    The fingerprint never contains the credential itself.

    Args:
        findings: The blocking findings, used only for their paths.

    Returns:
        str: Hex sha256, or ``""`` when no found finding carries a path.

    Raises:
        OSError: ``git`` is not available.
        subprocess.CalledProcessError: the command failed.
    """
    paths = sorted(
        {(getattr(finding, "path", "") or "") for finding in findings} - {""}
    )
    if not paths:
        return ""
    completed = subprocess.run(
        ["git", "ls-files", "-s", "--", *paths],
        capture_output=True,
        text=True,
        check=True,
    )
    entries = sorted(
        line for line in (completed.stdout or "").splitlines() if line.strip()
    )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def approval_path() -> str:
    """Return the absolute path of the one-time approval file."""
    return os.path.join(git_dir(), APPROVAL_FILE_NAME)


def read_approval() -> Optional[dict]:
    """Read the pending approval, if any.

    Returns:
        Optional[dict]: The stored record, or ``None`` when there is none or it
        cannot be parsed (a corrupt store must never approve anything).
    """
    try:
        with open(approval_path(), "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def clear_approval() -> bool:
    """Delete the pending approval.

    Returns:
        bool: ``True`` when a record was removed.
    """
    try:
        os.remove(approval_path())
    except OSError:
        return False
    return True


def record_approval(
    findings: List,
    fingerprint: str,
    ttl: int = DEFAULT_APPROVAL_TTL,
) -> dict:
    """Approve the CURRENT staged content for exactly one commit.

    The record stores what was reviewed (paths, rule ids, fingerprint) and never
    the credential, so the approval itself cannot become a leak. It replaces any
    previous approval: there is only ever one pending decision.

    Args:
        findings: The blocking findings that a human decided to accept.
        fingerprint: Value from :func:`index_fingerprint`.
        ttl: Seconds the approval stays usable.

    Returns:
        dict: The record written to the git dir.
    """
    now = time.time()
    record = {
        "fingerprint": fingerprint,
        "paths": sorted(
            {(getattr(finding, "path", "") or "") for finding in findings} - {""}
        ),
        "rules": sorted(
            {(getattr(finding, "rule_id", "") or "") for finding in findings} - {""}
        ),
        "findings": len(findings),
        "created_at": int(now),
        "expires_at": int(now + max(int(ttl), 60)),
    }
    target = approval_path()
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp_path = f"{target}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, target)
    return record


def consume_approval(fingerprint: str, now: Optional[float] = None) -> bool:
    """Spend the pending approval when it matches the staged content.

    One approval authorises one commit: on a match the record is deleted, so the
    next commit is reviewed again even when it stages byte-identical content. A
    record that does not match (the file changed after approval) is kept but
    does not apply, and an expired one is dropped.

    Args:
        fingerprint: Value from :func:`index_fingerprint` for the current index.
        now: Current epoch seconds (injectable for tests).

    Returns:
        bool: ``True`` when the commit is approved and the record consumed.
    """
    record = read_approval()
    if not record:
        return False
    current = now if now is not None else time.time()
    if float(record.get("expires_at", 0) or 0) < current:
        clear_approval()
        return False
    if not fingerprint or record.get("fingerprint") != fingerprint:
        return False
    clear_approval()
    return True


def _parse_ttl(argv: List[str]) -> int:
    """Return the ``--ttl`` value found in *argv*.

    Args:
        argv: Command line arguments.

    Returns:
        int: Lifetime in seconds, at least 60; the default when absent/invalid.
    """
    for index, token in enumerate(argv):
        value = ""
        if token == "--ttl" and index + 1 < len(argv):
            value = argv[index + 1]
        elif token.startswith("--ttl="):
            value = token.split("=", 1)[1]
        if value:
            try:
                return max(int(value), 60)
            except ValueError:
                return DEFAULT_APPROVAL_TTL
    return DEFAULT_APPROVAL_TTL


def package_prefix() -> str:
    """Return the repo-relative folder of the CISO package itself.

    Returns:
        str: ``"<path>/"`` (with a trailing slash), or ``""`` when the package
        is not inside the repository being committed.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    root = (completed.stdout or "").strip()
    if not root:
        return ""
    relative = os.path.relpath(here, root).replace(os.sep, "/")
    if relative.startswith(".."):
        return ""
    return f"{relative}/"


def excluded_prefixes() -> tuple:
    """Return the repo-relative folders this gate does not scan.

    Two folders must CONTAIN the patterns the detectors look for:

    * the CISO package itself — rule tables and their documentation,
    * ``<api>/tests/security`` — fixtures with fake AWS keys, a reverse shell,
      ``rm -rf /`` and ``ignore previous instructions``.

    Every secret scanner needs such an allowlist: without it the gate blocks
    its own maintenance, and people learn to reach for ``--no-verify``, which
    disables the protection everywhere.

    Returns:
        tuple: Path prefixes (with trailing slash) to skip.
    """
    package = package_prefix()
    if not package:
        return ()
    api_root = package.split("/", 1)[0]
    return (package, f"{api_root}/tests/security/")


def relevant_findings(findings: List) -> List:
    """Drop findings located inside the scanner or its fixtures.

    Args:
        findings: Findings produced by :func:`guard_diff`.

    Returns:
        List: Findings that must still block the commit.
    """
    prefixes = excluded_prefixes()
    if not prefixes:
        return list(findings)
    return [
        finding
        for finding in findings
        if not any(
            (getattr(finding, "path", "") or "").replace(os.sep, "/").startswith(prefix)
            for prefix in prefixes
        )
    ]


def blocking_findings(findings: List, categories: Any) -> List:
    """Return the findings allowed to block the commit: credentials only.

    The gate has exactly one job — no credential may become a git object — so
    a credential blocks wherever it lands, and everything else is reported
    without blocking. Destructive or exfiltrating *content* cannot leak a
    secret, a human is reading the diff, and blocking on it turns the hook into
    an obstacle: the project's own installer pipes a download into ``bash``, and
    a gate that refuses to commit it is a gate people run with ``--no-verify``.

    Args:
        findings: Findings produced by :func:`guard_diff`, already filtered.
        categories: The ``SecurityCategory`` enum of the CISO models.

    Returns:
        List: Findings that decide the verdict.
    """
    return [
        finding
        for finding in findings
        if getattr(finding, "category", None) == categories.SECRET
    ]


def main(argv: Optional[list] = None) -> int:
    """Scan the staged changes and decide whether the commit may proceed.

    None of the flags changes what is detected — they only record the decision
    a human already took:

    ``--approve``   approve the EXACT staged content for one commit,
    ``--revoke``    drop a pending approval,
    ``--status``    print the pending approval,
    ``--ttl N``     approval lifetime in seconds (default 1800).

    Args:
        argv: Command line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: One of :data:`EXIT_OK`, :data:`EXIT_BLOCKED`, :data:`EXIT_UNAVAILABLE`.
    """
    args = list(argv if argv is not None else sys.argv[1:])

    if "--revoke" in args:
        try:
            removed = clear_approval()
        except (OSError, subprocess.CalledProcessError):
            removed = False
        print(
            "CISO: pending approval revoked."
            if removed
            else "CISO: there is no pending approval."
        )
        return EXIT_OK

    if "--status" in args:
        try:
            record = read_approval()
        except (OSError, subprocess.CalledProcessError):
            record = None
        if not record:
            print("CISO: no pending approval.")
            return EXIT_OK
        remaining = int(float(record.get("expires_at", 0) or 0) - time.time())
        print(
            f"CISO: approval pending for ONE commit — {record.get('findings', 0)} "
            f"finding(s) in {', '.join(record.get('paths', []))} "
            f"(rules: {', '.join(record.get('rules', []))}), "
            f"expires in {max(remaining, 0)}s."
        )
        return EXIT_OK

    try:
        from codx.junior.security.ciso import gate
        from codx.junior.security.ciso import policy as ciso_policy
        from codx.junior.security.ciso.models import SecurityCategory, SecurityDomain
    except Exception as ex:  # pragma: no cover - depends on the environment
        print(f"CISO: the secret scanner is not importable ({ex})", file=sys.stderr)
        return EXIT_UNAVAILABLE

    try:
        patch = staged_diff()
    except (OSError, subprocess.CalledProcessError) as ex:
        print(f"CISO: could not read the staged diff ({ex})", file=sys.stderr)
        return EXIT_UNAVAILABLE

    if not patch.strip():
        if "--approve" in args:
            print("CISO: nothing is staged; there is nothing to approve.")
        return EXIT_OK

    scanned = gate.guard_diff(patch)
    findings = relevant_findings(scanned.findings)
    if not findings:
        if "--approve" in args:
            print("CISO: no finding in the staged changes; no approval is needed.")
        return EXIT_OK

    # Re-evaluate the ladder on the findings that survive the filter: keeping
    # the original decision would block on a rule the filter just dropped.
    blocking = blocking_findings(findings, SecurityCategory)
    others = len(findings) - len(blocking)
    if not blocking:
        print(
            f"CISO: {len(findings)} finding(s) in the staged diff, none of them "
            "a credential; nothing blocks."
        )
        return EXIT_OK

    try:
        fingerprint = index_fingerprint(blocking)
    except (OSError, subprocess.CalledProcessError) as ex:
        print(f"CISO: could not fingerprint the staged files ({ex})", file=sys.stderr)
        return EXIT_UNAVAILABLE

    if "--approve" in args:
        record = record_approval(blocking, fingerprint, _parse_ttl(args))
        print(
            f"CISO: approved {record['findings']} credential finding(s) for ONE commit."
            "\n  rules: " + ", ".join(record["rules"])
            + "\n  files: " + ", ".join(record["paths"])
            + f"\n  fingerprint: {fingerprint[:16]}…"
            + "\n  The approval is spent by the next commit that uses it and stops "
            "applying the moment the staged content changes, so later edits are "
            "reviewed again. Nothing is exempt by path."
        )
        return EXIT_OK

    if consume_approval(fingerprint):
        print(
            f"CISO: this exact staged content was approved for one commit "
            f"({len(blocking)} finding(s)); the approval is now spent."
        )
        return EXIT_OK

    policy = ciso_policy.resolve_policy(None)
    verdict = ciso_policy.evaluate(
        blocking,
        domain=SecurityDomain.GIT_COMMIT,
        policy=policy,
        rules_evaluated=scanned.rules_evaluated,
        truncated=scanned.truncated,
    )

    if others:
        print(
            f"CISO: {others} further finding(s) are not credentials and are "
            "reported, not blocking (a commit leaks only what it stores)."
        )
    print(
        ciso_policy.render_report(
            verdict,
            title="CISO pre-commit report",
            min_severity=policy.block_above,
        )
    )
    if verdict.blocked:
        print(
            "\nCISO: commit blocked. Remove the credential, rotate it (assume it "
            "is disclosed) and stage the fix.\n"
            "Inspect the staged changes with: git diff --cached\n"
            "If the finding is a quotation or a fixture that must be committed, "
            "approve THIS content for one commit (no path is ever exempt):\n"
            "  PYTHONPATH=api python3 -m codx.junior.security.ciso.precommit --approve\n"
            "Any later edit to the same file changes the fingerprint and is "
            "reviewed again.",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
