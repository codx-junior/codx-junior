"""
Business operations for companies.

The manager is the only place that mutates a :class:`~.models.Company`: the API,
the profile tools and the autopilot all call the same methods, so validation,
journaling and dispatch behave identically no matter who acts.

What it owns
------------
* **Validation** — names, references (users/profiles/projects), org tree cycles,
  team rosters, mission destinations and status transitions.
* **Mission lifecycle** — create, dispatch to the project orchestration runtime,
  sync the run back, accept/reject.
* **Autonomy** — the guard-railed cycle that dispatches at most N ``auto``
  missions per interval, never more than the concurrency limit.
* **Dashboard** — :meth:`snapshot` returns the exact payload the UI renders.
* **Journal** — every mutation appends a human-readable event.

Dependency injection
--------------------
The AI runtime is heavy and optional. The manager therefore accepts providers:

* ``projects_provider`` / ``users_provider`` / ``profiles_provider`` for the
  reference catalog (tests use fakes),
* ``project_settings_provider`` for the real ``CODXJuniorSettings`` used by
  dispatch,
* ``dispatcher`` to start the work (default: durable orchestration),
* ``run_status_provider`` to read a run back,
* ``clock`` for deterministic cycles.

Nothing in this module imports the AI stack at import time.

Made with ❤️ by codx-junior
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import os
import re

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .models import (
    MISSION_PRIORITIES,
    MISSION_TRANSITIONS,
    AutonomyPolicy,
    Company,
    CompanyChat,
    CompanyEvent,
    CompanyMember,
    CompanyProject,
    CompanySpace,
    CompanySpaceFolder,
    CompanyTeam,
    Mission,
    OrgUnit,
    SpendEntry,
    can_transition,
    new_id,
    now_iso,
    parse_iso,
    slugify,
)
from .spaces import (
    cleanup_space_files,
    create_folder as space_create_folder,
    delete_entry as space_delete_entry,
    ensure_space_folder,
    list_path as space_list_path,
    resolve_entry as space_resolve_entry,
    save_upload as space_save_upload,
    space_stats,
    validate_mapped_folder,
    write_text as space_write_text,
)
from .store import CompanyNotFound, CompanyStore, CompanyStoreError
from .templates import apply_template

logger = logging.getLogger(__name__)

#: Fields a generic company update may touch. Org/teams/projects/missions and
#: autonomy have dedicated methods so a broad PUT cannot delete work.
COMPANY_EDITABLE_FIELDS = (
    "name",
    "slug",
    "description",
    "mission",
    "vision",
    "values",
    "website",
    "logo",
    "color",
    "status",
    "owner",
    "tags",
    "business",
    "all_projects",
)

TEAM_EDITABLE_FIELDS = (
    "name",
    "description",
    "purpose",
    "org_unit_id",
    "lead_kind",
    "lead_name",
    "members",
    "project_ids",
    "autonomy",
    "tools",
    "tags",
    "budget_cxjcoins",
    "requires_approval",
    "color",
    "icon",
)

MISSION_EDITABLE_FIELDS = (
    "title",
    "objective",
    "acceptance",
    "priority",
    "team_id",
    "project_id",
    "owner_kind",
    "owner_name",
    "dispatch_mode",
    "requires_approval",
    "budget_cxjcoins",
    "due_date",
    "tags",
)

AUTONOMY_EDITABLE_FIELDS = (
    "enabled",
    "level",
    "interval_minutes",
    "max_concurrent_missions",
    "max_missions_per_cycle",
    "daily_budget_cxjcoins",
    "require_human_acceptance",
)

#: Name of the company created automatically on a fresh installation.
#: Deployments can override it with ``CODX_JUNIOR_DEFAULT_COMPANY_NAME``.
DEFAULT_COMPANY_NAME = "codx-junior"

#: Slug of the default company. It is stable so a deep link can address it
#: before the user has had any chance to rename it.
DEFAULT_COMPANY_SLUG = "codx-junior"

#: First line of the description of the auto-provisioned default company. It is
#: how the wizard and the migration recognise a placeholder the owner has not
#: configured yet (the renamed company keeps it until the wizard writes over it).
AUTO_DEFAULT_DESCRIPTION = (
    "Default company created automatically. It links every "
    "codx-junior project and can be renamed or split into more "
    "companies at any time."
)

#: Names the auto-provisioned default company used in earlier releases. An
#: existing placeholder with one of these names is renamed to the current
#: default; a company the owner named/customised is never touched.
LEGACY_DEFAULT_COMPANY_NAMES = ("acme",)

#: Environment variable that overrides the default company name.
DEFAULT_COMPANY_NAME_ENV = "CODX_JUNIOR_DEFAULT_COMPANY_NAME"

#: Order used when picking the next mission to dispatch.
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: Maximum SpendEntry rows kept in the company document. The cumulative total
#: lives on Company.spent_cxjcoins, so trimming old deltas never loses the
#: overall number; it only limits how far back the daily ledger can look.
SPEND_LEDGER_LIMIT = 2000

#: Maximum size of the company logo stored in the document. The Settings picker
#: downscales the file first, so this is a guard against a huge payload, not a
#: limit the UI ever reaches.
MAX_LOGO_LENGTH = 1024 * 1024

#: Maximum size of an uploaded logo file. The Settings picker downscales the
#: image before uploading, so this is a guard against a huge payload, not a limit
#: the UI ever reaches.
MAX_LOGO_FILE_BYTES = 2 * 1024 * 1024

#: Image types accepted as an inline company logo (legacy documents keep the
#: image inside ``company.json``; new uploads are stored as a file).
LOGO_MEDIA_TYPES = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/bmp",
    "image/x-icon",
)

#: Magic bytes of the image types accepted as a logo file, with the extension it
#: is stored with. The payload is sniffed instead of trusting the client content
#: type: that is what stops an arbitrary file from being written with an image
#: extension and then served back to a browser.
LOGO_FILE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
    (b"BM", ".bmp", "image/bmp"),
)

#: Path of the endpoint that serves a company logo. The document stores this
#: short URL instead of the image, so every read/write of ``company.json`` stays
#: small; the optional ``?v=`` query is the cache buster of
#: :meth:`CompanyManager.logo_url`.
COMPANY_LOGO_PATH = re.compile(r"^/api/companies/[\w.-]+/logo(\?\S*)?$")

#: Run statuses that mean "still working" (sync keeps the mission running).
_ACTIVE_RUN_STATUSES = ("queued", "running")
_TERMINAL_RUN_STATUSES = (
    "completed",
    "failed",
    "stopped",
    "budget_exhausted",
    "stalled",
)


class CompanyValidationError(ValueError):
    """The requested operation would produce an invalid company."""


class CompanyConflict(CompanyValidationError):
    """The requested operation conflicts with the current state."""


class CompanyDispatchError(RuntimeError):
    """A mission could not be dispatched to its project."""


def detect_logo_type(content: bytes) -> Tuple[str, str]:
    """
    Recognise the image type of a logo payload.

    The bytes are sniffed instead of trusting the content type the client sent:
    a ``.png`` that is really an HTML document would otherwise be stored and
    served back to the browser.

    Args:
        content: Raw file bytes.

    Returns:
        ``(extension, media_type)``; the extension includes the dot.

    Raises:
        CompanyValidationError: When the payload is not a supported image.
    """
    for signature, extension, media_type in LOGO_FILE_SIGNATURES:
        if content.startswith(signature):
            return extension, media_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    # SVG is text: allow an XML prolog, comments or attributes before <svg.
    head = content[:2048].lstrip()
    if head.startswith(b"<?xml") or b"<svg" in head:
        return ".svg", "image/svg+xml"
    raise CompanyValidationError(
        "Unsupported logo file. Use a PNG, JPEG, GIF, WebP, BMP or SVG image"
    )


# ---------------------------------------------------------------------------
# Default providers (lazy: no AI stack at import time)
# ---------------------------------------------------------------------------


def _default_projects() -> List[Dict[str, Any]]:
    """Return the discovered codx-junior projects as plain dictionaries."""
    try:
        from codx.junior.project.project_discover import find_all_projects

        projects = find_all_projects() or {}
    except Exception as ex:  # pragma: no cover - discovery is best effort
        logger.warning("Company: project discovery failed: %s", ex)
        return []
    result: List[Dict[str, Any]] = []
    for project_id, settings in projects.items():
        if settings is None:
            continue
        result.append(
            {
                "project_id": getattr(settings, "project_id", project_id) or project_id,
                "project_name": getattr(settings, "project_name", "") or project_id,
                "project_path": getattr(settings, "abs_project_path", "")
                or getattr(settings, "project_path", ""),
                "codx_path": getattr(settings, "codx_path", ""),
            }
        )
    return sorted(result, key=lambda project: project["project_name"].lower())


def _default_users() -> List[Dict[str, Any]]:
    """Return the codx-junior users as plain dictionaries."""
    try:
        from codx.junior.security.user_management import UserSecurityManager

        users = UserSecurityManager().list_user() or []
        return [
            {
                "username": user.get("username", ""),
                "avatar": user.get("avatar", ""),
                "email": user.get("email", ""),
                "role": user.get("role", ""),
                "companies": user.get("companies", []),
            }
            for user in users
            if user.get("username")
        ]
    except Exception as ex:  # pragma: no cover - auth may be disabled
        logger.debug("Company: user catalog unavailable: %s", ex)
        return []


def _default_project_settings(project_id: str) -> Any:
    """Return the real ``CODXJuniorSettings`` of a project, or ``None``."""
    try:
        from codx.junior.project.project_discover import find_all_projects

        projects = find_all_projects() or {}
        return projects.get(project_id) or next(
            (
                settings
                for settings in projects.values()
                if settings and getattr(settings, "project_id", None) == project_id
            ),
            None,
        )
    except Exception as ex:  # pragma: no cover - defensive
        logger.warning("Company: could not resolve project settings: %s", ex)
        return None


def _default_profiles(project_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Return the profiles visible from the linked projects (union by name)."""
    try:
        from codx.junior.profiles.profile_manager import ProfileManager
        from codx.junior.project.project_discover import find_all_projects

        projects = find_all_projects() or {}
        settings_list = []
        for project_id, settings in projects.items():
            if settings is None:
                continue
            if project_ids and project_id not in project_ids:
                continue
            settings_list.append(settings)
        catalog: Dict[str, Dict[str, Any]] = {}
        for settings in settings_list:
            try:
                manager = ProfileManager(settings=settings)
                for profile in manager.list_all_profiles():
                    if not profile or not profile.name:
                        continue
                    entry = catalog.setdefault(
                        profile.name,
                        {
                            "name": profile.name,
                            "category": getattr(profile, "category", "") or "",
                            "description": getattr(profile, "description", "") or "",
                            "projects": [],
                        },
                    )
                    project_name = getattr(settings, "project_name", "") or ""
                    if project_name and project_name not in entry["projects"]:
                        entry["projects"].append(project_name)
            except Exception as ex:  # pragma: no cover - one bad project is not fatal
                logger.debug("Company: profiles of one project failed: %s", ex)
        return sorted(catalog.values(), key=lambda profile: profile["name"].lower())
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("Company: profile catalog unavailable: %s", ex)
        return []


class CompanyManager:
    """
    CRUD + operating system for companies.

    Args:
        root_path: Companies root (default: ``CODX_JUNIOR_COMPANIES_PATH``).
        store: Optional pre-built :class:`CompanyStore` (tests).
        projects_provider: Callable returning the project catalog.
        users_provider: Callable returning the user catalog.
        profiles_provider: Callable(project_ids) returning the profile catalog.
        project_settings_provider: Callable(project_id) returning settings.
        dispatcher: Callable(company, mission, brief) starting the mission.
        run_status_provider: Callable(project_id, run_id) returning run status.
        chat_manager_provider: Callable(project_id) returning a ChatManager.
        clock: Callable returning ``datetime.now``.
    """

    def __init__(
        self,
        root_path: Optional[str] = None,
        store: Optional[CompanyStore] = None,
        projects_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        users_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        profiles_provider: Optional[
            Callable[[Optional[List[str]]], List[Dict[str, Any]]]
        ] = None,
        project_settings_provider: Optional[Callable[[str], Any]] = None,
        dispatcher: Optional[Callable[[Company, Mission, str], Dict[str, Any]]] = None,
        run_status_provider: Optional[
            Callable[[str, str], Dict[str, Any]]
        ] = None,
        chat_manager_provider: Optional[Callable[[str], Any]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.store = store or CompanyStore(root_path=root_path)
        self._projects_provider = projects_provider or _default_projects
        self._users_provider = users_provider or _default_users
        self._profiles_provider = profiles_provider or _default_profiles
        self._project_settings_provider = (
            project_settings_provider or _default_project_settings
        )
        self._dispatcher = dispatcher or self._default_dispatcher
        self._run_status_provider = run_status_provider or self._default_run_status
        self._chat_manager_provider = (
            chat_manager_provider or self._default_chat_manager
        )
        self._clock = clock or datetime.now

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        """Return the manager clock."""
        return self._clock()

    def _event(
        self,
        company: Company,
        event_type: str,
        message: str = "",
        actor: str = "system",
        mission_id: str = "",
        **data: Any,
    ) -> CompanyEvent:
        """Append a journal event for *company*."""
        event = CompanyEvent(
            type=event_type,
            actor=actor or "system",
            message=message,
            mission_id=mission_id,
            data=data,
        )
        return self.store.append_event(company.id, event)

    @staticmethod
    def _clean(value: Any) -> str:
        """Trim a value into a string."""
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _apply(instance: Any, patch: Dict[str, Any], fields: Iterable[str]) -> List[str]:
        """
        Apply a validated patch to a pydantic model.

        Args:
            instance: Model to mutate.
            patch: Raw patch values.
            fields: Allowed field names.

        Returns:
            Names of the fields that actually changed.
        """
        changed: List[str] = []
        for field in fields:
            if field not in patch or patch[field] is None:
                continue
            value = patch[field]
            if getattr(instance, field, None) != value:
                setattr(instance, field, value)
                changed.append(field)
        return changed

    def project_settings(self, project_id: str) -> Any:
        """
        Return the ``CODXJuniorSettings`` of a linked project, or ``None``.

        The company assistant uses this to create project-owned profiles. It is
        a small public wrapper around the injected provider so the assistant
        never reaches into a private attribute.
        """
        try:
            return self._project_settings_provider(project_id)
        except Exception as ex:
            logger.debug("Company: project settings unavailable for %s: %s", project_id, ex)
            return None

    def _project_catalog(self) -> List[Dict[str, Any]]:
        """Return the (normalized) project catalog."""
        try:
            projects = self._projects_provider() or []
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Company: project catalog failed: %s", ex)
            return []
        normalized = []
        for project in projects:
            if not project:
                continue
            project_id = self._clean(
                project.get("project_id") or project.get("id") or ""
            )
            if not project_id:
                continue
            normalized.append(
                {
                    "project_id": project_id,
                    "project_name": self._clean(
                        project.get("project_name")
                        or project.get("name")
                        or project_id
                    ),
                    "project_path": self._clean(
                        project.get("project_path") or project.get("path") or ""
                    ),
                    "codx_path": self._clean(project.get("codx_path") or ""),
                }
            )
        return normalized

    def _profile_catalog(self, project_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Return the (normalized) profile catalog."""
        try:
            profiles = self._profiles_provider(project_ids) or []
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Company: profile catalog failed: %s", ex)
            return []
        normalized = []
        for profile in profiles:
            name = self._clean(profile.get("name") if isinstance(profile, dict) else profile)
            if not name:
                continue
            normalized.append(
                profile
                if isinstance(profile, dict)
                else {"name": name, "category": "", "description": "", "projects": []}
            )
        return normalized

    def _validate_member(
        self,
        member: CompanyMember,
        *,
        projects: Optional[List[Dict[str, Any]]] = None,
        users: Optional[List[Dict[str, Any]]] = None,
        profiles: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        Validate a member reference against the catalogs.

        Catalogs are only enforced when they are available: a fresh install with
        no discovered project must still be able to define a company, and tests
        can inject their own catalog.
        """
        if member.is_ai:
            catalog = profiles if profiles is not None else self._profile_catalog()
            if not catalog:
                return
            names = {self._clean(profile.get("name")) for profile in catalog}
            if member.name not in names:
                sample = ", ".join(sorted(names)[:12])
                raise CompanyValidationError(
                    f"AI profile '{member.name}' does not exist in the linked "
                    f"projects. Available profiles: {sample or 'none'}"
                )
            return

        catalog = users if users is not None else self._users()
        if not catalog:
            return
        names = {self._clean(user.get("username")) for user in catalog}
        if member.name not in names:
            sample = ", ".join(sorted(names)[:12])
            raise CompanyValidationError(
                f"User '{member.name}' does not exist. Available users: "
                f"{sample or 'none'}"
            )

    def _users(self) -> List[Dict[str, Any]]:
        """Return the user catalog (safe when auth is disabled)."""
        try:
            return self._users_provider() or []
        except Exception as ex:  # pragma: no cover - defensive
            logger.debug("Company: user catalog failed: %s", ex)
            return []

    def _validate_project(self, project_id: str, projects: Optional[List[Dict[str, Any]]] = None) -> None:
        """Validate a project reference against the discovery catalog."""
        project_id = self._clean(project_id)
        if not project_id:
            raise CompanyValidationError("project_id is required")
        catalog = projects if projects is not None else self._project_catalog()
        if not catalog:
            return
        if not any(project["project_id"] == project_id for project in catalog):
            available = ", ".join(
                f"{project['project_name']} ({project['project_id']})"
                for project in catalog[:10]
            )
            raise CompanyValidationError(
                f"Project '{project_id}' is not a discovered codx-junior project. "
                f"Available: {available or 'none'}"
            )

    def _validate_team_members(
        self,
        team: CompanyTeam,
        projects: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Validate every member of a team (and the lead identity)."""
        profile_catalog = (
            self._profile_catalog([project["project_id"] for project in projects])
            if projects
            else self._profile_catalog()
        )
        user_catalog = self._users()
        for member in team.members:
            self._validate_member(
                member,
                projects=projects,
                users=user_catalog,
                profiles=profile_catalog,
            )
        if team.lead_name:
            self._validate_member(
                CompanyMember(kind=team.lead_kind, name=team.lead_name),
                projects=projects,
                users=user_catalog,
                profiles=profile_catalog,
            )

    def _validate_org_parent(
        self,
        company: Company,
        unit_id: str,
        parent_id: str,
    ) -> None:
        """Reject a missing parent or a parent cycle."""
        parent_id = self._clean(parent_id)
        if not parent_id:
            return
        if parent_id == unit_id:
            raise CompanyValidationError("an org unit cannot be its own parent")
        if not company.org_unit(parent_id):
            raise CompanyValidationError(
                f"Parent org unit '{parent_id}' does not exist"
            )
        seen = {unit_id}
        current = company.org_unit(parent_id)
        while current is not None and current.parent_id:
            if current.id in seen:
                raise CompanyValidationError(
                    "The org unit hierarchy would contain a cycle"
                )
            seen.add(current.id)
            current = company.org_unit(current.parent_id)
        if current is not None and current.id in seen:
            raise CompanyValidationError(
                "The org unit hierarchy would contain a cycle"
            )

    def _slug_in_use(self, slug: str, exclude_id: str = "") -> bool:
        """Tell whether *slug* is already used by another company."""
        wanted = (slug or "").lower()
        # Never bootstrap here: creating a company must not create another one.
        for company in self.list_companies(include_archived=True, ensure_default=False):
            if company["id"] == exclude_id:
                continue
            if (company.get("slug") or "").lower() == wanted:
                return True
        return False

    def _unique_slug(self, base: str, exclude_id: str = "") -> str:
        """
        Return a slug derived from *base* that no other company is using.

        The display name is not a primary key, the slug is. Renaming a company
        to a name another one already used (or creating a second company with
        the same name) is legitimate, so the derived slug gets a numeric suffix
        (``acme``, ``acme-2``, ``acme-3`` …) instead of failing with a confusing
        "already exists": the URL stays unique and the name the owner typed is
        kept verbatim.

        Args:
            base: Name (or raw value) the slug is derived from.
            exclude_id: Company id that may keep the slug (a rename of itself).

        Returns:
            A free slug.

        Raises:
            CompanyConflict: When no free slug can be built.
        """
        slug = slugify(base)
        if not self._slug_in_use(slug, exclude_id=exclude_id):
            return slug
        for index in range(2, 1000):
            candidate = f"{slug}-{index}"
            if not self._slug_in_use(candidate, exclude_id=exclude_id):
                return candidate
        raise CompanyConflict(f"Cannot build a unique slug from '{base}'")

    def _clean_logo(self, value: Any) -> str:
        """
        Validate and normalise the company logo.

        The logo is a small brand asset rendered by the company shell. Three
        forms are accepted:

        * the URL this API serves an uploaded file from
          (``/api/companies/<id>/logo``, see :meth:`save_logo`),
        * an ``http(s)`` URL of an externally hosted image,
        * a legacy inline ``data:image/...`` URL (the format used before the
          logo became a file; old documents keep rendering with it).

        Anything else — and any inline payload above :data:`MAX_LOGO_LENGTH` —
        is rejected instead of being stored.

        Args:
            value: Raw ``logo`` value.

        Returns:
            The trimmed logo (``""`` clears it).

        Raises:
            CompanyValidationError: When the value is not a supported logo.
        """
        logo = self._clean(value)
        if not logo:
            return ""
        if len(logo) > MAX_LOGO_LENGTH:
            raise CompanyValidationError(
                f"Logo is too large ({len(logo)} characters, max {MAX_LOGO_LENGTH})"
            )
        if logo.lower().startswith("data:image/"):
            header, _, payload = logo.partition(",")
            media = header[len("data:"):].split(";")[0].strip().lower()
            if not payload or ";base64" not in header.lower():
                raise CompanyValidationError(
                    "Logo data URL must be base64 encoded"
                )
            if media not in LOGO_MEDIA_TYPES:
                raise CompanyValidationError(
                    "Unsupported logo type '%s'. Allowed: %s"
                    % (media, ", ".join(LOGO_MEDIA_TYPES))
                )
            return logo
        if logo.lower().startswith(("http://", "https://")):
            return logo
        if COMPANY_LOGO_PATH.match(logo):
            return logo
        raise CompanyValidationError(
            "Logo must be an http(s) URL, the /api/companies/<id>/logo path "
            "or an inline image data URL"
        )

    # ------------------------------------------------------------------
    # Logo
    # ------------------------------------------------------------------

    def logo_url(self, company_id: str, content: bytes = b"") -> str:
        """
        Return the URL that serves the logo of a company.

        The document stores this short path instead of the image itself, so a
        multi-megabyte brand render never travels inside every ``company.json``
        read and write. The ``v`` parameter is the digest of the stored bytes: it
        changes exactly when the image changes, so the browser picks the new mark
        without a hard reload and still caches an unchanged one.

        Args:
            company_id: Company id.
            content: Image bytes (omitted only before the file is stored).

        Returns:
            The URL of :func:`codx.junior.api.company.api_company_logo`.
        """
        version = (
            hashlib.md5(content).hexdigest()[:10]
            if content
            else str(int(self._now().timestamp()))
        )
        return f"/api/companies/{company_id}/logo?v={version}"

    def save_logo(
        self,
        company_id: str,
        content: bytes,
        filename: str = "",
        actor: str = "system",
    ) -> Company:
        """
        Store an uploaded image as the company logo.

        The bytes are written next to the company document (``logo.png`` …) and
        the document only keeps the URL that serves them, so the image never
        inflates the document nor the snapshot payload.

        Args:
            company_id: Company id.
            content: Raw image bytes.
            filename: Original file name (recorded in the audit trail).
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.

        Raises:
            CompanyValidationError: When the payload is empty, too large or not
                a supported image.
            CompanyNotFound: Unknown company.
        """
        if not content:
            raise CompanyValidationError("The logo file is empty")
        if len(content) > MAX_LOGO_FILE_BYTES:
            raise CompanyValidationError(
                "Logo is too large (%d bytes, max %d)"
                % (len(content), MAX_LOGO_FILE_BYTES)
            )
        extension, _ = detect_logo_type(content)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            self.store.write_logo(company.id, content, extension)
            company.logo = self.logo_url(company.id, content)
            saved = self.store.save(company)
            self._event(
                saved,
                "company.logo",
                "Company logo updated",
                actor=actor,
                extension=extension,
                size=len(content),
                filename=self._clean(filename) or None,
            )
            logger.info("Company logo updated: %s (%s)", saved.id, extension)
            return saved

    def clear_logo(self, company_id: str, actor: str = "system") -> Company:
        """
        Remove the company logo (file and stored URL).

        Args:
            company_id: Company id.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.

        Raises:
            CompanyNotFound: Unknown company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            removed = self.store.remove_logo(company.id)
            had_value = bool(self._clean(company.logo))
            company.logo = ""
            saved = self.store.save(company)
            if removed or had_value:
                self._event(
                    saved,
                    "company.logo",
                    "Company logo removed",
                    actor=actor,
                )
            return saved

    def logo_asset(self, company_id: str) -> Dict[str, Any]:
        """
        Describe how the logo of a company must be served.

        Args:
            company_id: Company id.

        Returns:
            ``{"kind": "url", "url": ...}`` for an externally hosted logo,
            ``{"kind": "file", "path": ..., "media_type": ...}`` for a stored
            file and ``{"kind": "inline", "content": bytes, "media_type": ...}``
            for a legacy inline data URL, so one endpoint answers for every
            document version.

        Raises:
            CompanyNotFound: Unknown company.
            CompanyValidationError: When the company has no logo.
        """
        company = self.get_company(company_id)
        logo = self._clean(company.logo)
        if logo.lower().startswith(("http://", "https://")):
            return {"kind": "url", "url": logo}
        path = self.store.logo_path(company.id)
        if path:
            media_type, _ = mimetypes.guess_type(path)
            return {
                "kind": "file",
                "path": path,
                "media_type": media_type or "application/octet-stream",
            }
        if logo.lower().startswith("data:image/"):
            header, _, payload = logo.partition(",")
            media_type = header[len("data:"):].split(";")[0].strip().lower()
            try:
                return {
                    "kind": "inline",
                    "content": base64.b64decode(payload, validate=False),
                    "media_type": media_type or "image/png",
                }
            except Exception:  # pragma: no cover - defensive
                logger.warning("Company '%s' has an undecodable inline logo", company.id)
        raise CompanyValidationError("This company has no logo")

    # ------------------------------------------------------------------
    # Company CRUD
    # ------------------------------------------------------------------

    def _default_company_name(self) -> str:
        """Resolve the display name of the installation's default company."""
        configured = self._clean(os.environ.get(DEFAULT_COMPANY_NAME_ENV))
        return configured or DEFAULT_COMPANY_NAME

    def _company_ids(self) -> List[str]:
        """Return every stored company id without triggering a bootstrap."""
        try:
            return self.store.list_ids()
        except Exception as ex:  # pragma: no cover - defensive
            logger.warning("Company: cannot list stored companies: %s", ex)
            return []

    def _sync_default_company_projects(
        self, company: Company, actor: str = "system"
    ) -> Company:
        """
        Keep the default company able to work with every discovered project.

        The default company starts with **all** projects linked and keeps
        linking newly discovered ones while ``auto_sync_projects`` is true. The
        flag is switched off the first time a user edits the project list, so
        an explicit unlink is never undone behind their back.

        Args:
            company: Default company to synchronize.
            actor: Username recorded when new projects are linked.

        Returns:
            The company (unchanged when there is nothing new to link).
        """
        if not company.is_default or not company.auto_sync_projects:
            return company

        catalog = self._project_catalog()
        if not catalog:
            return company
        linked = {project.project_id for project in company.projects}
        missing = [
            project for project in catalog
            if project["project_id"] not in linked
        ]
        if not missing:
            return company

        with self.store.lock(company.id):
            # Reload under the lock: another process may have linked a project
            # between the read above and this write.
            current = self.store.load(company.id)
            if not current.is_default or not current.auto_sync_projects:
                return current
            linked = {project.project_id for project in current.projects}
            added: List[str] = []
            for project in missing:
                if project["project_id"] in linked:
                    continue
                current.projects.append(
                    CompanyProject(
                        project_id=project["project_id"],
                        name=project["project_name"] or project["project_id"],
                        role="",
                    )
                )
                linked.add(project["project_id"])
                added.append(project["project_id"])
            if not added:
                return current
            saved = self.store.save(current)
            self._event(
                saved,
                "project.linked",
                f"{len(added)} discovered project(s) linked to the default company",
                actor=actor,
                project_ids=added,
                automatic=True,
            )
            return saved

    def _enroll_users_in_default_company(self, company: Company) -> None:
        """
        Make every existing user a member of the fresh default company.

        The default company is the installation-wide company, so a user must
        not have to be invited before entering the product. Global admins and
        company admins become company admins; everyone else joins as a member.
        The operation is idempotent and best-effort: auth may be disabled or a
        user document may be read-only.
        """
        try:
            from codx.junior.security.user_management import UserSecurityManager

            security = UserSecurityManager()
            for user in self._users():
                username = self._clean(user.get("username"))
                if not username:
                    continue
                role = (
                    "admin"
                    if user.get("role") in ("admin", "company_admin")
                    else "member"
                )
                try:
                    security.add_company_member(
                        company_id=company.id,
                        username=username,
                        role=role,
                        title="Owner" if role == "admin" else "",
                    )
                except Exception as ex:  # pragma: no cover - per-user best effort
                    logger.debug(
                        "Company: cannot enroll '%s' in the default company: %s",
                        username,
                        ex,
                    )
        except Exception as ex:  # pragma: no cover - auth may be disabled
            logger.debug("Company: default company enrollment unavailable: %s", ex)

    def _is_auto_default(self, company: Company) -> bool:
        """Tell whether a company is (or was) the auto-provisioned default."""
        tags = {str(tag).strip().lower() for tag in (company.tags or [])}
        description = str(company.description or "").strip()
        return "default" in tags or description.startswith(AUTO_DEFAULT_DESCRIPTION)

    def _migrate_default_company(self, company: Company) -> bool:
        """
        Bring an existing default company up to the current shape.

        * Old defaults use auto-sync; the first-class flag is ``all_projects``.
        * An untouched auto-provisioned default still named ``ACME`` (the old
          release's placeholder) is renamed to the current default name.
          A company the owner renamed or configured keeps its name.

        Returns:
            ``True`` when the company was changed and must be saved.
        """
        changed = False
        if company.auto_sync_projects and not company.all_projects:
            company.all_projects = True
            changed = True
        if (
            self._is_auto_default(company)
            and company.name.strip().lower() in LEGACY_DEFAULT_COMPANY_NAMES
        ):
            company.name = self._default_company_name()
            company.slug = self._unique_slug(company.name, exclude_id=company.id)
            changed = True
        if changed:
            company.updated_at = now_iso()
        return changed

    def _canonical_default(self, defaults: List[Company]) -> Company:
        """Pick the default company to keep (the oldest one wins)."""
        return min(
            defaults,
            key=lambda company: (company.created_at or "", company.id),
        )

    def _demote_duplicate_defaults(
        self, defaults: List[Company], canonical: Company, actor: str
    ) -> None:
        """
        Reconcile a store that ended up with more than one default company.

        Only one company can be the installation default. Extra defaults are
        demoted; the auto-provisioned ones (a bootstrap race) are archived so
        the company switcher does not show duplicates.
        """
        for extra in defaults:
            if extra.id == canonical.id:
                continue
            extra.is_default = False
            if self._is_auto_default(extra):
                extra.status = "archived"
            extra.updated_at = now_iso()
            saved = self.store.save(extra)
            self._event(
                saved,
                "company.default",
                f"Company '{saved.name}' demoted: '{canonical.name}' is the default",
                actor=actor,
                duplicate_of=canonical.id,
                archived=saved.status == "archived",
            )

    def ensure_default_company(
        self, actor: str = "system", name: str = ""
    ) -> Company:
        """
        Return the installation's default company, creating it when needed.

        A fresh installation has no company. Instead of forcing every user to
        create one before seeing anything, codx-junior provisions a default
        company that links **every discovered project**. Existing installations
        created before companies were the top-level entity are adopted: the
        oldest stored company becomes the default and keeps its project list
        (the user's existing configuration is never rewritten implicitly).

        The whole read-create sequence runs under an installation-wide file
        lock: two API workers that both see an empty store must never each
        create their own default company.

        Args:
            actor: Username recorded in the journal.
            name: Optional display name for the company when it is created.

        Returns:
            The default company.
        """
        with self.store.installation_lock("default-company"):
            return self._ensure_default_company_locked(actor=actor, name=name)

    def _ensure_default_company_locked(
        self, actor: str = "system", name: str = ""
    ) -> Company:
        """Implementation of :meth:`ensure_default_company` (lock held)."""
        companies: List[Company] = []
        for company_id in self._company_ids():
            try:
                companies.append(self.store.load(company_id))
            except Exception as ex:
                logger.warning(
                    "Company '%s' skipped during bootstrap: %s", company_id, ex
                )

        defaults = [company for company in companies if company.is_default]
        if defaults:
            company = self._canonical_default(defaults)
            changed = self._migrate_default_company(company)
            if changed:
                company = self.store.save(company)
            if len(defaults) > 1:
                self._demote_duplicate_defaults(defaults, company, actor)
            return self._sync_default_company_projects(company, actor=actor)

        if companies:
            # Migration path: adopt the oldest stored company as the default so
            # the product keeps a single top-level company without destroying
            # data. It keeps its name and project list.
            adopted = min(
                companies,
                key=lambda company: (company.created_at or "", company.id),
            )
            adopted.is_default = True
            adopted.auto_sync_projects = False
            adopted.updated_at = now_iso()
            saved = self.store.save(adopted)
            self._event(
                saved,
                "company.default",
                f"Company '{saved.name}' adopted as the default company",
                actor=actor,
                adopted=True,
            )
            return saved

        catalog = self._project_catalog()
        company_name = self._clean(name) or self._default_company_name()
        company = Company(
            name=company_name,
            slug=DEFAULT_COMPANY_SLUG,
            description=AUTO_DEFAULT_DESCRIPTION,
            owner="" if (actor or "system") == "system" else actor,
            is_default=True,
            auto_sync_projects=True,
            all_projects=True,
            tags=["default"],
        )
        for project in catalog:
            existing = company.project(project["project_id"])
            if existing is not None:
                continue
            company.projects.append(
                CompanyProject(
                    project_id=project["project_id"],
                    name=project["project_name"] or project["project_id"],
                    role="",
                )
            )
        saved = self.store.save(company)
        self._event(
            saved,
            "company.created",
            f"Default company '{saved.name}' created with {len(saved.projects)} project(s)",
            actor=actor,
            default=True,
        )
        # The default company is the installation-wide company: every user can
        # enter it from the first login.
        self._enroll_users_in_default_company(saved)
        logger.info(
            "Default company created: %s (%s) with %d project(s)",
            saved.name,
            saved.id,
            len(saved.projects),
        )
        return saved

    def lookup_companies_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Return the companies that can see *project_id*.

        Used by the router to resolve legacy ``/<project>/...`` URLs to the
        canonical ``/<company>/workspace/<project>/...`` path. A company sees a
        project when it is a direct link, a visible child (``include_children``)
        or when it sees all projects.
        """
        project_id = self._clean(project_id)
        if not project_id:
            return []
        found: List[Dict[str, Any]] = []
        for company_id in self._company_ids():
            try:
                company = self.store.load(company_id)
            except Exception:
                continue
            if company.status == "archived":
                continue
            if company.all_projects or project_id in set(self.visible_project_ids(company)):
                found.append(
                    {
                        "id": company.id,
                        "name": company.name,
                        "slug": company.slug,
                        "is_default": company.is_default,
                    }
                )
        return found

    def list_companies(
        self,
        include_archived: bool = True,
        ensure_default: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        List every stored company.

        Args:
            include_archived: When false, archived companies are hidden.
            ensure_default: Provision the default company on a fresh install
                (and keep it linked to every discovered project) before listing.
                Pass ``False`` from write paths that must not mutate the store
                implicitly.

        Returns:
            Company summaries sorted by name.
        """
        if ensure_default:
            try:
                self.ensure_default_company()
            except Exception as ex:  # pragma: no cover - bootstrap must not break reads
                logger.warning("Company: default company bootstrap failed: %s", ex)
        result: List[Dict[str, Any]] = []
        for company_id in self.store.list_ids():
            try:
                company = self.store.load(company_id)
            except Exception as ex:
                logger.warning("Company '%s' skipped: %s", company_id, ex)
                continue
            if not include_archived and company.status == "archived":
                continue
            result.append(company.summary())
        return sorted(result, key=lambda item: item["name"].lower())

    def get_company(self, company_id: str) -> Company:
        """
        Load a company by id **or** slug.

        Args:
            company_id: Company id or slug.

        Returns:
            The company.

        Raises:
            CompanyNotFound: If no company matches.
        """
        company_id = self._clean(company_id)
        if not company_id:
            raise CompanyNotFound("company_id is required")
        if self.store.exists(company_id):
            return self.store.load(company_id)
        # Slug fallback: the UI may address a company by its readable slug.
        for stored_id in self.store.list_ids():
            try:
                company = self.store.load(stored_id)
            except Exception:
                continue
            if company.slug.lower() == company_id.lower():
                return company
        raise CompanyNotFound(f"Company '{company_id}' not found")

    def create_company(
        self,
        company: Any,
        actor: str = "system",
        template: str = "",
        project_ids: Optional[List[str]] = None,
    ) -> Company:
        """
        Create a company.

        Args:
            company: A :class:`Company`, mapping or JSON payload.
            actor: Username recorded in the audit trail.
            template: Optional starter (``saas``, ``blank``).
            project_ids: Projects to link before applying the template.

        Returns:
            The persisted company.

        Raises:
            CompanyValidationError: On invalid input.
            CompanyConflict: When the slug is already used.
        """
        # ``Company`` derives the slug from the name, so an explicitly requested
        # slug can only be detected on the raw payload, before the model is built.
        explicit_slug = ""
        if isinstance(company, Company):
            company = company.model_copy(deep=True)
        else:
            payload = dict(company or {})
            if self._clean(payload.get("slug")):
                explicit_slug = slugify(payload.get("slug"))
            try:
                company = Company(**payload)
            except Exception as ex:
                raise CompanyValidationError(str(ex)) from ex

        company.id = new_id("company")
        company.created_at = now_iso()
        company.updated_at = company.created_at
        company.name = self._clean(company.name)
        if not company.name:
            raise CompanyValidationError("company name is required")
        # An explicit slug is a hard requirement (deep links use it); a slug
        # derived from the name is disposable and gets a suffix when it collides,
        # so a second company with the same name is never refused.
        if explicit_slug and self._slug_in_use(explicit_slug):
            raise CompanyConflict(
                f"A company with slug '{explicit_slug}' already exists"
            )
        company.slug = explicit_slug or self._unique_slug(company.name)
        company.logo = self._clean_logo(company.logo)
        if not company.owner:
            company.owner = actor

        # Link requested projects first so the template can point teams at them.
        projects = self._project_catalog()
        for project_id in project_ids or []:
            project_id = self._clean(
                project_id.get("project_id") if isinstance(project_id, dict) else project_id
            )
            if not project_id or company.project(project_id):
                continue
            self._validate_project(project_id, projects=projects)
            match = next(
                (item for item in projects if item["project_id"] == project_id),
                None,
            )
            company.projects.append(
                CompanyProject(
                    project_id=project_id,
                    name=(match or {}).get("project_name", project_id),
                    role="",
                )
            )

        company = apply_template(company, template, project_ids=project_ids)

        # Assign ids to anything the caller left blank.
        for unit in company.org_units:
            unit.id = unit.id or new_id("org")
            unit.updated_at = now_iso()
        for team in company.teams:
            team.id = team.id or new_id("team")
            team.ensure_lead()
            team.updated_at = now_iso()
        for mission in company.missions:
            mission.id = mission.id or new_id("mission")
            mission.updated_at = now_iso()

        self._ensure_unique_children(company)
        for team in company.teams:
            self._validate_team_members(team, projects=projects)

        saved = self.store.save(company)
        self._event(
            saved,
            "company.created",
            f"Company '{saved.name}' created",
            actor=actor,
            template=template or None,
        )
        logger.info("Company created: %s (%s)", saved.name, saved.id)
        return saved

    def update_company(
        self,
        company_id: str,
        patch: Any,
        actor: str = "system",
    ) -> Company:
        """
        Update the company-level fields (never the org/teams/projects/missions).

        Args:
            company_id: Company id.
            patch: Mapping (or :class:`Company`) with the fields to change.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.

        Raises:
            CompanyConflict: When the new slug is already used.
        """
        values = self._patch_dict(patch)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            explicit_slug = bool(self._clean(values.get("slug")))
            if explicit_slug:
                slug = slugify(values["slug"])
                if self._slug_in_use(slug, exclude_id=company.id):
                    raise CompanyConflict(
                        f"A company with slug '{slug}' already exists"
                    )
                values["slug"] = slug
            if "name" in values:
                name = self._clean(values["name"])
                if not name:
                    raise CompanyValidationError("company name is required")
                values["name"] = name
                if not explicit_slug and name != company.name:
                    # The slug is the company URL and it is derived from the name:
                    # renaming must move it too, otherwise the old name stays
                    # occupied by a company that no longer uses it and the next
                    # company created with that name fails with "already exists".
                    values["slug"] = self._unique_slug(name, exclude_id=company.id)
            if "logo" in values:
                values["logo"] = self._clean_logo(values.get("logo"))
            if "all_projects" in values and values["all_projects"] is not None:
                # The wildcard and the auto-sync flag are the same behaviour for
                # the default company: switching to an explicit list must stop
                # new projects from being linked behind the owner's back.
                company.auto_sync_projects = bool(values["all_projects"])
            changed = self._apply(company, values, COMPANY_EDITABLE_FIELDS)
            saved = self.store.save(company)
            if changed:
                self._event(
                    saved,
                    "company.updated",
                    f"Company updated: {', '.join(changed)}",
                    actor=actor,
                    changed=changed,
                )
            return saved

    def delete_company(self, company_id: str, actor: str = "system") -> Dict[str, Any]:
        """
        Delete a company (folder + journal).

        Args:
            company_id: Company id.
            actor: Username recorded in the audit trail.

        Returns:
            ``{"deleted": id, "name": name}``.
        """
        company = self.get_company(company_id)
        with self.store.lock(company.id):
            self.store.delete(company.id)
        logger.info("Company deleted: %s by %s", company.id, actor)
        return {"deleted": company.id, "name": company.name}

    # ------------------------------------------------------------------
    # Organization
    # ------------------------------------------------------------------

    def add_org_unit(
        self, company_id: str, unit: Any, actor: str = "system"
    ) -> Company:
        """
        Add a department/function to the company organization.

        Args:
            company_id: Company id.
            unit: :class:`OrgUnit` or mapping.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        payload = self._patch_dict(unit)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            payload["id"] = new_id("org")
            try:
                org_unit = OrgUnit(**payload)
            except Exception as ex:
                raise CompanyValidationError(str(ex)) from ex
            self._validate_org_parent(company, org_unit.id, org_unit.parent_id)
            self._ensure_unique_org_name(company, org_unit.name)
            company.org_units.append(org_unit)
            saved = self.store.save(company)
            self._event(
                saved,
                "org_unit.created",
                f"Org unit '{org_unit.name}' created",
                actor=actor,
                org_unit_id=org_unit.id,
            )
            return saved

    def update_org_unit(
        self,
        company_id: str,
        unit_id: str,
        patch: Any,
        actor: str = "system",
    ) -> Company:
        """
        Update an org unit.

        Args:
            company_id: Company id.
            unit_id: Org unit id.
            patch: Mapping with the fields to change.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        values = self._patch_dict(patch)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            org_unit = company.org_unit(unit_id)
            if org_unit is None:
                raise CompanyNotFound(f"Org unit '{unit_id}' not found")
            if "parent_id" in values:
                self._validate_org_parent(company, unit_id, values["parent_id"])
            if "name" in values:
                name = self._clean(values["name"])
                if not name:
                    raise CompanyValidationError("org unit name is required")
                self._ensure_unique_org_name(company, name, exclude_id=unit_id)
                values["name"] = name
            changed = self._apply(
                org_unit,
                values,
                (
                    "name",
                    "kind",
                    "description",
                    "parent_id",
                    "lead_kind",
                    "lead_name",
                    "responsibilities",
                    "color",
                    "icon",
                    "order",
                ),
            )
            org_unit.updated_at = now_iso()
            saved = self.store.save(company)
            if changed:
                self._event(
                    saved,
                    "org_unit.updated",
                    f"Org unit '{org_unit.name}' updated: {', '.join(changed)}",
                    actor=actor,
                    org_unit_id=unit_id,
                )
            return saved

    def delete_org_unit(
        self, company_id: str, unit_id: str, actor: str = "system"
    ) -> Company:
        """
        Delete an org unit, detaching its children and teams.

        Args:
            company_id: Company id.
            unit_id: Org unit id.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            org_unit = company.org_unit(unit_id)
            if org_unit is None:
                raise CompanyNotFound(f"Org unit '{unit_id}' not found")
            company.org_units = [
                unit for unit in company.org_units if unit.id != unit_id
            ]
            for child in company.org_units:
                if child.parent_id == unit_id:
                    child.parent_id = ""
            for team in company.teams:
                if team.org_unit_id == unit_id:
                    team.org_unit_id = ""
            saved = self.store.save(company)
            self._event(
                saved,
                "org_unit.deleted",
                f"Org unit '{org_unit.name}' deleted",
                actor=actor,
                org_unit_id=unit_id,
            )
            return saved

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    def add_team(self, company_id: str, team: Any, actor: str = "system") -> Company:
        """
        Add a hybrid team to the company.

        Args:
            company_id: Company id.
            team: :class:`CompanyTeam` or mapping.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        payload = self._patch_dict(team)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            payload["id"] = new_id("team")
            try:
                company_team = CompanyTeam(**payload)
            except Exception as ex:
                raise CompanyValidationError(str(ex)) from ex
            if not company_team.lead_name and company_team.members:
                company_team.lead_kind = company_team.members[0].kind
                company_team.lead_name = company_team.members[0].name
            self._ensure_unique_team_name(company, company_team.name)
            if company_team.org_unit_id and not company.org_unit(
                company_team.org_unit_id
            ):
                raise CompanyValidationError(
                    f"Org unit '{company_team.org_unit_id}' does not exist"
                )
            projects = self._project_catalog()
            for project_id in company_team.project_ids:
                self._validate_project(project_id, projects=projects)
            self._validate_team_members(company_team, projects=projects)
            company.teams.append(company_team)
            saved = self.store.save(company)
            self._event(
                saved,
                "team.created",
                f"Team '{company_team.name}' created",
                actor=actor,
                team_id=company_team.id,
                members=[member.key for member in company_team.members],
            )
            return saved

    def update_team(
        self,
        company_id: str,
        team_id: str,
        patch: Any,
        actor: str = "system",
    ) -> Company:
        """
        Update a team (including a full roster replacement when ``members`` is sent).

        Args:
            company_id: Company id.
            team_id: Team id.
            patch: Mapping with fields to change.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        values = self._patch_dict(patch)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            team = company.team(team_id)
            if team is None:
                raise CompanyNotFound(f"Team '{team_id}' not found")
            if "name" in values:
                name = self._clean(values["name"])
                if not name:
                    raise CompanyValidationError("team name is required")
                self._ensure_unique_team_name(company, name, exclude_id=team_id)
                values["name"] = name
            if "org_unit_id" in values and values["org_unit_id"]:
                if not company.org_unit(values["org_unit_id"]):
                    raise CompanyValidationError(
                        f"Org unit '{values['org_unit_id']}' does not exist"
                    )
            if "members" in values:
                try:
                    values["members"] = CompanyMember.parse_list(values["members"])
                except Exception as ex:
                    raise CompanyValidationError(str(ex)) from ex
            if "project_ids" in values:
                projects = self._project_catalog()
                for project_id in values["project_ids"]:
                    self._validate_project(project_id, projects=projects)
            changed = self._apply(team, values, TEAM_EDITABLE_FIELDS)
            if "lead_name" in changed or "lead_kind" in changed or "members" in changed:
                team.ensure_lead()
            if not team.lead_name and team.members:
                team.lead_kind = team.members[0].kind
                team.lead_name = team.members[0].name
            team.updated_at = now_iso()
            self._validate_team_members(team)
            saved = self.store.save(company)
            if changed:
                self._event(
                    saved,
                    "team.updated",
                    f"Team '{team.name}' updated: {', '.join(changed)}",
                    actor=actor,
                    team_id=team_id,
                )
            return saved

    def delete_team(
        self,
        company_id: str,
        team_id: str,
        actor: str = "system",
        force: bool = False,
    ) -> Company:
        """
        Delete a team.

        Args:
            company_id: Company id.
            team_id: Team id.
            actor: Username recorded in the audit trail.
            force: Detach open missions instead of refusing.

        Returns:
            The updated company.

        Raises:
            CompanyConflict: When open missions reference the team and not forced.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            team = company.team(team_id)
            if team is None:
                raise CompanyNotFound(f"Team '{team_id}' not found")
            open_missions = [
                mission
                for mission in company.open_missions
                if mission.team_id == team_id
            ]
            if open_missions and not force:
                raise CompanyConflict(
                    f"Team '{team.name}' owns {len(open_missions)} open mission(s). "
                    "Reassign or close them first, or force the delete."
                )
            for mission in open_missions:
                mission.team_id = ""
                mission.updated_at = now_iso()
            company.teams = [item for item in company.teams if item.id != team_id]
            for project in company.projects:
                if project.team_id == team_id:
                    project.team_id = ""
            saved = self.store.save(company)
            self._event(
                saved,
                "team.deleted",
                f"Team '{team.name}' deleted",
                actor=actor,
                team_id=team_id,
                detached_missions=[mission.id for mission in open_missions],
            )
            return saved

    def add_team_members(
        self,
        company_id: str,
        team_id: str,
        members: Iterable[Any],
        actor: str = "system",
    ) -> Company:
        """
        Add (or update) humans and AI profiles in a team.

        Args:
            company_id: Company id.
            team_id: Team id.
            members: Names, ``kind:name`` strings or member mappings.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        try:
            parsed = CompanyMember.parse_list(list(members or []))
        except Exception as ex:
            raise CompanyValidationError(str(ex)) from ex
        if not parsed:
            raise CompanyValidationError("members are required")
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            team = company.team(team_id)
            if team is None:
                raise CompanyNotFound(f"Team '{team_id}' not found")
            self._validate_team_members(
                CompanyTeam(
                    id=team.id,
                    name=team.name,
                    members=parsed,
                    lead_kind=team.lead_kind,
                    lead_name=team.lead_name,
                )
            )
            results = [team.upsert_member(member) for member in parsed]
            if not team.lead_name:
                team.lead_kind = team.members[0].kind
                team.lead_name = team.members[0].name
            team.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "team.members.added",
                f"{len(parsed)} member(s) upserted in '{team.name}'",
                actor=actor,
                team_id=team_id,
                members=[member.key for member in parsed],
                results=results,
            )
            return saved

    def remove_team_members(
        self,
        company_id: str,
        team_id: str,
        members: Iterable[str],
        actor: str = "system",
    ) -> Company:
        """
        Remove members from a team by ``kind:name`` or plain name.

        Args:
            company_id: Company id.
            team_id: Team id.
            members: References to remove.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        refs = [self._clean(member) for member in (members or []) if self._clean(member)]
        if not refs:
            raise CompanyValidationError("members are required")
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            team = company.team(team_id)
            if team is None:
                raise CompanyNotFound(f"Team '{team_id}' not found")
            removed = []
            for ref in refs:
                if ":" in ref:
                    kind, name = ref.split(":", 1)
                    if team.member(kind, name) is not None:
                        team.remove_member(kind, name)
                        removed.append(ref)
                    continue
                matching = [
                    member
                    for member in team.members
                    if member.name == ref
                ]
                for member in matching:
                    team.remove_member(member.kind, member.name)
                    removed.append(member.key)
            if not removed:
                raise CompanyValidationError(
                    f"None of {refs} is a member of '{team.name}'"
                )
            if not team.lead_name and team.members:
                team.lead_kind = team.members[0].kind
                team.lead_name = team.members[0].name
            team.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "team.members.removed",
                f"{len(removed)} member(s) removed from '{team.name}'",
                actor=actor,
                team_id=team_id,
                members=removed,
            )
            return saved

    def set_team_lead(
        self,
        company_id: str,
        team_id: str,
        kind: str,
        name: str,
        actor: str = "system",
    ) -> Company:
        """
        Change a team lead (adding it to the roster when needed).

        Args:
            company_id: Company id.
            team_id: Team id.
            kind: ``user`` or ``profile``.
            name: Username or profile name.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        name = self._clean(name)
        if not name:
            raise CompanyValidationError("lead name is required")
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            team = company.team(team_id)
            if team is None:
                raise CompanyNotFound(f"Team '{team_id}' not found")
            member = CompanyMember(kind=kind or "profile", name=name, role="lead")
            self._validate_member(member)
            team.lead_kind = member.kind
            team.lead_name = member.name
            team.ensure_lead()
            team.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "team.lead.changed",
                f"Team '{team.name}' lead is now {member.key}",
                actor=actor,
                team_id=team_id,
                lead=member.key,
            )
            return saved

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def link_project(
        self,
        company_id: str,
        project_id: str,
        actor: str = "system",
        **options: Any,
    ) -> Company:
        """
        Link a discovered codx-junior project to the company.

        Args:
            company_id: Company id.
            project_id: codx-junior project id.
            actor: Username recorded in the audit trail.
            **options: ``name``, ``role``, ``description``, ``team_id``,
                ``priority``, ``budget_cxjcoins``.

        Returns:
            The updated company.
        """
        project_id = self._clean(project_id)
        projects = self._project_catalog()
        self._validate_project(project_id, projects=projects)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            existing = company.project(project_id)
            match = next(
                (item for item in projects if item["project_id"] == project_id),
                None,
            )
            payload = {
                "name": options.get("name")
                or (existing.name if existing else "")
                or (match or {}).get("project_name", project_id),
                "role": options.get("role", existing.role if existing else ""),
                "description": options.get(
                    "description", existing.description if existing else ""
                ),
                "team_id": options.get("team_id", existing.team_id if existing else ""),
                "priority": options.get(
                    "priority", existing.priority if existing else "medium"
                ),
                "budget_cxjcoins": options.get(
                    "budget_cxjcoins", existing.budget_cxjcoins if existing else 0.0
                ),
                "status": options.get(
                    "status", existing.status if existing else "active"
                ),
                "include_children": options.get(
                    "include_children",
                    existing.include_children if existing else True,
                ),
            }
            if payload["team_id"] and not company.team(payload["team_id"]):
                raise CompanyValidationError(
                    f"Team '{payload['team_id']}' does not exist"
                )
            if existing is None:
                company.projects.append(
                    CompanyProject(project_id=project_id, **payload)
                )
                event_type = "project.linked"
                message = f"Project '{payload['name']}' linked"
            else:
                for field, value in payload.items():
                    setattr(existing, field, value)
                existing.updated_at = now_iso()
                event_type = "project.updated"
                message = f"Project '{existing.name}' updated"
            # An explicit link/unlink means the user manages the project list.
            company.auto_sync_projects = False
            company.all_projects = False
            saved = self.store.save(company)
            self._event(
                saved,
                event_type,
                message,
                actor=actor,
                project_id=project_id,
            )
            return saved

    def unlink_project(
        self,
        company_id: str,
        project_id: str,
        actor: str = "system",
        force: bool = False,
    ) -> Company:
        """
        Unlink a project from the company.

        Args:
            company_id: Company id.
            project_id: Project id.
            actor: Username recorded in the audit trail.
            force: Detach open missions instead of refusing.

        Returns:
            The updated company.

        Raises:
            CompanyConflict: When open missions run in the project and not forced.
        """
        project_id = self._clean(project_id)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            project = company.project(project_id)
            if project is None:
                raise CompanyNotFound(f"Project '{project_id}' is not linked")
            open_missions = [
                mission
                for mission in company.open_missions
                if mission.project_id == project_id
            ]
            if open_missions and not force:
                raise CompanyConflict(
                    f"Project '{project.name or project_id}' has "
                    f"{len(open_missions)} open mission(s). Close or reassign them "
                    "first, or force the unlink."
                )
            for mission in open_missions:
                mission.project_id = ""
                mission.updated_at = now_iso()
            company.projects = [
                item for item in company.projects if item.project_id != project_id
            ]
            for team in company.teams:
                team.project_ids = [
                    item for item in team.project_ids if item != project_id
                ]
            # An explicit unlink is a user decision: stop the default company
            # from auto-linking the project again on the next request.
            company.auto_sync_projects = False
            company.all_projects = False
            saved = self.store.save(company)
            self._event(
                saved,
                "project.unlinked",
                f"Project '{project.name or project_id}' unlinked",
                actor=actor,
                project_id=project_id,
            )
            return saved

    def set_project_links(
        self,
        company_id: str,
        project_ids: List[str],
        actor: str = "system",
    ) -> Company:
        """
        Replace the company's project list in one atomic operation.

        This is the endpoint the company settings screen uses: linking and
        unlinking one by one is fine for an automation, but a settings form
        must not leave the company half-configured when one request fails.
        Metadata of already-linked projects (role, team, budget) is preserved.

        Args:
            company_id: Company id.
            project_ids: Complete list of project ids to keep linked.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.

        Raises:
            CompanyValidationError: When any project is not discovered.
        """
        catalog = {project["project_id"]: project for project in self._project_catalog()}
        wanted: List[str] = []
        include_children: Dict[str, bool] = {}
        for project_id in project_ids or []:
            if isinstance(project_id, dict):
                value = self._clean(project_id.get("project_id"))
                if "include_children" in project_id:
                    include_children[value] = bool(project_id.get("include_children"))
            else:
                value = self._clean(project_id)
            if not value or value in wanted:
                continue
            self._validate_project(value, projects=list(catalog.values()))
            wanted.append(value)

        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            previous = {project.project_id: project for project in company.projects}
            projects: List[CompanyProject] = []
            added: List[str] = []
            removed: List[str] = []
            for project_id in wanted:
                project = previous.get(project_id)
                if project is not None and project_id in include_children:
                    project.include_children = include_children[project_id]
                if project is None:
                    discovered = catalog.get(project_id, {})
                    project = CompanyProject(
                        project_id=project_id,
                        name=discovered.get("project_name") or project_id,
                        role="",
                        include_children=include_children.get(project_id, True),
                    )
                    added.append(project_id)
                projects.append(project)
            for project_id in previous:
                if project_id not in wanted:
                    removed.append(project_id)
            company.projects = projects
            # From now on the user owns the project list.
            company.auto_sync_projects = False
            company.all_projects = False
            # Detach removed projects from teams (missions keep their reference
            # and are handled by the dedicated mission routes).
            for team in company.teams:
                team.project_ids = [
                    item for item in team.project_ids if item in wanted
                ]
            saved = self.store.save(company)
            if added or removed:
                self._event(
                    saved,
                    "projects.updated",
                    f"Project list updated (+{len(added)} / -{len(removed)})",
                    actor=actor,
                    added=added,
                    removed=removed,
                )
            return saved

    # ------------------------------------------------------------------
    # Coworking spaces
    # ------------------------------------------------------------------

    #: Fields a space update may touch. The id and bookkeeping stay internal.
    SPACE_EDITABLE_FIELDS = (
        "name",
        "description",
        "project_ids",
        "folders",
        "color",
        "icon",
    )

    def _space_payload(
        self,
        company: Company,
        space: CompanySpace,
        catalog: Dict[str, Dict[str, Any]],
        include_stats: bool = False,
    ) -> Dict[str, Any]:
        """Build the API representation of one space."""
        projects = []
        for project_id in space.project_ids:
            project = catalog.get(project_id) or {}
            root = str(project.get("project_path") or "")
            projects.append(
                {
                    "project_id": project_id,
                    "name": project.get("project_name") or project_id,
                    "available": bool(root and os.path.isdir(root)),
                }
            )
        payload = {
            **space.model_dump(),
            "project_count": len(space.project_ids),
            "folder_count": len(space.folders),
            "projects": projects,
        }
        if include_stats:
            payload["stats"] = space_stats(
                self.store.company_dir(company.id), space
            )
        return payload

    def _space_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Return the project catalog keyed by project id."""
        return {
            project["project_id"]: project
            for project in self._project_catalog()
        }

    def _validate_space_name(
        self, company: Company, name: str, exclude_id: str = ""
    ) -> str:
        """Require a non-empty, unique space name."""
        value = self._clean(name)
        if not value:
            raise CompanyValidationError("The space needs a name")
        for space in company.spaces:
            if space.id == exclude_id:
                continue
            if space.name.strip().lower() == value.lower():
                raise CompanyValidationError(
                    f"A space named '{value}' already exists in this company"
                )
        return value

    def _validate_space_projects(
        self, company: Company, project_ids: List[str]
    ) -> List[str]:
        """Keep only projects the company can see, rejecting unknown ids."""
        wanted: List[str] = []
        visible = set(self.visible_project_ids(company))
        for project_id in project_ids or []:
            value = self._clean(project_id)
            if not value or value in wanted:
                continue
            if value not in visible:
                raise CompanyValidationError(
                    f"Project '{value}' is not linked to this company. "
                    "Link it in Projects before mapping it into a space."
                )
            wanted.append(value)
        return wanted

    def _normalize_space_folders(
        self, company_id: str, folders: Iterable[Any]
    ) -> List[CompanySpaceFolder]:
        """Validate the host folders mapped into a space."""
        company_dir = self.store.company_dir(company_id)
        result: List[CompanySpaceFolder] = []
        seen_paths = set()
        for entry in folders or []:
            try:
                if isinstance(entry, CompanySpaceFolder):
                    folder = entry.model_copy(deep=True)
                elif isinstance(entry, str):
                    folder = CompanySpaceFolder(path=entry)
                elif isinstance(entry, dict):
                    folder = CompanySpaceFolder(**entry)
                else:
                    raise CompanyValidationError(
                        "a mapped folder must be a path or an object with a path"
                    )
            except ValueError as ex:
                raise CompanyValidationError(str(ex)) from ex
            resolved = validate_mapped_folder(folder.path, company_dir)
            if resolved in seen_paths:
                raise CompanyValidationError(
                    f"The folder '{folder.name}' is mapped twice"
                )
            seen_paths.add(resolved)
            folder.path = resolved
            if not folder.id:
                folder.id = new_id("folder")
            if not folder.name:
                folder.name = os.path.basename(resolved.rstrip("/")) or "folder"
            result.append(folder)
        return result

    def _default_space_projects(self, company: Company) -> List[str]:
        """Projects a freshly created default space maps."""
        direct = [project.project_id for project in company.projects]
        if direct or not company.all_projects:
            return direct
        return list(self.visible_project_ids(company))

    def _create_default_space(self, company: Company) -> Company:
        """Create the first space of a company (idempotent, caller locks)."""
        if company.spaces:
            return company
        project_ids = self._default_space_projects(company)
        space = CompanySpace(
            name="Company files",
            description=(
                "Shared files of the company. Projects linked to the company "
                "are shown here as read-only virtual folders."
            ),
            project_ids=project_ids,
        )
        ensure_space_folder(self.store.company_dir(company.id), space)
        company.spaces = [space]
        company.updated_at = now_iso()
        saved = self.store.save(company)
        self._event(
            saved,
            "space.created",
            f"Space '{space.name}' created",
            actor="system",
            space_id=space.id,
            projects=project_ids,
        )
        return saved

    def ensure_default_space(
        self, company_id: str, actor: str = "system"
    ) -> Company:
        """Make sure the company has at least one coworking space."""
        company = self.get_company(company_id)
        if company.spaces:
            return company
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            if not company.spaces:
                company = self._create_default_space(company)
            return company

    def _resolve_space(
        self, company_id: str, space_id: str = ""
    ) -> Tuple[Company, CompanySpace]:
        """Load a space (creating the default one when the company has none)."""
        company = self.ensure_default_space(company_id)
        space = (
            company.space(space_id)
            if space_id
            else (company.spaces[0] if company.spaces else None)
        )
        if space is None:
            raise CompanyNotFound(
                f"Space '{space_id}' does not exist in this company"
            )
        return company, space

    def list_spaces(
        self, company_id: str, include_stats: bool = False
    ) -> List[Dict[str, Any]]:
        """
        List the coworking spaces of a company.

        The first call of a company that predates spaces creates the default
        "Company files" space, so every company can browse its own files.

        Args:
            company_id: Company id.
            include_stats: Include file count/size of the company files.

        Returns:
            Space payloads ready for the file browser.
        """
        company = self.ensure_default_space(company_id)
        catalog = self._space_catalog()
        return [
            self._space_payload(
                company, space, catalog, include_stats=include_stats
            )
            for space in company.spaces
        ]

    def get_space(self, company_id: str, space_id: str) -> Dict[str, Any]:
        """Return one space payload."""
        company, space = self._resolve_space(company_id, space_id)
        return self._space_payload(
            company, space, self._space_catalog(), include_stats=True
        )

    def add_space(
        self,
        company_id: str,
        payload: Any,
        actor: str = "system",
    ) -> Dict[str, Any]:
        """
        Create a coworking space owned by the company.

        Args:
            company_id: Company id.
            payload: ``CompanySpace`` or a dictionary with its editable fields.
            actor: Username recorded in the audit trail.

        Returns:
            The created space payload.
        """
        values = (
            payload.model_dump() if isinstance(payload, CompanySpace) else dict(payload or {})
        )
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            name = self._validate_space_name(company, values.get("name", ""))
            project_ids = self._validate_space_projects(
                company, values.get("project_ids") or []
            )
            folders = self._normalize_space_folders(
                company_id, values.get("folders") or []
            )
            space = CompanySpace(
                name=name,
                description=self._clean(values.get("description")),
                project_ids=project_ids,
                folders=folders,
                color=self._clean(values.get("color")) or "#6366f1",
                icon=self._clean(values.get("icon"))
                or "fa-solid fa-folder-tree",
            )
            ensure_space_folder(self.store.company_dir(company.id), space)
            company.spaces.append(space)
            company.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "space.created",
                f"Space '{space.name}' created",
                actor=actor,
                space_id=space.id,
                projects=project_ids,
            )
            return self._space_payload(
                saved,
                saved.space(space.id) or space,
                self._space_catalog(),
                include_stats=True,
            )

    def update_space(
        self,
        company_id: str,
        space_id: str,
        payload: Any,
        actor: str = "system",
    ) -> Dict[str, Any]:
        """
        Update the name, mappings and presentation of a space.

        Args:
            company_id: Company id.
            space_id: Space id.
            payload: Dictionary (or ``CompanySpace``) with editable fields.
            actor: Username recorded in the audit trail.

        Returns:
            The updated space payload.
        """
        values = (
            payload.model_dump() if isinstance(payload, CompanySpace) else dict(payload or {})
        )
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            space = company.space(space_id)
            if space is None:
                raise CompanyNotFound(
                    f"Space '{space_id}' does not exist in this company"
                )
            changes = {
                key: value
                for key, value in values.items()
                if key in self.SPACE_EDITABLE_FIELDS and value is not None
            }
            if "name" in changes:
                changes["name"] = self._validate_space_name(
                    company, changes["name"], exclude_id=space.id
                )
            if "project_ids" in changes:
                changes["project_ids"] = self._validate_space_projects(
                    company, changes["project_ids"] or []
                )
            if "folders" in changes:
                changes["folders"] = self._normalize_space_folders(
                    company_id, changes["folders"] or []
                )
            if "description" in changes:
                changes["description"] = self._clean(changes["description"])
            if "color" in changes:
                changes["color"] = self._clean(changes["color"]) or "#6366f1"
            if "icon" in changes:
                changes["icon"] = (
                    self._clean(changes["icon"]) or "fa-solid fa-folder-tree"
                )
            for key, value in changes.items():
                setattr(space, key, value)
            space.slug = slugify(space.name, "space")
            space.updated_at = now_iso()
            company.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "space.updated",
                f"Space '{space.name}' updated",
                actor=actor,
                space_id=space.id,
                fields=sorted(changes),
            )
            return self._space_payload(
                saved,
                saved.space(space.id) or space,
                self._space_catalog(),
                include_stats=True,
            )

    def delete_space(
        self, company_id: str, space_id: str, actor: str = "system"
    ) -> Dict[str, Any]:
        """
        Delete a space and move its company files to the company trash.

        Args:
            company_id: Company id.
            space_id: Space id.
            actor: Username recorded in the audit trail.

        Returns:
            ``{deleted: True, space_id, name}``.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            space = company.space(space_id)
            if space is None:
                raise CompanyNotFound(
                    f"Space '{space_id}' does not exist in this company"
                )
            company.spaces = [
                item for item in company.spaces if item.id != space_id
            ]
            company.updated_at = now_iso()
            saved = self.store.save(company)
            cleanup_space_files(self.store.company_dir(company.id), space_id)
            self._event(
                saved,
                "space.deleted",
                f"Space '{space.name}' deleted",
                actor=actor,
                space_id=space_id,
            )
            return {"deleted": True, "space_id": space_id, "name": space.name}

    # -- space file browser (company files + virtual project folders) ----

    def space_listing(
        self, company_id: str, space_id: str = "", path: str = ""
    ) -> Dict[str, Any]:
        """List one virtual folder of a space."""
        company, space = self._resolve_space(company_id, space_id)
        return space_list_path(
            self.store.company_dir(company.id),
            space,
            path,
            self._project_catalog(),
        )

    def space_entry(
        self, company_id: str, space_id: str = "", path: str = ""
    ) -> Dict[str, Any]:
        """Resolve one file/folder of a space to its real host path."""
        company, space = self._resolve_space(company_id, space_id)
        return space_resolve_entry(
            self.store.company_dir(company.id),
            space,
            path,
            self._project_catalog(),
        )

    def space_create_folder(
        self, company_id: str, space_id: str = "", path: str = "", name: str = ""
    ) -> Dict[str, Any]:
        """Create a folder inside the company files of a space."""
        company, space = self._resolve_space(company_id, space_id)
        result = space_create_folder(
            self.store.company_dir(company.id), space, path, name
        )
        self._event(
            company,
            "space.folder_created",
            f"Folder '{name}' created in space '{space.name}'",
            space_id=space.id,
            path=result["path"],
        )
        return result

    def space_delete_entry(
        self, company_id: str, space_id: str = "", path: str = ""
    ) -> Dict[str, Any]:
        """Delete a company file (or an empty folder) of a space."""
        company, space = self._resolve_space(company_id, space_id)
        result = space_delete_entry(
            self.store.company_dir(company.id), space, path
        )
        self._event(
            company,
            "space.file_deleted",
            f"Deleted '{result['path']}' from space '{space.name}'",
            space_id=space.id,
            path=result["path"],
        )
        return result

    def space_write_text(
        self,
        company_id: str,
        space_id: str = "",
        path: str = "",
        content: str = "",
    ) -> Dict[str, Any]:
        """Create or replace a text file in the company files of a space."""
        company, space = self._resolve_space(company_id, space_id)
        return space_write_text(
            self.store.company_dir(company.id), space, path, content
        )

    def space_upload(
        self,
        company_id: str,
        space_id: str = "",
        path: str = "",
        filename: str = "",
        content: bytes = b"",
    ) -> Dict[str, Any]:
        """Store one uploaded file in the company files of a space."""
        company, space = self._resolve_space(company_id, space_id)
        result = space_save_upload(
            self.store.company_dir(company.id),
            space,
            path,
            filename,
            content,
        )
        self._event(
            company,
            "space.file_uploaded",
            f"Uploaded '{result['name']}' to space '{space.name}'",
            space_id=space.id,
            path=result["path"],
        )
        return result

    # ------------------------------------------------------------------
    # Company chats
    # ------------------------------------------------------------------

    def _default_chat_manager(self, project_id: str) -> Any:
        """Build the real ``ChatManager`` of a linked project (lazy import)."""
        settings = self._project_settings_provider(project_id)
        if settings is None:
            raise CompanyDispatchError(
                f"Project '{project_id}' is not available (not discovered)"
            )
        from codx.junior.chat_manager import ChatManager

        return ChatManager(settings=settings)

    def _chat_manager_for(self, project_id: str) -> Any:
        """Return the chat manager used to read/write a company channel."""
        try:
            manager = self._chat_manager_provider(project_id)
        except CompanyDispatchError:
            raise
        except Exception as ex:
            raise CompanyDispatchError(
                f"Chats are not available for project '{project_id}': {ex}"
            ) from ex
        if manager is None:
            raise CompanyDispatchError(
                f"Project '{project_id}' has no chat storage available"
            )
        return manager

    @staticmethod
    def _chat_summary(chat: Any) -> Dict[str, Any]:
        """Serialize a hosted Chat for the company channel list."""
        messages = list(getattr(chat, "messages", None) or [])
        last = messages[-1] if messages else None
        return {
            "id": getattr(chat, "id", ""),
            "name": getattr(chat, "name", "") or "",
            "description": getattr(chat, "description", "") or "",
            "mode": getattr(chat, "mode", "") or "",
            "profiles": list(getattr(chat, "profiles", None) or []),
            "users": list(getattr(chat, "users", None) or []),
            "updated_at": getattr(chat, "updated_at", "") or "",
            "message_count": len(messages),
            "last_message": (
                last.model_dump()
                if hasattr(last, "model_dump")
                else (last if isinstance(last, dict) else None)
            ),
        }

    def _company_chat_ref(self, company: Company, chat_id: str) -> CompanyChat:
        """Find a company channel reference or raise."""
        chat_id = self._clean(chat_id)
        ref = next((item for item in company.chats if item.chat_id == chat_id), None)
        if ref is None:
            raise CompanyNotFound(f"Company chat '{chat_id}' not found")
        return ref

    def list_company_chats(self, company_id: str) -> List[Dict[str, Any]]:
        """
        List the company channels with their hosted chat summary.

        A channel whose project/chat disappeared is still listed with
        ``available=false``; the company document keeps the reference until a
        human removes it.
        """
        company = self.get_company(company_id)
        result: List[Dict[str, Any]] = []
        for ref in company.chats:
            entry = {**ref.model_dump(), "available": False, "error": ""}
            try:
                manager = self._chat_manager_for(ref.project_id)
                chat = manager.find_by_id(ref.chat_id)
                if chat is not None:
                    entry["available"] = True
                    entry["chat"] = self._chat_summary(chat)
                    # The hosted Chat is the source of truth for the name:
                    # ChatView can rename it without going through this API.
                    entry["name"] = chat.name or entry.get("name", "")
                    entry["description"] = (
                        getattr(chat, "description", "") or entry.get("description", "")
                    )
            except Exception as ex:
                entry["error"] = str(ex)
            result.append(entry)
        def sort_key(item: Dict[str, Any]):
            updated = parse_iso(item.get("updated_at"))
            return (
                not item.get("pinned", False),
                -(updated.timestamp() if updated else 0),
                (item.get("name") or "").lower(),
            )

        return sorted(result, key=sort_key)

    def create_company_chat(
        self,
        company_id: str,
        name: str,
        description: str = "",
        team_id: str = "",
        project_id: str = "",
        actor: str = "system",
    ) -> Company:
        """
        Create a company channel as a normal ``mode='channel'`` Chat.

        The chat is hosted by one of the company's linked projects, so messages,
        mentions, attachments, unread counters and AI replies use the existing
        Messenger machinery. The company document stores the reference.
        """
        name = self._clean(name)
        if not name:
            raise CompanyValidationError("channel name is required")
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            project_id = self._clean(project_id) or (
                company.projects[0].project_id if company.projects else ""
            )
            if not project_id:
                raise CompanyConflict(
                    "Link a project before creating a company channel: the "
                    "messages need a project to live in."
                )
            if company.project(project_id) is None:
                raise CompanyValidationError(
                    f"Project '{project_id}' is not linked to this company"
                )
            # A linked project can disappear from discovery (folder removed,
            # moved, or the discovery scan did not reach it). Fail with an
            # actionable conflict instead of letting the chat manager raise a
            # technical "not available (not discovered)" 502: the owner picked
            # this project from the selector and needs to know what to fix.
            if self.project_settings(project_id) is None:
                raise CompanyConflict(
                    f"Project '{project_id}' is linked to this company but this "
                    "workspace has not discovered it, so its chats cannot be "
                    "stored. Check that the project folder still exists on this "
                    "host, then refresh the project list and try again."
                )
            team = company.team(team_id) if team_id else None
            if team_id and team is None:
                raise CompanyValidationError(f"Team '{team_id}' does not exist")
            profiles = team.profile_names if team else []
            users = team.user_names if team else []
            try:
                from codx.junior.db import Chat

                chat = Chat(
                    name=name,
                    description=self._clean(description),
                    mode="channel",
                    board="messenger",
                    column="channels",
                    profiles=profiles,
                    users=users,
                    owner_project_id=project_id,
                    project_id=project_id,
                    tags=[
                        "company",
                        f"company:{company.id}",
                        *([f"company-team:{team.id}"] if team else []),
                    ],
                    meta_data={
                        "company_id": company.id,
                        "company_team_id": team.id if team else "",
                    },
                )
                saved = self._chat_manager_for(project_id).save_chat(chat=chat)
            except CompanyDispatchError:
                raise
            except Exception as ex:
                raise CompanyDispatchError(
                    f"Could not create the company channel: {ex}"
                ) from ex
            ref = CompanyChat(
                chat_id=saved.id,
                project_id=project_id,
                name=saved.name or name,
                description=self._clean(description),
                team_id=team.id if team else "",
                created_by=actor,
            )
            company.chats.append(ref)
            saved_company = self.store.save(company)
            self._event(
                saved_company,
                "chat.created",
                f"Channel '{ref.name}' created in project '{project_id}'",
                actor=actor,
                chat_id=ref.chat_id,
                project_id=project_id,
                team_id=ref.team_id,
            )
            return saved_company

    def update_company_chat(
        self,
        company_id: str,
        chat_id: str,
        patch: Any,
        actor: str = "system",
    ) -> Company:
        """Update a company channel (name, description, team, pinned)."""
        values = self._patch_dict(patch)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            ref = self._company_chat_ref(company, chat_id)
            if "name" in values:
                name = self._clean(values["name"])
                if not name:
                    raise CompanyValidationError("channel name is required")
                values["name"] = name
            team_id = values.get("team_id", ref.team_id)
            team = company.team(team_id) if team_id else None
            if team_id and team is None:
                raise CompanyValidationError(f"Team '{team_id}' does not exist")
            changed = self._apply(
                ref, values, ("name", "description", "team_id", "pinned")
            )
            if team_id != ref.team_id:
                ref.team_id = team_id
            ref.updated_at = now_iso()
            # Best effort: keep the hosted chat in sync; a missing chat keeps
            # the company reference editable.
            try:
                manager = self._chat_manager_for(ref.project_id)
                chat = manager.find_by_id(ref.chat_id)
                if chat is not None:
                    chat.name = ref.name
                    chat.description = ref.description
                    chat.profiles = team.profile_names if team else []
                    chat.users = team.user_names if team else []
                    manager.save_chat(chat=chat, chat_only=True)
            except Exception as ex:
                logger.warning("Company chat '%s' could not be updated: %s", chat_id, ex)
            saved = self.store.save(company)
            if changed:
                self._event(
                    saved,
                    "chat.updated",
                    f"Channel '{ref.name}' updated: {', '.join(changed)}",
                    actor=actor,
                    chat_id=chat_id,
                )
            return saved

    def sync_company_chat_members(
        self, company_id: str, chat_id: str, actor: str = "system"
    ) -> Company:
        """Copy the owning team roster (humans + AI profiles) into the chat."""
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            ref = self._company_chat_ref(company, chat_id)
            team = company.team(ref.team_id) if ref.team_id else None
            if team is None:
                raise CompanyConflict(
                    f"Channel '{ref.name}' has no owning team to sync from"
                )
            manager = self._chat_manager_for(ref.project_id)
            chat = manager.find_by_id(ref.chat_id)
            if chat is None:
                raise CompanyNotFound(
                    f"Hosted chat '{ref.chat_id}' no longer exists"
                )
            chat.profiles = team.profile_names
            chat.users = team.user_names
            manager.save_chat(chat=chat, chat_only=True)
            ref.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "chat.members.synced",
                f"Channel '{ref.name}' synced with team '{team.name}'",
                actor=actor,
                chat_id=chat_id,
                team_id=team.id,
            )
            return saved

    def delete_company_chat(
        self, company_id: str, chat_id: str, actor: str = "system"
    ) -> Company:
        """Delete a company channel reference and its hosted chat."""
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            ref = self._company_chat_ref(company, chat_id)
            try:
                self._chat_manager_for(ref.project_id).delete_chat(chat_id=ref.chat_id)
            except Exception as ex:
                logger.warning("Company chat '%s' file not deleted: %s", chat_id, ex)
            company.chats = [item for item in company.chats if item.chat_id != chat_id]
            saved = self.store.save(company)
            self._event(
                saved,
                "chat.deleted",
                f"Channel '{ref.name}' deleted",
                actor=actor,
                chat_id=chat_id,
                project_id=ref.project_id,
            )
            return saved

    # ------------------------------------------------------------------
    # Missions
    # ------------------------------------------------------------------

    def add_mission(
        self, company_id: str, mission: Any, actor: str = "system"
    ) -> Company:
        """
        Add a mission to the company.

        Args:
            company_id: Company id.
            mission: :class:`Mission` or mapping.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        payload = self._patch_dict(mission)
        payload["id"] = new_id("mission")
        payload.setdefault("created_by", actor)
        explicit_dispatch = "dispatch_mode" in payload
        try:
            company_mission = Mission(**payload)
        except Exception as ex:
            raise CompanyValidationError(str(ex)) from ex
        if company_mission.status in ("running", "paused", "review", "done"):
            raise CompanyValidationError(
                f"A new mission cannot start in '{company_mission.status}'. "
                "Create it as draft/ready and dispatch it."
            )
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            if not explicit_dispatch:
                # A goal created while the company runs autonomously (or owned
                # by an autonomous team) starts itself. Without this inference
                # every goal stayed "manual" and the autopilot never saw it.
                team = company.team(company_mission.team_id)
                if (
                    (company.autonomy.enabled and company.autonomy.level != "off")
                    or (team is not None and team.autonomy == "autonomous")
                ):
                    company_mission.dispatch_mode = "auto"
            self._validate_mission_destination(company, company_mission)
            if (
                self._mission_needs_approval(company, company_mission)
                and company_mission.approval_status == "not_required"
            ):
                company_mission.approval_status = "pending"
            company.missions.append(company_mission)
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.created",
                f"Mission '{company_mission.title}' created",
                actor=actor,
                mission_id=company_mission.id,
                team_id=company_mission.team_id,
                project_id=company_mission.project_id,
            )
            return saved

    def update_mission(
        self,
        company_id: str,
        mission_id: str,
        patch: Any,
        actor: str = "system",
    ) -> Company:
        """
        Update a mission. Status changes go through :meth:`transition_mission`.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            patch: Mapping with fields to change.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        values = self._patch_dict(patch)
        status = values.pop("status", None)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            self._apply(mission, values, MISSION_EDITABLE_FIELDS)
            if status and status != mission.status:
                if not can_transition(mission.status, status):
                    raise CompanyValidationError(
                        f"Mission cannot move from '{mission.status}' to '{status}'. "
                        f"Allowed: {', '.join(MISSION_TRANSITIONS.get(mission.status, [])) or 'none'}"
                    )
                if status == "running" and not mission.run_id:
                    raise CompanyConflict(
                        "Use dispatch_mission to move a mission to running: a "
                        "running mission needs an orchestration run."
                    )
                mission.status = status
                if status == "done":
                    mission.completed_at = now_iso()
            self._validate_mission_destination(company, mission)
            mission.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.updated",
                f"Mission '{mission.title}' updated",
                actor=actor,
                mission_id=mission_id,
                status=mission.status,
            )
            return saved

    def delete_mission(
        self,
        company_id: str,
        mission_id: str,
        actor: str = "system",
        force: bool = False,
    ) -> Company:
        """
        Delete a mission.

        A running mission must be stopped (or forced) first, so deleting a card
        never leaves an orphan run spending money in a project.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            actor: Username recorded in the audit trail.
            force: Allow deleting a running mission (the run is not cancelled).

        Returns:
            The updated company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if mission.status == "running" and not force:
                raise CompanyConflict(
                    f"Mission '{mission.title}' is running. Pause/stop it first "
                    "or force the delete."
                )
            company.missions = [
                item for item in company.missions if item.id != mission_id
            ]
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.deleted",
                f"Mission '{mission.title}' deleted",
                actor=actor,
                mission_id=mission_id,
                run_id=mission.run_id,
            )
            return saved

    def dispatch_mission(
        self,
        company_id: str,
        mission_id: str,
        actor: str = "system",
        force: bool = False,
    ) -> Company:
        """
        Dispatch a mission to its project's durable orchestration runtime.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            actor: Username recorded in the audit trail.
            force: Re-dispatch a mission that is already running.

        Returns:
            The updated company (mission now ``running`` with a ``run_id``).

        Raises:
            CompanyDispatchError: When the run cannot be created; the mission is
                kept in its previous state with ``last_error`` set.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if mission.status == "running" and mission.run_id and not force:
                raise CompanyConflict(
                    f"Mission '{mission.title}' is already running "
                    f"(run {mission.run_id})"
                )
            if mission.status not in ("ready", "blocked", "running"):
                raise CompanyConflict(
                    f"Mission '{mission.title}' is '{mission.status}'; only ready "
                    "or blocked missions can be dispatched (move a paused mission "
                    "back to ready first)"
                )
            self._validate_mission_destination(company, mission)
            if (
                self._mission_needs_approval(company, mission)
                and mission.approval_status != "approved"
            ):
                raise CompanyConflict(
                    f"Mission '{mission.title}' needs approval before it can be "
                    f"dispatched (status: {mission.approval_status})."
                )
            self._ensure_dispatch_budget(company, mission, force=force)
            brief = self.build_mission_brief(company, mission)
            try:
                result = self._dispatcher(company, mission, brief) or {}
            except Exception as ex:
                message = str(ex) or ex.__class__.__name__
                mission.last_error = message
                mission.updated_at = now_iso()
                saved = self.store.save(company)
                self._event(
                    saved,
                    "mission.dispatch_failed",
                    f"Mission '{mission.title}' could not be dispatched: {message}",
                    actor=actor,
                    mission_id=mission_id,
                    error=message,
                )
                logger.warning("Company dispatch failed: %s", message)
                raise CompanyDispatchError(message) from ex

            mission.run_id = self._clean(result.get("run_id"))
            mission.run_status = self._clean(result.get("status") or "running")
            mission.status = "running"
            mission.dispatched_at = now_iso()
            mission.updated_at = mission.dispatched_at
            mission.last_error = ""
            mission.progress = 0
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.dispatched",
                f"Mission '{mission.title}' dispatched to project "
                f"'{mission.project_id}' (run {mission.run_id or 'unknown'})",
                actor=actor,
                mission_id=mission_id,
                run_id=mission.run_id,
                project_id=mission.project_id,
                team_id=mission.team_id,
            )
            logger.info(
                "Company mission dispatched: %s (%s) run=%s",
                mission.title,
                mission.id,
                mission.run_id,
            )
            return saved

    def transition_mission(
        self,
        company_id: str,
        mission_id: str,
        status: str,
        actor: str = "system",
        force: bool = False,
    ) -> Company:
        """
        Move a mission through the explicit status machine.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            status: Target status.
            actor: Username recorded in the audit trail.
            force: Skip the transition table (used by admins to repair state).

        Returns:
            The updated company.
        """
        status = self._clean(status).lower()
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if not force and not can_transition(mission.status, status):
                raise CompanyValidationError(
                    f"Mission cannot move from '{mission.status}' to '{status}'. "
                    f"Allowed: {', '.join(MISSION_TRANSITIONS.get(mission.status, [])) or 'none'}"
                )
            if status == "running" and not mission.run_id:
                raise CompanyConflict(
                    "Use dispatch_mission to move a mission to running: a running "
                    "mission needs an orchestration run."
                )
            previous = mission.status
            mission.status = status
            mission.updated_at = now_iso()
            if status == "done":
                mission.completed_at = now_iso()
                mission.progress = 100
            if status == "cancelled" and mission.run_id and previous == "running":
                mission.last_error = (
                    "Mission cancelled; the orchestration run was not stopped."
                )
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.transitioned",
                f"Mission '{mission.title}': {previous} → {status}",
                actor=actor,
                mission_id=mission_id,
                previous=previous,
                status=status,
            )
            return saved

    def accept_mission(
        self,
        company_id: str,
        mission_id: str,
        result: str = "",
        actor: str = "system",
    ) -> Company:
        """
        Accept a mission in review, marking it done.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            result: Human summary of the outcome.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if mission.status not in ("review", "running"):
                raise CompanyConflict(
                    f"Mission '{mission.title}' is '{mission.status}', not in review"
                )
            mission.status = "done"
            mission.progress = 100
            mission.completed_at = now_iso()
            mission.result = self._clean(result) or mission.result or "Accepted"
            mission.updated_at = mission.completed_at
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.accepted",
                f"Mission '{mission.title}' accepted",
                actor=actor,
                mission_id=mission_id,
                run_id=mission.run_id,
                result=mission.result[:500],
            )
            return saved

    def reject_mission(
        self,
        company_id: str,
        mission_id: str,
        reason: str = "",
        actor: str = "system",
    ) -> Company:
        """
        Reject a mission in review, returning it to ``ready``.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            reason: Why the outcome is not acceptable (kept for the next run).
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if mission.status not in ("review", "running", "blocked"):
                raise CompanyConflict(
                    f"Mission '{mission.title}' is '{mission.status}', not in review"
                )
            previous_run = mission.run_id
            mission.status = "ready"
            mission.progress = 0
            mission.run_id = ""
            mission.run_status = ""
            mission.last_error = self._clean(reason)
            mission.updated_at = now_iso()
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.rejected",
                f"Mission '{mission.title}' returned to ready",
                actor=actor,
                mission_id=mission_id,
                previous_run_id=previous_run,
                reason=mission.last_error[:500],
            )
            return saved

    def approve_mission(
        self,
        company_id: str,
        mission_id: str,
        note: str = "",
        actor: str = "system",
    ) -> Company:
        """
        Approve a mission that is gated by a mission/team approval policy.

        Approval does not dispatch the mission: it only removes the gate, so a
        human keeps control of *when* the run starts.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            note: Optional approval note.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if mission.status in ("done", "cancelled"):
                raise CompanyConflict(
                    f"Mission '{mission.title}' is '{mission.status}' and no "
                    "longer needs approval"
                )
            mission.approval_status = "approved"
            mission.approved_by = actor
            mission.approved_at = now_iso()
            mission.approval_note = self._clean(note)
            mission.updated_at = mission.approved_at
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.approved",
                f"Mission '{mission.title}' approved",
                actor=actor,
                mission_id=mission_id,
                note=mission.approval_note[:500],
            )
            return saved

    def deny_mission(
        self,
        company_id: str,
        mission_id: str,
        note: str = "",
        actor: str = "system",
    ) -> Company:
        """
        Deny a gated mission; it cannot be dispatched until approved.

        Args:
            company_id: Company id.
            mission_id: Mission id.
            note: Why it was denied.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            mission = company.get_mission(mission_id)
            if mission is None:
                raise CompanyNotFound(f"Mission '{mission_id}' not found")
            if mission.status in ("done", "cancelled"):
                raise CompanyConflict(
                    f"Mission '{mission.title}' is '{mission.status}' and no "
                    "longer needs approval"
                )
            mission.approval_status = "denied"
            mission.approved_by = actor
            mission.approved_at = now_iso()
            mission.approval_note = self._clean(note)
            mission.updated_at = mission.approved_at
            saved = self.store.save(company)
            self._event(
                saved,
                "mission.denied",
                f"Mission '{mission.title}' denied",
                actor=actor,
                mission_id=mission_id,
                note=mission.approval_note[:500],
            )
            return saved

    # ------------------------------------------------------------------
    # Autonomy
    # ------------------------------------------------------------------

    def update_autonomy(
        self,
        company_id: str,
        patch: Any,
        actor: str = "system",
    ) -> Company:
        """
        Configure the autopilot policy.

        Args:
            company_id: Company id.
            patch: Mapping with the policy fields to change.
            actor: Username recorded in the audit trail.

        Returns:
            The updated company.
        """
        values = self._patch_dict(patch)
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            changed = self._apply(company.autonomy, values, AUTONOMY_EDITABLE_FIELDS)
            if company.autonomy.level == "off":
                company.autonomy.enabled = False
            elif "enabled" not in values and "level" in values:
                # Choosing a level is the user turning the autopilot on. Before,
                # clicking "Autonomous" left `enabled=False`, so the goal panel
                # said autonomous while nothing was ever dispatched.
                company.autonomy.enabled = True
            if company.autonomy.last_cycle_at:
                last = parse_iso(company.autonomy.last_cycle_at) or self._now()
                company.autonomy.next_cycle_at = (
                    last + timedelta(minutes=company.autonomy.interval_minutes)
                ).isoformat()
            company.updated_at = now_iso()
            saved = self.store.save(company)
            if changed:
                self._event(
                    saved,
                    "autonomy.updated",
                    f"Autopilot updated: {', '.join(changed)}",
                    actor=actor,
                    enabled=saved.autonomy.enabled,
                    level=saved.autonomy.level,
                )
            return saved

    def sync_missions(self, company_id: str, actor: str = "system") -> Dict[str, Any]:
        """
        Mirror the orchestration runs back onto the company missions.

        Args:
            company_id: Company id.
            actor: Username recorded for status events.

        Returns:
            ``{"company_id", "checked", "changed", "missions": [...]}``.
        """
        with self.store.lock(self._company_id(company_id)):
            company = self.get_company(company_id)
            changed: List[Dict[str, Any]] = []
            checked = 0
            spend_changed = False
            for mission in company.missions:
                if not mission.run_id or mission.status in ("done", "cancelled"):
                    continue
                checked += 1
                try:
                    run = self._run_status_provider(mission.project_id, mission.run_id)
                except Exception as ex:
                    logger.debug(
                        "Company: run %s not readable: %s", mission.run_id, ex
                    )
                    continue
                if not run:
                    continue
                status = self._clean(run.get("status"))
                progress = run.get("progress")
                if progress is not None:
                    mission.progress = max(0, min(100, int(progress)))
                mission.run_status = status or mission.run_status

                # The run's cumulative cost is the source of truth. Only the
                # positive delta goes to the ledger, so re-syncing the same run
                # never double counts; a run that reports a lower value (reset)
                # adjusts the mission without a negative entry.
                spent = run.get("spent_cxjcoins")
                if spent is not None:
                    try:
                        observed = max(0.0, float(spent))
                    except (TypeError, ValueError):
                        observed = mission.spent_cxjcoins
                    delta = round(observed - mission.spent_cxjcoins, 6)
                    if delta > 0:
                        company.spend.append(
                            SpendEntry(
                                at=self._now().isoformat(),
                                mission_id=mission.id,
                                project_id=mission.project_id,
                                run_id=mission.run_id,
                                amount=delta,
                                note=f"Run {mission.run_id} spend",
                            )
                        )
                        company.spent_cxjcoins = round(
                            company.spent_cxjcoins + delta, 6
                        )
                        mission.spent_cxjcoins = observed
                        mission.updated_at = self._now().isoformat()
                        spend_changed = True
                    elif observed < mission.spent_cxjcoins:
                        mission.spent_cxjcoins = observed
                        mission.updated_at = self._now().isoformat()
                        spend_changed = True

                target = self._mission_status_for_run(company, run)
                if target and target != mission.status:
                    previous = mission.status
                    mission.status = target
                    mission.updated_at = now_iso()
                    if target == "done":
                        mission.completed_at = now_iso()
                        mission.progress = 100
                    if target == "blocked":
                        mission.last_error = (
                            self._clean(run.get("error"))
                            or f"Run finished as '{status}'"
                        )
                    changed.append(
                        {
                            "id": mission.id,
                            "title": mission.title,
                            "previous": previous,
                            "status": target,
                            "run_status": status,
                        }
                    )
                    self._event(
                        company,
                        "mission.run_synced",
                        f"Mission '{mission.title}': {previous} → {target} "
                        f"(run {status or 'unknown'})",
                        actor=actor,
                        mission_id=mission.id,
                        run_id=mission.run_id,
                        run_status=status,
                    )
            if len(company.spend) > SPEND_LEDGER_LIMIT:
                # Keep the document bounded; the cumulative total is separate.
                company.spend = company.spend[-SPEND_LEDGER_LIMIT:]
                spend_changed = True
            if changed or spend_changed:
                self.store.save(company)
            return {
                "company_id": company.id,
                "checked": checked,
                "changed": len(changed),
                "spend_changed": spend_changed,
                "missions": changed,
                "budget": self.budget_state(company),
            }

    def cycle(
        self,
        company_id: str,
        actor: str = "autopilot",
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Run one autonomy cycle.

        The cycle first syncs the running missions, then dispatches up to
        ``max_missions_per_cycle`` ``auto`` missions while respecting
        ``max_concurrent_missions``. A disabled autopilot does nothing unless
        *force* is set (a human asking "run one cycle now").

        Args:
            company_id: Company id.
            actor: Username recorded in the journal.
            force: Run even when the policy says it is not due/enabled.

        Returns:
            A cycle report: ``status``, ``dispatched``, ``failed``, ``synced``
            and the resulting ``autonomy`` block.
        """
        now = self._now()
        company = self.get_company(company_id)
        if not force:
            if not company.autonomy.enabled or company.autonomy.level == "off":
                return {
                    "status": "disabled",
                    "company_id": company.id,
                    "message": "Autopilot is disabled. Enable it first.",
                    "autonomy": company.autonomy.model_dump(),
                }
            if not company.autonomy.is_due(now):
                return {
                    "status": "not_due",
                    "company_id": company.id,
                    "message": (
                        "Next cycle is due at "
                        f"{company.autonomy.next_cycle_at or 'unknown'}"
                    ),
                    "autonomy": company.autonomy.model_dump(),
                }

        synced = self.sync_missions(company.id, actor=actor)
        company = self.get_company(company.id)

        budget = self.budget_state(company, now=now)
        if budget["over_daily_budget"] and not force:
            message = (
                "Daily budget exhausted: "
                f"{budget['spent_today_cxjcoins']} / "
                f"{budget['daily_limit_cxjcoins']} cxjcoins spent today"
            )
            company.autonomy.mark_cycle(message, now=now)
            company.updated_at = now_iso()
            self.store.save(company)
            self._event(
                company,
                "autopilot.budget_exhausted",
                message,
                actor=actor,
                spent_today=budget["spent_today_cxjcoins"],
                daily_limit=budget["daily_limit_cxjcoins"],
            )
            return {
                "status": "budget_exhausted",
                "company_id": company.id,
                "message": message,
                "dispatched": [],
                "failed": [],
                "synced": synced,
                "budget": budget,
                "autonomy": company.autonomy.model_dump(),
            }

        candidates = self._dispatchable_missions(company)
        running = len(company.active_missions)
        slots = max(
            0,
            company.autonomy.max_concurrent_missions - running,
        )
        limit = min(slots, company.autonomy.max_missions_per_cycle)
        dispatched: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for mission in candidates[:limit]:
            try:
                self.dispatch_mission(company.id, mission.id, actor=actor)
                current = self.get_company(company.id)
                updated = current.get_mission(mission.id)
                dispatched.append(
                    {
                        "id": mission.id,
                        "title": mission.title,
                        "run_id": updated.run_id if updated else "",
                    }
                )
            except Exception as ex:
                failed.append({"id": mission.id, "title": mission.title, "error": str(ex)})
                logger.warning(
                    "Company cycle could not dispatch mission '%s': %s",
                    mission.title,
                    ex,
                )

        company = self.get_company(company.id)
        budget = self.budget_state(company, now=now)
        summary = (
            f"{len(dispatched)} dispatched, {len(failed)} failed, "
            f"{synced.get('changed', 0)} synced"
        )
        if budget["over_daily_budget"]:
            summary += " (over daily budget, forced)"
        company.autonomy.mark_cycle(summary, now=now)
        company.updated_at = now_iso()
        self.store.save(company)
        self._event(
            company,
            "autopilot.cycle",
            f"Autopilot cycle: {summary}",
            actor=actor,
            dispatched=dispatched,
            failed=failed,
            synced=synced.get("missions", []),
            forced=force,
        )
        return {
            "status": "ran",
            "company_id": company.id,
            "dispatched": dispatched,
            "failed": failed,
            "synced": synced,
            "budget": budget,
            "autonomy": company.autonomy.model_dump(),
            "summary": summary,
        }

    def sync_all_missions(self, actor: str = "autopilot-sync") -> List[Dict[str, Any]]:
        """
        Mirror every orchestration run onto its company mission.

        The autopilot cycle already syncs the companies it processes, but a
        company with the autopilot disabled still needs this: its runs are
        started by a human and would otherwise stay ``running`` forever after
        the project worker finished them. The background loop calls this on
        every tick, so mission state follows the run even when no cycle runs.

        Args:
            actor: Username recorded in the journal.

        Returns:
            One sync report per company (failures included).
        """
        reports: List[Dict[str, Any]] = []
        for summary in self.list_companies():
            company_id = summary["id"]
            try:
                reports.append(self.sync_missions(company_id, actor=actor))
            except Exception as ex:  # pragma: no cover - defensive
                logger.warning(
                    "Company sync failed for '%s': %s", company_id, ex
                )
        return reports

    def run_due_cycles(self, actor: str = "autopilot") -> List[Dict[str, Any]]:
        """
        Run a cycle for every company whose autopilot is due.

        Args:
            actor: Username recorded in the journals.

        Returns:
            One report per company that was due (failures included).
        """
        reports: List[Dict[str, Any]] = []
        for summary in self.list_companies():
            company_id = summary["id"]
            try:
                company = self.get_company(company_id)
            except Exception as ex:
                logger.warning("Company '%s' unreadable: %s", company_id, ex)
                continue
            if not company.autonomy.is_due(self._now()):
                continue
            try:
                reports.append(self.cycle(company_id, actor=actor))
            except Exception as ex:
                logger.exception(
                    "Company autopilot cycle failed for '%s': %s", company_id, ex
                )
                reports.append(
                    {
                        "status": "error",
                        "company_id": company_id,
                        "error": str(ex),
                    }
                )
        return reports

    # ------------------------------------------------------------------
    # Reads for the UI / tools
    # ------------------------------------------------------------------

    def child_projects(self, company: Company) -> List[Dict[str, Any]]:
        """
        Return the projects nested under the company's linked projects.

        A company maps top-level projects; the codx-junior installation may
        discover smaller projects inside them (``parent/sub-project``). They are
        not separate company mappings, but managers must be able to see them
        (and the assistant can include them in plans).

        Args:
            company: Company document.

        Returns:
            Child project descriptors with their parent reference.
        """
    @staticmethod
    def _is_path_child_of(child_path: str, parent_path: str) -> bool:
        """Return True when *child_path* is strictly inside *parent_path*."""
        child_path = os.path.normpath(child_path or "")
        parent_path = os.path.normpath(parent_path or "")
        if not child_path or not parent_path or child_path == parent_path:
            return False
        try:
            return os.path.commonpath([child_path, parent_path]) == parent_path
        except ValueError:
            return False

    def _descendant_projects(
        self, catalog: List[Dict[str, Any]], project_id: str
    ) -> List[Dict[str, Any]]:
        """Return every discovered project nested under *project_id* by path."""
        by_id = {project["project_id"]: project for project in catalog}
        parent = by_id.get(project_id)
        parent_path = (parent or {}).get("project_path") or ""
        if not parent_path:
            return []
        return [
            candidate
            for candidate in catalog
            if candidate.get("project_id") != project_id
            and self._is_path_child_of(
                candidate.get("project_path") or "", parent_path
            )
        ]

    def company_project_ids(self, company: Company) -> List[str]:
        """
        Return the projects the company defines as its own projects.

        These are the direct links shown by the Projects section, or the whole
        discovered catalogue when the company uses the ``all_projects``
        wildcard. Inherited child projects are not separate company projects;
        they are reached through the advanced workspace of their linked root.

        Args:
            company: Company document.

        Returns:
            Project ids in catalogue order (wildcard) or link order (selected).
        """
        if company.all_projects:
            return [
                project["project_id"]
                for project in self._project_catalog()
                if project.get("project_id")
            ]
        return [
            project.project_id
            for project in company.projects
            if project.project_id
        ]

    def root_project_ids(self, company: Company) -> List[str]:
        """
        Return the company's root projects (the ones the owner associates).

        In ``all_projects`` mode the roots are the top-level discovered
        projects; otherwise they are the direct company links, including a
        child project the owner added manually.
        """
        catalog = self._project_catalog()
        if not catalog:
            return []
        if not company.all_projects:
            return [
                project.project_id
                for project in company.projects
                if project.project_id
            ]
        # all_projects: only the top-level discovered projects are roots.
        roots = []
        for project in catalog:
            path = project.get("project_path") or ""
            has_parent = any(
                other.get("project_id") != project.get("project_id")
                and self._is_path_child_of(path, other.get("project_path") or "")
                for other in catalog
            )
            if not has_parent:
                roots.append(project["project_id"])
        return roots

    def visible_project_ids(self, company: Company) -> List[str]:
        """
        Return every project the company can see.

        ``all_projects`` sees the whole discovered catalog. A selected company
        sees its direct links plus the descendants of links whose
        ``include_children`` flag is on.
        """
        catalog = self._project_catalog()
        if not catalog:
            return []
        if company.all_projects:
            return [project["project_id"] for project in catalog]
        visible: List[str] = []
        seen = set()

        def add(project_id: str) -> None:
            if project_id and project_id not in seen:
                seen.add(project_id)
                visible.append(project_id)

        for linked in company.projects:
            add(linked.project_id)
            if linked.include_children:
                for child in self._descendant_projects(catalog, linked.project_id):
                    add(child["project_id"])
        # Keep the catalog order for a stable UI (unknown links appended).
        order = {project["project_id"]: index for index, project in enumerate(catalog)}
        visible.sort(key=lambda project_id: order.get(project_id, len(order)))
        return visible

    def child_projects(self, company: Company) -> List[Dict[str, Any]]:
        """
        Return the child projects the company actually sees.

        A child is visible only when its parent link has ``include_children``
        on (or the company sees all projects). Children added as direct links
        are company projects, not inherited children, and are excluded here.
        """
        catalog = self._project_catalog()
        by_id = {project["project_id"]: project for project in catalog}
        linked_ids = {project.project_id for project in company.projects}
        children: Dict[str, Dict[str, Any]] = {}
        for linked in company.projects:
            if not company.all_projects and not linked.include_children:
                continue
            parent = by_id.get(linked.project_id)
            parent_path = (parent or {}).get("project_path") or ""
            if not parent_path:
                continue
            for candidate in catalog:
                child_id = candidate.get("project_id") or ""
                if not child_id or child_id in linked_ids or child_id == linked.project_id:
                    continue
                if not self._is_path_child_of(
                    candidate.get("project_path") or "", parent_path
                ):
                    continue
                entry = {
                    "project_id": child_id,
                    "project_name": candidate.get("project_name") or child_id,
                    "project_path": candidate.get("project_path") or "",
                    "parent_project_id": linked.project_id,
                    "parent_project_name": linked.name or linked.project_id,
                }
                # A nested project can be under two mapped parents; keep the
                # closest one (longest parent path).
                previous = children.get(child_id)
                if previous is None or len(parent_path) > len(previous.get("_parent_path", "")):
                    entry["_parent_path"] = parent_path
                    children[child_id] = entry
        result = []
        for entry in children.values():
            entry.pop("_parent_path", None)
            result.append(entry)
        return sorted(result, key=lambda item: (item["parent_project_name"].lower(), item["project_name"].lower()))

    def _project_spent(self, company: Company, project_id: str) -> float:
        """Return the cumulative AI spend attributed to one project."""
        return round(
            sum(
                mission.spent_cxjcoins
                for mission in company.missions
                if mission.project_id == project_id
            ),
            4,
        )

    def _team_spent(self, company: Company, team_id: str) -> float:
        """Return the cumulative AI spend attributed to one team."""
        return round(
            sum(
                mission.spent_cxjcoins
                for mission in company.missions
                if mission.team_id == team_id
            ),
            4,
        )

    def budget_state(
        self, company: Company, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Build the company budget ledger shown by the UI and the assistant.

        ``spent_today`` is computed from the append-only :class:`SpendEntry`
        ledger (bounded to the most recent entries); ``spent_total`` is the
        cumulative counter on the company, so deleting/archiving a mission does
        not make past spend disappear. Project budgets are hard limits checked
        on dispatch; the daily limit is enforced by the autopilot cycle.

        Args:
            company: Company document.
            now: Instant used to define "today" (defaults to the manager clock).

        Returns:
            A budget block with the daily limit, today/total spend, remaining
            amounts, per-project rows and per-mission totals.
        """
        current = now or self._now()
        today = current.date()
        spent_today = 0.0
        for entry in company.spend:
            at = parse_iso(entry.at)
            if at is not None and at.date() == today:
                spent_today = round(spent_today + entry.amount, 6)

        mission_spent = round(
            sum(mission.spent_cxjcoins for mission in company.missions), 4
        )
        spent_total = round(max(company.spent_cxjcoins, mission_spent), 4)
        daily_limit = round(float(company.autonomy.daily_budget_cxjcoins or 0.0), 4)

        projects: List[Dict[str, Any]] = []
        for project in company.projects:
            spent = self._project_spent(company, project.project_id)
            budget = round(float(project.budget_cxjcoins or 0.0), 4)
            remaining = None if budget <= 0 else round(max(0.0, budget - spent), 4)
            projects.append(
                {
                    "project_id": project.project_id,
                    "name": project.name or project.project_id,
                    "budget_cxjcoins": budget,
                    "spent_cxjcoins": spent,
                    "remaining_cxjcoins": remaining,
                    "over_budget": budget > 0 and spent >= budget,
                }
            )

        teams: List[Dict[str, Any]] = []
        for team in company.teams:
            spent = self._team_spent(company, team.id)
            budget = round(float(team.budget_cxjcoins or 0.0), 4)
            remaining = None if budget <= 0 else round(max(0.0, budget - spent), 4)
            teams.append(
                {
                    "team_id": team.id,
                    "name": team.name,
                    "budget_cxjcoins": budget,
                    "spent_cxjcoins": spent,
                    "remaining_cxjcoins": remaining,
                    "over_budget": budget > 0 and spent >= budget,
                }
            )

        total_budget = round(
            sum(project.budget_cxjcoins for project in company.projects), 4
        )
        return {
            "currency": "cxjcoins",
            "daily_limit_cxjcoins": daily_limit,
            "spent_today_cxjcoins": round(spent_today, 4),
            "remaining_today_cxjcoins": (
                None
                if daily_limit <= 0
                else round(max(0.0, daily_limit - spent_today), 4)
            ),
            "over_daily_budget": daily_limit > 0 and spent_today >= daily_limit,
            "spent_total_cxjcoins": spent_total,
            "total_project_budget_cxjcoins": total_budget,
            "remaining_project_budget_cxjcoins": (
                None
                if total_budget <= 0
                else round(max(0.0, total_budget - mission_spent), 4)
            ),
            "projects": projects,
            "teams": teams,
            "by_mission": {
                mission.id: round(mission.spent_cxjcoins, 4)
                for mission in company.missions
                if mission.spent_cxjcoins > 0
            },
        }

    def snapshot(self, company_id: str, journal_limit: int = 30) -> Dict[str, Any]:
        """
        Build the full payload the company dashboard renders.

        Args:
            company_id: Company id.
            journal_limit: Number of journal entries included.

        Returns:
            ``company``, ``counts``, ``teams``, ``projects``, ``missions``,
            ``attention``, ``journal`` and ``autonomy``.
        """
        company = self.get_company(company_id)
        catalog = {project["project_id"]: project for project in self._project_catalog()}
        visible_project_ids = self.visible_project_ids(company)
        company_project_ids = self.company_project_ids(company)
        root_project_ids = self.root_project_ids(company)
        budget = self.budget_state(company)
        budget_by_project = {
            row["project_id"]: row for row in budget.get("projects", [])
        }
        teams = []
        for team in company.teams:
            open_missions = [
                mission
                for mission in company.open_missions
                if mission.team_id == team.id
            ]
            teams.append(
                {
                    **team.model_dump(),
                    "human_count": team.human_count,
                    "ai_count": team.ai_count,
                    "open_missions": len(open_missions),
                    "running_missions": len(
                        [mission for mission in open_missions if mission.status == "running"]
                    ),
                }
            )
        child_projects = self.child_projects(company)
        children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for child in child_projects:
            children_by_parent.setdefault(child["parent_project_id"], []).append(child)
        direct_by_id = {project.project_id: project for project in company.projects}
        if company.all_projects:
            # A wildcard company may have no materialized links (a new company
            # created with "all projects"); the snapshot still needs one entry
            # per visible project for the dashboard/assistant.
            project_refs = [
                direct_by_id.get(project["project_id"])
                or CompanyProject(
                    project_id=project["project_id"],
                    name=project.get("project_name") or project["project_id"],
                    include_children=True,
                )
                for project in catalog.values()
            ]
        else:
            project_refs = list(company.projects)
        projects = []
        for project in project_refs:
            discovered = catalog.get(project.project_id)
            project_budget = budget_by_project.get(project.project_id, {})
            projects.append(
                {
                    **project.model_dump(),
                    "available": discovered is not None,
                    "discovered_name": (discovered or {}).get("project_name", ""),
                    "discovered_path": (discovered or {}).get("project_path", ""),
                    "children": children_by_parent.get(project.project_id, []),
                    "open_missions": len(
                        [
                            mission
                            for mission in company.open_missions
                            if mission.project_id == project.project_id
                        ]
                    ),
                    "spent_cxjcoins": project_budget.get("spent_cxjcoins", 0.0),
                    "remaining_budget_cxjcoins": project_budget.get(
                        "remaining_cxjcoins"
                    ),
                    "over_budget": project_budget.get("over_budget", False),
                }
            )
        missions = [
            mission.model_dump()
            for mission in sorted(company.missions, key=self._mission_sort_key)
        ]
        attention = self._attention(company, catalog)
        journal = [
            event.model_dump()
            for event in self.store.read_events(company.id, limit=journal_limit)
        ]
        return {
            # The ledger is not needed by the dashboard: `budget` summarises it.
            # Excluding it keeps the snapshot small even with a long history.
            "company": company.model_dump(exclude={"spend"}),
            "counts": {
                **company.counts(),
                "projects": len(company_project_ids),
                "child_projects": len(child_projects),
            },
            "all_projects": company.all_projects,
            "root_project_ids": root_project_ids,
            "visible_project_ids": visible_project_ids,
            "teams": teams,
            "projects": projects,
            "spaces": [
                self._space_payload(company, space, catalog)
                for space in company.spaces
            ],
            "child_projects": child_projects,
            "missions": missions,
            "members": self.company_members(company),
            "attention": attention,
            "journal": journal,
            "autonomy": company.autonomy.model_dump(),
            "budget": budget,
            "due": company.autonomy.is_due(self._now()),
        }

    def actor_snapshot(self, company_id: str) -> Dict[str, Any]:
        """
        Return the autonomous actors of the projects linked to a company.

        The actor runtime is project-scoped (profiles, boards, budgets and state
        live in the project), while the company is the top-level organization.
        This read-only projection lets the company shell see the same actors,
        including paused ones, with their project, schedule and today's spend.
        It never starts a shift and never imports the AI stack.
        """
        company = self.get_company(company_id)
        catalog = {
            project["project_id"]: project
            for project in self._project_catalog()
            if project.get("project_id")
        }
        direct = {project.project_id: project for project in company.projects}
        projects = []
        for project_id in self.company_project_ids(company):
            reference = catalog.get(project_id) or {}
            projects.append(
                direct.get(project_id)
                or CompanyProject(
                    project_id=project_id,
                    name=reference.get("project_name") or project_id,
                )
            )
        actors: List[Dict[str, Any]] = []
        unavailable: List[str] = []
        for project in projects:
            settings = self._project_settings_provider(project.project_id)
            if settings is None:
                unavailable.append(project.project_id)
                continue
            try:
                from codx.junior.autonomy.service import ActorService

                status = ActorService(settings=settings).status()
            except Exception as ex:  # pragma: no cover - one project is not fatal
                logger.warning(
                    "Company actors: cannot read project '%s': %s",
                    project.project_id,
                    ex,
                )
                unavailable.append(project.project_id)
                continue
            for entry in status.get("actors") or []:
                actors.append(
                    {
                        **entry,
                        "project_id": project.project_id,
                        "project_name": project.name or project.project_id,
                    }
                )
        return {
            "company_id": company.id,
            "actors": actors,
            "counts": {
                "total": len(actors),
                "enabled": len(
                    [
                        entry for entry in actors
                        if (entry.get("config") or {}).get("enabled")
                    ]
                ),
                "projects": len(projects),
                "unavailable_projects": len(unavailable),
            },
            "unavailable_projects": unavailable,
        }

    def company_members(self, company: Company) -> List[Dict[str, Any]]:
        """
        Return the human roster of a company.

        The roster is built from ``CodxUser.companies`` (the user-side index),
        plus the company owner when it has no membership yet (legacy documents
        and companies created before the role model). The owner is always
        reported as a company admin.

        Args:
            company: Company document.

        Returns:
            Members sorted by company role (admin → manager → member) and name.
        """
        role_rank = {"admin": 0, "manager": 1, "member": 2}
        members: Dict[str, Dict[str, Any]] = {}
        for catalog_user in self._users():
            username = self._clean(catalog_user.get("username"))
            if not username:
                continue
            membership = next(
                (
                    item
                    for item in (catalog_user.get("companies") or [])
                    if str(item.get("company_id") or "") == company.id
                ),
                None,
            )
            if membership is None and username != company.owner:
                continue
            role = (
                "admin"
                if username == company.owner
                else ((membership or {}).get("role") or "member")
            )
            members[username] = {
                "username": username,
                "email": catalog_user.get("email", ""),
                "avatar": catalog_user.get("avatar", ""),
                "role": catalog_user.get("role", ""),
                "company_role": role,
                "title": (membership or {}).get("title", ""),
                "disabled": bool(catalog_user.get("disabled")),
                "owner": username == company.owner,
            }
        # The owner may not exist in the catalog (auth disabled / fresh install).
        if company.owner and company.owner not in members:
            members[company.owner] = {
                "username": company.owner,
                "email": "",
                "avatar": "",
                "role": "",
                "company_role": "admin",
                "title": "Owner",
                "disabled": False,
                "owner": True,
            }
        return sorted(
            members.values(),
            key=lambda item: (
                role_rank.get(item.get("company_role"), 9),
                item["username"].lower(),
            ),
        )

    def references(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Return the catalogs a company picker needs.

        Args:
            project_id: Optional project scope for the profile catalog.

        Returns:
            ``projects``, ``users``, ``profiles`` and ``templates``.
        """
        projects = self._project_catalog()
        profiles = self._profile_catalog([project_id] if project_id else None)
        return {
            "projects": projects,
            "users": self._users(),
            "profiles": profiles,
            "templates": ["blank", "saas"],
        }

    def resolve_team(self, company_id: str, team_id: str) -> Dict[str, Any]:
        """
        Resolve a team into its roster, with availability per reference.

        Args:
            company_id: Company id.
            team_id: Team id.

        Returns:
            The team, its members with ``available`` flags and mission counts.
        """
        company = self.get_company(company_id)
        team = company.team(team_id)
        if team is None:
            raise CompanyNotFound(f"Team '{team_id}' not found")
        profiles = {
            profile["name"]: profile for profile in self._profile_catalog(team.project_ids)
        }
        users = {user["username"]: user for user in self._users()}
        members = []
        for member in team.members:
            entry = member.model_dump()
            if member.is_ai:
                entry["available"] = not profiles or member.name in profiles
                entry["projects"] = (profiles.get(member.name) or {}).get("projects", [])
            else:
                entry["available"] = not users or member.name in users
                entry["projects"] = []
            members.append(entry)
        return {
            **team.model_dump(),
            "members": members,
            "human_count": team.human_count,
            "ai_count": team.ai_count,
            "missions": [
                mission.model_dump()
                for mission in company.missions
                if mission.team_id == team.id
            ],
        }

    def journal(self, company_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the most recent journal events (newest last)."""
        company = self.get_company(company_id)
        return [
            event.model_dump()
            for event in self.store.read_events(company.id, limit=limit)
        ]

    def build_mission_brief(self, company: Company, mission: Mission) -> str:
        """
        Build the objective handed to the project orchestration run.

        The brief is what makes the AI team work *as the company*: it carries the
        company direction, the org context, the team roster with responsibilities
        and the acceptance criteria, not just the mission title.

        Args:
            company: Owning company.
            mission: Mission to dispatch.

        Returns:
            A markdown brief.
        """
        team = company.team(mission.team_id)
        project = company.project(mission.project_id)
        org_unit = company.org_unit(team.org_unit_id) if team else None
        lines = [
            f"# Mission: {mission.title}",
            "",
            "## Company",
            f"- **Company:** {company.name}",
        ]
        if company.mission:
            lines.append(f"- **Mission:** {company.mission}")
        if company.vision:
            lines.append(f"- **Vision:** {company.vision}")
        if company.values:
            lines.append(f"- **Values:** {', '.join(company.values)}")
        lines += ["", "## Mission objective", mission.objective or mission.title]
        if mission.acceptance:
            lines += ["", "## Acceptance criteria", mission.acceptance]
        if project:
            lines += [
                "",
                "## Project",
                f"- **Project:** {project.name or project.project_id}",
                f"- **Role in the company:** {project.role or 'not specified'}",
            ]
            if project.description:
                lines.append(f"- **Why it exists:** {project.description}")
        if team:
            lines += [
                "",
                "## Team",
                f"- **Team:** {team.name}",
            ]
            if team.purpose:
                lines.append(f"- **Purpose:** {team.purpose}")
            if org_unit:
                lines.append(f"- **Org unit:** {org_unit.name} ({org_unit.kind})")
            lines.append("- **Roster:**")
            for member in team.members:
                role = member.role or "member"
                responsibility = f" — {member.responsibility}" if member.responsibility else ""
                lines.append(
                    f"  - `{member.name}` ({member.kind}, {role}){responsibility}"
                )
            lines += [
                "",
                "Work as this team. Use the team profiles for their areas of "
                "ownership, keep the work visible on the project board, and "
                "verify the result against the acceptance criteria before "
                "reporting completion.",
            ]
        if mission.due_date:
            lines += ["", f"Due date: {mission.due_date}"]
        if mission.last_error and mission.status in ("ready", "blocked"):
            lines += [
                "",
                "## Previous attempt feedback",
                mission.last_error,
                "Address this feedback explicitly in the new run.",
            ]
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _patch_dict(value: Any) -> Dict[str, Any]:
        """Convert a model/patch into a plain dict, dropping private keys."""
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            data = value.model_dump(exclude_unset=True)
        elif isinstance(value, dict):
            data = dict(value)
        else:
            raise CompanyValidationError(
                f"Expected an object, got {type(value).__name__}"
            )
        return {key: item for key, item in data.items() if not key.startswith("_")}

    def _company_id(self, company_id: str) -> str:
        """Resolve a company id/slug to the stored id (used for locks)."""
        return self.get_company(company_id).id

    def _ensure_unique_children(self, company: Company) -> None:
        """Validate child names/ids and referential integrity of a new company."""
        names = set()
        for unit in company.org_units:
            key = unit.name.lower()
            if key in names:
                raise CompanyConflict(f"Duplicated org unit name: {unit.name}")
            names.add(key)
        team_names = set()
        for team in company.teams:
            key = team.name.lower()
            if key in team_names:
                raise CompanyConflict(f"Duplicated team name: {team.name}")
            team_names.add(key)
            if team.org_unit_id and not company.org_unit(team.org_unit_id):
                raise CompanyValidationError(
                    f"Team '{team.name}' points at unknown org unit "
                    f"'{team.org_unit_id}'"
                )
        for project in company.projects:
            if not project.project_id:
                raise CompanyValidationError("company project needs a project_id")
        mission_ids = set()
        for mission in company.missions:
            if mission.id in mission_ids:
                raise CompanyConflict(f"Duplicated mission id: {mission.id}")
            mission_ids.add(mission.id)

    def _ensure_unique_org_name(
        self, company: Company, name: str, exclude_id: str = ""
    ) -> None:
        """Reject a duplicated org unit name (case-insensitive)."""
        wanted = (name or "").strip().lower()
        for unit in company.org_units:
            if unit.id != exclude_id and unit.name.lower() == wanted:
                raise CompanyConflict(f"An org unit named '{name}' already exists")

    def _ensure_unique_team_name(
        self, company: Company, name: str, exclude_id: str = ""
    ) -> None:
        """Reject a duplicated team name (case-insensitive)."""
        wanted = (name or "").strip().lower()
        for team in company.teams:
            if team.id != exclude_id and team.name.lower() == wanted:
                raise CompanyConflict(f"A team named '{name}' already exists")

    def _mission_needs_approval(self, company: Company, mission: Mission) -> bool:
        """
        Tell whether a mission is subject to an approval gate.

        The gate is enabled by the mission itself or by its team policy. It is
        intentionally dynamic: turning the team policy on blocks the next
        dispatch even for missions created before the policy existed.
        """
        team = company.team(mission.team_id)
        return bool(
            mission.requires_approval
            or (team is not None and team.requires_approval)
        )

    def _ensure_dispatch_budget(
        self, company: Company, mission: Mission, force: bool = False
    ) -> None:
        """
        Enforce the hard budgets before a mission starts a new run.

        A project budget and a mission budget are hard limits: once the observed
        spend reaches them, dispatch is refused unless *force* is set (an admin
        escape hatch). The daily budget is a policy limit enforced by the
        autopilot cycle, not here, because a human may deliberately dispatch
        over it.

        Args:
            company: Company document.
            mission: Mission about to be dispatched.
            force: Bypass the hard limits.

        Raises:
            CompanyConflict: When a project or mission budget is exhausted.
        """
        if force:
            return
        project = company.project(mission.project_id)
        if project is not None and project.budget_cxjcoins > 0:
            spent = self._project_spent(company, project.project_id)
            if spent >= project.budget_cxjcoins:
                raise CompanyConflict(
                    f"Project '{project.name or project.project_id}' is over its "
                    f"budget: {spent} / {project.budget_cxjcoins} cxjcoins. "
                    "Increase the budget or force the dispatch."
                )
        team = company.team(mission.team_id)
        if team is not None and team.budget_cxjcoins > 0:
            spent = self._team_spent(company, team.id)
            if spent >= team.budget_cxjcoins:
                raise CompanyConflict(
                    f"Team '{team.name}' is over its budget: {spent} / "
                    f"{team.budget_cxjcoins} cxjcoins. Increase the budget or "
                    "force the dispatch."
                )
        if mission.budget_cxjcoins > 0 and mission.spent_cxjcoins >= mission.budget_cxjcoins:
            raise CompanyConflict(
                f"Mission '{mission.title}' is over its budget: "
                f"{mission.spent_cxjcoins} / {mission.budget_cxjcoins} cxjcoins."
            )

    def _validate_mission_destination(self, company: Company, mission: Mission) -> None:
        """Validate the team/project of a mission (project optional while draft)."""
        if mission.team_id and not company.team(mission.team_id):
            raise CompanyValidationError(
                f"Team '{mission.team_id}' does not exist in this company"
            )
        if mission.project_id:
            if not company.project(mission.project_id):
                raise CompanyValidationError(
                    f"Project '{mission.project_id}' is not linked to this company"
                )
        elif mission.status in ("ready", "running"):
            raise CompanyValidationError(
                "A ready/running mission needs a linked project_id"
            )

    def _dispatchable_missions(self, company: Company) -> List[Mission]:
        """
        Return the auto missions that are ready to be dispatched.

        A mission is dispatchable by the autopilot only when it is ``ready``,
        marked ``auto``, has a team and a project, **and** its team is not
        ``manual``: a manual team explicitly only accepts human dispatches.
        """
        candidates = []
        for mission in company.missions:
            if (
                mission.status != "ready"
                or mission.dispatch_mode != "auto"
                or not mission.team_id
                or not mission.project_id
            ):
                continue
            team = company.team(mission.team_id)
            if team is not None and team.autonomy == "manual":
                continue
            if (
                self._mission_needs_approval(company, mission)
                and mission.approval_status != "approved"
            ):
                continue
            candidates.append(mission)
        return sorted(candidates, key=self._mission_sort_key)

    @staticmethod
    def _mission_sort_key(mission: Mission) -> Tuple[int, str, str]:
        """Sort missions by priority, then due date, then creation."""
        return (
            _PRIORITY_RANK.get(mission.priority, 9),
            mission.due_date or "9999-12-31",
            mission.created_at or "",
        )

    def _mission_status_for_run(self, company: Company, run: Dict[str, Any]) -> Optional[str]:
        """Map an orchestration run status onto a mission status."""
        status = (run.get("status") or "").strip().lower()
        if status in _ACTIVE_RUN_STATUSES:
            return "running"
        if status == "paused":
            return "paused"
        if status == "completed":
            if (
                company.autonomy.level == "autonomous"
                and not company.autonomy.require_human_acceptance
            ):
                return "done"
            return "review"
        if status in ("failed", "stopped", "budget_exhausted", "stalled"):
            return "blocked"
        return None

    def _attention(
        self, company: Company, catalog: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build the "needs a human" list shown on the dashboard."""
        today = date.today()
        items: List[Dict[str, Any]] = []
        for mission in company.missions:
            reason = ""
            if (
                mission.status in ("ready", "blocked")
                and self._mission_needs_approval(company, mission)
                and mission.approval_status in ("pending", "denied")
            ):
                reason = "approval"
            elif mission.status == "blocked":
                reason = "blocked"
            elif (
                mission.status == "ready"
                and mission.dispatch_mode == "auto"
                and not company.autonomy.enabled
            ):
                reason = "autopilot_off"
            elif mission.last_error:
                reason = "error"
            elif mission.due_date:
                try:
                    due = date.fromisoformat(mission.due_date[:10])
                    if due < today and mission.is_open:
                        reason = "overdue"
                except ValueError:
                    pass
            elif not mission.team_id and mission.is_open:
                reason = "unassigned_team"
            elif not mission.project_id and mission.status in ("ready", "running"):
                reason = "unassigned_project"
            if not reason:
                continue
            items.append(
                {
                    "mission_id": mission.id,
                    "title": mission.title,
                    "status": mission.status,
                    "priority": mission.priority,
                    "reason": reason,
                    "last_error": mission.last_error,
                    "due_date": mission.due_date,
                    "project_id": mission.project_id,
                }
            )
        for project in company.projects:
            if project.project_id not in catalog:
                items.append(
                    {
                        "mission_id": "",
                        "title": project.name or project.project_id,
                        "status": project.status,
                        "priority": project.priority,
                        "reason": "project_missing",
                        "project_id": project.project_id,
                    }
                )
        return items

    # ------------------------------------------------------------------
    # Default dispatch / run status (durable orchestration)
    # ------------------------------------------------------------------

    @staticmethod
    def _board_title(value: str, fallback: str) -> str:
        """
        Turn a company/mission name into a safe board title.

        Board titles become folders under ``.codx/tasks``. Path separators and
        control characters are stripped so a company name like ``a/b`` can never
        escape the project's tasks folder, and the title is kept short enough to
        stay readable on a card.
        """
        title = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", str(value or ""))
        title = " ".join(title.split()).strip()
        return (title[:60] or fallback).strip()

    def _default_dispatcher(
        self, company: Company, mission: Mission, brief: str
    ) -> Dict[str, Any]:
        """
        Start a durable orchestration run for a mission.

        The AI stack is imported lazily: creating a company or editing its org
        never pays for it.
        """
        settings = self._project_settings_provider(mission.project_id)
        if settings is None:
            raise CompanyDispatchError(
                f"Project '{mission.project_id}' is not available (not discovered). "
                "Make sure the project is registered in codx-junior."
            )
        try:
            from codx.junior.chat_manager import ChatManager
            from codx.junior.orchestration.models import RunConfig
            from codx.junior.orchestration.service import OrchestrationService
        except Exception as ex:  # pragma: no cover - AI stack unavailable
            raise CompanyDispatchError(
                f"The orchestration runtime is not available: {ex}"
            ) from ex

        team = company.team(mission.team_id)
        profiles = self._project_profiles(settings, team)
        config = RunConfig(
            budget_cxjcoins=float(mission.budget_cxjcoins or 0.0),
            supervisor_profiles=profiles,
            board=self._board_title(company.name, "Company"),
            sprint=f"Mission: {self._board_title(mission.title, mission.id[-6:])}",
            verification="supervisor",
            created_by="company",
        )
        service = OrchestrationService(
            settings=settings,
            chat_manager=ChatManager(settings=settings),
        )
        run = service.create_run(
            name=mission.title,
            objective=brief,
            config=config,
            created_by="company",
            start=True,
            persist_main=True,
        )
        return {
            "run_id": run.run_id,
            "status": run.status,
            "main_chat_id": run.main_chat_id,
        }

    def _project_profiles(self, settings: Any, team: Optional[CompanyTeam]) -> List[str]:
        """
        Pick the supervisor profiles that exist in the target project.

        The team lead is preferred; ``orchestrator``/``software_developer`` are
        safe fallbacks because they ship with codx-junior.
        """
        try:
            from codx.junior.profiles.profile_manager import ProfileManager

            available = {
                profile.name
                for profile in ProfileManager(settings=settings).list_all_profiles()
                if profile and profile.name
            }
        except Exception:
            available = set()
        wanted: List[str] = []
        if team and team.lead_kind == "profile" and team.lead_name:
            wanted.append(team.lead_name)
        if team:
            wanted.extend(
                member.name
                for member in team.members
                if member.is_ai and member.name not in wanted
            )
        for fallback in ("orchestrator", "project", "software_developer"):
            if fallback not in wanted:
                wanted.append(fallback)
        selected = [name for name in wanted if not available or name in available]
        if not selected:
            selected = ["orchestrator"] if "orchestrator" in available else list(available)[:1]
        # The first selected profile is the supervisor; keep the list small so
        # the main task is not composed with the whole roster.
        return selected[:1] if selected else ["orchestrator"]

    def _default_run_status(self, project_id: str, run_id: str) -> Dict[str, Any]:
        """Read an orchestration run from the project store (light import)."""
        settings = self._project_settings_provider(project_id)
        if settings is None:
            raise CompanyDispatchError(
                f"Project '{project_id}' is not available (not discovered)"
            )
        from codx.junior.orchestration.store import RunStore

        run = RunStore(getattr(settings, "codx_path", "")).load(run_id)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "progress": run.progress(),
            "spent_cxjcoins": float(getattr(run, "spent_cxjcoins", 0.0) or 0.0),
            "error": getattr(run, "last_error", "") or "",
            "name": run.name,
        }


# Made with ❤️ by codx-junior
