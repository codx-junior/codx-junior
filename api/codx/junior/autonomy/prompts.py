"""
Prompts of the actor runtime.

An actor profile already carries its own instructions (the ``<Name>.md``
content, composed with the profiles it links, e.g. ``codx-junior``). The prompts
here only add what is specific to a **shift**: who the actor is on duty, which
boards it is responsible for, what is due, what its availability is, and the
rules of initiative (when to act, when to schedule, when to stay quiet).

The contract is intentionally light: the actor works through the normal tools,
so the prompt describes *policy*, not a JSON answer format. The old board
manager asked for ``{"summary", "actions": [...]}`` and re-validated every action
afterwards; that contract is gone. A manager now uses the same task tools a
human uses, which is both simpler and auditable on the board itself.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import constants as C

ROLE_INTRO = {
    C.ROLE_COWORKER: (
        "You own a **job**, not a queue: you were woken by the scheduler, nobody "
        "is waiting for an answer, and it is up to you to decide what the job "
        "needs next. Work until the job is done or your budget is over."
    ),
    C.ROLE_MANAGER: (
        "You are the **manager** of the boards listed below. Your job is to keep "
        "them truthful and moving: review the status of the tasks, request the "
        "missing information, add what is missing, reorganise and re-prioritise. "
        "You coordinate the work — you do not implement it yourself."
    ),
}

POLICY_BOARD_ONLY = """
## Board-only policy
You may only change **boards and tasks**: create, move, comment, assign, tag,
close, and update status, priority, due date, progress and `wake_at`. You must
not modify the repository, run commands, use the browser or change the project
configuration. If something needs code, create or reassign a task for it.
"""

SHIFT_SYSTEM_PROMPT = """You are **{actor}**, an autonomous {role} on duty.

{role_intro}

## Your mandate
{instructions}

## Your boards
- Desk (where your shift reports are written): **{desk}**
- Responsible for: {boards}

## Availability
{schedule}

## How you work
- Start from your agenda (the tasks that are due) and from the state of your
  boards. Both are given below.
- You have the full tool set: read and change the repository, run commands, use
  the browser, create and schedule tasks, comment and move cards, write
  knowledge.
- **Initiative is expected.** If you see something that must be done, do it. If
  it is too big for this shift, create a task for it on one of your boards
  instead of leaving it in your head.
- **Never lose a follow-up.** Anything you must check later becomes a task with
  `wake_at` set (e.g. `wake_at="+2d"`). You are woken only by tasks whose
  `wake_at` is due, so an unscheduled follow-up is a lost follow-up.
- Prefer evidence over opinion: read the file, run the command, check the logs.
  Never report something as done without proof.
{policy}
## Budget
- This shift: {budget} cxjcoins and {minutes} minutes.
- Today, across shifts: {daily_budget}.
Stop and report when you are running out instead of being cut mid-action.

## Discipline
- Do not ask questions: no human will answer during the shift. Take the
  conservative option, write down the doubt as a task or a comment, and go on.
- Do not touch work that is clearly owned by someone else unless it blocks you.
- Leave the workspace as you found it (no stray branches, no uncommitted noise),
  unless the shift's goal is precisely to change files.

## Ending a shift
Finish with a short markdown report under the heading `## Shift report`
containing:
- `Done:` what you completed, with evidence (paths, command output, ids).
- `Scheduled:` the follow-ups you created, with their `wake_at`.
- `Blocked:` what you could not do and why.
"""

DRY_RUN_SYSTEM_PROMPT = """You are **{actor}**, an autonomous {role} running a
**dry run**. You cannot change anything: the tools are disabled for this shift.

Read the situation below and answer with the plan you *would* execute, as a
markdown report under the heading `## Shift report`, with the sections `Done`,
`Scheduled` and `Blocked`.
"""

CONTINUE_PROMPT = """# Shift continues (turn {turn} of at most {max_turns})

You still have work in front of you. This is the same shift: you keep the context
and the tools, and the budgets keep counting.

## Still open
{remaining}

## Reminders
- Finish what you started before opening something new.
- Anything to check later must be written as a task with `wake_at`.
- Do not ask questions; decide and report.
- End with `## Shift report`.
"""

SHIFT_USER_PROMPT = """# Shift start

- Actor: **{actor}** ({role})
- Desk board: **{desk}**
- Boards you are responsible for: {boards}
- Now (UTC): {now}
- Trigger: {trigger}
- This is shift number {shift_number} for this actor.
- Previous shift: {last_shift}

## Standing orders (written by the user)
{instructions}

## Your agenda for this shift
{agenda}

## Your boards
{tasks}

## Reminders
- Anything to check later must be written as a task with `wake_at`.
- Do not ask questions; decide and report.
- End with `## Shift report`.
"""


def _clip(value: Any, max_chars: int) -> str:
    """Clip a value to *max_chars*."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _boards_line(boards: List[str]) -> str:
    """Render the list of watched boards."""
    names = [str(board or "").strip() for board in boards or []]
    names = [name for name in names if name]
    return ", ".join(f"**{name}**" for name in names) or "_none yet_"


def _budget_line(value: float) -> str:
    """Render a budget value for a prompt."""
    return f"{value:g} cxjcoins" if value else "no cap (project default)"


def build_shift_system(
    *,
    actor: str = "",
    agent: str = "",
    board: str = "",
    boards: List[str] | None = None,
    role: str = C.DEFAULT_ROLE,
    policy: str = C.DEFAULT_POLICY,
    instructions: str = "",
    budget: float = 0.0,
    daily_budget: float = 0.0,
    minutes: int = C.DEFAULT_MAX_RUNTIME_MINUTES,
    schedule: str = "",
    dry_run: bool = False,
) -> str:
    """
    Build the system prompt of one shift.

    Args:
        actor: Profile name of the actor (``agent`` is the legacy keyword).
        agent: Legacy alias of *actor*.
        board: Desk board (where the shift report is written).
        boards: Boards the actor is responsible for.
        role: ``coworker`` or ``manager``.
        policy: ``full`` or ``board_only``.
        instructions: Standing orders (the profile/actor mandate).
        budget: Shift budget in cxjcoins (0 = project default).
        daily_budget: Daily budget in cxjcoins (0 = no cap).
        minutes: Shift runtime limit.
        schedule: Human description of the availability window.
        dry_run: Use the read-only variant.
    """
    name = actor or agent or "actor"
    normalized_role = role if role in C.ROLES else C.DEFAULT_ROLE
    watched = list(boards or [])
    if board and board not in watched:
        watched.insert(0, board)
    template = DRY_RUN_SYSTEM_PROMPT if dry_run else SHIFT_SYSTEM_PROMPT
    return template.format(
        actor=name,
        role=normalized_role,
        role_intro=ROLE_INTRO[normalized_role],
        instructions=_clip(instructions, 4000) or "_No standing orders yet._",
        desk=board or "_not set_",
        boards=_boards_line(watched),
        schedule=schedule or "always available (no schedule)",
        policy="\n" + POLICY_BOARD_ONLY if policy == C.POLICY_BOARD else "",
        budget=_budget_line(budget),
        daily_budget=_budget_line(daily_budget),
        minutes=minutes,
    )


def describe_agenda(items: List[Dict[str, Any]]) -> str:
    """Render the due tasks of the shift."""
    if not items:
        return "_Nothing is due. Decide what to do from your boards and mandate._"
    lines: List[str] = []
    for item in items:
        lines.append(
            f"- `{item.get('id')}` **{item.get('name')}** "
            f"(wake_at {item.get('wake_at')}, status `{item.get('status') or 'todo'}`)"
        )
        description = _clip(item.get("description"), 1200)
        if description:
            lines.append(f"  - {description}")
        last = _clip(item.get("last_message"), 600)
        if last:
            lines.append(f"  - last message: {last}")
    return "\n".join(lines)


def describe_tasks(items: List[Dict[str, Any]]) -> str:
    """Render the board snapshot of the shift."""
    if not items:
        return "_Your boards are empty._"
    lines: List[str] = []
    for item in items:
        marker = "x" if item.get("done") else " "
        wake = item.get("wake_at") or ""
        scheduled = f" · wake_at {wake}" if wake else ""
        board = item.get("board") or ""
        on_board = f" · {board}" if board else ""
        lines.append(
            f"- [{marker}] `{item.get('id')}` **{item.get('name')}** "
            f"· {item.get('column') or '-'} · status `{item.get('status') or '-'}`"
            f"{scheduled}{on_board}"
        )
    return "\n".join(lines)


def build_shift_prompt(
    *,
    actor: str = "",
    agent: str = "",
    board: str = "",
    boards: List[str] | None = None,
    role: str = C.DEFAULT_ROLE,
    now: str = "",
    trigger: str = "schedule",
    shift_number: int = 0,
    last_shift: str = "",
    instructions: str = "",
    agenda: List[Dict[str, Any]] | None = None,
    tasks: List[Dict[str, Any]] | None = None,
) -> str:
    """Build the user prompt of one shift."""
    name = actor or agent or "actor"
    return SHIFT_USER_PROMPT.format(
        actor=name,
        role=role if role in C.ROLES else C.DEFAULT_ROLE,
        desk=board or "_not set_",
        boards=_boards_line(list(boards or [])),
        now=now,
        trigger=trigger,
        shift_number=int(shift_number or 0) + 1,
        last_shift=_clip(last_shift, 4000) or "_This is your first shift._",
        instructions=_clip(instructions, 8000) or "_No standing orders._",
        agenda=describe_agenda(list(agenda or [])),
        tasks=describe_tasks(list(tasks or [])),
    )


def build_continue_prompt(
    *,
    turn: int,
    max_turns: int,
    remaining: List[Dict[str, Any]],
) -> str:
    """Build the prompt that keeps an ``until_done`` shift going."""
    return CONTINUE_PROMPT.format(
        turn=int(turn),
        max_turns=int(max_turns),
        remaining=describe_tasks(list(remaining or [])),
    )

# Made with ❤️ by codx-junior
