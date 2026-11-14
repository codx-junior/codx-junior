"""Offline tests of the content creator: briefs, sources and build plans.

No FastAPI, no network: the module under test (``codx.junior.media.content``)
only needs a temporary media store.
"""

from pathlib import Path

import pytest

from codx.junior.media.content import (
    BRIEF_JSON,
    BRIEF_MARKDOWN,
    BUILD_MARKDOWN,
    CONTENT_FORMATS,
    MAX_UPLOAD_BYTES,
    ContentBrief,
    ContentBuild,
    ContentSource,
    ContentWorkspace,
    brief_from_payload,
    format_spec,
    infer_kind,
    merge_brief,
    missing_parts,
    normalize_formats,
    plan_build,
    render_brief_markdown,
    slugify,
)
from codx.junior.media.store import MediaStore


@pytest.fixture()
def workspace(tmp_path: Path) -> ContentWorkspace:
    return ContentWorkspace(MediaStore(tmp_path / ".codx" / "media"))


def make_brief(**kwargs) -> ContentBrief:
    data = {
        "content_id": "channel-launch",
        "title": "Channel launch",
        "intent": "Show how the new channel feature works in codx-junior",
        "formats": ["video", "wiki"],
    }
    data.update(kwargs)
    return ContentBrief(**data)


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_slugify_is_filesystem_safe():
    assert slugify("Channel launch: cómo funciona!") == "channel-launch-como-funciona"
    assert slugify("  ") == "content"
    assert slugify("../../etc/passwd") == "etc-passwd"


def test_slugify_never_keeps_separators():
    slug = slugify("a/b\\c")
    assert "/" not in slug and "\\" not in slug


def test_formats_keep_selection_order_and_dedupe():
    assert normalize_formats(["wiki", "Video", "video"]) == ["wiki", "video"]
    assert normalize_formats("video, short") == ["video", "short"]
    assert normalize_formats(None) == []
    assert normalize_formats([]) == []


def test_unknown_format_is_rejected_with_the_valid_list():
    with pytest.raises(ValueError) as error:
        normalize_formats(["video", "hologram"])
    message = str(error.value)
    assert "hologram" in message
    assert "video" in message


def test_every_declared_format_has_a_spec():
    for spec in CONTENT_FORMATS:
        assert format_spec(spec.id) is spec
        assert spec.output
    assert format_spec("nope") is None


def test_infer_kind_from_mime_then_extension():
    assert infer_kind("image/png", "x") == "image"
    assert infer_kind("", "cover.PNG") == "image"
    assert infer_kind("video/mp4", "x") == "video"
    assert infer_kind("", "clip.webm") == "video"
    assert infer_kind("audio/mpeg", "x") == "audio"
    assert infer_kind("application/pdf", "guide.pdf") == "document"


def test_render_brief_markdown_carries_the_contract():
    brief = make_brief(
        audience="new users",
        tone="friendly",
        language="en",
        duration_seconds=360,
        notes="Keep it under 6 minutes.",
        sources=[
            ContentSource(kind="link", label="Docs", value="https://example.com/docs"),
            ContentSource(kind="image", label="cover.png", value="sources/cover.png", bytes=2048),
        ],
    )
    markdown = render_brief_markdown(brief)
    assert markdown.startswith("# Channel launch")
    assert "Show how the new channel feature works" in markdown
    assert "**Outputs** - video, wiki" in markdown
    assert "**Target length** - 6:00" in markdown
    assert "https://example.com/docs" in markdown
    assert "sources/cover.png" in markdown
    assert "Keep it under 6 minutes." in markdown


def test_plan_build_creates_jobs_only_for_rendered_formats():
    plan = plan_build(make_brief(formats=["video", "short", "wiki", "podcast"]))
    kinds = [spec["kind"] for spec in plan.job_specs]
    assert kinds == ["episode", "short"]
    assert [step.format for step in plan.steps] == ["video", "short", "wiki", "podcast"]
    assert all(step.profile == "codx-junior" for step in plan.steps)
    # The two media steps reference a job, the text steps do not.
    assert [step.kind for step in plan.steps] == ["media-job", "media-job", "builder", "builder"]
    assert plan.steps[0].output == "channel-launch-video.mp4"


def test_plan_build_payload_points_to_the_item_folder():
    brief = make_brief()
    brief.content_id = "channel-launch"
    payload = plan_build(brief).job_specs[0]["payload"]
    assert payload["content_id"] == "channel-launch"
    assert payload["brief"] == "content/channel-launch/brief.md"
    assert payload["output"] == "content/channel-launch/outputs/channel-launch-video.mp4"
    assert payload["title"] == "Channel launch"


def test_plan_build_prompt_is_self_contained():
    plan = plan_build(make_brief())
    assert plan.task_name.startswith("Create content:")
    assert "brief.md" in plan.task_prompt
    assert "redaction" in plan.task_prompt
    assert "outputs/" in plan.task_prompt


def test_missing_parts_explains_what_is_needed():
    assert missing_parts(ContentBrief()) == [
        "Describe what you want to create (the intent).",
        "Pick at least one output format.",
        "Add a title: it becomes the task name and the output file names.",
    ]
    assert missing_parts(make_brief()) == []


def test_plan_without_formats_has_no_steps():
    plan = plan_build(ContentBrief(intent="something"))
    assert plan.steps == []
    assert plan.job_specs == []
    assert plan.missing


# ─────────────────────────────────────────────────────────────────────────────
# Workspace
# ─────────────────────────────────────────────────────────────────────────────


def test_create_writes_brief_json_and_markdown(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    assert brief.content_id == "channel-launch"
    directory = workspace.item_dir(brief.content_id)
    assert (directory / BRIEF_JSON).is_file()
    assert (directory / BRIEF_MARKDOWN).is_file()
    assert (directory / "sources").is_dir()
    assert (directory / "outputs").is_dir()
    assert "Channel launch" in (directory / BRIEF_MARKDOWN).read_text(encoding="utf-8")


def test_create_generates_unique_slugs(workspace: ContentWorkspace):
    first = workspace.create(make_brief())
    second = workspace.create(make_brief())
    assert first.content_id == "channel-launch"
    assert second.content_id == "channel-launch-2"
    assert workspace.slugs() == ["channel-launch", "channel-launch-2"]


def test_load_list_and_delete(workspace: ContentWorkspace):
    created = workspace.create(make_brief())
    loaded = workspace.load(created.content_id)
    assert loaded is not None
    assert loaded.title == created.title
    assert [item.content_id for item in workspace.list_items()] == [created.content_id]

    assert workspace.delete(created.content_id) is True
    assert workspace.load(created.content_id) is None
    assert workspace.delete(created.content_id) is False


def test_load_unknown_and_corrupted(workspace: ContentWorkspace):
    assert workspace.load("nope") is None
    assert workspace.load("") is None
    created = workspace.create(make_brief())
    (workspace.item_dir(created.content_id) / BRIEF_JSON).write_text("{not json", encoding="utf-8")
    assert workspace.load(created.content_id) is None
    with pytest.raises(KeyError):
        workspace.require(created.content_id)


def test_add_and_remove_link_source(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    workspace.add_source(
        brief.content_id, ContentSource(kind="link", label="Docs", value="https://example.com")
    )
    reloaded = workspace.load(brief.content_id)
    assert len(reloaded.sources) == 1
    source = reloaded.sources[0]
    assert source.source_id.startswith("src-")
    assert source.added_at is not None

    _, removed = workspace.remove_source(brief.content_id, source.source_id)
    assert removed is True
    assert workspace.load(brief.content_id).sources == []

    _, removed_again = workspace.remove_source(brief.content_id, source.source_id)
    assert removed_again is False


def test_upload_lands_in_the_item_sources_folder(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    source = workspace.store_upload(brief.content_id, "../../evil name.png", b"\x89PNGdata", "image/png")
    assert source.kind == "image"
    assert source.bytes == 8
    assert source.value == "sources/evil-name.png"
    stored = workspace.item_dir(brief.content_id) / source.value
    assert stored.is_file()
    # Never outside the item folder.
    assert not (workspace.root / "evil-name.png").exists()
    assert workspace.listing(brief.content_id)["sources"][0]["name"] == "evil-name.png"


def test_upload_rejects_empty_and_oversized(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    with pytest.raises(ValueError):
        workspace.store_upload(brief.content_id, "a.png", b"", "image/png")
    with pytest.raises(ValueError) as error:
        workspace.store_upload(brief.content_id, "a.png", b"x" * (MAX_UPLOAD_BYTES + 1), "image/png")
    assert "too big" in str(error.value)


def test_remove_upload_source_deletes_the_file(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    source = workspace.store_upload(brief.content_id, "cover.png", b"png", "image/png")
    path = workspace.item_dir(brief.content_id) / source.value
    assert path.is_file()
    _, removed = workspace.remove_source(brief.content_id, source.source_id)
    assert removed is True
    assert not path.exists()


def test_file_helper_blocks_traversal(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    assert workspace.file(brief.content_id, "../brief.json") is None
    assert workspace.file(brief.content_id, "sources/missing.png") is not None  # resolved, not existing
    inside = workspace.file(brief.content_id, "brief.json")
    assert inside is not None and inside.is_file()


def test_write_build_and_record_build(workspace: ContentWorkspace):
    brief = workspace.create(make_brief())
    plan = plan_build(brief)
    target = workspace.write_build(brief, plan)
    assert target.name == BUILD_MARKDOWN
    text = target.read_text(encoding="utf-8")
    assert "## Steps" in text
    assert "## Prompt" in text

    built = workspace.record_build(
        brief,
        ContentBuild(build_id="build-1", formats=brief.formats, job_ids=["job-1"], state="queued"),
    )
    assert built.state == "queued"
    assert workspace.load(brief.content_id).build.job_ids == ["job-1"]


# ─────────────────────────────────────────────────────────────────────────────
# Payload helpers used by the API layer
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_from_payload_ignores_sources_and_cleans_duration():
    brief = brief_from_payload(
        {
            "title": "Intro",
            "intent": "Explain the channel",
            "formats": "video,short",
            "duration_seconds": "",
            "sources": [{"value": "https://example.com"}],
        }
    )
    assert brief.formats == ["video", "short"]
    assert brief.duration_seconds == 0
    assert brief.sources == []


def test_brief_from_payload_rejects_unknown_format():
    with pytest.raises(ValueError):
        brief_from_payload({"formats": ["hologram"]})


def test_merge_brief_updates_only_the_given_fields():
    brief = make_brief(audience="devs", sources=[ContentSource(kind="link", value="https://x.dev")])
    merged = merge_brief(brief, {"tone": "dry", "formats": ["doc"], "duration_seconds": "90"})
    assert merged.tone == "dry"
    assert merged.formats == ["doc"]
    assert merged.duration_seconds == 90
    assert merged.audience == "devs"  # untouched
    assert len(merged.sources) == 1  # never replaced by a merge
