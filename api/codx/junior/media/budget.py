"""Cost gate for the Project's Channel media pipeline.

Tracks spending per run and per day and answers the single question a provider
call needs before running: *may I spend this estimate?*. Pure python, no
network and no configuration imports; the optional JSON file is only used to
mirror the counters so a restart does not forget what was already spent.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CURRENCY = "cxjcoins"
# Tolerance so 0.1 + 0.2 style additions do not trip an exact budget
_EPSILON = 1e-9


def _today() -> str:
    return date.today().isoformat()


def _unlimited(budget: float) -> bool:
    """``0.0`` (or negative) means "no limit"."""
    return budget <= 0.0


def _remaining(budget: float, spent: float) -> float:
    if _unlimited(budget):
        return float("inf")
    return max(0.0, budget - spent)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class BudgetDecision:
    """Outcome of ``BudgetGate.check``; truthiness mirrors ``allowed``."""

    allowed: bool
    reason: str = ""
    estimate: float = 0.0
    remaining_run: float = 0.0
    remaining_day: float = 0.0
    currency: str = DEFAULT_CURRENCY

    def __bool__(self) -> bool:
        return bool(self.allowed)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "estimate": self.estimate,
            "remaining_run": self.remaining_run,
            "remaining_day": self.remaining_day,
            "currency": self.currency,
        }


class BudgetGate:
    """In memory budget with an optional JSON mirror.

    ``run_budget`` and ``daily_budget`` are limits in ``currency``; ``0.0``
    means unlimited. The day counter rolls over automatically on date change.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        run_budget: float = 0.0,
        daily_budget: float = 0.0,
        currency: str = DEFAULT_CURRENCY,
    ) -> None:
        self.path: Optional[Path] = Path(path) if path else None
        self.run_budget = max(0.0, _as_float(run_budget))
        self.daily_budget = max(0.0, _as_float(daily_budget))
        self.currency = currency or DEFAULT_CURRENCY
        self._spent_run = 0.0
        self._spent_day = 0.0
        self._day = _today()
        self._load()

    # ------------------------------------------------------------------ state

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError:
            return
        try:
            data = json.loads(raw) if raw.strip() else {}
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self._spent_run = max(0.0, _as_float(data.get("spent_run")))
        self._spent_day = max(0.0, _as_float(data.get("spent_day")))
        self._day = str(data.get("day") or "") or _today()
        self._rollover()

    def _persist(self) -> None:
        """Atomic best effort mirror to ``path``; failures never break a job."""
        if self.path is None:
            return
        payload = {
            "currency": self.currency,
            "spent_run": self._spent_run,
            "spent_day": self._spent_day,
            "day": self._day,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle_fd, tmp_name = tempfile.mkstemp(
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                os.replace(tmp_name, self.path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError:
            return

    def _rollover(self) -> None:
        today = _today()
        if self._day != today:
            self._day = today
            self._spent_day = 0.0

    # ------------------------------------------------------------------- api

    def check(self, estimate: float, reason: str = "") -> BudgetDecision:
        """Decide whether ``estimate`` may be spent. Never raises."""
        try:
            cost = max(0.0, _as_float(estimate))
            self._rollover()
            label = reason or "media job"
            if (
                not _unlimited(self.run_budget)
                and self._spent_run + cost > self.run_budget + _EPSILON
            ):
                return self._decision(
                    False,
                    f"{label} needs {cost:.2f} {self.currency} but the run budget "
                    f"is {self.run_budget:.2f} (already spent {self._spent_run:.2f})",
                    cost,
                )
            if (
                not _unlimited(self.daily_budget)
                and self._spent_day + cost > self.daily_budget + _EPSILON
            ):
                return self._decision(
                    False,
                    f"{label} needs {cost:.2f} {self.currency} but the daily budget "
                    f"is {self.daily_budget:.2f} (already spent {self._spent_day:.2f})",
                    cost,
                )
            return self._decision(True, f"{label} within budget", cost)
        except Exception as exc:  # noqa: BLE001 - a gate must never raise
            return BudgetDecision(
                allowed=False,
                reason=f"budget check failed: {exc}",
                estimate=0.0,
                remaining_run=0.0,
                remaining_day=0.0,
                currency=self.currency,
            )

    def charge(self, actual: float) -> float:
        """Book ``actual`` spending and return the accumulated run total."""
        cost = max(0.0, _as_float(actual))
        self._rollover()
        self._spent_run += cost
        self._spent_day += cost
        self._persist()
        return self._spent_run

    def snapshot(self) -> Dict[str, Any]:
        self._rollover()
        return {
            "currency": self.currency,
            "run_budget": self.run_budget,
            "daily_budget": self.daily_budget,
            "spent_run": self._spent_run,
            "spent_day": self._spent_day,
            "remaining_run": _remaining(self.run_budget, self._spent_run),
            "remaining_day": _remaining(self.daily_budget, self._spent_day),
            "day": self._day,
            "path": str(self.path) if self.path else None,
        }

    def reset(self) -> None:
        self._spent_run = 0.0
        self._spent_day = 0.0
        self._day = _today()
        self._persist()

    # --------------------------------------------------------------- private

    def _decision(self, allowed: bool, reason: str, cost: float) -> BudgetDecision:
        return BudgetDecision(
            allowed=allowed,
            reason=reason,
            estimate=cost,
            remaining_run=_remaining(self.run_budget, self._spent_run),
            remaining_day=_remaining(self.daily_budget, self._spent_day),
            currency=self.currency,
        )
