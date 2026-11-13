"""Repository audit: what is already committed?

The gates stop new secrets and the pre-commit hook stops a commit. Neither can
clean a repository that already contains them, and the most valuable artifact of
a security review is the list of what is *there today*.

Scope it at a tracked data plane, not the whole tree::

    PYTHONPATH=<repo>/api python -m codx.junior.security.ciso.audit .codx \
        --project-path . --max-files 2000

The audit only reads: nothing is modified and every finding carries redacted
evidence, so the report can be attached to a ticket as it is.

Made with ❤️ by codx-junior
"""

import argparse
import os
import sys
from typing import List, Optional, Sequence

from codx.junior.security.ciso import policy as ciso_policy
from codx.junior.security.ciso import scanner
from codx.junior.security.ciso.models import SecurityDomain


def collect_targets(
    paths: Sequence[str],
    project_path: str = ".",
    max_files: int = 2000,
) -> List[str]:
    """Expand *paths* (files or folders) into a bounded list of files.

    Args:
        paths: Project-relative or absolute files/folders.
        project_path: Project root used to resolve relative paths.
        max_files: Maximum number of files returned.

    Returns:
        List[str]: Absolute file paths.
    """
    policy = ciso_policy.resolve_policy(None)
    targets: List[str] = []
    for path in paths:
        absolute = path if os.path.isabs(path) else os.path.join(project_path, path)
        absolute = os.path.abspath(absolute)
        if os.path.isdir(absolute):
            targets.extend(
                scanner.iter_project_files(absolute, max_files=max_files, policy=policy)
            )
        elif os.path.isfile(absolute):
            targets.append(absolute)
        else:
            print(f"CISO audit: '{path}' does not exist", file=sys.stderr)
        if len(targets) >= max_files:
            break
    return targets[:max_files]


def audit(
    paths: Sequence[str],
    project_path: str = ".",
    max_files: int = 2000,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
) -> str:
    """Scan *paths* and return a Markdown report.

    Args:
        paths: Project-relative folders or files to audit.
        project_path: Project root (findings are reported relative to it).
        max_files: Global file budget (the scan is bounded on purpose).
        domain: Domain recorded in the report.

    Returns:
        str: Markdown report; a single "No finding in" line when clean.
    """
    policy = ciso_policy.resolve_policy(None)
    targets = collect_targets(paths, project_path=project_path, max_files=max_files)
    scope = ", ".join(f"`{path}`" for path in paths)

    if not targets:
        return f"## CISO audit\n\nNothing to scan in {scope}"

    verdict = scanner.scan_project(
        project_path=os.path.abspath(project_path),
        max_files=max_files,
        domain=domain,
        policy=policy,
        paths=targets,
    )
    if not verdict.findings:
        return (
            f"## CISO audit\n\nNo finding in {scope} "
            f"({len(targets)} file(s) scanned)."
        )
    return ciso_policy.render_report(
        verdict,
        title=f"CISO audit — {scope}",
        max_findings=50,
    )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: ``0`` when nothing was found, ``1`` when the audit reported
        findings (so it can fail a pipeline).
    """
    parser = argparse.ArgumentParser(
        prog="ciso.audit",
        description="Scan project paths for credentials and malicious content.",
    )
    parser.add_argument("paths", nargs="+", help="Paths to audit (file or folder).")
    parser.add_argument("--project-path", default=".", help="Project root.")
    parser.add_argument("--max-files", type=int, default=2000, help="File budget.")
    args = parser.parse_args(argv)

    report = audit(
        paths=args.paths,
        project_path=args.project_path,
        max_files=args.max_files,
    )
    print(report)
    return 0 if "No finding in" in report or "Nothing to scan" in report else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
