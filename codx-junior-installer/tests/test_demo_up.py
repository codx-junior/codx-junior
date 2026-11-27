"""Bridge the shell test harness into the QA runner.

The QA runner in this environment only speaks `pytest` (and `npm`), while this
repository is shell + yaml: the real assertions live in `demo/tests/test_up.sh`
(TAP-like; the harness prints its own `1..N` plan, so this count never
rots). Without a bridge the runner collects zero tests, so a
change to `demo/up.sh` can never be verified — the "vacuous allow" problem
tracked by cards `d0a8b091` / `de08e8d9`.

This module runs the existing harness unchanged and fails the test when any
check fails, so `qa_run_tests` executes the real gate instead of guessing.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "demo" / "tests" / "test_up.sh"
GATE = ROOT / "tests" / "test_shell.sh"
INSTALL = ROOT / "tests" / "test_install.sh"


def _run(script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def test_demo_up_harness() -> None:
    """`demo/up.sh` behaviour: 17 checks, including --list-providers and --ref."""
    assert HARNESS.is_file(), f"missing harness: {HARNESS}"
    result = _run(HARNESS)
    report = (result.stdout or "") + (result.stderr or "")
    print(report, file=sys.stderr)
    assert result.returncode == 0, f"demo/tests/test_up.sh failed:\n{report}"


def test_shell_gate() -> None:
    """`tests/test_shell.sh`: bash -n + shellcheck + compose config over tracked files."""
    assert GATE.is_file(), f"missing gate: {GATE}"
    result = _run(GATE)
    report = (result.stdout or "") + (result.stderr or "")
    print(report, file=sys.stderr)
    assert result.returncode == 0, f"tests/test_shell.sh failed:\n{report}"


def test_install_harness() -> None:
    """`install.sh` CLI/behaviour: `tests/test_install.sh` (no Docker, no network)."""
    assert INSTALL.is_file(), f"missing harness: {INSTALL}"
    result = _run(INSTALL)
    report = (result.stdout or "") + (result.stderr or "")
    print(report, file=sys.stderr)
    assert result.returncode == 0, f"tests/test_install.sh failed:\n{report}"
