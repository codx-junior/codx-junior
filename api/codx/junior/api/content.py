"""Content creator API of the Project's Channel.

Admins describe what they want ("a video showing how the channel works"), pick
one or more output formats, attach the project files and external resources and
then build. ``POST /items/{id}/build`` writes the build instructions, enqueues
the media jobs and returns the payload the client uses to open the builder task
with the ``codx-junior`` profile.

The router is auto discovered (``codx.junior.api.discover_routers`` mounts every
module of this package under ``/api``), so no change to ``app.py`` is needed.
"""

import base64
import binascii
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from codx.junior.api import get_current_session, require_admin
from codx.junior.api.media import project_path, store_for_project
from codx.junior.engine import CODXJuniorSession
from codx.junior.media.content import (
    BUILD_MARKDOWN,
    BRIEF_MARKDOWN,
    CONTENT_FORMATS,
    CREATOR_PROFILE,
    BUILDER_PROFILE,
    MAX_UPLOAD_BYTES,
    ContentBrief,
    ContentBuild,
    ContentSource,
    ContentWorkspace,
    brief_from_payload,
    merge_brief,
    missing_parts,
    normalize_formats,
    plan_build,
)
from codx.junior.media.model import MediaJob
from codx.junior.model.model import CodxUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/content", tags=["content"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def workspace_for(session: CODXJuniorSession) -> ContentWorkspace:
    """Content workspace of the active project (``.codx/media/content``)."""
    return ContentWorkspace(store_for_project(project_path(session)))


def _project_name(session: CODXJuniorSession) -> str:
    settings = getattr(session, "settings", None)
    name = getattr(settings, "project_name", None)
    return name or project_path(session).name


def _bad_request(message: str, **extra: Any) -> HTTPException:
    detail: Dict[str, Any] = {"message": message}
    detail.update(extra)
    return HTTPException(status_code=400, detail=detail)


def _load(workspace: ContentWorkspace, content_id: str) -> ContentBrief:
    brief = workspace.load(content_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="Content not found: %s" % content_id)
    return brief


def _brief_payload(workspace: ContentWorkspace, brief: ContentBrief) -> Dict[str, Any]:
    """Everything the client needs to render one content item."""
    return {
        "brief": brief.model_dump(mode="json"),
        "missing": missing_parts(brief),
        "files": workspace.listing(brief.content_id),
        "folder": workspace.rel_dir(brief.content_id),
        "brief_md": "%s/%s" % (workspace.rel_dir(brief.content_id), BRIEF_MARKDOWN),
        "build_md": "%s/%s" % (workspace.rel_dir(brief.content_id), BUILD_MARKDOWN),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Formats & items
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/formats")
async def list_formats(
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """Output formats the creator offers, plus the profiles involved."""
    return {
        "formats": [spec.model_dump(mode="json") for spec in CONTENT_FORMATS],
        "profiles": {"creator": CREATOR_PROFILE, "builder": BUILDER_PROFILE},
        "limits": {"max_upload_bytes": MAX_UPLOAD_BYTES},
    }


@router.get("/items")
async def list_items(
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """Every content item of the project, most recently updated first."""
    workspace = workspace_for(session)
    items = [
        {
            "content_id": brief.content_id,
            "title": brief.title,
            "intent": brief.intent,
            "formats": brief.formats,
            "state": brief.state,
            "sources": len(brief.sources),
            "updated_at": brief.updated_at,
            "build": brief.build.model_dump(mode="json") if brief.build else None,
        }
        for brief in workspace.list_items()
    ]
    return {"items": items}


@router.post("/items")
async def create_item(
    payload: Dict[str, Any] = Body(...),
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Create a content item: folders, ``brief.json`` and ``brief.md``."""
    workspace = workspace_for(session)
    try:
        brief = brief_from_payload(payload)
    except ValueError as ex:
        raise _bad_request(str(ex))
    except Exception as ex:  # noqa: BLE001 - pydantic validation
        raise _bad_request("Invalid content payload: %s" % ex)
    if not (brief.content_id or "").strip():
        brief.content_id = workspace.unique_slug(brief.title or brief.intent)
    brief.created_by = getattr(user, "username", "") or ""
    try:
        brief = workspace.create(brief)
    except ValueError as ex:
        raise _bad_request(str(ex))
    return _brief_payload(workspace, brief)


@router.get("/items/{content_id}")
async def get_item(
    content_id: str,
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """One content item with its sources, outputs and rendered markdown."""
    workspace = workspace_for(session)
    brief = _load(workspace, content_id)
    return _brief_payload(workspace, brief)


@router.post("/items/{content_id}")
async def update_item(
    content_id: str,
    payload: Dict[str, Any] = Body(...),
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Partial update of a brief (title, intent, formats, audience, tone...)."""
    workspace = workspace_for(session)
    brief = _load(workspace, content_id)
    try:
        brief = merge_brief(brief, payload)
    except ValueError as ex:
        raise _bad_request(str(ex))
    return _brief_payload(workspace, workspace.save(brief))


@router.delete("/items/{content_id}")
async def delete_item(
    content_id: str,
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Delete the folder of a content item (brief, sources and outputs)."""
    workspace = workspace_for(session)
    if not workspace.delete(content_id):
        raise HTTPException(status_code=404, detail="Content not found: %s" % content_id)
    return {"deleted": content_id}


# ─────────────────────────────────────────────────────────────────────────────
# Sources
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/items/{content_id}/sources")
async def add_source(
    content_id: str,
    payload: Dict[str, Any] = Body(...),
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Attach an external resource: a link, a project reference or a note."""
    workspace = workspace_for(session)
    brief = _load(workspace, content_id)
    source = ContentSource(
        kind=(payload.get("kind") or "link").strip().lower(),
        label=(payload.get("label") or payload.get("value") or "").strip(),
        value=(payload.get("value") or "").strip(),
        note=payload.get("note"),
    )
    if not source.value:
        raise _bad_request("A source needs a value (url, file or note)")
    if source.kind == "link" and not source.value.lower().startswith(("http://", "https://")):
        raise _bad_request("A link source must start with http:// or https://")
    workspace.add_source(brief.content_id, source)
    return _brief_payload(workspace, _load(workspace, content_id))


@router.post("/items/{content_id}/uploads")
async def upload_source(
    content_id: str,
    payload: Dict[str, Any] = Body(...),
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Upload one file (base64 json body) into the sources folder of the item.

    The body is json on purpose: it works behind every proxy and needs no extra
    dependency on the server (no ``python-multipart``), and one item is one
    folder, so uploads of different contents never mix.
    """
    workspace = workspace_for(session)
    brief = _load(workspace, content_id)
    raw = payload.get("data_base64") or ""
    if not isinstance(raw, str) or not raw:
        raise _bad_request("Missing data_base64")
    if raw.lstrip().startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as ex:
        raise _bad_request("Invalid base64 payload: %s" % ex)
    try:
        source = workspace.store_upload(
            brief.content_id,
            payload.get("name") or "upload.bin",
            data,
            mime=payload.get("mime") or "",
            note=payload.get("note"),
        )
    except ValueError as ex:
        raise _bad_request(str(ex))
    return {
        "source": source.model_dump(mode="json"),
        **_brief_payload(workspace, _load(workspace, content_id)),
    }


@router.delete("/items/{content_id}/sources/{source_id}")
async def delete_source(
    content_id: str,
    source_id: str,
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Detach a source; an uploaded file is deleted from the sources folder."""
    workspace = workspace_for(session)
    _load(workspace, content_id)
    brief, removed = workspace.remove_source(content_id, source_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Source not found: %s" % source_id)
    return _brief_payload(workspace, brief)


@router.get("/items/{content_id}/file/{name:path}")
async def get_file(
    content_id: str,
    name: str,
    session: CODXJuniorSession = Depends(get_current_session),
) -> FileResponse:
    """Serve a file of the item (a source preview or a rendered output)."""
    workspace = workspace_for(session)
    path: Optional[Path] = workspace.file(content_id, name)
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid path: %s" % name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found: %s" % name)
    return FileResponse(path)


# ─────────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/items/{content_id}/build")
async def build_item(
    content_id: str,
    payload: Dict[str, Any] = Body(default={}),
    session: CODXJuniorSession = Depends(get_current_session),
    user: CodxUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Plan the build, enqueue the media jobs and write ``build.md``.

    The plan is deterministic: the same brief always yields the same steps and
    job specs. Nothing is rendered here - the media worker owns that - and the
    returned ``task`` is what the client uses to open the builder task with the
    ``codx-junior`` profile.
    """
    workspace = workspace_for(session)
    brief = _load(workspace, content_id)
    if payload:
        try:
            brief = merge_brief(brief, payload)
        except ValueError as ex:
            raise _bad_request(str(ex))
        brief = workspace.save(brief)

    formats = normalize_formats(brief.formats)
    if not formats:
        raise _bad_request(
            "Select at least one output format before building",
            missing=missing_parts(brief),
        )

    plan = plan_build(brief)
    workspace.write_build(brief, plan)

    store = store_for_project(project_path(session))
    job_ids: List[str] = []
    for spec in plan.job_specs:
        job = MediaJob(
            project=_project_name(session),
            kind=spec["kind"],
            payload=dict(spec["payload"]),
        )
        store.put_job(job)
        job_ids.append(job.job_id)

    build = ContentBuild(
        build_id="build-%s" % uuid.uuid4().hex[:10],
        formats=formats,
        job_ids=job_ids,
        task_name=plan.task_name,
        state="queued",
    )
    brief = workspace.record_build(brief, build)

    folder = workspace.rel_dir(brief.content_id)
    files = ["%s/%s" % (folder, BRIEF_MARKDOWN), "%s/%s" % (folder, BUILD_MARKDOWN)]
    files.extend(
        "%s/%s" % (folder, source.value)
        for source in brief.sources
        if source.value.startswith("sources/")
    )
    return {
        **_brief_payload(workspace, brief),
        "plan": plan.model_dump(mode="json"),
        "job_ids": job_ids,
        "task": {
            "name": plan.task_name,
            "prompt": plan.task_prompt,
            "files": files,
            "profiles": [BUILDER_PROFILE],
            "mode": "task",
            "metadata": {
                "content_id": brief.content_id,
                "formats": formats,
                "job_ids": job_ids,
                "creator": CREATOR_PROFILE,
            },
        },
    }
