"""
Coworking image definitions: the team's own desktop images.

The gallery keeps one folder per image under ``.codx/coworking/images``::

    .codx/coworking/images/<image_id>.<slug>/
        image.json      manifest — the source of truth of this module
        Dockerfile      build recipe (source=dockerfile or dockerhub)
        build.sh        clone + build (source=git)
        README.md       generated summary of the definition
        support/        extra files copied into the image
        media/          icon/screenshot shown in the gallery

Three sources are supported and they all end up as a folder, so the gallery and
the editor treat them the same way:

``dockerfile``
    A base desktop image plus the packages/support files the team needs
    (e.g. Firefox + Jenkins tooling on top of ``lscr.io/linuxserver/webtop``).
``dockerhub``
    An existing Docker Hub image imported by reference; the folder keeps a
    ``FROM <ref>`` recipe so it can still be extended and rebuilt locally.
``git``
    A repository that already contains a Dockerfile; ``build.sh`` clones it and
    builds the image, and the manifest keeps the path/ref of the recipe.

Visibility decides who sees an image in the gallery:

``user``     only its creator;
``project``  every user of the owning project (the default);
``global``   every project of the instance;
``custom``   only the projects listed in ``projects``.

The heavy lifting of writing a real Dockerfile (packages, entrypoints, testing
the build) is delegated to the ``codx-junior-max`` assistant: :func:`build_prompt`
renders the instruction the UI sends to that chat, pointing at the folder.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

COWORKING_FOLDER = "coworking"
IMAGES_FOLDER = "images"
MANIFEST_FILE = "image.json"
DOCKERFILE_FILE = "Dockerfile"
BUILD_SCRIPT = "build.sh"
README_FILE = "README.md"
SUPPORT_FOLDER = "support"
MEDIA_FOLDER = "media"

#: ``user`` < ``project`` < ``custom`` < ``global`` in reach.
VISIBILITIES = ("user", "project", "custom", "global")
#: Where the definition of the image comes from.
SOURCES = ("dockerfile", "dockerhub", "git")
#: Profile that writes the real recipe; the UI hands the folder over to it.
ASSISTANT_PROFILE = "codx-junior-max"
#: Desktop the scaffold starts from, and the user that owns it.
DEFAULT_BASE_IMAGE = "lscr.io/linuxserver/webtop:ubuntu-kde"
DEFAULT_DESKTOP_USER = "abc"
#: Manifest may describe private environments: keep it 0600.
MANIFEST_MODE = 0o600
#: Live-view port of the linuxserver webtop / kasmweb desktops.
DEFAULT_LIVE_PORT = 3000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str, fallback: str = "image") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:40] or fallback


def _safe_relative(relative_path: str) -> str:
    """Normalise a folder-relative path and refuse anything that escapes it."""
    cleaned = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError("A file path inside the image folder is required")
    if any(part == ".." for part in parts):
        raise ValueError(f"Invalid file path: {relative_path}")
    return "/".join(parts)


class CoworkingImage(BaseModel):
    """One custom desktop image of the coworking gallery."""

    id: str = Field(description="Short stable id; also the folder prefix")
    slug: str = Field(description="Filesystem/Docker safe name")
    name: str = Field(description="Human readable image name")
    description: str = Field(default="", description="What the image is for")
    source: str = Field(default="dockerfile", description="dockerfile, dockerhub or git")
    base_image: Optional[str] = Field(default=None, description="Base desktop image")
    docker_ref: Optional[str] = Field(default=None, description="Docker Hub reference (source=dockerhub)")
    git_url: Optional[str] = Field(default=None, description="Repository URL (source=git)")
    git_ref: Optional[str] = Field(default=None, description="Branch or tag to build (source=git)")
    dockerfile: str = Field(default=DOCKERFILE_FILE, description="Recipe file inside the folder")
    build_script: Optional[str] = Field(default=None, description="Build helper inside the folder")
    support_files: List[str] = Field(default_factory=list, description="Extra files of the folder")
    media: List[str] = Field(default_factory=list, description="Icon/screenshot paths inside the folder")
    icon_url: Optional[str] = Field(default=None, description="Icon shown in the gallery")
    screenshot_url: Optional[str] = Field(default=None, description="Preview shown in the gallery")
    tags: List[str] = Field(default_factory=list, description="Search/filter tags")
    visibility: str = Field(default="project", description="user, project, custom or global")
    projects: List[str] = Field(default_factory=list, description="Projects allowed to see it (visibility=custom)")
    created_by: Optional[str] = Field(default=None, description="Username that created it")
    project_id: Optional[str] = Field(default=None, description="Owning project id")
    project_name: Optional[str] = Field(default=None, description="Owning project name")
    created_at: str = Field(description="ISO creation timestamp")
    updated_at: str = Field(description="ISO last-update timestamp")
    status: str = Field(default="draft", description="draft, ready or error")
    error: Optional[str] = Field(default=None, description="Last build/validation error")
    live_port: Optional[int] = Field(default=DEFAULT_LIVE_PORT, description="Live-view port of the image")
    desktop_user: str = Field(default=DEFAULT_DESKTOP_USER, description="User that runs the desktop")
    folder: Optional[str] = Field(default=None, description="Folder name inside the gallery")
    assistant_profile: str = Field(default=ASSISTANT_PROFILE, description="Profile that writes the recipe")
    assistant_prompt: str = Field(default="", description="Instruction to hand the folder to the assistant")

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=False)

    def docker_image(self, version: str = "latest") -> str:
        """Local Docker reference built from this definition."""
        return f"codx-coworking/{self.slug}:{version}"


class CoworkingImageListResponse(BaseModel):
    count: int
    images: List[CoworkingImage]


class CreateCoworkingImageRequest(BaseModel):
    name: str = Field(description="Name of the image")
    description: Optional[str] = Field(default=None, description="What the image is for")
    source: str = Field(default="dockerfile", description="dockerfile, dockerhub or git")
    base_image: Optional[str] = Field(default=None, description="Base desktop image (source=dockerfile)")
    docker_ref: Optional[str] = Field(default=None, description="Docker Hub reference (source=dockerhub)")
    git_url: Optional[str] = Field(default=None, description="Repository URL (source=git)")
    git_ref: Optional[str] = Field(default=None, description="Branch or tag (source=git)")
    tags: List[str] = Field(default_factory=list, description="Search/filter tags")
    media: List[str] = Field(default_factory=list, description="Icon/screenshot paths")
    icon_url: Optional[str] = Field(default=None, description="Icon URL")
    screenshot_url: Optional[str] = Field(default=None, description="Preview URL")
    visibility: str = Field(default="project", description="user, project, custom or global")
    projects: List[str] = Field(default_factory=list, description="Projects allowed to see it (visibility=custom)")
    live_port: Optional[int] = Field(default=None, description="Live-view port")
    scaffold: bool = Field(default=True, description="Write the Dockerfile/README scaffold")


class UpdateCoworkingImageRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    base_image: Optional[str] = None
    docker_ref: Optional[str] = None
    git_url: Optional[str] = None
    git_ref: Optional[str] = None
    tags: Optional[List[str]] = None
    media: Optional[List[str]] = None
    icon_url: Optional[str] = None
    screenshot_url: Optional[str] = None
    visibility: Optional[str] = None
    projects: Optional[List[str]] = None
    status: Optional[str] = None
    error: Optional[str] = None
    live_port: Optional[int] = None
    desktop_user: Optional[str] = None


class ImageFileInfo(BaseModel):
    """One file stored in the image folder."""

    path: str
    size: int
    is_manifest: bool = False


class ImageFileContent(BaseModel):
    """One text file of the image folder."""

    path: str
    content: str


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def _dockerfile_template(
    name: str,
    description: str,
    base_image: str,
    desktop_user: str,
    live_port: int,
    slug: str,
) -> str:
    return f"""# syntax=docker/dockerfile:1
#
# {name} — coworking desktop image
#
# {description or "Custom desktop image for codx-junior coworking."}
#
# The folder .codx/coworking/images holds the definition; the codx-junior-max
# assistant fills the packages and support files below and validates the build.
ARG BASE_IMAGE={base_image}
FROM ${{BASE_IMAGE}}

# ── System packages ───────────────────────────────────────────────────────
# Install what the image must provide, e.g. a browser and the CI/CD tooling:
#   firefox-esr  chromium  openjdk-17-jre  nodejs  npm  git  curl
USER root
RUN apt-get update \\
 && apt-get install -y --no-install-recommends \\
      firefox-esr \\
      git \\
      curl \\
 && rm -rf /var/lib/apt/lists/*

# ── Support files ─────────────────────────────────────────────────────────
# Files of this folder are copied from support/ (desktop entries, configs...).
# COPY support/ /opt/coworking/
# COPY support/desktop/ /config/Desktop/

# ── Metadata ──────────────────────────────────────────────────────────────
ARG IMAGE_VERSION=1.0.0
LABEL org.opencontainers.image.title="{name}" \\
      org.opencontainers.image.version="${{IMAGE_VERSION}}" \\
      org.opencontainers.image.description="{description or name}" \\
      org.opencontainers.image.source="codx-junior:coworking/{slug}"

USER {desktop_user}
EXPOSE {live_port}
"""


def _dockerhub_dockerfile(reference: str, name: str, slug: str) -> str:
    return f"""# syntax=docker/dockerfile:1
#
# {name} — imported from Docker Hub
#
# The upstream image is used as-is; keep the FROM and add the extra layers
# this team needs (they will be part of every coworking session).
ARG BASE_IMAGE={reference}
FROM ${{BASE_IMAGE}}

# Add the team layers here, for example:
# USER root
# RUN apt-get update && apt-get install -y --no-install-recommends <packages> \\
#  && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.source="codx-junior:coworking/{slug}"
"""


def _git_build_script(git_url: str, git_ref: str, image: str) -> str:
    ref = git_ref or "main"
    return f"""#!/usr/bin/env bash
# Build the coworking image from its repository.
#
# The repository is the source of truth of the recipe; this script clones the
# requested ref and runs the docker build the repository ships.
set -euo pipefail

REPO="{git_url}"
IMAGE="{image}"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

git clone --depth 1 --branch "{ref}" "$REPO" "$workdir/repo"
docker build -t "$IMAGE" "$workdir/repo"
echo "Built $IMAGE from $REPO@{ref}"
"""


def _readme_template(item: CoworkingImage) -> str:
    lines = [
        f"# {item.name}",
        "",
        item.description or "Custom desktop image of the coworking gallery.",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Id | `{item.id}` |",
        f"| Source | `{item.source}` |",
        f"| Visibility | `{item.visibility}`"
        + (f" ({', '.join(item.projects)})" if item.projects else ""),
        "| Base image | `{}` |".format(item.base_image or "-"),
        "| Docker Hub | `{}` |".format(item.docker_ref or "-"),
        "| Repository | `{}` |".format(item.git_url or "-"),
        "| Tags | {} |".format(", ".join(f"`{tag}`" for tag in item.tags) or "-"),
        f"| Owner | `{item.created_by or '-'}` |",
        f"| Status | `{item.status}` |",
        "",
        "## Files",
        "",
        f"* `{MANIFEST_FILE}` — this definition, read by codx-junior.",
        f"* `{item.dockerfile}` — build recipe of the image.",
    ]
    if item.build_script:
        lines.append(f"* `{item.build_script}` — clone + build helper of the repository.")
    lines += [
        f"* `{SUPPORT_FOLDER}/` — files copied into the image.",
        f"* `{MEDIA_FOLDER}/` — icon and preview shown in the gallery.",
        "",
        "## Build",
        "",
        "```bash",
        f"docker build -t {item.docker_image()} .",
        "```",
        "",
        "Hand the folder to the `codx-junior-max` assistant to complete the recipe:",
        "",
        "```text",
        item.assistant_prompt,
        "```",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def visible_to(
    item: CoworkingImage,
    username: Optional[str] = None,
    project_id: Optional[str] = None,
    is_admin: bool = False,
) -> bool:
    """Whether a user of ``project_id`` may see this image in the gallery."""
    if is_admin:
        return True
    visibility = (item.visibility or "project").lower()
    if visibility == "global":
        return True
    if visibility == "user":
        return bool(username) and item.created_by == username
    if visibility == "custom":
        return bool(project_id) and project_id in (item.projects or [])
    return bool(project_id) and item.project_id == project_id


def can_edit(
    item: CoworkingImage,
    username: Optional[str] = None,
    project_id: Optional[str] = None,
    is_admin: bool = False,
) -> bool:
    """Owner, project admin role and global entries: see the API router."""
    if is_admin:
        return True
    if item.created_by and item.created_by == username:
        return True
    if (item.visibility or "project").lower() == "user":
        return False
    return bool(project_id) and item.project_id == project_id


def build_prompt(item: CoworkingImage, folder_path: str) -> str:
    """Instruction that hands the folder to the ``codx-junior-max`` assistant."""
    origin = {
        "dockerfile": f"base image `{item.base_image or DEFAULT_BASE_IMAGE}`",
        "dockerhub": f"Docker Hub reference `{item.docker_ref}`",
        "git": f"repository `{item.git_url}` (ref `{item.git_ref or 'main'}`)",
    }.get(item.source, f"source `{item.source}`")
    return (
        f"As the `{ASSISTANT_PROFILE}` assistant, create the coworking desktop image "
        f"**{item.name}** in the folder "
        f"`{os.path.join(folder_path, MANIFEST_FILE)}` (definition) and "
        f"`{os.path.join(folder_path, item.dockerfile)}` (recipe).\n\n"
        f"* Source: {origin}.\n"
        f"* Purpose: {item.description or 'custom desktop image for the team'}.\n"
        f"* Tags: {', '.join(item.tags) or 'none'}.\n"
        f"* Live view: port {item.live_port or DEFAULT_LIVE_PORT}, desktop user "
        f"`{item.desktop_user}`.\n\n"
        "Steps:\n"
        "1. Complete the Dockerfile (packages, support files, entrypoint) keeping "
        "the live-view service of the base desktop working.\n"
        "2. Add the support files under `support/` and the gallery icon/preview "
        f"under `{MEDIA_FOLDER}/`; list them in `{MANIFEST_FILE}` "
        "(media, support_files, icon_url, screenshot_url).\n"
        "3. Build the image and smoke-test that the desktop starts.\n"
        f"4. Set `status` to `ready` (or `error` with the message) in `{MANIFEST_FILE}`.\n"
    )


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------
class CoworkingImageLibrary:
    """Filesystem-backed gallery of custom coworking images."""

    def __init__(self, codx_path: str, project_id: Optional[str] = None, project_name: Optional[str] = None):
        self.codx_path = codx_path
        self.project_id = project_id
        self.project_name = project_name
        self.root = os.path.join(codx_path, COWORKING_FOLDER, IMAGES_FOLDER)
        os.makedirs(self.root, exist_ok=True)

    # ── paths ──────────────────────────────────────────────────────────────
    def folder_name(self, item: CoworkingImage) -> str:
        """``<image_id>.<slug>``: a stable, human-readable folder name."""
        return f"{item.id}.{item.slug}"

    def folder_path(self, image_id: str) -> Optional[str]:
        if not image_id:
            return None
        for folder in Path(self.root).iterdir():
            if not folder.is_dir() or folder.name == ".trash":
                continue
            if folder.name == image_id or folder.name.split(".")[0] == image_id:
                if os.path.isfile(str(folder / MANIFEST_FILE)):
                    return str(folder)
        return None

    def file_path(self, image_id: str, relative_path: str) -> str:
        folder = self.folder_path(image_id)
        if not folder:
            raise FileNotFoundError(image_id)
        return os.path.join(folder, _safe_relative(relative_path))

    # ── CRUD ───────────────────────────────────────────────────────────────
    def list(self) -> List[CoworkingImage]:
        items: List[CoworkingImage] = []
        if not os.path.isdir(self.root):
            return items
        for folder in Path(self.root).iterdir():
            if not folder.is_dir() or folder.name == ".trash":
                continue
            item = self._read(str(folder / MANIFEST_FILE))
            if item:
                items.append(item)
        items.sort(key=lambda entry: entry.updated_at or entry.created_at or "", reverse=True)
        return items

    def list_visible(
        self,
        username: Optional[str] = None,
        project_id: Optional[str] = None,
        is_admin: bool = False,
        q: Optional[str] = None,
        visibility: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[CoworkingImage]:
        needle = (q or "").strip().lower()
        wanted_tag = (tag or "").strip().lower()
        wanted_visibility = (visibility or "").strip().lower()
        result = []
        for item in self.list():
            if not visible_to(item, username=username, project_id=project_id, is_admin=is_admin):
                continue
            if wanted_visibility and item.visibility != wanted_visibility:
                continue
            if wanted_tag and wanted_tag not in [entry.lower() for entry in item.tags]:
                continue
            if needle:
                haystack = " ".join(
                    [item.name, item.description, item.slug, " ".join(item.tags), item.docker_ref or "", item.git_url or ""]
                ).lower()
                if needle not in haystack:
                    continue
            result.append(item)
        return result

    def get(self, image_id: str) -> Optional[CoworkingImage]:
        if not image_id:
            return None
        folder = self.folder_path(image_id)
        return self._read(os.path.join(folder, MANIFEST_FILE)) if folder else None

    def save(self, item: CoworkingImage) -> CoworkingImage:
        folder = os.path.join(self.root, self.folder_name(item))
        os.makedirs(folder, exist_ok=True)
        for name in (SUPPORT_FOLDER, MEDIA_FOLDER):
            os.makedirs(os.path.join(folder, name), exist_ok=True)
        item.folder = os.path.basename(folder)
        item.updated_at = _now()
        stored = item.model_copy(update={"assistant_prompt": ""})
        tmp = os.path.join(folder, MANIFEST_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(stored.to_dict(), handle, indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(folder, MANIFEST_FILE))
        try:
            os.chmod(os.path.join(folder, MANIFEST_FILE), MANIFEST_MODE)
        except OSError:
            pass
        item.assistant_prompt = build_prompt(item, folder)
        return item

    def delete(self, image_id: str) -> bool:
        folder = self.folder_path(image_id)
        if not folder:
            return False
        shutil.rmtree(folder, ignore_errors=True)
        return True

    # ── creation / import ──────────────────────────────────────────────────
    def create(
        self,
        req: CreateCoworkingImageRequest,
        created_by: Optional[str] = None,
    ) -> CoworkingImage:
        name = (req.name or "").strip()
        if not name:
            raise ValueError("The image needs a name")
        source = (req.source or "dockerfile").strip().lower()
        if source not in SOURCES:
            raise ValueError(f"Unknown source '{req.source}'. Use one of: {', '.join(SOURCES)}")
        visibility = (req.visibility or "project").strip().lower()
        if visibility not in VISIBILITIES:
            raise ValueError(f"Unknown visibility '{req.visibility}'. Use one of: {', '.join(VISIBILITIES)}")
        projects = [entry.strip() for entry in (req.projects or []) if entry and entry.strip()]
        if visibility == "custom" and not projects:
            raise ValueError("visibility=custom needs at least one project")
        if source == "dockerhub" and not (req.docker_ref or "").strip():
            raise ValueError("A Docker Hub import needs the image reference")
        if source == "git" and not (req.git_url or "").strip():
            raise ValueError("A repository import needs the git URL")

        slug = _slug(name)
        item = CoworkingImage(
            id=uuid.uuid4().hex[:8],
            slug=slug,
            name=name,
            description=(req.description or "").strip(),
            source=source,
            base_image=(req.base_image or "").strip() or (
                (req.docker_ref or "").strip() if source == "dockerhub" else DEFAULT_BASE_IMAGE
            ),
            docker_ref=(req.docker_ref or "").strip() or None,
            git_url=(req.git_url or "").strip() or None,
            git_ref=(req.git_ref or "").strip() or None,
            media=[entry for entry in (req.media or []) if entry],
            icon_url=req.icon_url or None,
            screenshot_url=req.screenshot_url or None,
            tags=[entry for entry in (req.tags or []) if entry],
            visibility=visibility,
            projects=projects,
            created_by=created_by,
            project_id=self.project_id,
            project_name=self.project_name,
            created_at=_now(),
            updated_at=_now(),
            status="draft",
            live_port=req.live_port or DEFAULT_LIVE_PORT,
        )
        self.save(item)
        if req.scaffold:
            self._scaffold(item)
        return self.get(item.id) or item

    def _scaffold(self, item: CoworkingImage) -> None:
        """Write the starter files; safe to call twice (never overwrites)."""
        folder = self.folder_path(item.id)
        if not folder:
            return
        dockerfile = os.path.join(folder, getattr(item, "dockerfile", DOCKERFILE_FILE))
        if not os.path.exists(dockerfile):
            if item.source == "dockerhub":
                content = _dockerhub_dockerfile(item.docker_ref or item.base_image or "", item.name, item.slug)
            else:
                content = _dockerfile_template(
                    item.name,
                    item.description,
                    item.base_image or DEFAULT_BASE_IMAGE,
                    item.desktop_user,
                    item.live_port or DEFAULT_LIVE_PORT,
                    item.slug,
                )
            _write_file(dockerfile, content)
        if item.source == "git" and item.git_url:
            script = os.path.join(folder, BUILD_SCRIPT)
            if not os.path.exists(script):
                _write_file(
                    script,
                    _git_build_script(item.git_url, item.git_ref or "main", item.docker_image()),
                    executable=True,
                )
            item.build_script = BUILD_SCRIPT
        item.support_files = sorted(
            {
                entry.path
                for entry in self.files(item.id)
                if not entry.is_manifest and entry.path != README_FILE
            }
        )
        self.save(item)
        _write_file(os.path.join(folder, README_FILE), _readme_template(item))

    def import_from_dockerhub(self, reference: str, created_by: Optional[str] = None, **kwargs: Any) -> CoworkingImage:
        """Import an existing Docker Hub image as a coworking image."""
        reference = (reference or "").strip()
        if not reference:
            raise ValueError("A Docker Hub reference is required")
        name = kwargs.pop("name", None) or reference.split(":")[0].split("/")[-1]
        req = CreateCoworkingImageRequest(
            name=name,
            source="dockerhub",
            docker_ref=reference,
            description=kwargs.pop("description", None) or f"Imported from Docker Hub: {reference}",
            tags=kwargs.pop("tags", []) or ["imported", "dockerhub"],
            **kwargs,
        )
        return self.create(req, created_by=created_by)

    def import_from_git(self, git_url: str, created_by: Optional[str] = None, **kwargs: Any) -> CoworkingImage:
        """Import a repository that already ships a Dockerfile."""
        git_url = (git_url or "").strip()
        if not git_url:
            raise ValueError("A repository URL is required")
        name = kwargs.pop("name", None) or git_url.rstrip("/").split("/")[-1].removesuffix(".git")
        req = CreateCoworkingImageRequest(
            name=name,
            source="git",
            git_url=git_url,
            git_ref=kwargs.pop("git_ref", None) or "main",
            description=kwargs.pop("description", None) or f"Imported from repository: {git_url}",
            tags=kwargs.pop("tags", []) or ["imported", "git"],
            **kwargs,
        )
        return self.create(req, created_by=created_by)

    def update(self, image_id: str, req: UpdateCoworkingImageRequest) -> Optional[CoworkingImage]:
        item = self.get(image_id)
        if not item:
            return None
        changes = req.model_dump(exclude_none=True)
        if "visibility" in changes:
            visibility = str(changes["visibility"]).strip().lower()
            if visibility not in VISIBILITIES:
                raise ValueError(f"Unknown visibility '{visibility}'. Use one of: {', '.join(VISIBILITIES)}")
            changes["visibility"] = visibility
        if "source" in changes:
            source = str(changes["source"]).strip().lower()
            if source not in SOURCES:
                raise ValueError(f"Unknown source '{source}'. Use one of: {', '.join(SOURCES)}")
            changes["source"] = source
        if "name" in changes and changes["name"]:
            item.slug = _slug(str(changes["name"]), item.slug)
        updated = item.model_copy(update=changes)
        if updated.visibility == "custom" and not updated.projects:
            raise ValueError("visibility=custom needs at least one project")
        return self.save(updated)

    # ── files ──────────────────────────────────────────────────────────────
    def files(self, image_id: str) -> List[ImageFileInfo]:
        folder = self.folder_path(image_id)
        if not folder:
            return []
        result: List[ImageFileInfo] = []
        for path in sorted(Path(folder).rglob("*")):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            relative = str(path.relative_to(folder)).replace(os.sep, "/")
            result.append(
                ImageFileInfo(
                    path=relative,
                    size=path.stat().st_size,
                    is_manifest=relative == MANIFEST_FILE,
                )
            )
        return result

    def read_file(self, image_id: str, relative_path: str) -> ImageFileContent:
        path = self.file_path(image_id, relative_path)
        if not os.path.isfile(path):
            raise FileNotFoundError(relative_path)
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return ImageFileContent(path=_safe_relative(relative_path), content=handle.read())

    def write_file(self, image_id: str, relative_path: str, content: str) -> ImageFileContent:
        path = self.file_path(image_id, relative_path)
        _write_file(path, content, executable=path.endswith(".sh"))
        item = self.get(image_id)
        if item:
            item.support_files = sorted(
                {entry.path for entry in self.files(image_id) if not entry.is_manifest and entry.path != README_FILE}
            )
            self.save(item)
        return ImageFileContent(path=_safe_relative(relative_path), content=content)

    def prompt_for(self, image_id: str) -> Optional[str]:
        item = self.get(image_id)
        folder = self.folder_path(image_id)
        if not item or not folder:
            return None
        return build_prompt(item, folder)

    # ── internals ──────────────────────────────────────────────────────────
    @staticmethod
    def _read(path: str) -> Optional[CoworkingImage]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                item = CoworkingImage(**json.load(handle))
        except FileNotFoundError:
            return None
        except Exception as ex:  # pragma: no cover - corrupt manifest
            logger.warning("Could not read coworking image '%s': %s", path, ex)
            return None
        item.folder = os.path.basename(os.path.dirname(path))
        item.assistant_prompt = build_prompt(item, os.path.dirname(path))
        return item


def _write_file(path: str, content: str, executable: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    if executable:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


def coworking_image_library(settings: Any) -> CoworkingImageLibrary:
    """Return the gallery of the project behind ``settings``."""
    return CoworkingImageLibrary(
        codx_path=settings.codx_path,
        project_id=getattr(settings, "project_id", None),
        project_name=getattr(settings, "project_name", None),
    )
