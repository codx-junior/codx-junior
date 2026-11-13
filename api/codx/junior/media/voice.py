"""Voice resolution for the Project's Channel.

The voice is a property of the profile (``Profile.voice``), but an episode can
be produced with a project wide default voice or with no configuration at all.
This module owns that decision and nothing else: pure python, no I/O, and the
only import is ``codx.junior.media.model`` (stdlib + pydantic), so the profile
model, the worker, the router and the tests can all import it without cycles.

Resolution order used when a profile has to speak:

1. ``profile`` – the profile's own voice, when its ``enabled`` flag is true;
2. ``project`` – the project default voice (``.codx/media`` ``project`` entry);
3. ``system``  – ``DEFAULT_VOICE``, the offline fallback.

``enabled: False`` on the profile is SILENCE: the profile explicitly asked not
to be voiced, so resolution stops there (``source='disabled'``) and it never
falls back to the project or system voice. This is a feature - muting a
profile must keep it muted even in a project that defines a default voice.

A profile that is unknown (or that simply has no voice configured) is not an
error: resolution falls through to the project and then to the system voice.
"""

from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from codx.junior.media.model import VoiceProfile

__all__ = [
    "DEFAULT_VOICE",
    "PROJECT_STORE_KEY",
    "ResolvedVoice",
    "VoiceSource",
    "resolve_voice",
    "voice_snapshot",
]

# Where the resolved voice comes from: profile, project, system, or the
# explicit silence declared by a profile with ``enabled: False``.
VoiceSource = Literal["profile", "project", "system", "disabled"]

# Key used by the media store to persist the project default voice
PROJECT_STORE_KEY = "project"

# Offline system fallback: used when neither the profile nor the project
# declares a voice. ``provider='local'`` keeps the promise of no external
# service in this milestone (a local TTS provider resolves it).
DEFAULT_VOICE = VoiceProfile(
    enabled=True,
    provider="local",
    voice_id="default",
    model=None,
    speed=1.0,
    style=None,
    language=None,
)

class ResolvedVoice(BaseModel):
    """Result of :func:`resolve_voice`, ready to be shown in the UI."""

    voice: VoiceProfile = Field(
        default_factory=lambda: DEFAULT_VOICE.model_copy(deep=True),
        description="Voice to speak with",
    )
    source: VoiceSource = Field(
        default="system",
        description="Origin of the voice: profile, project, system or disabled",
    )
    reason: str = Field(
        default="system default voice",
        description="Human readable explanation shown in the UI",
    )
    profile: Optional[str] = Field(
        default=None, description="Profile the resolution was requested for"
    )


def _as_voice(value: Any) -> Optional[VoiceProfile]:
    """Normalize a voice-shaped value (model, mapping or None) to a model."""
    if value is None:
        return None
    if isinstance(value, VoiceProfile):
        return value
    if isinstance(value, Mapping):
        return VoiceProfile.model_validate(dict(value))
    return None


def _profile_name(entry: Any) -> Optional[str]:
    """Best effort profile name for a profile entry (model or mapping)."""
    if entry is None or isinstance(entry, (str, VoiceProfile)):
        return None
    if isinstance(entry, Mapping):
        name = entry.get("name")
    else:
        name = getattr(entry, "name", None)
    return str(name) if name else None


def _profile_voice(entry: Any) -> Optional[VoiceProfile]:
    """Voice declared by a profile entry, ``None`` when it has none."""
    if entry is None or isinstance(entry, (str, VoiceProfile)):
        return None
    value = entry.get("voice") if isinstance(entry, Mapping) else getattr(entry, "voice", None)
    return _as_voice(value)


def _iterate_profiles(profiles: Any) -> List[Tuple[str, Any]]:
    """Normalize ``profiles`` (mapping or iterable) to ``(name, entry)`` pairs."""
    if not profiles:
        return []
    if isinstance(profiles, Mapping):
        return [(str(name), entry) for name, entry in profiles.items()]
    items: List[Tuple[str, Any]] = []
    for entry in profiles:
        name = _profile_name(entry)
        if name is not None:
            items.append((name, entry))
    return items


def _lookup_profile_voice(profile_name: Optional[str], profiles: Any) -> Optional[VoiceProfile]:
    """Voice configured by ``profile_name``, ``None`` when absent or unset."""
    if not profile_name or not profiles:
        return None
    for name, entry in _iterate_profiles(profiles):
        if name == profile_name:
            return _profile_voice(entry)
    return None


def resolve_voice(
    profile_name: Optional[str] = None,
    profiles: Any = None,
    project_default: Any = None,
    system_default: Optional[VoiceProfile] = DEFAULT_VOICE,
) -> ResolvedVoice:
    """Resolve the voice a profile speaks with.

    ``profiles`` accepts a mapping ``name -> Profile`` or an iterable of
    profiles; ``project_default`` accepts a ``VoiceProfile`` or a mapping.
    A disabled voice on the profile means silence and is never overridden.
    """
    profile_voice = _lookup_profile_voice(profile_name, profiles)
    if profile_voice is not None:
        if not profile_voice.enabled:
            return ResolvedVoice(
                voice=profile_voice.model_copy(deep=True),
                source="disabled",
                reason=(
                    f"voice is disabled in profile '{profile_name}' "
                    "(profile is silent, no fallback)"
                ),
                profile=profile_name,
            )
        return ResolvedVoice(
            voice=profile_voice.model_copy(deep=True),
            source="profile",
            reason=f"voice configured in profile '{profile_name}'",
            profile=profile_name,
        )

    project_voice = _as_voice(project_default)
    if project_voice is not None and project_voice.enabled:
        return ResolvedVoice(
            voice=project_voice.model_copy(deep=True),
            source="project",
            reason="inherited from project",
            profile=profile_name,
        )

    voice = _as_voice(system_default) or DEFAULT_VOICE
    return ResolvedVoice(
        voice=voice.model_copy(deep=True),
        source="system",
        reason="system default voice (no voice configured)",
        profile=profile_name,
    )


def voice_snapshot(
    profiles: Any = None,
    project_default: Any = None,
    system_default: Optional[VoiceProfile] = DEFAULT_VOICE,
) -> Dict[str, VoiceProfile]:
    """Build the ``EpisodeIR.voice_snapshot`` for the speaking profiles.

    Every profile is resolved through :func:`resolve_voice`, so the snapshot
    holds a concrete voice per speaking profile. Disabled profiles are left
    out: they do not speak and must not leak a fallback voice into the episode.
    """
    snapshot: Dict[str, VoiceProfile] = {}
    for name, _entry in _iterate_profiles(profiles):
        resolved = resolve_voice(
            name,
            profiles=profiles,
            project_default=project_default,
            system_default=system_default,
        )
        if resolved.source == "disabled":
            continue
        snapshot[name] = resolved.voice.model_copy(deep=True)
    return snapshot
