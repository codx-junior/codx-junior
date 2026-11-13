"""Local media providers: the offline cover renderer and the ComfyUI stub.

``LocalCoverProvider`` renders a deterministic cover PNG with Pillow. It needs
no network, no model and no API key, so it is the provider that always works:
the channel can build episode covers on a disconnected machine.

``ComfyUICoverProvider`` is the real target (ComfyUI runs locally) but it stays
optional: it is only available when ``COMFYUI_URL`` (or an explicit ``url``) is
set and reachable, it never touches the network at import time, and ``run``
raises ``NoProviderAvailable`` while no workflow is wired.

Both providers cache through the content addressed ``MediaStore``: the cache key
is the sha256 of ``{provider, model, params}`` (``json.dumps(..., sort_keys=True)``)
and the ``key -> asset`` mapping is kept next to the assets, so asking for the
same cover twice returns the stored asset instead of rendering it again.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import string
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from codx.junior.media.model import MediaAsset, ProviderInfo
from codx.junior.media.providers import NoProviderAvailable

logger = logging.getLogger(__name__)

LOCAL_COVER_PROVIDER = "local-cover"
COMFYUI_PROVIDER = "comfyui"
COVER_MODEL = "pillow-cover-v1"
COMFYUI_MODEL = "comfyui-cover-v1"

PNG_MIME = "image/png"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Supported cover formats and the resolution each one renders at
FORMAT_SIZES: Dict[str, Tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}
DEFAULT_FORMAT = "16:9"

DEFAULT_THEME = {
    "bg": "#0f1220",
    "fg": "#f5f7ff",
    "accent": "#4f8cff",
}

# Sidecar file (next to the assets) that maps a cache key to a stored asset
CACHE_SIDECAR_SUFFIX = ".cover.json"
# Bumped whenever the rendering changes so old entries stop matching
CACHE_KEY_VERSION = 1

FONT_ENV_VAR = "CODX_JUNIOR_COVER_FONT"
# Bundled font folder, resolved relative to the repository root when present
BUNDLED_FONT_FOLDERS = (("client", "public", "fonts"),)
SYSTEM_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)

# ComfyUI is the real target but is never required: short probe, placeholder cost
COMFYUI_PROBE_TIMEOUT = 1.5
COMFYUI_COST_PER_COVER = 0.02


# ---------------------------------------------------------------------------
# request normalization and cache key
# ---------------------------------------------------------------------------


def _normalize_color(value: Any, default: str) -> str:
    """Return a ``#rrggbb`` color, falling back to ``default`` when invalid."""
    text = str(value or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3 and all(char in string.hexdigits for char in text):
        text = "".join(char * 2 for char in text)
    if len(text) == 6 and all(char in string.hexdigits for char in text):
        return "#" + text.lower()
    return default


def normalize_cover_request(request: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the canonical form of a cover request.

    Rendering and cache keying both use this normalized dict, so equivalent
    requests (``{"format": "9:16"}`` vs no format, partial themes, ...) hit the
    same cache entry. The function is idempotent.
    """
    request = request or {}
    if not isinstance(request, dict):
        request = {}

    fmt = str(request.get("format") or DEFAULT_FORMAT).strip()
    if fmt not in FORMAT_SIZES:
        fmt = DEFAULT_FORMAT
    width, height = FORMAT_SIZES[fmt]

    theme = request.get("theme") or {}
    if not isinstance(theme, dict):
        theme = {}

    project = str(request.get("project") or "").strip()
    title = str(request.get("title") or "").strip()

    return {
        "title": title or project or "Untitled episode",
        "subtitle": str(request.get("subtitle") or "").strip(),
        "project": project,
        "episode_id": str(request.get("episode_id") or "").strip(),
        "format": fmt,
        "width": width,
        "height": height,
        "theme": {
            key: _normalize_color(theme.get(key), default)
            for key, default in DEFAULT_THEME.items()
        },
    }


def cover_cache_key(
    request: Optional[Dict[str, Any]] = None,
    provider: str = LOCAL_COVER_PROVIDER,
    model: str = COVER_MODEL,
) -> str:
    """Content addressed cache key of a cover request.

    ``sha256(json.dumps({provider, model, params}, sort_keys=True))``: a
    different provider, model or render parameter never reuses an asset.
    """
    payload = {
        "version": CACHE_KEY_VERSION,
        "provider": provider or "",
        "model": model or "",
        "params": normalize_cover_request(request),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# cache sidecars (stored inside the MediaStore assets folder)
# ---------------------------------------------------------------------------


def cover_cache_path(store: Any, cache_key: str) -> Path:
    """Path of the cache entry of ``cache_key`` inside ``store``."""
    return Path(store.assets_dir) / cache_key[:2] / f"{cache_key}{CACHE_SIDECAR_SUFFIX}"


def load_cached_asset(store: Any, cache_key: str) -> Optional[MediaAsset]:
    """Return the stored asset for ``cache_key``; ``None`` when not cached."""
    path = cover_cache_path(store, cache_key)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken entry is a cache miss
        logger.warning("ignoring unreadable cover cache entry %s", path, exc_info=True)
        return None

    try:
        asset = MediaAsset.model_validate(payload)
    except Exception:  # noqa: BLE001
        logger.warning("ignoring invalid cover cache entry %s", path, exc_info=True)
        return None

    if not asset.path:
        return None
    if not (Path(store.root) / asset.path).exists():
        logger.info("cover cache entry %s points to a missing asset", cache_key)
        return None
    return asset


def save_cached_asset(store: Any, cache_key: str, asset: MediaAsset) -> None:
    """Persist the ``cache_key -> asset`` mapping (best effort)."""
    path = cover_cache_path(store, cache_key)
    try:
        data = json.dumps(asset.model_dump(mode="json"), indent=2, sort_keys=True)
        store.write_atomic(path, data.encode("utf-8") + b"\n")
    except Exception:  # noqa: BLE001 - caching must never fail a render
        logger.warning("could not persist cover cache entry %s", cache_key, exc_info=True)


# ---------------------------------------------------------------------------
# font + text helpers
# ---------------------------------------------------------------------------


def _pillow():
    """Return ``(Image, ImageDraw, ImageFont)`` or ``None`` when unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001 - Pillow is a soft dependency here
        logger.debug("Pillow is not importable", exc_info=True)
        return None
    return Image, ImageDraw, ImageFont


@lru_cache(maxsize=1)
def _font_path() -> Optional[str]:
    """First usable font file: env override, bundled font, then system fonts."""
    candidates: List[str] = [os.environ.get(FONT_ENV_VAR, "").strip()]
    candidates.extend(_bundled_fonts())
    candidates.extend(SYSTEM_FONTS)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _bundled_fonts() -> List[str]:
    """Fonts bundled in the repository (``client/public/fonts``), if any."""
    try:
        parents = Path(__file__).resolve().parents[:6]
    except Exception:  # noqa: BLE001
        return []

    for parent in parents:
        for parts in BUNDLED_FONT_FOLDERS:
            folder = parent.joinpath(*parts)
            if not folder.is_dir():
                continue
            fonts: List[str] = []
            for pattern in ("*.ttf", "*.otf"):
                fonts.extend(sorted(str(path) for path in folder.glob(pattern)))
            if fonts:
                return fonts
    return []


def _load_font(size: int):
    """Load a truetype font at ``size``; never raise for a missing font."""
    pil = _pillow()
    if pil is None:
        raise RuntimeError("Pillow is not available")
    _Image, _ImageDraw, ImageFont = pil

    path = _font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - fall back to the bitmap font
            logger.debug("cannot load font %s", path, exc_info=True)

    try:
        return ImageFont.load_default(size=size)
    except Exception:  # noqa: BLE001 - Pillow < 10.1 has no size argument
        return ImageFont.load_default()


def _text_width(draw: Any, text: str, font: Any) -> float:
    """Width of ``text`` in pixels, whatever Pillow version is installed."""
    try:
        return float(draw.textlength(text, font=font))
    except Exception:  # noqa: BLE001
        try:
            return float(font.getlength(text))
        except Exception:  # noqa: BLE001
            return float(len(text) * 10)


def _split_long_word(draw: Any, word: str, font: Any, max_width: float) -> List[str]:
    """Break a word that does not fit in ``max_width`` into chunks."""
    chunks: List[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _ellipsize(draw: Any, text: str, font: Any, max_width: float) -> str:
    """Trim ``text`` until it fits in ``max_width``."""
    trimmed = text
    while trimmed and _text_width(draw, trimmed + "…", font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed and trimmed != text else trimmed


def _wrap_text(
    draw: Any,
    text: str,
    font: Any,
    max_width: float,
    max_lines: Optional[int] = None,
) -> List[str]:
    """Greedy word wrap of ``text`` to ``max_width`` pixels."""
    words = [word for word in re.split(r"\s+", (text or "").strip()) if word]
    if not words:
        return []

    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if _text_width(draw, word, font) <= max_width:
            current = word
        else:
            lines.extend(_split_long_word(draw, word, font, max_width))

    if current:
        lines.append(current)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(draw, lines[-1], font, max_width)
    return lines


def _rgb(color: str) -> Tuple[int, int, int]:
    """``#rrggbb`` -> RGB tuple."""
    value = color or DEFAULT_THEME["bg"]
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def _mix(first: Tuple[int, int, int], second: Tuple[int, int, int], ratio: float):
    """Blend two RGB colors; ``ratio`` 0 keeps ``first``, 1 keeps ``second``."""
    return tuple(
        int(round(first[index] * (1 - ratio) + second[index] * ratio))
        for index in range(3)
    )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_cover_png(request: Optional[Dict[str, Any]]) -> bytes:
    """Render the cover of ``request`` and return the PNG bytes.

    Deterministic: the same request always renders the same bytes (no clock,
    no randomness), which is what makes the content addressed cache safe.
    """
    pil = _pillow()
    if pil is None:
        raise RuntimeError(
            "Pillow is not installed: the local cover provider cannot render"
        )
    Image, ImageDraw, _ImageFont = pil

    params = normalize_cover_request(request)
    width = int(params["width"])
    height = int(params["height"])
    theme = params["theme"]

    background = _rgb(theme["bg"])
    foreground = _rgb(theme["fg"])
    accent = _rgb(theme["accent"])
    muted = _mix(foreground, background, 0.45)

    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    margin = max(24, int(round(width * 0.075)))
    bar_width = max(6, int(round(width * 0.012)))
    gap = max(12, int(round(width * 0.025)))
    text_left = margin + bar_width + gap
    text_width = max(160, width - text_left - margin)

    title_size = max(28, int(round(height * 0.085)))
    subtitle_size = max(20, int(round(height * 0.040)))
    project_size = max(16, int(round(height * 0.028)))
    footer_size = max(14, int(round(height * 0.022)))

    title_font = _load_font(title_size)
    subtitle_font = _load_font(subtitle_size)
    project_font = _load_font(project_size)
    footer_font = _load_font(footer_size)

    title_lines = _wrap_text(draw, params["title"], title_font, text_width, max_lines=4)
    title_line_height = int(round(title_size * 1.24))
    bar_top = int(round(height * 0.30))
    bar_height = title_line_height * max(1, len(title_lines))

    # accent bar next to the title block
    draw.rectangle(
        [margin, bar_top, margin + bar_width, bar_top + bar_height], fill=accent
    )

    y = bar_top
    for line in title_lines:
        draw.text((text_left, y), line, font=title_font, fill=foreground)
        y += title_line_height

    y = bar_top + bar_height + int(round(gap * 0.8))
    for line in _wrap_text(
        draw, params["subtitle"], subtitle_font, text_width, max_lines=3
    ):
        draw.text((text_left, y), line, font=subtitle_font, fill=muted)
        y += int(round(subtitle_size * 1.35))

    baseline_y = max(bar_top + bar_height, height - margin - project_size)
    if params["project"]:
        draw.text((margin, baseline_y), params["project"], font=project_font, fill=accent)

    footer_text = "codx-junior"
    footer_width = _text_width(draw, footer_text, footer_font)
    footer_x = int(round(width - margin - footer_width))
    draw.text(
        (max(margin, footer_x), baseline_y + max(0, project_size - footer_size) // 2),
        footer_text,
        font=footer_font,
        fill=muted,
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


class LocalCoverProvider:
    """Render episode covers locally with Pillow: free, offline, deterministic."""

    name = LOCAL_COVER_PROVIDER
    model = COVER_MODEL

    def info(self) -> ProviderInfo:
        available = self.available()
        return ProviderInfo(
            name=self.name,
            capability="image",
            available=available,
            local=True,
            model=self.model,
            reason=None if available else "Pillow (PIL) is not importable",
            cost_per_unit=0.0,
        )

    def available(self) -> bool:
        """True when Pillow can be imported (it ships with the api)."""
        return _pillow() is not None

    def estimate(self, request: dict) -> float:
        """Local rendering is free."""
        return 0.0

    def run(self, request: dict) -> Dict[str, Any]:
        """Render the cover and return the PNG payload."""
        params = normalize_cover_request(request)
        content = render_cover_png(params)
        return {
            "cache_key": cover_cache_key(params, provider=self.name, model=self.model),
            "mime": PNG_MIME,
            "content": content,
            "format": params["format"],
            "width": params["width"],
            "height": params["height"],
        }

    def render(self, request: dict, store: Any) -> MediaAsset:
        """Return the cached cover asset of ``request``, rendering it on miss.

        ``store`` is a ``MediaStore``; the asset is content addressed, so the
        same cover never produces a duplicate file.
        """
        cache_key = cover_cache_key(request, provider=self.name, model=self.model)
        cached = load_cached_asset(store, cache_key)
        if cached is not None:
            logger.debug("cover cache hit: %s", cache_key)
            return cached

        content = render_cover_png(normalize_cover_request(request))
        asset = store.put_asset(
            content,
            kind="image",
            mime=PNG_MIME,
            provider=self.name,
            model=self.model,
            ext=".png",
        )
        save_cached_asset(store, cache_key, asset)
        logger.info("cover rendered: asset=%s key=%s", asset.asset_id, cache_key)
        return asset


class ComfyUINotConfigured(NoProviderAvailable):
    """Raised when ComfyUI is asked to render a cover it cannot produce yet."""

    def __init__(self, reason: str) -> None:
        super().__init__("image", tried=[COMFYUI_PROVIDER])
        self.reason = str(reason)
        self.args = (f"comfyui cover provider is unavailable: {self.reason}",)


class ComfyUICoverProvider:
    """Optional ComfyUI cover provider (the real, model based target).

    Nothing happens at import or construction time: the endpoint probe runs
    inside ``available()`` (cached afterwards) with a short timeout, so a
    missing or hung ComfyUI never blocks or crashes the channel.
    """

    name = COMFYUI_PROVIDER
    model = COMFYUI_MODEL

    def __init__(
        self,
        url: Optional[str] = None,
        probe_timeout: float = COMFYUI_PROBE_TIMEOUT,
        workflow: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.url = (
            url
            or os.environ.get("COMFYUI_URL")
            or os.environ.get("COMFYUI_API_URL")
            or ""
        ).strip().rstrip("/")
        self.probe_timeout = float(probe_timeout)
        self.workflow = workflow
        self._available: Optional[bool] = None
        self._reason: Optional[str] = None if self.url else "COMFYUI_URL is not set"

    def info(self) -> ProviderInfo:
        available = self.available()
        return ProviderInfo(
            name=self.name,
            capability="image",
            available=available,
            local=True,
            model=self.model,
            reason=None if available else self.reason(),
            cost_per_unit=COMFYUI_COST_PER_COVER,
        )

    def reason(self) -> str:
        """Why ComfyUI cannot render right now (empty string when it can)."""
        if self._reason:
            return self._reason
        return "" if self.available() else "ComfyUI is not reachable"

    def available(self) -> bool:
        """Whether ComfyUI answers; the probe is cached after the first call."""
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        if not self.url:
            self._reason = "COMFYUI_URL is not set"
            return False
        try:
            request = urllib.request.Request(
                f"{self.url}/system_stats", headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=self.probe_timeout) as response:
                response.read(1)
                status = getattr(response, "status", 200)
            if 200 <= int(status) < 300:
                self._reason = None
                return True
            self._reason = f"ComfyUI answered HTTP {status} at {self.url}"
        except Exception as ex:  # noqa: BLE001 - unavailable is a normal state
            self._reason = f"ComfyUI is not reachable at {self.url}: {ex}"
            logger.debug("ComfyUI probe failed: %s", ex)
        return False

    def estimate(self, request: dict) -> float:
        """Placeholder cost of a ComfyUI cover (never zero)."""
        return COMFYUI_COST_PER_COVER

    def run(self, request: dict) -> Dict[str, Any]:
        """Not wired yet: report the reason instead of pretending to render."""
        raise ComfyUINotConfigured(self.reason() or "workflow not implemented")
