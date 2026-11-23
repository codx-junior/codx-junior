"""Tests for desktop/VM docker commands and folder mapping."""

import os

import pytest

from codx.junior.browser.docker_browser_manager import DockerBrowserManager
from codx.junior.browser.model import BrowserSession


def _manager(tmp_path, monkeypatch):
    manager = DockerBrowserManager(
        str(tmp_path),
        traefik_domain="example.test",
        project_id="project-1",
        project_root_path=str(tmp_path / "project"),
        project_name="My Project",
    )
    (tmp_path / "project").mkdir(exist_ok=True)
    monkeypatch.setattr(manager, "_ensure_image", lambda image=None: None)
    monkeypatch.setattr(manager, "_prepare_computer_agent", lambda sid: "/tmp/agent.py")
    return manager


def test_linux_run_command_uses_webtop_and_mounts_the_agent(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(name="Linux", kind="linux")
    session.id = "12345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=True)
    cmd = manager._build_linux_run_command(
        session,
        "computer_12345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-12345678",
    )
    joined = " ".join(cmd)

    assert manager.image_for(session) == "lscr.io/linuxserver/webtop:debian-kde"
    assert "computer_12345678" in cmd
    assert f"SUBFOLDER=/computer/{session.id}/" in cmd
    assert "/tmp/agent.py:/codx/computer_agent.py:ro" in cmd
    # Auto (client size) is the default: the manual mode must stay off so
    # Selkies follows the browser instead of letterboxing a fixed 1920x1080.
    assert "SELKIES_MANUAL_RESOLUTION=false" in joined
    assert "SELKIES_ENABLE_RESIZE=true" in joined
    assert "SELKIES_MANUAL_WIDTH=1920" not in joined
    assert "SELKIES_IS_MANUAL_RESOLUTION_MODE" not in joined
    assert "traefik.http.routers.codx-computer-12345678.rule=" in joined
    assert f"PathPrefix(`/computer/{session.id}/`)" in joined
    assert "3000" in joined
    assert manager.linux_image in cmd


def test_vm_run_command_has_kvm_vnc_and_single_shared_folder(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(name="Windows", kind="windows", os_version="10")
    session.id = "22345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=True)
    cmd = manager._build_vm_run_command(session, "computer_22345678", str(tmp_path / "vm"))
    joined = " ".join(cmd)

    assert manager.image_for(session) == "dockurr/windows"
    assert "/dev/kvm" in cmd and "/dev/net/tun" in cmd
    assert "NET_ADMIN" in joined
    assert "VERSION=10" in joined
    assert f"127.0.0.1::{manager.vm_vnc_port}" in joined
    assert "-v" in cmd and "/shared" in joined
    assert session.guest_share_path.startswith("Z:\\")
    # One host folder is shared with the guest, not the multi-folder browser tree.
    assert any(arg.endswith(":/shared") for arg in cmd)
    assert "traefik.enable=true" not in joined


def test_vm_advanced_resources_override_host_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CODX_COMPUTER_VM_RAM", "16G")
    monkeypatch.setenv("CODX_COMPUTER_VM_CPUS", "2")
    monkeypatch.setenv("CODX_COMPUTER_VM_DISK", "32G")
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(
        name="Windows",
        kind="windows",
        cpu_cores=8,
        ram_size="12G",
        disk_size="128G",
        extra_env={"FOO": "bar", "RAM_SIZE": "1G"},
    )
    manager.configure_share(session, share_project=False)
    cmd = manager._build_vm_run_command(session, "computer_abc", str(tmp_path / "vm"))
    joined = " ".join(cmd)

    assert "RAM_SIZE=12G" in joined
    assert "CPU_CORES=8" in joined
    assert "DISK_SIZE=128G" in joined
    assert "FOO=bar" in joined
    # Manager-owned variables cannot be overridden from the advanced settings.
    assert "RAM_SIZE=1G" not in joined


def test_linux_advanced_resources_and_extra_env(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(
        name="Linux",
        kind="linux",
        cpu_cores=2,
        ram_size="4G",
        shm_size="3G",
        extra_env={"LC_ALL": "C.UTF-8", "SUBFOLDER": "/evil"},
    )
    session.id = "12345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=False)
    cmd = manager._build_linux_run_command(
        session,
        "computer_12345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-12345678",
    )
    joined = " ".join(cmd)

    assert "--cpus 2" in joined
    assert "--memory 4G" in joined
    assert "--shm-size 3G" in joined
    assert "LC_ALL=C.UTF-8" in joined
    assert "SUBFOLDER=/evil" not in joined


def test_browser_advanced_window_size(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    proxy = tmp_path / "cdp_proxy.py"
    proxy.write_text("# proxy", encoding="utf-8")
    monkeypatch.setattr(manager, "_prepare_cdp_proxy", lambda sid: str(proxy))
    session = BrowserSession(
        name="Browser",
        desktop_width=1366,
        desktop_height=768,
        cpu_cores=1,
        shm_size="1G",
    )
    manager.configure_share(session, share_project=False)
    cmd = manager._build_browser_run_command(
        session,
        "browser_12345678",
        str(tmp_path / "profile"),
        f"/browser/{session.id}/",
        "codx-browser-12345678",
    )
    joined = " ".join(cmd)

    assert "--window-size=1366,768" in joined
    assert "--cpus 1" in joined
    assert "--shm-size 1G" in joined


def test_macos_vm_uses_macos_image_and_default_version(tmp_path, monkeypatch):
    monkeypatch.delenv("CODX_COMPUTER_VM_DISK", raising=False)
    monkeypatch.delenv("CODX_COMPUTER_MACOS_DISK", raising=False)
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(name="Mac", kind="macos")
    manager.configure_share(session, share_project=False)
    cmd = manager._build_vm_run_command(session, "computer_abc", str(tmp_path / "vm"))
    joined = " ".join(cmd)
    assert manager.image_for(session) == "dockurr/macos"
    assert "VERSION=15" in joined
    # macOS gets a larger default disk than Windows for Xcode/Simulator.
    assert "DISK_SIZE=128G" in joined
    assert "USERNAME=" not in joined


def test_browser_command_is_unchanged(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    proxy = tmp_path / "cdp_proxy.py"
    proxy.write_text("# proxy", encoding="utf-8")
    monkeypatch.setattr(manager, "_prepare_cdp_proxy", lambda sid: str(proxy))
    session = BrowserSession(name="Browser", current_url="https://example.com")
    session.id = "32345678-aaaa-bbbb-cccc-ddddeeeeffff"
    cmd = manager._build_run_command(
        session,
        "browser_12345678",
        str(tmp_path / "profile"),
        f"/browser/{session.id}/",
        "codx-browser-12345678",
    )
    joined = " ".join(cmd)
    assert manager.image in cmd
    assert "CHROME_CLI=--remote-debugging-port=9222" in joined
    assert "python3 /cdp_proxy.py" in joined
    assert "-p" not in cmd


def test_path_prefix_is_kind_aware(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    assert manager._path_prefix("abc", "browser") == "/browser/abc/"
    assert manager._path_prefix("abc", "linux") == "/computer/abc/"


def test_vm_without_project_does_not_mount_shared_twice(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(name="Isolated", kind="windows")
    session.id = "42345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=False)
    mounts = manager._build_share_mounts(session)
    assert len(mounts) == 1
    assert mounts[0].endswith(":/shared")


def test_linux_fixed_desktop_pins_selkies_resolution(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(
        name="Linux",
        kind="linux",
        desktop_auto_resize=False,
        desktop_width=1600,
        desktop_height=900,
    )
    session.id = "52345678-aaaa-bbbb-cccc-ddddeeeeffff"
    manager.configure_share(session, share_project=False)
    cmd = manager._build_linux_run_command(
        session,
        "computer_52345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-52345678",
    )
    joined = " ".join(cmd)

    assert "SELKIES_MANUAL_RESOLUTION=true" in joined
    assert "SELKIES_MANUAL_WIDTH=1600" in joined
    assert "SELKIES_MANUAL_HEIGHT=900" in joined
    assert "SELKIES_ENABLE_RESIZE=true" not in joined


def test_kasm_auto_resize_is_default_and_has_view_only_password(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(name="Kasm", kind="linux")
    session.id = "62345678-aaaa-bbbb-cccc-ddddeeeeffff"
    session.image = "kasmweb/desktop:1.16.0"
    session.image_provider = "kasmweb"
    session.live_view = "kasmvnc"
    manager.configure_share(session, share_project=False)
    cmd = manager._build_kasm_run_command(
        session,
        "computer_62345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-62345678",
    )
    joined = " ".join(cmd)

    assert "KVNC_DESKTOP_ALLOW_RESIZE=true" in joined
    assert session.viewer_password
    assert session.viewer_view_password
    # Observers must never receive the controller password.
    assert session.viewer_view_password != session.viewer_password
    assert f"VNC_PW={session.viewer_password}" in joined
    assert f"VNC_VIEW_ONLY_PW={session.viewer_view_password}" in joined


def test_kasm_fixed_desktop_disables_remote_resize(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    session = BrowserSession(
        name="Kasm",
        kind="linux",
        desktop_auto_resize=False,
        desktop_width=1366,
        desktop_height=768,
    )
    session.id = "72345678-aaaa-bbbb-cccc-ddddeeeeffff"
    session.image = "kasmweb/desktop:1.16.0"
    session.image_provider = "kasmweb"
    session.live_view = "kasmvnc"
    manager.configure_share(session, share_project=False)
    cmd = manager._build_kasm_run_command(
        session,
        "computer_72345678",
        str(tmp_path / "profile"),
        f"/computer/{session.id}/",
        "codx-computer-72345678",
    )
    joined = " ".join(cmd)

    assert "KVNC_DESKTOP_ALLOW_RESIZE=false" in joined
    assert "VNC_RESOLUTION=1366x768" in joined
