"""
Collection change events — keep every connected client in sync.

A **collection** is any server-side set of items a client caches in its store:
the project list, the profiles of a project, the teams, the kanban boards…
When one of those items is created, updated or deleted, answering the caller of
the REST endpoint is not enough: every OTHER client (another browser tab,
another user, the same user in the desktop shell) keeps rendering its cached
copy and only sees the change after a manual reload.

This module is the single place that announces those changes. One Socket.IO
event — :data:`COLLECTION_EVENT` — carries the collection, the action and the
affected item::

    {
      "collection": "profiles",
      "action": "upserted",
      "item": { "name": "reviewer" },
      "codx_path": "/projects/acme/.codx",
      "project_id": "3f1c…",
      "project_name": "acme",
      "source": "profile_manager",
      "ts": 1712345678
    }

Clients subscribe **once** and reload only the collection named in the payload
(see ``client/src/store/collections.js``). The REST endpoints stay the source of
truth: the event never carries the new state, it only says "this changed,
reload it".

Design notes
------------
* **Broadcast, never targeted.** :class:`~codx.junior.sio.session_channel.SessionChannel`
  appends to ``EVENT_QUEUE``, which the background thread emits with
  ``sio.emit(event, data)`` — no ``sid`` — so the change reaches every client.
  A collection is global state, so a per-session channel would leave the other
  clients stale.
* **Best effort, never raises.** Emitting happens *after* the write succeeded.
  A lost notification degrades to the previous behaviour (a stale list until
  the next reload) and must never turn a successful save into an error. Every
  failure is logged and swallowed.
* **Called from the managers, not the routers.** ``ProfileManager``,
  ``TeamManager``, ``ChatManager`` and :func:`create_project` are the choke
  points shared by the REST API, the AI tools and the background workers, so a
  profile created by ``profile_management`` reaches the clients exactly like
  one created through ``POST /api/profiles``.

Diagram
-------
```mermaid
flowchart LR
    Save[ProfileManager / TeamManager / ChatManager / create_project] --> Emit[emit_collection_event]
    Emit --> Queue[SessionChannel EVENT_QUEUE]
    Queue --> Sio[socket.io broadcast]
    Sio --> Clients[Clients: codx-junior-collection]
    Clients --> Store[store/collections.js reloads that collection]
```

Made with ❤️ by codx-junior
"""

import logging

from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Socket.IO event every client subscribes to. Keep in sync with
# `client/src/store/collections.js`.
COLLECTION_EVENT = "codx-junior-collection"

# Collections kept in sync. A collection is identified by its name only: the
# project it belongs to (when it is project-scoped) travels in the payload.
COLLECTION_PROJECTS = "projects"
COLLECTION_PROFILES = "profiles"
COLLECTION_TEAMS = "teams"
COLLECTION_BOARDS = "boards"

COLLECTIONS = frozenset({
    COLLECTION_PROJECTS,
    COLLECTION_PROFILES,
    COLLECTION_TEAMS,
    COLLECTION_BOARDS,
})

# Action of a single-item change.
ACTION_CREATED = "created"
ACTION_UPDATED = "updated"
ACTION_DELETED = "deleted"
# Action of the "create or update" write path (a save that may do either).
ACTION_UPSERTED = "upserted"
# Coarse action: the collection was rewritten (several items at once, e.g. the
# whole kanban document). Clients reload it and work out the differences.
ACTION_CHANGED = "changed"

ACTIONS = frozenset({
    ACTION_CREATED,
    ACTION_UPDATED,
    ACTION_DELETED,
    ACTION_UPSERTED,
    ACTION_CHANGED,
})


def collection_payload(
    collection: str,
    action: str,
    item: Optional[Dict[str, Any]] = None,
    settings: Any = None,
    codx_path: Optional[str] = None,
    project_id: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """
    Build the payload of a collection event.

    The project scope is resolved from ``settings`` when it is available (the
    managers all hold the project settings) so a caller can emit with a single
    argument. Everything else travels verbatim in ``extra``.

    Args:
        collection: Collection name (see :data:`COLLECTIONS`).
        action: What happened (see :data:`ACTIONS`).
        item: Identifying fields of the affected item, e.g. ``{"name": "x"}``.
            Only identification — clients reload the item from the REST API.
        settings: Optional :class:`~codx.junior.settings.CODXJuniorSettings` used
            to fill ``codx_path``, ``project_id`` and ``project_name``.
        codx_path: Overrides the ``codx_path`` taken from ``settings``.
        project_id: Overrides the ``project_id`` taken from ``settings``.
        **extra: Additional payload fields (``source``, ``board``...).

    Returns:
        The payload dict, ready to be sent as the event data.

    Example:
        >>> collection_payload(
        ...     COLLECTION_PROFILES, ACTION_DELETED, {"name": "reviewer"},
        ...     settings=settings, source="profile_manager",
        ... )
        {'collection': 'profiles', 'action': 'deleted', 'item': {'name': 'reviewer'}, ...}
    """
    if collection not in COLLECTIONS:
        logger.debug("Collection event for an unregistered collection '%s'", collection)
    if action not in ACTIONS:
        logger.debug("Collection event with an unknown action '%s'", action)

    payload: Dict[str, Any] = {
        "collection": collection,
        "action": action,
        "item": dict(item or {}),
    }

    if settings is not None:
        codx_path = codx_path or getattr(settings, "codx_path", None)
        project_id = project_id or getattr(settings, "project_id", None)
        project_name = getattr(settings, "project_name", None)
        if project_name:
            payload["project_name"] = project_name

    if codx_path:
        payload["codx_path"] = codx_path
    if project_id:
        payload["project_id"] = project_id

    payload.update(extra)
    return payload


def emit_collection_event(
    collection: str,
    action: str,
    item: Optional[Dict[str, Any]] = None,
    settings: Any = None,
    codx_path: Optional[str] = None,
    project_id: Optional[str] = None,
    **extra: Any,
) -> bool:
    """
    Tell every connected client that a collection changed.

    Call it **after** the write succeeded, from the manager that owns the
    storage, so every write path (REST, AI tools, background workers) is
    covered by a single call site.

    Args:
        collection: Collection name (see :data:`COLLECTIONS`).
        action: What happened (see :data:`ACTIONS`).
        item: Identifying fields of the affected item.
        settings: Optional project settings used to scope the event.
        codx_path: Overrides the ``codx_path`` taken from ``settings``.
        project_id: Overrides the ``project_id`` taken from ``settings``.
        **extra: Additional payload fields.

    Returns:
        ``True`` when the event was queued, ``False`` when it could not be sent
        (the caller never needs to look at it: emitting is best effort).

    Example:
        >>> emit_collection_event(
        ...     COLLECTION_PROFILES, ACTION_UPSERTED,
        ...     {"name": profile.name}, settings=self.settings,
        ...     source="profile_manager",
        ... )
    """
    try:
        payload = collection_payload(
            collection=collection,
            action=action,
            item=item,
            settings=settings,
            codx_path=codx_path,
            project_id=project_id,
            **extra,
        )
        # Imported here: the socket layer starts its emitting thread on import
        # and pulls the engine in, so keeping it lazy avoids turning this
        # module into an import-cycle source for the managers that use it.
        from codx.junior.sio.session_channel import SessionChannel
        from codx.junior.sio.sio import sio

        # No sid: EVENT_QUEUE is broadcast to every connected client.
        SessionChannel(sio=sio).send_event(COLLECTION_EVENT, payload)
        logger.info(
            "Collection event: %s.%s %s",
            collection,
            action,
            item or {},
        )
        return True
    except Exception as ex:  # never break a completed write
        logger.warning(
            "Could not emit collection event %s.%s: %s", collection, action, ex
        )
        return False

# Made with ❤️ by codx-junior
