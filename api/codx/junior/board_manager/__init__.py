"""
Board manager: the board declaration of an actor.

A board can associate a profile (``board.manager.profile``, falling back to the
board assistant) and a cadence. That is the **whole** feature: the review itself
runs on the actor runtime (:mod:`codx.junior.autonomy.service`), exactly like an
autonomous profile, with the ``manager`` role and the ``board_only`` policy.

The JSON action contract that used to live here (``{"summary", "actions": []}``
plus an applier) is gone: the manager uses the ordinary task tools, so its
changes are board changes like any other.

Public surface:

* :class:`BoardManagerConfig` / :class:`ManagerState` — configuration and state.
* :class:`BoardManagerService` — one review, status and scheduling.
* :class:`BoardManagerStore` — atomic runtime state.

There is **no worker here any more**: the single background worker of the actor
runtime drives both sources (:func:`codx.junior.autonomy.worker.start_actor_worker`).

Made with ❤️ by codx-junior
"""

from .constants import DEFAULT_PROFILE
from .models import BoardManagerConfig, ManagerState
from .service import BoardManagerService
from .store import BoardManagerStore

__all__ = [
    "DEFAULT_PROFILE",
    "BoardManagerConfig",
    "BoardManagerService",
    "BoardManagerStore",
    "ManagerState",
]

# Made with ❤️ by codx-junior
