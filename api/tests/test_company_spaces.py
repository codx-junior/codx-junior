"""Company-level coworking spaces and their virtual file browser.

A space belongs to the **company** (not to a project): it stores its own files
and exposes the company's linked projects as virtual, read-only folders. These
tests cover the stored document, the company-file operations and the virtual
project/mapped-folder navigation.
"""

import json
import os
import tempfile

import pytest

# The tests create real company documents: keep them out of the installation
# store even when a test builds its own CompanyManager directly.
os.environ.setdefault("CODX_JUNIOR_COMPANIES_PATH", tempfile.mkdtemp())

from codx.junior.company.manager import CompanyManager  # noqa: E402
from codx.junior.company.spaces import (  # noqa: E402
    FOLDERS_ROOT,
    PROJECTS_ROOT,
    SpacePathError,
    space_files_root,
)


@pytest.fixture(autouse=True)
def _isolated_companies_root(tmp_path, monkeypatch):
    """Every test gets a private companies root (never the installation one)."""
    monkeypatch.setenv(
        "CODX_JUNIOR_COMPANIES_PATH", str(tmp_path / "companies")
    )


def _catalog(root, project_id="p1", name="Project One"):
    project_dir = os.path.join(str(root), "projects", project_id)
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(project_dir, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("# Project One\n")
    return [
        {
            "project_id": project_id,
            "project_name": name,
            "project_path": project_dir,
            "codx_path": os.path.join(str(root), ".codx", project_id),
        }
    ]


def _manager(tmp_path, catalog):
    return CompanyManager(
        projects_provider=lambda: catalog,
        project_settings_provider=lambda project_id: None,
    )


def _company(tmp_path, catalog):
    manager = _manager(tmp_path, catalog)
    company = manager.create_company(
        {"name": "Acme"}, actor="tester", project_ids=["p1"]
    )
    return manager, company


def _default_space(manager, company):
    return manager.list_spaces(company.id)[0]


def test_a_company_owns_its_space_and_its_own_files_folder(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)

    space = _default_space(manager, company)

    assert space["name"] == "Company files"
    assert space["project_ids"] == ["p1"]
    assert space["projects"][0]["name"] == "Project One"
    folder = space_files_root(
        manager.store.company_dir(company.id), space["id"]
    )
    assert os.path.isdir(folder)

    # The space is persisted inside the company document, not in a project.
    with open(
        manager.store.company_path(company.id), "r", encoding="utf-8"
    ) as handle:
        stored = json.load(handle)
    assert stored["spaces"][0]["id"] == space["id"]
    assert stored["spaces"][0]["project_ids"] == ["p1"]


def test_company_files_are_created_listed_written_and_deleted(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)
    space = _default_space(manager, company)

    created = manager.space_create_folder(company.id, space["id"], "", "docs")
    assert created == {"name": "docs", "path": "docs", "is_dir": True}
    manager.space_write_text(
        company.id, space["id"], "docs/brief.txt", "the company brief"
    )
    uploaded = manager.space_upload(
        company.id, space["id"], "docs", "logo.txt", b"logo"
    )
    assert uploaded["path"] == "docs/logo.txt"

    listing = manager.space_listing(company.id, space["id"], "docs")
    names = sorted(entry["name"] for entry in listing["entries"])
    assert names == ["brief.txt", "logo.txt"]
    assert all(entry["readonly"] is False for entry in listing["entries"])

    entry = manager.space_entry(company.id, space["id"], "docs/brief.txt")
    assert entry["is_dir"] is False
    assert entry["readonly"] is False

    manager.space_delete_entry(company.id, space["id"], "docs/brief.txt")
    listing = manager.space_listing(company.id, space["id"], "docs")
    assert [entry["name"] for entry in listing["entries"]] == ["logo.txt"]


def test_root_shows_virtual_roots_and_the_company_files(tmp_path):
    catalog = _catalog(tmp_path)
    external = tmp_path / "shared"
    external.mkdir()
    (external / "roadmap.md").write_text("roadmap", encoding="utf-8")

    manager, company = _company(tmp_path, catalog)
    space = _default_space(manager, company)
    manager.space_create_folder(company.id, space["id"], "", "company")
    manager.update_space(
        company.id,
        space["id"],
        {"folders": [{"path": str(external)}]},
        actor="tester",
    )

    listing = manager.space_listing(company.id, space["id"], "")
    by_path = {entry["path"]: entry for entry in listing["entries"]}

    assert by_path[PROJECTS_ROOT]["kind"] == "projects_root"
    assert by_path[FOLDERS_ROOT]["kind"] == "folders_root"
    assert by_path["company"]["kind"] == "folder"
    # The virtual roots come first so the browser can pin them.
    assert [entry["path"] for entry in listing["entries"]][:2] == [
        PROJECTS_ROOT,
        FOLDERS_ROOT,
    ]


def test_projects_are_virtual_read_only_folders_of_the_company(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)
    space = _default_space(manager, company)

    roots = manager.space_listing(company.id, space["id"], PROJECTS_ROOT)
    assert [entry["name"] for entry in roots["entries"]] == ["Project One"]
    project_entry = roots["entries"][0]
    assert project_entry["path"] == "projects/p1"
    assert project_entry["readonly"] is True

    project_listing = manager.space_listing(
        company.id, space["id"], "projects/p1"
    )
    assert [entry["name"] for entry in project_listing["entries"]] == [
        "README.md"
    ]
    assert project_listing["readonly"] is True
    assert project_listing["breadcrumbs"][1]["name"] == "Projects"

    entry = manager.space_entry(company.id, space["id"], "projects/p1/README.md")
    assert entry["readonly"] is True
    assert entry["kind"] == "project"
    assert entry["project_id"] == "p1"

    # Browsing a project never writes through the company space.
    with pytest.raises(SpacePathError):
        manager.space_write_text(
            company.id, space["id"], "projects/p1/created.txt", "nope"
        )


def test_mapped_host_folders_are_read_only_by_default(tmp_path):
    catalog = _catalog(tmp_path)
    external = tmp_path / "contracts"
    external.mkdir()
    (external / "terms.md").write_text("terms", encoding="utf-8")
    manager, company = _company(tmp_path, catalog)
    space = manager.add_space(
        company.id,
        {"name": "Shared", "folders": [{"path": str(external)}]},
        actor="tester",
    )

    listing = manager.space_listing(company.id, space["id"], FOLDERS_ROOT)
    folder_entry = listing["entries"][0]
    assert folder_entry["name"] == "contracts"
    assert folder_entry["readonly"] is True

    inner = manager.space_listing(
        company.id, space["id"], f"{FOLDERS_ROOT}/{folder_entry['folder_id']}"
    )
    assert [entry["name"] for entry in inner["entries"]] == ["terms.md"]

    with pytest.raises(SpacePathError):
        manager.space_write_text(
            company.id,
            space["id"],
            f"{FOLDERS_ROOT}/{folder_entry['folder_id']}/new.md",
            "nope",
        )


def test_a_mapped_host_folder_can_allow_writes(tmp_path):
    catalog = _catalog(tmp_path)
    external = tmp_path / "deliverables"
    external.mkdir()
    manager, company = _company(tmp_path, catalog)
    space = manager.add_space(
        company.id,
        {
            "name": "Writable",
            "folders": [{"path": str(external), "readonly": False}],
        },
        actor="tester",
    )
    folder_id = space["folders"][0]["id"]

    written = manager.space_write_text(
        company.id, space["id"], f"{FOLDERS_ROOT}/{folder_id}/plan.md", "plan"
    )
    assert written["path"] == f"{FOLDERS_ROOT}/{folder_id}/plan.md"
    assert (external / "plan.md").read_text(encoding="utf-8") == "plan"

    listing = manager.space_listing(
        company.id, space["id"], f"{FOLDERS_ROOT}/{folder_id}"
    )
    assert listing["readonly"] is False
    assert [entry["name"] for entry in listing["entries"]] == ["plan.md"]


def test_a_space_can_map_a_company_project_and_a_folder(tmp_path):
    catalog = _catalog(tmp_path)
    external = tmp_path / "shared"
    external.mkdir()
    manager, company = _company(tmp_path, catalog)

    space = manager.add_space(
        company.id,
        {
            "name": "Delivery",
            "description": "What the company is delivering",
            "project_ids": ["p1"],
            "folders": [{"name": "Shared drive", "path": str(external)}],
        },
        actor="tester",
    )
    assert space["project_ids"] == ["p1"]
    assert space["folders"][0]["name"] == "Shared drive"

    updated = manager.update_space(
        company.id,
        space["id"],
        {"name": "Delivery room", "project_ids": []},
        actor="tester",
    )
    assert updated["name"] == "Delivery room"
    assert updated["project_ids"] == []

    deleted = manager.delete_space(company.id, space["id"], actor="tester")
    assert deleted["deleted"] is True
    assert manager.get_company(company.id).space(space["id"]) is None


def test_space_names_are_unique_and_paths_are_validated(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)
    manager.add_space(company.id, {"name": "Shared"}, actor="tester")

    with pytest.raises(Exception):
        manager.add_space(company.id, {"name": "shared"}, actor="tester")
    with pytest.raises(Exception):
        manager.add_space(
            company.id,
            {"name": "Escape", "folders": [{"path": "relative/folder"}]},
            actor="tester",
        )
    with pytest.raises(Exception):
        manager.add_space(
            company.id,
            {"name": "Unknown project", "project_ids": ["not-linked"]},
            actor="tester",
        )


def test_paths_cannot_escape_the_space_root(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)
    space = _default_space(manager, company)

    with pytest.raises(SpacePathError):
        manager.space_listing(company.id, space["id"], "../outside")
    with pytest.raises(SpacePathError):
        manager.space_write_text(
            company.id, space["id"], "../escape.txt", "nope"
        )


def test_a_non_empty_company_folder_cannot_be_deleted(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)
    space = _default_space(manager, company)
    manager.space_create_folder(company.id, space["id"], "", "docs")
    manager.space_write_text(company.id, space["id"], "docs/kept.txt", "keep")

    with pytest.raises(SpacePathError):
        manager.space_delete_entry(company.id, space["id"], "docs")


def test_snapshot_includes_the_company_spaces(tmp_path):
    catalog = _catalog(tmp_path)
    manager, company = _company(tmp_path, catalog)
    space = _default_space(manager, company)

    snapshot = manager.snapshot(company.id)

    assert [entry["id"] for entry in snapshot["spaces"]] == [space["id"]]
    assert snapshot["company"]["spaces"][0]["id"] == space["id"]
    assert snapshot["counts"]["spaces"] == 1


def test_api_exposes_the_space_routes():
    from codx.junior.api.company import router

    paths = [getattr(route, "path", "") for route in router.routes]
    assert "/companies/{company_id}/spaces" in paths
    assert "/companies/{company_id}/spaces/{space_id}" in paths
    assert "/companies/{company_id}/spaces/{space_id}/files" in paths
    assert "/companies/{company_id}/spaces/{space_id}/file" in paths
    assert "/companies/{company_id}/spaces/{space_id}/folders" in paths
    assert "/companies/{company_id}/spaces/{space_id}/upload" in paths


def test_http_browser_reads_the_company_files_and_the_virtual_project(tmp_path, monkeypatch):
    """End-to-end HTTP contract of the company file browser."""
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import codx.junior.api.company as company_api
    from codx.junior.model.model import CodxUser

    monkeypatch.setenv(
        "CODX_JUNIOR_COMPANIES_PATH", str(tmp_path / "companies")
    )
    catalog = _catalog(tmp_path)
    # The API builds its own manager, so discovery is patched where it is read.
    monkeypatch.setattr(
        company_api, "_manager", lambda: _manager(tmp_path, catalog)
    )

    app = FastAPI()
    app.include_router(company_api.router, prefix="/api")
    app.dependency_overrides[company_api.get_authenticated_user] = lambda: CodxUser(
        # A synthetic principal: creating the company must not enroll a real
        # installation user in a company that only exists in this tmp store.
        username="company-spaces-test", role="admin"
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/companies", json={"name": "Acme", "project_ids": ["p1"]}
        )
        assert created.status_code == 201, created.text
        company_id = created.json()["id"]

        spaces = client.get(f"/api/companies/{company_id}/spaces")
        assert spaces.status_code == 200, spaces.text
        space_id = spaces.json()[0]["id"]

        folder = client.post(
            f"/api/companies/{company_id}/spaces/{space_id}/folders",
            json={"path": "", "name": "docs"},
        )
        assert folder.status_code == 201, folder.text

        written = client.put(
            f"/api/companies/{company_id}/spaces/{space_id}/file",
            json={"path": "docs/brief.txt", "content": "company brief"},
        )
        assert written.status_code == 200, written.text

        listed = client.get(
            f"/api/companies/{company_id}/spaces/{space_id}/files",
            params={"path": "docs"},
        )
        assert listed.status_code == 200, listed.text
        assert [entry["name"] for entry in listed.json()["entries"]] == [
            "brief.txt"
        ]

        read = client.get(
            f"/api/companies/{company_id}/spaces/{space_id}/file",
            params={"path": "docs/brief.txt"},
        )
        assert read.status_code == 200
        assert read.text == "company brief"

        root = client.get(
            f"/api/companies/{company_id}/spaces/{space_id}/files"
        )
        assert root.status_code == 200
        entries = {entry["path"]: entry for entry in root.json()["entries"]}
        assert entries["projects"]["kind"] == "projects_root"
        assert "docs" in entries

        project = client.get(
            f"/api/companies/{company_id}/spaces/{space_id}/files",
            params={"path": "projects/p1"},
        )
        assert project.status_code == 200
        assert [entry["name"] for entry in project.json()["entries"]] == [
            "README.md"
        ]
