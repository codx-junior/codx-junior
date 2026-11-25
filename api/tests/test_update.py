"""
Tests for the self-update orchestration and its API routes.

No Docker daemon and no installer are required: ``update`` takes the Docker
Engine client as an argument (or through ``docker_client``, which is
monkeypatched), and the helper is only *created*, never executed.
"""

import importlib
import json
import pathlib
import sys
import types
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from codx.junior import update
from codx.junior.infra.docker_client import DockerAPIError

IMAGE = "codxjuniorai/codx-junior:latest"
HOME = "/opt/codx-junior"
SETTINGS_MOUNT = "/opt/codx-junior/data/settings"


# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeDocker:
    """Minimal Engine API surface used by the update module."""

    def __init__(self, container=None, images=None, helper_exists=False):
        self.container = container or self.compose_container()
        self.images = dict(images or {})
        self.helper_exists = helper_exists
        self.calls = []

    @staticmethod
    def compose_container(**overrides):
        data = {
            "Id": "self-container-id",
            "Name": "/codx-junior",
            "Config": {
                "Image": IMAGE,
                "Hostname": "self-container-id",
                "Labels": {
                    update.LABEL_PROJECT: "codx-junior",
                    update.LABEL_WORKING_DIR: f"{HOME}/lib/runtime",
                    update.LABEL_CONFIG_FILES: (
                        f"{HOME}/lib/runtime/docker-compose.yaml,"
                        f"{HOME}/lib/runtime/docker-compose.local.yaml"
                    ),
                },
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": SETTINGS_MOUNT,
                    "Destination": "/home/codx-junior/.codx-junior",
                }
            ],
        }
        data.update(overrides)
        return data

    def get_json(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path.startswith("/containers/") and path.endswith("/json"):
            container_id = unquote(path[len("/containers/"):-len("/json")])
            if container_id == self.container["Id"]:
                return self.container
            if self.helper_exists:
                return {"Id": container_id}
            raise DockerAPIError(404, "No such container", path)
        if path.startswith("/images/") and path.endswith("/json"):
            image = unquote(path[len("/images/"):-len("/json")])
            if image in self.images:
                return {"Id": self.images[image]}
            raise DockerAPIError(404, "No such image", path)
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, params=None, json_body=None):
        self.calls.append(("POST", path, params, json_body))
        if path == "/containers/create":
            return {"Id": "helper-container-id"}
        if path.startswith("/containers/") and path.endswith("/start"):
            return {}
        raise AssertionError(f"unexpected POST {path}")

    def close(self):
        self.calls.append(("CLOSE", "", None))


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CODX_JUNIOR_CONFIG_FOLDER", str(tmp_path))
    return tmp_path


@pytest.fixture()
def docker(folder, monkeypatch):
    # The settings folder must be a mount *of the container*: that is what the
    # helper shares with the API (the installer's compose mounts
    # <data>/settings at CODX_JUNIOR_CONFIG_FOLDER).
    container = FakeDocker.compose_container(
        Mounts=[
            {
                "Type": "bind",
                "Source": "/host/settings",
                "Destination": str(folder),
            }
        ]
    )
    fake = FakeDocker(container=container, images={IMAGE: "sha256:old"})
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")
    monkeypatch.setattr(update, "docker_client", lambda timeout=None: fake)
    return fake


def write_descriptor(folder, **overrides):
    data = {
        "source": "codx-junior-installer",
        "home": HOME,
        "instance": "codx-junior",
        "mode": "local",
        "container": False,
        "image": IMAGE,
        "env_file": f"{HOME}/config/codx-junior.env",
        "data_dir": f"{HOME}/data",
        "cli": f"{HOME}/bin/codx-junior",
        "installer": f"{HOME}/lib/install.sh",
    }
    data.update(overrides)
    (folder / update.DESCRIPTOR_NAME).write_text(json.dumps(data))
    return data


# ── Image references ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("codxjuniorai/codx-junior:latest", ("codxjuniorai/codx-junior", "latest")),
        ("codxjuniorai/codx-junior", ("codxjuniorai/codx-junior", "latest")),
        ("localhost:5000/team/app", ("localhost:5000/team/app", "latest")),
        ("localhost:5000/team/app:v1", ("localhost:5000/team/app", "v1")),
        ("repo@sha256:abc", ("repo@sha256:abc", "")),
    ],
)
def test_split_image(value, expected):
    assert update.split_image(value) == expected


@pytest.mark.parametrize(
    "value", ["codx-junior; rm -rf /", "image with spaces", "$(whoami)", ""]
)
def test_validate_image_rejects_unsafe_references(value):
    with pytest.raises(update.UpdateError):
        update.validate_image(value)


def test_validate_image_accepts_a_release_tag():
    assert update.validate_image(IMAGE) == IMAGE
    assert update.validate_image("codxjuniorai/codx-junior:v1.4.0") == "codxjuniorai/codx-junior:v1.4.0"


# ── Discovery ────────────────────────────────────────────────────────────────


def test_discovers_the_installation_from_the_descriptor(folder, docker):
    write_descriptor(folder)

    installation, self_container = update.discover(docker)

    assert installation.home == HOME
    assert installation.instance == "codx-junior"
    assert installation.layout == "host"
    assert installation.deployment == "local"
    assert installation.cli == f"{HOME}/bin/codx-junior"
    assert installation.source == "descriptor"
    assert self_container.id == "self-container-id"


def test_descriptor_with_a_root_home_is_rejected(folder, docker):
    """The home becomes a bind-mount source: ``/`` must never reach Docker."""
    write_descriptor(folder, home="/")

    with pytest.raises(update.UpdateUnsupportedError):
        update.discover(docker)


def test_falls_back_to_compose_labels_for_old_installations(folder, docker):
    installation, _ = update.discover(docker)

    assert installation.source == "labels"
    assert installation.home == HOME
    assert installation.layout == "host"
    assert installation.image == IMAGE
    assert installation.env_file == f"{HOME}/config/codx-junior.env"


def test_detects_a_dind_layout_from_the_labels(folder, monkeypatch):
    container = FakeDocker.compose_container()
    container["Config"]["Labels"] = {
        update.LABEL_PROJECT: "codx-junior",
        update.LABEL_WORKING_DIR: "/data",
        update.LABEL_CONFIG_FILES: "/data/docker-compose.yaml",
    }
    fake = FakeDocker(container=container)
    monkeypatch.setattr(update, "docker_client", lambda timeout=None: fake)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")

    installation, _ = update.discover(fake)

    assert installation.layout == "dind"
    assert installation.home == "/data"


def test_rejects_containers_that_are_not_an_installation(folder, monkeypatch):
    container = FakeDocker.compose_container()
    container["Config"]["Labels"] = {}
    fake = FakeDocker(container=container)
    monkeypatch.setattr(update, "docker_client", lambda timeout=None: fake)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")

    with pytest.raises(update.UpdateUnsupportedError):
        update.discover(fake)


def test_source_for_maps_a_nested_settings_folder_to_the_host(folder, monkeypatch):
    container = FakeDocker.compose_container(
        Mounts=[
            {"Type": "bind", "Source": "/srv/codx", "Destination": "/opt/codx-junior"},
            {"Type": "bind", "Source": "/srv/codx/data", "Destination": f"/opt/codx-junior/data"},
        ]
    )
    fake = FakeDocker(container=container)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")

    self_container = update.inspect_self(fake)

    assert self_container.source_for("/opt/codx-junior/data/settings") == "/srv/codx/data/settings"
    assert self_container.source_for("/opt/codx-junior/bin/codx-junior") == "/srv/codx/bin/codx-junior"
    assert self_container.source_for("/elsewhere") == ""


def test_support_accepts_a_settings_folder_inside_a_home_mount(folder, monkeypatch):
    container = FakeDocker.compose_container(
        Mounts=[
            {"Type": "bind", "Source": "/opt/codx-junior", "Destination": "/opt/codx-junior"}
        ]
    )
    fake = FakeDocker(container=container)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")
    monkeypatch.setenv("CODX_JUNIOR_CONFIG_FOLDER", "/opt/codx-junior/data/settings")

    installation, self_container = update.discover(fake)

    assert update.support(installation, self_container).supported is True


def test_support_reports_the_manual_command_without_the_settings_mount(folder, monkeypatch):
    container = FakeDocker.compose_container(Mounts=[])
    fake = FakeDocker(container=container)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")

    installation, self_container = update.discover(fake)
    decision = update.support(installation, self_container)

    assert decision.supported is False
    assert "bind mount" in decision.reason
    assert "bin/codx-junior update" in decision.manual_command


def test_support_is_available_on_a_compose_host_installation(folder, docker):
    write_descriptor(folder)
    installation, self_container = update.discover(docker)
    assert update.support(installation, self_container).supported is True


def test_dind_instances_are_updated_by_restarting_the_dind_container(folder, monkeypatch):
    container = FakeDocker.compose_container()
    container["Config"]["Labels"] = {
        update.LABEL_PROJECT: "codx-junior",
        update.LABEL_WORKING_DIR: "/data",
        update.LABEL_CONFIG_FILES: "/data/docker-compose.yaml",
    }
    fake = FakeDocker(container=container)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")

    installation, self_container = update.discover(fake)
    decision = update.support(installation, self_container)

    assert decision.supported is False
    assert "Docker-in-Docker" in decision.reason
    assert decision.manual_command.startswith("docker restart")


# ── Helper command construction ──────────────────────────────────────────────


def test_helper_script_runs_the_installer_cli_with_the_target_image():
    installation = update.Installation(
        home=HOME,
        instance="codx-junior",
        image=IMAGE,
        cli=f"{HOME}/bin/codx-junior",
        installer=f"{HOME}/lib/install.sh",
        env_file=f"{HOME}/config/codx-junior.env",
    )

    script = update.helper_script(installation, image="codxjuniorai/codx-junior:v9")

    assert f"if [ ! -f {HOME}/lib/install.sh ]" in script
    assert f"bash {HOME}/bin/codx-junior update --non-interactive --image codxjuniorai/codx-junior:v9" in script
    assert f"{update.EXIT_MARKER}$rc" in script
    assert "exit 2" in script  # the home was not mounted


def test_helper_script_falls_back_to_install_sh_without_the_cli():
    installation = update.Installation(
        home=HOME,
        instance="codx-junior",
        image=IMAGE,
        cli=f"{HOME}/bin/codx-junior",
        installer=f"{HOME}/lib/install.sh",
        env_file=f"{HOME}/config/codx-junior.env",
    )

    script = update.helper_script(installation)

    assert f"bash {HOME}/lib/install.sh --home {HOME} --instance codx-junior" in script
    assert "--env-file" in script
    assert "upgrade --non-interactive" in script


# ── Pull + apply ─────────────────────────────────────────────────────────────


def _fake_pull(new_id="sha256:new", calls=None):
    def _pull(docker, image, timeout=None):
        if calls is not None:
            calls.append((docker, image))
        docker.images[image] = new_id

    return _pull


def test_run_update_starts_the_helper_when_the_image_changed(folder, docker, monkeypatch):
    write_descriptor(folder)
    monkeypatch.setattr(update, "pull_image", _fake_pull("sha256:new"))

    state = update.run_update()

    assert state["status"] == "updating"
    assert state["from_image_id"] == "sha256:old"
    assert state["to_image_id"] == "sha256:new"
    assert state["helper_id"] == "helper-container-id"

    create = [call for call in docker.calls if call[0] == "POST" and call[1] == "/containers/create"]
    assert len(create) == 1
    name = create[0][2]["name"]
    body = create[0][3]
    assert name.startswith("codx-junior-updater-")
    assert body["Image"] == IMAGE
    assert body["HostConfig"]["NetworkMode"] == "host"
    assert body["HostConfig"]["AutoRemove"] is True
    binds = body["HostConfig"]["Binds"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in binds
    assert f"{HOME}:{HOME}" in binds
    assert any(env.startswith("CODX_JUNIOR_LOG=") for env in body["Env"])
    assert "upgrade --non-interactive" in body["Cmd"][2]

    persisted = update.read_state()
    assert persisted["status"] == "updating"
    assert persisted["helper_id"] == "helper-container-id"
    assert persisted["log_file"].startswith("logs/update-")


def test_run_update_does_not_restart_when_the_image_is_current(folder, docker, monkeypatch):
    write_descriptor(folder)
    monkeypatch.setattr(update, "pull_image", _fake_pull("sha256:old"))

    state = update.run_update()

    assert state["status"] == "up_to_date"
    assert not [call for call in docker.calls if call[1] == "/containers/create"]


def test_run_update_restarts_when_forced(folder, docker, monkeypatch):
    write_descriptor(folder)
    monkeypatch.setattr(update, "pull_image", _fake_pull("sha256:old"))

    state = update.run_update(force=True)

    assert state["status"] == "updating"
    assert [call for call in docker.calls if call[1] == "/containers/create"]


def test_run_update_records_a_pull_failure(folder, docker, monkeypatch):
    write_descriptor(folder)
    docker.images = {}
    monkeypatch.setattr(
        update,
        "pull_image",
        lambda docker, image, timeout=None: (_ for _ in ()).throw(
            update.UpdateDockerError("registry said no")
        ),
    )

    with pytest.raises(update.UpdateDockerError):
        update.run_update()

    state = update.read_state()
    assert state["status"] == "failed"
    assert "registry said no" in state["message"]


def test_run_update_refuses_a_remote_daemon(folder, monkeypatch):
    write_descriptor(folder)
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.5:2376")

    with pytest.raises(update.UpdateUnsupportedError):
        update.run_update()


# ── State / log ──────────────────────────────────────────────────────────────


def _write_log(folder, name, lines):
    directory = folder / update.LOG_FOLDER
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("\n".join(lines) + "\n")


def test_completed_state_comes_from_the_helper_exit_marker(folder):
    _write_log(folder, "update.log", ["installing", f"{update.EXIT_MARKER}0"])
    update.write_state(
        {
            **update.empty_state(),
            "status": "updating",
            "helper_id": "gone",
            "log_file": "logs/update.log",
            "started_at": update._now(),
        }
    )

    state = update.describe_state()

    assert state["status"] == "completed"
    assert state["exit_code"] == 0
    assert "installing" in state["log"]


def test_failed_state_comes_from_a_non_zero_exit_marker(folder):
    _write_log(folder, "update.log", ["boom", f"{update.EXIT_MARKER}1"])
    update.write_state(
        {
            **update.empty_state(),
            "status": "updating",
            "helper_id": "gone",
            "log_file": "logs/update.log",
            "started_at": update._now(),
        }
    )

    state = update.describe_state()

    assert state["status"] == "failed"
    assert state["exit_code"] == 1


def test_running_state_while_the_helper_exists(folder, docker):
    update.write_state(
        {
            **update.empty_state(),
            "status": "updating",
            "helper_id": "helper-container-id",
            "log_file": "logs/update.log",
            "started_at": update._now(),
        }
    )
    docker.helper_exists = True

    assert update.describe_state(docker)["status"] == "updating"


def test_stale_running_state_becomes_unknown(folder):
    update.write_state(
        {
            **update.empty_state(),
            "status": "updating",
            "helper_id": "gone",
            "started_at": update._now() - (update.STALE_SECONDS + 10),
        }
    )

    assert update.describe_state()["status"] == "unknown"


def test_start_update_refuses_a_dind_instance(folder, monkeypatch):
    container = FakeDocker.compose_container()
    container["Config"]["Labels"] = {
        update.LABEL_PROJECT: "codx-junior",
        update.LABEL_WORKING_DIR: "/data",
        update.LABEL_CONFIG_FILES: "/data/docker-compose.yaml",
    }
    fake = FakeDocker(container=container)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")
    monkeypatch.setattr(update, "docker_client", lambda timeout=None: fake)

    with pytest.raises(update.UpdateUnsupportedError) as raised:
        update.start_update()

    assert "Docker-in-Docker" in raised.value.detail
    assert not [call for call in fake.calls if call[1] == "/containers/create"]


def test_run_update_refuses_a_dind_instance(folder, monkeypatch):
    container = FakeDocker.compose_container()
    container["Config"]["Labels"] = {
        update.LABEL_PROJECT: "codx-junior",
        update.LABEL_WORKING_DIR: "/data",
        update.LABEL_CONFIG_FILES: "/data/docker-compose.yaml",
    }
    fake = FakeDocker(container=container)
    monkeypatch.setenv("CODX_JUNIOR_SELF_CONTAINER", "self-container-id")
    monkeypatch.setattr(update, "docker_client", lambda timeout=None: fake)

    with pytest.raises(update.UpdateUnsupportedError):
        update.run_update()

    assert update.read_state()["status"] == "failed"
    assert not [call for call in fake.calls if call[1] == "/containers/create"]


def test_start_update_is_exclusive(folder, docker, monkeypatch):
    write_descriptor(folder)
    monkeypatch.setattr(update, "pull_image", _fake_pull("sha256:new"))
    update.write_state(
        {
            **update.empty_state(),
            "status": "updating",
            "helper_id": "helper",
            "started_at": update._now(),
        }
    )

    with pytest.raises(update.UpdateConflictError):
        update.start_update()


# ── Router ───────────────────────────────────────────────────────────────────


def _stub_package(name, **attrs):
    module = types.ModuleType(name)
    module.__path__ = []
    module.__dict__.update(attrs)
    return module


class _CodxUser:
    def __init__(self, username="admin", role="admin"):
        self.username = username
        self.role = role


@pytest.fixture()
def router_app(monkeypatch):
    """
    The system router with the two fat imports of ``codx.junior.api`` stubbed.

    ``require_admin`` pulls the whole API bootstrap (and therefore every
    optional dependency) and ``CodxUser`` comes from the model package, which is
    unrelated to this route. ``monkeypatch.setitem`` restores the real modules
    after the test, so the stubs never leak into another test file.
    """
    from fastapi import FastAPI

    api_dir = pathlib.Path(__file__).resolve().parents[1] / "codx" / "junior" / "api"
    api_pkg = _stub_package("codx.junior.api", require_admin=lambda: _CodxUser())
    api_pkg.__path__ = [str(api_dir)]
    monkeypatch.setitem(sys.modules, "codx.junior.api", api_pkg)
    monkeypatch.setitem(sys.modules, "codx.junior.model", _stub_package("codx.junior.model"))
    monkeypatch.setitem(
        sys.modules,
        "codx.junior.model.model",
        _stub_package("codx.junior.model.model", CodxUser=_CodxUser),
    )
    sys.modules.pop("codx.junior.api.system", None)
    system = importlib.import_module("codx.junior.api.system")

    monkeypatch.setattr(
        system.update, "snapshot", lambda: {"supported": True, "current": {}, "update": {}}
    )
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[system.require_admin] = lambda: _CodxUser()
    return SimpleNamespace(app=app, system=system)


def _client(router_app):
    from fastapi.testclient import TestClient

    return TestClient(router_app.app)


def test_get_update_status_returns_the_snapshot(router_app):
    response = _client(router_app).get("/api/system/update")
    assert response.status_code == 200
    assert response.json()["supported"] is True


def test_post_update_starts_and_returns_202(router_app, monkeypatch):
    calls = []

    def _start(**kwargs):
        calls.append(kwargs)
        return {"status": "pulling"}

    monkeypatch.setattr(router_app.system.update, "start_update", _start)
    response = _client(router_app).post(
        "/api/system/update", json={"image": "", "force": True}
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pulling"
    assert calls == [{"image": "", "force": True}]


def test_post_update_maps_conflicts(router_app, monkeypatch):
    def _start(**kwargs):
        raise update.UpdateConflictError("An update is already running.")

    monkeypatch.setattr(router_app.system.update, "start_update", _start)
    response = _client(router_app).post("/api/system/update", json={})
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_post_update_rejects_an_unsafe_image(router_app):
    response = _client(router_app).post(
        "/api/system/update", json={"image": "evil; rm -rf /"}
    )
    assert response.status_code == 400
