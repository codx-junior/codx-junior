"""Durable storage of guardian runs, findings, suppressions and review cache.

Runs are append-only records: one JSON file per run plus a bounded index with
the summaries the UI lists. They are deliberately *not* stored in
``project.json``: a run can be large, it is generated data and it must never
travel with the project settings (which also hold credentials).

Layout::

    .codx/quality/
      policy.json          # the GuardianPolicy overrides
      runs/
        index.json         # newest first, bounded summaries
        <run-id>.json      # full run (files, checks, findings, verdict)
      suppressions.json    # dismissed findings that must not come back
      cache.json           # short-lived AI review cache (same diff + rules)

Every write is atomic (``tmp`` + ``os.replace``) and serialised with the
project-wide quality file lock, so the API process and the background worker can
run guardians at the same time without corrupting the index.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from codx.junior.utils.file_lock import interprocess_lock

from .models import (
    GuardianSeverity,
    make_verdict,
    normalize_severity,
    utc_now,
)
from .policy import quality_path, resolve_policy

logger = logging.getLogger(__name__)

RUNS_FOLDER = "runs"
RUN_INDEX_FILE = "index.json"
SUPPRESSIONS_FILE = "suppressions.json"
CACHE_FILE = "cache.json"

#: Maximum summaries kept in the run index. The full run files are pruned too.
MAX_RUNS = 100

#: Maximum cached AI reviews (each entry can be a few KB).
MAX_CACHE_ENTRIES = 200

#: Cached AI reviews older than this are ignored.
CACHE_TTL_SECONDS = 24 * 60 * 60

#: Run ids are file names: keep them boring and impossible to traverse.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")


def _read_json(path: str, default: Any) -> Any:
    """Read a JSON file, returning *default* on any error."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write_json(path: str, value: Any) -> None:
    """Atomically write *value* as JSON, creating its folder."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def _run_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    """Return the bounded summary of a run stored in the index."""
    verdict = run.get("verdict") or {}
    return {
        "id": run.get("id", ""),
        "created_at": run.get("created_at", ""),
        "trigger": run.get("trigger", ""),
        "scope": run.get("scope", ""),
        "mode": run.get("mode", ""),
        "guardians": list(run.get("guardians") or []),
        "file_count": len(run.get("files") or []),
        "finding_count": len(
            [
                finding for finding in (run.get("findings") or [])
                if str(finding.get("status") or "open") == "open"
            ]
        ),
        "action": verdict.get("action", "allow"),
        "highest_severity": verdict.get("highest_severity", ""),
        "ai_calls": run.get("ai_calls", 0),
        "ai_cost": run.get("ai_cost", 0),
        "duration_seconds": run.get("duration_seconds", 0),
        "errors": list(run.get("errors") or [])[:5],
    }


def _recompute_verdict(run: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute a run verdict after its findings changed status."""
    thresholds = run.get("policy") if isinstance(run.get("policy"), dict) else {}
    verdict = make_verdict(
        list(run.get("findings") or []),
        block_above=thresholds.get("block_above", "high"),
        approval_above=thresholds.get("approval_above", "medium"),
        warn_above=thresholds.get("warn_above", "low"),
    )
    run["verdict"] = verdict
    return verdict


class RunStore:
    """Read/write the guardian state of one project."""

    def __init__(self, settings: Any):
        self.settings = settings
        self.root = quality_path(settings)
        self.runs_path = os.path.join(self.root, RUNS_FOLDER)
        self.index_path = os.path.join(self.runs_path, RUN_INDEX_FILE)
        self.suppressions_path = os.path.join(self.root, SUPPRESSIONS_FILE)
        self.cache_path = os.path.join(self.root, CACHE_FILE)
        self.lock_path = quality_path(settings, ".lock")

    # ── Runs ──────────────────────────────────────────────────────────────
    def save_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a run and prepend its summary to the index.

        Args:
            run: Run dict produced by the runner.

        Returns:
            Dict: The stored run (same object, after normalisation).

        Raises:
            ValueError: When the run has no valid id.
        """
        run_id = str(run.get("id") or "").strip()
        if not RUN_ID_PATTERN.match(run_id):
            raise ValueError(f"Invalid guardian run id: {run_id!r}")

        run.setdefault("created_at", utc_now())
        run.setdefault("schema", 1)
        run["finding_count"] = len(
            [
                finding for finding in (run.get("findings") or [])
                if str(finding.get("status") or "open") == "open"
            ]
        )
        if not isinstance(run.get("verdict"), dict):
            _recompute_verdict(run)

        with interprocess_lock(self.lock_path):
            os.makedirs(self.runs_path, exist_ok=True)
            _write_json(os.path.join(self.runs_path, f"{run_id}.json"), run)

            index = _read_json(self.index_path, {"version": 1, "runs": []})
            summaries = [
                item for item in (index.get("runs") or [])
                if isinstance(item, dict) and item.get("id") != run_id
            ]
            summaries.insert(0, _run_summary(run))
            summaries = summaries[:MAX_RUNS]
            _write_json(self.index_path, {"version": 1, "runs": summaries})
            self._prune_runs({item.get("id") for item in summaries})
        return run

    def _prune_runs(self, keep_ids: set) -> None:
        """Delete run files that fell out of the index (best effort)."""
        try:
            names = os.listdir(self.runs_path)
        except OSError:
            return
        for name in names:
            if not name.endswith(".json") or name == RUN_INDEX_FILE:
                continue
            run_id = name[:-5]
            if run_id in keep_ids:
                continue
            try:
                os.remove(os.path.join(self.runs_path, name))
            except OSError as ex:  # pragma: no cover - best effort
                logger.debug("Cannot prune guardian run %s: %s", name, ex)

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the newest run summaries (bounded by *limit*)."""
        index = _read_json(self.index_path, {"version": 1, "runs": []})
        runs = [item for item in (index.get("runs") or []) if isinstance(item, dict)]
        try:
            limit = max(1, min(int(limit), MAX_RUNS))
        except (TypeError, ValueError):
            limit = 20
        return runs[:limit]

    def read_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Read one full run (``None`` when it does not exist)."""
        run_id = str(run_id or "").strip()
        if not RUN_ID_PATTERN.match(run_id):
            return None
        data = _read_json(os.path.join(self.runs_path, f"{run_id}.json"), None)
        return data if isinstance(data, dict) else None

    def latest_run(self, guardian: Optional[str] = None, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return the newest run, optionally filtered by guardian/path."""
        for summary in self.list_runs(limit=MAX_RUNS):
            if guardian and guardian not in (summary.get("guardians") or []):
                continue
            run = self.read_run(str(summary.get("id") or ""))
            if not run:
                continue
            if path and not any(
                str(item.get("path") or "") == str(path).replace("\\", "/").lstrip("./")
                for item in (run.get("files") or [])
            ):
                continue
            return run
        return None

    def update_finding(
        self,
        run_id: str,
        finding_id: str,
        status: str,
        *,
        reason: str = "",
        user: str = "",
        suppression_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Change the lifecycle status of one finding inside a run.

        Args:
            run_id: Run that owns the finding.
            finding_id: Finding fingerprint.
            status: ``open``, ``dismissed``, ``fixed`` or ``suppressed``.
            reason: Why the status changed (kept on the finding).
            user: Who changed it.
            suppression_id: Suppression that justifies a ``suppressed`` status.

        Returns:
            Optional[Dict]: The updated run, or ``None`` when it does not exist.
        """
        run = self.read_run(run_id)
        if not run:
            return None

        allowed = {"open", "dismissed", "fixed", "suppressed"}
        status = str(status or "").strip().lower()
        if status not in allowed:
            raise ValueError(f"Invalid finding status: {status!r}")

        changed = False
        for finding in run.get("findings") or []:
            if str(finding.get("id") or "") != str(finding_id or ""):
                continue
            finding["status"] = status
            finding["status_reason"] = str(reason or "").strip()[:1000]
            finding["status_user"] = str(user or "").strip()
            finding["status_at"] = utc_now()
            if suppression_id:
                finding["suppression_id"] = suppression_id
            changed = True
            break
        if not changed:
            return run

        _recompute_verdict(run)
        run["updated_at"] = utc_now()
        with interprocess_lock(self.lock_path):
            _write_json(os.path.join(self.runs_path, f"{run_id}.json"), run)
            index = _read_json(self.index_path, {"version": 1, "runs": []})
            summaries = []
            for item in index.get("runs") or []:
                if isinstance(item, dict) and item.get("id") == run_id:
                    summaries.append(_run_summary(run))
                else:
                    summaries.append(item)
            _write_json(self.index_path, {"version": 1, "runs": summaries})
        return run

    # ── Suppressions ──────────────────────────────────────────────────────
    def suppressions(self) -> List[Dict[str, Any]]:
        """Return the stored suppressions."""
        data = _read_json(self.suppressions_path, {"version": 1, "items": []})
        items = data.get("items") if isinstance(data, dict) else []
        return [item for item in (items or []) if isinstance(item, dict)]

    def save_suppressions(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace the suppression list."""
        with interprocess_lock(self.lock_path):
            _write_json(self.suppressions_path, {"version": 1, "items": items})
        return items

    def add_suppression(self, suppression: Dict[str, Any]) -> Dict[str, Any]:
        """Add (or replace by id) one suppression."""
        item = dict(suppression)
        item.setdefault("id", f"sup-{int(time.time() * 1000):x}")
        item.setdefault("created_at", utc_now())
        with interprocess_lock(self.lock_path):
            data = _read_json(self.suppressions_path, {"version": 1, "items": []})
            items = [
                existing for existing in (data.get("items") or [])
                if isinstance(existing, dict) and existing.get("id") != item["id"]
            ]
            items.append(item)
            _write_json(self.suppressions_path, {"version": 1, "items": items})
        return item

    def remove_suppression(self, suppression_id: str) -> bool:
        """Remove a suppression by id; returns whether it existed."""
        with interprocess_lock(self.lock_path):
            data = _read_json(self.suppressions_path, {"version": 1, "items": []})
            items = [item for item in (data.get("items") or []) if isinstance(item, dict)]
            kept = [item for item in items if item.get("id") != suppression_id]
            if len(kept) == len(items):
                return False
            _write_json(self.suppressions_path, {"version": 1, "items": kept})
        return True

    def suppression_for(self, finding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return the suppression that covers *finding*, if any.

        A suppression matches when every criterion it declares matches. That
        lets a project suppress one fingerprint, a whole rule on one file, or a
        guardian's rule everywhere, without inventing three different syntaxes.
        """
        now = utc_now()
        for item in reversed(self.suppressions()):
            if item.get("expires_at") and str(item["expires_at"]) <= now:
                continue
            if item.get("finding_id") and item["finding_id"] != finding.get("id"):
                continue
            if item.get("guardian") and item["guardian"] != finding.get("guardian"):
                continue
            if item.get("rule_id") and item["rule_id"] != finding.get("rule_id"):
                continue
            path = str(finding.get("path") or "").replace("\\", "/").lstrip("./")
            if item.get("path") and not fnmatch.fnmatch(path, str(item["path"])):
                continue
            if item.get("title") and str(item["title"]).strip().lower() != str(finding.get("title") or "").strip().lower():
                continue
            return item
        return None

    # ── AI review cache ──────────────────────────────────────────────────
    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return a cached AI review for *key* when it is still fresh."""
        if not key:
            return None
        data = _read_json(self.cache_path, {"version": 1, "entries": {}})
        entries = data.get("entries") if isinstance(data, dict) else {}
        entry = (entries or {}).get(key)
        if not isinstance(entry, dict):
            return None
        try:
            created = float(entry.get("created_at_ts") or 0)
        except (TypeError, ValueError):
            return None
        if time.time() - created > CACHE_TTL_SECONDS:
            return None
        value = entry.get("value")
        return value if isinstance(value, dict) else None

    def cache_put(self, key: str, value: Dict[str, Any]) -> None:
        """Store one AI review result, pruning the oldest entries."""
        if not key:
            return
        with interprocess_lock(self.lock_path):
            data = _read_json(self.cache_path, {"version": 1, "entries": {}})
            entries = data.get("entries") if isinstance(data, dict) else {}
            entries = dict(entries or {})
            entries[key] = {"created_at_ts": time.time(), "value": value}
            if len(entries) > MAX_CACHE_ENTRIES:
                ordered = sorted(
                    entries.items(),
                    key=lambda item: float((item[1] or {}).get("created_at_ts") or 0),
                )
                entries = dict(ordered[-MAX_CACHE_ENTRIES:])
            _write_json(self.cache_path, {"version": 1, "entries": entries})

    # ── Summary used by the UI ───────────────────────────────────────────
    def summary(self) -> Dict[str, Any]:
        """Return the aggregate state the Quality panel renders.

        Returns:
            Dict: latest runs, counts by severity/guardian, suppressions and
            the policy in force.
        """
        runs = self.list_runs(limit=MAX_RUNS)
        latest = self.read_run(str(runs[0].get("id") or "")) if runs else None
        findings = (latest or {}).get("findings") or []
        by_severity: Dict[str, int] = {severity.value: 0 for severity in GuardianSeverity}
        by_guardian: Dict[str, int] = {}
        for finding in findings:
            if str(finding.get("status") or "open") != "open":
                continue
            severity = normalize_severity(finding.get("severity"))
            by_severity[severity.value] = by_severity.get(severity.value, 0) + 1
            guardian = str(finding.get("guardian") or "")
            by_guardian[guardian] = by_guardian.get(guardian, 0) + 1
        return {
            "policy": resolve_policy(self.settings).to_dict(),
            "runs": runs,
            "latest": latest,
            "latest_run_id": (latest or {}).get("id", ""),
            "open_findings": sum(by_severity.values()),
            "by_severity": by_severity,
            "by_guardian": by_guardian,
            "suppressions": self.suppressions(),
        }


# Made with ❤️ by codx-junior
