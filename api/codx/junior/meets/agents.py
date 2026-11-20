"""
Meeting agents: the AI profiles that attend a meeting and the minute taker.

Two prompts live here:

* **Profile answer** – one profile contributes to the live conversation. The
  profile instructions, the meeting agenda, the recent transcript and a fresh
  task-status snapshot are composed into a single system prompt. The model runs
  without agent tools (``harness=False``): a meeting answer must be fast and
  must not start writing code or opening browsers by itself. The actions it
  proposes are materialised later, from the minutes, by explicit user actions.
* **Minutes** – the secretary turns the transcript into a summary, decisions and
  action items. The answer is parsed as JSON so the summary page can render and
  create one task per item.

Both functions are async and receive the request project session. They never
emit socket events: the orchestrator owns the lifecycle.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from typing import Any, Dict, List, Optional

from codx.junior.meets.context import (
    MAX_MINUTES_TASKS,
    format_task_context,
    task_snapshot,
)
from codx.junior.meets.model import Meet, MeetActionItem, MeetMinutes
from codx.junior.meets.profiles import load_profile, resolve_profile_voice
from codx.junior.utils.utils import extract_json_blocks

logger = logging.getLogger(__name__)

#: Maximum characters of transcript sent to a profile answer.
MAX_TRANSCRIPT_CHARS = 12000
#: Maximum characters of transcript sent to the minute taker.
MAX_MINUTES_TRANSCRIPT_CHARS = 40000
#: Answers are clipped before they are stored/spoken.
MAX_ANSWER_CHARS = 2400

_FENCE_RE = re.compile(r"```[\s\S]*?```")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MULTISPACE_RE = re.compile(r"[ \t]+")
_MULTILINE_RE = re.compile(r"\n{3,}")


def _clean_answer(value: str) -> str:
    """Normalize a model answer without destroying its content."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^```[\w-]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = _MULTILINE_RE.sub("\n\n", text)
    return text.strip()[:MAX_ANSWER_CHARS]


def speech_text(value: str, max_chars: int = 1200) -> str:
    """
    Convert a profile answer into text safe to speak aloud.

    Code blocks, markdown links, bullet markers and repeated whitespace are
    removed; the result keeps the meaning and stays under ``max_chars`` so a
    single answer does not monopolise the call.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = _FENCE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|[-*+]|\d+\.)\s+", "", text)
    text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)
    text = text.replace("**", "").replace("__", "").replace("~~", "")
    text = _MULTISPACE_RE.sub(" ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = _MULTILINE_RE.sub("\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    # Prefer cutting at a sentence boundary.
    cut = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if cut > max_chars * 0.6:
        clipped = clipped[: cut + 1]
    return clipped.strip() + " …"


def _transcript_text(meet: Meet, max_chars: int) -> str:
    """Render the last part of the transcript, bounded by ``max_chars``."""
    lines: List[str] = []
    size = 0
    for entry in reversed(meet.transcript):
        speaker = entry.speaker or "someone"
        prefix = f"[{entry.at[11:19]}] " if len(entry.at) >= 19 else ""
        line = f"{prefix}{speaker}: {entry.text}"
        if entry.interrupted:
            line += " (interrupted)"
        if size + len(line) > max_chars and lines:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(reversed(lines))


def _participants_text(meet: Meet) -> str:
    """Render the participant list for a prompt."""
    names: List[str] = []
    for participant in meet.participants:
        if participant.kind.value == "profile":
            names.append(f"{participant.name or participant.username} (AI profile)")
        else:
            names.append(participant.name or participant.username)
    return ", ".join(names) or "nobody"


def _profile_system_prompt(
    meet: Meet,
    profile: Any,
    snapshot: Dict[str, Any],
) -> str:
    """Build the system prompt of one profile participant."""
    instructions = (profile.parsed_content or profile.content or "").strip()
    description = (profile.description or "").strip()
    return f"""You are **{profile.name}**, an AI participant in a live work meeting.
{description}

Meeting: {meet.name}
Agenda: {meet.agenda or "review the status of the team's tasks and decide next steps"}
Participants: {_participants_text(meet)}

Your role instructions:
{instructions or "(no additional instructions — act as a focused member of the team)"}

{format_task_context(snapshot)}

How to take part:
- Speak in the first person, as {profile.name}, and address the team directly.
- Be concise: at most 3 short sentences (the answer is spoken aloud).
- Do not repeat what was already said. Add a fact, a risk, a decision or a concrete next step.
- Use the task status above. If something is blocked, say so; if a task should be created or scheduled, propose it explicitly.
- Never talk over someone else. If the latest transcript line is from another participant and does not need you, answer with exactly "PASS".
- Do not use markdown headings, tables or code blocks. Plain conversational text only.
- Do not claim you have executed an action. The task is created later, from the minutes.
"""


def _profile_prompt(meet: Meet, profile_name: str) -> str:
    """Build the human prompt of one profile turn."""
    transcript = _transcript_text(meet, MAX_TRANSCRIPT_CHARS)
    return f"""Recent conversation:
{transcript or "(the meeting just started)"}

Add your contribution now as {profile_name}.
Remember: if you have nothing useful to add, answer only PASS.
"""


async def generate_profile_answer(
    session: Any,
    meet: Meet,
    profile_name: str,
    *,
    instructions: str = "",
) -> Dict[str, Any]:
    """
    Generate one profile answer.

    Args:
        session: Project session.
        meet: Meeting (already contains the transcript up to this turn).
        profile_name: Name of the profile that answers.
        instructions: Optional extra instruction from the orchestrator.

    Returns:
        ``{profile, text, speech, model, passed, error}``. ``passed`` is true
        when the profile chose not to speak.
    """
    from codx.junior.ai.ai import AI

    profile = load_profile(session, profile_name)
    if profile is None:
        return {
            "profile": profile_name,
            "text": "",
            "speech": "",
            "passed": True,
            "error": f"profile '{profile_name}' not found",
        }

    snapshot = task_snapshot(
        session, board=meet.board, task_ids=meet.task_ids
    )
    system = _profile_system_prompt(meet, profile, snapshot)
    prompt = _profile_prompt(meet, profile.name or profile_name)
    if instructions:
        prompt += f"\nFacilitator instruction: {instructions.strip()}\n"

    try:
        ai = AI(
            settings=session.settings,
            llm_model=profile.llm_model or None,
            user=getattr(session, "user", None),
            system=system,
            harness=False,
        )
        result = await ai.a_chat_run(prompt=prompt)
        answer = _clean_answer(getattr(result, "content", "") or "")
        if not answer or answer.strip().upper() in {"PASS", "PASS.", "NO COMMENT"}:
            return {
                "profile": profile.name,
                "text": "",
                "speech": "",
                "passed": True,
                "model": profile.llm_model or "",
                "error": None,
            }
        resolved = resolve_profile_voice(session, profile)
        speech = (
            ""
            if resolved.source == "disabled"
            else speech_text(answer, max_chars=meet.settings.max_speech_chars)
        )
        return {
            "profile": profile.name,
            "text": answer,
            "speech": speech,
            "passed": False,
            "model": profile.llm_model or "",
            "error": None,
        }
    except Exception as ex:  # noqa: BLE001 - a failed profile must not break the call
        logger.warning("Meeting: profile '%s' failed to answer: %s", profile_name, ex)
        return {
            "profile": profile_name,
            "text": "",
            "speech": "",
            "passed": True,
            "model": profile.llm_model or "",
            "error": str(ex),
        }


# ---------------------------------------------------------------------------
# Minutes
# ---------------------------------------------------------------------------

MINUTES_SYSTEM_PROMPT = """You are the minute taker of a work meeting.
Turn the transcript into accurate, useful minutes. Never invent facts, owners
or dates that are not in the conversation. Prefer the actual names used in the
meeting. Return ONLY a JSON object with this schema:

{
  "summary": "2-4 sentence executive summary",
  "key_points": ["important fact or update", "..."],
  "decisions": ["decision that was actually made", "..."],
  "action_items": [
    {
      "title": "short actionable title",
      "description": "complete context: what, why and done criteria",
      "assignee": "username or profile name, empty when nobody was named",
      "profile": "profile that should execute it, empty when unknown",
      "priority": "high | medium | low",
      "due_date": "YYYY-MM-DD or null",
      "scheduled_at": "ISO-8601 instant or null",
      "run": false
    }
  ]
}

Rules:
- action_items are tasks; include only real follow-ups agreed in the conversation.
- If the team explicitly asked to run something now, set "run": true on that item.
- If a follow-up has a date or time, fill due_date/scheduled_at; otherwise null.
- Keep the summary factual and the action items small and verifiable.
- Use the task status context to detect blocked work and to avoid duplicating existing tasks.
"""


def _minutes_prompt(meet: Meet, snapshot: Dict[str, Any], instructions: str) -> str:
    """Build the minute-taker prompt."""
    transcript = _transcript_text(meet, MAX_MINUTES_TRANSCRIPT_CHARS)
    return f"""Meeting: {meet.name}
Agenda: {meet.agenda or "(no agenda)"}
Participants: {_participants_text(meet)}
Started: {meet.created_at}
Ended: {meet.ended_at or "still running"}

Task status context:
{format_task_context(snapshot, max_tasks=MAX_MINUTES_TASKS)}

Transcript:
{transcript or "(no utterances were recorded)"}

Extra instructions: {instructions or "(none)"}

Return the minutes JSON now.
"""


def _as_list(value: Any) -> List[str]:
    """Normalize a model-provided list of strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result: List[str] = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("text") or item.get("title") or ""
        else:
            text = item
        text = str(text or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _as_action_item(value: Any) -> Optional[MeetActionItem]:
    """Validate one model-provided action item."""
    if not isinstance(value, dict):
        return None
    title = str(value.get("title") or value.get("name") or "").strip()
    if not title:
        return None
    description = str(value.get("description") or value.get("context") or "").strip()
    priority = str(value.get("priority") or "medium").strip().lower()
    if priority not in {"high", "medium", "low"}:
        priority = "medium"
    return MeetActionItem(
        id=str(uuid.uuid4()),
        title=title[:220],
        description=description or title,
        assignee=str(value.get("assignee") or "").strip()[:200],
        profile_name=str(value.get("profile") or value.get("profile_name") or "").strip()[:200],
        priority=priority,
        due_date=(str(value["due_date"]).strip() if value.get("due_date") else None),
        scheduled_at=(
            str(value["scheduled_at"]).strip() if value.get("scheduled_at") else None
        ),
        run=bool(value.get("run", False)),
    )


def _parse_minutes(content: str) -> Dict[str, Any]:
    """Extract the JSON object of the minutes from a model answer."""
    text = str(content or "").strip()
    if not text:
        return {}
    blocks = list(extract_json_blocks(text))
    for block in blocks:
        if isinstance(block, dict) and (
            "summary" in block or "action_items" in block or "decisions" in block
        ):
            return block
    # Fallback: the model may return raw JSON without a fenced block.
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    return {}


async def generate_minutes(
    session: Any,
    meet: Meet,
    *,
    instructions: str = "",
    translate: bool = False,
) -> MeetMinutes:
    """
    Generate the minutes of ``meet`` from its transcript.

    Returns:
        A :class:`MeetMinutes` document. On failure ``status='error'`` and the
        error is stored in ``error`` (it never raises to the caller).
    """
    from codx.junior.ai.ai import AI

    snapshot = task_snapshot(
        session, board=meet.board, task_ids=meet.task_ids, include_done=True
    )
    prompt = _minutes_prompt(meet, snapshot, instructions)
    model = ""
    content = ""
    try:
        ai = AI(
            settings=session.settings,
            llm_model=None,
            user=getattr(session, "user", None),
            system=MINUTES_SYSTEM_PROMPT,
            harness=False,
        )
        result = await ai.a_chat_run(prompt=prompt)
        content = getattr(result, "content", "") or ""
        model = str(getattr(result, "model", "") or "")
    except Exception as ex:  # noqa: BLE001 - surfaced in the minutes status
        logger.warning("Meeting: minutes generation failed: %s", ex)
        return MeetMinutes(
            status="error",
            error=str(ex),
            attendees=list(dict.fromkeys(
                [entry.speaker for entry in meet.transcript if entry.speaker]
            )),
        )

    payload = _parse_minutes(content)
    if not payload:
        # Unstructured answer: keep it as the summary instead of losing the turn.
        return MeetMinutes(
            status="ready",
            summary=_clean_answer(content)[:4000],
            key_points=[],
            decisions=[],
            action_items=[],
            attendees=_attendees(meet),
            generated_at=None,
            model=model or None,
            error="the model did not return the expected JSON; the summary holds its text",
        )

    action_items = [
        item
        for item in (_as_action_item(value) for value in payload.get("action_items") or [])
        if item is not None
    ]
    attendees = _as_list(payload.get("attendees")) or _attendees(meet)
    return MeetMinutes(
        status="ready",
        summary=str(payload.get("summary") or "").strip(),
        key_points=_as_list(payload.get("key_points") or payload.get("points")),
        decisions=_as_list(payload.get("decisions")),
        action_items=action_items,
        attendees=attendees,
        model=model or None,
    )


def _attendees(meet: Meet) -> List[str]:
    """Build the attendee list from the participants and the transcript."""
    names = [
        participant.name or participant.username
        for participant in meet.participants
        if participant.status.value != "left"
    ]
    for entry in meet.transcript:
        if entry.speaker and entry.speaker not in names:
            names.append(entry.speaker)
    return [name for name in dict.fromkeys(names) if name]
