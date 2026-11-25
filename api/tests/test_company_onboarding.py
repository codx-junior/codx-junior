"""Default-company provisioning and the onboarding wizard gate.

The wizard is the first-run **gate**: on a fresh installation there is no
company at all, or the auto-provisioned default company is still a placeholder
(the old ``ACME`` / current ``codx-junior``) the owner never set up. The product
stays locked until the owner finishes the wizard; only ``completed`` opens it.
Provisioning itself must stay idempotent even with several API workers.
"""

import threading

import pytest

from codx.junior.company.manager import (
    AUTO_DEFAULT_DESCRIPTION,
    DEFAULT_COMPANY_NAME,
    DEFAULT_COMPANY_SLUG,
    CompanyManager,
)
from codx.junior.company.onboarding import build_status, merge_state


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CODX_JUNIOR_COMPANIES_PATH", str(tmp_path / "companies")
    )
    # These tests describe the company-first product; the 0.0.1 `core` profile
    # (which hides the company shell) is covered by test_features.py and by the
    # explicit workspace-mode test below.
    monkeypatch.delenv("CODX_JUNIOR_ACTIVE_FEATURES", raising=False)


def _manager():
    return CompanyManager(
        projects_provider=lambda: [],
        users_provider=lambda: [],
        profiles_provider=lambda project_ids=None: [],
        project_settings_provider=lambda project_id: None,
    )


def _company_summary(**overrides):
    summary = {
        "id": "cmp-test",
        "name": "codx-junior",
        "slug": "codx-junior",
        "description": AUTO_DEFAULT_DESCRIPTION,
        "is_default": True,
        "auto_sync_projects": True,
        "all_projects": True,
        "logo": "",
        "counts": {"teams": 0, "missions": 0},
        "project_ids": [],
    }
    summary.update(overrides)
    return summary


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def test_a_fresh_install_creates_the_codx_junior_default():
    manager = _manager()

    company = manager.ensure_default_company(actor="system")

    assert company.name == DEFAULT_COMPANY_NAME == "codx-junior"
    assert company.slug == DEFAULT_COMPANY_SLUG == "codx-junior"
    assert company.is_default is True
    assert company.all_projects is True
    # Idempotent: the second call never creates another company.
    again = manager.ensure_default_company(actor="system")
    assert again.id == company.id
    assert len(manager.list_companies(include_archived=True)) == 1


def test_concurrent_bootstraps_create_exactly_one_default():
    """Two API workers seeing an empty store must not each create a company."""
    barrier = threading.Barrier(8)
    results = []
    errors = []

    def bootstrap():
        try:
            manager = _manager()
            barrier.wait(timeout=10)
            results.append(manager.ensure_default_company(actor="system").id)
        except Exception as ex:  # pragma: no cover - surfaced below
            errors.append(ex)

    threads = [threading.Thread(target=bootstrap) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert len(set(results)) == 1
    manager = _manager()
    companies = manager.list_companies(include_archived=True)
    assert len(companies) == 1
    assert companies[0]["is_default"] is True


def test_a_legacy_acme_default_is_renamed_to_codx_junior():
    manager = _manager()
    legacy = manager.create_company(
        {
            "name": "ACME",
            "slug": "acme",
            "description": AUTO_DEFAULT_DESCRIPTION,
            "is_default": True,
            "auto_sync_projects": True,
            "all_projects": False,
            "tags": ["default"],
        },
        actor="system",
    )

    migrated = manager.ensure_default_company(actor="system")

    assert migrated.id == legacy.id
    assert migrated.name == "codx-junior"
    assert migrated.slug == "codx-junior"
    assert migrated.all_projects is True


def test_an_owner_configured_default_keeps_its_name():
    manager = _manager()
    custom = manager.create_company(
        {
            "name": "Acme Studio",
            "description": "The studio that builds the product",
            "is_default": True,
            "auto_sync_projects": False,
            "all_projects": False,
            "tags": ["default"],
        },
        actor="owner",
    )

    unchanged = manager.ensure_default_company(actor="owner")

    assert unchanged.id == custom.id
    assert unchanged.name == "Acme Studio"
    assert unchanged.slug == "acme-studio"


def test_duplicate_defaults_are_reconciled_to_one():
    manager = _manager()
    first = manager.create_company(
        {
            "name": "codx-junior",
            "slug": "codx-junior",
            "description": AUTO_DEFAULT_DESCRIPTION,
            "is_default": True,
            "all_projects": True,
            "tags": ["default"],
        },
        actor="system",
    )
    second = manager.create_company(
        {
            "name": "ACME",
            "slug": "acme",
            "description": AUTO_DEFAULT_DESCRIPTION,
            "is_default": True,
            "all_projects": True,
            "tags": ["default"],
        },
        actor="system",
    )

    canonical = manager.ensure_default_company(actor="system")

    assert canonical.id == first.id
    reloaded = manager.get_company(second.id)
    assert reloaded.is_default is False
    assert reloaded.status == "archived"
    visible = manager.list_companies(include_archived=False)
    assert [item["id"] for item in visible] == [first.id]


def test_an_existing_non_default_company_is_adopted_as_the_default():
    manager = _manager()
    existing = manager.create_company(
        {"name": "Legacy Company", "description": "Kept as is"}, actor="owner"
    )

    adopted = manager.ensure_default_company(actor="owner")

    assert adopted.id == existing.id
    assert adopted.is_default is True
    assert adopted.name == "Legacy Company"
    assert adopted.auto_sync_projects is False


# ---------------------------------------------------------------------------
# Wizard gate
# ---------------------------------------------------------------------------


def test_the_wizard_opens_when_there_is_no_company():
    merge_state({"status": "pending"}, actor="tester")
    status = build_status([])
    assert status["needs_setup"] is True
    assert status["has_companies"] is False


def test_the_wizard_opens_while_the_default_is_a_placeholder():
    merge_state({"status": "pending"}, actor="tester")
    status = build_status([_company_summary()])
    assert status["needs_setup"] is True
    assert status["default_configured"] is False


def test_the_wizard_stays_closed_after_the_default_was_configured():
    merge_state({"status": "pending"}, actor="tester")
    configured = _company_summary(
        name="Acme Studio",
        description="The studio that builds the product",
        auto_sync_projects=False,
        all_projects=False,
    )
    status = build_status([configured])
    assert status["needs_setup"] is False
    assert status["default_configured"] is True


def test_a_legacy_skip_does_not_open_the_gate():
    """Skipping is not finishing: a fresh install must define its company."""
    merge_state({"status": "skipped"}, actor="tester")
    status = build_status([_company_summary()])
    assert status["setup_required"] is True
    assert status["needs_setup"] is True
    assert status["completed"] is False


def test_the_workspace_profile_skips_the_company_gate(monkeypatch):
    """0.0.1 defaults to `core`: no company shell means no company wizard."""
    monkeypatch.setenv("CODX_JUNIOR_ACTIVE_FEATURES", "core")

    status = build_status([_company_summary()])

    assert status["company_mode"] is False
    assert status["setup_required"] is False
    assert status["needs_setup"] is False
    assert status["skippable"] is True


def test_finishing_the_wizard_opens_the_gate():
    merge_state({"status": "completed"}, actor="tester")
    status = build_status([_company_summary()])
    assert status["setup_required"] is False
    assert status["needs_setup"] is False
    assert status["completed"] is True
    assert status["skippable"] is True


def test_finishing_with_the_configured_company_opens_the_gate():
    merge_state(
        {"status": "completed", "company_id": "cmp-test"},
        actor="tester",
    )
    status = build_status([_company_summary()])
    assert status["completed"] is True
    assert status["setup_required"] is False


def test_finishing_records_when_the_wizard_was_completed():
    state = merge_state({"status": "completed"}, actor="tester")
    assert state["completed_at"], "completion must be auditable"
    assert merge_state({"reset": True}, actor="tester")["completed_at"] == ""


def test_a_completed_installation_is_not_relocked_by_a_re_run():
    """Skip/pending patches cannot downgrade a finished wizard; reset can."""
    merge_state({"status": "completed"}, actor="tester")
    skipped = merge_state({"status": "skipped"}, actor="tester")
    assert skipped["status"] == "completed"
    assert skipped["completed_at"]
    assert build_status([_company_summary()])["setup_required"] is False
    pending = merge_state({"status": "pending"}, actor="tester")
    assert pending["status"] == "completed"
    # Re-opening the gate is an explicit product action, never a side effect.
    reset = merge_state({"reset": True}, actor="tester")
    assert reset["status"] == "pending"
    assert reset["completed_at"] == ""


def test_an_upgrade_with_a_configured_company_is_never_locked_out():
    """Installs that predate the wizard keep working: no company basics to ask."""
    merge_state({"status": "pending"}, actor="tester")
    configured = _company_summary(
        name="Acme Studio",
        description="The studio that builds the product",
        auto_sync_projects=False,
        all_projects=False,
    )
    status = build_status([configured])
    assert status["setup_required"] is False
    assert status["needs_setup"] is False


def test_deleting_the_completed_company_reopens_the_gate():
    """A durable completion cannot keep an installation usable after its company is gone."""
    merge_state(
        {"status": "completed", "company_id": "cmp-deleted"},
        actor="tester",
    )
    status = build_status([_company_summary()])
    assert status["completed"] is False
    assert status["setup_required"] is True


def test_the_wizard_opens_without_companies_even_after_a_decision():
    merge_state({"status": "completed"}, actor="tester")
    status = build_status([])
    assert status["needs_setup"] is True
    assert status["setup_required"] is True


def test_a_deployment_named_default_is_still_a_placeholder(monkeypatch):
    """CODX_JUNIOR_DEFAULT_COMPANY_NAME must not defeat the first-run gate."""
    monkeypatch.setenv("CODX_JUNIOR_DEFAULT_COMPANY_NAME", "MyOrg")
    merge_state({"status": "pending"}, actor="tester")
    status = build_status([_company_summary(name="MyOrg")])
    assert status["default_configured"] is False
    assert status["setup_required"] is True


def test_editing_projects_counts_as_a_configuration_signal():
    merge_state({"status": "pending"}, actor="tester")
    edited = _company_summary(auto_sync_projects=False, all_projects=False)
    status = build_status([edited])
    assert status["default_configured"] is True
    assert status["needs_setup"] is False


def test_only_the_default_company_decides_the_gate():
    merge_state({"status": "pending"}, actor="tester")
    placeholder_other = _company_summary(
        id="cmp-other", name="codx-junior", is_default=False
    )
    configured_default = _company_summary(
        id="cmp-default",
        name="Codx Junior Cloud",
        description="The company that runs the platform",
        auto_sync_projects=False,
        all_projects=False,
    )
    status = build_status([placeholder_other, configured_default])
    assert status["default_company"]["id"] == "cmp-default"
    assert status["default_configured"] is True
    assert status["needs_setup"] is False


def test_a_missing_default_keeps_the_wizard_open():
    merge_state({"status": "pending"}, actor="tester")
    only_other = _company_summary(
        id="cmp-other",
        name="Another company",
        description="Something",
        is_default=False,
    )
    status = build_status([only_other])
    assert status["default_company"] is None
    assert status["default_configured"] is False
    assert status["needs_setup"] is True
