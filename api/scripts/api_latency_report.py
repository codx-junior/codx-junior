#!/usr/bin/env python3
"""Rank API routes by real latency, straight from the JSONL perf store.

Companion of ``audit_async_blocking.py``: that one finds the *code* that can
block the event loop, this one shows the *impact* on the endpoints. It reads
the write-behind files produced by ``codx.junior.api_logs.storage``
(``<YYYY-MM-DD>_api_calls.jsonl``) and prints, per route:

* call count
* p50 / p95 / max duration
* total seconds spent (the real contribution to "everything feels slow")

Usage::

    python api_latency_report.py                # today's file
    python api_latency_report.py --days 3       # last three days
    python api_latency_report.py --top 25

The script is read-only and never touches the running API.
"""
import argparse
import glob
import json
import os
import statistics
import sys
import time
from typing import Dict, List

DEFAULT_BASE = "/home/codx-junior/.codx-junior/api_logs"


def _pick(row: Dict, *names, default=None):
    """Return the first present key (the event schema drifted over time)."""
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def _route(row: Dict) -> str:
    route = _pick(row, "path", "route", "endpoint", "url", default="?")
    method = _pick(row, "method", default="")
    return f"{method} {route}".strip()


def _duration_ms(row: Dict) -> float:
    """Duration in ms, accepting seconds or milliseconds in the payload."""
    value = _pick(
        row,
        "duration_ms",
        "duration_seconds",
        "duration",
        "elapsed_ms",
        "duration_s",
    )
    if value is None:
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    # ``duration`` has historically been stored in seconds.
    return value * 1000.0 if value < 100 else value


def _status(row: Dict):
    value = _pick(row, "status", "status_code", "response_status")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_rows(base_path: str, days: int) -> List[Dict]:
    files = sorted(glob.glob(os.path.join(base_path, "*_api_calls.jsonl")))
    if not files:
        return []
    rows: List[Dict] = []
    for path in files[-max(1, days):]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as ex:
            print(f"warn: cannot read {path}: {ex}", file=sys.stderr)
    return rows


def report(rows: List[Dict], top: int) -> str:
    if not rows:
        return "No API events found (is CODX_JUNIOR_API_LOGS_DATA_PATH correct?)"

    by_route: Dict[str, List[float]] = {}
    errors: Dict[str, int] = {}
    for row in rows:
        route = _route(row)
        by_route.setdefault(route, []).append(_duration_ms(row))
        status = _status(row)
        if status is not None and status >= 500:
            errors[route] = errors.get(route, 0) + 1

    lines = [f"Events: {len(rows)} across {len(by_route)} routes", ""]

    def _pct(values: List[float], q: float) -> float:
        if len(values) == 1:
            return values[0]
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
        return ordered[index]

    def _table(title: str, key) -> None:
        lines.append(title)
        lines.append(f"{'route':<58}{'n':>6}{'p50':>9}{'p95':>9}{'max':>10}{'total s':>10}{'5xx':>5}")
        for route, values in sorted(by_route.items(), key=key)[:top]:
            lines.append(
                f"{route[:57]:<58}{len(values):>6}"
                f"{_pct(values, 0.50):>9.1f}{_pct(values, 0.95):>9.1f}"
                f"{max(values):>10.1f}{sum(values) / 1000.0:>10.1f}"
                f"{errors.get(route, 0):>5}"
            )
        lines.append("")

    _table("Top routes by TOTAL time (what makes the UI feel slow)",
           key=lambda item: sum(item[1]))
    _table("Top routes by MAX single call (blocking / spikes)",
           key=lambda item: max(item[1]))

    slow = [row for row in rows if _duration_ms(row) >= 1000]
    lines.append(f"Calls >= 1000 ms: {len(slow)} ({len(slow) * 100 / len(rows):.1f}% of traffic)")
    if slow:
        lines.append("")
        lines.append("Slowest individual calls")
        lines.append(f"{'route':<58}{'ms':>10}{'status':>8}")
        for row in sorted(slow, key=_duration_ms, reverse=True)[:top]:
            lines.append(
                f"{_route(row)[:57]:<58}{_duration_ms(row):>10.1f}"
                f"{str(_status(row) or ''):>8}"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get(
        "CODX_JUNIOR_API_LOGS_DATA_PATH", DEFAULT_BASE))
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--minutes", type=int, default=0,
                        help="only events from the last N minutes (0 = all)")
    args = parser.parse_args()

    rows = load_rows(args.base, args.days)
    if args.minutes > 0:
        cutoff = time.time() - args.minutes * 60
        rows = [row for row in rows if float(_pick(row, "timestamp", default=0) or 0) >= cutoff]
        print(f"Window: last {args.minutes} min")
    print(report(rows, args.top))
    if rows:
        durations = sorted(_duration_ms(row) for row in rows)
        print(f"\nGlobal p50={statistics.median(durations):.1f} ms "
              f"p95={durations[int(len(durations) * 0.95)]:.1f} ms "
              f"max={durations[-1]:.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
