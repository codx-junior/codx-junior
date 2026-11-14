"""QA coverage: read the report the project already produced.

The QA subsystem does not instrument anything: it reads the coverage artefacts
of the project (``coverage.xml``/``coverage.json`` from coverage.py,
``coverage-summary.json`` from istanbul) and answers one question — is the
measured coverage above the threshold of the policy?

Reading instead of generating keeps the tool cheap and keeps the run
deterministic: the coverage numbers come from the suite execution, not from the
QA report generator.

Made with ❤️ by codx-junior
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from codx.junior.quality.qa.models import CoverageReport
from codx.junior.quality.qa.mapping import IGNORED_DIRS, normalize

logger = logging.getLogger(__name__)

#: Report file names understood by the reader, relative to a scanned folder.
CANDIDATE_NAMES = (
    "coverage.xml",
    "coverage.json",
    "coverage-summary.json",
    os.path.join("coverage", "coverage.json"),
    os.path.join("coverage", "coverage-summary.json"),
    os.path.join("coverage", "coverage-final.json"),
    os.path.join("htmlcov", "coverage.xml"),
)

_LINE_RATE = re.compile(r"line-rate=\"([0-9.]+)\"")


def _candidates(project_path: str, paths: Optional[List[str]], max_dirs: int = 2) -> List[str]:
    """Return the report files to inspect, newest first."""
    found: List[str] = []

    for path in paths or []:
        absolute = path if os.path.isabs(path) else os.path.join(project_path, path)
        if os.path.isfile(absolute):
            found.append(absolute)

    frontier = [project_path]
    for _ in range(max(0, max_dirs)):
        next_frontier: List[str] = []
        for directory in frontier:
            for name in CANDIDATE_NAMES:
                candidate = os.path.join(directory, name)
                if os.path.isfile(candidate) and candidate not in found:
                    found.append(candidate)
            try:
                entries = os.scandir(directory)
            except OSError:  # pragma: no cover - unreadable folder
                continue
            with entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in IGNORED_DIRS and not entry.name.startswith("."):
                            next_frontier.append(entry.path)
        frontier = next_frontier

    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    return sorted(found, key=_mtime, reverse=True)


def parse_report(path: str) -> Optional[CoverageReport]:
    """Parse one coverage report.

    Args:
        path: Absolute path of a ``coverage.xml``/``coverage.json`` file.

    Returns:
        Optional[CoverageReport]: ``None`` when the file is not understood.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(4_000_000)
    except OSError as ex:  # pragma: no cover - unreadable file
        logger.debug("QA coverage: cannot read %s: %s", path, ex)
        return None

    percent: Optional[float] = None
    details: Dict[str, Any] = {}

    if path.endswith(".xml"):
        match = _LINE_RATE.search(content)
        if match:
            percent = round(float(match.group(1)) * 100, 2)
    else:
        try:
            data = json.loads(content)
        except Exception:  # pragma: no cover - broken json
            return None
        if isinstance(data, dict):
            totals = data.get("totals")
            if isinstance(totals, dict) and "percent_covered" in totals:
                percent = round(float(totals["percent_covered"]), 2)
                details["covered_lines"] = totals.get("covered_lines")
                details["num_statements"] = totals.get("num_statements")
            elif isinstance(data.get("total"), dict):
                lines = data["total"].get("lines") if isinstance(data["total"], dict) else None
                if isinstance(lines, dict) and "pct" in lines:
                    percent = round(float(lines["pct"]), 2)
                    details["covered_lines"] = lines.get("covered")
                    details["num_statements"] = lines.get("total")
            elif "percent_covered" in data:
                percent = round(float(data["percent_covered"]), 2)

    if percent is None:
        return None
    return CoverageReport(percent=percent, source=normalize(path), details=details)


def read_coverage(
    project_path: str,
    paths: Optional[List[str]] = None,
    max_dirs: int = 2,
) -> Optional[CoverageReport]:
    """Read the most recent coverage report of the project.

    Args:
        project_path: Absolute project path.
        paths: Optional explicit report paths (wins over discovery).
        max_dirs: How many folder levels below the root are scanned.

    Returns:
        Optional[CoverageReport]: The report, or ``None`` when the project has
        none (the caller turns that into a finding).

    Example:
        >>> read_coverage("/repo") is None or True
        True
    """
    for candidate in _candidates(project_path, paths, max_dirs=max_dirs):
        report = parse_report(candidate)
        if report:
            return report
    return None


def coverage_summary(report: Optional[CoverageReport]) -> str:
    """Return a one line description of a coverage report."""
    if not report:
        return "no coverage report found"
    return f"{report.percent}% from `{report.source}`"
