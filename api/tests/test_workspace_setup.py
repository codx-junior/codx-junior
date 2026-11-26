"""
0.0.1 workspace setup contract.

Source-level regression tests (the suite deliberately runs without a browser or
a Node runtime). They pin the four fixes that made a fresh ``core`` install
unusable:

* a created project must be visible in the next ``GET /api/projects``;
* a project-less ``/api/settings`` must answer 4xx, never 500 or the literal
  ``codx_path=undefined``;
* the active-project switch must never erase itself when the discovery cache is
  momentarily empty;
* the workspace first-run wizard, the setup API and the QA/AI assistant must
  exist and stay wired to the shell.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
CLIENT = ROOT / "client"


def read(path: Path) -> str:
    assert path.exists(), f"{path} is part of the 0.0.1 setup flow and must exist"
    return path.read_text(encoding="utf-8")


def test_created_project_is_added_to_the_discovery_snapshot():
    source = read(API / "codx" / "junior" / "project" / "project_manager.py")
    assert "update_project_snapshot(new_project)" in source
    assert "from codx.junior.project.project_discover import" in source


def test_settings_answers_4xx_without_a_project():
    source = read(API / "codx" / "junior" / "app.py")
    assert "def api_settings_check" in source
    assert 'getattr(request.state, "codx_junior_session", None)' in source
    assert "status_code=422" in source


def test_client_does_not_append_undefined_codx_path():
    source = read(CLIENT / "src" / "api" / "connection.js")
    assert "const codxPath = this.settings?.codx_path" in source
    assert "if (!codxPath) return url" in source


def test_active_project_switch_keeps_the_project_object():
    source = read(CLIENT / "src" / "api" / "api.js")
    assert "async setActiveProject(project)" in source
    assert "const target = API.allProjects?.find" in source
    assert "(project?.codx_path ? project : null)" in source


def test_workspace_wizard_is_the_core_first_run_gate():
    app = read(CLIENT / "src" / "App.vue")
    assert "WorkspaceWizard" in app
    assert "workspaceSetupRequired" in app
    assert "assistant-only" in app
    wizard = read(CLIENT / "src" / "components" / "setup" / "WorkspaceWizard.vue")
    for step in ("'welcome'", "'project'", "'ai'"):
        assert f"step === {step}" in wizard
    # The finish screen is the final v-else branch.
    assert 'v-else class="flex max-w-xl' in wizard
    assert "/api/setup/ai" in wizard
    assert "SetupAssistantPanel" in wizard


def test_setup_assistant_has_qa_and_ai_paths():
    source = read(API / "codx" / "junior" / "api" / "setup.py")
    assert "def _qa_answer" in source
    assert "def _ai_answer" in source
    assert "source\": \"qa\"" in source or "source': 'qa'" in source
    assert "@router.post(\"/assistant\")" in source
    assert "@router.get(\"/status\")" in source
    assert "@router.post(\"/ai\")" in source


def test_assistant_panel_is_always_available_in_the_shell():
    app = read(CLIENT / "src" / "App.vue")
    assert "assistantOpen = true" in app
    assert "assistant-only" in app
    panel = read(CLIENT / "src" / "components" / "setup" / "SetupAssistantPanel.vue")
    assert "preconfigured" in panel
    assert "localQa" in panel
    assert "/api/setup/assistant" in panel


def test_project_list_is_the_core_landing_and_shows_every_project():
    router = read(CLIENT / "src" / "router" / "index.ts")
    assert "if (to.name === 'workspace-projects')" in router
    assert "0.0.1 workspace mode: the project list is a real landing screen" in router
    overview = read(CLIENT / "src" / "components" / "project" / "ProjectOverview.vue")
    assert "features?.companyEnabled === false" in overview
    assert "this.$projects.allProjects" in overview
