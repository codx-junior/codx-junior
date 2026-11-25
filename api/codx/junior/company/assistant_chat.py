"""
Company assistant chat: a real ChatView conversation backed by a project chat.

The company shell does not reimplement chat. It provisions (once) a normal
``Chat`` hosted by one of the company's visible projects, with the
``codx-junior`` profile, and the client renders that chat with the standard
``ChatView``. This module owns the two things the generic chat engine does not
know:

* **Provisioning** the assistant chat (deterministic id, idempotent, welcome
  message, company metadata). It is a normal chat, so streaming, attachments,
  voice, mentions, tools, unread state and message history keep working.
* **Company context and role guardrails** for the chat engine: the prompt gets
  the company/project catalog, and a read-only member receives investigation
  tools only even though the profile is ``codx-junior`` (which grants ``*``).

The chat is not a company channel: it does not live in ``Company.chats`` and the
Messenger never lists it. It is an internal conversation with the assistant.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

#: Profile the assistant conversation always runs with.
ASSISTANT_PROFILE = "codx-junior"

#: Chat name shown by ChatView.
ASSISTANT_NAME = "codx-junior"

#: Board/column that keeps the assistant out of normal task queues while still
#: being a normal Chat (the old inline assistant used the same board).
ASSISTANT_BOARD = "codx-junior"
ASSISTANT_COLUMN = "assistant"

#: Tools available to a read-only company member. Anything that writes
#: (company, teams, tickets, files, shell) stays out; the profile grants every
#: tool, so this is the second gate that keeps a viewer from becoming an
#: operator through the chat.
READ_ONLY_TOOLS: Set[str] = {
    "project_map",
    "project_locate",
    "project_trace",
    "project_impact",
    "project_health",
    "project_read_file",
    "search_codx",
    "ticket_query",
    "team_context",
    "fetch_webpage",
    "web_search",
}

WELCOME = (
    "Hi — I'm **codx-junior**, your company assistant. I can see the company, "
    "its teams, projects, goals and ticket queues, and I can act on them with "
    "the project tools.\n\n"
    "Ask me anything, or start with one of the suggestions above. I can place "
    "a ticket on any project, let it run unattended and report the delivery for "
    "your review."
)


def _clean(value: Any) -> str:
    """Return a stripped string (empty for None)."""
    return str(value or "").strip()


def _safe_id(value: str) -> str:
    """Make a company id safe for a deterministic chat id/folder name."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", _clean(value)) or "company"


def assistant_chat_id(company_id: str) -> str:
    """Return the deterministic id of the company assistant chat."""
    return f"assistant-{_safe_id(company_id)}"


def _manager() -> Any:
    """Build a company manager lazy (avoids an import cycle at module load)."""
    from codx.junior.company.manager import CompanyManager

    return CompanyManager()


def _company(manager: Any, company_id: Any) -> Any:
    """Resolve a company id/document."""
    company = company_id
    if not hasattr(company, "id"):
        company = manager.get_company(_clean(company_id))
    return company


def company_role(manager: Any, company: Any, user: Any) -> str:
    """
    Return the company role of *user* ('' when not a member).

    The rules mirror :func:`codx.junior.api.company._company_role` without
    importing the API layer: global admin → owner → membership, and an
    unowned company is open so the bootstrap owner can finish setup.
    """
    if user is None:
        return ""
    if _clean(getattr(user, "role", "")) == "admin":
        return "admin"
    owner = _clean(getattr(company, "owner", ""))
    username = _clean(
        getattr(user, "username", "")
        or (user if isinstance(user, str) else "")
    )
    if not owner:
        return "admin"
    if username and username == owner:
        return "admin"
    try:
        role = user.company_role(company.id) if hasattr(user, "company_role") else ""
    except Exception:
        role = ""
    return _clean(role)


def can_manage_company(manager: Any, company: Any, user: Any) -> bool:
    """True when the user may change the company (admin or manager)."""
    return company_role(manager, company, user) in ("admin", "manager")


def company_assistant_tool_allowlist(
    company_id: str, user: Any
) -> Optional[Set[str]]:
    """
    Return the tool allowlist of the assistant turn.

    ``None`` means "use the profile grants" (company managers get every tool).
    A set restricts the turn to read-only investigation tools.
    """
    try:
        manager = _manager()
        company = _company(manager, company_id)
        if can_manage_company(manager, company, user):
            return None
        return set(READ_ONLY_TOOLS)
    except Exception as ex:  # pragma: no cover - a broken catalog fails closed
        logger.warning("company assistant: role lookup failed: %s", ex)
        return set(READ_ONLY_TOOLS)


def company_assistant_context(company_id: str, user: Any = None) -> str:
    """
    Build the company section appended to the assistant chat system prompt.

    Args:
        company_id: Company id.
        user: Authenticated user (used for the role note).

    Returns:
        Markdown context, or '' when the company cannot be resolved.
    """
    try:
        manager = _manager()
        company = _company(manager, company_id)
    except Exception as ex:
        logger.warning("company assistant: cannot build context: %s", ex)
        return ""

    role = company_role(manager, company, user) or "member"
    lines = [
        "## COMPANY ASSISTANT",
        f"You are the always-on assistant of **{company.name}** "
        f"(company id: `{company.id}`).",
        f"Today (UTC): {datetime.now(timezone.utc).isoformat(timespec='minutes')}Z.",
        f"The signed-in user's company role is **{role}**.",
    ]
    if company.description:
        lines.append(f"Company description: {company.description}")
    if company.mission:
        lines.append(f"Mission: {company.mission}")
    if company.vision:
        lines.append(f"Vision: {company.vision}")

    projects = []
    try:
        for project_id in manager.visible_project_ids(company):
            reference = company.project(project_id)
            settings = manager.project_settings(project_id)
            name = (
                _clean(getattr(settings, "project_name", ""))
                or _clean(getattr(reference, "name", ""))
                or project_id
            )
            projects.append(
                {
                    "project_id": project_id,
                    "name": name,
                    "available": settings is not None,
                }
            )
    except Exception as ex:  # pragma: no cover - context is best effort
        logger.debug("company assistant: project catalog failed: %s", ex)
    if projects:
        lines.append("Projects (use the id when placing a ticket):")
        for project in projects:
            suffix = "" if project["available"] else " — not discovered"
            lines.append(f"- {project['name']} (id: `{project['project_id']}`){suffix}")
    else:
        lines.append("Projects: none linked yet.")

    if company.teams:
        lines.append("Teams:")
        for team in company.teams[:12]:
            lines.append(
                f"- {team.name}: {team.human_count} human + {team.ai_count} AI, "
                f"autonomy {team.autonomy}"
            )

    open_missions = [mission for mission in company.missions if mission.is_open]
    if open_missions:
        lines.append(f"Open goals: {len(open_missions)}. Recent:")
        for mission in open_missions[:8]:
            lines.append(
                f"- {mission.title}: {mission.status}, project "
                f"`{mission.project_id or 'none'}`, {mission.progress}%"
            )
    try:
        budget = manager.budget_state(company)
        lines.append(
            "AI budget: today "
            f"{budget.get('spent_today_cxjcoins')} / "
            f"{budget.get('daily_limit_cxjcoins')} cxj."
        )
    except Exception:
        pass

    if role in ("admin", "manager"):
        lines.append(
            "You run with the full profile tool set. Use `company_management` for "
            "company/team/goal operations and `company_ticket` for company tickets. "
            "When the owner asks for project work, place a ticket with a complete "
            "request and acceptance criteria; it runs unattended and is reviewed in "
            "the company Tickets screen. Confirm destructive actions first."
        )
    else:
        lines.append(
            "This user is a read-only company member: you have investigation tools "
            "only. Answer from the context/tools and, for a change, tell them which "
            "manager action is needed. Never claim you changed something."
        )
    return "\n".join(lines)


def ensure_assistant_chat(
    manager: Any,
    company_id: Any,
    actor: str = "system",
) -> Dict[str, Any]:
    """
    Ensure the company assistant chat exists and return its locator.

    The chat is a normal ``Chat`` hosted by the first available company project,
    with the ``codx-junior`` profile and the company metadata. Calling this
    repeatedly is safe: the deterministic id is loaded and only the missing
    pieces (profile, metadata, welcome message) are repaired.

    Args:
        manager: Company manager.
        company_id: Company id or document.
        actor: Username recorded as the chat user/creator.

    Returns:
        ``chat_id``, ``project_id``, ``owner_project_id``, ``name``, ``mode``,
        ``profiles`` and ``created``.

    Raises:
        CompanyConflict: When the company has no available project to host the
            conversation.
    """
    from codx.junior.company.manager import CompanyConflict
    from codx.junior.db import Chat, Message

    company = _company(manager, company_id)
    project_id = ""
    for candidate in manager.visible_project_ids(company):
        if manager.project_settings(candidate) is not None:
            project_id = candidate
            break
    if not project_id:
        raise CompanyConflict(
            "Link a project to this company before using the assistant: the "
            "conversation needs a project to live in."
        )

    chat_id = assistant_chat_id(company.id)
    chat_manager = manager._chat_manager_for(project_id)
    existing = chat_manager.find_by_id(chat_id)
    created = existing is None

    if existing is None:
        now = datetime.now(timezone.utc).isoformat()
        chat = Chat(
            id=chat_id,
            name=ASSISTANT_NAME,
            description=f"Company assistant for {company.name}",
            mode="chat",
            board=ASSISTANT_BOARD,
            column=ASSISTANT_COLUMN,
            profiles=[ASSISTANT_PROFILE],
            users=[actor] if _clean(actor) else [],
            owner_project_id=project_id,
            project_id=project_id,
            tags=["company", f"company:{company.id}", "assistant"],
            meta_data={
                "company_id": company.id,
                "company_assistant": True,
            },
            messages=[
                Message(
                    doc_id=str(uuid.uuid4()),
                    role="assistant",
                    content=WELCOME,
                    user=ASSISTANT_PROFILE,
                    project_id=project_id,
                    created_at=now,
                    updated_at=now,
                    done=True,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        saved = chat_manager.save_chat(chat=chat)
    else:
        saved = existing
        changed = False
        if _clean(getattr(saved, "mode", "")) != "chat":
            saved.mode = "chat"
            changed = True
        profiles = list(getattr(saved, "profiles", None) or [])
        if ASSISTANT_PROFILE not in profiles:
            saved.profiles = [ASSISTANT_PROFILE, *profiles]
            changed = True
        meta = dict(getattr(saved, "meta_data", None) or {})
        if meta.get("company_id") != company.id or not meta.get("company_assistant"):
            meta.update({"company_id": company.id, "company_assistant": True})
            saved.meta_data = meta
            changed = True
        if not list(getattr(saved, "messages", None) or []):
            now = datetime.now(timezone.utc).isoformat()
            saved.messages = [
                Message(
                    doc_id=str(uuid.uuid4()),
                    role="assistant",
                    content=WELCOME,
                    user=ASSISTANT_PROFILE,
                    project_id=project_id,
                    created_at=now,
                    updated_at=now,
                    done=True,
                )
            ]
            changed = True
        if changed:
            saved = chat_manager.save_chat(chat=saved) or saved

    return {
        "chat_id": getattr(saved, "id", chat_id),
        "project_id": getattr(saved, "project_id", None) or project_id,
        "owner_project_id": getattr(saved, "owner_project_id", None) or project_id,
        "name": getattr(saved, "name", None) or ASSISTANT_NAME,
        "mode": getattr(saved, "mode", None) or "chat",
        "profiles": list(getattr(saved, "profiles", None) or [ASSISTANT_PROFILE]),
        "created": created,
    }
