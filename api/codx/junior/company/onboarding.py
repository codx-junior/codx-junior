"""
Installation onboarding — the "New company" wizard state.

After codx-junior is installed the product renders the *company mode* and runs a
short wizard that guides the owner through the first company, its projects and
the basic AI settings. The wizard is **skippable** (every step and the whole
wizard) and **re-runnable** at any time from the company menu.

Where the state lives
---------------------
``<companies_root>/onboarding.json`` — one document per installation, so the
wizard is offered exactly once per install and not once per company. It stores:

```json
{
  "version": 1,
  "status": "pending" | "completed" | "skipped",
  "steps": {"welcome": "done", "company": "skipped"},
  "settings": {"default_model": "gpt-4o-mini"},
  "company_id": "acme",
  "updated_at": "…",
  "updated_by": "admin"
}
```

The document is written atomically (tmp file + ``os.replace``) so a wizard
running in the browser can never leave a half-written file behind. Corruption
degrades to the default state: a broken onboarding file must never block the
product.
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
        "description": "Which repositories the company works with.",
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
    if status in ("pending", "completed", "skipped"):
        state["status"] = status
    step = str(patch.get("step") or "").strip()
    step_status = str(patch.get("step_status") or "").strip().lower()
    if step and step_status in ("pending", "done", "skipped"):
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


def build_status(companies: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Build the payload the wizard renders.

    Args:
        companies: Company summaries (the wizard offers to *configure* an
            existing one instead of creating a duplicate).

    Returns:
        A dict with the stored ``state``, the ``steps`` catalog, the
        ``companies`` summaries, ``needs_setup`` (the wizard should open by
        itself) and ``path`` (where the document lives, for support).
    """
    state = read_state()
    companies = companies or []
    return {
        "state": state,
        "steps": STEP_CATALOG,
        "companies": companies,
        "needs_setup": state["status"] == "pending",
        "installed": True,
        "path": onboarding_path(),
    }
