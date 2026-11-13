"""
Teams API router – CRUD endpoints backed by :class:`codx.junior.model.team.Team`.

The router is discovered automatically by
:func:`codx.junior.api.discover_routers` (every module of this package exporting
a ``router`` is registered under ``/api``), so no change is needed in
``codx.junior.app``.

The payload is the **same model** the ``team_management`` tool uses: a lead
plus a list of members, each member carrying its profile name, role,
responsibility and order. The API therefore never reshapes a team, it only
persists it through :class:`~codx.junior.teams.team_manager.TeamManager`.

Route layout
------------
GET    /api/teams               – list the teams of the project (with profile names)
GET    /api/teams/{team_name}   – definition + charter + resolved composition
POST   /api/teams               – create a team (409 when it already exists)
PUT    /api/teams/{team_name}   – replace an existing team, roster included
DELETE /api/teams/{team_name}   – delete the team folder

A rename is ``POST`` + ``DELETE``: the folder name must always match the team
name, so it is never patched in place.

Diagram
-------
```mermaid
flowchart LR
    Client -->|GET /api/teams| List
    Client -->|GET /api/teams/:name| Detail
    Client -->|POST /api/teams| Create
    Client -->|PUT /api/teams/:name| Update
    Client -->|DELETE /api/teams/:name| Delete

    List --> TeamManager
    Detail --> TeamManager
    Create --> TeamManager
    Update --> TeamManager
    Delete --> TeamManager
    TeamManager --> Model[Team / TeamMember model]
    TeamManager --> Disk[.codx/teams/NAME/NAME.team + NAME.md]
```

Made with ❤️ by codx-junior
"""

import logging

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from codx.junior.api import get_current_session
from codx.junior.model.team import Team
from codx.junior.teams.team_manager import TeamManager

logger = logging.getLogger(__name__)

router = APIRouter()


class TeamSuggestionRequest(BaseModel):
    """A plain-language need to turn into a team proposal."""

    need: str = Field(
        description="What the user asked for: 'I need an IT team', "
        "'take care of the maintenance tickets', ..."
    )
    autonomous: bool = Field(
        default=False,
        description="Give every generated profile an actor block so the team works on its own.",
    )
    board: str = Field(
        default="", description="Board the generated actors are responsible for."
    )
    schedule: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Weekly availability applied to the generated actors: "
            "{enabled, timezone, windows: [{days, start, end}]}."
        ),
    )


class TeamGenerationRequest(BaseModel):
    """The proposal to materialize (the payload ``/generate/suggest`` returns)."""

    suggestion: Dict[str, Any] = Field(
        description="The proposal returned by POST /api/teams/generate/suggest."
    )
    team_name: Optional[str] = Field(
        default=None, description="Override of the generated team name."
    )


def get_team_manager(request: Request) -> TeamManager:
    """
    Build the TeamManager of the project in context.

    Args:
        request: The incoming HTTP request.

    Returns:
        A TeamManager rooted at the project's ``.codx/teams`` folder.

    Raises:
        HTTPException: 400 when the request carries no project session.
    """
    session = get_current_session(request)
    if session is None:
        raise HTTPException(
            status_code=400,
            detail="No project session for this request (codx_path is required)",
        )
    return TeamManager(settings=session.settings)


@router.get("/teams")
def list_teams(request: Request) -> List[Dict[str, Any]]:
    """
    List the teams of the project.

    Returns:
        One entry per team, with ``members`` and the derived ``profile_names``.

    Diagram:
    sequenceDiagram
        participant Client
        participant Router as TeamsRouter
        participant Manager as TeamManager
        participant Disk as .codx/teams
        Client->>Router: GET /api/teams
        Router->>Manager: list_teams()
        Manager->>Disk: read */**.team
        Disk-->>Manager: definitions
        Manager-->>Router: Team objects
        Router-->>Client: [Team.definition()]
    """
    manager = get_team_manager(request)
    teams = manager.list_teams()
    logger.info("list_teams: %d team(s)", len(teams))
    return [team.definition() for team in teams]


@router.post("/teams/generate/suggest")
def suggest_team(payload: TeamSuggestionRequest, request: Request) -> Dict[str, Any]:
    """
    Turn a plain-language need into a team **proposal** (nothing is written).

    The proposal is the advisor suggestion enriched with the ``actor`` block
    every generated profile would get, so the UI can show who would be created
    and under which availability window before anything touches the project.

    Args:
        payload: The need, plus the autonomy options.
        request: The incoming HTTP request.

    Returns:
        ``{mode, team_name, roles, members, summary, autonomous, board, ...}``

    Raises:
        HTTPException: 400 when the need is empty.
    """
    need = str(payload.need or "").strip()
    if not need:
        raise HTTPException(status_code=400, detail="need is required")
    manager = get_team_manager(request)
    from codx.junior.teams import generator

    suggestion = generator.suggest(
        manager.settings,
        need,
        autonomous=bool(payload.autonomous),
        board=str(payload.board or "").strip(),
        schedule=payload.schedule,
    )
    logger.info("suggest_team: %d role(s) for %r", len(suggestion.get("roles") or []), need)
    return suggestion


@router.post("/teams/generate/apply", status_code=201)
def generate_team(payload: TeamGenerationRequest, request: Request) -> Dict[str, Any]:
    """
    Write the profiles and the team of a proposal.

    Existing profiles are never modified: a role that already has a profile is
    reused as-is. The team is written with its charter, exactly like a team
    created by hand.

    Args:
        payload: The proposal and an optional team name override.
        request: The incoming HTTP request.

    Returns:
        ``{team, created_profiles, reused_profiles, errors}``

    Raises:
        HTTPException: 400 when nothing can be created.
    """
    manager = get_team_manager(request)
    from codx.junior.teams import generator

    result = generator.apply(
        manager.settings, payload.suggestion, team_name=payload.team_name or ""
    )
    if result.get("errors") and not result.get("team") and not result.get("created_profiles"):
        raise HTTPException(status_code=400, detail="; ".join(result["errors"]))
    logger.info(
        "generate_team: created=%s reused=%s",
        result.get("created_profiles"),
        result.get("reused_profiles"),
    )
    return result


@router.get("/teams/{team_name}")
def get_team(team_name: str, request: Request) -> Dict[str, Any]:
    """
    Return one team: definition, charter and resolved composition.

    Args:
        team_name: Exact team name.

    Returns:
        The team definition (charter included) plus the ``resolved`` block with
        the profiles, tools, model and any missing profile reference.

    Raises:
        HTTPException: 404 when the team does not exist.
    """
    manager = get_team_manager(request)
    team = manager.read_team(team_name)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_name}' not found")

    return {
        **team.definition(include_content=True),
        "resolved": manager.resolve_team(team, include_content=False),
    }


@router.post("/teams", status_code=201)
def create_team(team: Team, request: Request) -> Dict[str, Any]:
    """
    Create a team (both files) in the project.

    Args:
        team: Team payload; ``members`` also accepts plain profile names.
        request: The incoming HTTP request.

    Returns:
        The persisted team definition (charter included).

    Raises:
        HTTPException: 409 when the team already exists (use ``PUT`` instead).
    """
    manager = get_team_manager(request)

    if manager.read_team(team.name):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Team '{team.name}' already exists. "
                "Use PUT /api/teams/{team_name} to update it."
            ),
        )

    team.project_id = manager.settings.project_id
    team.ensure_lead_in_roster()
    saved = manager.save_team(team)
    logger.info("create_team: '%s'", saved.name)
    return saved.definition(include_content=True)


@router.put("/teams/{team_name}")
def update_team(team_name: str, team: Team, request: Request) -> Dict[str, Any]:
    """
    Replace an existing team, roster included.

    The payload is the full team (the same shape ``GET`` returns), so a client
    can read, edit and write it back without a partial-update dialect.

    Args:
        team_name: Exact name of the team to update.
        team: New team payload.
        request: The incoming HTTP request.

    Returns:
        The persisted team definition (charter included).

    Raises:
        HTTPException: 400 when the body name differs from the path, 404 when
            the team does not exist.
    """
    manager = get_team_manager(request)

    existing = manager.read_team(team_name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Team '{team_name}' not found")

    if team.name and team.name != team_name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Body team name '{team.name}' does not match the path "
                f"'{team_name}'. Renaming is POST + DELETE."
            ),
        )

    team.name = team_name
    team.project_id = existing.project_id or manager.settings.project_id
    team.created_at = existing.created_at
    team.ensure_lead_in_roster()

    saved = manager.save_team(team)
    logger.info("update_team: '%s'", saved.name)
    return saved.definition(include_content=True)


@router.delete("/teams/{team_name}")
def delete_team(team_name: str, request: Request) -> Dict[str, Any]:
    """
    Delete a team (its folder and both files).

    The profiles the team referenced are untouched: a team never owned them.

    Args:
        team_name: Exact name of the team to delete.

    Returns:
        ``{"deleted": name, "members": [...]}``.

    Raises:
        HTTPException: 404 when the team does not exist.
    """
    manager = get_team_manager(request)

    existing = manager.read_team(team_name)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Team '{team_name}' not found")

    members = existing.profile_names
    manager.delete_team(team_name)
    logger.info("delete_team: '%s'", team_name)
    return {"deleted": team_name, "members": members}

# Made with ❤️ by codx-junior