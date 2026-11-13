"""Pre-commit secret gate.

Run it from a git hook::

    PYTHONPATH=<repo>/api python -m codx.junior.security.ciso.precommit

Exit codes
    0   the staged diff is clean (or has nothing to scan),
    1   a credential was found: the commit must be stopped,
    3   the scanner could not run (missing dependencies, no git): the hook
        decides whether that is fatal (``CISO_PRECOMMIT_STRICT=1``).

Why the check belongs here
    Remote push protection fires *after* the commit exists: the secret is
    already a blob in the local object database, reachable from any branch and
    from the reflog, and stopping it then costs a history rewrite plus a
    credential rotation. This gate runs before the object is created, needs no
    network, works with any remote (GitHub, GitLab, self-hosted) and scans
    source files, not only committed history.

Made with ❤️ by codx-junior
"""

import os
import subprocess
import sys
from typing import List, Optional

#: Exit code when the staged changes are clean.
EXIT_OK = 0
#: Exit code when a finding blocks the commit.
EXIT_BLOCKED = 1
#: Exit code when the gate could not run at all.
EXIT_UNAVAILABLE = 3

#: ``--unified=0`` keeps the diff small; ``scan_diff`` reads added lines only.
DIFF_COMMAND = ["git", "diff", "--cached", "--unified=0", "--no-color"]


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


def main(argv: Optional[list] = None) -> int:
    """Scan the staged changes and decide whether the commit may proceed.

    Args:
        argv: Unused (kept for ``python -m`` compatibility).

    Returns:
        int: One of :data:`EXIT_OK`, :data:`EXIT_BLOCKED`, :data:`EXIT_UNAVAILABLE`.
    """
    try:
        from codx.junior.security.ciso import gate
        from codx.junior.security.ciso import policy as ciso_policy
        from codx.junior.security.ciso.models import SecurityDomain
    except Exception as ex:  # pragma: no cover - depends on the environment
        print(f"CISO: the secret scanner is not importable ({ex})", file=sys.stderr)
        return EXIT_UNAVAILABLE

    try:
        patch = staged_diff()
    except (OSError, subprocess.CalledProcessError) as ex:
        print(f"CISO: could not read the staged diff ({ex})", file=sys.stderr)
        return EXIT_UNAVAILABLE

    if not patch.strip():
        return EXIT_OK

    scanned = gate.guard_diff(patch)
    findings = relevant_findings(scanned.findings)
    if not findings:
        return EXIT_OK

    # Re-evaluate the ladder on the findings that survive the filter: keeping
    # the original decision would block on a rule the filter just dropped.
    policy = ciso_policy.resolve_policy(None)
    verdict = ciso_policy.evaluate(
        findings,
        domain=SecurityDomain.GIT_COMMIT,
        policy=policy,
        rules_evaluated=scanned.rules_evaluated,
        truncated=scanned.truncated,
    )

    print(ciso_policy.render_report(verdict, title="CISO pre-commit report"))
    if verdict.blocked:
        print(
            "\nCISO: commit blocked. Remove the credential, rotate it (assume it "
            "is disclosed) and stage the fix.\n"
            "Inspect the staged changes with: git diff --cached",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
