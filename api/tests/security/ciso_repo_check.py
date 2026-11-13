"""CISO repository hygiene check — the data plane must not be committed.

The gates wired into the platform stop a secret from being *written*; this check
stops the other half of the incident: **runtime state entering git**. It is a
gate, not a report, so it fails when:

* a ``.codx`` data-plane entry is tracked by git (the leaked-task-card path),
* ``.gitignore`` and ``runtime.DATA_PLANE_ENTRIES`` drift apart (a new runtime
  folder that nobody ignores),
* a tracked ``.codx`` file contains a credential (historical leaks included),
* the data plane cannot be relocated outside the repository.

Run it in CI and before a release::

    PYTHONPATH=<repo>/api python <repo>/api/tests/security/ciso_repo_check.py

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

API_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = os.path.dirname(API_ROOT)
if API_ROOT not in sys.path:  # pragma: no cover - direct script execution
    sys.path.insert(0, API_ROOT)

from codx.junior.runtime import (  # noqa: E402
    DATA_PLANE_ENTRIES,
    ENV_DATA_PATH,
    data_dir,
    data_root,
)
from codx.junior.security.ciso import audit as ciso_audit  # noqa: E402
from codx.junior.security.ciso.models import (  # noqa: E402
    SecurityCategory,
    SecuritySeverity,
)

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record and print one check."""
    if condition:
        print(f"PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        print(f"FAIL  {name}" + (f"  → {detail}" if detail else ""))
        FAILURES.append(name)


def git(*args: str) -> str:
    """Run git in the repository root and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    """Run every hygiene check.

    Args:
        argv: Command line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: ``0`` when the repository is clean, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="ciso_repo_check",
        description="Verify that runtime state never reaches git.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help=(
            "Also scan the tracked .codx files for credentials. Off by default "
            "because task cards can be megabytes; run it in a nightly job."
        ),
    )
    args = parser.parse_args(argv)

    print("CISO repository hygiene check")
    print("=" * 40)
    print(f"repository: {REPO_ROOT}")

    ignore_text = ""
    ignore_path = os.path.join(REPO_ROOT, ".gitignore")
    if os.path.isfile(ignore_path):
        with open(ignore_path, "r", encoding="utf-8") as handle:
            ignore_text = handle.read()

    # -- 1. every runtime folder is ignored, for real ---------------------
    for entry in DATA_PLANE_ENTRIES:
        relative = f".codx/{entry}"
        check(
            f"ignored: {relative}",
            bool(relative.rstrip("/") in ignore_text),
            "no rule in .gitignore",
        )

    # -- 2. git agrees with the rules (not just the file content) ---------
    def ignored_by_git(entry: str) -> bool:
        """Return True when git ignores *entry* (file or folder)."""
        probe = f".codx/{entry}"
        candidates = [probe]
        if "." not in entry:
            # A folder rule (``.codx/cache/``) only matches a real directory, and
            # git cannot know a not-yet-created path is one: probe a child too.
            candidates.append(f"{probe}/probe")
        return any(
            subprocess.run(
                ["git", "check-ignore", "-q", "--no-index", candidate],
                cwd=REPO_ROOT,
                check=False,
            ).returncode
            == 0
            for candidate in candidates
        )

    not_ignored = [
        entry for entry in DATA_PLANE_ENTRIES if not ignored_by_git(entry)
    ]
    check(
        "git applies the ignore rules",
        not not_ignored,
        f"not ignored: {', '.join(not_ignored)}",
    )

    # -- 3. nothing of the data plane is tracked --------------------------
    tracked = [line for line in git("ls-files", ".codx").splitlines() if line]
    offenders = [
        path
        for path in tracked
        if any(
            path == f".codx/{entry}" or path.startswith(f".codx/{entry.rstrip('/')}/")
            for entry in DATA_PLANE_ENTRIES
        )
    ]
    check(
        "no data-plane file is tracked by git",
        not offenders,
        f"{len(offenders)} tracked: {', '.join(offenders[:5])}",
    )
    print(f"      tracked .codx files remaining: {len(tracked)}")

    # -- 4. the data plane can be relocated outside the repository --------
    codx_path = os.path.join(REPO_ROOT, ".codx")
    default_dir = data_dir(codx_path, "chat_turns")
    check(
        "data plane defaults to .codx (bind mounts keep working)",
        default_dir == os.path.join(codx_path, "chat_turns"),
        default_dir,
    )
    with tempfile.TemporaryDirectory() as temporary:
        previous = os.environ.get(ENV_DATA_PATH)
        os.environ[ENV_DATA_PATH] = temporary
        try:
            relocated = data_dir(codx_path, "chat_turns")
        finally:
            if previous is None:
                os.environ.pop(ENV_DATA_PATH, None)
            else:
                os.environ[ENV_DATA_PATH] = previous
        check(
            "data plane relocates outside the repository",
            relocated.startswith(os.path.abspath(temporary))
            and os.path.isdir(relocated),
            relocated,
        )
    check(
        "relocated root is derived from the project, not the cwd",
        data_root(codx_path) == os.path.abspath(codx_path),
        data_root(codx_path),
    )

    # -- 5. the tracked half of .codx is secret free ----------------------
    if tracked and args.scan:
        # Scan exactly what git would publish, not the whole .codx tree: the
        # untracked data plane is runtime state and is covered by the gates.
        report, findings = ciso_audit.triage(
            paths=[os.path.join(REPO_ROOT, path) for path in tracked],
            project_path=REPO_ROOT,
            max_files=len(tracked),
            min_severity=SecuritySeverity.HIGH,
            max_rows=25,
        )
        # The gate blocks on the category this task is about: a credential in a
        # tracked artifact. Prompt-injection findings inside *security
        # documentation* (a profile that declares the attack strings it forbids)
        # are expected, so they are reported but do not fail the gate.
        credentials = [
            finding
            for finding in findings
            if finding.category
            in (SecurityCategory.SECRET, SecurityCategory.DATA_EXFILTRATION)
        ]
        check(
            "tracked .codx files carry no credential (>= high)",
            not credentials,
            f"{len(credentials)} credential finding(s) — see the triage report below",
        )
        other = len(findings) - len(credentials)
        if other:
            print(
                f"      note: {other} non-credential finding(s) at or above high "
                "(expected in security documentation)"
            )
        if credentials:
            print()
            print(report)

    print("=" * 40)
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
