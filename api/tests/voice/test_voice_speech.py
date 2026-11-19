"""Tests of the optional server speech backends."""

import json

import pytest

from codx.junior.media.model import ProviderInfo
from codx.junior.media.providers import ProviderRegistry
from codx.junior.media.providers.voice_openai import OpenAIVoiceProvider
from codx.junior.voice.speech import (
    SpeechSynthesizer,
    SpeechToText,
    SpeechUnavailable,
    audio_suffix,
)


class FakeVoiceProvider:
    def __init__(self, available=True):
        self._available = available
        self.requests = []

    def info(self):
        return ProviderInfo(
            name="fake-voice",
            capability="voice",
            available=self._available,
            local=False,
            model="fake-tts",
            reason=None if self._available else "offline",
        )

    def available(self):
        return self._available

    def estimate(self, request):
        return 0.0

    def run(self, request):
        self.requests.append(request)
        return {
            "content": b"audio-bytes",
            "mime": "audio/mpeg",
            "provider": "fake-voice",
            "model": "fake-tts",
        }


def registry_with(provider):
    registry = ProviderRegistry()
    registry.register(provider)
    return registry


def test_audio_suffix_prefers_a_known_extension():
    assert audio_suffix("clip.WEBM", "audio/webm") == ".webm"
    assert audio_suffix("clip", "audio/ogg;codecs=opus") == ".ogg"
    assert audio_suffix("clip.unknown", "audio/mp4") == ".mp4"


def test_synthesis_returns_provider_payload():
    provider = FakeVoiceProvider()
    synthesizer = SpeechSynthesizer(registry_with(provider))

    result = synthesizer.synthesize("hello", voice_id="v1", speed=1.25)

    assert result["content"] == b"audio-bytes"
    assert provider.requests[0]["text"] == "hello"
    assert provider.requests[0]["voice_id"] == "v1"
    assert provider.requests[0]["speed"] == 1.25


def test_synthesis_without_a_provider_raises_unavailable():
    synthesizer = SpeechSynthesizer(ProviderRegistry())

    with pytest.raises(SpeechUnavailable) as error:
        synthesizer.synthesize("hello")

    assert error.value.capability == "synthesis"


def test_unavailable_provider_is_not_selected():
    synthesizer = SpeechSynthesizer(registry_with(FakeVoiceProvider(available=False)))

    assert synthesizer.info()["available"] is False
    with pytest.raises(SpeechUnavailable):
        synthesizer.synthesize("hello")


def test_stt_info_is_truthful_when_the_dependency_is_missing(monkeypatch):
    stt = SpeechToText()
    monkeypatch.setattr(stt, "available", lambda: False)

    info = stt.info()

    assert info["available"] is False
    assert "faster-whisper" in info["reason"]
    with pytest.raises(SpeechUnavailable):
        stt.transcribe(b"audio")


def test_openai_voice_provider_needs_an_api_key(monkeypatch):
    monkeypatch.delenv("CODX_JUNIOR_TTS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIVoiceProvider(base_url="https://example.test/v1", api_key="")

    assert provider.available() is False
    assert provider.info().available is False


def test_openai_voice_provider_posts_the_speech_request(monkeypatch):
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "audio/mpeg"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"mp3-bytes"

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAIVoiceProvider(
        base_url="https://example.test/v1",
        api_key="secret",
        model="tts-1",
        voice="alloy",
    )

    result = provider.run({"text": "hello", "voice_id": "nova", "speed": 1.5})

    assert result["content"] == b"mp3-bytes"
    assert captured["url"] == "https://example.test/v1/audio/speech"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["input"] == "hello"
    assert captured["payload"]["voice"] == "nova"
    assert captured["payload"]["speed"] == 1.5
