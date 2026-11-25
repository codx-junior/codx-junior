"""
Installation onboarding — the "New company" wizard state.

After codx-junior is installed the product renders the *company mode* and runs a
short wizard that guides the owner through the first company, its projects and
the basic AI settings. On a fresh installation the wizard is a **gate**: the
owner must define the company basics and finish it before the product can be
used. The wizard stays **re-runnable** at any time from the company menu, and an
installation whose default company was already configured (an upgrade) is never
locked out.

Where the state lives
---------------------
``<companies_root>/onboarding.json`` — one document per installation, so the
wizard is offered exactly once per install and not once per company. It stores:

```json
{
  "version": 1,
  "status": "pending" | "completed" | "skipped",
  "steps": {"welcome": "done", "company": "done"},
  "settings": {"default_model": "gpt-4o-mini"},
  "company_id": "acme",
  "completed_at": "…",
  "updated_at": "…",
  "updated_by": "admin"
}
```

The document is written atomically (tmp file + ``os.replace``) so a wizard
running in the browser can never leave a half-written file behind. Corruption
degrades to the default (pending) state instead of crashing the product; on an
already-configured installation the default company still opens the gate, so a
broken file cannot lock an upgrade out.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from codx.junior.company.store import get_companies_root

logger = logging.getLogger(__name__)

ONBOARDING_FILE = "onboarding.json"
STATE_VERSION = 1

#: Ordered wizard steps. ``welcome`` and ``review`` are informational; the three
#: editable ones own the data the wizard collects.
STEP_IDS = ("welcome", "company", "projects", "models", "review")

#: Description prefix of the auto-provisioned default company. A company that
#: still carries it (and no other setup signal) is a placeholder, not a company
#: the owner configured.
AUTO_DEFAULT_DESCRIPTION_PREFIX = "Default company created automatically."

#: Names used by the auto-provisioned default company (old and current). A
#: company with one of these names is not, by itself, a configured company.
DEFAULT_COMPANY_NAMES = {"acme", "codx-junior"}

#: Deployment override of the default company name (see ``manager``). A
#: deployment that provisions a differently named default must still be gated.
DEFAULT_COMPANY_NAME_ENV = "CODX_JUNIOR_DEFAULT_COMPANY_NAME"

STEP_CATALOG: List[Dict[str, str]] = [
    {
        "id": "welcome",
        "title": "Welcome",
        "description": "What the company workspace does for you.",
        "icon": "fa-solid fa-hand-sparkles",
    },
    {
        "id": "company",
        "title": "First company",
        "description": "Name, purpose and brand of the company.",
        "icon": "fa-solid fa-building",
    },
    {
        "id": "projects",
        "title": "Projects",
        "description": "Which repositories the company works with (all by default).",
        "icon": "fa-solid fa-diagram-project",
    },
    {
        "id": "models",
        "title": "AI models",
        "description": "Default model used by the company's teams.",
        "icon": "fa-solid fa-brain",
    },
    {
        "id": "review",
        "title": "Finish",
        "description": "Review and start working.",
        "icon": "fa-solid fa-flag-checkered",
    },
]

_LOCK = threading.RLock()


class OnboardingError(RuntimeError):
    """Raised when the onboarding document cannot be written."""


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def onboarding_path() -> str:
    """Absolute path of the installation onboarding document."""
    return os.path.join(get_companies_root(), ONBOARDING_FILE)


def default_state() -> Dict[str, Any]:
    """Return a pristine onboarding state (wizard pending)."""
    return {
        "version": STATE_VERSION,
        "status": "pending",
        "steps": {},
        "settings": {},
        "company_id": "",
        "completed_at": "",
        "updated_at": "",
        "updated_by": "",
    }


def _normalise(raw: Any) -> Dict[str, Any]:
    """Coerce a stored/partial document into a complete, valid state."""
    state = default_state()
    if not isinstance(raw, dict):
        return state
    status = str(raw.get("status") or "").strip().lower()
    if status in ("pending", "completed", "skipped"):
        state["status"] = status
    steps = raw.get("steps")
    if isinstance(steps, dict):
        state["steps"] = {
            str(key): str(value)
            for key, value in steps.items()
            if str(value) in ("pending", "done", "skipped")
        }
    settings = raw.get("settings")
    if isinstance(settings, dict):
        state["settings"] = dict(settings)
    state["company_id"] = str(raw.get("company_id") or "")
    state["completed_at"] = str(raw.get("completed_at") or "")
    state["updated_at"] = str(raw.get("updated_at") or "")
    state["updated_by"] = str(raw.get("updated_by") or "")
    return state


def read_state() -> Dict[str, Any]:
    """Read the onboarding state (never raises)."""
    path = onboarding_path()
    with _LOCK:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return _normalise(json.load(handle))
        except FileNotFoundError:
            return default_state()
        except Exception as ex:  # corrupt file must not block the product
            logger.warning("Company onboarding: cannot read %s: %s", path, ex)
            return default_state()


def write_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persist a complete onboarding state atomically.

    Args:
        state: The state to store (it is normalised first).

    Returns:
        The stored state.

    Raises:
        OnboardingError: When the document cannot be written.
    """
    payload = _normalise(state)
    path = onboarding_path()
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as ex:
            raise OnboardingError(f"Cannot store the onboarding state: {ex}") from ex
    return payload


def merge_state(patch: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
    """
    Apply a partial update to the onboarding state.

    ``reset=True`` restores the pristine *pending* state, which is what the
    "run the wizard again" entry point uses.

    Args:
        patch: Fields to merge (``status``, ``step``/``step_status``,
            ``settings``, ``company_id``, ``reset``).
        actor: Username recorded in the document.

    Returns:
        The stored state.
    """
    patch = patch or {}
    if patch.get("reset"):
        state = default_state()
        state["updated_at"] = _now_iso()
        state["updated_by"] = actor or "system"
        return write_state(state)

    state = read_state()
    status = str(patch.get("status") or "").strip().lower()
    # Completion is sticky. Re-running the wizard must never downgrade a
    # finished installation (for example "Skip setup" on a re-run) and re-lock
    # the product; only an explicit reset clears the completion.
    if (state["status"] == "completed" or state["completed_at"]) and status in (
        "pending",
        "skipped",
    ):
        status = "completed"
    if status in ("pending", "completed", "skipped"):
        state["status"] = status
        if status == "completed":
            state["completed_at"] = state["completed_at"] or _now_iso()
        elif status == "pending":
            state["completed_at"] = ""
    step = str(patch.get("step") or "").strip()
    step_status = str(patch.get("step_status") or "").strip().lower()
    if step in STEP_IDS and step_status in ("pending", "done", "skipped"):
        state["steps"][step] = step_status
    settings = patch.get("settings")
    if isinstance(settings, dict):
        state["settings"].update(settings)
    if patch.get("company_id"):
        state["company_id"] = str(patch["company_id"])
    state["version"] = STATE_VERSION
    state["updated_at"] = _now_iso()
    state["updated_by"] = actor or "system"
    return write_state(state)


def company_configured(company: Optional[Dict[str, Any]]) -> bool:
    """
    Tell whether a company has been set up by a human.

    The auto-provisioned default company is a placeholder: it has the canned
    description, a default name and every project linked automatically. The
    wizard exists to replace that with the owner's company. Once the owner
    changes the description/brand, picks a name or customises the project
    policy, the company is configured and the wizard must not auto-open again.

    Args:
        company: Company summary (``Company.summary()``) or ``None``.

    Returns:
        ``True`` when the company shows a real setup.
    """
    if not company:
        return False
    description = str(company.get("description") or "").strip()
    if description and not description.startswith(AUTO_DEFAULT_DESCRIPTION_PREFIX):
        return True
    if company.get("logo"):
        return True
    counts = company.get("counts") or {}
    if counts.get("teams") or counts.get("missions"):
        return True
    # Editing the project list turns auto-sync off; that is a deliberate setup
    # decision even when the name and description are still the placeholders.
    if not company.get("auto_sync_projects") or not company.get("all_projects"):
        return True
    name = str(company.get("name") or "").strip().lower()
    names = set(DEFAULT_COMPANY_NAMES)
    configured_name = str(os.environ.get(DEFAULT_COMPANY_NAME_ENV) or "").strip().lower()
    if configured_name:
        names.add(configured_name)
    return bool(name and name not in names)


def build_status(companies: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Build the payload the wizard renders.

    The product is locked while the installation needs its first company:

    * there is no company at all, or
    * the default company is still the auto-provisioned placeholder (``ACME``
      in older releases, ``codx-junior`` now) that the owner has not set up,
      and the wizard was **not finished**.

    ``completed`` is the only status that opens the gate. A legacy ``skipped``
    document (written by a previous release) does not: on a fresh installation
    the owner must finish the wizard so the company basics are defined. An
    installation whose default company is already configured (an upgrade) is
    never locked out. The wizard can always be re-run from the company menu.

    Args:
        companies: Company summaries (the wizard offers to *configure* an
            existing one instead of creating a duplicate).

    Returns:
        A dict with ``state``, the ``steps`` catalog, the ``companies``
        summaries, ``needs_setup``/``setup_required`` (the gate is closed),
        ``completed``, ``has_companies``, ``default_company``,
        ``default_configured`` and ``path`` (where the document lives).
    """
    state = read_state()
    companies = companies or []
    default = next(
        (company for company in companies if company.get("is_default")),
        None,
    )
    default_configured = company_configured(default)
    no_companies = not companies
    # ``completed_at`` is the durable completion mark: it survives a legacy
    # status patch and is only cleared by an explicit reset.
    completed = state["status"] == "completed" or bool(state["completed_at"])
    # A completion is only meaningful while the company it configured still
    # exists. Deleting every company must reopen the gate even though the
    # onboarding document remembers the old completion.
    completed_company_id = str(state.get("company_id") or "")
    completed_company = next(
        (
            company for company in companies
            if str(company.get("id")) == completed_company_id
            or str(company.get("slug") or "") == completed_company_id
        ),
        None,
    ) if completed_company_id else None
    completion_valid = completed and (
        not completed_company_id or completed_company is not None
    )
    # The gate is authoritative: while it is closed the product cannot be used.
    # Finishing the wizard is the ONLY way to open it on a fresh installation.
    # An installation that already has a configured default company (an upgrade)
    # is never locked out, even if its onboarding document predates the wizard.
    setup_required = no_companies or (
        not completion_valid and not default_configured
    )
    return {
        "state": state,
        "steps": STEP_CATALOG,
        "companies": companies,
        # `needs_setup` is the original name used by the shell to open the
        # wizard; it now also means "the product is locked".
        "needs_setup": setup_required,
        "setup_required": setup_required,
        "completed": completion_valid and not no_companies,
        "skippable": not setup_required,
        "has_companies": not no_companies,
        "default_company": default,
        "default_configured": default_configured,
        "installed": True,
        "path": onboarding_path(),
    }


# Backwards-compatible private alias: the previous implementation exposed this
# helper as ``_company_configured`` to the API module.
_company_configured = company_configured
