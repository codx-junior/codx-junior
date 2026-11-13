"""Offline tests for the media data models."""

import pytest
from pydantic import ValidationError

from codx.junior.media.model import (
    EpisodeIR,
    MediaAsset,
    MediaJob,
    ProviderInfo,
    RenderSpec,
    Shot,
    VoiceProfile,
)


def test_provider_info_from_dict():
    info = ProviderInfo.model_validate({
        "name": "comfyui",
        "capability": "image",
        "available": False,
        "local": True,
        "reason": "not running",
    })
    assert info.name == "comfyui"
    assert info.capability == "image"
    assert info.available is False
    assert info.cost_per_unit == 0.0


def test_media_asset_from_dict():
    asset = MediaAsset.model_validate({
        "asset_id": "a1",
        "kind": "image",
        "path": ".codx/media/assets/a1.png",
        "sha256": "deadbeef",
        "mime": "image/png",
        "bytes": 1234,
        "provider": "local",
        "model": "pillow",
    })
    assert asset.kind == "image"
    assert asset.bytes == 1234
    assert asset.created_at is not None


def test_render_spec_defaults():
    spec = RenderSpec()
    assert spec.format == "16:9"
    assert spec.width == 1920
    assert spec.height == 1080
    assert spec.fps == 30
    assert spec.loudness_target == -16.0
    assert spec.captions is True
    assert spec.burned_captions is False


def test_voice_profile_defaults():
    voice = VoiceProfile()
    assert voice.enabled is True
    assert voice.speed == 1.0
    assert voice.reference_asset is None


def test_media_job_defaults():
    job = MediaJob()
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.state == "queued"
    assert job.worker_id is None
    assert job.heartbeat_at is None
    assert job.cost == 0.0
    assert job.assets == []
    assert job.error is None


def test_media_job_json_roundtrip():
    job = MediaJob(
        job_id="j1",
        project="demo",
        kind="cover",
        payload={"prompt": "a cover"},
        assets=[MediaAsset(asset_id="a1", kind="image", path="cover.png", sha256="abc")],
    )
    restored = MediaJob.model_validate_json(job.model_dump_json())
    assert restored.job_id == job.job_id
    assert restored.kind == "cover"
    assert restored.payload == {"prompt": "a cover"}
    assert restored.assets[0].asset_id == "a1"
    assert restored.created_at.tzinfo is not None


def test_episode_ir_defaults():
    episode = EpisodeIR()
    assert episode.format == "16:9"
    assert episode.shots == []
    assert episode.sources == []
    assert isinstance(episode.spec, RenderSpec)
    assert episode.spec.width == 1920
    assert episode.spec.height == 1080


def test_episode_ir_json_roundtrip():
    episode = EpisodeIR(
        episode_id="e1",
        title="Pilot",
        format="9:16",
        project="demo",
        shots=[
            Shot(
                shot_id="s1",
                kind="slide",
                duration=12.5,
                narration="hello",
                evidence=["ev1"],
                profile="biblio",
                refs={"wiki_path": "docs/a.md"},
            )
        ],
        voice_snapshot={"biblio": VoiceProfile(provider="piper", voice_id="v1")},
        sources=["wiki:docs/a.md"],
        spec=RenderSpec(format="9:16", width=1080, height=1920),
    )
    restored = EpisodeIR.model_validate_json(episode.model_dump_json())
    assert restored.episode_id == "e1"
    assert restored.format == "9:16"
    assert restored.spec.width == 1080
    assert restored.shots[0].refs == {"wiki_path": "docs/a.md"}
    assert restored.voice_snapshot["biblio"].voice_id == "v1"


def test_media_job_rejects_unknown_state():
    with pytest.raises(ValidationError):
        MediaJob(job_id="j2", project="demo", kind="episode", state="exploded")


def test_shot_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        Shot(shot_id="s9", kind="hologram")
