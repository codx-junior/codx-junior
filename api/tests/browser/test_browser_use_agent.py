"""browser-use bridge: workspace, payload, worker protocol and tool wiring.

``use_agent`` is stdlib-only (browser-use runs in its own interpreter), so this
test loads it by path and uses a fake worker script for the process protocol.

Run with pytest, or directly:
``python tests/browser/test_browser_use_agent.py``.
"""

import ast
import asyncio
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

API_ROOT = Path(__file__).resolve().parents[2]
BROWSER_DIR = API_ROOT / "codx" / "junior" / "browser"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_codx_packages():
    """Provide the ``codx.junior.ai`` package chain without importing the API."""
    import types

    for name, folder in (
        ("codx", API_ROOT / "codx"),
        ("codx.junior", API_ROOT / "codx" / "junior"),
        ("codx.junior.ai", API_ROOT / "codx" / "junior" / "ai"),
    ):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(folder)]
            sys.modules[name] = module


_install_codx_packages()
llm_gateway = _load("codx.junior.ai.llm_gateway", API_ROOT / "codx" / "junior" / "ai" / "llm_gateway.py")
use_agent = _load("cxj_use_agent", BROWSER_DIR / "use_agent.py")
worker_module = _load("cxj_browser_use_worker", API_ROOT / "scripts" / "browser_use_worker.py")


class FakeLLM:
    """Stand-in for AISettings."""

    model = "test-model"
    api_url = "http://llm.local/v1"
    api_key = "sk-test"


class FakeSettings:
    """Stand-in for CODXJuniorSettings."""

    def __init__(self, codx_path):
        self.codx_path = str(codx_path)

    def get_llm_settings(self, llm_model=None):
        return FakeLLM()


class FakeShare:
    def __init__(self, name, host_path):
        self.name = name
        self.folder = name
        self.host_path = str(host_path)
        self.mount_path = f"/home/proj/{name}"


class FakeSession:
    def __init__(self, downloads, shared=()):
        self.id = "session-42"
        self.debug_url = "ws://browser_session-42:9223/devtools/browser/abc"
        self.current_url = "https://www.linkedin.com/feed/"
        self.downloads_path = str(downloads)
        self.shared_folders = list(shared)
        self.status = "running"
        # Co-browsing state: by default the AI may drive.
        self.controller = "ai"
        self.handoff_reason = None
        self.ai_may_act_now = lambda quiet_seconds=1.0: True
        self.user_is_active = lambda: False


class FakeActivityLog:
    def __init__(self, entries):
        self.entries = entries

    def read(self, limit=100):
        return list(self.entries)[-limit:]


class FakeManager:
    def __init__(self, session_folder, entries=()):
        self.session_folder = session_folder
        self.activity = []
        self.entries = list(entries)

    def _session_folder_path(self, session_id):
        return str(self.session_folder)

    def _activity_log(self, session_or_id):
        return FakeActivityLog(self.entries)

    def log_activity(self, session, **kwargs):
        self.activity.append(kwargs)
        return kwargs


class FakeRuntime:
    def __init__(self):
        self.began = 0
        self.ended = 0

    async def begin_external_ai(self):
        self.began += 1

    async def end_external_ai(self):
        self.ended += 1


def _fixture(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "photo.jpg").write_bytes(b"jpeg")
    docs = tmp_path / "project" / "docs"
    docs.mkdir(parents=True)
    (docs / "cv.pdf").write_bytes(b"%PDF")
    session = FakeSession(downloads, [FakeShare("docs", docs)])
    manager = FakeManager(tmp_path / "session")
    settings = FakeSettings(tmp_path)
    return settings, session, manager, downloads, docs


def _write_worker(tmp_path, body):
    script = tmp_path / "fake_worker.py"
    script.write_text(body)
    try:  # the worker is invoked as `python script`, the bit is optional
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    except OSError:
        pass
    return script


def _no_gateway():
    """Keep a run on the direct provider path (no LLM gateway)."""
    return patch.object(use_agent.llm_gateway, "gateway_enabled", lambda: False)


def _enable_worker(script):
    """Make use_agent believe browser-use is installed and use *script*."""
    env = {
        use_agent.ENV_WORKER_PYTHON: sys.executable,
        use_agent.ENV_WORKER_SCRIPT: str(script),
    }
    return patch.dict(os.environ, env), patch.object(use_agent, "_can_import_browser_use", lambda python: True)


def test_workspace_links_downloads_and_shared_folders(tmp_path):
    settings, session, manager, downloads, docs = _fixture(tmp_path)
    runner = use_agent.BrowserUseRunner(settings=settings, session=session, manager=manager)

    workspace = runner.workspace()

    assert (workspace / "downloads").is_symlink()
    assert (workspace / "downloads").resolve() == downloads.resolve()
    assert (workspace / "shared" / "docs").resolve() == docs.resolve()
    manifest = runner.workspace_manifest(workspace)
    assert "photo.jpg" in manifest
    assert "upload_file" in manifest
    assert "linkedin.com" in manifest


def test_payload_uses_the_project_llm_and_the_shared_browser(tmp_path):
    settings, session, manager, _, _ = _fixture(tmp_path)
    runner = use_agent.BrowserUseRunner(settings=settings, session=session, manager=manager)

    payload = runner.payload("upload photo.jpg", max_steps=500, timeout=5)

    assert payload["task"] == "upload photo.jpg"
    assert payload["cdp_url"] == session.debug_url
    assert payload["model"] == "test-model"
    assert payload["base_url"] == "http://llm.local/v1"
    assert payload["api_key"] == "sk-test"
    assert payload["max_steps"] == use_agent.MAX_STEPS_CAP, "steps are clamped"
    assert payload["timeout"] == use_agent.MIN_TIMEOUT_SECONDS, "timeouts are clamped"
    assert Path(payload["workspace"], "downloads").is_symlink()


def test_payload_requires_a_running_browser(tmp_path):
    settings, session, manager, _, _ = _fixture(tmp_path)
    session.debug_url = None
    runner = use_agent.BrowserUseRunner(settings=settings, session=session, manager=manager)

    try:
        runner.payload("do something")
    except use_agent.BrowserUseError as ex:
        assert "DevTools" in str(ex)
    else:  # pragma: no cover
        raise AssertionError("expected BrowserUseError")


def test_run_uses_the_worker_and_reports_activity(tmp_path):
    settings, session, manager, _, _ = _fixture(tmp_path)
    script = _write_worker(tmp_path, """
import json, sys
request = json.load(sys.stdin)
print(json.dumps({
    "ok": True, "final_result": "posted " + request["task"], "success": True,
    "status": "completed", "steps": 4,
    "usage": {"input": 120, "output": 30, "requests": 4},
    "cost_usd": 0.012, "downloads": ["/tmp/out.pdf"], "duration_s": 3.5,
}))
""")
    runtime = FakeRuntime()
    runner = use_agent.BrowserUseRunner(
        settings=settings, session=session, manager=manager, chat_id="chat-7"
    )
    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, _no_gateway():
        use_agent._counter["checked"] = False
        result = asyncio.run(runner.run("upload photo.jpg", runtime=runtime))

    assert result.success is True
    assert result.final_result == "posted upload photo.jpg"
    assert result.steps == 4
    assert result.total_tokens == 150
    assert result.cost_usd == 0.012
    assert result.downloads == ["/tmp/out.pdf"]
    assert result.worker_python == sys.executable
    assert runtime.began == 1 and runtime.ended == 1
    actions = [entry["action"] for entry in manager.activity]
    assert actions == ["browser_use_start", "browser_use"]
    assert manager.activity[-1]["chat_id"] == "chat-7"
    assert manager.activity[-1]["params"]["task"] == "upload photo.jpg"


def test_run_reports_a_worker_error(tmp_path):
    settings, session, manager, _, _ = _fixture(tmp_path)
    script = _write_worker(tmp_path, """
import json
print(json.dumps({"ok": False, "error": "model refused the task"}))
""")
    runner = use_agent.BrowserUseRunner(settings=settings, session=session, manager=manager)
    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, _no_gateway():
        use_agent._counter["checked"] = False
        try:
            asyncio.run(runner.run("do it"))
        except use_agent.BrowserUseError as ex:
            assert "model refused the task" in str(ex)
        else:  # pragma: no cover
            raise AssertionError("expected BrowserUseError")


def test_run_fails_cleanly_when_the_worker_prints_nothing(tmp_path):
    settings, session, manager, _, _ = _fixture(tmp_path)
    script = _write_worker(tmp_path, "import sys\nsys.exit(3)\n")
    runner = use_agent.BrowserUseRunner(settings=settings, session=session, manager=manager)
    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, _no_gateway():
        use_agent._counter["checked"] = False
        try:
            asyncio.run(runner.run("do it"))
        except use_agent.BrowserUseError as ex:
            assert "no result" in str(ex).lower() or "returned no" in str(ex).lower()
        else:  # pragma: no cover
            raise AssertionError("expected BrowserUseError")


def test_agent_mode_can_be_forced_native(tmp_path):
    with patch.dict(os.environ, {"CODX_BROWSER_AGENT": "native"}):
        assert use_agent.agent_mode() == "native"
    with patch.dict(os.environ, {"CODX_BROWSER_AGENT": "browser-use"}):
        assert use_agent.agent_mode() == "browser-use"


def _runner(tmp_path, entries=()):
    settings, session, manager, _, _ = _fixture(tmp_path)
    if entries:
        manager.entries = list(entries)
    runner = use_agent.BrowserUseRunner(settings=settings, session=session, manager=manager)
    return runner, session, manager


def test_human_took_over_on_lease_handoff_and_typing(tmp_path):
    runner, session, manager = _runner(tmp_path)

    assert runner._human_took_over() is False

    session.controller = "user"
    assert runner._human_took_over() is True

    session.controller = "ai"
    session.ai_may_act_now = lambda quiet_seconds=1.0: False
    assert runner._human_took_over() is True, "recent human input pauses the agent"

    session.ai_may_act_now = lambda quiet_seconds=1.0: True
    session.handoff_reason = "the user must log in"
    assert runner._human_took_over() is True


def test_manifest_carries_the_recent_cobrowsing_activity(tmp_path):
    entries = [
        {"ts": "2026-11-23T12:00:00+00:00", "actor": "user", "action": "click",
         "params": {"target": {"text": "Sign in"}}},
        {"ts": "2026-11-23T12:00:05+00:00", "actor": "ai", "action": "navigate",
         "params": {"url": "https://www.linkedin.com/feed/"}},
    ]
    runner, session, manager = _runner(tmp_path, entries=entries)

    manifest = runner.workspace_manifest(runner.workspace())

    assert "Recent co-browsing activity" in manifest
    assert "user: click · Sign in" in manifest
    assert "ai: navigate" in manifest
    assert "The human always wins" in manifest or "They always win" in manifest


def test_run_is_paused_when_the_human_takes_the_live_view(tmp_path):
    runner, session, manager = _runner(tmp_path)
    script = _write_worker(tmp_path, "import time\ntime.sleep(60)\n")
    # The human is already driving before the second lease poll.
    session.ai_may_act_now = lambda quiet_seconds=1.0: False

    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, _no_gateway(), \
         patch.object(use_agent, "LEASE_POLL_SECONDS", 0.05), \
         patch.object(use_agent, "WORKER_GRACE_SECONDS", 5):
        use_agent._counter["checked"] = False
        started = __import__("time").monotonic()
        try:
            asyncio.run(runner.run("do it"))
        except use_agent.BrowserUsePaused as ex:
            assert "human took control" in str(ex)
        else:  # pragma: no cover
            raise AssertionError("expected BrowserUsePaused")
        elapsed = __import__("time").monotonic() - started

    assert elapsed < 10, "the worker must be stopped, not waited on"
    assert [entry["action"] for entry in manager.activity] == [
        "browser_use_start",
        "browser_use_pause",
    ]
    assert manager.activity[-1]["actor"] == "user"


def test_run_routes_model_calls_through_the_llm_gateway(tmp_path):
    runner, session, manager = _runner(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    llm_run = use_agent.llm_gateway.LlmRun(
        run_id="run-1",
        token="run-token",
        model="test-model",
        ledger_path=str(ledger),
        expires_at=__import__("time").time() + 600,
        chat_id="chat-9",
        browser_session_id=session.id,
        username="alice",
    )
    opened = {}

    def fake_open_run(settings, **kwargs):
        opened.update(kwargs)
        return llm_run

    script = _write_worker(tmp_path, """
import json, sys
request = json.load(sys.stdin)
print(json.dumps({
    "ok": True,
    "final_result": request["base_url"] + "|" + request["api_key"] + "|" + str(request["gateway"]),
    "success": True, "status": "completed", "steps": 2,
    "usage": {"input": 1, "output": 1},
}))
""")
    runner.user = type("U", (), {"username": "alice"})()
    runner.chat_id = "chat-9"
    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, \
         patch.object(use_agent.llm_gateway, "gateway_enabled", lambda: True), \
         patch.object(use_agent.llm_gateway, "gateway_base_url", lambda: "http://127.0.0.1:19980/api/ai/llm/v1"), \
         patch.object(use_agent.llm_gateway, "open_run", fake_open_run), \
         patch.object(use_agent.llm_gateway, "usage_totals", lambda path: {
             "cost_cxj": 0.031, "input_tokens": 1200, "output_tokens": 340, "calls": 4,
         }):
        use_agent._counter["checked"] = False
        result = asyncio.run(runner.run("do it", model="chat-model"))

    assert opened["model"] == "chat-model", "the chat's model must reach the gateway token"
    assert result.gateway is True
    assert result.cost_cxj == 0.031
    assert result.usage == {"input": 1200, "output": 340, "requests": 4}
    assert result.final_result == "http://127.0.0.1:19980/api/ai/llm/v1|run-token|True"
    assert opened["chat_id"] == "chat-9"
    assert opened["browser_session_id"] == session.id
    assert opened["username"] == "alice"


def test_run_falls_back_to_the_provider_when_the_gateway_fails(tmp_path):
    runner, session, manager = _runner(tmp_path)
    script = _write_worker(tmp_path, """
import json, sys
request = json.load(sys.stdin)
print(json.dumps({
    "ok": True,
    "final_result": request["base_url"] + "|" + request["api_key"] + "|" + str(request["gateway"]),
    "success": True, "status": "completed", "steps": 1,
}))
""")

    def broken_open_run(settings, **kwargs):
        raise RuntimeError("no signing secret")

    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, \
         patch.object(use_agent.llm_gateway, "gateway_enabled", lambda: True), \
         patch.object(use_agent.llm_gateway, "gateway_base_url", lambda: "http://127.0.0.1:19980/api/ai/llm/v1"), \
         patch.object(use_agent.llm_gateway, "open_run", broken_open_run):
        use_agent._counter["checked"] = False
        result = asyncio.run(runner.run("do it"))

    assert result.gateway is False
    assert result.cost_cxj == 0.0
    assert result.final_result == "http://llm.local/v1|sk-test|False", "direct provider fallback"


def test_failed_run_still_reports_the_partial_cost(tmp_path):
    runner, session, manager = _runner(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    llm_gateway.append_usage(str(ledger), {"input_tokens": 10, "output_tokens": 5, "cost_cxj": 0.05})
    llm_run = use_agent.llm_gateway.LlmRun(
        run_id="run-2",
        token="run-token",
        model="test-model",
        ledger_path=str(ledger),
        expires_at=__import__("time").time() + 600,
    )
    script = _write_worker(tmp_path, """
import json
print(json.dumps({"ok": False, "error": "provider exploded"}))
""")
    env_patch, import_patch = _enable_worker(script)
    with env_patch, import_patch, \
         patch.object(use_agent.llm_gateway, "gateway_enabled", lambda: True), \
         patch.object(use_agent.llm_gateway, "gateway_base_url", lambda: "http://gw/api/ai/llm/v1"), \
         patch.object(use_agent.llm_gateway, "open_run", lambda settings, **kwargs: llm_run):
        use_agent._counter["checked"] = False
        try:
            asyncio.run(runner.run("do it"))
        except use_agent.BrowserUseError as ex:
            assert "provider exploded" in str(ex)
            assert ex.cost_cxj == 0.05, "partial spend must reach the chat"
        else:  # pragma: no cover
            raise AssertionError("expected BrowserUseError")


def test_worker_result_mapping_tolerates_the_real_history_list():
    class Raw:
        status = "completed"
        success = True
        steps = 7
        usage = {"input": 10, "output": 4}
        cost_usd = 0.5
        downloads = ["/tmp/a"]
        duration_s = 2.0

        def final_result(self):
            return "all done"

        def number_of_steps(self):
            return 7

    payload = worker_module._result_payload("goal", Raw(), 0.0)
    assert payload["ok"] is True
    assert payload["final_result"] == "all done"
    assert payload["steps"] == 7
    assert payload["usage"] == {"input": 10, "output": 4}
    assert payload["cost_usd"] == 0.5
    assert payload["downloads"] == ["/tmp/a"]


def test_worker_rejects_a_request_without_a_goal():
    payload = asyncio.run(worker_module.run_task({"task": "", "cdp_url": "ws://x", "workspace": "/tmp"}))
    assert payload["ok"] is False
    assert "task" in payload["error"]


# -- static wiring: no API imports needed ----------------------------------


def _assigned_dict(source, name):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


def test_browse_action_schema_has_the_upload_action():
    source = (BROWSER_DIR.parent / "tools" / "browser_tools.py").read_text()
    schema = _assigned_dict(source, "BROWSE_ACTION_TOOL_JSON")["function"]
    properties = schema["parameters"]["properties"]
    assert "upload" in properties["action"]["enum"]
    assert properties["files"]["type"] == "array"
    assert "upload" in schema["description"].lower()


def test_browser_agent_is_registered_as_a_tool():
    tools_source = (BROWSER_DIR.parent / "tools" / "__init__.py").read_text()
    assert "browser_agent" in tools_source
    browser_source = (BROWSER_DIR.parent / "tools" / "browser_tools.py").read_text()
    schema = _assigned_dict(browser_source, "BROWSER_AGENT_TOOL_JSON")["function"]
    assert schema["name"] == "browser_agent"
    assert schema["parameters"]["required"] == ["task"]
    settings = _assigned_dict(browser_source, "BROWSER_TOOL_SETTINGS")
    assert settings["inject_user"] is True, "the gateway needs the asking user"
    assert settings["inject_llm_model"] is True, "the agent must use the chat's model"
    assert '"model": {' not in json.dumps(schema["parameters"]["properties"]), (
        "the model must not be able to override the chat's model"
    )
    assert "model=llm_model" in browser_source, "the chat model must reach the runner"
    assert "cost=result.cost_cxj" in browser_source, "the run cost must reach the chat"


if __name__ == "__main__":  # pragma: no cover - ad-hoc runner
    import tempfile

    passed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        if "tmp_path" in function.__code__.co_varnames[: function.__code__.co_argcount]:
            with tempfile.TemporaryDirectory() as folder:
                function(Path(folder))
        else:
            function()
        passed += 1
        print("ok -", name)
    print(f"\n{passed} browser-use tests passed")
