"""
Autonomous profiles (agents).

A profile becomes an **agent** when its ``autonomy`` block is enabled: it gets a
board of its own, a cadence, a mandate and the full tool set, and the
:class:`~codx.junior.autonomy.worker.AutonomyWorker` wakes it on schedule so it
can work without a human in the loop.

Layout:

* :mod:`.constants` — switches, defaults, statuses (dependency free).
* :mod:`.models` — the profile configuration, the runtime state and the
  scheduling vocabulary (``"+2d"``).
* :mod:`.store` — per-project persistence (state, heartbeat, shift locks).
* :mod:`.prompts` — the shift contract (initiative, agenda, report).
* :mod:`.service` — one bounded shift: board -> agenda -> agent turn -> report.
* :mod:`.worker` — the background thread that takes the due shifts.

The user-facing surface is the ``autonomy_management`` tool and the
``/api/autonomy`` router.

Made with ❤️ by codx-junior
"""

from .constants import AUTONOMY_DIR  # noqa: F401
from .models import (  # noqa: F401
    AgendaItem,
    AutonomyConfig,
    AutonomyState,
    ShiftReport,
    default_board_name,
    parse_when,
)
from .service import AutonomyService  # noqa: F401
from .store import AutonomyStore  # noqa: F401

__all__ = [
    "AUTONOMY_DIR",
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
