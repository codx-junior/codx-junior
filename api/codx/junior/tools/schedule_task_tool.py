"""
Schedule task tool: put a task "for later" (after hours).

``wake_at`` already exists as a task field: it is the alarm clock the autonomy
worker reads, so a task carrying a due ``wake_at`` is picked up by the actor
owning its board without a human in the loop. Until now the field was reachable
from ``project_manager`` (create/update) and from the API, which means the model
had to know it existed to use it. This tool is the natural-language entry point
for the very same field:

    "schedule the chat-db task for tonight at 01:00, max effort"

Design notes:
    - **One field, one tool.** ``schedule_task`` never invents state: it writes
      ``Chat.wake_at`` (plus, optionally, ``profiles``) through
      ``ChatManager.update_chat_metadata``, exactly like ``project_manager``.
    - **Schedule an existing task or create one on the fly.** With ``task_id``
      (or ``task``, a name match) the task is rescheduled; with ``name`` +
      ``description`` a new task is created already scheduled.
    - **Off-hours vocabulary.** ``"01:00"``, ``"tonight 01:00"``,
      ``"tomorrow 1am"``, ``"after hours"`` (defaults to 01:00), ``"+2d"``,
      ``"3 hours"``, ``"2026-04-03"`` and ISO instants are accepted. Clock times
      are resolved in ``timezone`` (UTC by default) to the next occurrence, so
      "01:00" said at 15:00 means tonight, not thirteen hours ago.
    - **Read-back.** The tool returns the value stored in the task, so the model
      reports what happened instead of what it asked for.

Made with ❤️ by codx-junior
"""

import json
import logging
import re
from datetime import timedelta, timezone as dt_timezone
from typing import TYPE_CHECKING, Any, List, Optional

from codx.junior.autonomy.models import parse_when, timezone_for, utcnow
from codx.junior.task_manager import TaskManager, to_profile_names
from .model import ToolResponse
from .project_manager_tools import _resolve_settings, validate_task_destination

if TYPE_CHECKING:
    from codx.junior.settings import CODXJuniorSettings

logger = logging.getLogger(__name__)

# Used when the caller only says "after hours"/"tonight" without a clock time.
DEFAULT_OFF_HOURS = "01:00"

_CLOCK_RE = re.compile(r"^(?:at\s+|@\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)

_NIGHT_WORDS = ("tonight", "after hours", "after work", "overnight", "at night", "night")
_DAY_WORDS = ("tomorrow",)


def _strip_when_words(text: str) -> str:
    """Remove the natural-language prefix ("tonight", "tomorrow at", ...)."""
    cleaned = text
    for word in _NIGHT_WORDS + _DAY_WORDS:
        cleaned = re.sub(rf"^{word}\b[,\s]*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^(?:at|@)\s*", "", cleaned, flags=re.I)
    return cleaned.strip()


def _resolve_when(
    when: str,
    timezone_name: Optional[str] = None,
    now: Optional[Any] = None,
) -> str:
    """
    Resolve a scheduling expression into an ISO-8601 UTC instant.

    Clock times (``"01:00"``, ``"tonight 01:00"``, ``"tomorrow 1am"``) are
    resolved to the *next* occurrence in ``timezone_name`` (UTC by default).
    Everything else is delegated to
    :func:`codx.junior.autonomy.models.parse_when`, which understands relative
    expressions (``"+2d"``, ``"3 hours"``), ISO dates and ISO instants.

    Args:
        when: The expression written by the user/model.
        timezone_name: IANA zone used to interpret clock times (``None`` = UTC).
        now: Reference instant, for tests.

    Returns:
        ISO-8601 UTC instant.

    Raises:
        ValueError: When the expression cannot be understood.

    Example:
        >>> _resolve_when("12 hours")  # doctest: +SKIP
        '2026-04-01T22:00:00+00:00'
    """
    text = str(when or "").strip()
    if not text:
        raise ValueError(
            "schedule_task requires 'when': '01:00', 'tonight 01:00', "
            "'tomorrow 1am', 'after hours', '+2d', '3 hours' or an ISO instant"
        )

    lowered = text.lower()
    tonight = any(word in lowered for word in _NIGHT_WORDS)
    tomorrow = any(word in lowered for word in _DAY_WORDS)
    cleaned = _strip_when_words(lowered)
    if not cleaned and tonight:
        cleaned = DEFAULT_OFF_HOURS

    clock = _CLOCK_RE.match(cleaned)
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        meridiem = (clock.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"schedule_task: '{when}' is not a valid clock time")
        zone = timezone_for(timezone_name)
        reference = (now or utcnow()).astimezone(zone)
        moment = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # "01:00" at 15:00 means tonight (tomorrow 01:00), not 14 hours ago.
        if tomorrow or moment <= reference:
            moment = moment + timedelta(days=1)
        return moment.astimezone(dt_timezone.utc).isoformat()

    resolved = parse_when(text, now=now)
    if not resolved:
        raise ValueError(
            f"schedule_task: cannot understand when='{text}'. Use a clock time "
            "('01:00', 'tonight 01:00', 'tomorrow 1am'), a relative expression "
            "('+2d', '3 hours'), an ISO date ('2026-04-03') or an ISO instant."
        )
    return resolved


def _find_task(chat_manager: Any, task_id: Optional[str], task: Optional[str]) -> Any:
    """
    Load the task to reschedule by id, falling back to a name match.

    Args:
        chat_manager: The project chat manager.
        task_id: Task id (preferred).
        task: Task name (exact first, then substring, case insensitive).

    Returns:
        The :class:`~codx.junior.db.Chat` task.

    Raises:
        ValueError: When no task matches or the name is ambiguous.
    """
    if task_id and str(task_id).strip():
        chat = chat_manager.find_by_id(chat_id=str(task_id).strip())
        if chat is None:
            raise ValueError(f"schedule_task: task '{task_id}' does not exist")
        return chat

    wanted = str(task or "").strip().lower()
    if not wanted:
        raise ValueError(
            "schedule_task requires 'task_id' (or 'task' name), or 'name' + "
            "'description' to create an already scheduled task"
        )

    chats = list(chat_manager.list_chats() or [])
    exact = [c for c in chats if (c.name or "").strip().lower() == wanted]
    matches = exact or [c for c in chats if wanted in (c.name or "").strip().lower()]
    if not matches:
        raise ValueError(f"schedule_task: no task matches '{task}'")
    if len(matches) > 1:
        listing = ", ".join(f"`{c.id}` ({c.name})" for c in matches[:10])
        raise ValueError(
            f"schedule_task: '{task}' is ambiguous, pass 'task_id': {listing}"
        )
    return matches[0]


def _merge_profiles(current: Any, extra: Optional[List[str]]) -> Optional[List[str]]:
    """Merge the requested profiles into the task list (order preserved)."""
    wanted = to_profile_names(extra)
    if not wanted:
        return None
    merged = to_profile_names(list(current or []) + wanted)
    return merged or None


SCHEDULE_TASK_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "schedule_task",
        "description": (
            "Schedule a task to run later, typically after hours (e.g. 'tonight at "
            "01:00') so an autonomous agent picks it up when nobody is working. "
            "Writes the task 'wake_at' field (the alarm clock the autonomy worker "
            "reads; it is NOT a deadline - use due_date for that). "
            "Either reschedule an existing task ('task_id' or 'task' name) or "
            "create one already scheduled ('name' + 'description'). "
            "'when' accepts '01:00', 'tonight 01:00', 'tomorrow 1am', "
            "'after hours', '+2d', '3 hours', an ISO date or an ISO instant; clock "
            "times resolve to the next occurrence in 'timezone' (UTC by default). "
            "Use 'profiles' (e.g. ['codx-junior-max']) to run it with the effort "
            "tier you want. Returns the value actually stored in the task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "when": {
                    "type": "string",
                    "description": (
                        "When to wake the task: '01:00', 'tonight 01:00', "
                        "'tomorrow 1am', 'after hours', '+2d', '3 hours', "
                        "'2026-04-03' or an ISO instant."
                    ),
                },
                "task_id": {
                    "type": "string",
                    "description": "Id of the task to reschedule.",
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Name of the task to reschedule (exact, else substring). "
                        "Ambiguous names are rejected: use 'task_id'."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Name of a NEW task to create already scheduled "
                        "(requires 'description')."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Description of the new task (with 'name').",
                },
                "board": {
                    "type": "string",
                    "description": (
                        "Board for the new task (default 'kanban'; must exist)."
                    ),
                },
                "column": {
                    "type": "string",
                    "description": "Column for the new task (default 'tasks').",
                },
                "profiles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Profiles to add to the task, e.g. ['codx-junior-max'] for "
                        "maximum effort or ['software_developer']. Merged with the "
                        "profiles the task already has."
                    ),
                },
                "timezone": {
                    "type": "string",
                    "description": (
                        "IANA zone used to interpret clock times (default UTC), "
                        "e.g. 'Europe/Madrid'."
                    ),
                },
                "clear": {
                    "type": "boolean",
                    "description": (
                        "Remove the schedule instead of setting it (the task stays "
                        "on its board, the autonomy worker stops waking it)."
                    ),
                },
            },
            "required": ["when"],
        },
    },
}


async def schedule_task(
    when: str,
    task_id: Optional[str] = None,
    task: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    board: Optional[str] = None,
    column: Optional[str] = None,
    profiles: Optional[List[str]] = None,
    timezone: Optional[str] = None,
    clear: bool = False,
    settings: Optional["CODXJuniorSettings"] = None,
    session: Any = None,
    event_manager: Any = None,
    **kwargs: Any,
) -> ToolResponse:
    """
    Schedule an existing task for later, or create a task already scheduled.

    Args:
        when: Scheduling expression ('01:00', 'tonight 01:00', '+2d', ISO...).
        task_id: Id of the task to reschedule.
        task: Name of the task to reschedule (exact, else substring).
        name: Name of a new task to create already scheduled.
        description: Description of the new task (required with ``name``).
        board: Board for the new task (default ``kanban``).
        column: Column for the new task (default ``tasks``).
        profiles: Profiles to add to the task (merged, never replaced).
        timezone: IANA zone used to interpret clock times (default UTC).
        clear: Remove the schedule instead of setting it.
        settings: Project settings (injected when the tool is registered with
            ``project_settings=True``).
        session: Optional agent session (used to resolve the settings/manager).
        event_manager: Optional event manager.

    Returns:
        ToolResponse with a human-readable confirmation and a JSON payload
        containing the task id and the ``wake_at`` value actually stored.

    Raises:
        ValueError: When ``when`` cannot be understood, the task does not exist,
            the name is ambiguous or the destination board is unknown.
        RuntimeError: When the project settings are missing or the write fails.

    Example:
        >>> schedule_task(  # doctest: +SKIP
        ...     when="tonight 01:00",
        ...     task="Chat storage folder layout",
        ...     profiles=["codx-junior-max"],
        ...     timezone="Europe/Madrid",
        ... )
    """
    if not clear:
        resolved = _resolve_when(when, timezone_name=timezone)
    else:
        resolved = ""

    if settings is None:
        settings = _resolve_settings(
            settings=kwargs.get("settings"), session=session or kwargs.get("session")
        )
    if settings is None:
        raise RuntimeError(
            "schedule_task requires a project session or project settings "
            "(none were injected by the runtime)"
        )

    session = session or getattr(settings, "_active_session", None)
    chat_manager = None
    if session is not None and hasattr(session, "get_chat_manager"):
        try:
            chat_manager = session.get_chat_manager()
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("schedule_task: cannot resolve chat manager: %s", ex)

    manager = TaskManager(
        settings=settings,
        chat_manager=chat_manager,
        event_manager=event_manager or getattr(session, "event_manager", None),
    )
    chat_manager = manager.chat_manager

    try:
        if name and str(name).strip():
            if not description or not str(description).strip():
                raise ValueError(
                    "schedule_task: creating a scheduled task requires "
                    "'description' as well as 'name'"
                )
            location = validate_task_destination(
                chat_manager=chat_manager,
                board=(board or "kanban").strip(),
                column=(column or "tasks").strip(),
                sprints=None,
                status=None,
            )
            created = manager.create_task(
                name=str(name).strip(),
                description=str(description).strip(),
                content=None,
                board=location["board"],
                column=location["column"],
                sprints=location["sprints"],
                status=location["status"],
                profiles=to_profile_names(profiles) or None,
                wake_at=resolved or None,
                parent_id=kwargs.get("parent_id"),
                parent_chat=getattr(session, "_current_chat", None),
            )
            stored = chat_manager.find_by_id(chat_id=created.id) or created
            action = "created+scheduled"
        else:
            stored = _find_task(chat_manager, task_id, task)
            metadata = {"wake_at": resolved}
            merged = _merge_profiles(stored.profiles, profiles)
            if merged:
                metadata["profiles"] = merged
            stored = chat_manager.update_chat_metadata(chat=stored, metadata=metadata)
            stored = chat_manager.find_by_id(chat_id=stored.id) or stored
            action = "rescheduled"
    except ValueError:
        raise
    except Exception as ex:
        logger.exception("schedule_task: error scheduling task: %s", ex)
        raise RuntimeError(f"Failed to schedule task: {ex}") from ex

    wake_at = getattr(stored, "wake_at", None) or ""
    head = "🗓️ Schedule cleared" if not wake_at else "🗓️ Task scheduled"
    user_lines = [
        f"{head}: **{getattr(stored, 'name', '(unnamed)')}**",
        f"- Task ID: `{getattr(stored, 'id', '-')}`",
        f"- Board/Column: `{getattr(stored, 'board', '-')}` / "
        f"`{getattr(stored, 'column', '-')}`",
        f"- Wake at: `{wake_at or '-'}`" + (f" (requested `{when}`)" if when else ""),
    ]
    if timezone:
        user_lines.append(f"- Clock time interpreted in: `{timezone}`")
    stored_profiles = list(getattr(stored, "profiles", None) or [])
    if stored_profiles:
        user_lines.append(f"- Profiles: {', '.join(f'`{p}`' for p in stored_profiles)}")
    if wake_at:
        user_lines.append(
            "\nThe autonomy worker wakes this task when `wake_at` is due: the actor "
            "owning the board must be enabled and available in that window."
        )

    payload = json.dumps(
        {
            "status": action,
            "task_id": getattr(stored, "id", None),
            "name": getattr(stored, "name", None),
            "board": getattr(stored, "board", None),
            "column": getattr(stored, "column", None),
            "wake_at": wake_at or None,
            "requested_when": when,
            "timezone": timezone,
            "profiles": stored_profiles,
        }
    )
    return ToolResponse(user_response="\n".join(user_lines), llm_response=payload)


# Made with ❤️ by codx-junior
