"""
Efforts API router – the effort definitions profiles are linked to.

The router is discovered automatically by
:func:`codx.junior.api.discover_routers` (every module of this package exporting
a ``router`` is registered under ``/api``), so no change is needed in
``codx.junior.app``.

Two screens use it:

* **Global Settings → Efforts** (advanced) reads ``/api/efforts/fields`` for the
  explanation of every value and CRUDs the definitions.
* **Team view** (simple) only lists ``/api/efforts`` to link a profile to a name.

Route layout
------------
GET    /api/efforts                 – list the efforts (built-ins seeded)
GET    /api/efforts/fields          – documentation of every value (groups)
GET    /api/efforts/{effort_name}   – one definition, with its profiles
POST   /api/efforts                 – create an effort (409 when it exists)
PUT    /api/efforts/{effort_name}   – replace an existing effort
DELETE /api/efforts/{effort_name}   – delete an effort (409 when in use)

A rename is ``POST`` + ``DELETE``: the folder name must always match the effort
name, so it is never patched in place.

Made with ❤️ by codx-junior
"""

import logging

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from codx.junior.efforts.effort_manager import EffortManager
from codx.junior.model.effort import EFFORT_TIERS, EffortDefinition, EffortValues

logger = logging.getLogger(__name__)

router = APIRouter()


class EffortPayload(BaseModel):
    """The payload used to create or replace an effort."""

    name: str = Field(default="", description="Unique effort name (folder name).")
    description: str = Field(
        default="",
        description="What the effort is for: this is what the team view shows.",
    )
    tier: str = Field(
        default="agile",
        description=f"Base tier the values start from: {', '.join(EFFORT_TIERS)}.",
    )
    values: EffortValues = Field(
        default_factory=EffortValues,
        description="Overrides applied on top of the base tier.",
    )


def get_effort_manager(request: Request) -> EffortManager:
    """
    Build the EffortManager of the project in context.

    Args:
        request: The incoming HTTP request.

    Returns:
        An EffortManager rooted at the project's ``.codx/efforts``.

    Raises:
        HTTPException: 500 when the request carries no project.
    """
    session = getattr(request.state, "codx_junior_session", None)
    settings = getattr(session, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="No project in context")
    return EffortManager(settings)


@router.get("/efforts")
def list_efforts(request: Request) -> List[Dict[str, Any]]:
    """
    List every effort of the project.

    The built-in efforts (``agile``, ``max``, ``default``) are seeded on first
    access, so the list is never empty.

    Returns:
        The definitions, built-ins first, each with the profiles linked to it.
    """
    manager = get_effort_manager(request)
    return [effort.model_dump() for effort in manager.list_efforts()]


@router.get("/efforts/fields")
def effort_fields() -> Dict[str, Any]:
    """
    Return the documentation of every value the advanced editor can set.

    Every field carries a ``summary`` and an ``effect`` explaining what it
    changes in the profile's work, plus its ``group`` and ``control`` type.

    Returns:
        ``{"groups": [...], "fields": [...], "tiers": [...]}``.
    """
    return EffortManager.field_docs()


@router.get("/efforts/{effort_name}")
def get_effort(effort_name: str, request: Request) -> Dict[str, Any]:
    """
    Read one effort definition.

    Args:
        effort_name: Exact name of the effort.

    Returns:
        The definition, with the profiles linked to it.

    Raises:
        HTTPException: 404 when the effort does not exist.
    """
    manager = get_effort_manager(request)
    definition = manager.read_effort(effort_name)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Effort '{effort_name}' not found")
    return definition.model_dump()


@router.post("/efforts")
def create_effort(payload: EffortPayload, request: Request) -> Dict[str, Any]:
    """
    Create an effort.

    Args:
        payload: The effort to create.

    Returns:
        The stored definition.

    Raises:
        HTTPException: 409 when it already exists, 422 when it is invalid.
    """
    manager = get_effort_manager(request)
    try:
        name = manager.validate_name(payload.name)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if manager.read_effort(name, with_profiles=False):
        raise HTTPException(status_code=409, detail=f"Effort '{name}' already exists")

    definition = EffortDefinition(
        name=name,
        description=payload.description,
        tier=payload.tier,
        values=payload.values,
    )
    try:
        saved = manager.save_effort(definition)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    logger.info("create_effort: '%s'", saved.name)
    return saved.model_dump()


@router.put("/efforts/{effort_name}")
def update_effort(
    effort_name: str,
    payload: EffortPayload,
    request: Request,
) -> Dict[str, Any]:
    """
    Replace an existing effort (its values are pushed to the linked profiles).

    Args:
        effort_name: Name of the effort to update.
        payload: The new definition.

    Returns:
        The stored definition.

    Raises:
        HTTPException: 404 when it does not exist, 422 when it is invalid.
    """
    manager = get_effort_manager(request)
    if not manager.read_effort(effort_name, with_profiles=False):
        raise HTTPException(status_code=404, detail=f"Effort '{effort_name}' not found")

    definition = EffortDefinition(
        name=effort_name,
        description=payload.description,
        tier=payload.tier,
        values=payload.values,
    )
    try:
        saved = manager.save_effort(definition)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    logger.info("update_effort: '%s'", saved.name)
    return saved.model_dump()


@router.delete("/efforts/{effort_name}")
def delete_effort(effort_name: str, request: Request) -> Dict[str, Any]:
    """
    Delete an effort.

    A built-in effort or one still linked to profiles is never deleted: the
    answer says who is using it instead of silently breaking those profiles.

    Args:
        effort_name: Exact name of the effort to delete.

    Returns:
        ``{"deleted": name}``.

    Raises:
        HTTPException: 404 when missing, 409 when it is built-in or in use.
    """
    manager = get_effort_manager(request)
    if not manager.read_effort(effort_name, with_profiles=False):
        raise HTTPException(status_code=404, detail=f"Effort '{effort_name}' not found")
    try:
        manager.delete_effort(effort_name)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    logger.info("delete_effort: '%s'", effort_name)
    return {"deleted": effort_name}


# Made with ❤️ by codx-junior
