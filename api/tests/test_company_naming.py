"""Company naming: renaming keeps name and slug in sync, duplicates never fail."""

import os
import tempfile

import pytest

os.environ.setdefault("CODX_JUNIOR_COMPANIES_PATH", tempfile.mkdtemp())

from codx.junior.company.manager import (  # noqa: E402
    CompanyConflict,
    CompanyManager,
    CompanyValidationError,
)


@pytest.fixture()
def manager(monkeypatch):
    monkeypatch.setenv("CODX_JUNIOR_COMPANIES_PATH", tempfile.mkdtemp())
    return CompanyManager()


def test_rename_updates_name_and_slug(manager):
    company = manager.create_company({"name": "Acme Corp"}, actor="tester")
    assert company.slug == "acme-corp"

    renamed = manager.update_company(company.id, {"name": "Acme Studio"}, actor="tester")

    assert renamed.name == "Acme Studio"
    assert renamed.slug == "acme-studio"


def test_rename_frees_the_old_name(manager):
    """The reported bug: after a rename the old name must be creatable again."""
    company = manager.create_company({"name": "Codx Junior"}, actor="tester")
    manager.update_company(company.id, {"name": "Codx Junior Website"}, actor="tester")

    created = manager.create_company({"name": "Codx Junior"}, actor="tester")

    assert created.name == "Codx Junior"
    assert created.slug == "codx-junior"


def test_rename_to_a_taken_name_keeps_a_unique_slug(manager):
    first = manager.create_company({"name": "Acme"}, actor="tester")
    second = manager.create_company({"name": "Globex"}, actor="tester")

    renamed = manager.update_company(second.id, {"name": "Acme"}, actor="tester")

    assert renamed.name == "Acme"
    assert renamed.slug == "acme-2"
    assert manager.get_company(first.id).slug == "acme"


def test_create_with_a_repeated_name_gets_a_suffix(manager):
    first = manager.create_company({"name": "Acme"}, actor="tester")
    second = manager.create_company({"name": "Acme"}, actor="tester")

    assert (first.slug, second.slug) == ("acme", "acme-2")


def test_explicit_duplicated_slug_still_conflicts(manager):
    manager.create_company({"name": "Acme", "slug": "shared"}, actor="tester")

    with pytest.raises(CompanyConflict):
        manager.create_company({"name": "Other", "slug": "shared"}, actor="tester")


def test_logo_is_stored_and_cleared(manager):
    company = manager.create_company({"name": "Acme"}, actor="tester")
    logo = "data:image/png;base64,iVBORw0KGgo="

    saved = manager.update_company(company.id, {"logo": logo}, actor="tester")
    assert saved.logo == logo

    cleared = manager.update_company(company.id, {"logo": ""}, actor="tester")
    assert cleared.logo == ""


def test_logo_accepts_an_https_url(manager):
    company = manager.create_company({"name": "Acme"}, actor="tester")

    saved = manager.update_company(
        company.id, {"logo": "https://cdn.example.com/logo.png"}, actor="tester"
    )

    assert saved.logo == "https://cdn.example.com/logo.png"


@pytest.mark.parametrize(
    "logo",
    [
        "javascript:alert(1)",
        "/relative/logo.png",
        "data:text/html;base64,PHNjcmlwdD4=",
        "data:image/png;base64,",
    ],
)
def test_invalid_logo_is_rejected(manager, logo):
    company = manager.create_company({"name": "Acme"}, actor="tester")

    with pytest.raises(CompanyValidationError):
        manager.update_company(company.id, {"logo": logo}, actor="tester")
