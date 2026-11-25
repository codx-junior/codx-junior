"""Browser-session image catalog: engines, CDP capability and CLI env vars.

Loaded by path with a tiny pydantic stand-in so the catalog can be checked
without the API dependency tree (the module only needs ``BaseModel``/``Field``).

Run with pytest, or directly: ``python tests/browser/test_browser_catalog.py``.
"""

import importlib.util
import sys
import types
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = API_ROOT / "codx" / "junior" / "computer" / "images.py"


def _pydantic_stub():
    """Minimal pydantic so the catalog module imports without the API venv."""
    if "pydantic" in sys.modules:
        return
    module = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def dict(self):
            return dict(self.__dict__)

    def Field(default=None, **kwargs):  # noqa: N802 - pydantic's name
        return default

    module.BaseModel = BaseModel
    module.Field = Field
    sys.modules["pydantic"] = module


def _load():
    _pydantic_stub()
    spec = importlib.util.spec_from_file_location("cxj_images", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cxj_images"] = module
    spec.loader.exec_module(module)
    return module


images = _load()


def test_the_catalog_has_the_major_browsers():
    apps = {entry[0] for entry in images.BROWSER_IMAGES}
    # Chromium family (AI-drivable over CDP).
    assert {"chromium", "chrome", "ungoogled-chromium", "brave", "msedge", "opera", "vivaldi"} <= apps
    # Firefox family (live view only: no CDP).
    assert {"firefox", "librewolf", "zen", "mullvad-browser"} <= apps
    assert len(apps) == 11


def test_cdp_support_matches_the_engine():
    for app, _name, engine, supports_cdp, _cli, _desc in images.BROWSER_IMAGES:
        if engine == "chromium":
            assert supports_cdp, f"{app} is Chromium: it must be CDP-drivable"
        else:
            assert engine == "firefox"
            assert not supports_cdp, f"{app} is Firefox-based: no CDP"


def test_each_family_has_its_cli_environment_variable():
    expected = {
        "chromium": "CHROME_CLI",
        "chrome": "CHROME_CLI",
        "ungoogled-chromium": "CHROME_CLI",
        "brave": "BRAVE_CLI",
        "msedge": "EDGE_CLI",
        "opera": "OPERA_CLI",
        "vivaldi": "VIVALDI_CLI",
        "firefox": "FIREFOX_CLI",
        "librewolf": "LIBREWOLF_CLI",
    }
    for app, _name, _engine, _cdp, cli_env, _desc in images.BROWSER_IMAGES:
        if app in expected:
            assert cli_env == expected[app], app


def test_profile_resolution_from_an_image_reference():
    brave = images.browser_image_profile("lscr.io/linuxserver/brave:latest")
    assert brave.engine == "chromium"
    assert brave.supports_cdp is True
    assert brave.cli_env == "BRAVE_CLI"
    assert brave.name == "Brave"

    firefox = images.browser_image_profile("lscr.io/linuxserver/firefox:latest")
    assert firefox.engine == "firefox"
    assert firefox.supports_cdp is False

    assert images.browser_image_profile("lscr.io/linuxserver/webtop:latest") is None
    assert images.browser_image_profile("docker.io/library/nginx:latest") is None
    assert images.browser_image_profile("") is None


def test_browser_kind_lists_only_browser_images():
    entries = images.list_images(kind="browser")
    apps = {entry.repository.rsplit("/", 1)[-1] for entry in entries}
    assert "chromium" in apps and "firefox" in apps and "brave" in apps
    assert all(entry.category == "Browser" for entry in entries)
    assert all(entry.browser_engine for entry in entries)
    # Kasm desktops are not browser-session images.
    assert not any(entry.provider == "kasmweb" for entry in entries)
    # A provider with no browser images yields an empty list.
    assert images.list_images(kind="browser", provider="kasmweb") == []


def test_resolve_image_carries_the_browser_profile():
    chromium = images.resolve_image("lscr.io/linuxserver/chromium:latest")
    assert chromium.live_view == "selkies"
    assert chromium.live_port == 3000
    assert chromium.strip_prefix is False
    assert chromium.control == "cdp"
    assert chromium.supports_cdp is True
    assert chromium.browser_engine == "chromium"
    assert chromium.cli_env == "CHROME_CLI"

    firefox = images.resolve_image("lscr.io/linuxserver/firefox:latest")
    assert firefox.control == "none", "Firefox has no CDP: the AI must not drive it"
    assert firefox.supports_cdp is False
    assert firefox.browser_engine == "firefox"


def test_browser_entries_include_the_live_view_and_capability_tags():
    entry = next(e for e in images._browser_entries() if e.repository == "linuxserver/brave")
    assert entry.live_view == "selkies"
    assert entry.live_port == 3000
    assert entry.control == "cdp"
    assert "cdp" in entry.tags

    firefox = next(e for e in images._browser_entries() if e.repository == "linuxserver/firefox")
    assert firefox.control == "none"
    assert "live-view-only" in firefox.tags


def test_live_view_only_sessions_are_gated_for_the_ai():
    tools_source = (API_ROOT / "codx" / "junior" / "tools" / "browser_tools.py").read_text()
    assert "def _cdp_only_note(" in tools_source
    assert tools_source.count("_cdp_only_note(session)") >= 3, (
        "browse_action, browser_agent and start_session must check CDP support"
    )


def test_merged_catalog_endpoint_is_kind_aware():
    source = (API_ROOT / "codx" / "junior" / "api" / "computer.py").read_text()
    assert 'kind: str = "linux"' in source
    assert "kind=kind" in source, "the merged catalog must pass kind to list_images"
    assert 'kind or "").lower() != "browser"' in source, (
        "saved/custom entries are desktops and must not enter a browser-kind catalog"
    )


def test_background_browser_image_is_wired():
    runtime = (API_ROOT / "codx" / "junior" / "browser" / "runtime.py").read_text()
    for marker in (
        "IMAGE_VERSION_SCRIPT",
        "async def _refresh_image",
        "def image(",
        "def cached_text",
        "def cached_screenshot",
        "async def text(",
    ):
        assert marker in runtime, marker

    tools = (API_ROOT / "codx" / "junior" / "tools" / "browser_tools.py").read_text()
    assert 'action in ("state", "browser_state")' in tools
    assert "def _format_image(" in tools
    assert "await runtime.text()" in tools
    assert "full_page: bool = False" in tools, "viewport screenshots by default"
    assert "took {elapsed_ms} ms" in tools


def test_run_command_uses_the_image_cli_profile():
    runner_source = (API_ROOT / "codx" / "junior" / "browser" / "docker_browser_manager.py").read_text()
    assert "browser_image_profile(self.image_for(session))" in runner_source
    assert "--remote-debugging-port=9222" in runner_source
    assert "cli_env" in runner_source, "each browser image gets its own CLI env var"


if __name__ == "__main__":  # pragma: no cover - ad-hoc runner
    passed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        function()
        passed += 1
        print("ok -", name)
    print(f"\n{passed} browser catalog tests passed")
