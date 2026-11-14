"""QA flakiness: a test that passes twice and fails once is a defect.

Re-running a suite is the only honest way to detect a flaky test, so this
module does exactly that: run N times, diff the failing sets. A test that fails
in *some* runs but not in all of them is flaky; a test that fails in every run
is simply broken (the regular ``tests.failed`` finding covers it).

Made with ❤️ by codx-junior
"""

import logging
from typing import List, Optional

from codx.junior.quality.qa import runner
from codx.junior.quality.qa.models import QaSuite, TestResult, TestRun

logger = logging.getLogger(__name__)

#: Upper bound for the number of repetitions requested in one check.
MAX_REPEATS = 10


def flaky_results(runs: List[TestRun]) -> List[TestResult]:
    """Return the tests that failed in some runs but not in all of them.

    Args:
        runs: Runs of the same suite.

    Returns:
        List[TestResult]: One ``flaky`` result per unstable test.

    Example:
        >>> flaky_results([])
        []
    """
    if len(runs) < 2:
        return []
    if any(run.error or run.timed_out for run in runs):
        return []

    counts = {}
    for run in runs:
        for failure in run.failures:
            entry = counts.setdefault(failure.id, {"count": 0, "status": failure.status})
            entry["count"] += 1

    flaky: List[TestResult] = []
    for test_id, entry in sorted(counts.items()):
        if entry["count"] < len(runs):
            flaky.append(
                TestResult(
                    id=test_id,
                    status="flaky",
                    message=f"failed in {entry['count']} of {len(runs)} runs",
                )
            )
    return flaky


def detect_flaky(
    suite: QaSuite,
    project_path: str,
    paths: Optional[List[str]] = None,
    repeats: int = 3,
    timeout: Optional[int] = None,
    extra_args: Optional[List[str]] = None,
    settings=None,
) -> List[TestRun]:
    """Run *suite* repeatedly and return every run.

    Args:
        suite: Suite to execute.
        project_path: Absolute project path.
        paths: Optional test files / folders to restrict the runs.
        repeats: Number of runs (clamped to :data:`MAX_REPEATS`).
        timeout: Seconds per run.
        extra_args: Extra arguments (validated by the runner).
        settings: Project settings (used by the CISO command gate).

    Returns:
        List[TestRun]: Every run, in order.
    """
    total = max(1, min(int(repeats or 3), MAX_REPEATS))
    runs: List[TestRun] = []
    for index in range(total):
        run = runner.run_suite(
            suite,
            project_path,
            paths=paths,
            timeout=timeout,
            extra_args=extra_args,
            settings=settings,
        )
        logger.debug("QA flaky check: run %s/%s -> %s", index + 1, total, run.summary)
        runs.append(run)
        if run.error or run.timed_out:
            break
    return runs
