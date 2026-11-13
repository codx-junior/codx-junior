"""Pydantic models for the Project's Channel media pipeline.

Pure data definitions: only stdlib + pydantic, no I/O and no imports from
other codx.junior packages, so this module stays importable from the
store, the worker, the API router and the tests alike.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Timezone aware timestamp used as default for every ``created_at``."""
    return datetime.now(timezone.utc)


# Capabilities a provider can declare
ProviderCapability = Literal["image", "voice", "video", "capture", "assemble"]
# Asset kinds stored under .codx/media/assets
MediaKind = Literal["image", "audio", "video", "caption", "doc"]
# Render formats
MediaFormat = Literal["16:9", "9:16"]
# Shot kinds inside an episode ir
ShotKind = Literal["slide", "replay", "still", "title", "transcript"]
# Job kinds
JobKind = Literal["episode", "short", "cover", "revoice"]
# Job lifecycle
JobState = Literal["queued", "leased", "running", "done", "failed", "rejected"]


class ProviderInfo(BaseModel):
    """Description of a media provider and of its current availability."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Provider identifier, e.g. 'comfyui', 'piper'")
    capability: ProviderCapability = Field(
        description="What the provider produces: image, voice, video, capture or assemble"
    )
    available: bool = Field(default=False, description="Provider reachable right now")
    local: bool = Field(default=True, description="Runs on this machine / offline")
    model: Optional[str] = Field(default=None, description="Model or checkpoint used")
    reason: Optional[str] = Field(
        default=None, description="Why the provider is unavailable, when it is"
    )
    cost_per_unit: float = Field(
        default=0.0, description="Cost per unit produced, 0 means free/local"
    )


class MediaAsset(BaseModel):
    """A produced artifact, addressed by content hash and stored in the project."""

    asset_id: str = Field(default="", description="Stable asset identifier")
    kind: MediaKind = Field(default="doc", description="Asset kind")
    path: str = Field(default="", description="Project relative path of the asset")
    sha256: str = Field(default="", description="Content hash used for caching")
    mime: str = Field(default="application/octet-stream")
    bytes: int = Field(default=0, description="Asset size in bytes")
    provider: str = Field(default="", description="Provider that produced the asset")
    model: Optional[str] = Field(default=None, description="Provider model")
    created_at: datetime = Field(default_factory=utc_now)


class RenderSpec(BaseModel):
    """How an episode/short is rendered."""

    format: MediaFormat = Field(default="16:9")
    width: int = Field(default=1920)
    height: int = Field(default=1080)
    fps: int = Field(default=30)
    loudness_target: float = Field(
        default=-16.0, description="Integrated loudness target in LUFS"
    )
    captions: bool = Field(default=True, description="Generate a caption track")
    burned_captions: bool = Field(
        default=False, description="Burn captions into the video"
    )


class VoiceProfile(BaseModel):
    """Voice used for narration; snapshotted into the episode ir."""

    enabled: bool = Field(default=True)
    provider: str = Field(default="")
    voice_id: str = Field(default="")
    model: Optional[str] = Field(default=None)
    speed: float = Field(default=1.0)
    style: Optional[str] = Field(default=None)
    language: Optional[str] = Field(default=None)
    reference_asset: Optional[str] = Field(
        default=None, description="Asset id used as voice reference (cloning)"
    )


class Shot(BaseModel):
    """One unit of the episode timeline."""

    shot_id: str = Field(default="")
    kind: ShotKind = Field(default="slide")
    duration: float = Field(default=0.0, description="Duration in seconds")
    narration: str = Field(default="", description="Text spoken during the shot")
    evidence: List[str] = Field(
        default_factory=list, description="Evidence ids backing the shot"
    )
    profile: Optional[str] = Field(
        default=None, description="Profile that speaks this shot"
    )
    refs: Dict[str, Any] = Field(
        default_factory=dict, description="Free form references (wiki page, replay, ...)"
    )


class EpisodeIR(BaseModel):
    """Intermediate representation of an episode, persisted under .codx/media/episodes."""

    model_config = ConfigDict(extra="allow")

    episode_id: str = Field(default="")
    title: str = Field(default="")
    format: MediaFormat = Field(default="16:9")
    project: str = Field(default="")
    shots: List[Shot] = Field(default_factory=list)
    voice_snapshot: Dict[str, VoiceProfile] = Field(default_factory=dict)
    sources: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    spec: RenderSpec = Field(default_factory=RenderSpec)


class MediaJob(BaseModel):
    """A unit of work of the media worker, persisted with leases and retries."""

    model_config = ConfigDict(extra="allow")

    job_id: str = Field(default="")
    project: str = Field(default="")
    kind: JobKind = Field(default="episode")
    state: JobState = Field(default="queued")
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=3)
    worker_id: Optional[str] = Field(default=None)
    heartbeat_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    payload: Dict[str, Any] = Field(default_factory=dict)
    assets: List[MediaAsset] = Field(default_factory=list)
    cost: float = Field(default=0.0, description="Accumulated cost of the job")
    error: Optional[str] = Field(default=None)
