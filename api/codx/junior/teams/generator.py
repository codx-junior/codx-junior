"""
Team generation from a plain-language need.

A manager says *"I need an IT team"* or *"someone to take care of the
maintenance tickets"* — not a profile list. This module turns that sentence into
a **proposal** and, when asked, into real profiles + a team.

Two steps, deliberately separated so the UI can preview before writing anything:

* :func:`suggest` — pure proposal. It reuses
  :func:`codx.junior.company.profile_advisor.recommend`, which inspects the
  project stack and maps the need onto known roles, and enriches it with the
  **actor block** each new profile would get (so an autonomous team can be
  generated with a weekly availability window in one shot).
* :func:`apply` — writes the profiles that are missing, then the team and its
  charter. It never overwrites an existing profile: a role whose profile already
  exists is reused as-is (its actor block is not touched, that is the user's
  decision).

The team is written with :class:`~codx.junior.teams.team_manager.TeamManager`, so
the folder layout (``.codx/teams/<Name>/<Name>.team`` + ``.md``) and the
``@mention`` behaviour are exactly the same as when a team is created by hand:
the generator is a shortcut for the manual flow, not a second one.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Profile names are folder names and @mentions: keep them boring.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: Safe defaults for a generated autonomous teammate. A generated team must not
#: be an unbounded spender: two cxjcoins per shift and ten per day keeps a
#: six-turn ``until_done`` shift inside a predictable envelope. The caller can
#: override either value.
DEFAULT_ACTOR_BUDGET_CXJCOINS = 2.0
DEFAULT_ACTOR_DAILY_BUDGET_CXJCOINS = 10.0


def _valid_name(name: str) -> bool:
    """Return True when *name* can be used as a profile name (and a folder)."""
    return bool(
        name
        and len(name) <= 64
        and ".." not in name
        and "/" not in name
        and "\\" not in name
        and _NAME_RE.match(name)
    )


def _available_profiles(settings: Any) -> List[str]:
    """Return the profile names visible from the project (best effort)."""
    try:
        from codx.junior.profiles.profile_manager import ProfileManager

        return [
            str(getattr(profile, "name", "") or "")
            for profile in ProfileManager(settings=settings).list_all_profiles() or []
            if getattr(profile, "name", None)
        ]
    except Exception as ex:  # pragma: no cover - catalogue is best effort
        logger.warning("team generator: cannot list profiles: %s", ex)
        return []


def _projects(settings: Any) -> List[Dict[str, Any]]:
    """Return the project in the shape the advisor expects."""
    project_id = str(getattr(settings, "project_id", "") or "")
    project_name = str(getattr(settings, "project_name", "") or "")
    project_path = str(getattr(settings, "project_path", "") or "")
    if not project_id and not project_path:
        return []
    return [{
        "project_id": project_id or project_name,
        "project_name": project_name or project_id,
        "project_path": project_path,
    }]


def _actor_block(
    role: Dict[str, Any],
    *,
    autonomous: bool,
    board: str,
    schedule: Optional[Dict[str, Any]],
    budget_cxjcoins: float = DEFAULT_ACTOR_BUDGET_CXJCOINS,
    daily_budget_cxjcoins: float = DEFAULT_ACTOR_DAILY_BUDGET_CXJCOINS,
) -> Dict[str, Any]:
    """
    Return the ``actor`` block of a generated profile.

    A generated teammate is a **coworker** by default: it owns its job, works
    until it is done or the budget is over, and writes its shift reports on its
    own desk board. The schedule is copied as-is so a generated team can be kept
    inside office hours from the start.
    """
    if not autonomous:
        return {}
    from codx.junior.autonomy import constants as C
    from codx.junior.autonomy.models import ActorConfig, default_board_name

    name = str(role.get("profile_name") or "")
    watched = [board] if board else []
    desk = default_board_name(name)
    return ActorConfig(
        enabled=True,
        role=C.ROLE_COWORKER,
        policy=C.POLICY_FULL,
        session=C.SESSION_UNTIL_DONE,
        board=desk,
        boards=watched,
        interval_minutes=24 * 60,
        budget_cxjcoins=max(0.0, float(budget_cxjcoins or 0.0)),
        daily_budget_cxjcoins=max(0.0, float(daily_budget_cxjcoins or 0.0)),
        instructions=(
            f"You own the \"{role.get('title')}\" responsibility of this team. "
            "Work until the job is done or the daily budget is over, and "
            "schedule every follow-up as a task with `wake_at`."
        ),
        schedule=schedule or {},
    ).normalized().model_dump()


def suggest(
    settings: Any,
    need: str,
    *,
    autonomous: bool = False,
    board: str = "",
    schedule: Optional[Dict[str, Any]] = None,
    budget_cxjcoins: float = DEFAULT_ACTOR_BUDGET_CXJCOINS,
    daily_budget_cxjcoins: float = DEFAULT_ACTOR_DAILY_BUDGET_CXJCOINS,
    company_id: str = "",
) -> Dict[str, Any]:
    """
    Turn a plain-language need into a team proposal.

    Args:
        settings: Project settings.
        need: What the user asked for ("I need an IT team", "take care of the
            maintenance tickets", ...).
        autonomous: When True every generated profile also gets an ``actor``
            block, so the team works on its own.
        board: Board the generated actors are responsible for. Empty leaves each
            actor on its own desk board.
        schedule: Weekly availability applied to the generated actors.

    Returns:
        The advisor suggestion, enriched with ``actor`` (the block that would be
        written) on every role and the derived team members.
    """
    from codx.junior.company.profile_advisor import recommend

    project_name = str(getattr(settings, "project_name", "") or "")
    suggestion = recommend(
        need,
        company_name=project_name,
        available_profiles=_available_profiles(settings),
        available_tools=None,
        projects=_projects(settings),
    )
    roles = suggestion.get("roles") or []
    for index, role in enumerate(roles):
        role["order"] = index
        role["actor"] = _actor_block(
            role,
            autonomous=autonomous,
            board=board,
            schedule=schedule,
            budget_cxjcoins=budget_cxjcoins,
            daily_budget_cxjcoins=daily_budget_cxjcoins,
        )
    suggestion["autonomous"] = bool(autonomous)
    suggestion["board"] = board
    suggestion["schedule"] = schedule or {}
    suggestion["budget_cxjcoins"] = float(budget_cxjcoins or 0.0)
    suggestion["daily_budget_cxjcoins"] = float(daily_budget_cxjcoins or 0.0)
    suggestion["company_id"] = str(company_id or "").strip()
    suggestion["members"] = [
        {
            "profile": role.get("profile_name"),
            "role": "lead" if index == 0 else role.get("role_id"),
            "responsibility": role.get("description") or "",
            "order": index,
        }
        for index, role in enumerate(roles)
        if role.get("profile_name")
    ]
    return suggestion


def _charter(
    name: str,
    suggestion: Dict[str, Any],
    members: List[Dict[str, Any]],
) -> str:
    """
    Build the markdown charter of a generated team.

    Kept close to the charter the ``team_management`` tool generates for a hand
    written team, plus the two things that only exist when the team is
    autonomous: the shift report habit and the availability window.
    """
    lines = [f"# {name} team", ""]
    goal = str(suggestion.get("need") or "").strip()
    if goal:
        lines.extend([f"**Goal:** {goal}", ""])
    lines.extend(["## How we work"])
    lead = members[0]["profile"] if members else ""
    if lead:
        lines.append(
            f"1. **@{lead}** coordinates: splits the request, assigns it to the "
            "members below and consolidates the final answer."
        )
    lines.append("2. Each member works only inside its responsibility.")
    lines.append(
        "3. Conflicts are resolved explicitly; a decision is never silent."
    )
    if suggestion.get("autonomous"):
        lines.append(
            "4. Every member works its own board and leaves a shift report, so "
            "the progress of the team can be read without asking."
        )
        lines.append(
            "5. Anything that must be checked later becomes a task with "
            "`wake_at`. An unscheduled follow-up is a lost follow-up."
        )
    lines.append("")
    if members:
        lines.extend(["## Responsibilities", ""])
        for member in members:
            role = f" ({member['role']})" if member.get("role") else ""
            responsibility = (
                f" — {member['responsibility']}" if member.get("responsibility") else ""
            )
            lines.append(f"- **@{member['profile']}**{role}{responsibility}")
        lines.append("")
    return "\n".join(lines)


def _sync_company_team(
    *,
    company_id: str,
    team_name: str,
    need: str,
    members: List[Dict[str, Any]],
    project_id: str,
    errors: List[str],
) -> Dict[str, Any]:
    """
    Create (or extend) the company-level team for a generated project team.

    The project ``Team`` is what the workspace uses; the company
    ``CompanyTeam`` is what the company shell, missions and approvals use. A
    generated autonomous team must exist in both or it cannot participate in
    the company. Existing company teams are never overwritten: generated
    profile members are upserted and any human roster is preserved.
    """
    if not company_id or not members:
        return {}
    try:
        from codx.junior.company.manager import CompanyManager

        manager = CompanyManager()
        company = manager.get_company(company_id)
        roster = [
            {
                "kind": "profile",
                "name": member["profile"],
                "role": member.get("role") or "member",
                "responsibility": member.get("responsibility") or "",
                "order": member.get("order") or index,
            }
            for index, member in enumerate(members)
            if member.get("profile")
        ]
        existing = next(
            (
                team for team in company.teams
                if str(team.name or "").strip().lower() == team_name.lower()
            ),
            None,
        )
        if existing is not None:
            manager.add_team_members(
                company.id, existing.id, roster, actor="team-generator"
            )
            return {"created": False, "team_id": existing.id, "team_name": existing.name}
        payload = {
            "name": team_name,
            "description": (
                f"Autonomous team generated for: {need or 'an expressed need'}"
            )[:400],
            "purpose": str(need or "")[:400],
            "lead_kind": "profile",
            "lead_name": members[0]["profile"],
            "members": roster,
            "project_ids": (
                [project_id] if project_id and company.project(project_id) else []
            ),
            "autonomy": "supervised",
            "tags": ["generated", "autonomous"],
        }
        manager.add_team(company.id, payload, actor="team-generator")
        refreshed = manager.get_company(company.id)
        created_team = next(
            (
                team for team in refreshed.teams
                if str(team.name or "").strip().lower() == team_name.lower()
            ),
            None,
        )
        return {
            "created": True,
            "team_id": created_team.id if created_team else "",
            "team_name": team_name,
        }
    except Exception as ex:  # pragma: no cover - company shell is optional
        logger.exception("team generator: cannot sync company team: %s", ex)
        errors.append(f"company team: {ex}")
        return {}


def apply(
    settings: Any,
    suggestion: Dict[str, Any],
    *,
    team_name: str = "",
    company_id: str = "",
) -> Dict[str, Any]:
    """
    Write the profiles and the team of *suggestion*.

    Args:
        settings: Project settings.
        suggestion: The payload returned by :func:`suggest`.
        team_name: Optional override of the generated team name.

    Returns:
        ``{"team": <team dict or empty>, "created_profiles": [...],
        "reused_profiles": [...], "errors": [...]}``

    Existing profiles are never modified: a role that already has a profile is
    reused as-is.
    """
    from codx.junior.model.profile import Profile, ProfileApiSettings
    from codx.junior.model.team import Team
    from codx.junior.profiles.profile_manager import ProfileManager
    from codx.junior.teams.team_manager import TeamManager

    profiles_manager = ProfileManager(settings=settings)
    team_manager = TeamManager(settings=settings)
    project_name = str(getattr(settings, "project_name", "") or "")
    project_id = str(getattr(settings, "project_id", "") or "")
    stack = suggestion.get("stack") or {}
    company_id = str(company_id or suggestion.get("company_id") or "").strip()

    # The suggestion may come back from the client between two requests, so the
    # names are validated here again before anything is written to disk.
    available = {
        str(name).lower(): str(name) for name in _available_profiles(settings)
    }

    created: List[str] = []
    reused: List[str] = []
    errors: List[str] = []
    members: List[Dict[str, Any]] = []

    for index, role in enumerate(suggestion.get("roles") or []):
        name = str(role.get("profile_name") or "").strip()
        if not _valid_name(name):
            errors.append(f"invalid profile name {name!r}")
            continue
        existing = available.get(name.lower())
        if existing:
            reused.append(existing)
            members.append({
                "profile": existing,
                "role": "lead" if index == 0 else str(role.get("role_id") or "member"),
                "responsibility": str(role.get("description") or "")[:200],
                "order": len(members),
            })
            continue
        if not role.get("create"):
            errors.append(
                f"{name}: the profile does not exist and was not requested"
            )
            continue
        try:
            from codx.junior.company.profile_advisor import profile_content

            profile = Profile(
                name=name,
                description=str(role.get("description") or "")[:400],
                category=str(role.get("category") or "agent"),
                content=profile_content(
                    role,
                    company_name=project_name,
                    project_name=project_name,
                    stack=stack,
                ),
                profiles=[str(base) for base in role.get("bases") or []],
                tools=[str(tool) for tool in role.get("tools") or []],
                tags=["generated", "team"],
                use_knowledge=True,
                api_settings=ProfileApiSettings(),
                actor=role.get("actor") or None,
                project_id=project_id,
            )
            profiles_manager.save_profile(profile=profile)
            available[name.lower()] = name
            created.append(name)
            members.append({
                "profile": name,
                "role": "lead" if index == 0 else str(role.get("role_id") or "member"),
                "responsibility": str(role.get("description") or "")[:200],
                "order": len(members),
            })
        except Exception as ex:  # pragma: no cover - per-profile isolation
            logger.exception("team generator: cannot create profile '%s': %s", name, ex)
            errors.append(f"{name}: {ex}")

    if not members:
        return {"team": {}, "created_profiles": created,
                "reused_profiles": reused, "errors": errors}

    resolved_name = str(team_name or suggestion.get("team_name") or "Team").strip()
    if not _valid_name(resolved_name):
        errors.append(f"invalid team name {resolved_name!r}")
        return {"team": {}, "created_profiles": created,
                "reused_profiles": reused, "errors": errors}
    if team_manager.read_team(resolved_name) is not None:
        company_team = _sync_company_team(
            company_id=company_id,
            team_name=resolved_name,
            need=str(suggestion.get("need") or ""),
            members=members,
            project_id=project_id,
            errors=errors,
        )
        return {
            "team": {},
            "created_profiles": created,
            "reused_profiles": reused,
            "errors": errors,
            "company_team": company_team,
            "note": f"the team '{resolved_name}' already exists and was not modified",
        }

    try:
        team = Team(
            name=resolved_name,
            description=(
                f"Team generated for: {suggestion.get('need') or 'an expressed need'}"
            )[:400],
            goal=str(suggestion.get("need") or ""),
            lead=members[0]["profile"],
            members=Team.parse_members(members),
            tags=["generated"],
            project_id=project_id,
        )
        team.ensure_lead_in_roster()
        team.content = _charter(resolved_name, suggestion, members)
        saved = team_manager.save_team(team)
    except Exception as ex:  # pragma: no cover - defensive
        logger.exception("team generator: cannot create team: %s", ex)
        errors.append(f"team: {ex}")
        return {"team": {}, "created_profiles": created,
                "reused_profiles": reused, "errors": errors}

    company_team = _sync_company_team(
        company_id=company_id,
        team_name=resolved_name,
        need=str(suggestion.get("need") or ""),
        members=members,
        project_id=project_id,
        errors=errors,
    )
    return {
        "team": team_manager.resolve_team(saved),
        "created_profiles": created,
        "reused_profiles": reused,
        "errors": errors,
        "company_team": company_team,
    }

# Made with ❤️ by codx-junior
