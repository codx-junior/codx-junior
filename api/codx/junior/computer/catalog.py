"""Merged catalog of the image library.

The UI used to read the library from four independent feeds — the provider
catalog (``/computers/images``), the saved computers (``/computers/library``)
and the custom coworking gallery (``/computers/library/custom``) — with three
different item shapes and two different preview mechanisms (public URL versus
authenticated blob).  That is why there was no single grid.

This module flattens the three feeds into ONE list of :class:`CatalogImage` so a
grid can render, search and filter the whole library with one request.  The
custom image keeps its whole manifest nested under ``coworking_image``: the
client reads ``{ coworking_image: { env } }`` (see ``client/src/utils/imageEnv.js``)
and flattening it is what silently empties the create-session form.
"""
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

#: Sources of the merged feed.  Providers come first so the grid opens on the
#: catalog, then the project's saved computers, then the coworking gallery.
CATALOG_SOURCES = ("linuxserver", "kasmweb", "browseruse", "saved", "custom")


class CatalogImage(BaseModel):
    """One card of the unified image grid."""

    source: str = Field(description="linuxserver, kasmweb, browseruse, saved or custom")
    id: str = Field(description="Stable id inside its source")
    name: str = Field(description="Human readable name")
    description: Optional[str] = Field(default=None, description="Short description")
    type: Optional[str] = Field(default=None, description="Grid type: desktop, application, browser, saved, custom")
    tags: List[str] = Field(default_factory=list, description="Search/filter tags")
    visibility: Optional[str] = Field(default=None, description="user, project, custom or global")
    status: str = Field(default="ready", description="ready, draft or error")
    icon_url: Optional[str] = Field(default=None, description="Icon (public URL when possible)")
    preview_url: Optional[str] = Field(default=None, description="Preview URL when public")
    has_preview_blob: bool = Field(
        default=False,
        description="True when the preview must be fetched from the authenticated blob route",
    )
    image: Optional[str] = Field(default=None, description="Docker image reference")
    provider: Optional[str] = Field(default=None, description="Image provider/profile")
    coworking_image: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Full custom-image manifest (source=custom); keeps `env` nested",
    )


class CatalogImageListResponse(BaseModel):
    """Unified image library response."""

    count: int
    images: List[CatalogImage]


def _provider_type(image: Any) -> str:
    """Grid type of a provider catalog entry."""
    category = (getattr(image, "category", "") or "").strip().lower()
    if not category:
        return "desktop"
    if category.startswith("desktop"):
        return "desktop"
    if "browser" in category or "automation" in category:
        return "browser"
    return "application"


def _provider_items(images: Optional[Iterable[Any]]) -> List[CatalogImage]:
    """Map provider catalog entries (``ComputerImage``) to catalog items."""
    items: List[CatalogImage] = []
    for image in images or []:
        items.append(CatalogImage(
            source=(getattr(image, "provider", "") or "curated"),
            id=image.id,
            name=image.name,
            description=(image.description or None),
            type=_provider_type(image),
            tags=list(image.tags or []),
            visibility="global",
            status="ready",
            icon_url=image.icon_url,
            preview_url=getattr(image, "screenshot_url", None),
            has_preview_blob=False,
            image=image.image,
            provider=image.provider,
        ))
    return items


def _saved_items(computers: Optional[Iterable[Any]]) -> List[CatalogImage]:
    """Map saved computers (``SavedComputer``) to catalog items."""
    items: List[CatalogImage] = []
    for item in computers or []:
        items.append(CatalogImage(
            source="saved",
            id=item.id,
            name=item.name,
            description=getattr(item, "description", None) or None,
            type="saved",
            tags=list(getattr(item, "tags", None) or []),
            visibility=getattr(item, "visibility", None),
            status=getattr(item, "status", None) or "ready",
            icon_url=None,
            # The preview is only served by /computers/library/{id}/preview,
            # behind the API auth: the grid must fetch it as a blob.
            preview_url=None,
            has_preview_blob=bool(getattr(item, "has_preview", False)),
            image=getattr(item, "image", None),
            provider=getattr(item, "provider", None),
        ))
    return items


def _custom_items(images: Optional[Iterable[Any]]) -> List[CatalogImage]:
    """Map coworking gallery entries (``CoworkingImage``) to catalog items."""
    items: List[CatalogImage] = []
    for item in images or []:
        payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        items.append(CatalogImage(
            source="custom",
            id=item.id,
            name=item.name,
            description=item.description or None,
            type="custom",
            tags=list(item.tags or []),
            visibility=item.visibility,
            status=item.status or "draft",
            icon_url=item.icon_url,
            # Icon and preview live in the definition folder and are only
            # readable through /computers/library/custom/{id}/media.
            preview_url=None,
            has_preview_blob=bool(item.screenshot_url or item.media),
            image=item.docker_image(),
            provider="custom",
            coworking_image=payload,
        ))
    return items


def build_catalog(
    provider_images: Optional[Iterable[Any]] = None,
    saved_computers: Optional[Iterable[Any]] = None,
    custom_images: Optional[Iterable[Any]] = None,
) -> List[CatalogImage]:
    """Merge the three feeds into one ordered list (pure, no IO)."""
    return [
        *_provider_items(provider_images),
        *_saved_items(saved_computers),
        *_custom_items(custom_images),
    ]


def _matches(item: CatalogImage, needle: str) -> bool:
    haystack = " ".join([
        item.id,
        item.name,
        item.description or "",
        item.image or "",
        item.type or "",
        " ".join(item.tags),
    ]).lower()
    return needle in haystack


def filter_catalog(
    items: Iterable[CatalogImage],
    query: Optional[str] = None,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    visibility: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
) -> List[CatalogImage]:
    """Apply the grid filters server-side, case-insensitively (pure)."""
    needle = (query or "").strip().lower()
    wanted_type = (type or "").strip().lower()
    wanted_tag = (tag or "").strip().lower()
    wanted_visibility = (visibility or "").strip().lower()
    wanted_status = (status or "").strip().lower()
    wanted_source = (source or "").strip().lower()

    result: List[CatalogImage] = []
    for item in items:
        if wanted_source and item.source.lower() != wanted_source:
            continue
        if wanted_type and (item.type or "").lower() != wanted_type:
            continue
        if wanted_tag and wanted_tag not in [entry.lower() for entry in item.tags]:
            continue
        if wanted_visibility and (item.visibility or "").lower() != wanted_visibility:
            continue
        if wanted_status and (item.status or "").lower() != wanted_status:
            continue
        if needle and not _matches(item, needle):
            continue
        result.append(item)
    return result
