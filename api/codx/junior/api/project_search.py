import logging
from fastapi import APIRouter, Request, HTTPException
from typing import List, Dict, Any

from codx.junior.engine import CODXJuniorSession

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum number of items served by the degraded (lexical) path.
DEGRADED_RESULT_LIMIT = 50


def _to_search_item(entry: Any) -> Dict[str, Any]:
    """Normalize one lexical file hit to the search item contract."""
    if isinstance(entry, dict):
        item = dict(entry)
        item.setdefault("id", item.get("path") or item.get("name"))
        item.setdefault("name", item.get("path") or item.get("id"))
        item.setdefault("resource_type", "file")
        return item
    path = str(entry)
    return {
        "id": path,
        "name": path.rsplit("/", 1)[-1],
        "resource_type": "file",
    }


def _lexical_fallback(codx_junior_session: CODXJuniorSession, query: str) -> List[Dict[str, Any]]:
    """
    Bounded lexical scan used when the embeddings provider is unavailable.

    Reuses ``CODXJuniorSession.search_files`` on purpose: it is the existing
    lexical (path based) search of the file engine, so no embeddings are
    involved and no second search stack is introduced.
    """
    try:
        matches = codx_junior_session.search_files(search=query) or []
    except Exception as exc:  # the fallback itself must never raise
        logger.warning("project_search lexical fallback failed: %s", exc)
        return []
    return [_to_search_item(entry) for entry in list(matches)[:DEGRADED_RESULT_LIMIT]]


@router.get("/project/search")
async def search_projects(request: Request, query: str):
    """
    Searches across project files, chat messages, and wiki pages.

    Args:
        query: The search string.

    Returns:
        A list of search results, each with an id, name, and resource_type.
        When the embeddings provider is unavailable the endpoint degrades
        instead of failing: it answers 200 with
        ``{"degraded": true, "reason": ..., "items": [...]}`` where ``items``
        is the bounded lexical scan, capped at ``DEGRADED_RESULT_LIMIT``.
    """
    if not (query or "").strip():
        raise HTTPException(status_code=422, detail="query must not be blank.")

    codx_junior_session: CODXJuniorSession = request.state.codx_junior_session

    if not codx_junior_session:
        raise HTTPException(status_code=400, detail="CODXJuniorSession not found in request state.")

    try:
        return codx_junior_session.project_search(query=query)
    except Exception as exc:
        reason = str(exc) or exc.__class__.__name__
        logger.warning(
            "project_search degraded to lexical scan (%s): %s",
            exc.__class__.__name__,
            reason,
        )
        return {
            "degraded": True,
            "reason": reason[:500],
            "items": _lexical_fallback(codx_junior_session, query),
        }
