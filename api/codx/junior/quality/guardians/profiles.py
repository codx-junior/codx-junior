"""Guardian profiles: create them automatically, list them, polish them.

A guardian **is a profile**. That is the core design decision of this
subsystem: the same objects that power chats, the team view and profile
learning are the ones that guard the files. There is no second registry to keep
in sync, no separate prompt store and no per-feature permission model.

This module is the bridge between the catalog templates and the
:class:`~codx.junior.profiles.profile_manager.ProfileManager`:

* :func:`bootstrap` detects the stack and creates the missing guardians;
* an auto-generated guardian that the user has **not** polished is refreshed
  when the catalog/signature changes; a polished one is never overwritten;
* :func:`enable_guardian` adopts an existing ``file_match`` profile as a
  guardian without changing its category or content;
* :func:`guardians_for_files` is the single matcher used by the runner (and the
  Validator) to answer "who owns this file?".

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from codx.junior.model.profile import (
    Profile,
    ProfileApiSettings,
    ProfileGuardianCheck,
    ProfileGuardianSettings,
)
from codx.junior.profiles.profile_manager import ProfileManager
from codx.junior.utils.file_lock import interprocess_lock

from .catalog import GuardianTemplate, ProjectSignals, build_templates, scan_project
from .checks import BUILTIN_CHECKS
from .models import normalize_severity, slugify, utc_now
from .policy import GuardianPolicy, matches_any, quality_path, resolve_policy

logger = logging.getLogger(__name__)

#: Category used by auto-created guardians.
GUARDIAN_CATEGORY = "guardian"

#: Categories that are treated as guardians even without a ``guardian`` block
#: (compatibility with the pre-existing validator profiles).
GUARDIAN_CATEGORIES = {GUARDIAN_CATEGORY, "agent-validator", "validator"}

#: Triggers a guardian may declare.
VALID_TRIGGERS = ("change", "staged", "commit", "manual")

#: Read-only tools granted to a guardian: enough to inspect the files it is
#: talking about, plus the quality tools so a user can chat with it.
GUARDIAN_TOOLS = [
    "project_read_file",
    "project_grep",
    "project_glob",
    "project_search",
    "project_changes",
    "project_structure",
    "read_folder",
    "profile_learning",
    "profile_knowledge_search",
    "quality_review",
    "quality_guardians",
]

#: Maximum edited content accepted from a tool/API call (profile prompts are
#: injected into every review; a huge one is a cost bug, not a feature).
MAX_GUARDIAN_CONTENT_CHARS = 40_000

#: Maximum checks one guardian may declare.
MAX_GUARDIAN_CHECKS = 30


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _manager(settings: Any) -> ProfileManager:
    """Return a ProfileManager for the project in context."""
    return ProfileManager(settings=settings)


def _content_hash(content: str) -> str:
    """Return the stable hash of guardian Markdown."""
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _profile_hash(
    *,
    description: str,
    file_match: str,
    content: str,
    tags: Iterable[str],
    checks: Iterable[ProfileGuardianCheck],
) -> str:
    """Return a stable hash of the generated fields of a guardian.

    It covers every field an automatic refresh rewrites. When the current
    profile hash differs from the generated one, the user edited the guardian
    and the catalog must not overwrite it.
    """
    payload = {
        "description": str(description or ""),
        "file_match": str(file_match or ""),
        "content_hash": _content_hash(content),
        "tags": sorted(str(tag) for tag in (tags or [])),
        "checks": [
            {
                "id": check.id,
                "title": check.title,
                "kind": check.kind,
                "builtin": check.builtin,
                "command": list(check.command or []),
                "fix_command": list(check.fix_command or []),
                "cwd": check.cwd,
                "attach_paths": bool(check.attach_paths),
                "timeout_seconds": check.timeout_seconds,
                "severity": check.severity,
                "enabled": bool(check.enabled),
            }
            for check in (checks or [])
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _current_profile_hash(profile: Profile) -> str:
    """Hash the current generated fields of a guardian profile."""
    block = profile.guardian or ProfileGuardianSettings()
    return _profile_hash(
        description=profile.description or "",
        file_match=profile.file_match or "",
        content=profile.content or "",
        tags=profile.tags or [],
        checks=block.checks or [],
    )


def _is_auto_generated(profile: Profile) -> bool:
    """Return True when the profile was created by the catalog."""
    return bool(profile and profile.guardian and profile.guardian.auto_generated)


def _is_disabled(profile: Profile) -> bool:
    """Return True when the guardian is explicitly disabled."""
    return bool(profile.guardian and not profile.guardian.enabled)


def guardian_settings(profile: Profile) -> Optional[ProfileGuardianSettings]:
    """Return the guardian block of *profile*, or a compatibility default.

    A profile with the ``guardian`` category and a ``file_match`` is a guardian
    even when it predates the block (the old ``agent-validator`` profiles).
    The default is deliberately not written to disk: :func:`enable_guardian`
    does that when the user opts in.

    Args:
        profile: Profile to inspect.

    Returns:
        Optional[ProfileGuardianSettings]: The settings, or ``None`` when the
        profile is not a guardian.
    """
    if not profile:
        return None
    if profile.guardian is not None:
        return profile.guardian
    category = str(profile.category or "").strip().lower()
    if category in GUARDIAN_CATEGORIES and str(profile.file_match or "").strip():
        return ProfileGuardianSettings(
            enabled=True,
            kind="custom",
            triggers=["staged", "manual"],
        )
    return None


def is_guardian(profile: Profile, *, include_disabled: bool = False) -> bool:
    """Return True when *profile* is a guardian.

    Args:
        profile: Profile to inspect.
        include_disabled: Count disabled guardians too.
    """
    settings = guardian_settings(profile)
    if settings is None:
        return False
    if not include_disabled and not settings.enabled:
        return False
    return bool(str(profile.file_match or "").strip())


def _check_summary(check: ProfileGuardianCheck) -> Dict[str, Any]:
    """Return the compact check entry exposed by the API/UI."""
    return {
        "id": check.id,
        "title": check.title or check.id,
        "kind": check.kind,
        "builtin": check.builtin,
        "command": list(check.command or []),
        "fix_command": list(check.fix_command or []),
        "cwd": check.cwd,
        "attach_paths": bool(check.attach_paths),
        "timeout_seconds": check.timeout_seconds,
        "severity": check.severity,
        "enabled": bool(check.enabled),
    }


def guardian_summary(profile: Profile, *, policy: Optional[GuardianPolicy] = None) -> Dict[str, Any]:
    """Return the guardian entry the Quality panel and tools consume.

    Args:
        profile: Guardian profile.
        policy: Effective policy (reports whether the policy disables it).

    Returns:
        Dict: Name, file scope, triggers, checks, provenance and status.
    """
    settings = guardian_settings(profile) or ProfileGuardianSettings()
    policy = policy or GuardianPolicy()
    return {
        "name": profile.name,
        "description": profile.description or "",
        "category": profile.category or "",
        "file_match": profile.file_match or "",
        "enabled": bool(settings.enabled) and policy.is_guardian_enabled(profile.name),
        "declared_enabled": bool(settings.enabled),
        "disabled_by_policy": not policy.is_guardian_enabled(profile.name),
        "kind": settings.kind,
        "languages": list(settings.languages or []),
        "triggers": list(settings.triggers or []),
        "checks": [_check_summary(check) for check in (settings.checks or [])],
        "check_count": len(settings.checks or []),
        "severity": settings.severity,
        "blocking_severity": settings.blocking_severity,
        "auto_fix": bool(settings.auto_fix),
        "auto_generated": bool(settings.auto_generated),
        "generator": settings.generator,
        "polished": bool(settings.polished),
        "signature": settings.signature,
        "generated_at": settings.generated_at,
        "ignore_paths": list(settings.ignore_paths or []),
        "tags": list(profile.tags or []),
        "tools": list(profile.tools or []),
        "llm_model": profile.llm_model or "",
        "content_chars": len(profile.content or ""),
    }


def _find_profile(profiles: Iterable[Profile], name: str) -> Optional[Profile]:
    """Return the profile named *name* (case sensitive, like the registry)."""
    wanted = str(name or "").strip().lstrip("@")
    for profile in profiles:
        if profile and profile.name == wanted:
            return profile
    return None


# -----------------------------------------------------------------------------
# Listing
# -----------------------------------------------------------------------------
def list_guardians(
    settings: Any,
    *,
    include_disabled: bool = True,
    include_candidates: bool = False,
) -> Dict[str, Any]:
    """List the guardians and the candidates of a project.

    Args:
        settings: Project settings.
        include_disabled: Include guardians disabled by their profile/policy.
        include_candidates: Include existing ``file_match`` profiles that are
            not guardians yet (the "adopt" list).

    Returns:
        Dict: ``guardians``, ``candidates``, ``policy`` and ``signals`` (only
        when the project has no guardians yet).
    """
    manager = _manager(settings)
    policy = resolve_policy(settings)
    profiles = manager.list_all_profiles()

    guardians: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for profile in profiles:
        if not profile or not str(profile.file_match or "").strip():
            continue
        settings_block = guardian_settings(profile)
        if settings_block is not None:
            if include_disabled or settings_block.enabled:
                guardians.append(guardian_summary(profile, policy=policy))
            continue
        if include_candidates and settings_block is None:
            candidates.append(
                {
                    "name": profile.name,
                    "description": profile.description or "",
                    "category": profile.category or "",
                    "file_match": profile.file_match,
                    "content_chars": len(profile.content or ""),
                    "enabled": False,
                }
            )

    guardians.sort(key=lambda item: (item.get("kind") or "", item.get("name") or ""))
    candidates.sort(key=lambda item: item.get("name") or "")
    result = {
        "guardians": guardians,
        "guardian_count": len(guardians),
        "candidates": candidates,
        "candidate_count": len(candidates),
        "policy": policy.to_dict(),
        "category": GUARDIAN_CATEGORY,
    }
    return result


def has_guardians(settings: Any, *, trigger: Optional[str] = None) -> bool:
    """Return True when the project has a guardian (optionally for *trigger*).

    Args:
        settings: Project settings.
        trigger: When given, only count guardians that declare this trigger.
    """
    try:
        manager = _manager(settings)
        for profile in manager.list_all_profiles():
            if not is_guardian(profile, include_disabled=False):
                continue
            if trigger is None or trigger_allowed(profile, trigger):
                return True
        return False
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("Cannot inspect guardian profiles: %s", ex)
        return False


# -----------------------------------------------------------------------------
# Creation and refresh
# -----------------------------------------------------------------------------
def _guardian_avatar(name: str) -> str:
    """Return a deterministic avatar URL for a guardian."""
    return f"https://api.dicebear.com/10.x/glyphs/svg?seed={name}"


def _template_profile(
    template: GuardianTemplate,
    settings: Any,
    signals: ProjectSignals,
) -> Profile:
    """Build the :class:`Profile` of a catalog template."""
    checks = [ProfileGuardianCheck(**check) for check in template.checks]
    guardian = ProfileGuardianSettings(
        enabled=True,
        kind=template.kind,
        languages=list(template.languages or []),
        triggers=["change", "staged", "manual"],
        checks=checks,
        severity="medium",
        blocking_severity="high",
        auto_fix=False,
        ignore_paths=list(template.ignore_paths or []),
        auto_generated=True,
        generator=template.generator or template.kind,
        signature=signals.signature(),
        generated_at=utc_now(),
        generated_content_hash=_content_hash(template.content),
        polished=False,
        version=1,
    )
    profile = Profile(
        name=template.name,
        description=template.description,
        category=GUARDIAN_CATEGORY,
        file_match=template.file_match,
        content=template.content,
        tags=list(template.tags or []),
        tools=list(GUARDIAN_TOOLS),
        use_knowledge=False,
        llm_model="",
        avatar=_guardian_avatar(template.name),
        api_settings=ProfileApiSettings(),
        guardian=guardian,
        project_id=getattr(settings, "project_id", None),
        # Do not persist the default CodxUser() (it carries wallet/session
        # fields that have nothing to do with a generated guardian).
        user=None,
    )
    guardian.generated_profile_hash = _profile_hash(
        description=profile.description,
        file_match=profile.file_match,
        content=profile.content or "",
        tags=profile.tags,
        checks=guardian.checks,
    )
    return profile


def _profile_diverged(profile: Profile) -> bool:
    """Return True when the current profile no longer matches its generation.

    New guardians store ``generated_profile_hash``. Profiles created by an older
    version fall back to the Markdown hash, which is enough to detect the most
    common polish (editing the instructions).
    """
    block = profile.guardian
    if not block:
        return False
    if block.generated_profile_hash:
        return _current_profile_hash(profile) != block.generated_profile_hash
    if block.generated_content_hash:
        return _content_hash(profile.content or "") != block.generated_content_hash
    return False


def _needs_refresh(profile: Profile, template: GuardianTemplate, signals: ProjectSignals) -> bool:
    """Return True when an auto-generated guardian should be regenerated.

    A guardian the user polished (content/file_match/checks changed, or
    ``polished=True``) is never refreshed automatically: the catalog is a
    starting point, not the owner of the profile.
    """
    if not profile.guardian or not profile.guardian.auto_generated:
        return False
    if profile.guardian.polished:
        return False
    if _profile_diverged(profile):
        return False
    if profile.guardian.signature != signals.signature():
        return True
    return _content_hash(profile.content or "") != _content_hash(template.content)


def _refresh_profile(profile: Profile, template: GuardianTemplate, signals: ProjectSignals) -> None:
    """Apply a catalog template to an existing auto-generated guardian."""
    profile.description = template.description
    profile.file_match = template.file_match
    profile.content = template.content
    profile.tags = list(template.tags or [])
    profile.guardian.kind = template.kind
    profile.guardian.languages = list(template.languages or [])
    profile.guardian.checks = [ProfileGuardianCheck(**check) for check in template.checks]
    profile.guardian.ignore_paths = list(template.ignore_paths or [])
    profile.guardian.generator = template.generator or template.kind
    profile.guardian.signature = signals.signature()
    profile.guardian.generated_at = utc_now()
    profile.guardian.generated_content_hash = _content_hash(template.content)
    profile.guardian.generated_profile_hash = _profile_hash(
        description=profile.description,
        file_match=profile.file_match,
        content=profile.content or "",
        tags=profile.tags,
        checks=profile.guardian.checks,
    )


def bootstrap(
    settings: Any,
    *,
    force: bool = False,
    adopt: bool = False,
) -> Dict[str, Any]:
    """Create the guardians the detected stack needs (serialised).

    The API process (Quality panel) and the background worker (file-change
    trigger) may both discover that a project has no guardians at the same
    time. The inter-process lock makes the bootstrap single-writer, so the two
    cannot write the same profile files concurrently.
    """
    lock_path = quality_path(settings, ".bootstrap.lock")
    with interprocess_lock(lock_path, timeout=120):
        return _bootstrap_impl(settings, force=force, adopt=adopt)


def _bootstrap_impl(
    settings: Any,
    *,
    force: bool = False,
    adopt: bool = False,
) -> Dict[str, Any]:
    """Implementation of :func:`bootstrap` (must be called under the lock).

    The operation is idempotent:

    * missing guardians are created from the catalog;
    * an auto-generated guardian whose signature/content changed is refreshed
      **only** when the user has not polished it;
    * user-created profiles are never touched;
    * ``adopt`` converts existing ``file_match`` profiles into guardians by
      enabling their block (content untouched).

    Args:
        settings: Project settings.
        force: Re-scan and refresh auto-generated guardians even when the
            signature looks unchanged.
        adopt: Adopt existing file_match profiles as guardians.

    Returns:
        Dict: ``created``, ``updated``, ``kept``, ``adopted``, ``signals``,
        ``templates`` and ``guardians``.
    """
    manager = _manager(settings)
    existing_profiles = manager.list_all_profiles()
    existing = {profile.name: profile for profile in existing_profiles if profile}

    signals = scan_project(settings)
    templates = build_templates(settings, signals)

    created: List[str] = []
    updated: List[str] = []
    kept: List[str] = []
    adopted: List[str] = []
    errors: List[str] = []

    for template in templates:
        current = existing.get(template.name)
        if current is None:
            try:
                profile = _template_profile(template, settings, signals)
                manager.save_profile(profile)
                created.append(template.name)
            except Exception as ex:
                logger.exception("Could not create guardian %s: %s", template.name, ex)
                errors.append(f"{template.name}: {ex}")
            continue

        if not current.guardian or not current.guardian.auto_generated:
            kept.append(template.name)
            continue
        try:
            if not current.guardian.polished and _profile_diverged(current):
                # The user edited the generated fields without flipping the flag.
                # Persist the fact so a later bootstrap cannot overwrite them.
                current.guardian.polished = True
                manager.save_profile(current)
                kept.append(template.name)
                continue
            if force or _needs_refresh(current, template, signals):
                if current.guardian.polished:
                    kept.append(template.name)
                    continue
                _refresh_profile(current, template, signals)
                manager.save_profile(current)
                updated.append(template.name)
            else:
                kept.append(template.name)
        except Exception as ex:
            logger.exception("Could not refresh guardian %s: %s", template.name, ex)
            errors.append(f"{template.name}: {ex}")

    if adopt:
        for profile in existing_profiles:
            if not profile or not str(profile.file_match or "").strip():
                continue
            if guardian_settings(profile) is not None:
                continue
            try:
                profile.guardian = ProfileGuardianSettings(
                    enabled=True,
                    kind="custom",
                    triggers=["staged", "manual"],
                    checks=[],
                    auto_generated=False,
                    polished=True,
                )
                manager.save_profile(profile)
                adopted.append(profile.name)
            except Exception as ex:
                logger.exception("Could not adopt profile %s as guardian: %s", profile.name, ex)
                errors.append(f"{profile.name}: {ex}")

    # Re-read so the result reflects the saved files (and the composed content).
    listing = list_guardians(settings, include_disabled=True)
    return {
        "created": created,
        "updated": updated,
        "kept": kept,
        "adopted": adopted,
        "errors": errors,
        "signals": signals.to_dict(),
        "signature": signals.signature(),
        "templates": [template.name for template in templates],
        "guardians": listing["guardians"],
        "guardian_count": listing["guardian_count"],
        "candidates": listing["candidates"],
        "candidate_count": listing["candidate_count"],
        "policy": listing["policy"],
    }


# -----------------------------------------------------------------------------
# Edit / adopt / enable / disable
# -----------------------------------------------------------------------------
def _validate_checks(checks: Any) -> List[ProfileGuardianCheck]:
    """Validate and normalize a list of check declarations.

    Args:
        checks: List of dicts or :class:`ProfileGuardianCheck` objects.

    Returns:
        List[ProfileGuardianCheck]: Validated checks.

    Raises:
        ValueError: For an invalid check.
    """
    if checks is None:
        return []
    if not isinstance(checks, list):
        raise ValueError("checks must be a list of check objects")
    if len(checks) > MAX_GUARDIAN_CHECKS:
        raise ValueError(f"a guardian may declare at most {MAX_GUARDIAN_CHECKS} checks")

    validated: List[ProfileGuardianCheck] = []
    seen: set = set()
    for index, raw in enumerate(checks):
        if isinstance(raw, ProfileGuardianCheck):
            check = raw.model_copy(deep=True)
        elif isinstance(raw, dict):
            check = ProfileGuardianCheck(**raw)
        else:
            raise ValueError(f"check #{index} must be an object")
        check.id = str(check.id or "").strip() or f"check-{index + 1}"
        if check.id in seen:
            raise ValueError(f"duplicate check id: {check.id}")
        seen.add(check.id)
        check.kind = str(check.kind or "builtin").strip().lower()
        if check.kind not in ("builtin", "command"):
            raise ValueError(f"{check.id}: kind must be builtin or command")
        if check.kind == "builtin":
            check.builtin = str(check.builtin or check.id).strip()
            if check.builtin not in BUILTIN_CHECKS:
                raise ValueError(
                    f"{check.id}: unknown builtin {check.builtin!r}. "
                    f"Valid values: {', '.join(BUILTIN_CHECKS)}"
                )
            check.command = []
            check.fix_command = []
        else:
            check.command = [str(part) for part in (check.command or []) if str(part).strip()]
            if not check.command:
                raise ValueError(f"{check.id}: a command check needs a non-empty command")
            check.fix_command = [str(part) for part in (check.fix_command or []) if str(part).strip()]
            check.builtin = ""
        check.severity = normalize_severity(check.severity).value
        check.timeout_seconds = max(1, min(int(check.timeout_seconds or 120), 600))
        check.enabled = bool(check.enabled)
        validated.append(check)
    return validated


def _validate_triggers(triggers: Any) -> List[str]:
    """Validate a trigger list (unknown values fail instead of being ignored)."""
    if triggers is None:
        return ["change", "staged", "manual"]
    if isinstance(triggers, str):
        values = [item.strip() for item in triggers.split(",")]
    elif isinstance(triggers, (list, tuple, set)):
        values = [str(item).strip() for item in triggers]
    else:
        raise ValueError("triggers must be a list or a comma separated string")
    values = [value.lower() for value in values if value]
    invalid = [value for value in values if value not in VALID_TRIGGERS]
    if invalid:
        raise ValueError(
            f"Invalid trigger(s) {invalid}. Valid values: {', '.join(VALID_TRIGGERS)}"
        )
    return sorted(set(values)) or ["manual"]


def get_guardian(settings: Any, name: str) -> Optional[Profile]:
    """Read one guardian profile by name."""
    profile = _manager(settings).read_profile(name)
    if profile is None:
        return None
    return profile


def update_guardian(
    settings: Any,
    name: str,
    *,
    description: Optional[str] = None,
    content: Optional[str] = None,
    file_match: Optional[str] = None,
    tags: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    llm_model: Optional[str] = None,
    use_knowledge: Optional[bool] = None,
    enabled: Optional[bool] = None,
    kind: Optional[str] = None,
    triggers: Optional[List[str]] = None,
    checks: Optional[List[Dict[str, Any]]] = None,
    severity: Optional[str] = None,
    blocking_severity: Optional[str] = None,
    auto_fix: Optional[bool] = None,
    ignore_paths: Optional[List[str]] = None,
    polished: bool = True,
) -> Profile:
    """Update a guardian profile (merge semantics).

    Only the provided fields are replaced, so a tool call that changes the
    triggers never wipes the checks or the learned rules (the rules live in
    ``enrich.md`` and are not touched here at all).

    Args:
        settings: Project settings.
        name: Guardian name.
        description/content/file_match/tags/tools/llm_model/use_knowledge:
            Standard profile fields.
        enabled/kind/triggers/checks/severity/blocking_severity/auto_fix/
            ignore_paths: Guardian block fields.
        polished: Mark the guardian as user-edited so auto-refresh leaves it
            alone. Defaults to True for any explicit edit.

    Returns:
        Profile: The saved profile.

    Raises:
        ValueError: When the profile does not exist or a field is invalid.
    """
    manager = _manager(settings)
    profile = manager.read_profile(name)
    if profile is None:
        raise ValueError(f"Unknown guardian profile: {name}")

    if content is not None:
        content = str(content)
        if len(content) > MAX_GUARDIAN_CONTENT_CHARS:
            raise ValueError(
                f"guardian content is {len(content)} characters; the maximum is "
                f"{MAX_GUARDIAN_CONTENT_CHARS}. Store long material as profile "
                "knowledge instead."
            )
        profile.content = content
    if description is not None:
        profile.description = str(description)
    if file_match is not None:
        pattern = str(file_match)
        try:
            re.compile(pattern)
        except re.error as ex:
            raise ValueError(f"Invalid file_match regex {pattern!r}: {ex}") from ex
        profile.file_match = pattern
    if tags is not None:
        profile.tags = [str(tag) for tag in tags if str(tag).strip()]
    if tools is not None:
        profile.tools = [str(tool) for tool in tools if str(tool).strip()]
    if llm_model is not None:
        profile.llm_model = str(llm_model)
    if use_knowledge is not None:
        profile.use_knowledge = bool(use_knowledge)

    block = profile.guardian or ProfileGuardianSettings(
        enabled=True,
        kind="custom",
        triggers=["staged", "manual"],
        auto_generated=False,
    )
    if enabled is not None:
        block.enabled = bool(enabled)
    if kind is not None:
        block.kind = str(kind).strip().lower()
    if triggers is not None:
        block.triggers = _validate_triggers(triggers)
    if checks is not None:
        block.checks = _validate_checks(checks)
    if severity is not None:
        block.severity = normalize_severity(severity).value
    if blocking_severity is not None:
        block.blocking_severity = normalize_severity(blocking_severity).value
    if auto_fix is not None:
        block.auto_fix = bool(auto_fix)
    if ignore_paths is not None:
        block.ignore_paths = [str(pattern) for pattern in ignore_paths if str(pattern).strip()]
    if polished:
        block.polished = True
    block.version = block.version or 1
    profile.guardian = block

    # A guardian must have a file scope; an empty pattern would collect every
    # file in the project, which is never what "polish this guardian" means.
    if not str(profile.file_match or "").strip():
        raise ValueError("A guardian profile needs a file_match pattern")

    manager.save_profile(profile)
    return manager.read_profile(name)


def enable_guardian(settings: Any, name: str, enabled: bool = True) -> Profile:
    """Enable/disable a guardian without touching its content or checks."""
    manager = _manager(settings)
    profile = manager.read_profile(name)
    if profile is None:
        raise ValueError(f"Unknown guardian profile: {name}")
    if not str(profile.file_match or "").strip():
        raise ValueError(
            f"Profile '{name}' has no file_match, so it cannot be a guardian. "
            "Set its file scope first."
        )
    if profile.guardian is None:
        profile.guardian = ProfileGuardianSettings(
            enabled=bool(enabled),
            kind="custom",
            triggers=["staged", "manual"],
            checks=[],
            auto_generated=False,
            polished=True,
        )
    else:
        profile.guardian.enabled = bool(enabled)
        if profile.category == GUARDIAN_CATEGORY or profile.guardian.auto_generated:
            profile.guardian.polished = True
    manager.save_profile(profile)
    return manager.read_profile(name)


def adopt_profile(settings: Any, name: str, *, enabled: bool = True) -> Profile:
    """Adopt an existing ``file_match`` profile as a quality guardian.

    The profile keeps its category and content; it only gains a guardian block
    with conservative triggers (staged + manual). The user can then add checks
    or run it manually before turning it on for every change.

    Args:
        settings: Project settings.
        name: Existing profile name.
        enabled: Initial enabled state.

    Returns:
        Profile: The saved guardian.

    Raises:
        ValueError: When the profile does not exist or has no file_match.
    """
    manager = _manager(settings)
    profile = manager.read_profile(name)
    if profile is None:
        raise ValueError(f"Unknown profile: {name}")
    if not str(profile.file_match or "").strip():
        raise ValueError(
            f"Profile '{name}' has no file_match. Set a file scope before "
            "adopting it as a guardian."
        )
    if profile.guardian is None:
        profile.guardian = ProfileGuardianSettings(
            enabled=bool(enabled),
            kind="custom",
            triggers=["staged", "manual"],
            checks=[],
            auto_generated=False,
            polished=True,
        )
    else:
        profile.guardian.enabled = bool(enabled)
        profile.guardian.polished = True
    manager.save_profile(profile)
    return manager.read_profile(name)


# -----------------------------------------------------------------------------
# Matching
# -----------------------------------------------------------------------------
def _profile_regex(profile: Profile) -> Optional[re.Pattern]:
    """Compile a profile's file_match, returning None for an invalid pattern."""
    pattern = str(profile.file_match or "")
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error:
        logger.warning("Invalid file_match in profile '%s': %s", profile.name, pattern)
        return None


def guardians_for_files(
    settings: Any,
    paths: Iterable[str],
    *,
    profiles: Optional[List[Profile]] = None,
    policy: Optional[GuardianPolicy] = None,
    include_disabled: bool = False,
) -> Dict[str, List[Profile]]:
    """Map every path to the enabled guardians that own it.

    Matching is done against the profile ``file_match`` regex with the absolute
    path (the same semantics as :meth:`ProfileManager.is_profile_match`), then
    the guardian policy and the profile's own ignores are applied.

    Args:
        settings: Project settings.
        paths: Project-relative or absolute paths.
        profiles: Pre-loaded profiles (avoids a registry scan per path).
        policy: Effective policy (resolved when omitted).
        include_disabled: Include disabled guardians in the result.

    Returns:
        Dict[str, List[Profile]]: Path -> owning guardian profiles.
    """
    policy = policy or resolve_policy(settings)
    if not policy.enabled:
        return {}
    if profiles is None:
        profiles = _manager(settings).list_all_profiles()

    guardians = [
        profile for profile in profiles
        if is_guardian(profile, include_disabled=include_disabled)
        and policy.is_guardian_enabled(profile.name)
    ]
    if not guardians:
        return {}

    compiled: List[Tuple[Profile, re.Pattern]] = []
    for profile in guardians:
        regex = _profile_regex(profile)
        if regex is not None:
            compiled.append((profile, regex))

    root = os.path.abspath(
        getattr(settings, "abs_project_path", None)
        or getattr(settings, "project_path", None)
        or os.getcwd()
    )
    result: Dict[str, List[Profile]] = {}
    for path in paths:
        text = str(path or "").replace("\\", "/")
        absolute = text if os.path.isabs(text) else os.path.join(root, text)
        relative = os.path.relpath(absolute, root).replace(os.sep, "/") if os.path.isabs(absolute) else text.lstrip("./")
        if relative == ".." or relative.startswith("../"):
            continue
        owned: List[Profile] = []
        for profile, regex in compiled:
            settings_block = guardian_settings(profile) or ProfileGuardianSettings()
            if matches_any(relative, list(settings_block.ignore_paths or [])):
                continue
            if regex.search(absolute):
                owned.append(profile)
        if owned:
            result[relative] = owned
    return result


def guardian_for_name(profiles: Iterable[Profile], name: str) -> Optional[Profile]:
    """Return the guardian profile named *name*."""
    profile = _find_profile(profiles, name)
    if profile and is_guardian(profile, include_disabled=True):
        return profile
    return None


def trigger_allowed(profile: Profile, trigger: str) -> bool:
    """Return True when the guardian declares *trigger*."""
    block = guardian_settings(profile)
    if block is None or not block.enabled:
        return False
    triggers = [str(item).strip().lower() for item in (block.triggers or [])]
    return str(trigger or "manual").strip().lower() in triggers


# -----------------------------------------------------------------------------
# Learning
# -----------------------------------------------------------------------------
def remember(
    settings: Any,
    guardian_name: str,
    title: str,
    content: str,
) -> Dict[str, Any]:
    """Teach one guardian a durable project rule (``enrich.md``).

    The rule reaches every AI review of that guardian. Permissions are checked
    by the caller (the tool/API layer), matching the profile-learning contract.

    Args:
        settings: Project settings.
        guardian_name: Guardian profile name.
        title: Rule title (upsert key).
        content: Markdown instruction.

    Returns:
        Dict: ``path``, ``title``, ``created`` and ``rules`` from the manager.

    Raises:
        ValueError: When the profile is not a guardian or the rule is invalid.
    """
    profile = _manager(settings).read_profile(guardian_name)
    if profile is None or not is_guardian(profile, include_disabled=True):
        raise ValueError(f"Unknown guardian profile: {guardian_name}")
    title = " ".join(str(title or "").split()).strip() or f"Guardian rule {slugify(content, 30)}"
    return _manager(settings).upsert_profile_rule(
        profile_name=guardian_name,
        title=title,
        text=content,
    )


# Made with ❤️ by codx-junior
