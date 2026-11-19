"""Server-side speech capabilities for the voice conversation mode.

The browser is the default engine for both directions:

* speech-to-text uses ``SpeechRecognition`` (Chrome/Edge) and is free/instant;
* text-to-speech uses ``SpeechSynthesis`` and is free/offline.

Some deployments need/want the server to do the work (Firefox/Safari users,
a shared high quality voice, auditing...). This module exposes those optional
backends behind a tiny interface, without making any of them mandatory:

* :class:`SpeechToText` uses ``faster-whisper`` when installed. It is lazily
  imported/cached: the base install does not download torch/ctranslate2 just
  to start the API.
* :func:`default_voice_registry` resolves an OpenAI compatible TTS provider
  when it is configured. No provider means the API answers 503 and the client
  keeps using the browser voice.

Nothing here is imported by the chat engine, so a missing optional dependency
can never break a normal chat.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from codx.junior.media import providers as media_providers
from codx.junior.media.providers import NoProviderAvailable

logger = logging.getLogger(__name__)

#: Hard cap for one uploaded utterance (a call sends short clips).
MAX_AUDIO_BYTES = 25 * 1024 * 1024

#: Extensions faster-whisper/av can read. The upload filename decides the
#: suffix of the temporary file; an unknown container is reported as invalid.
ALLOWED_AUDIO_SUFFIXES = {
    ".webm", ".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".oga", ".flac", ".aac"
}

DEFAULT_WHISPER_MODEL = "small"
DEFAULT_WHISPER_DEVICE = "cpu"
DEFAULT_WHISPER_COMPUTE_TYPE = "float32"

_MODEL_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_MODEL_CACHE: Dict[Tuple[str, str, str], Any] = {}


class SpeechUnavailable(RuntimeError):
    """Raised when a server speech backend cannot run in this installation."""

    def __init__(self, capability: str, reason: str) -> None:
        self.capability = capability
        self.reason = reason
        super().__init__(f"{capability} is not available: {reason}")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def audio_suffix(filename: Optional[str], content_type: Optional[str] = None) -> str:
    """Return a safe temporary suffix for an uploaded utterance."""
    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_AUDIO_SUFFIXES:
        return suffix
    mime = (content_type or "").split(";")[0].strip().lower()
    by_mime = {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".mp4",
        "audio/x-m4a": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/aac": ".aac",
    }
    return by_mime.get(mime, ".webm")


class SpeechToText:
    """Optional ``faster-whisper`` transcription backend."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ) -> None:
        self.model_name = model_name or _env(
            "CODX_JUNIOR_WHISPER_MODEL", DEFAULT_WHISPER_MODEL
        )
        self.device = device or _env(
            "CODX_JUNIOR_WHISPER_DEVICE", DEFAULT_WHISPER_DEVICE
        )
        self.compute_type = compute_type or _env(
            "CODX_JUNIOR_WHISPER_COMPUTE_TYPE", DEFAULT_WHISPER_COMPUTE_TYPE
        )

    def available(self) -> bool:
        """True when ``faster-whisper`` is importable (no model is loaded yet)."""
        try:
            return importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):
            return False

    def info(self) -> dict:
        available = self.available()
        return {
            "available": available,
            "provider": "faster-whisper",
            "model": self.model_name,
            "device": self.device,
            "reason": None
            if available
            else "the optional dependency 'faster-whisper' is not installed",
        }

    def _model(self):
        if not self.available():
            raise SpeechUnavailable(
                "transcription",
                "the optional dependency 'faster-whisper' is not installed",
            )
        key = (self.model_name, self.device, self.compute_type)
        with _MODEL_LOCK:
            model = _MODEL_CACHE.get(key)
            if model is None:
                from faster_whisper import WhisperModel

                logger.info(
                    "Loading whisper model '%s' (%s/%s)",
                    self.model_name,
                    self.device,
                    self.compute_type,
                )
                model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )
                _MODEL_CACHE[key] = model
            return model

    def transcribe(
        self,
        content: bytes,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        language: Optional[str] = None,
    ) -> dict:
        """Transcribe an audio clip and return ``{text, language, provider}``."""
        if not content:
            raise ValueError("empty audio upload")
        if len(content) > MAX_AUDIO_BYTES:
            raise ValueError(
                f"audio is too large (max {MAX_AUDIO_BYTES // (1024 * 1024)} MB)"
            )
        suffix = audio_suffix(filename, content_type)
        if suffix not in ALLOWED_AUDIO_SUFFIXES:
            raise ValueError(f"unsupported audio format '{suffix}'")
        model = self._model()
        tmp_path = None
        try:
            descriptor, tmp_name = tempfile.mkstemp(
                prefix="voice-utterance-", suffix=suffix
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            # The model is a shared, lazy singleton; serialize inference so two
            # callers cannot use it concurrently.
            with _INFERENCE_LOCK:
                segments, info = model.transcribe(
                    str(tmp_path),
                    language=(language or "").split("-")[0] or None,
                    vad_filter=True,
                    no_repeat_ngram_size=2,
                )
            text = " ".join(
                (segment.text or "").strip()
                for segment in segments
                if (segment.text or "").strip()
            ).strip()
            return {
                "text": text,
                "language": getattr(info, "language", None) or language,
                "provider": "faster-whisper",
                "model": self.model_name,
            }
        except SpeechUnavailable:
            raise
        except Exception as ex:  # noqa: BLE001 - surface a useful 503/422
            logger.warning("Whisper transcription failed: %s", ex, exc_info=True)
            raise SpeechUnavailable("transcription", str(ex)) from ex
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


class SpeechSynthesizer:
    """Resolve a configured TTS provider and synthesize one utterance."""

    def __init__(self, registry=None) -> None:
        self.registry = registry or media_providers.default_voice_registry()

    def providers(self) -> List[dict]:
        try:
            return [
                info.model_dump(mode="json")
                for info in self.registry.list("voice")
            ]
        except Exception:  # noqa: BLE001 - a broken provider must not 500
            logger.warning("Could not list TTS providers", exc_info=True)
            return []

    def info(self) -> dict:
        providers = self.providers()
        available = [provider for provider in providers if provider.get("available")]
        return {
            "available": bool(available),
            "providers": providers,
            "reason": None
            if available
            else "no TTS provider is configured (set CODX_JUNIOR_TTS_API_KEY "
            "or keep using the browser voice)",
        }

    def synthesize(
        self,
        text: str,
        provider: str = "",
        voice_id: str = "",
        language: Optional[str] = None,
        speed: float = 1.0,
        style: Optional[str] = None,
    ) -> dict:
        """Synthesize ``text`` and return the provider payload (bytes included)."""
        value = (text or "").strip()
        if not value:
            raise ValueError("text is required")
        try:
            selected = self.registry.resolve("voice", preferred=provider or None)
        except NoProviderAvailable as ex:
            raise SpeechUnavailable("synthesis", str(ex)) from ex
        request = {
            "text": value,
            "voice_id": voice_id,
            "language": language,
            "speed": speed,
            "style": style,
        }
        try:
            result = selected.run(request)
        except NoProviderAvailable as ex:
            raise SpeechUnavailable("synthesis", str(ex)) from ex
        except Exception as ex:  # noqa: BLE001 - report a provider failure truthfully
            logger.warning("TTS provider failed: %s", ex, exc_info=True)
            raise SpeechUnavailable("synthesis", str(ex)) from ex
        if not isinstance(result, dict) or not result.get("content"):
            raise SpeechUnavailable("synthesis", "the TTS provider returned no audio")
        return result


def capabilities(
    speech_to_text: Optional[SpeechToText] = None,
    synthesizer: Optional[SpeechSynthesizer] = None,
) -> dict:
    """Describe the server-side speech backends to the client."""
    stt = speech_to_text or SpeechToText()
    tts = synthesizer or SpeechSynthesizer()
    return {
        "transcription": stt.info(),
        "synthesis": tts.info(),
    }
