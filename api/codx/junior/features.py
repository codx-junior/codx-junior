"""
Install-time feature profile.

The application image always contains every feature. What a given installation
*exposes* is chosen at install time by the installer and published to the
container as ``CODX_JUNIOR_ACTIVE_FEATURES`` (see
``docs/product/v0.0.1-feature-matrix.md`` and
``codx-junior-installer/install.sh``).

Supported values
----------------
``all``
    Every feature (development and CI). An **unset** variable also means
    ``all``, so an instance created before the profile existed keeps today's
    product.
``core``
    The 0.0.1 public release: the project workspace loop only. The company
    surfaces (and therefore the first-run company wizard) are hidden.
``<id>[,<id>...]``
    An explicit subset. ``core``/``all`` may be mixed in
    (``core,mcp`` expands the core ids plus MCP).

The manifest lives next to this module (``features.json``), copied from the
public contract at build time. Unknown ids never enable a feature: a typo is
ignored and logged instead of silently opening a hidden surface.
"""

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

FEATURES_ENV = "CODX_JUNIOR_ACTIVE_FEATURES"
PROFILE_ALL = "all"
PROFILE_CORE = "core"
PROFILE_CUSTOM = "custom"

_MANIFEST_PATH = Path(__file__).with_name("features.json")
_MANIFEST_LOCK = threading.RLock()
_MANIFEST: Dict[str, Any] = {}
_MANIFEST_HASH = ""


def _load_manifest() -> Dict[str, Any]:
    """Load and cache the feature manifest (never raises after the first call)."""
    global _MANIFEST, _MANIFEST_HASH
    with _MANIFEST_LOCK:
        if _MANIFEST:
            return _MANIFEST
        try:
            raw = _MANIFEST_PATH.read_bytes()
            _MANIFEST_HASH = hashlib.sha256(raw).hexdigest()
            _MANIFEST = json.loads(raw.decode("utf-8"))
        except Exception as ex:  # a missing manifest must not take the API down
            logger.error("Features: cannot read %s: %s", _MANIFEST_PATH, ex)
            _MANIFEST = {"version": "0.0.1", "profiles": {"core": [], "all": "*"}, "features": []}
            _MANIFEST_HASH = ""
        return _MANIFEST


def manifest() -> Dict[str, Any]:
    """Return a copy of the manifest (safe for callers to mutate)."""
    return json.loads(json.dumps(_load_manifest()))


def _feature_ids(manifest_data: Dict[str, Any]) -> List[str]:
    """Every known feature id, in manifest order."""
    return [
        str(item.get("id"))
        for item in manifest_data.get("features", [])
        if item.get("id")
    ]


def _core_ids(manifest_data: Dict[str, Any]) -> List[str]:
    """The ids of the ``core`` profile, in manifest order."""
    core = (manifest_data.get("profiles") or {}).get(PROFILE_CORE) or []
    return [str(item) for item in core if item]


def _unique(values: Iterable[str]) -> List[str]:
    """Deduplicate while keeping the first occurrence."""
    seen = set()
    result = []
    for value in values:
        key = str(value)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def resolve_profile(value: Any = None) -> Tuple[str, List[str]]:
    """
    Resolve a raw ``CODX_JUNIOR_ACTIVE_FEATURES`` value.

    Args:
        value: Raw profile string. When ``None`` the environment is read.

    Returns:
        ``(profile, active_ids)`` where ``profile`` is ``all``, ``core`` or
        ``custom``. Unknown ids are ignored.
    """
    data = _load_manifest()
    all_ids = _feature_ids(data)
    core_ids = _core_ids(data)

    raw = os.environ.get(FEATURES_ENV, "") if value is None else value
    raw = str(raw or "").strip()
    if not raw:
        return PROFILE_ALL, list(all_ids)

    lowered = raw.lower()
    if lowered == PROFILE_ALL:
        return PROFILE_ALL, list(all_ids)
    if lowered == PROFILE_CORE:
        return PROFILE_CORE, _unique(core_ids)

    requested = [item.strip().lower() for item in lowered.split(",") if item.strip()]
    known = set(all_ids)
    resolved: List[str] = []
    for item in requested:
        if item == PROFILE_ALL:
            resolved.extend(all_ids)
        elif item == PROFILE_CORE:
            resolved.extend(core_ids)
        elif item in known:
            resolved.append(item)
        else:
            logger.warning(
                "Features: ignoring unknown id %r in %s=%s",
                item,
                FEATURES_ENV,
                raw,
            )
    return PROFILE_CUSTOM, _unique(resolved)


def active_features() -> List[str]:
    """Ids exposed by this installation."""
    return resolve_profile()[1]


def profile_name() -> str:
    """The resolved profile name (``all``, ``core`` or ``custom``)."""
    return resolve_profile()[0]


def is_active(feature_id: str) -> bool:
    """Whether *feature_id* is exposed by this installation."""
    return str(feature_id or "").strip().lower() in set(active_features())


def company_enabled() -> bool:
    """
    Whether the company shell (and its first-run wizard) is exposed.

    The company OS is a set of ``company-*`` features; the 0.0.1 ``core``
    profile contains none of them, so a default install starts in the project
    workspace. ``all`` and any custom set that opts into a company feature get
    the company-first shell.
    """
    return any(feature.startswith("company") for feature in active_features())


def company_wizard_enabled() -> bool:
    """The company wizard is only meaningful when the company shell is on."""
    return company_enabled() and is_active("onboarding")


def hidden_features() -> List[str]:
    """Every known feature that this installation does not expose."""
    active = set(active_features())
    return [feature for feature in _feature_ids(_load_manifest()) if feature not in active]


def manifest_hash() -> str:
    """Stable hash of the manifest the runtime resolved against."""
    _load_manifest()
    return _MANIFEST_HASH


def describe() -> Dict[str, Any]:
    """Machine-readable payload for ``GET /api/features``."""
    data = _load_manifest()
    profile, active = resolve_profile()
    active_set = set(active)
    company = any(feature.startswith("company") for feature in active)
    return {
        "version": str(data.get("version") or "0.0.1"),
        "profile": profile,
        "active": active,
        "hidden": [feature for feature in _feature_ids(data) if feature not in active_set],
        "company": company,
        "company_wizard": company and "onboarding" in active_set,
        "manifest_hash": manifest_hash(),
    }
