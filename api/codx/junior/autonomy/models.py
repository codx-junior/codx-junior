"""
Data model of the actor runtime.

An **actor** is a profile that works on its own: it has a role, one or more
boards, a cadence, a weekly availability window and a budget. Three things live
here:

* :class:`ActorConfig` — the *declaration*. It is stored inside the profile
  document (``<Name>.profile`` -> ``"actor": {...}``), so an actor is just a
  profile with initiative switched on, and it inherits everything the profile
  already has (linked profiles, tools, knowledge, model). The legacy
  ``autonomy`` key is still read, and the legacy ``board.manager`` block of a
  board is mapped onto an actor by :mod:`codx.junior.board_manager.service`.
* :class:`WeeklySchedule` — **when** the actor is allowed to work. Autonomous
  profiles are powerful and can be wrong; a schedule is how a team keeps them
  out of the hours where nobody is online to review. Disabled (the default)
  means "always available".
* :class:`ActorState` — the *runtime* (last shift, cost, daily spend, agenda).
  It lives in the project state store, never in the profile, so a shift never
  rewrites the profile catalogue.

:func:`parse_when` is the small scheduling vocabulary shared by every entry
point (tools, API, tests): ``"+2d"``, ``"3 hours"``, ``"in 90m"``, an ISO date
or an ISO instant.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from . import constants as C

try:  # Python 3.9+ ; the container always has tzdata for the common zones.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback for a stripped runtime
    ZoneInfo = None  # type: ignore[assignment]


def utcnow() -> datetime:
    """Return the current aware UTC instant."""
    return datetime.now(tz=timezone.utc)


def utcnow_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string."""
    return utcnow().isoformat()


def parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO instant (or date) into an aware UTC datetime."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_WHEN_RE = re.compile(
    r"^\s*(?:in\s+)?(?:\+|after\s+)?(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)?\s*$",
    re.I,
)


def parse_when(value: Any, *, now: Optional[datetime] = None) -> str:
    """
    Resolve a scheduling expression into an ISO-8601 UTC instant.

    Accepted forms (case insensitive):

    * ``"+2d"``, ``"2 days"``, ``"in 90m"``, ``"3 hours"``, ``"1w"``
    * a bare number, interpreted as days
    * an ISO date (``2026-04-01``) -> that day at 00:00 UTC
    * an ISO instant (``2026-04-01T09:30:00``), naive means UTC
    * an empty value -> ``""`` (no schedule)

    Args:
        value: The expression to resolve.
        now: Optional reference instant (tests).

    Returns:
        The ISO instant, or ``""`` when the value cannot be understood.

    Example:
        >>> parse_when("+2d", now=datetime(2026, 4, 1, tzinfo=timezone.utc))
        '2026-04-03T00:00:00+00:00'
    """
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()

    text = str(value or "").strip()
    if not text:
        return ""

    parsed = parse_iso(text)
    if parsed is not None:
        return parsed.isoformat()

    match = _WHEN_RE.match(text)
    if not match:
        return ""
    try:
        amount = float(match.group(1))
    except (TypeError, ValueError):
        return ""
    unit = (match.group(2) or "d").strip().lower().rstrip("s") or "d"
    # Accept both the plural spelling and the short alias.
    seconds = C.SCHEDULE_UNITS.get(unit) or C.SCHEDULE_UNITS.get(unit + "s")
    if not seconds:
        return ""
    reference = now or utcnow()
    return (reference + timedelta(seconds=amount * seconds)).isoformat()


def is_due(value: Any, now: Optional[datetime] = None) -> bool:
    """Return True when the ISO instant *value* is in the past (empty = not due)."""
    moment = parse_iso(value)
    if moment is None:
        return False
    return moment <= (now or utcnow())


# ---------------------------------------------------------------------------
# Weekly availability
# ---------------------------------------------------------------------------


def timezone_for(name: Any) -> tzinfo:
    """Resolve an IANA timezone name, falling back to UTC."""
    key = str(name or "").strip()
    if ZoneInfo is not None and key:
        try:
            return ZoneInfo(key)
        except Exception:  # unknown zone, missing tzdata, ...
            pass
    if key.upper() in ("UTC", "GMT", "Z", ""):
        return timezone.utc
    return timezone.utc


def _parse_hhmm(value: Any) -> Optional[int]:
    """Parse ``"HH:MM"`` (or ``"H"``) into minutes since midnight."""
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^(\d{1,2})(?::(\d{1,2}))?$", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    return min(24 * 60, hour * 60 + minute)


def _format_hhmm(minutes: int) -> str:
    """Format minutes since midnight as ``"HH:MM"``."""
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _normalize_weekday(value: Any) -> str:
    """Map a weekday name/number onto ``mon``..``sun``."""
    text = str(value or "").strip().lower()[:3]
    return C.WEEKDAY_ALIASES.get(text, "")


class ScheduleWindow(BaseModel):
    """
    A recurring availability window.

    Attributes:
        days: Weekdays the window applies to (``mon``..``sun``). Empty means
            every day.
        start: Opening time ``"HH:MM"`` (actor local time).
        end: Closing time ``"HH:MM"``. ``end <= start`` is an **overnight**
            window: it opens on the listed days and closes the morning after
            (e.g. ``22:00``–``06:00`` is the night shift).
    """

    days: List[str] = Field(default_factory=list)
    start: str = Field(default=C.DEFAULT_WINDOW_START)
    end: str = Field(default=C.DEFAULT_WINDOW_END)

    def normalized(self) -> "ScheduleWindow":
        """Return a clamped copy."""
        days: List[str] = []
        for raw in self.days or []:
            day = _normalize_weekday(raw)
            if day and day not in days:
                days.append(day)
        start = _parse_hhmm(self.start)
        end = _parse_hhmm(self.end)
        return ScheduleWindow(
            days=[day for day in C.WEEKDAYS if day in days],
            start=_format_hhmm(start if start is not None else 0),
            end=_format_hhmm(end if end is not None else 24 * 60 - 1),
        )

    def open_at(self, day: str, minutes: int) -> bool:
        """Return True when *day*/*minutes* (actor local time) is inside."""
        window = self.normalized()
        start = _parse_hhmm(window.start) or 0
        end = _parse_hhmm(window.end)
        if end is None:
            end = 24 * 60
        days = window.days or list(C.WEEKDAYS)
        if start < end:
            return day in days and start <= minutes < end
        # Overnight: the tail belongs to the previous day.
        if minutes >= start:
            return day in days
        previous = C.WEEKDAYS[(C.WEEKDAYS.index(day) - 1) % len(C.WEEKDAYS)]
        return minutes < end and previous in days


class WeeklySchedule(BaseModel):
    """
    When an actor is allowed to work.

    Disabled (the default) means **always available**, which is what an agent
    needs to catch up after a downtime. When enabled, the actor only takes
    shifts inside the windows; a due task that falls outside is *kept* and
    handled at the next opening (its ``wake_at`` is not consumed).

    Attributes:
        enabled: Whether the windows are enforced.
        timezone: IANA zone used to read the windows (``Europe/Madrid``, …).
        windows: The recurring windows. Empty + enabled means "never".
    """

    enabled: bool = False
    timezone: str = Field(default=C.DEFAULT_TIMEZONE)
    windows: List[ScheduleWindow] = Field(default_factory=list)

    def normalized(self) -> "WeeklySchedule":
        """Return a clamped copy (never mutate the input)."""
        windows: List[ScheduleWindow] = []
        for index, window in enumerate(self.windows or []):
            if index >= C.MAX_SCHEDULE_WINDOWS:
                break
            if isinstance(window, ScheduleWindow):
                windows.append(window.normalized())
            elif isinstance(window, dict):
                windows.append(ScheduleWindow(**window).normalized())
        zone = str(self.timezone or C.DEFAULT_TIMEZONE).strip()[:64]
        if zone.upper() not in ("UTC", "GMT", "Z") and ZoneInfo is not None:
            try:
                ZoneInfo(zone)
            except Exception:
                zone = C.DEFAULT_TIMEZONE
        return WeeklySchedule(
            enabled=bool(self.enabled), timezone=zone, windows=windows
        )

    def is_open(self, moment: Optional[datetime] = None) -> bool:
        """Return True when the actor may work at *moment*."""
        schedule = self.normalized()
        if not schedule.enabled:
            return True
        if not schedule.windows:
            return False
        local = (moment or utcnow()).astimezone(timezone_for(schedule.timezone))
        day = C.WEEKDAYS[local.weekday()]
        minutes = local.hour * 60 + local.minute
        return any(window.open_at(day, minutes) for window in schedule.windows)

    def next_opening(self, moment: Optional[datetime] = None) -> Optional[datetime]:
        """
        Return the first instant at or after *moment* when the actor may work.

        Returns ``None`` when the schedule is enabled but has no window (the
        actor is effectively paused). Disabled schedules return *moment*.
        """
        schedule = self.normalized()
        now = moment or utcnow()
        if not schedule.enabled:
            return now
        if not schedule.windows:
            return None
        if schedule.is_open(now):
            return now
        zone = timezone_for(schedule.timezone)
        local = now.astimezone(zone)
        for offset in range(0, C.MAX_SCHEDULE_LOOKAHEAD_DAYS):
            day_date: date = local.date() + timedelta(days=offset)
            day = C.WEEKDAYS[day_date.weekday()]
            for window in schedule.windows:
                if window.days and day not in window.days:
                    continue
                start = _parse_hhmm(window.start) or 0
                candidate = datetime(
                    day_date.year, day_date.month, day_date.day, tzinfo=zone
                ) + timedelta(minutes=start)
                if candidate > local:
                    return candidate.astimezone(timezone.utc)
        return None

    def describe(self) -> str:
        """Return a short human description (used in the prompts and the API)."""
        schedule = self.normalized()
        if not schedule.enabled:
            return "always available (no schedule)"
        if not schedule.windows:
            return "paused (schedule enabled without windows)"
        parts: List[str] = []
        for window in schedule.windows:
            days = ", ".join(window.days) if window.days else "every day"
            parts.append(f"{days} {window.start}-{window.end}")
        return f"{'; '.join(parts)} ({schedule.timezone})"


# ---------------------------------------------------------------------------
# Configuration (stored inside the profile)
# ---------------------------------------------------------------------------


class ActorConfig(BaseModel):
    """
    Declarative autonomy of a profile.

    An actor replaces the two older primitives (`autonomy` profile blocks and
    per-board managers): the difference between a *board manager* and an
    *autonomous coworker* is a role and a session shape, not a different
    runtime.

    Attributes:
        enabled: When True the actor takes shifts on its own.
        role: ``coworker`` (owns a job end to end) or ``manager`` (curates the
            boards it is responsible for). The role only changes the prompt.
        policy: ``full`` (every tool of the profile) or ``board_only`` (the
            actor must stay on boards/tasks and never touch code). Combined
            with the profile tool grant, which the guardrails enforce.
        session: ``single`` (one turn per shift) or ``until_done`` (keep turning
            while work remains, bounded by ``max_turns`` and the budgets).
        board: Desk board: where the shift reports of the actor are written.
            Defaults to the first watched board or ``Agent: <name>``.
        boards: Boards the actor is responsible for. Their tasks are the agenda
            and the snapshot handed to the actor on every shift.
        interval_minutes: Cadence between two automatic shifts.
        mode: ``active`` executes the tools; ``dry_run`` reports only. The
            legacy ``apply``/``suggest`` values are mapped onto them.
        budget_cxjcoins: Cap for one shift (0 = project/chat default).
        daily_budget_cxjcoins: Cap for one day, across shifts (0 = no cap).
        max_runtime_minutes: Hard limit of one shift.
        max_turns: Upper bound of turns inside one ``until_done`` shift.
        max_agenda: How many due tasks are put in front of the actor.
        max_tasks: How many board tasks are included as context.
        include_done: Whether closed tasks are also included as context.
        instructions: Standing orders written by the user (the actor's mandate).
        wake_at: Next planned wake (ISO). Empty means "derive from the cadence".
        notify: Report the shift on the actor board.
        schedule: Weekly availability windows.
    """

    enabled: bool = False
    role: str = C.DEFAULT_ROLE
    policy: str = C.DEFAULT_POLICY
    session: str = C.DEFAULT_SESSION
    board: str = ""
    boards: List[str] = Field(default_factory=list)
    interval_minutes: int = C.DEFAULT_INTERVAL_MINUTES
    mode: str = C.DEFAULT_MODE
    budget_cxjcoins: float = 0.0
    daily_budget_cxjcoins: float = 0.0
    max_runtime_minutes: int = C.DEFAULT_MAX_RUNTIME_MINUTES
    max_turns: int = C.DEFAULT_MAX_TURNS
    max_agenda: int = C.DEFAULT_MAX_AGENDA
    max_tasks: int = C.DEFAULT_MAX_TASKS
    include_done: bool = False
    instructions: str = ""
    wake_at: str = ""
    notify: bool = True
    schedule: WeeklySchedule = Field(default_factory=WeeklySchedule)

    def normalized(self) -> "ActorConfig":
        """Return a clamped copy (never mutate the input)."""
        data = self.model_dump()
        data["board"] = str(data.get("board") or "").strip()[:120]
        boards: List[str] = []
        for raw in data.get("boards") or []:
            title = str(raw or "").strip()[:120]
            if title and title not in boards:
                boards.append(title)
        if len(boards) > C.MAX_BOARDS:
            boards = boards[: C.MAX_BOARDS]
        # `board` is the desk (where reports are written); `boards` are watched.
        if not boards and data["board"]:
            boards = [data["board"]]
        data["boards"] = boards
        # The desk board is preserved when it is explicitly set: an actor can
        # be responsible for a board while writing its reports on its own desk.
        if boards and not data["board"]:
            data["board"] = boards[0]

        role = str(data.get("role") or C.DEFAULT_ROLE).strip().lower()
        data["role"] = role if role in C.ROLES else C.DEFAULT_ROLE
        policy = str(data.get("policy") or C.DEFAULT_POLICY).strip().lower()
        data["policy"] = policy if policy in C.POLICIES else C.DEFAULT_POLICY
        session = str(data.get("session") or C.DEFAULT_SESSION).strip().lower()
        data["session"] = session if session in C.SESSIONS else C.DEFAULT_SESSION
        mode = str(data.get("mode") or C.DEFAULT_MODE).strip().lower()
        mode = C.MODE_ALIASES.get(mode, mode)
        data["mode"] = mode if mode in C.MODES else C.DEFAULT_MODE

        data["interval_minutes"] = _clamp_int(
            data.get("interval_minutes"),
            C.DEFAULT_INTERVAL_MINUTES,
            C.MIN_INTERVAL_MINUTES,
            C.MAX_INTERVAL_MINUTES,
        )
        data["max_runtime_minutes"] = _clamp_int(
            data.get("max_runtime_minutes"),
            C.DEFAULT_MAX_RUNTIME_MINUTES,
            C.MIN_MAX_RUNTIME_MINUTES,
            C.MAX_MAX_RUNTIME_MINUTES,
        )
        data["max_turns"] = _clamp_int(
            data.get("max_turns"), C.DEFAULT_MAX_TURNS, 1, C.MAX_TURNS_LIMIT
        )
        data["max_agenda"] = _clamp_int(
            data.get("max_agenda"), C.DEFAULT_MAX_AGENDA, 1, C.MAX_AGENDA_LIMIT
        )
        data["max_tasks"] = _clamp_int(
            data.get("max_tasks"), C.DEFAULT_MAX_TASKS, 1, C.MAX_TASKS_LIMIT
        )
        data["budget_cxjcoins"] = max(0.0, _as_float(data.get("budget_cxjcoins")))
        data["daily_budget_cxjcoins"] = max(
            0.0, _as_float(data.get("daily_budget_cxjcoins"))
        )
        data["instructions"] = str(data.get("instructions") or "").strip()[:16000]
        data["wake_at"] = parse_when(data.get("wake_at"))
        data["enabled"] = bool(data.get("enabled"))
        data["notify"] = bool(data.get("notify", True))
        data["include_done"] = bool(data.get("include_done"))
        raw_schedule = data.get("schedule")
        if isinstance(raw_schedule, WeeklySchedule):
            data["schedule"] = raw_schedule.normalized().model_dump()
        elif isinstance(raw_schedule, dict):
            data["schedule"] = WeeklySchedule(**raw_schedule).normalized().model_dump()
        else:
            data["schedule"] = WeeklySchedule().model_dump()
        return ActorConfig(**data)

    @classmethod
    def from_profile(cls, profile: Any, profile_name: str = "") -> "ActorConfig":
        """
        Read the actor configuration of a profile (or of its raw document).

        Accepts a ``Profile`` model, the decoded ``.profile`` JSON or ``None``.
        The block is optional: a profile without it is simply not an actor. The
        legacy ``autonomy`` key is read as a fallback.
        """
        name = str(profile_name or getattr(profile, "name", "") or "").strip()
        raw: Any = None
        if isinstance(profile, dict):
            raw = profile.get(C.ACTOR_CONFIG_KEY)
            if not isinstance(raw, dict):
                raw = profile.get(C.LEGACY_CONFIG_KEY)
            name = name or str(profile.get("name") or "")
        else:
            raw = getattr(profile, C.ACTOR_CONFIG_KEY, None)
            if not isinstance(raw, dict):
                raw = getattr(profile, C.LEGACY_CONFIG_KEY, None)
            if isinstance(raw, BaseModel):
                raw = raw.model_dump()
        config = cls(**raw) if isinstance(raw, dict) else cls()
        config = config.normalized()
        if not config.board:
            config.board = default_board_name(name)
            if not config.boards:
                config.boards = [config.board]
        return config

    @classmethod
    def from_store_value(cls, value: Any) -> "ActorConfig":
        """Build a configuration from a plain mapping (API payloads, tests)."""
        return cls(**value).normalized() if isinstance(value, dict) else cls()

    def to_profile_value(self) -> Dict[str, Any]:
        """Return the JSON value stored inside the profile document."""
        return self.normalized().model_dump()

    @property
    def watched_boards(self) -> List[str]:
        """Return the boards whose tasks feed the agenda."""
        boards = list(self.boards or [])
        if not boards and self.board:
            boards = [self.board]
        return boards

    @property
    def desk_board(self) -> str:
        """Return the board where the shift reports of the actor are written."""
        return self.board or (self.watched_boards[0] if self.boards else "")


def default_board_name(profile_name: str) -> str:
    """Return the default board title of an actor profile."""
    name = str(profile_name or "").strip() or "Agent"
    return f"{C.BOARD_PREFIX}{name}"


def _as_float(value: Any) -> float:
    """Best effort float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Clamp *value* into ``[minimum, maximum]`` with a safe fallback."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------


class AgendaItem(BaseModel):
    """One scheduled task the actor had to handle during a shift."""

    chat_id: str = Field(default="")
    name: str = Field(default="")
    wake_at: str = Field(default="")
    result: str = Field(default="")


class ShiftReport(BaseModel):
    """Outcome of one shift of one actor."""

    actor: str = Field(default="")
    agent: str = Field(default="", description="Alias of `actor` (legacy)")
    board: str = Field(default="")
    role: str = Field(default="")
    shift_chat_id: str = Field(default="")
    started_at: str = Field(default_factory=utcnow_iso)
    finished_at: str = Field(default="")
    status: str = Field(default="")
    stop_reason: str = Field(default="")
    summary: str = Field(default="")
    report: str = Field(default="")
    error: str = Field(default="")
    cost_cxjcoins: float = Field(default=0.0)
    duration_seconds: float = Field(default=0.0)
    turns: int = Field(default=0)
    agenda: List[AgendaItem] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:  # pragma: no cover - pydantic hook
        """Keep ``actor`` and its legacy alias ``agent`` in sync."""
        if not self.actor and self.agent:
            self.actor = self.agent
        if not self.agent and self.actor:
            self.agent = self.actor


class ActorState(BaseModel):
    """
    Persisted runtime of one actor.

    The configuration is *not* duplicated here: it is read from the profile on
    every tick, so editing the profile takes effect immediately. Only what
    changed at runtime is stored — including the **daily** spend, which is what
    makes ``daily_budget_cxjcoins`` enforceable across shifts.
    """

    actor: str = Field(default="")
    agent: str = Field(default="", description="Alias of `actor` (legacy)")
    board: str = Field(default="")
    running: bool = Field(default=False)
    wake_requested: bool = Field(False)
    wake_at: str = Field(default="", description="Next planned wake (ISO)")
    last_shift_at: str = Field(default="")
    last_finished_at: str = Field(default="")
    last_status: str = Field(default="")
    last_stop_reason: str = Field(default="")
    last_summary: str = Field(default="")
    last_report: str = Field(default="")
    last_error: str = Field(default="")
    last_cost: float = Field(default=0.0)
    last_turns: int = Field(default=0)
    total_cost: float = Field(default=0.0)
    day: str = Field(default="", description="UTC day of `day_cost` (YYYY-MM-DD)")
    day_cost: float = Field(default=0.0)
    shifts: int = Field(default=0)
    duration_seconds: float = Field(default=0.0)
    last_agenda: List[AgendaItem] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:  # pragma: no cover - pydantic hook
        """Keep ``actor`` and its legacy alias ``agent`` in sync."""
        if not self.actor and self.agent:
            self.actor = self.agent
        if not self.agent and self.actor:
            self.agent = self.actor

    def day_spent(self, today: Optional[str] = None) -> float:
        """Return the cost already spent today (UTC)."""
        key = today or utcnow_iso()[:10]
        return float(self.day_cost or 0.0) if self.day == key else 0.0

    def mark_started(self, actor: str, board: str) -> None:
        """Set the in-flight markers before the actor turn."""
        self.actor = actor
        self.agent = actor
        self.board = board
        self.running = True
        self.wake_requested = False
        self.wake_at = ""
        self.last_shift_at = utcnow_iso()

    def mark_finished(self, result: ShiftReport) -> None:
        """Record a finished shift (success or failure)."""
        result.finished_at = result.finished_at or utcnow_iso()
        day = (result.started_at or utcnow_iso())[:10]
        if self.day != day:
            self.day = day
            self.day_cost = 0.0
        self.running = False
        self.wake_requested = False
        self.last_finished_at = result.finished_at
        self.last_status = result.status
        self.last_stop_reason = result.stop_reason
        self.last_summary = result.summary[:8000]
        self.last_report = result.report[:20000]
        self.last_error = result.error[:4000]
        self.last_cost = max(0.0, float(result.cost_cxjcoins or 0.0))
        self.last_turns = max(0, int(result.turns or 0))
        self.total_cost = float(self.total_cost or 0.0) + self.last_cost
        self.day_cost = float(self.day_cost or 0.0) + self.last_cost
        self.duration_seconds = max(0.0, float(result.duration_seconds or 0.0))
        self.shifts = int(self.shifts or 0) + 1
        self.last_agenda = list(result.agenda or [])

    def public(self) -> Dict[str, Any]:
        """Return the API/UI shape of the state."""
        return self.model_dump()


#: Legacy names: the runtime used to be called "autonomy".
AutonomyConfig = ActorConfig
AutonomyState = ActorState

# Made with ❤️ by codx-junior
