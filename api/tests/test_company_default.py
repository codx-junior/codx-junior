"""Tests for the default company bootstrap.

The company is the top-level element of the product. A fresh installation must
not start empty: it gets a default company that is linked to every discovered
project, and that company is kept in sync with newly discovered projects until
the user edits the project list.
"""

from __future__ import annotations

import pytest

from codx.junior.company.manager import CompanyManager
from codx.junior.company.models import CompanyProject, CompanyTeam
from codx.junior.company.store import CompanyStore


@pytest.fixture()
def projects():
    """Mutable project catalogue shared with the manager."""
    return [
        {"project_id": "p-api", "project_name": "API"},
        {"project_id": "p-web", "project_name": "Website"},
    ]


@pytest.fixture()
def manager(tmp_path, projects):
    """Company manager with a fake project and user catalogue."""
    return CompanyManager(
        store=CompanyStore(root_path=str(tmp_path / "companies")),
        projects_provider=lambda: projects,
        users_provider=lambda: [],
        profiles_provider=lambda project_ids=None: [],
    )


def test_fresh_installation_gets_a_default_company_with_every_project(manager):
    company = manager.ensure_default_company(actor="alice")

    assert company.is_default is True
    assert company.slug == "default"
    assert company.name
    assert [project.project_id for project in company.projects] == ["p-api", "p-web"]
    assert company.auto_sync_projects is True


def test_default_company_is_idempotent_and_links_new_projects(manager, projects):
    first = manager.ensure_default_company(actor="alice")

    # No duplicate company on a second call.
    assert len(manager.store.list_ids()) == 1
    assert manager.ensure_default_company(actor="alice").id == first.id

    projects.append({"project_id": "p-mobile", "project_name": "Mobile"})
    synced = manager.ensure_default_company(actor="alice")

    assert [project.project_id for project in synced.projects] == [
        "p-api",
        "p-web",
        "p-mobile",
    ]


def test_list_companies_provisions_the_default_on_a_fresh_store(manager):
    companies = manager.list_companies()

    assert len(companies) == 1
    assert companies[0]["is_default"] is True
    assert companies[0]["project_ids"] == ["p-api", "p-web"]


def test_editing_the_project_list_stops_automatic_sync(manager, projects):
    company = manager.ensure_default_company(actor="alice")

    updated = manager.set_project_links(company.id, ["p-api"], actor="alice")

    assert [project.project_id for project in updated.projects] == ["p-api"]
    assert updated.auto_sync_projects is False

    # A project discovered later is NOT re-added behind the user's back.
    projects.append({"project_id": "p-new", "project_name": "New"})
    assert [
        project.project_id
        for project in manager.ensure_default_company(actor="alice").projects
    ] == ["p-api"]


def test_set_project_links_preserves_existing_project_metadata(manager):
    company = manager.ensure_default_company(actor="alice")
    manager.link_project(
        company.id,
        "p-api",
        actor="alice",
        role="platform",
        team_id="",
        description="The API",
    )

    updated = manager.set_project_links(company.id, ["p-api", "p-web"], actor="alice")
    api = next(project for project in updated.projects if project.project_id == "p-api")

    assert api.role == "platform"
    assert api.description == "The API"


def test_existing_installation_adopts_its_first_company(tmp_path, projects):
    store = CompanyStore(root_path=str(tmp_path / "companies"))
    manager = CompanyManager(
        store=store,
        projects_provider=lambda: projects,
        users_provider=lambda: [],
        profiles_provider=lambda project_ids=None: [],
    )
    legacy = manager.create_company({"name": "Legacy Co"}, actor="alice")

    adopted = manager.ensure_default_company(actor="alice")

    assert adopted.id == legacy.id
    assert adopted.is_default is True
    # Adoption never silently rewrites the project list.
    assert adopted.projects == []


def _company_with_team(manager):
    """A company with an autonomous team and one linked project."""
    company = manager.create_company(
        {
            "name": "Autonomous Co",
            "projects": [CompanyProject(project_id="p-api", name="API")],
            "teams": [
                CompanyTeam(
                    id="team-1",
                    name="Delivery",
                    autonomy="autonomous",
                    project_ids=["p-api"],
                )
            ],
        },
        actor="alice",
    )
    return company


def test_goal_created_in_an_autonomous_company_starts_itself(manager):
    """A goal must not stay 'manual' when the company already runs autonomously."""
    company = _company_with_team(manager)
    manager.update_autonomy(
        company.id, {"enabled": True, "level": "supervised"}, actor="alice"
    )

    updated = manager.add_mission(
        company.id,
        {
            "title": "Ship the customer flow",
            "objective": "A customer can sign up and create a task",
            "team_id": "team-1",
            "project_id": "p-api",
            "status": "ready",
        },
        actor="alice",
    )

    mission = updated.missions[0]
    assert mission.dispatch_mode == "auto"
    # The autopilot now sees it as a candidate instead of ignoring it.
    assert manager._dispatchable_missions(updated) == [mission]


def test_sync_all_missions_closes_a_finished_run_without_a_cycle(manager):
    """A human-dispatched run must not leave its goal 'running' forever."""
    company = _company_with_team(manager)
    updated = manager.add_mission(
        company.id,
        {
            "title": "Ship the customer flow",
            "objective": "create a task on the deployed workspace",
            "team_id": "team-1",
            "project_id": "p-api",
            "status": "ready",
            "dispatch_mode": "manual",
            "run_id": "run-1",
        },
        actor="alice",
    )
    updated.missions[0].status = "running"
    manager.store.save(updated)
    manager._run_status_provider = lambda project_id, run_id: {
        "status": "completed",
        "progress": 100,
    }

    reports = manager.sync_all_missions(actor="test")

    synced = manager.get_company(company.id).missions[0]
    assert synced.status == "review"
    assert synced.progress == 100
    assert reports and reports[0]["changed"] == 1


def test_choosing_an_autonomy_level_enables_the_autopilot(manager):
    """Clicking "Autonomous" must enable dispatch, not just set a label."""
    company = _company_with_team(manager)

    updated = manager.update_autonomy(
        company.id, {"level": "autonomous"}, actor="alice"
    )

    assert updated.autonomy.level == "autonomous"
    assert updated.autonomy.enabled is True
    assert updated.autonomy.is_due() is True
