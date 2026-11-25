"""Install-time feature profile (``CODX_JUNIOR_ACTIVE_FEATURES``).

The 0.0.1 installer writes ``core`` by default: the company OS is hidden and the
instance starts in the project workspace. ``all`` reproduces the full product
and an unset variable keeps the historical all-features behaviour.
"""

import pytest

from codx.junior import features


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv(features.FEATURES_ENV, raising=False)


def test_unset_means_all_for_existing_installations():
    profile, active = features.resolve_profile()

    assert profile == features.PROFILE_ALL
    assert len(active) > 40
    assert features.company_enabled() is True


def test_core_hides_the_company_shell_and_wizard(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "core")

    profile, active = features.resolve_profile()

    assert profile == features.PROFILE_CORE
    assert "install" in active
    assert "workspace-shell" in active
    assert not any(feature.startswith("company") for feature in active)
    assert features.company_enabled() is False
    assert features.company_wizard_enabled() is False


def test_all_enables_the_company_first_product(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "all")

    assert features.resolve_profile()[0] == features.PROFILE_ALL
    assert features.company_enabled() is True
    assert features.company_wizard_enabled() is True


def test_a_custom_set_can_expand_core_and_add_a_feature(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "core,mcp")

    profile, active = features.resolve_profile()

    assert profile == features.PROFILE_CUSTOM
    assert "mcp" in active
    assert "workspace-shell" in active
    assert features.company_enabled() is False


def test_a_company_feature_turns_the_company_shell_on(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "core,company-business")

    assert features.company_enabled() is True


def test_unknown_ids_do_not_enable_a_hidden_surface(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "core,definitely-not-a-feature")

    profile, active = features.resolve_profile()

    assert profile == features.PROFILE_CUSTOM
    assert "definitely-not-a-feature" not in active
    assert features.is_active("definitely-not-a-feature") is False


def test_describe_is_the_public_payload(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "core")

    payload = features.describe()

    assert payload["version"] == "0.0.1"
    assert payload["profile"] == "core"
    assert payload["company"] is False
    assert payload["company_wizard"] is False
    assert payload["manifest_hash"]
    assert "company-business" in payload["hidden"]


def test_the_api_router_exposes_the_payload(monkeypatch):
    monkeypatch.setenv(features.FEATURES_ENV, "core")
    from codx.junior.api.features import api_features

    payload = api_features()

    assert payload["profile"] == "core"
    assert payload["company"] is False
