"""Optional remote text-to-speech provider (OpenAI compatible ``/audio/speech``).

The voice conversation mode always has a zero-configuration fallback: the
browser's ``SpeechSynthesis``. This provider is for installations that want a
better voice (or a voice shared by every device): set the environment
variables below and the API starts serving ``POST /api/voice/speech``.

Environment
-----------
``CODX_JUNIOR_TTS_BASE_URL``
    OpenAI compatible base url (default ``https://api.openai.com/v1``).
``CODX_JUNIOR_TTS_API_KEY``
    API key. ``OPENAI_API_KEY`` is used as a fallback.
``CODX_JUNIOR_TTS_MODEL``
    Speech model (default ``tts-1``).
``CODX_JUNIOR_TTS_VOICE``
    Default voice id (default ``alloy``).
``CODX_JUNIOR_TTS_FORMAT``
    Response format (default ``mp3``).

Nothing happens at import or construction time and ``available()`` never
performs a network call: a missing key simply means "this provider cannot
speak", so a disconnected/self-hosted install keeps the browser fallback.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from codx.junior.media.model import ProviderInfo
from codx.junior.media.providers import NoProviderAvailable

logger = logging.getLogger(__name__)

PROVIDER_NAME = "openai-tts"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"
DEFAULT_FORMAT = "mp3"
REQUEST_TIMEOUT_SECONDS = 60.0

#: Small deterministic table so the endpoint returns a truthful content type.
MIME_BY_FORMAT = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/wav",
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class OpenAIVoiceProvider:
    """Speak text with an OpenAI compatible ``/audio/speech`` endpoint."""

    name = PROVIDER_NAME
    model = DEFAULT_MODEL

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        response_format: Optional[str] = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (
            base_url
            or _env("CODX_JUNIOR_TTS_BASE_URL")
            or _env("OPENAI_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key
            or _env("CODX_JUNIOR_TTS_API_KEY")
            or _env("OPENAI_API_KEY")
        )
        self.model = model or _env("CODX_JUNIOR_TTS_MODEL", DEFAULT_MODEL)
        self.voice = voice or _env("CODX_JUNIOR_TTS_VOICE", DEFAULT_VOICE)
        self.response_format = (
            response_format or _env("CODX_JUNIOR_TTS_FORMAT", DEFAULT_FORMAT)
        ).lower()
        self.timeout = float(timeout)

    # ------------------------------------------------------------------ protocol
    def info(self) -> ProviderInfo:
        available = self.available()
        return ProviderInfo(
            name=self.name,
            capability="voice",
            available=available,
            local=False,
            model=self.model,
            reason=None if available else "CODX_JUNIOR_TTS_API_KEY is not set",
            cost_per_unit=0.0,
        )

    def available(self) -> bool:
        """True when the provider has credentials; never touches the network."""
        return bool(self.api_key and self.base_url)

    def estimate(self, request: dict) -> float:
        """Remote TTS pricing is provider/model dependent; report unknown."""
        return 0.0

    def run(self, request: dict) -> Dict[str, Any]:
        """Return ``{"content": bytes, "mime": ..., "provider": ...}``.

        Raises:
            NoProviderAvailable: when the provider is not configured.
            RuntimeError: when the provider answers with an error.
        """
        if not self.available():
            raise NoProviderAvailable("voice", tried=[self.name])
        text = str((request or {}).get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        voice = str((request or {}).get("voice_id") or self.voice or DEFAULT_VOICE)
        model = str((request or {}).get("model") or self.model or DEFAULT_MODEL)
        fmt = str((request or {}).get("format") or self.response_format or DEFAULT_FORMAT)
        speed = float((request or {}).get("speed") or 1.0)
        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "speed": max(0.25, min(4.0, speed)),
            "response_format": fmt,
        }
        url = f"{self.base_url}/audio/speech"
        request_obj = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": MIME_BY_FORMAT.get(fmt, "audio/mpeg"),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request_obj, timeout=self.timeout) as response:
                content = response.read()
                content_type = (
                    response.headers.get("Content-Type")
                    or MIME_BY_FORMAT.get(fmt)
                    or "audio/mpeg"
                )
        except urllib.error.HTTPError as ex:
            detail = ""
            try:
                detail = ex.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                detail = ""
            raise RuntimeError(
                f"TTS provider '{self.name}' answered HTTP {ex.code}: {detail}"
            ) from ex
        except urllib.error.URLError as ex:
            raise RuntimeError(
                f"TTS provider '{self.name}' is unreachable: {ex.reason}"
            ) from ex
        if not content:
            raise RuntimeError(f"TTS provider '{self.name}' returned no audio")
        return {
            "content": content,
            "mime": content_type.split(";")[0].strip(),
            "provider": self.name,
            "model": model,
            "voice_id": voice,
            "format": fmt,
        }
