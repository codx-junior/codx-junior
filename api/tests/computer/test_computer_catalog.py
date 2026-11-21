"""Merged image catalog: the single feed behind the image grid."""

from codx.junior.computer.catalog import build_catalog, filter_catalog
from codx.junior.computer.custom_images import CoworkingImage, ImageEnvField
from codx.junior.computer.images import ComputerImage
from codx.junior.computer.library import SavedComputer


def _provider_image():
    return ComputerImage(
        id="linuxserver:webtop:debian-kde",
        provider="linuxserver",
        repository="linuxserver/webtop",
        image="lscr.io/linuxserver/webtop:debian-kde",
        tag="debian-kde",
        default_tag="debian-kde",
        name="Debian KDE",
        description="Debian with KDE Plasma.",
        category="Desktop",
        live_view="selkies",
        live_port=3000,
        live_scheme="http",
        tags=["debian-kde", "webtop"],
    )


def _custom_image():
    return CoworkingImage(
        id="abc123",
        slug="research-desktop",
        name="Research desktop",
        description="KDE with the research toolchain",
        env=[ImageEnvField(name="OPENAI_API_KEY", label="OpenAI key", secret=True)],
        tags=["research", "kde"],
        visibility="project",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        status="ready",
    )


def _saved_computer():
    return SavedComputer(
        id="saved-1",
        name="My desktop",
        image="codx-library/saved-1:latest",
        created_at="2026-01-01T00:00:00+00:00",
        has_preview=True,
        tags=["saved"],
    )


def test_build_catalog_merges_every_source():
    items = build_catalog([_provider_image()], [_saved_computer()], [_custom_image()])

    assert [item.source for item in items] == ["linuxserver", "saved", "custom"]
    assert items[0].type == "desktop"
    assert items[1].type == "saved"
    assert items[2].type == "custom"


def test_custom_image_keeps_the_manifest_nested():
    item = build_catalog(custom_images=[_custom_image()])[0]

    assert item.coworking_image is not None
    assert item.coworking_image["env"][0]["name"] == "OPENAI_API_KEY"
    # The media route is authenticated: no public preview URL on the card.
    assert item.preview_url is None
    assert item.has_preview_blob is False


def test_saved_preview_goes_through_the_blob_route():
    item = build_catalog(saved_computers=[_saved_computer()])[0]

    assert item.preview_url is None
    assert item.has_preview_blob is True


def test_filter_catalog_applies_query_tag_and_visibility():
    items = build_catalog([_provider_image()], [_saved_computer()], [_custom_image()])

    assert [item.source for item in filter_catalog(items, query="kde")] == ["linuxserver", "custom"]
    assert [item.source for item in filter_catalog(items, tag="research")] == ["custom"]
    # Saved computers default to project visibility too.
    assert [item.source for item in filter_catalog(items, visibility="project")] == ["saved", "custom"]
    assert [item.source for item in filter_catalog(items, type="saved")] == ["saved"]
    assert filter_catalog(items, source="kasmweb") == []
