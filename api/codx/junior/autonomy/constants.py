"""
Constants of the actor runtime.

An **actor** is a normal profile whose ``actor`` block is enabled. Once enabled
the profile stops being a passive collaborator and gets a *shift*: a background
worker wakes it on its own cadence (inside its weekly availability window),
puts its boards and its agenda (scheduled tasks) in front of it and lets it work
with its whole tool set, deciding by itself what to do.

The same runtime serves both actor roles:

* ``coworker`` — owns a job end to end ("CISO", "maintenance tickets") and
  works until the job is done or the budget is over;
* ``manager`` — curates the boards it is responsible for (this is what the old
  ``board_manager`` did with a JSON action contract, now removed).

This module is dependency free so the runtime, the API and the tests can import
it without the AI stack.

Made with ❤️ by codx-junior
"""

#: Profile document key holding the actor configuration.
ACTOR_CONFIG_KEY = "actor"

#: Legacy profile key, still read (and migrated on save).
LEGACY_CONFIG_KEY = "autonomy"

#: Backwards compatible alias.
PROFILE_CONFIG_KEY = ACTOR_CONFIG_KEY

#: Scope key of the per-project actor state folder (under ``<codx_path>``).
AUTONOMY_DIR = "autonomy"

#: Legacy: the module (and its folders) are still named ``autonomy``.
ACTORS_DIR = AUTONOMY_DIR

#: Storage layout inside :data:`AUTONOMY_DIR`.
STATE_FILE = "state.json"
WORKER_FILE = "worker.json"
LOCK_FILE = ".state.lock"
LOCKS_DIR = "locks"

#: Default board of an agent is named after the profile, e.g. ``Agent: Agenda``.
BOARD_PREFIX = "Agent: "

#: Columns created (once) on the board of an agent.
COLUMN_INBOX = "Inbox"
COLUMN_SCHEDULED = "Scheduled"
COLUMN_WORKING = "Working"
COLUMN_DONE = "Done"
DEFAULT_COLUMNS = [COLUMN_INBOX, COLUMN_SCHEDULED, COLUMN_WORKING, COLUMN_DONE]

#: Where a shift card is created and where it ends up.
SHIFT_COLUMN = COLUMN_WORKING
SHIFT_DONE_COLUMN = COLUMN_DONE

#: Default cadence: once a day.
DEFAULT_INTERVAL_MINUTES = 24 * 60
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 30 * 24 * 60

#: A shift cannot run forever (seconds).
DEFAULT_MAX_RUNTIME_MINUTES = 30
MIN_MAX_RUNTIME_MINUTES = 1
MAX_MAX_RUNTIME_MINUTES = 180

#: Background worker cadence (seconds).
DEFAULT_TICK_SECONDS = 30
MAX_TICK_SECONDS = 600

#: A worker heartbeat older than this means "no worker present".
WORKER_TTL_SECONDS = 120

#: A "running" shift older than this is considered abandoned.
STALE_SHIFT_SECONDS = 900

#: Modes. ``active`` uses the tools; ``dry_run`` only reports.
MODE_ACTIVE = "active"
MODE_DRY_RUN = "dry_run"
MODES = (MODE_ACTIVE, MODE_DRY_RUN)
DEFAULT_MODE = MODE_ACTIVE

#: Legacy board-manager modes mapped onto the actor modes.
MODE_ALIASES = {"apply": MODE_ACTIVE, "suggest": MODE_DRY_RUN}

#: Roles: what the actor is on duty for.
ROLE_COWORKER = "coworker"
ROLE_MANAGER = "manager"
ROLES = (ROLE_COWORKER, ROLE_MANAGER)
DEFAULT_ROLE = ROLE_COWORKER

#: Tool policy of a shift.
POLICY_FULL = "full"
POLICY_BOARD = "board_only"
POLICIES = (POLICY_FULL, POLICY_BOARD)
DEFAULT_POLICY = POLICY_FULL

#: Session shape: one turn, or turns until the work is done.
SESSION_SINGLE = "single"
SESSION_UNTIL_DONE = "until_done"
SESSIONS = (SESSION_SINGLE, SESSION_UNTIL_DONE)
DEFAULT_SESSION = SESSION_SINGLE

#: Maximum turns inside one ``until_done`` shift.
DEFAULT_MAX_TURNS = 6
MAX_TURNS_LIMIT = 40

#: Boards one actor may be responsible for.
MAX_BOARDS = 12

#: Daily budget (0 = no cap).
DEFAULT_DAILY_BUDGET = 0.0

#: Agenda: how many due tasks are put in front of the agent.
DEFAULT_MAX_AGENDA = 10
MAX_AGENDA_LIMIT = 50

#: How many tasks of the agent board are included as context.
DEFAULT_MAX_TASKS = 40
MAX_TASKS_LIMIT = 200

#: Weekly schedule.
DEFAULT_TIMEZONE = "UTC"
DEFAULT_WINDOW_START = "00:00"
DEFAULT_WINDOW_END = "23:59"
MAX_SCHEDULE_WINDOWS = 12
MAX_SCHEDULE_LOOKAHEAD_DAYS = 8
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_ALIASES = {
    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
    "fri": "fri", "sat": "sat", "sun": "sun",
    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
}

#: Why a shift stopped (``ShiftReport.stop_reason``).
STOP_DONE = "done"
STOP_SINGLE = "single_pass"
STOP_BUDGET = "budget_exhausted"
STOP_DAILY_BUDGET = "daily_budget_exhausted"
STOP_TURNS = "max_turns"
STOP_RUNTIME = "max_runtime"
STOP_SCHEDULE = "outside_schedule"

#: Shift statuses.
STATE_OK = "ok"
STATE_NO_WORK = "no_work"
STATE_SKIPPED = "skipped"
STATE_ERROR = "error"
STATE_BUDGET = "budget_exhausted"

#: Chat statuses that mean "the task is finished, never wake it again".
CLOSED_STATUSES = {"done", "closed", "completed", "rejected", "cancelled"}

#: Environment switches (the legacy AUTONOMY names are still honoured).
ENV_ENABLED = "CODX_JUNIOR_ACTORS"
ENV_LEGACY_ENABLED = "CODX_JUNIOR_AUTONOMY"
ENV_WORKER = "CODX_JUNIOR_ACTORS_WORKER"
ENV_LEGACY_WORKER = "CODX_JUNIOR_AUTONOMY_WORKER"
ENV_TICK_SECONDS = "CODX_JUNIOR_ACTORS_TICK_SECONDS"
ENV_LEGACY_TICK_SECONDS = "CODX_JUNIOR_AUTONOMY_TICK_SECONDS"
ENV_MAX_AGENTS = "CODX_JUNIOR_ACTORS_MAX_AGENTS"
ENV_LEGACY_MAX_AGENTS = "CODX_JUNIOR_AUTONOMY_MAX_AGENTS"
ENV_SCHEDULE = "CODX_JUNIOR_ACTORS_SCHEDULE"

#: Scheduling helpers: the natural-language offsets an agent may use.
SCHEDULE_UNITS = {
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 7 * 86400,
    "week": 7 * 86400,
    "weeks": 7 * 86400,
}

# Made with ❤️ by codx-junior
