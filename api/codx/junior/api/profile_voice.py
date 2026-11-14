"""Profile voice API: which voice a profile speaks with, and a sample preview.

The voice is a profile property (``Profile.voice``) and the decision of *which*
voice is used is owned by :mod:`codx.junior.media.voice` (pure python, no I/O).
This router is the thin HTTP layer over it — no business logic lives here, the
endpoints only translate the request into a media call and dump the result as
plain json.

Endpoints
---------
GET  /api/profiles/{profile_name}/voice          resolved voice + source + reason
POST /api/profiles/{profile_name}/voice/preview  synthesize a short sample

The router is auto discovered: ``codx.junior.api.discover_routers()`` imports
every module of this package and registers its ``router`` with the ``/api``
prefix, so ``APIRouter(prefix="/profiles")`` serves ``/api/profiles/*`` and no
change is needed in ``app.py``.

Resolution
----------
:func:`resolve_voice` is called with the profile loaded through the same
manager the profiles router uses (:class:`ProfileManager`) and with the project
default voice read from the project media store (``.codx/media`` entry
``PROJECT_STORE_KEY``). A profile that declares ``enabled: False`` is silent and
is never overridden by the project or system voice.

Preview
-------
There is no TTS engine in M0. The provider is resolved through
:mod:`codx.junior.media.providers` (the default registry) instead of a hardcoded
vendor, so a local TTS provider can be registered later without touching this
endpoint. When nobody can serve the ``voice`` capability the endpoint answers
**503** with ``{"reason": "no tts provider", ...}``: it never returns fake or
silent audio.
"""

import json
import logging
from pathlib import Path as FsPath
from typing import Any, Dict, Mapping, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from codx.junior.api import get_current_session
from codx.junior.engine import CODXJuniorSession
from codx.junior.media import providers as media_providers
from codx.junior.media.model import VoiceProfile
from codx.junior.media.store import MediaStore
from codx.junior.media.voice import PROJECT_STORE_KEY, ResolvedVoice, resolve_voice
from codx.junior.model.profile import Profile
from codx.junior.profiles.profile_manager import ProfileManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["profiles"])

#: Capability a speech provider declares (see ``media.model.ProviderCapability``)
VOICE_CAPABILITY = "voice"
#: Store file holding the project level media settings, keyed by PROJECT_STORE_KEY
SETTINGS_FILENAME = "settings.json"
#: Text of the preview sample: short on purpose, a preview must stay cheap
PREVIEW_SAMPLE = "This is a preview of the voice used by this profile."
#: Reason reported when no provider can synthesize speech (M0 has no TTS engine)
NO_TTS_REASON = "no tts provider"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def project_path(session: CODXJuniorSession) -> FsPath:
    """Project directory of the active session."""
    settings = getattr(session, "settings", None)
    value = getattr(settings, "abs_project_path", None)
    if not value:
        raise HTTPException(status_code=500, detail="No project path in session")
    return FsPath(value)


def store_for_project(project_dir: FsPath) -> MediaStore:
    """Store of a project: ``<project>/.codx/media``."""
    return MediaStore.for_project(FsPath(project_dir))


def _store(session: CODXJuniorSession) -> MediaStore:
    return store_for_project(project_path(session))


def _as_voice(value: Any) -> Optional[VoiceProfile]:
    """Normalize a voice-shaped value (model or mapping) to ``VoiceProfile``."""
    if value is None:
        return None
    if isinstance(value, VoiceProfile):
        return value
    if isinstance(value, Mapping):
        return VoiceProfile.model_validate(dict(value))
    return None


def project_default_voice(store: MediaStore) -> Optional[VoiceProfile]:
    """Project default voice stored by the project, ``None`` when it has none.

    The entry lives in the media store under ``PROJECT_STORE_KEY``. The store
    API is preferred when it exists; otherwise the same entry is read from the
    ``settings.json`` mirror that sits next to the jobs/episodes folders. Both
    paths are tolerant: a missing or unreadable entry simply means "no project
    default", which makes resolution fall back to the system voice.
    """
    getter = getattr(store, "get_setting", None)
    if callable(getter):
        try:
            voice = _as_voice(getter(PROJECT_STORE_KEY))
        except Exception:  # noqa: BLE001 - a broken store is not an error here
            logger.debug("Could not read the project voice from the store", exc_info=True)
            voice = None
        if voice is not None:
            return voice

    settings_path = FsPath(store.root) / SETTINGS_FILENAME
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    return _as_voice(payload.get(PROJECT_STORE_KEY))


def load_profile(session: CODXJuniorSession, profile_name: str) -> Profile:
    """Load the profile of the request with the same loader the profiles API uses.

    An unknown profile is a 404, never a silent fallback.
    """
    name = (profile_name or "").strip()
    if not name:
        raise HTTPException(status_code=404, detail="Unknown profile")
    try:
        manager = ProfileManager(settings=session.settings)
        if not manager.profile_exists(name):
            raise HTTPException(status_code=404, detail=f"Unknown profile '{name}'")
        return manager.read_profile(name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - an unreadable profile is unknown
        logger.warning("Could not load profile '%s': %s", name, exc)
        raise HTTPException(status_code=404, detail=f"Unknown profile '{name}'") from exc


def resolve_profile_voice(
    session: CODXJuniorSession, profile_name: str
) -> ResolvedVoice:
    """Resolve the voice of ``profile_name`` for the project of the request."""
    profile = load_profile(session, profile_name)
    name = (profile.name or profile_name or "").strip()
    return resolve_voice(
        name,
        profiles={name: profile},
        project_default=project_default_voice(_store(session)),
    )


def voice_provider(voice: VoiceProfile):
    """Provider able to speak ``voice``, ``None`` when nobody can (yet).

    Resolution goes through the media provider registry, so a local TTS
    provider dropped in later is picked up without changing the endpoint.
    """
    try:
        return media_providers.default_registry.resolve(
            VOICE_CAPABILITY, preferred=voice.provider or None
        )
    except media_providers.NoProviderAvailable:
        return None


def _provider_name(provider: Any) -> Optional[str]:
    """Name of ``provider``, ``None`` when it cannot describe itself."""
    try:
        return provider.info().name
    except Exception:  # noqa: BLE001 - a broken provider only loses its label
        return None


def voice_preview(
    session: CODXJuniorSession, profile_name: str
) -> Tuple[int, Dict[str, Any]]:
    """``(http status, body)`` of a preview: 200 sample, 409 muted, 503 no engine."""
    resolved = resolve_profile_voice(session, profile_name)
    body: Dict[str, Any] = {
        "profile": resolved.profile,
        "source": resolved.source,
        "reason": resolved.reason,
        "voice": resolved.voice.model_dump(mode="json"),
        "provider": None,
    }
    if resolved.source == "disabled":
        return 409, {
            **body,
            "reason": "voice is disabled in this profile (profile is silent)",
        }

    provider = voice_provider(resolved.voice)
    if provider is None:
        return 503, {**body, "reason": NO_TTS_REASON}

    name = _provider_name(provider)
    try:
        sample = provider.run({"text": PREVIEW_SAMPLE, "voice": body["voice"]})
    except Exception as exc:  # noqa: BLE001 - the client learns the engine failed
        logger.warning("Voice preview failed for profile '%s': %s", profile_name, exc)
        return 503, {**body, "provider": name, "reason": f"tts provider failed: {exc}"}
    return 200, {**body, "provider": name, "sample": sample}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{profile_name}/voice",
    summary="Voice a profile speaks with",
)
async def get_profile_voice(
    profile_name: str = Path(
        min_length=1,
        description="Profile name (e.g. software_developer)",
    ),
    session: CODXJuniorSession = Depends(get_current_session),
) -> Dict[str, Any]:
    """
    Return the voice a profile speaks with: ``voice``, ``source``, ``reason``.

    ``source`` is ``profile``, ``project``, ``system`` or ``disabled`` (the
    profile explicitly asked to stay silent, with no fallback).

    Example:
        GET /api/profiles/software_developer/voice?codx_path=/path/to/project
    """
    resolved = resolve_profile_voice(session, profile_name)
    return resolved.model_dump(mode="json")


@router.post(
    "/{profile_name}/voice/preview",
    summary="Synthesize a short sample of the profile voice",
)
async def preview_profile_voice(
    profile_name: str = Path(
        min_length=1,
        description="Profile name (e.g. software_developer)",
    ),
    session: CODXJuniorSession = Depends(get_current_session),
) -> JSONResponse:
    """
    Synthesize a short sample of the resolved voice.

    M0 ships no TTS engine: the request is answered with **503** and
    ``{"reason": "no tts provider", "voice": ...}`` instead of fake audio. A
    profile whose voice is disabled is **409** (nothing to speak).
    """
    status_code, body = voice_preview(session, profile_name)
    return JSONResponse(status_code=status_code, content=body)
