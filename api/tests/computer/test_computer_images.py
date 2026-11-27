"""Image library catalog, resolution and per-provider Docker commands."""

from codx.junior.browser.model import BrowserSession
from codx.junior.computer.images import (
    DEFAULT_KASM_TAG,
    all_catalog_images,
    list_images,
    list_image_tags,
    resolve_image,
)


def test_library_offers_kasmweb_and_linuxserver():
    kasm = list_images(provider="kasmweb")
    linuxserver = list_images(provider="linuxserver")

    assert kasm and all(image.provider == "kasmweb" for image in kasm)
    assert linuxserver and all(image.provider == "linuxserver" for image in linuxserver)
    assert any(image.name == "Kasm Desktop" for image in kasm)
    assert any(image.tag == "debian-kde" for image in linuxserver)
    assert {image.live_view for image in kasm} == {"kasmvnc"}
    # LinuxServer now serves both Selkies desktops and plain web IDEs.
    assert {image.live_view for image in linuxserver} == {"selkies", "web"}


def test_library_offers_code_server_and_vscodium_web():
    """The two web IDEs are ordinary catalog entries with a web live view."""
    images = list_images(provider="linuxserver")
    by_app = {
        image.repository.split("/", 1)[-1]: image
        for image in images
        if image.live_view == "web"
    }

    assert set(by_app) == {"code-server", "vscodium-web"}
    code = by_app["code-server"]
    assert code.image == "lscr.io/linuxserver/code-server:latest"
    assert code.live_port == 8443
    assert code.live_scheme == "http"
    assert code.control == "none"
    assert "web" in code.tags

    vscodium = by_app["vscodium-web"]
    assert vscodium.image == "lscr.io/linuxserver/vscodium-web:latest"
    assert vscodium.live_port == 8000


def test_resolve_web_ide_profiles():
    code = resolve_image("lscr.io/linuxserver/code-server:latest")
    assert code.provider == "linuxserver"
    assert code.live_view == "web"
    assert code.live_port == 8443
    assert code.live_scheme == "http"
    # code-server uses relative URLs and works behind a stripped prefix.
    assert code.strip_prefix is True
    assert code.control == "none"
    assert code.image == "lscr.io/linuxserver/code-server:latest"

    vscodium = resolve_image("linuxserver/vscodium-web")
    assert vscodium.live_view == "web"
    assert vscodium.live_port == 8000
    assert vscodium.strip_prefix is False
    assert vscodium.image == "lscr.io/linuxserver/vscodium-web:latest"

    # A web IDE is not a Selkies desktop and must not be mistaken for one.
    webtop = resolve_image("lscr.io/linuxserver/webtop:debian-kde")
    assert webtop.live_view == "selkies"


def test_library_offers_browseruse_as_a_headless_screenshot_image():
    """The official browser-use image has no display and no noVNC."""
    images = list_images(provider="browseruse")

    assert images and all(image.provider == "browseruse" for image in images)
    entry = images[0]
    assert entry.repository == "browseruse/browseruse"
    assert entry.live_view == "screenshot"
    assert entry.live_port is None


def test_resolve_browseruse_keeps_it_screenshot_only():
    spec = resolve_image("browseruse/browseruse")

    assert spec.provider == "browseruse"
    assert spec.image == "browseruse/browseruse:latest"
    assert spec.live_view == "screenshot"
    assert spec.live_port is None
    assert spec.strip_prefix is False


def test_browseruse_is_part_of_the_unfiltered_catalog():
    assert any(image.provider == "browseruse" for image in all_catalog_images())


def test_legacy_kasm_images_pin_their_last_published_tag():
    """Kasm stopped shipping current tags for some legacy images.

    The catalog must surface the last tag that actually exists, otherwise the
    card looks selectable but the pull fails with "tag not found".
    """
    images = list_images(provider="kasmweb")
    teams = next(image for image in images if image.repository == "kasmweb/teams")

    assert teams.default_tag == "1.12.0"
    assert teams.image == "kasmweb/teams:1.12.0"
    assert "1.12.0" in teams.tags

    spec = resolve_image("kasmweb/teams")
    assert spec.tag == "1.12.0"
    assert spec.image == "kasmweb/teams:1.12.0"


def test_library_search_and_category_filters():
    desktop = list_images(provider="kasmweb", category="Desktop")
    vscode = list_images(provider="kasmweb", query="vs code")

    assert desktop and all(image.category == "Desktop" for image in desktop)
    assert vscode and vscode[0].repository == "kasmweb/vs-code"


def test_resolve_kasmweb_and_linuxserver_profiles():
    kasm = resolve_image("kasmweb/desktop:1.19.0")
    assert kasm.provider == "kasmweb"
    assert kasm.live_view == "kasmvnc"
    assert kasm.live_port == 6901
    assert kasm.live_scheme == "https"
    assert kasm.strip_prefix is True
    assert kasm.viewer_user == "kasm_user"
    assert kasm.image == "kasmweb/desktop:1.19.0"

    ls = resolve_image("lscr.io/linuxserver/webtop:ubuntu-xfce")
    assert ls.provider == "linuxserver"
    assert ls.live_view == "selkies"
    assert ls.live_port == 3000
    assert ls.live_scheme == "http"
    assert ls.strip_prefix is False
    assert ls.image == "lscr.io/linuxserver/webtop:ubuntu-xfce"

    default_kasm = resolve_image("kasmweb/desktop")
    assert default_kasm.tag == DEFAULT_KASM_TAG


def test_resolve_custom_image_is_screenshot_only():
    spec = resolve_image("ghcr.io/example/desktop:1.2")
    assert spec.provider == "custom"
    assert spec.live_view == "screenshot"
    assert spec.live_port is None
    assert spec.image == "ghcr.io/example/desktop:1.2"


def test_tags_fallback_without_network(monkeypatch):
    import codx.junior.computer.images as images

    def boom(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(images, "_cached_json", boom)
    tags = list_image_tags("kasmweb", "kasmweb/desktop")
    assert tags and tags[0].name == DEFAULT_KASM_TAG
    ls_tags = list_image_tags("linuxserver", "linuxserver/webtop")
    assert ls_tags and ls_tags[0].name == "debian-kde"


def test_kasm_container_command_uses_kasmvnc_profile(tmp_path, monkeypatch):
    from codx.junior.browser.docker_browser_manager import DockerBrowserManager

    manager = DockerBrowserManager(
        str(tmp_path),
        traefik_domain="example.test",
        project_id="project-1",
        project_root_path=str(tmp_path / "project"),
        project_name="proj",
    )
    (tmp_path / "project").mkdir(exist_ok=True)
    monkeypatch.setattr(manager, "_ensure_image", lambda image=None: None)
    monkeypatch.setattr(manager, "_prepare_computer_agent", lambda sid: "/tmp/agent.py")

    session = BrowserSession(name="Kasm", kind="linux", image="kasmweb/desktop:1.19.0")
    session.id = "52345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=False)
    manager._apply_image_profile(session)
    assert session.image_provider == "kasmweb"
    assert session.viewer_password
    cmd = manager._build_kasm_run_command(
        session,
        "computer_52345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-52345678",
    )
    joined = " ".join(cmd)

    assert "kasmweb/desktop:1.19.0" in cmd
    assert f"VNC_PW={session.viewer_password}" in cmd
    assert "--user" in cmd
    assert "/tmp/agent.py:/codx/computer_agent.py:ro" in cmd
    assert "loadbalancer.server.port=6901" in joined
    assert "loadbalancer.server.scheme=https" in joined
    assert "codx-insecure@http" in joined
    assert "stripprefix.prefixes=/computer/" + session.id in joined
    assert "customrequestheaders.Authorization=Basic " in joined


def test_traefik_labels_for_selkies_are_not_stripped(tmp_path):
    from codx.junior.browser.docker_browser_manager import DockerBrowserManager

    manager = DockerBrowserManager(str(tmp_path), traefik_domain="example.test")
    labels = manager._build_traefik_labels(
        router_name="codx-browser-x",
        session_id="x",
        path_prefix="/browser/x/",
    )
    joined = "\n".join(labels)
    assert "server.port=3000" in joined
    assert "server.scheme=http" in joined
    assert "stripprefix" not in joined


def test_web_app_container_command_uses_the_web_transport(tmp_path, monkeypatch):
    from codx.junior.browser.docker_browser_manager import DockerBrowserManager

    manager = DockerBrowserManager(
        str(tmp_path),
        traefik_domain="example.test",
        project_id="project-1",
        project_root_path=str(tmp_path / "project"),
        project_name="proj",
    )
    (tmp_path / "project").mkdir(exist_ok=True)
    monkeypatch.setattr(manager, "_ensure_image", lambda image=None: None)
    monkeypatch.setattr(manager, "_prepare_computer_agent", lambda sid: "/tmp/agent.py")

    session = BrowserSession(
        name="Code Server",
        kind="linux",
        image="lscr.io/linuxserver/code-server:latest",
        image_provider="linuxserver",
        live_view="web",
        live_port=8443,
        live_scheme="http",
        control="none",
    )
    session.id = "62345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=True)
    profile_path = str(tmp_path / "profile")
    cmd = manager._build_web_app_run_command(
        session,
        "computer_62345678",
        profile_path,
        f"/computer/{session.id}/",
        "codx-computer-62345678",
    )
    joined = " ".join(cmd)

    assert "lscr.io/linuxserver/code-server:latest" in cmd
    assert "loadbalancer.server.port=8443" in joined
    assert "loadbalancer.server.scheme=http" in joined
    # code-server serves relative URLs: Traefik strips the public prefix.
    assert "stripprefix.prefixes=/computer/" + session.id in joined
    assert "codx-junior-auth" in joined
    # The public edge terminates TLS but forwards plain HTTP: Traefik would
    # otherwise add X-Forwarded-Port: 80 and VSCodium/code-server would dial
    # wss://host:80 (websocket 1006).  The web-app route drops it.
    assert "customrequestheaders.X-Forwarded-Port=" in joined
    assert "/tmp/agent.py:/codx/computer_agent.py:ro" in cmd
    assert not (tmp_path / "profile" / ".config").exists()


def test_vscodium_web_command_passes_the_server_base_path(tmp_path, monkeypatch):
    from codx.junior.browser.docker_browser_manager import DockerBrowserManager

    manager = DockerBrowserManager(
        str(tmp_path),
        traefik_domain="example.test",
        project_id="project-1",
        project_root_path=str(tmp_path / "project"),
        project_name="proj",
    )
    (tmp_path / "project").mkdir(exist_ok=True)
    monkeypatch.setattr(manager, "_ensure_image", lambda image=None: None)
    monkeypatch.setattr(manager, "_prepare_computer_agent", lambda sid: "/tmp/agent.py")

    session = BrowserSession(
        name="VSCodium",
        kind="linux",
        image="lscr.io/linuxserver/vscodium-web:latest",
        image_provider="linuxserver",
        live_view="web",
        live_port=8000,
        live_scheme="http",
        control="none",
    )
    session.id = "72345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=False)
    cmd = manager._build_web_app_run_command(
        session,
        "computer_72345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-72345678",
    )
    joined = " ".join(cmd)

    assert f"CODE_ARGS=--server-base-path=/computer/{session.id}" in cmd
    assert "loadbalancer.server.port=8000" in joined
    assert "stripprefix" not in joined
    assert "customrequestheaders.X-Forwarded-Port=" in joined


def test_linux_session_can_use_the_none_control_channel():
    session = BrowserSession(name="Web", kind="linux", control="none")

    assert session.control == "none"


def test_web_app_readiness_probes_the_container_path(tmp_path, monkeypatch):
    from codx.junior.browser.docker_browser_manager import DockerBrowserManager

    manager = DockerBrowserManager(str(tmp_path), traefik_domain="example.test")
    monkeypatch.setattr(manager, "_container_exists", lambda name: True)

    seen = []

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=5, context=None):
        seen.append(request.full_url)
        return _Response()

    monkeypatch.setattr(
        "codx.junior.browser.docker_browser_manager.urllib.request.urlopen",
        fake_urlopen,
    )

    code = BrowserSession(
        name="Code",
        kind="linux",
        live_view="web",
        live_port=8443,
        live_scheme="http",
        strip_prefix=True,
    )
    code.id = "82345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager._wait_for_web_ready(code, "computer_82345678")

    vscodium = BrowserSession(
        name="VSCodium",
        kind="linux",
        live_view="web",
        live_port=8000,
        live_scheme="http",
        strip_prefix=False,
    )
    vscodium.id = "92345678-aaaa-bbbb-cccc-ddddeeeeffff"
    vscodium.traefik_path_prefix = f"/computer/{vscodium.id}/"
    manager._wait_for_web_ready(vscodium, "computer_92345678")

    assert seen[0] == "http://computer_82345678:8443/"
    assert seen[1] == f"http://computer_92345678:8000/computer/{vscodium.id}/"
