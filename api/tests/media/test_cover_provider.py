"""Offline tests for the local cover provider and its content addressed cache.

No network and no AI service is involved: the provider only needs Pillow, which
is why the whole module is skipped when it is missing.
"""

import hashlib
import os

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from codx.junior.media.providers import (  # noqa: E402
    MediaProvider,
    NoProviderAvailable,
    default_image_registry,
)
from codx.junior.media.providers import image_local  # noqa: E402
from codx.junior.media.providers.image_local import (  # noqa: E402
    PNG_MAGIC,
    ComfyUICoverProvider,
    LocalCoverProvider,
    cover_cache_key,
    normalize_cover_request,
    render_cover_png,
)
from codx.junior.media.store import MediaStore  # noqa: E402


def _store(tmp_path):
    return MediaStore(tmp_path / "media")


def _request(title="Deploying the channel", fmt="16:9", **extra):
    request = {
        "title": title,
        "subtitle": "Episode 12 - background services, wiki scheduler and more",
        "format": fmt,
        "project": "codx-junior",
        "episode_id": "ep-1",
    }
    request.update(extra)
    return request


# ------------------------------------------------------------------ registry


def test_provider_folder_does_not_shadow_the_registry_module():
    """``media/providers/`` must stay a plain folder (regression guard)."""
    assert image_local.__file__.endswith(os.path.join("providers", "image_local.py"))
    assert default_image_registry().names("image")


def test_local_provider_declares_a_free_offline_image_capability():
    provider = LocalCoverProvider()
    assert isinstance(provider, MediaProvider)
    info = provider.info()
    assert info.name == "local-cover"
    assert info.capability == "image"
    assert info.local is True
    assert info.available is True
    assert provider.available() is True
    assert provider.estimate(_request()) == 0.0


def test_default_image_registry_resolves_the_local_provider(monkeypatch):
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    monkeypatch.delenv("COMFYUI_API_URL", raising=False)

    registry = default_image_registry()
    assert set(registry.names("image")) == {"local-cover", "comfyui"}
    assert registry.resolve("image").info().name == "local-cover"


# ------------------------------------------------------------------ rendering


@pytest.mark.parametrize(
    "fmt, size",
    [("16:9", (1920, 1080)), ("9:16", (1080, 1920))],
)
def test_render_writes_a_valid_png_cover_per_format(tmp_path, fmt, size):
    store = _store(tmp_path)

    asset = LocalCoverProvider().render(_request(fmt=fmt), store)

    assert asset.kind == "image"
    assert asset.mime == "image/png"
    assert asset.provider == "local-cover"
    assert asset.asset_id

    path = store.root / asset.path
    assert path.exists()
    content = path.read_bytes()
    assert content.startswith(PNG_MAGIC)
    assert asset.bytes == len(content)
    assert asset.sha256 == hashlib.sha256(content).hexdigest()

    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == size


def test_cover_render_is_deterministic():
    assert render_cover_png(_request()) == render_cover_png(_request())


def test_repeated_render_reuses_the_asset_without_rewriting_the_file(tmp_path):
    store = _store(tmp_path)
    provider = LocalCoverProvider()
    request = _request()

    first = provider.render(request, store)
    path = store.root / first.path

    # A rewrite replaces the file (atomic write + os.replace), so a frozen
    # timestamp proves the second call did not render/write again.
    frozen = 1_000_000_000
    os.utime(path, (frozen, frozen))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    second = provider.render(dict(request), store)

    assert second.asset_id == first.asset_id
    assert second.path == first.path
    assert int(os.stat(path).st_mtime) == frozen
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert len(list((store.root / "assets").rglob("*.png"))) == 1
    assert len(list((store.root / "assets").rglob("*.cover.json"))) == 1


def test_render_refuses_a_cache_entry_whose_asset_disappeared(tmp_path):
    store = _store(tmp_path)
    provider = LocalCoverProvider()

    first = provider.render(_request(), store)
    (store.root / first.path).unlink()

    second = provider.render(_request(), store)

    assert (store.root / second.path).exists()
    assert second.asset_id == first.asset_id


def test_a_different_title_produces_a_different_asset(tmp_path):
    store = _store(tmp_path)
    provider = LocalCoverProvider()

    first = provider.render(_request(title="Alpha release"), store)
    second = provider.render(_request(title="Beta release"), store)

    assert first.asset_id != second.asset_id
    assert first.path != second.path
    assert (store.root / first.path).read_bytes() != (
        store.root / second.path
    ).read_bytes()


def test_cache_key_is_stable_and_sensitive_to_every_parameter():
    assert cover_cache_key(_request()) == cover_cache_key(_request())
    assert cover_cache_key(_request()) != cover_cache_key(_request(title="other"))
    assert cover_cache_key(_request()) != cover_cache_key(_request(fmt="9:16"))
    assert cover_cache_key(_request()) != cover_cache_key(
        _request(theme={"bg": "#101010"})
    )
    # Equivalent requests (normalized theme, explicit 16:9) share a key
    default_theme = normalize_cover_request(_request())["theme"]
    assert cover_cache_key(_request()) == cover_cache_key(
        _request(fmt="16:9", theme=default_theme)
    )


def test_bad_theme_colors_fall_back_to_the_defaults():
    params = normalize_cover_request({"title": "x", "theme": {"bg": "nope"}})
    assert params["theme"]["bg"] == image_local.DEFAULT_THEME["bg"]
    assert params["theme"]["accent"] == image_local.DEFAULT_THEME["accent"]


# ------------------------------------------------------------------- comfyui


def test_comfyui_provider_is_optional_and_never_blocks(monkeypatch):
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    monkeypatch.delenv("COMFYUI_API_URL", raising=False)

    provider = ComfyUICoverProvider()

    assert provider.info().capability == "image"
    assert provider.available() is False
    assert provider.estimate(_request()) > 0.0
    with pytest.raises(NoProviderAvailable) as excinfo:
        provider.run(_request())
    assert "comfyui" in str(excinfo.value).lower()


def test_comfyui_construction_does_no_io(monkeypatch):
    """An unreachable endpoint is reported by ``available()``, not raised."""
    provider = ComfyUICoverProvider(url="http://127.0.0.1:9", probe_timeout=0.1)
    assert provider.available() is False
    assert "9" in provider.reason()
