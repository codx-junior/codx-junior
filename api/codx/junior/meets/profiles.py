"""
Profile participants of a meeting.

An AI participant is a normal :class:`~codx.junior.model.profile.Profile` of
the project: it carries the avatar, the display name, the instructions, the
model and the voice. This module resolves those attributes and keeps the
meeting document independent from the profile files (the participant stores a
snapshot: name, avatar, voice and project).

Voice resolution mirrors ``GET /api/profiles/{name}/voice``: the profile's own
voice wins; a project default is used when the profile does not declare one;
``enabled: False`` means silence and is never overridden.
"""

from __future__ import annotations

import json
import logging

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from codx.junior.media import providers as media_providers
from codx.junior.media.model import VoiceProfile
from codx.junior.media.store import MediaStore
from codx.junior.media.voice import PROJECT_STORE_KEY, ResolvedVoice, resolve_voice
from codx.junior.model.profile import Profile
from codx.junior.profiles.profile_manager import ProfileManager

logger = logging.getLogger(__name__)

PROJECT_SETTINGS_FILENAME = "settings.json"


def _as_voice(value: Any) -> Optional[VoiceProfile]:
    """Normalize a voice-shaped value (model, mapping or None)."""
    if value is None:
        return None
    if isinstance(value, VoiceProfile):
        return value
    if isinstance(value, Mapping):
        try:
            return VoiceProfile.model_validate(dict(value))
        except Exception:  # noqa: BLE001 - a broken voice is "no voice"
            return None
    return None


def project_path(settings: Any) -> Optional[Path]:
    """Project source path of the session settings, when available."""
    value = getattr(settings, "abs_project_path", None)
    if value:
        return Path(value)
    project_path = getattr(settings, "project_path", None)
    return Path(project_path) if project_path else None


def project_default_voice(settings: Any) -> Optional[VoiceProfile]:
    """Read the project default voice (``.codx/media`` ``project`` entry)."""
    path = project_path(settings)
    if path is None:
        return None
    try:
        store = MediaStore.for_project(path)
        getter = getattr(store, "get_setting", None)
        if callable(getter):
            voice = _as_voice(getter(PROJECT_STORE_KEY))
            if voice is not None:
                return voice
        settings_path = Path(store.root) / PROJECT_SETTINGS_FILENAME
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            return _as_voice(payload.get(PROJECT_STORE_KEY))
    except Exception:  # noqa: BLE001 - the default voice is optional
        logger.debug("Could not read the project default voice", exc_info=True)
    return None


def load_profile(session: Any, profile_name: str) -> Optional[Profile]:
    """Load a profile visible from the session project, ``None`` when unknown."""
    name = str(profile_name or "").strip().lstrip("@").strip()
    if not name:
        return None
    try:
        manager = ProfileManager(settings=session.settings)
        if not manager.profile_exists(name):
            return None
        return manager.read_profile(name)
    except Exception as ex:  # noqa: BLE001 - an unreadable profile is unknown
        logger.warning("Meeting: cannot load profile '%s': %s", name, ex)
        return None


def resolve_profile_voice(session: Any, profile: Profile) -> ResolvedVoice:
    """Resolve the voice of ``profile`` for the session project."""
    name = (profile.name or "").strip()
    return resolve_voice(
        name,
        profiles={name: profile},
        project_default=project_default_voice(session.settings),
    )


def voice_payload(resolved: ResolvedVoice) -> Dict[str, Any]:
    """Serialize a resolved voice for a participant snapshot."""
    return {
        "source": resolved.source,
        "reason": resolved.reason,
        "enabled": resolved.source != "disabled",
        "voice": resolved.voice.model_dump(mode="json"),
    }


def participant_snapshot(session: Any, profile_name: str) -> Optional[Dict[str, Any]]:
    """
    Build the participant snapshot of ``profile_name``.

    Returns:
        ``{profile_name, name, avatar, project_id, voice}`` or ``None`` when the
        profile does not exist in the project.
    """
    profile = load_profile(session, profile_name)
    if profile is None:
        return None
    resolved = resolve_profile_voice(session, profile)
    return {
        "profile_name": profile.name or profile_name,
        "name": profile.name or profile_name,
        "avatar": profile.avatar or "",
        "project_id": getattr(session.settings, "project_id", None),
        "voice": voice_payload(resolved),
        "description": profile.description or "",
        "model": profile.llm_model or "",
        "content": profile.parsed_content or profile.content or "",
        "use_knowledge": bool(profile.use_knowledge),
        "tags": list(profile.tags or []),
    }

def profile_tts_provider(resolved: ResolvedVoice):
    """Provider able to speak ``resolved.voice`` (``None`` when unavailable)."""
    try:
        return media_providers.default_registry.resolve(
            "voice", preferred=resolved.voice.provider or None
        )
    except media_providers.NoProviderAvailable:
        return None


def profile_for_participant(session: Any, participant: Any) -> Optional[Profile]:
    """Load the profile behind a :class:`MeetParticipant`."""
    name = getattr(participant, "profile_name", "") or getattr(
        participant, "username", ""
    )
    return load_profile(session, name)


def participant_voice(session: Any, participant: Any) -> Tuple[Optional[ResolvedVoice], Optional[Profile]]:
    """Return the resolved voice and profile of a virtual participant."""
    profile = profile_for_participant(session, participant)
    if profile is None:
        return None, None
    return resolve_profile_voice(session, profile), profile
