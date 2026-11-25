"""Uploads into a page: path resolution and the browser file input/chooser.

The two modules under test are loaded in isolation (by file path) so this test
does not need the API's full dependency tree (pydantic/FastAPI/...); it only
uses stdlib + the module itself.

Run with pytest, or directly: ``python tests/browser/test_browser_uploads.py``.
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
BROWSER_DIR = API_ROOT / "codx" / "junior" / "browser"


def _load(name, path, **extra):
    """Load a module from a file path (no package __init__ side effects)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__dict__.update(extra)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


uploads = _load("cxj_uploads", BROWSER_DIR / "uploads.py")


def _load_browser_class():
    """Import ``Browser`` without the browser package's heavy __init__."""
    package_name = "codx.junior.browser"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(BROWSER_DIR)]
        sys.modules[package_name] = package
        model = types.ModuleType(f"{package_name}.model")
        model.BrowserSession = type("BrowserSession", (), {})
        sys.modules[f"{package_name}.model"] = model
        docker = types.ModuleType(f"{package_name}.docker_browser_manager")
        docker.DockerBrowserManager = type("DockerBrowserManager", (), {})
        sys.modules[f"{package_name}.docker_browser_manager"] = docker
    module_name = f"{package_name}.browser"
    module = sys.modules.get(module_name)
    if module is None:
        module = _load(module_name, BROWSER_DIR / "browser.py")
    return module.Browser


Browser = _load_browser_class()


class FakeShare:
    """Minimal SharedFolder stand-in."""

    def __init__(self, name, host_path, mount_path):
        self.name = name
        self.folder = name
        self.host_path = str(host_path)
        self.mount_path = mount_path
        self.readonly = False


class FakeSession:
    """Minimal BrowserSession stand-in."""

    def __init__(self, downloads, shared=()):
        self.id = "session-1"
        self.downloads_path = str(downloads)
        self.downloads_mount = "/downloads"
        self.shared_folders = list(shared)
        self.mark_active = lambda: None


def _session(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    docs = tmp_path / "project" / "docs"
    docs.mkdir(parents=True)
    return FakeSession(downloads, [FakeShare("docs", docs, "/home/proj/docs")]), downloads, docs


def test_resolves_downloads_name_and_prefix(tmp_path):
    session, downloads, _ = _session(tmp_path)
    picture = downloads / "photo.jpg"
    picture.write_bytes(b"jpeg-bytes")

    for spec in ("photo.jpg", "downloads/photo.jpg", "./downloads/photo.jpg", str(picture), "/downloads/photo.jpg"):
        resolved = uploads.resolve_session_file(session, spec)
        assert resolved.name == "photo.jpg", spec
        assert resolved.size == len(b"jpeg-bytes")
        assert resolved.mime_type == "image/jpeg"
        assert resolved.browser_path == "/downloads/photo.jpg"


def test_resolves_shared_folder_and_browser_path(tmp_path):
    session, _, docs = _session(tmp_path)
    (docs / "cv.pdf").write_bytes(b"%PDF-1.4")

    by_name = uploads.resolve_session_file(session, "cv.pdf")
    by_browser = uploads.resolve_session_file(session, "/home/proj/docs/cv.pdf")
    assert by_name.host_path == by_browser.host_path == (docs / "cv.pdf").resolve()
    assert by_name.browser_path == "/home/proj/docs/cv.pdf"
    assert by_name.mime_type == "application/pdf"


def test_missing_file_error_lists_available_files(tmp_path):
    session, downloads, _ = _session(tmp_path)
    (downloads / "real.png").write_bytes(b"png")

    try:
        uploads.resolve_session_file(session, "missing.png")
    except uploads.UploadError as ex:
        assert "missing.png" in str(ex)
        assert "real.png" in "\n".join(uploads.available_files(session))
    else:  # pragma: no cover
        raise AssertionError("expected UploadError")


def test_refuses_paths_outside_the_session_roots(tmp_path):
    session, _, _ = _session(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")

    try:
        uploads.resolve_session_file(session, str(secret))
    except uploads.UploadError as ex:
        assert "outside" in str(ex).lower()
    else:  # pragma: no cover
        raise AssertionError("expected UploadError")


def test_refuses_symlink_that_escapes_the_root(tmp_path):
    session, downloads, _ = _session(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (downloads / "link.txt").symlink_to(secret)

    try:
        uploads.resolve_session_file(session, "link.txt")
    except uploads.UploadError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a symlink out of the shared folder must be refused")


def test_refuses_directories_and_big_files(tmp_path):
    session, downloads, _ = _session(tmp_path)
    (downloads / "folder").mkdir()
    (downloads / "big.bin").write_bytes(b"x" * 2048)

    for spec, limit in (("folder", None), ("big.bin", 1024)):
        try:
            uploads.resolve_session_file(session, spec, max_bytes=limit)
        except uploads.UploadError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"{spec} should be rejected")


def test_file_payloads_keep_order_and_bytes(tmp_path):
    session, downloads, _ = _session(tmp_path)
    (downloads / "a.txt").write_bytes(b"A")
    (downloads / "b.txt").write_bytes(b"B")

    payloads = uploads.file_payloads(session, ["b.txt", "a.txt"])
    assert [payload["name"] for payload in payloads] == ["b.txt", "a.txt"]
    assert [payload["buffer"] for payload in payloads] == [b"B", b"A"]
    assert payloads[0]["mimeType"] == "text/plain"


def test_prepare_upload_supports_files_and_the_single_value(tmp_path):
    session, downloads, _ = _session(tmp_path)
    (downloads / "one.png").write_bytes(b"1")
    (downloads / "two.png").write_bytes(b"2")

    resolved, payloads = uploads.prepare_upload(session, ["one.png"], value="two.png")
    assert [item.name for item in resolved] == ["one.png", "two.png"]
    assert [payload["buffer"] for payload in payloads] == [b"1", b"2"]


def test_prepare_upload_without_files_explains_what_to_send(tmp_path):
    session, _, _ = _session(tmp_path)

    try:
        uploads.prepare_upload(session, [])
    except uploads.UploadError as ex:
        assert "files is required" in str(ex)
    else:  # pragma: no cover
        raise AssertionError("expected UploadError")


def test_prepare_upload_hints_the_available_files_on_a_miss(tmp_path):
    session, downloads, _ = _session(tmp_path)
    (downloads / "available.png").write_bytes(b"png")

    try:
        uploads.prepare_upload(session, ["guessed.png"])
    except uploads.UploadError as ex:
        assert "guessed.png" in str(ex)
        assert "available.png" in str(ex)
    else:  # pragma: no cover
        raise AssertionError("expected UploadError")


def test_safe_filename_strips_paths_and_control_characters():
    assert uploads.safe_filename("../../etc/passwd") == "passwd"
    assert uploads.safe_filename("photo (1).jpg") == "photo (1).jpg"
    assert uploads.safe_filename("a\x00b;rm -rf.png") == "a_b_rm -rf.png"
    assert uploads.safe_filename("") == "upload.bin"
    assert len(uploads.safe_filename("x" * 400)) <= 180


def test_unique_path_never_overwrites(tmp_path):
    folder = tmp_path / "downloads"
    folder.mkdir()
    first = uploads.unique_path(folder, "photo.jpg")
    assert first.name == "photo.jpg"
    first.write_bytes(b"1")
    second = uploads.unique_path(folder, "photo.jpg")
    assert second.name == "photo (1).jpg"
    second.write_bytes(b"2")
    third = uploads.unique_path(folder, "photo.jpg")
    assert third.name == "photo (2).jpg"


# -- Browser.upload_files ---------------------------------------------------


class FakeHandle:
    """Stand-in for a Playwright ElementHandle."""

    def __init__(self, is_input, files_count=1):
        self.is_input = is_input
        self.files_count = files_count
        self.set_calls = []

    async def evaluate(self, script):
        if "tagName" in script:
            return self.is_input
        return self.files_count

    async def set_input_files(self, files):
        self.set_calls.append(files)


class FakeChooser:
    def __init__(self):
        self.files = None

    async def set_files(self, files):
        self.files = files


class FakeChooserInfo:
    """Playwright's expect_file_chooser: the body runs, the exit waits."""

    def __init__(self, chooser, available=True):
        self._chooser = chooser
        self.available = available

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if not self.available:
            raise asyncio.TimeoutError()
        return False

    @property
    async def value(self):
        return self._chooser


class FakePage:
    def __init__(self, handle, *, chooser_available=True, inputs_after_click=None):
        self.handle = handle
        self.inputs = [handle] if handle is not None else []
        self.chooser_available = chooser_available
        self.inputs_after_click = inputs_after_click
        self.click_calls = []
        self.chooser = FakeChooser()

    async def query_selector(self, selector):
        return self.handle

    async def query_selector_all(self, selector):
        return list(self.inputs)

    async def click(self, selector, timeout=None):
        self.click_calls.append(selector)
        if self.inputs_after_click is not None:
            self.inputs = [self.inputs_after_click]

    def expect_file_chooser(self, timeout=None):
        return FakeChooserInfo(self.chooser, available=self.chooser_available)


def _browser(page):
    browser = Browser.__new__(Browser)
    browser.page = page

    async def noop(*args, **kwargs):
        return None

    browser.highlight = noop
    browser._sync_location = noop
    browser.session = type("S", (), {"mark_active": lambda self: None})()
    return browser


def test_get_session_context_is_async_where_it_is_awaited():
    """A sync dependency awaited by an endpoint breaks the whole router."""
    import re
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[2] / "codx" / "junior" / "api"
    offenders = []
    for path in api_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "await get_session_context(" not in source:
            continue
        # Modules that import it (browser.py) are checked by the source module.
        definition = re.search(r"(async\s+)?def get_session_context\(", source)
        if definition and not definition.group(1):
            offenders.append(path.name)
    assert not offenders, f"sync get_session_context awaited in: {offenders}"


def test_upload_files_sets_a_hidden_file_input():
    handle = FakeHandle(True)
    page = FakePage(handle)
    browser = _browser(page)
    payloads = [{"name": "photo.jpg", "mimeType": "image/jpeg", "buffer": b"x"}]

    receipt = asyncio.run(browser.upload_files("#avatar", payloads))

    assert handle.set_calls == [payloads]
    assert receipt["input"] is True
    assert receipt["strategy"] == "file-input"
    assert receipt["count"] == 1
    assert receipt["names"] == ["photo.jpg"]


def test_upload_files_uses_the_file_chooser_for_a_button():
    page = FakePage(FakeHandle(False))
    browser = _browser(page)
    payloads = [{"name": "cv.pdf", "mimeType": "application/pdf", "buffer": b"%PDF"}]

    receipt = asyncio.run(browser.upload_files("button.upload", payloads))

    assert page.click_calls == ["button.upload"]
    assert page.chooser.files == payloads
    assert receipt["strategy"] == "file-chooser"
    assert receipt["input"] is False


def test_upload_files_fills_the_input_a_click_opens():
    button = FakeHandle(False)
    modal_input = FakeHandle(True)
    page = FakePage(button, chooser_available=False, inputs_after_click=modal_input)
    browser = _browser(page)
    payloads = [{"name": "photo.jpg", "buffer": b"x"}]

    receipt = asyncio.run(browser.upload_files("button.add-photo", payloads))

    assert page.click_calls == ["button.add-photo"]
    assert modal_input.set_calls == [payloads]
    assert receipt["strategy"] == "modal-input"
    assert receipt["input"] is True


def test_upload_files_auto_detects_the_file_input():
    handle = FakeHandle(True)
    page = FakePage(handle)
    browser = _browser(page)
    payloads = [{"name": "photo.jpg", "buffer": b"x"}]

    receipt = asyncio.run(browser.upload_files(None, payloads))

    assert handle.set_calls == [payloads]
    assert receipt["strategy"] == "auto-input"
    assert receipt["selector"] is None


def test_upload_files_reports_a_missing_element():
    browser = _browser(FakePage(None))

    try:
        asyncio.run(browser.upload_files("#nope", [{"name": "a", "buffer": b"a"}]))
    except RuntimeError as ex:
        assert "not found" in str(ex).lower()
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_upload_files_reports_an_input_that_ignores_the_files():
    handle = FakeHandle(True, files_count=0)
    browser = _browser(FakePage(handle))

    try:
        asyncio.run(browser.upload_files("#photo", [{"name": "a", "buffer": b"a"}]))
    except RuntimeError as ex:
        assert "did not accept" in str(ex)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_upload_files_falls_back_to_a_path_inside_the_container(tmp_path):
    """Remote CDP cannot always take client-side bytes: use a container path."""

    class RottingHandle(FakeHandle):
        def __init__(self):
            super().__init__(True)
            self.calls = []

        async def set_input_files(self, files):
            self.calls.append(files)
            if len(self.calls) == 1:
                raise RuntimeError("file not found on the browser host")

    handle = RottingHandle()
    browser = _browser(FakePage(handle))
    browser.session.container_name = "browser_test"
    payload = {"name": "photo.jpg", "mimeType": "image/jpeg", "buffer": b"jpeg"}
    browser._write_container_file = lambda name, data: f"/tmp/codx-uploads/x/{name}"

    receipt = asyncio.run(browser.upload_files("#photo", [payload]))

    assert len(handle.calls) == 2
    assert handle.calls[1] == ["/tmp/codx-uploads/x/photo.jpg"]
    assert receipt["strategy"] == "file-input+container-path"
    assert receipt["input"] is True


def test_upload_files_without_any_input_explains_the_next_step():
    browser = _browser(FakePage(None))

    try:
        asyncio.run(browser.upload_files(None, [{"name": "a", "buffer": b"a"}]))
    except RuntimeError as ex:
        assert "input" in str(ex).lower()
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_browse_action_has_an_upload_branch_that_uses_prepare_upload():
    source = (BROWSER_DIR.parent / "tools" / "browser_tools.py").read_text()
    # The action is wired to the shared helper (no duplicated path logic).
    assert 'elif action == "upload":' in source
    assert "browser_uploads.prepare_upload(" in source
    assert 'runtime.run("upload_files"' in source


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
    print(f"\n{passed} upload tests passed")
