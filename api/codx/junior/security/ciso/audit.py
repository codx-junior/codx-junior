"""Repository audit: what is already committed?

The gates stop new secrets and the pre-commit hook stops a commit. Neither can
clean a repository that already contains them, and the most valuable artifact of
a security review is the list of what is *there today*.

Scope it at a tracked data plane, not the whole tree::

    PYTHONPATH=<repo>/api python -m codx.junior.security.ciso.audit .codx \
        --project-path . --min-severity high

Two report shapes are available:

* ``--format triage`` (default) — the *clean* list: one row per rule and one row
  per file, ordered by severity, with the noise below ``--min-severity``
  suppressed. This is the list a human triages.
* ``--format report`` — the full finding list produced by the policy engine,
  useful when a single finding has to be inspected.

Both only read: nothing is modified and every finding carries redacted evidence,
so the report can be attached to a ticket as it is.

Made with ❤️ by codx-junior
"""

import argparse
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from codx.junior.security.ciso import policy as ciso_policy
from codx.junior.security.ciso import scanner, secrets
from codx.junior.security.ciso.models import (
    SecurityDomain,
    SecurityFinding,
    SecuritySeverity,
    SecurityVerdict,
    parse_severity,
    severity_rank,
)

#: Default severity floor of the triage view. ``medium`` is the level of the
#: entropy heuristic, whose false positives bury everything else.
DEFAULT_MIN_SEVERITY = SecuritySeverity.HIGH


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


def scan(
    paths: Sequence[str],
    project_path: str = ".",
    max_files: int = 2000,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
) -> Tuple[SecurityVerdict, List[str]]:
    """Scan *paths* once and return the verdict together with the files read.

    Args:
        paths: Project-relative folders or files to audit.
        project_path: Project root (findings are reported relative to it).
        max_files: Global file budget (the scan is bounded on purpose).
        domain: Domain recorded in the report.

    Returns:
        Tuple[SecurityVerdict, List[str]]: Aggregated verdict and the scanned
        absolute paths.
    """
    policy = ciso_policy.resolve_policy(None)
    targets = collect_targets(paths, project_path=project_path, max_files=max_files)
    if not targets:
        return SecurityVerdict(domain=domain), targets
    verdict = scanner.scan_project(
        project_path=os.path.abspath(project_path),
        max_files=max_files,
        domain=domain,
        policy=policy,
        paths=targets,
    )
    return verdict, targets


def redact_targets(
    paths: Sequence[str],
    project_path: str = ".",
    max_files: int = 2000,
    apply: bool = False,
    max_file_bytes: int = 32 * 1024 * 1024,
) -> Tuple[List[Tuple[str, int]], int]:
    """Neutralise the credentials found in *paths* (dry run by default).

    The rewrite is textual, never a re-serialisation: a task card keeps its
    structure byte for byte and only the credential itself is replaced, so the
    JSON stays valid and the cleanup diff stays reviewable.

    Args:
        paths: Project-relative files or folders to clean.
        project_path: Project root used to resolve relative paths.
        max_files: Global file budget.
        apply: Write the changes (``False`` only reports what would change).
        max_file_bytes: Files larger than this are skipped and reported.

    Returns:
        Tuple[List[Tuple[str, int]], int]: ``(path, replacements)`` for every
        changed file, and the number of files skipped for their size.
    """
    policy = ciso_policy.resolve_policy(None)
    changed: List[Tuple[str, int]] = []
    skipped = 0
    for target in collect_targets(paths, project_path=project_path, max_files=max_files):
        try:
            size = os.path.getsize(target)
        except OSError:
            continue
        if size > max_file_bytes:
            skipped += 1
            print(
                f"CISO: '{target}' is {size} bytes; skipped (raise --max-file-bytes)",
                file=sys.stderr,
            )
            continue
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as ex:
            print(f"CISO: could not read '{target}' ({ex})", file=sys.stderr)
            continue
        redacted, count = secrets.redact_text(text, policy)
        if not count:
            continue
        changed.append((target, count))
        if apply:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(redacted)
    return changed, skipped


def audit(
    paths: Sequence[str],
    project_path: str = ".",
    max_files: int = 2000,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
    min_severity: Optional[SecuritySeverity] = None,
) -> str:
    """Scan *paths* and return a Markdown report.

    Args:
        paths: Project-relative folders or files to audit.
        project_path: Project root (findings are reported relative to it).
        max_files: Global file budget (the scan is bounded on purpose).
        domain: Domain recorded in the report.
        min_severity: Optional severity floor for the listed findings.

    Returns:
        str: Markdown report; a single "No finding in" line when clean.
    """
    verdict, targets = scan(
        paths, project_path=project_path, max_files=max_files, domain=domain
    )
    scope = ", ".join(f"`{path}`" for path in paths)

    if not targets:
        return f"## CISO audit\n\nNothing to scan in {scope}"
    if not verdict.findings:
        return (
            f"## CISO audit\n\nNo finding in {scope} "
            f"({len(targets)} file(s) scanned)."
        )
    return ciso_policy.render_report(
        verdict,
        title=f"CISO audit — {scope}",
        max_findings=50,
        min_severity=min_severity,
    )


def _relative(path: Optional[str], project_path: str) -> str:
    """Return *path* relative to *project_path*, with forward slashes."""
    if not path:
        return "-"
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(project_path))
    except ValueError:  # pragma: no cover - different drives on Windows
        rel = path
    return rel.replace(os.sep, "/")


def _worst(findings: Sequence[SecurityFinding]) -> Optional[SecuritySeverity]:
    """Return the worst severity of *findings*."""
    return max((f.severity for f in findings), key=severity_rank, default=None)


def triage(
    paths: Sequence[str],
    project_path: str = ".",
    max_files: int = 2000,
    domain: SecurityDomain = SecurityDomain.FILE_READ,
    min_severity: Optional[SecuritySeverity] = DEFAULT_MIN_SEVERITY,
    max_rows: int = 40,
) -> Tuple[str, List[SecurityFinding]]:
    """Scan *paths* and return a grouped, severity-filtered triage list.

    The triage view answers the two questions an engineer actually asks: *which
    rules fire* (so one fix can retire many findings) and *which files are
    affected* (so the work is assignable). Findings below *min_severity* are
    counted but not listed.

    Args:
        paths: Project-relative folders or files to audit.
        project_path: Project root (findings are reported relative to it).
        max_files: Global file budget.
        domain: Domain recorded in the report.
        min_severity: Severity floor for the listed findings.
        max_rows: Maximum rows per table.

    Returns:
        Tuple[str, List[SecurityFinding]]: The Markdown report and the findings
        at or above *min_severity* (empty means "nothing to triage").
    """
    verdict, targets = scan(
        paths, project_path=project_path, max_files=max_files, domain=domain
    )
    scope = ", ".join(f"`{path}`" for path in paths)
    kept = verdict.findings_at_least(min_severity)
    floor = min_severity.value if min_severity else "info"

    if not targets:
        return f"## CISO triage — {scope}\n\nNothing to scan.", []

    if not kept:
        return (
            f"## CISO triage — {scope}\n\n"
            f"No finding at or above `{floor}` in {len(targets)} scanned file(s) "
            f"({len(verdict.findings)} finding(s) below the floor)."
        ), []

    suppressed = len(verdict.findings) - len(kept)
    by_severity: Dict[str, int] = {}
    for finding in kept:
        by_severity[finding.severity.value] = by_severity.get(finding.severity.value, 0) + 1
    summary = ", ".join(
        f"{key} {value}"
        for key, value in sorted(
            by_severity.items(), key=lambda item: -severity_rank(parse_severity(item[0]))
        )
    )

    lines = [
        f"## CISO triage — {scope}",
        "",
        f"- Files scanned: {len(targets)}",
        f"- Findings at or above `{floor}`: **{len(kept)}** ({summary})",
        f"- Suppressed below the floor: {suppressed}",
        f"- Highest severity: **{(_worst(kept) or SecuritySeverity.INFO).value}**",
        f"- Rules evaluated: {verdict.rules_evaluated}",
        "",
    ]
    if verdict.truncated:
        lines += ["> ⚠️ The payload was truncated: only the beginning was scanned.", ""]

    # -- by rule: one fix can retire many findings ------------------------
    rules: Dict[str, List[SecurityFinding]] = {}
    for finding in kept:
        rules.setdefault(finding.rule_id, []).append(finding)
    lines += [
        "### By rule",
        "",
        "| Severity | Rule | Occurrences | Files | Remediation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rule_id, group in sorted(
        rules.items(),
        key=lambda item: (
            -severity_rank(_worst(item[1])),
            -len(item[1]),
            item[0],
        ),
    )[:max_rows]:
        files = {_relative(f.path, project_path) for f in group if f.path}
        remediation = (group[0].remediation or "").replace("|", "\\|")
        lines.append(
            f"| {(_worst(group) or SecuritySeverity.INFO).value} | `{rule_id}` | "
            f"{len(group)} | {len(files)} | {remediation} |"
        )
    if len(rules) > max_rows:
        lines.append(f"\n_{len(rules) - max_rows} more rule(s) omitted._")
    lines.append("")

    # -- by file: the assignable work list --------------------------------
    files_map: Dict[str, List[SecurityFinding]] = {}
    for finding in kept:
        files_map.setdefault(_relative(finding.path, project_path), []).append(finding)
    lines += [
        "### By file",
        "",
        "| # | File | Worst | Findings | First location | Rules |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(
        files_map.items(),
        key=lambda item: (-severity_rank(_worst(item[1])), -len(item[1]), item[0]),
    )
    for index, (path, group) in enumerate(ordered[:max_rows], start=1):
        first = min(group, key=lambda f: (f.line or 0, f.rule_id))
        location = f"`{path}:{first.line}`" if first.line else f"`{path}`"
        rules_in_file = sorted({f.rule_id for f in group})
        shown = ", ".join(f"`{rule}`" for rule in rules_in_file[:3])
        if len(rules_in_file) > 3:
            shown += f" +{len(rules_in_file) - 3}"
        lines.append(
            f"| {index} | `{path}` | {(_worst(group) or SecuritySeverity.INFO).value} | "
            f"{len(group)} | {location} | {shown} |"
        )
    if len(ordered) > max_rows:
        lines.append(f"\n_{len(ordered) - max_rows} more file(s) omitted._")

    lines += [
        "",
        "### Evidence (redacted)",
        "",
        "| Severity | Rule | Location | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for finding in kept[:max_rows]:
        location = _relative(finding.path, project_path)
        if finding.line:
            location = f"{location}:{finding.line}"
        evidence = (finding.evidence or finding.excerpt or "").replace("|", "\\|")
        lines.append(
            f"| {finding.severity.value} | `{finding.rule_id}` | `{location}` | {evidence} |"
        )
    lines += [
        "",
        "> A detected credential must be treated as disclosed: rotate it, then",
        "> remove it from the file. Evidence is redacted and cannot be reused.",
        "",
    ]
    return "\n".join(lines), kept


def _redact_main(args: argparse.Namespace) -> int:
    """Run the redaction mode and print its report.

    Args:
        args: Parsed command line arguments.

    Returns:
        int: ``0`` when nothing is left to redact, ``1`` otherwise (so a dry
        run can fail a pipeline that still carries credentials).
    """
    changed, skipped = redact_targets(
        paths=args.paths,
        project_path=args.project_path,
        max_files=args.max_files,
        apply=args.apply,
        max_file_bytes=args.max_file_bytes,
    )
    mode = "APPLIED" if args.apply else "DRY RUN (nothing written, add --apply)"
    total = sum(count for _, count in changed)
    lines = [
        "## CISO redaction — " + ", ".join(f"`{path}`" for path in args.paths),
        "",
        f"- Mode: **{mode}**",
        f"- Files with credentials: **{len(changed)}**",
        f"- Replacements: **{total}**",
    ]
    if skipped:
        lines.append(f"- Skipped for size: {skipped}")
    lines.append("")
    if not changed:
        print("\n".join(lines + ["No credential to redact.", ""]))
        return 0
    lines += ["| File | Replacements |", "| --- | --- |"]
    for path, count in changed:
        lines.append(f"| `{_relative(path, args.project_path)}` | {count} |")
    lines += [
        "",
        "> Redaction removes the copy, not the disclosure: rotate every",
        "> credential listed here, then commit the cleaned files.",
        "",
    ]
    print("\n".join(lines))
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: ``0`` when nothing was found at or above the severity floor,
        ``1`` otherwise, so the audit can fail a pipeline.
    """
    parser = argparse.ArgumentParser(
        prog="ciso.audit",
        description="Scan project paths for credentials and malicious content.",
    )
    parser.add_argument("paths", nargs="+", help="Paths to audit (file or folder).")
    parser.add_argument("--project-path", default=".", help="Project root.")
    parser.add_argument("--max-files", type=int, default=2000, help="File budget.")
    parser.add_argument(
        "--min-severity",
        default=DEFAULT_MIN_SEVERITY.value,
        choices=[severity.value for severity in SecuritySeverity],
        help="Severity floor: lower findings are counted but not listed.",
    )
    parser.add_argument(
        "--format",
        default="triage",
        choices=["triage", "report"],
        help="'triage' (grouped list) or 'report' (full finding list).",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help="Redact every credential found: reports, or rewrites with --apply.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --redact: rewrite the files instead of only reporting.",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=32 * 1024 * 1024,
        help="With --redact: skip files larger than this many bytes.",
    )
    args = parser.parse_args(argv)
    floor = parse_severity(args.min_severity)

    if args.redact:
        return _redact_main(args)

    if args.format == "report":
        report = audit(
            paths=args.paths,
            project_path=args.project_path,
            max_files=args.max_files,
            min_severity=floor,
        )
        print(report)
        clean = "No finding in" in report or "Nothing to scan" in report
        return 0 if clean else 1

    report, findings = triage(
        paths=args.paths,
        project_path=args.project_path,
        max_files=args.max_files,
        min_severity=floor,
    )
    print(report)
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
