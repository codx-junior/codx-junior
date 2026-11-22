"""
Prompts of the autonomous ticket runtime.

A ticket is a request one project places on another one. When autonomy is
enabled the target project does not wait for a human: it runs an agent loop on
the ticket conversation, with the full project tool set, and keeps working —
inspect, change, test, fix — until the validation gate is green.

The prompt only describes **policy** (what "done" means, how to investigate,
what to do when validation fails). The hard guarantees live in code:

* the conversation is the ticket itself, so every round is auditable;
* validation is executed by the runtime, never trusted to the model;
* the ticket is closed by :class:`~codx.junior.ticketing.autonomy.TicketAutonomyService`
  only after a green gate.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .model import TicketValidation


def _clip(value: Any, max_chars: int) -> str:
    """Return *value* as a stripped string clipped to *max_chars*."""
    text = ("" if value is None else str(value)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n… (truncated)"


SYSTEM_PROMPT = """You are an **autonomous engineer** working a ticket placed by \
**{source}** on your project, **{target}**.

A ticket is a real request from another team: it has acceptance criteria, it
lives in the project repository and it must be delivered working, not described.

## The ticket
- Title: **{name}**
- Kind: `{kind}` · priority: `{priority}`
- Ticket id: `{ticket_id}`
- Source reference: `{reference}`

### Request
{description}

## How you work
You are not answering a question: you are delivering the change.

1. **Dive before editing.** Read the relevant code and tests first
   (`project_structure`, `project_read_file`, `project_search`, `project_grep`,
   `project_map`, `project_trace`). Identify the files, the data flow and the
   existing conventions. Never guess a path or an API.
2. **Implement a complete vertical slice.** Change every layer the request
   needs (code, tests, docs when the project documents behaviour). Do not leave
   TODOs, stubs or "the next step would be".
3. **Run the validation gate.** The target project validates this ticket with:
   {validation}
   Run it yourself while you work (for the project QA suite use `qa_run_tests`
   after `qa_plan`). When it fails, read the failure, fix the cause and run it
   again. **Never weaken or delete a test to make it pass.**
4. **Only green is done.** The runtime runs the same validation independently
   after every round. A ticket is closed only when that gate exits successfully.
   If you cannot make it green, stop and report exactly what blocks you, with
   evidence — do not claim success.
5. **Stay inside the ticket.** Do not make unrelated changes, rename public
   APIs or reformat the repository. If you find unrelated work, report it at
   the end instead of doing it.
   The runtime owns the ticket status and the validation gate: you cannot close,
   decline or reconfigure the ticket, and the ticket tools are disabled for this
   run. Report a blocker instead of trying to work around the gate.
6. **Never ask questions.** Nobody is waiting to answer during the run. Choose
   the conservative option, document the assumption in your report and continue.
   Ignore any generic chat instruction about suggested replies or asking the
   user to decide: no human can click during this run.
7. **Leave the workspace clean.** The worktree branch is `{branch}`. Commit
   nothing yourself unless the ticket asks for it; the runtime commits the
   verified result.

## Your tools and workspace
You have the full tool set of the target project (files, shell, git, browser,
knowledge). The workspace is isolated for this ticket: your changes do not
touch the main checkout.

## Delivery report
When the validation is green (or when you are genuinely blocked), finish with a
markdown report:

```md
## What happened
## What I did
## Demo
## Next tasks
```

- `## What happened` — the situation, what you found and the decision, with
  evidence (`path:line`, command output).
- `## What I did` — the delivered behaviour, one line per file/area.
- `## Demo` — exactly one runnable command/test that proves the result.
- `## Next tasks` — only unrelated follow-ups, or `None — request delivered`.

End with `**Delivered.**` only when the validation is green. If it is not,
write `**Blocked.**` and the reason.

## Runtime context
- Round {{round}} of at most {max_rounds}.
- Started at (UTC): {started_at}
- Workspace: {workspace}
"""


ROUND_PROMPT = """# Ticket work order

You are working ticket `{ticket_id}`: **{name}**.

{description}

## Acceptance criteria
{criteria}

## Validation gate (the runtime runs it independently)
{validation}

## Work order
1. Inspect the codebase and the existing tests before changing anything.
2. Implement the complete change requested by the ticket.
3. Run the validation command(s) above. Fix every failure and re-run until the
   gate is green.
4. If a hard external blocker makes the ticket impossible, stop and report it
   with evidence instead of inventing a result.
5. End with the delivery report (`## What happened`, `## What I did`,
   `## Demo`, `## Next tasks`) and `**Delivered.**` only when the gate is green.

This is round {round} of at most {max_rounds}.
"""


CONTINUE_PROMPT = """# Validation failed — fix it and continue

The runtime ran the ticket validation gate and it is **red**. The ticket is not
done and will not be closed until the gate is green.

## Validation command
{command}

## Output (tail)
```
{output}
```

## What to do now
1. Read the failure carefully. Reproduce it if the output is incomplete.
2. Fix the **cause**, not the test. Do not weaken, skip or delete tests.
3. Run the validation again. Repeat until it is green.
4. If the failure is genuinely outside this ticket (a broken dependency, a
   missing credential, a failure that pre-exists and blocks the build), say so
   explicitly with evidence and stop — the runtime will report it as blocked.

Round {round} of at most {max_rounds}. The ticket has been open for {elapsed}.
"""


def _render_validation(validation: TicketValidation) -> str:
    """Render the configured validation gate as a prompt fragment."""
    source = validation.effective_source()
    if source == "commands":
        lines = [f"`{command}`" for command in validation.commands]
        return "\n".join(f"- {line}" for line in lines) or "_no command configured_"
    if source == "none":
        return (
            "_no automated validation: the target opted out. Be extra careful and "
            "prove the result with a runnable demo._"
        )
    suite = validation.suite or "auto"
    paths = ", ".join(f"`{path}`" for path in validation.paths)
    extra = f" limited to {paths}" if paths else ""
    return f"- QA test suite `{suite}`{extra}"


def build_ticket_system(
    *,
    ticket_id: str,
    name: str,
    description: str,
    kind: str = "",
    priority: Optional[str] = None,
    reference: Optional[str] = None,
    source: str = "",
    target: str = "",
    validation: Optional[TicketValidation] = None,
    branch: str = "",
    workspace: str = "",
    started_at: str = "",
    max_rounds: int = 6,
) -> str:
    """
    Build the system prompt of one autonomous ticket run.

    Args:
        ticket_id: Ticket id (== chat id).
        name: Ticket title.
        description: Full request body.
        kind: ``bug`` / ``feature`` / ``change`` / ``question``.
        priority: Optional priority.
        reference: Optional source-side reference (issue, incident, ...).
        source: Name of the requesting project.
        target: Name of the target (owning) project.
        validation: Validation declaration (rendered in the prompt).
        branch: Workspace branch of the run.
        workspace: Workspace path of the run.
        started_at: ISO start instant.
        max_rounds: Maximum rounds of the run.

    Returns:
        The system prompt. ``{{round}}`` is intentionally left for the caller
        (the round prompt already states the number, and the runtime injects it
        when a model needs it).
    """
    gate = validation or TicketValidation()
    rendered = SYSTEM_PROMPT.format(
        source=source or "another project",
        target=target or "this project",
        name=name or ticket_id,
        ticket_id=ticket_id,
        description=_clip(description, 12000) or "_No description._",
        kind=kind or "change",
        priority=priority or "normal",
        reference=reference or "—",
        validation=_render_validation(gate),
        branch=branch or "(main checkout)",
        workspace=workspace or "(main checkout)",
        started_at=started_at or "",
        max_rounds=max(1, int(max_rounds)),
    )
    # ``format`` already collapsed ``{{round}}`` into ``{round}``: replace the
    # free-form marker with a plain human label.
    return rendered.replace("{round}", "1")


def build_round_prompt(
    *,
    ticket_id: str,
    name: str,
    description: str,
    validation: Optional[TicketValidation] = None,
    round: int = 1,
    max_rounds: int = 6,
) -> str:
    """
    Build the first user message of a round.

    Args:
        ticket_id: Ticket id.
        name: Ticket title.
        description: Full request body (contains the acceptance criteria).
        validation: Validation declaration.
        round: 1-based round number.
        max_rounds: Maximum rounds of the run.

    Returns:
        The work order shown to the agent.
    """
    gate = validation or TicketValidation()
    return ROUND_PROMPT.format(
        ticket_id=ticket_id,
        name=name,
        description=_clip(description, 12000) or "_No description._",
        criteria=(
            "Every requirement in the request above, including every explicit "
            "expected behaviour and acceptance criterion."
        ),
        validation=_render_validation(gate),
        round=max(1, int(round)),
        max_rounds=max(1, int(max_rounds)),
    )


def build_continue_prompt(
    *,
    validation_output: str,
    command: str = "",
    round: int = 1,
    max_rounds: int = 6,
    elapsed: str = "",
) -> str:
    """
    Build the prompt that follows a red validation gate.

    Args:
        validation_output: Output/evidence of the failed gate.
        command: Command that failed.
        round: Next round number.
        max_rounds: Maximum rounds of the run.
        elapsed: Human description of the elapsed time.

    Returns:
        The "fix it" message.
    """
    return CONTINUE_PROMPT.format(
        command=command or "(see output)",
        output=_clip(validation_output, 6000) or "(no output)",
        round=max(1, int(round)),
        max_rounds=max(1, int(max_rounds)),
        elapsed=elapsed or "a few minutes",
    )


def describe_tickets(items: List[Dict[str, Any]]) -> str:
    """Render a short list of autonomous tickets (status/API helpers)."""
    if not items:
        return "_No autonomous tickets._"
    return "\n".join(
        f"- `{item.get('ticket_id')}` **{item.get('name')}** · "
        f"{item.get('state')} · {item.get('status')}"
        for item in items
    )


__all__ = [
    "SYSTEM_PROMPT",
    "ROUND_PROMPT",
    "CONTINUE_PROMPT",
    "build_ticket_system",
    "build_round_prompt",
    "build_continue_prompt",
    "describe_tickets",
]

# Made with ❤️ by codx-junior
