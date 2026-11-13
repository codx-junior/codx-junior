"""
Autonomous agents tools.

A profile becomes an **agent** when its ``autonomy`` block is enabled: it gets a
board of its own, a cadence and a mandate, and the autonomy worker wakes it on
schedule so it works without a human in the loop (see
:mod:`codx.junior.autonomy`).

This tool is the single entry point for that surface:

    - ``list`` / ``status`` : who the agents are, their board, cadence, cost and
                              next wake.
    - ``enable`` / ``disable`` : switch autonomy on/off for a profile.
    - ``configure``         : change the cadence, the mandate (standing orders),
                              the budget, the mode or the board.
    - ``wake``              : ask for a shift now (or at ``when``), e.g. after a
                              user leaves a note on the agent board.
    - ``schedule``          : set the next shift instant (``"+2d"``, ``"3 hours"``).
    - ``run``               : run a shift synchronously (blocking; for debugging).

Design notes:
    - **The profile is the agent.** There is no second catalogue: the
      configuration lives in ``.codx/profiles/<Name>/<Name>.profile`` under the
      ``autonomy`` key, so an agent inherits the whole profile machinery
      (linked profiles, tools, knowledge, model) instead of duplicating it. This
      is what lets an agent link the ``codx-junior`` profile and get the full
      workspace capabilities with initiative on top.
    - **Scheduling is a task field.** ``AutonomyService`` only wakes an agent for
      tasks whose ``Chat.wake_at`` is due, so an agent (or a human) schedules a
      follow-up by creating/updating a task with ``wake_at``.
    - **One tool, several actions** (like ``profile_management`` and
      ``project_manager``), to keep the prompt small.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .model import ToolResponse

logger = logging.getLogger(__name__)


def _service(**kwargs) -> Any:
    """Build the :class:`AutonomyService` for the current execution context."""
    from codx.junior.autonomy.service import AutonomyService
    from .project_manager_tools import _resolve_settings

    settings = _resolve_settings(**kwargs)
    if settings is None:
        raise ValueError(
            "autonomy_management requires a project session or project settings "
            "(none were injected by the runtime)"
        )
    return AutonomyService(settings=settings, event_manager=kwargs.get("event_manager"))


def _merge_actor(
    current: Optional[Dict[str, Any]],
    *,
    enabled: Optional[bool] = None,
    role: Optional[str] = None,
    policy: Optional[str] = None,
    session: Optional[str] = None,
    board: Optional[str] = None,
    boards: Optional[Any] = None,
    interval_minutes: Optional[int] = None,
    mode: Optional[str] = None,
    budget_cxjcoins: Optional[float] = None,
    daily_budget_cxjcoins: Optional[float] = None,
    max_runtime_minutes: Optional[int] = None,
    max_turns: Optional[int] = None,
    max_agenda: Optional[int] = None,
    max_tasks: Optional[int] = None,
    include_done: Optional[bool] = None,
    instructions: Optional[str] = None,
    wake_at: Optional[str] = None,
    schedule: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Merge the provided settings into the current actor block.

    Only the arguments that are not ``None`` are merged, so a partial update
    never resets the cadence, the schedule or the mandate by accident.
    """
    from codx.junior.autonomy.models import ActorConfig

    data = dict(current or {})
    if isinstance(boards, str):
        boards = [item.strip() for item in boards.split(",") if item.strip()]
    updates = {
        "enabled": enabled,
        "role": role,
        "policy": policy,
        "session": session,
        "board": board,
        "boards": boards,
        "interval_minutes": interval_minutes,
        "mode": mode,
        "budget_cxjcoins": budget_cxjcoins,
        "daily_budget_cxjcoins": daily_budget_cxjcoins,
        "max_runtime_minutes": max_runtime_minutes,
        "max_turns": max_turns,
        "max_agenda": max_agenda,
        "max_tasks": max_tasks,
        "include_done": include_done,
        "instructions": instructions,
        "wake_at": wake_at,
        "schedule": schedule,
    }
    for key, value in updates.items():
        if value is not None:
            data[key] = value
    return ActorConfig(**data).normalized().model_dump()


def _block_of(profile: Any) -> Optional[Dict[str, Any]]:
    """Return the current actor block of a profile (legacy key included)."""
    for key in ("actor", "autonomy"):
        value = getattr(profile, key, None)
        if isinstance(value, dict) and value:
            return value
    return None


def _save_actor(service: Any, profile_name: str, actor: Dict[str, Any]):
    """Persist the actor block inside the profile (single source of truth)."""
    manager = service._profiles()
    profile = manager.read_profile(profile_name)
    if profile is None:
        raise ValueError(f"profile '{profile_name}' does not exist")
    profile.actor = actor
    if getattr(profile, "autonomy", None):
        # Migrated: exactly one declaration per profile.
        profile.autonomy = None
    manager.save_profile(profile=profile)
    return profile


def _describe(entry: Dict[str, Any]) -> str:
    """Render one actor as a markdown bullet."""
    config = entry.get("config") or {}
    state = entry.get("state") or {}
    schedule = entry.get("schedule") or {}
    flags = "enabled" if config.get("enabled") else "disabled"
    due = " · **due now**" if entry.get("due") else ""
    return (
        f"- `{entry.get('actor')}` ({config.get('role')}, `{config.get('session')}`) "
        f"→ desk **{entry.get('board')}** · boards: "
        f"{', '.join(entry.get('boards') or []) or '-'} · {flags}{due}\n"
        f"  - every {config.get('interval_minutes')} min · mode `{config.get('mode')}` "
        f"· next wake {entry.get('next_wake_at') or '-'}\n"
        f"  - availability: {schedule.get('description') or 'always'}"
        f"{'' if schedule.get('open', True) else ' · **closed now**'}\n"
        f"  - shifts: {state.get('shifts', 0)} · total cost: "
        f"{state.get('total_cost', 0):.4f} cxjcoins · today: "
        f"{state.get('day_cost', 0):.4f} cxjcoins · last: "
        f"{state.get('last_status') or 'never'}"
    )


async def autonomy_management(
    action: str,
    profile_name: Optional[str] = None,
    enabled: Optional[bool] = None,
    role: Optional[str] = None,
    policy: Optional[str] = None,
    session: Optional[str] = None,
    board: Optional[str] = None,
    boards: Optional[Any] = None,
    interval_minutes: Optional[int] = None,
    mode: Optional[str] = None,
    budget_cxjcoins: Optional[float] = None,
    daily_budget_cxjcoins: Optional[float] = None,
    max_runtime_minutes: Optional[int] = None,
    max_turns: Optional[int] = None,
    max_agenda: Optional[int] = None,
    max_tasks: Optional[int] = None,
    include_done: Optional[bool] = None,
    instructions: Optional[str] = None,
    schedule: Optional[Dict[str, Any]] = None,
    when: Optional[str] = None,
    **kwargs,
) -> ToolResponse:
    """
    Manage the actors (autonomous profiles) of the project.

    Args:
        action: ``list``, ``status``, ``enable``, ``disable``, ``configure``,
            ``wake``, ``schedule`` or ``run``.
        profile_name: Profile that acts as the actor (required except for
            ``list``/``status``).
        enabled: Turn the actor on/off (``configure``).
        role: ``coworker`` (owns a job end to end) or ``manager`` (curates the
            boards it is responsible for).
        policy: ``full`` (every tool) or ``board_only`` (board work only).
        session: ``single`` (one turn per shift) or ``until_done`` (turns while
            work remains, bounded by ``max_turns`` and the budgets).
        board: Desk board, where the shift reports are written (defaults to
            ``Agent: <name>``).
        boards: Boards the actor is responsible for (list or comma separated).
        interval_minutes: Cadence between two automatic shifts.
        mode: ``active`` (the actor acts) or ``dry_run`` (it only reports).
        budget_cxjcoins: Budget of one shift (0 = project default).
        daily_budget_cxjcoins: Budget of one day across shifts (0 = no cap).
        max_runtime_minutes: Hard limit of one shift.
        max_turns: Maximum turns inside one ``until_done`` shift.
        max_agenda: How many due tasks the shift must handle.
        max_tasks: How many tasks of the boards are given as context.
        include_done: Include closed tasks as context.
        instructions: Standing orders added to the profile mandate.
        schedule: Weekly availability, e.g.
            ``{"enabled": true, "timezone": "Europe/Madrid",
            "windows": [{"days": ["mon","tue","wed","thu","fri"],
            "start": "09:00", "end": "18:00"}]}``. Use it to keep autonomous
            work inside office hours.
        when: Schedule expression for ``wake``/``schedule`` (``"+2d"``,
            ``"3 hours"``, an ISO date or instant).
        **kwargs: Runtime injection (session/settings/event_manager).

    Returns:
        ToolResponse with a markdown answer for the user and the JSON payload
        for the model.
    """
    import json

    action = str(action or "").strip().lower()
    service = _service(**kwargs)

    try:
        if action in ("list", "status"):
            payload = service.status()
            actors = payload.get("actors") or []
            if not actors:
                text = (
                    "No actors yet. Create a profile, link the "
                    "`codx-junior` profile (tools + knowledge) and call "
                    "`autonomy_management(action='enable', profile_name=...)`."
                )
            else:
                lines = [f"## {len(actors)} actor(s)", ""]
                lines += [_describe(entry) for entry in actors]
                if not payload.get("worker_present"):
                    lines += [
                        "",
                        "⚠️ No actor worker heartbeat: automatic shifts will "
                        "not run until the background service is up.",
                    ]
                text = "\n".join(lines)
            return ToolResponse(user_response=text, llm_response=json.dumps(payload))

        if not profile_name:
            raise ValueError(f"action='{action}' requires profile_name")

        if action in ("enable", "disable"):
            profile = _service_profile(service, profile_name)
            actor = _merge_actor(_block_of(profile), enabled=action == "enable")
            _save_actor(service, profile.name, actor)
            verb = "enabled" if action == "enable" else "disabled"
            entry = _entry_for(service, profile.name)
            return ToolResponse(
                user_response=(
                    f"✅ Actor {verb} for `{profile.name}`.\n"
                    f"- Desk board: **{entry.get('board')}**\n"
                    f"- Boards: {', '.join(entry.get('boards') or []) or '-'}\n"
                    f"- Availability: {(entry.get('schedule') or {}).get('description')}\n"
                    f"- Cadence: every {actor.get('interval_minutes')} min\n"
                    f"- Next wake: {entry.get('next_wake_at') or '-'}"
                ),
                llm_response=json.dumps(entry),
            )

        if action == "configure":
            profile = _service_profile(service, profile_name)
            actor = _merge_actor(
                _block_of(profile),
                enabled=enabled,
                role=role,
                policy=policy,
                session=session,
                board=board,
                boards=boards,
                interval_minutes=interval_minutes,
                mode=mode,
                budget_cxjcoins=budget_cxjcoins,
                daily_budget_cxjcoins=daily_budget_cxjcoins,
                max_runtime_minutes=max_runtime_minutes,
                max_turns=max_turns,
                max_agenda=max_agenda,
                max_tasks=max_tasks,
                include_done=include_done,
                instructions=instructions,
                schedule=schedule,
            )
            _save_actor(service, profile.name, actor)
            entry = _entry_for(service, profile.name)
            return ToolResponse(
                user_response=(
                    f"✅ Updated the actor `{profile.name}`.\n"
                    f"- Role: `{actor.get('role')}` · policy `{actor.get('policy')}` "
                    f"· session `{actor.get('session')}`\n"
                    f"- Desk board: **{entry.get('board')}**\n"
                    f"- Boards: {', '.join(entry.get('boards') or []) or '-'}\n"
                    f"- Availability: {(entry.get('schedule') or {}).get('description')}\n"
                    f"- Cadence: every {actor.get('interval_minutes')} min "
                    f"· mode `{actor.get('mode')}`\n"
                    f"- Runtime limit: {actor.get('max_runtime_minutes')} min "
                    f"· turns: {actor.get('max_turns')} "
                    f"· agenda: {actor.get('max_agenda')} task(s)\n"
                    f"- Budget: {actor.get('budget_cxjcoins')} / shift · "
                    f"{actor.get('daily_budget_cxjcoins')} / day\n"
                    f"- Next wake: {entry.get('next_wake_at') or '-'}"
                ),
                llm_response=json.dumps(entry),
            )

        if action == "wake":
            payload = service.request_wake(profile_name, when=when or "")
            return ToolResponse(
                user_response=(
                    f"⏰ Shift queued for `{payload.get('actor')}`"
                    + (f" at {payload.get('wake_at')}" if payload.get("wake_at") else " now")
                    + "."
                ),
                llm_response=json.dumps(payload),
            )

        if action == "schedule":
            if not when:
                raise ValueError("action='schedule' requires 'when'")
            payload = service.schedule_wake(profile_name, when)
            return ToolResponse(
                user_response=(
                    f"🗓️ `{payload.get('actor')}` will wake at "
                    f"**{payload.get('wake_at')}**."
                ),
                llm_response=json.dumps(payload),
            )

        if action == "run":
            payload = await service.run_shift(profile_name, force=True, trigger="tool")
            state = payload.get("state") or {}
            return ToolResponse(
                user_response=(
                    f"## Shift finished · `{payload.get('actor')}`\n\n"
                    f"- Status: **{state.get('last_status')}**\n"
                    f"- Cost: {state.get('last_cost', 0):.4f} cxjcoins\n"
                    f"- Duration: {state.get('duration_seconds', 0):.1f}s\n"
                    f"- Next wake: {payload.get('next_wake_at') or '-'}\n\n"
                    f"{state.get('last_report') or state.get('last_error') or ''}"
                ),
                llm_response=json.dumps(payload),
            )

        raise ValueError(
            f"unknown action '{action}'. Available: list, status, enable, "
            f"disable, configure, wake, schedule, run"
        )
    except ValueError as ex:
        return ToolResponse(user_response=f"⚠️ {ex}", llm_response=json.dumps({"error": str(ex)}))


def _service_profile(service: Any, profile_name: str):
    """Return the profile to configure, ensuring it exists."""
    manager = service._profiles()
    profile = manager.read_profile(str(profile_name or "").strip().lstrip("@"))
    if profile is None:
        raise ValueError(f"profile '{profile_name}' does not exist")
    return profile


def _entry_for(service: Any, profile_name: str) -> Dict[str, Any]:
    """Return the status entry of one agent (best effort)."""
    for entry in service.status().get("agents") or []:
        if str(entry.get("agent", "")).lower() == profile_name.lower():
            return entry
    return {"agent": profile_name}


AUTONOMY_MANAGEMENT_TOOL_JSON = {
    "type": "function",
    "function": {
        "name": "autonomy_management",
        "description": (
            "Manage the actors (autonomous profiles) of the project. An actor is "
            "a profile with `actor` enabled: it gets a role (coworker or board "
            "manager), boards, a cadence, a weekly availability window and "
            "budgets, and a background worker wakes it so it can work without a "
            "human ('take care of the maintenance tickets', 'manage the agenda'). "
            "Actions: list, status, enable, disable, configure, wake, schedule, "
            "run. Schedule follow-ups by creating tasks with `wake_at` (the actor "
            "is woken only for tasks whose wake_at is due). Use `schedule` to keep "
            "autonomous work inside office hours."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list", "status", "enable", "disable", "configure",
                        "wake", "schedule", "run",
                    ],
                    "description": "Operation to perform.",
                },
                "profile_name": {
                    "type": "string",
                    "description": (
                        "Profile that acts as the agent (required except for "
                        "list/status)."
                    ),
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Turn autonomy on/off (configure).",
                },
                "role": {
                    "type": "string",
                    "enum": ["coworker", "manager"],
                    "description": (
                        "coworker: owns a job end to end. manager: curates the "
                        "boards it is responsible for."
                    ),
                },
                "policy": {
                    "type": "string",
                    "enum": ["full", "board_only"],
                    "description": (
                        "full: every tool. board_only: the actor must stay on "
                        "boards and tasks and never touch code."
                    ),
                },
                "session": {
                    "type": "string",
                    "enum": ["single", "until_done"],
                    "description": (
                        "single: one turn per shift. until_done: keep turning "
                        "while work remains, bounded by max_turns and the budgets."
                    ),
                },
                "board": {
                    "type": "string",
                    "description": (
                        "Desk board, where the shift reports are written. Defaults "
                        "to 'Agent: <profile name>' and is created automatically."
                    ),
                },
                "boards": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Boards the actor is responsible for. Their tasks are the "
                        "agenda of every shift."
                    ),
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "Cadence between two automatic shifts.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["active", "dry_run"],
                    "description": (
                        "active: the agent acts with its tools. dry_run: it only "
                        "reports what it would do."
                    ),
                },
                "budget_cxjcoins": {
                    "type": "number",
                    "description": "Budget hint for one shift (0 = project default).",
                },
                "daily_budget_cxjcoins": {
                    "type": "number",
                    "description": (
                        "Budget of one day across shifts (0 = no cap). The actor "
                        "keeps working until it is done or this is over."
                    ),
                },
                "max_runtime_minutes": {
                    "type": "integer",
                    "description": "Hard limit of one shift, in minutes.",
                },
                "max_agenda": {
                    "type": "integer",
                    "description": "How many due tasks one shift must handle.",
                },
                "max_turns": {
                    "type": "integer",
                    "description": (
                        "Maximum turns inside one 'until_done' shift (default 6)."
                    ),
                },
                "max_tasks": {
                    "type": "integer",
                    "description": (
                        "How many tasks of the boards are given as context."
                    ),
                },
                "include_done": {
                    "type": "boolean",
                    "description": "Include closed tasks as context.",
                },
                "schedule": {
                    "type": "object",
                    "description": (
                        "Weekly availability. Disabled means always available. "
                        "Use it to keep autonomous work inside the hours where "
                        "someone is online to review it."
                    ),
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "timezone": {
                            "type": "string",
                            "description": "IANA zone, e.g. Europe/Madrid.",
                        },
                        "windows": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "days": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "mon..sun. Empty means every day."
                                        ),
                                    },
                                    "start": {"type": "string"},
                                    "end": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "instructions": {
                    "type": "string",
                    "description": (
                        "Standing orders (the agent's mandate), added to the "
                        "profile instructions."
                    ),
                },
                "when": {
                    "type": "string",
                    "description": (
                        "Schedule expression for wake/schedule: '+2d', '3 hours', "
                        "an ISO date (2026-04-01) or an ISO instant."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}

# Made with ❤️ by codx-junior
