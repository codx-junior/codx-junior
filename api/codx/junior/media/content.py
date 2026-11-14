"""Content creator workspace: briefs, sources and build plans.

The Project's Channel is not only a library: an admin asks for new content
("create a video showing how the channel works"), picks one or more output
formats, attaches project files and external resources (links, images, videos)
and then hands the work to the builder profile.

This module owns that workflow and it is pure python (stdlib + pydantic), so it
is unit tested offline, without a server and without network:

* ``ContentBrief`` - what the admin wants, editable until it is built.
* ``ContentSource`` - one attached resource (link, note, image, video, project).
* ``BuildPlan`` - deterministic decomposition of a brief into steps and media
  jobs (no LLM: the same brief always produces the same plan).
* ``ContentWorkspace`` - the folders of a single content item.

Filesystem layout under the media store of the project::

    .codx/media/content/<slug>/
        brief.json   editable source of truth
        brief.md     rendered brief, what an assistant reads
        build.md     build instructions handed to the builder profile
        sources/     uploads (images, videos, documents) of THIS content
        outputs/     rendered artifacts (mp4, m4a, md, pdf ...)

Rendered artifacts are never stored inline: ``build.md`` plus the brief is the
input of the builder, and the media worker owns ``outputs/``.
"""

import json
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .model import JobKind, utc_now
from .store import MediaStore

#: Folder holding every content item, next to jobs/episodes/assets
CONTENT_DIRNAME = "content"
#: Files of a single content item
BRIEF_JSON = "brief.json"
BRIEF_MARKDOWN = "brief.md"
BUILD_MARKDOWN = "build.md"
SOURCES_DIRNAME = "sources"
OUTPUTS_DIRNAME = "outputs"

#: Profile the user talks to while shaping the brief
CREATOR_PROFILE = "content_creator"
#: Profile that performs the build
BUILDER_PROFILE = "codx-junior"

#: Refuse bigger uploads: they travel base64 encoded in a json body
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
#: Longest accepted file name (before the extension)
MAX_NAME_LENGTH = 80

ContentSourceKind = str
ContentState = str


class ContentFormatSpec(BaseModel):
    """One output format the creator can produce."""

    id: str = Field(description="Format id used in the brief, e.g. 'video'")
    label: str = Field(description="Short label shown on the format chip")
    description: str = Field(default="", description="One line explaining the output")
    icon: str = Field(default="fa-solid fa-file", description="Font awesome class")
    output: str = Field(default="md", description="Default output extension")
    aspect: Optional[str] = Field(
        default=None, description="Aspect ratio for visual formats (16:9 / 9:16)"
    )
    job_kind: Optional[JobKind] = Field(
        default=None,
        description=(
            "Media job kind the worker renders; None means the builder profile "
            "produces the artifact as text"
        ),
    )


CONTENT_FORMATS: Tuple[ContentFormatSpec, ...] = (
    ContentFormatSpec(
        id="video",
        label="Video",
        description="A narrated 16:9 episode with chapters, built from the project and the sources.",
        icon="fa-solid fa-film",
        output="mp4",
        aspect="16:9",
        job_kind="episode",
    ),
    ContentFormatSpec(
        id="short",
        label="Short",
        description="A vertical 9:16 clip under a minute, for news and mobile features.",
        icon="fa-solid fa-mobile-screen",
        output="mp4",
        aspect="9:16",
        job_kind="short",
    ),
    ContentFormatSpec(
        id="podcast",
        label="Podcast",
        description="Audio only, one voice per profile, with the chapter timeline kept.",
        icon="fa-solid fa-microphone-lines",
        output="m4a",
        aspect=None,
        job_kind=None,
    ),
    ContentFormatSpec(
        id="wiki",
        label="Wiki page",
        description="A documentation page that lands in the project wiki (Library tab).",
        icon="fa-solid fa-book-open",
        output="md",
        aspect=None,
        job_kind=None,
    ),
    ContentFormatSpec(
        id="slides",
        label="Slides",
        description="A deck of 16:9 slides with speaker notes per slide.",
        icon="fa-solid fa-display",
        output="pdf",
        aspect="16:9",
        job_kind=None,
    ),
    ContentFormatSpec(
        id="doc",
        label="Guide",
        description="A written tutorial with steps, links and screenshots.",
        icon="fa-solid fa-file-lines",
        output="md",
        aspect=None,
        job_kind=None,
    ),
)

_FORMATS_BY_ID: Dict[str, ContentFormatSpec] = {spec.id: spec for spec in CONTENT_FORMATS}


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class ContentSource(BaseModel):
    """One resource attached to a content item."""

    model_config = ConfigDict(extra="allow")

    source_id: str = Field(default="", description="Stable id inside the brief")
    kind: str = Field(
        default="link",
        description="link | image | video | audio | document | project | task | note",
    )
    label: str = Field(default="", description="Human label shown in the UI")
    value: str = Field(
        default="",
        description="Url, project relative path (sources/...) or the note text itself",
    )
    note: Optional[str] = Field(default=None, description="Why this source matters")
    mime: Optional[str] = Field(default=None)
    bytes: int = Field(default=0)
    added_at: Any = Field(default_factory=utc_now)


class BuildStep(BaseModel):
    """One step of the build, executed by the builder profile."""

    step_id: str = ""
    title: str = ""
    kind: str = Field(default="builder", description="media-job | builder")
    format: str = ""
    profile: str = BUILDER_PROFILE
    job_kind: Optional[JobKind] = None
    output: str = Field(default="", description="Output file name inside outputs/")
    objective: str = ""


class ContentBuild(BaseModel):
    """Record of the last build requested for a content item."""

    model_config = ConfigDict(extra="allow")

    build_id: str = ""
    requested_at: Any = Field(default_factory=utc_now)
    formats: List[str] = Field(default_factory=list)
    job_ids: List[str] = Field(default_factory=list)
    task_name: str = ""
    state: str = Field(default="queued")
    error: Optional[str] = None


class ContentBrief(BaseModel):
    """Everything the builder needs to know about a requested content item."""

    model_config = ConfigDict(extra="allow")

    content_id: str = Field(default="", description="Folder name under content/")
    title: str = ""
    intent: str = Field(default="", description="What the admin wants, in their words")
    formats: List[str] = Field(default_factory=list, description="Selected format ids")
    audience: str = ""
    tone: str = ""
    language: str = ""
    duration_seconds: int = Field(default=0, description="Target length, 0 = let the builder decide")
    notes: str = ""
    sources: List[ContentSource] = Field(default_factory=list)
    state: str = Field(default="draft", description="draft | queued | building | done | failed")
    created_by: str = ""
    created_at: Any = Field(default_factory=utc_now)
    updated_at: Any = Field(default_factory=utc_now)
    build: Optional[ContentBuild] = None


class BuildPlan(BaseModel):
    """Deterministic plan derived from a brief."""

    content_id: str = ""
    task_name: str = ""
    task_prompt: str = ""
    steps: List[BuildStep] = Field(default_factory=list)
    job_specs: List[Dict[str, Any]] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


def slugify(value: str, fallback: str = "content") -> str:
    """Ascii, dash separated, filesystem safe identifier."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return (text[:60].strip("-") or fallback)


def format_spec(format_id: str) -> Optional[ContentFormatSpec]:
    """Spec of a format id, ``None`` when the format is unknown."""
    return _FORMATS_BY_ID.get(str(format_id or "").strip().lower())


def normalize_formats(values: Any) -> List[str]:
    """Validate and de-duplicate the selected formats, keeping their order.

    Accepts a single string (comma separated) or a list; raises ``ValueError``
    listing the valid ids so the API can answer 400 with a useful message.
    """
    if values is None:
        items: List[str] = []
    elif isinstance(values, str):
        items = [item for item in values.split(",")]
    else:
        items = [str(item) for item in values]

    selected: List[str] = []
    invalid: List[str] = []
    for item in items:
        candidate = item.strip().lower()
        if not candidate:
            continue
        if format_spec(candidate) is None:
            invalid.append(candidate)
            continue
        if candidate not in selected:
            selected.append(candidate)
    if invalid:
        raise ValueError(
            "Unknown format(s): %s. Valid formats: %s"
            % (", ".join(invalid), ", ".join(sorted(_FORMATS_BY_ID)))
        )
    return selected


def safe_formats(values: Any) -> List[str]:
    """``normalize_formats`` that never raises: a corrupted brief must not 500."""
    try:
        return normalize_formats(values)
    except ValueError:
        return []


def missing_parts(brief: ContentBrief) -> List[str]:
    """What still blocks a useful build, in the admin's words."""
    missing: List[str] = []
    if not (brief.intent or "").strip():
        missing.append("Describe what you want to create (the intent).")
    if not safe_formats(brief.formats):
        missing.append("Pick at least one output format.")
    if not (brief.title or "").strip():
        missing.append("Add a title: it becomes the task name and the output file names.")
    return missing


def infer_kind(mime: str = "", name: str = "") -> str:
    """Content source kind guessed from the mime type, then from the extension."""
    value = (mime or "").lower()
    suffix = Path(str(name or "")).suffix.lower()
    if value.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"}:
        return "image"
    if value.startswith("video/") or suffix in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        return "video"
    if value.startswith("audio/") or suffix in {".mp3", ".wav", ".m4a", ".ogg"}:
        return "audio"
    return "document"


def _human_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024.0
    return "%d B" % (value or 0)


def _human_duration(seconds: int) -> str:
    total = max(0, int(seconds or 0))
    if not total:
        return "let the builder decide"
    minutes, secs = divmod(total, 60)
    return "%d:%02d" % (minutes, secs)


def render_brief_markdown(brief: ContentBrief) -> str:
    """Render the brief as the markdown document an assistant reads."""
    formats = safe_formats(brief.formats)
    lines: List[str] = []
    lines.append("# %s" % ((brief.title or brief.content_id or "Untitled content").strip()))
    lines.append("")
    lines.append("**Intent** - %s" % ((brief.intent or "").strip() or "_(not described yet)_"))
    lines.append("")
    lines.append("**Outputs** - %s" % (", ".join(formats) if formats else "_(none selected)_"))
    if brief.audience:
        lines.append("")
        lines.append("**Audience** - %s" % brief.audience.strip())
    if brief.tone:
        lines.append("")
        lines.append("**Tone** - %s" % brief.tone.strip())
    if brief.language:
        lines.append("")
        lines.append("**Language** - %s" % brief.language.strip())
    lines.append("")
    lines.append("**Target length** - %s" % _human_duration(brief.duration_seconds))
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    if brief.sources:
        for source in brief.sources:
            extra: List[str] = [source.kind]
            if source.mime:
                extra.append(source.mime)
            if source.bytes:
                extra.append(_human_bytes(source.bytes))
            detail = " - %s" % ", ".join(part for part in extra if part)
            label = (source.label or source.value or "").strip()
            lines.append("- `%s`%s" % (label, detail))
            if source.value and source.value != label:
                lines.append("  - value: %s" % source.value)
            if source.note:
                lines.append("  - note: %s" % source.note)
    else:
        lines.append("_No sources attached: use the project itself._")
    if (brief.notes or "").strip():
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        lines.append(brief.notes.strip())
    lines.append("")
    return "\n".join(lines)


def _render_task_prompt(brief: ContentBrief, plan: BuildPlan, formats: List[str]) -> str:
    """Self-contained instructions for the builder profile."""
    folder = "%s/%s" % (CONTENT_DIRNAME, brief.content_id)
    lines: List[str] = []
    lines.append(
        'Create the content requested in `.codx/media/%s/%s` (formats: %s).'
        % (CONTENT_DIRNAME, brief.content_id, ", ".join(formats))
    )
    lines.append("")
    lines.append("Brief: %s/%s" % (folder, BRIEF_MARKDOWN))
    lines.append("Build instructions: %s/%s" % (folder, BUILD_MARKDOWN))
    if brief.sources:
        lines.append("")
        lines.append("Sources (already downloaded or uploaded, do not lose them):")
        for source in brief.sources:
            lines.append("- [%s] %s" % (source.kind, source.value or source.label))
    lines.append("")
    lines.append("Order of work:")
    for index, step in enumerate(plan.steps, start=1):
        lines.append("%d. %s" % (index, step.objective))
    lines.append("")
    lines.append("Rules:")
    lines.append(
        "- Read `%s/%s` first and honour the audience, tone, language and length." % (folder, BRIEF_MARKDOWN)
    )
    lines.append(
        "- Every claim must be traceable: cite the project file or the source it comes from."
    )
    lines.append(
        "- Run the redaction gate (`codx.junior.media.redaction.scan`) on the narration before publishing."
    )
    lines.append(
        "- Write the artifacts to `%s/%s/`; for video/short produce an Episode IR and enqueue a media job instead of rendering here."
        % (folder, OUTPUTS_DIRNAME)
    )
    lines.append("- When the outputs exist, list them in your final message with their paths.")
    lines.append("")
    return "\n".join(lines)


def plan_build(brief: ContentBrief) -> BuildPlan:
    """Decompose a brief into build steps and media job specs (no side effects)."""
    formats = normalize_formats(brief.formats)
    steps: List[BuildStep] = []
    job_specs: List[Dict[str, Any]] = []

    for index, format_id in enumerate(formats, start=1):
        spec = format_spec(format_id)
        if spec is None:  # pragma: no cover - normalize_formats already rejects it
            continue
        output = "%s-%s.%s" % (brief.content_id or "content", format_id, spec.output)
        if spec.job_kind:
            job_specs.append(
                {
                    "kind": spec.job_kind,
                    "payload": {
                        "content_id": brief.content_id,
                        "format": format_id,
                        "aspect": spec.aspect,
                        "title": brief.title,
                        "intent": brief.intent,
                        "brief": "%s/%s/%s" % (CONTENT_DIRNAME, brief.content_id, BRIEF_MARKDOWN),
                        "output": "%s/%s/%s/%s"
                        % (CONTENT_DIRNAME, brief.content_id, OUTPUTS_DIRNAME, output),
                        "sources": [source.value for source in brief.sources],
                    },
                }
            )
            objective = (
                "Produce the %s storyboard (Episode IR) from the brief and enqueue the "
                "%s media job; the worker renders it into outputs/%s."
                % (format_id, spec.job_kind, output)
            )
            kind = "media-job"
        else:
            objective = "Write the %s artifact from the brief and the sources into outputs/%s." % (
                format_id,
                output,
            )
            kind = "builder"
        steps.append(
            BuildStep(
                step_id="s%d-%s" % (index, format_id),
                title="%s: %s" % (spec.label, (brief.title or brief.content_id or "").strip()),
                kind=kind,
                format=format_id,
                profile=BUILDER_PROFILE,
                job_kind=spec.job_kind,
                output=output,
                objective=objective,
            )
        )

    task_name = "Create content: %s" % (
        (brief.title or brief.intent or brief.content_id or "untitled").strip()[:70]
    )
    plan = BuildPlan(
        content_id=brief.content_id,
        task_name=task_name,
        steps=steps,
        job_specs=job_specs,
        missing=missing_parts(brief),
    )
    plan.task_prompt = _render_task_prompt(brief, plan, formats)
    return plan


def render_build_markdown(brief: ContentBrief, plan: BuildPlan) -> str:
    """The instructions document handed to the builder profile."""
    lines: List[str] = []
    lines.append("# Build: %s" % (brief.title or brief.content_id or "content"))
    lines.append("")
    lines.append("Generated from the content brief of `content/%s`." % brief.content_id)
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    for index, step in enumerate(plan.steps, start=1):
        lines.append("%d. **%s** (`%s`, %s)" % (index, step.title, step.kind, step.profile))
        lines.append("   - %s" % step.objective)
        if step.output:
            lines.append("   - output: `%s/%s/%s/%s`" % (CONTENT_DIRNAME, brief.content_id, OUTPUTS_DIRNAME, step.output))
    lines.append("")
    if plan.job_specs:
        lines.append("## Media jobs")
        lines.append("")
        for spec in plan.job_specs:
            lines.append("- `%s` -> %s" % (spec["kind"], spec["payload"].get("output")))
        lines.append("")
    if plan.missing:
        lines.append("## Still missing")
        lines.append("")
        for item in plan.missing:
            lines.append("- %s" % item)
        lines.append("")
    lines.append("## Prompt")
    lines.append("")
    lines.append("```text")
    lines.append(plan.task_prompt)
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Workspace
# ─────────────────────────────────────────────────────────────────────────────


class ContentWorkspace:
    """Folders of the content items of one project."""

    def __init__(self, store: MediaStore):
        self.store = store
        self.root = Path(store.root) / CONTENT_DIRNAME
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_project(cls, project_path: Path) -> "ContentWorkspace":
        """Workspace of a project: ``<project>/.codx/media/content``."""
        return cls(MediaStore.for_project(Path(project_path)))

    # ------------------------------------------------------------------ paths
    def item_dir(self, content_id: str) -> Path:
        return self.root / _safe_slug(content_id)

    def sources_dir(self, content_id: str) -> Path:
        return self.item_dir(content_id) / SOURCES_DIRNAME

    def outputs_dir(self, content_id: str) -> Path:
        return self.item_dir(content_id) / OUTPUTS_DIRNAME

    def rel_dir(self, content_id: str) -> str:
        """Project relative folder of the item, as written in the brief."""
        return "%s/%s" % (CONTENT_DIRNAME, _safe_slug(content_id))

    def slugs(self) -> List[str]:
        """Existing content ids, folder name order."""
        if not self.root.exists():
            return []
        return sorted(
            entry.name
            for entry in self.root.iterdir()
            if entry.is_dir() and (entry / BRIEF_JSON).is_file()
        )

    def unique_slug(self, seed: str) -> str:
        base = slugify(seed)
        taken = set(self.slugs())
        if base not in taken:
            return base
        index = 2
        while "%s-%d" % (base, index) in taken:
            index += 1
        return "%s-%d" % (base, index)

    # ------------------------------------------------------------------ items
    def create(self, brief: ContentBrief, overwrite: bool = False) -> ContentBrief:
        """Create the folders of a brief and persist it."""
        content_id = (brief.content_id or "").strip() or self.unique_slug(
            brief.title or brief.intent
        )
        content_id = _safe_slug(content_id)
        if not overwrite and (self.item_dir(content_id) / BRIEF_JSON).is_file():
            content_id = self.unique_slug(content_id)
        brief.content_id = content_id
        brief.formats = normalize_formats(brief.formats)
        brief.state = brief.state or "draft"
        brief.updated_at = utc_now()
        self.sources_dir(content_id).mkdir(parents=True, exist_ok=True)
        self.outputs_dir(content_id).mkdir(parents=True, exist_ok=True)
        return self.save(brief)

    def save(self, brief: ContentBrief) -> ContentBrief:
        """Write ``brief.json`` and the rendered ``brief.md``."""
        if not brief.content_id:
            raise ValueError("A content brief needs a content_id before it is saved")
        brief.updated_at = utc_now()
        directory = self.item_dir(brief.content_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.store._write_json(directory / BRIEF_JSON, brief.model_dump(mode="json"))
        self.store.write_atomic(
            directory / BRIEF_MARKDOWN, render_brief_markdown(brief).encode("utf-8")
        )
        return brief

    def load(self, content_id: str) -> Optional[ContentBrief]:
        """Load a brief; ``None`` when the item is unknown or unreadable."""
        if not content_id:
            return None
        data = self.store._read_json(self.item_dir(content_id) / BRIEF_JSON)
        if not isinstance(data, dict):
            return None
        try:
            return ContentBrief.model_validate(data)
        except Exception:  # noqa: BLE001 - a corrupted brief must not break the list
            return None

    def require(self, content_id: str) -> ContentBrief:
        """Like :meth:`load` but raises ``KeyError`` for an unknown item."""
        brief = self.load(content_id)
        if brief is None:
            raise KeyError(content_id)
        return brief

    def list_items(self) -> List[ContentBrief]:
        """Every readable brief, most recently updated first."""
        items = [self.load(slug) for slug in self.slugs()]
        briefs = [brief for brief in items if brief is not None]
        briefs.sort(key=lambda brief: str(brief.updated_at), reverse=True)
        return briefs

    def delete(self, content_id: str) -> bool:
        """Remove the folder of an item (brief, sources and outputs)."""
        directory = self.item_dir(content_id)
        if not directory.is_dir():
            return False
        for path in sorted(directory.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        directory.rmdir()
        return True

    # ---------------------------------------------------------------- sources
    def add_source(self, content_id: str, source: ContentSource) -> ContentBrief:
        """Append a source (link, note, project reference) to the brief."""
        brief = self.require(content_id)
        if not source.source_id:
            source.source_id = "src-%s" % uuid.uuid4().hex[:8]
        if not source.added_at:
            source.added_at = utc_now()
        brief.sources = list(brief.sources) + [source]
        return self.save(brief)

    def remove_source(self, content_id: str, source_id: str) -> Tuple[ContentBrief, bool]:
        """Drop a source; deletes the uploaded file when it points inside sources/.

        Returns the updated brief and whether the removal happened.
        """
        brief = self.require(content_id)
        remaining: List[ContentSource] = []
        removed: Optional[ContentSource] = None
        for source in brief.sources:
            if source.source_id == source_id and removed is None:
                removed = source
                continue
            remaining.append(source)
        if removed is None:
            return brief, False
        brief.sources = remaining
        if removed.value.startswith("%s/" % SOURCES_DIRNAME):
            target = self.file(content_id, removed.value)
            if target is not None and target.is_file():
                target.unlink(missing_ok=True)
        return self.save(brief), True

    def store_upload(
        self,
        content_id: str,
        name: str,
        data: bytes,
        mime: str = "",
        note: Optional[str] = None,
    ) -> ContentSource:
        """Store an uploaded file inside the sources folder of the item."""
        brief = self.require(content_id)
        if not data:
            raise ValueError("The uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(
                "The file is too big (%s); the limit is %s"
                % (_human_bytes(len(data)), _human_bytes(MAX_UPLOAD_BYTES))
            )
        safe = _safe_filename(name)
        target = self.sources_dir(brief.content_id) / safe
        if target.exists():
            stem, suffix = target.stem, target.suffix
            target = target.with_name("%s-%s%s" % (stem, uuid.uuid4().hex[:6], suffix))
        self.store.write_atomic(target, data)
        return self.add_source(
            brief.content_id,
            ContentSource(
                kind=infer_kind(mime, target.name),
                label=safe,
                value="%s/%s" % (SOURCES_DIRNAME, target.name),
                note=note,
                mime=mime or None,
                bytes=len(data),
            ),
        ).sources[-1]

    def file(self, content_id: str, name: str) -> Optional[Path]:
        """Resolve ``name`` inside the item folder (sources/ or outputs/).

        Returns ``None`` when the path escapes the item folder; the caller turns
        that into a 400/404 so a crafted name can never read other files.
        """
        base = self.item_dir(content_id)
        candidate = (base / str(name or "").replace("\\", "/")).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            return None
        return candidate

    def listing(self, content_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Files of an item, split by folder, for the detail view."""
        result: Dict[str, List[Dict[str, Any]]] = {SOURCES_DIRNAME: [], OUTPUTS_DIRNAME: []}
        for folder in (SOURCES_DIRNAME, OUTPUTS_DIRNAME):
            directory = self.item_dir(content_id) / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file():
                    result[folder].append(
                        {
                            "name": path.name,
                            "size": path.stat().st_size,
                            "path": "%s/%s" % (folder, path.name),
                        }
                    )
        return result

    # ------------------------------------------------------------------ build
    def write_build(self, brief: ContentBrief, plan: BuildPlan) -> Path:
        """Write ``build.md`` next to the brief."""
        target = self.item_dir(brief.content_id) / BUILD_MARKDOWN
        self.store.write_atomic(
            target, render_build_markdown(brief, plan).encode("utf-8")
        )
        return target

    def record_build(self, brief: ContentBrief, build: ContentBuild) -> ContentBrief:
        """Persist the build record and flip the item state."""
        brief.build = build
        brief.state = build.state
        return self.save(brief)


# ─────────────────────────────────────────────────────────────────────────────
# Small validators shared with the API layer
# ─────────────────────────────────────────────────────────────────────────────


def _safe_slug(value: str) -> str:
    """A content id is a single folder name: never a path."""
    slug = slugify(value)
    if slug != str(value or "").strip() and re.search(r"[\\/]|\.\.", str(value or "")):
        raise ValueError("Invalid content id: %r" % (value,))
    return slug


def _safe_filename(value: str) -> str:
    """Strip directories and unsafe characters from an uploaded file name."""
    name = str(value or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    if not name:
        return "upload.bin"
    stem, suffix = Path(name).stem[:MAX_NAME_LENGTH].strip("-"), Path(name).suffix[:12]
    return "%s%s" % (stem or "upload", suffix)


def brief_from_payload(payload: Dict[str, Any]) -> ContentBrief:
    """Build a brief from a request body, tolerating partial payloads."""
    if not isinstance(payload, dict):
        raise ValueError("The content payload must be a json object")
    data = dict(payload)
    # A format may arrive as a comma separated string from a simple form.
    if "formats" in data:
        data["formats"] = normalize_formats(data.get("formats"))
    data.pop("sources", None)  # sources are added through their own endpoints
    if data.get("duration_seconds") in ("", None):
        data["duration_seconds"] = 0
    return ContentBrief.model_validate(data)


def merge_brief(brief: ContentBrief, payload: Dict[str, Any]) -> ContentBrief:
    """Apply a partial update to a brief, keeping the sources untouched."""
    for field in ("title", "intent", "audience", "tone", "language", "notes"):
        if field in payload and payload[field] is not None:
            setattr(brief, field, str(payload[field]))
    if payload.get("duration_seconds") is not None:
        try:
            brief.duration_seconds = int(payload.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            raise ValueError("duration_seconds must be a number of seconds")
    if "formats" in payload and payload["formats"] is not None:
        brief.formats = normalize_formats(payload["formats"])
    brief.updated_at = utc_now()
    return brief
