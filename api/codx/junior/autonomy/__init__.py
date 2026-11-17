"""
Autonomous profiles (actors).

A profile becomes an **actor** when its ``actor`` block is enabled: it gets a
role, boards, a weekly availability window, a cadence and hard budgets, and the
:class:`~codx.junior.autonomy.worker.ActorWorker` wakes it on schedule so it can
work without a human in the loop.

Layout:

* :mod:`.constants` — switches, defaults, statuses, board-only tool allowlist.
* :mod:`.models` — the profile configuration, runtime state and scheduling
  vocabulary (``"+2d"``).
* :mod:`.store` — per-project persistence (state, heartbeats, shift locks).
* :mod:`.prompts` — the shift contract (initiative, agenda, report).
* :mod:`.service` — one bounded shift: board -> agenda -> agent turn -> report.
* :mod:`.worker` — the background thread that takes the due shifts.

The user-facing surface is the ``autonomy_management`` tool and the
``/api/actors`` router (``/api/autonomy`` is the hidden legacy alias).

Made with ❤️ by codx-junior
"""

from .constants import AUTONOMY_DIR  # noqa: F401
from .models import (  # noqa: F401
    ActorConfig,
    ActorState,
    AgendaItem,
    AutonomyConfig,
    AutonomyState,
    ShiftReport,
    default_board_name,
    parse_when,
)
from .service import ActorService, AutonomyService  # noqa: F401
from .store import AutonomyStore  # noqa: F401

__all__ = [
    "AUTONOMY_DIR",
    "ActorConfig",
    "ActorService",
    "ActorState",
    "AgendaItem",
    "AutonomyConfig",
    "AutonomyService",
    "AutonomyState",
    "AutonomyStore",
    "ShiftReport",
    "default_board_name",
    "parse_when",
]

# Made with ❤️ by codx-junior
