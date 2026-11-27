"""
Harness-mode prompt and naming helpers.

A **harness session** is a normal codx-junior task chat executed by the
``codx-junior`` profile in ``task`` mode. The only processing difference with a
regular task is the *harness brief* appended to the first user message: it turns
the request into the closed loop a coding harness follows (survey, reproduce,
analyse, implement, test, verify, report) and fixes the shape of the final
delivery report.

Keeping the brief here (instead of editing the built-in profile) has two
advantages:

* the brief is persisted with the session, so a resume/rerun months later still
  carries the same contract;
* regular chats that use ``codx-junior`` are untouched.

The client strips everything from :data:`HARNESS_BRIEF_MARKER` on when it shows
the first message, so the user reads their own request, not the contract.
"""

from __future__ import annotations

import re
from typing import Optional

#: Board that owns the harness sessions of a project.
HARNESS_BOARD = "Harness"

#: Column a new harness session lands in (the chat status is ``doing``).
HARNESS_COLUMN = "Working"

#: Tag used to recognise a harness session without parsing its meta_data.
HARNESS_TAG = "harness-mode"

#: Key of the session descriptor stored in ``Chat.meta_data``.
HARNESS_META_KEY = "harness_mode"

#: Profile that executes a harness session (the built-in harness agent).
HARNESS_PROFILE = "codx-junior"

#: Chat mode of a harness session (autonomous task contract).
HARNESS_MODE = "task"

#: Marker that separates the user's request from the appended brief.
HARNESS_BRIEF_MARKER = "\n\n---\n\n## Harness mode\n"

#: Column layout the session board is created with (title, status).
HARNESS_BOARD_COLUMNS = (
    {"title": "Backlog", "status": "todo"},
    {"title": "Working", "status": "doing"},
    {"title": "Done", "status": "done"},
)

#: The processing contract: DSH-style "go all the way" plus a fixed report.
HARNESS_BRIEF = """## Harness mode

You are running as a coding harness. This request is a ticket: resolve it end
to end in this turn, not a plan, not a diagnosis, not one file.

**Loop (use your tools, in this order unless the evidence says otherwise)**

1. **Survey** — `project_structure`, `project_search`, `project_grep`,
   `project_glob` and `project_read_file` to find the real files, call sites and
   the tests that already cover the behaviour.
2. **Reproduce** — confirm the current behaviour with the strongest evidence
   available: a failing test, the exact code path, a command output. Never fix
   code you have not understood.
3. **Analyse** — state the root cause and the alternatives you rejected. Choose
   the smallest correct solution; do not widen the change.
4. **Implement** — write the change across every layer it needs (data, API,
   client, prompts, docs). Keep unrelated files untouched.
5. **Test** — add or update the tests that prove the behaviour. Run the targeted
   suite with `qa_run_tests`; create the environment or dependencies you need
   (Docker tools are available when a service must run) and make the test
   command reproducible.
6. **Verify** — run `qa_review_changes` (and `security_review_changes` when auth,
   secrets, dependencies or shell are touched) and fix what fails. A warning is
   not a delivery.
7. **Report** — close with the sections below, with real paths and command
   output. Never ask for permission to continue; stop only for a hard blocker
   (missing credential, a product decision only the user can make, a destructive
   action, or a sandbox boundary).

**Do not hand the turn back with a question**

Asking "should I continue?" is not a delivery and not a blocker. If the next
step is obvious from the ticket, the repository or the evidence you collected,
take it and keep working. When you really need the user's decision, do all the
work that does not depend on it first and end with ONE fenced `options` block of
concrete replies (the interface renders them as buttons, so the user can continue
with a click). A bare question without options wastes a turn.

**Required delivery report**

Use the platform delivery contract (the same four sections every codx-junior
delivery uses), with the problem and the tests made explicit:

```
## What happened
The observed behaviour, how it was reproduced, and the evidence.

## What I did
The root cause (exact file/symbol and mechanism), the solution you chose and
why, and one line per file changed:
- `path/to/file` — what changed and why.

## Demo
The tests you added or updated (`path/to/test` — what it proves) and the exact
command + result that verifies the ticket. This is the runnable evidence; it
must work from a clean checkout.

## Next tasks
Only genuinely out-of-scope follow-ups; otherwise "None — request delivered".

**Delivered.** Run the Demo above. If something fails, reply with the failing step.
```
"""


def session_brief(prompt: str) -> str:
    """
    Return the first user message of a harness session.

    The user's request is kept verbatim; the contract is appended after
    :data:`HARNESS_BRIEF_MARKER` so it can be hidden in the transcript and
    audited separately.

    Args:
        prompt: The request written by the user.

    Returns:
        The request plus the harness brief. An empty prompt yields an empty
        string (validation happens in the service).
    """
    text = str(prompt or "").strip()
    if not text:
        return ""
    return f"{text}{HARNESS_BRIEF_MARKER}{HARNESS_BRIEF}"


def strip_session_brief(content: str) -> str:
    """
    Remove the appended harness brief from a stored user message.

    Args:
        content: Stored message content.

    Returns:
        The user's original request (the input unchanged when no brief exists).
    """
    text = str(content or "")
    if HARNESS_BRIEF_MARKER in text:
        return text.split(HARNESS_BRIEF_MARKER, 1)[0].rstrip()
    return text


def session_name(prompt: str, limit: int = 72) -> str:
    """
    Build a readable session name from the first line of the request.

    Args:
        prompt: The user's request.
        limit: Maximum number of characters.

    Returns:
        A single-line, length-bounded title.
    """
    text = str(prompt or "")
    # Markdown headings/bullets read badly as a card title.
    text = re.sub(r"^\s*[#>*\-]+\s*", "", text, flags=re.MULTILINE)
    for line in text.splitlines():
        candidate = " ".join(line.split()).strip()
        if candidate:
            return candidate[:limit].rstrip() + ("…" if len(candidate) > limit else "")
    return "New harness session"


def is_harness_meta(meta: Optional[dict]) -> bool:
    """
    Tell whether a chat meta_data marks the chat as a harness session.

    Args:
        meta: ``Chat.meta_data`` (may be None).

    Returns:
        True when the harness descriptor is present and enabled.
    """
    if not isinstance(meta, dict):
        return False
    descriptor = meta.get(HARNESS_META_KEY)
    if isinstance(descriptor, dict):
        return bool(descriptor.get("enabled", True))
    return bool(descriptor)
