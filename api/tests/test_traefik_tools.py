"""
Offline tests for the read-only Traefik tool (``codx.junior.tools.traefik_tools``).

The Docker CLI and the route builder are stubbed, so nothing here needs a
running Traefik container or a real installation.

Run with::

    PYTHONPATH=api python -m pytest api/tests/test_traefik_tools.py -q
"""

import json

import pytest

from codx.junior.tools import traefik_tools


INSPECT_PAYLOAD = {
    "Name": "/traefik-codx-junior",
    "RestartCount": 2,
    "State": {
        "Status": "running",
        "StartedAt": "2026-01-01T00:00:00Z",
        "Health": {"Status": "healthy"},
    },
    "Config": {
        "Image": "traefik:latest",
        "Cmd": [
            "--configFile=/config/traefik.yaml",
            "--providers.http.endpoint=http://codx-junior-api:19980/api/traefik/config",
            "--entryPoints.web.address=:80",
        ],
        "Env": ["TRAEFIK_LOG_LEVEL=DEBUG"],
        "Labels": {
            "traefik.enable": "true",
            "com.docker.compose.project": "codx-junior",
        },
    },
    "Mounts": [{"Source": "/srv/traefik", "Destination": "/config"}],
}


class DockerStub:
    """Stand-in for ``traefik_tools._docker`` driven by canned output."""

    def __init__(self, inspect_payload=INSPECT_PAYLOAD, ps_output=None, logs_output=""):
        self.calls = []
        self.inspect_payload = inspect_payload
        self.ps_output = (
            ps_output
            if ps_output is not None
            else "traefik-codx-junior\ttraefik:latest\n"
        )
        self.logs_output = logs_output

    def __call__(self, args, timeout=20):
        self.calls.append(list(args))
        command = args[0]
        if command == "ps":
            return traefik_tools.TraefikCommandResult(self.ps_output, "", 0)
        if command == "inspect":
            if self.inspect_payload is None:
                return traefik_tools.TraefikCommandResult(
                    "", "no such container", 1
                )
            return traefik_tools.TraefikCommandResult(
                json.dumps([self.inspect_payload]), "", 0
            )
        if command == "logs":
            return traefik_tools.TraefikCommandResult(self.logs_output, "", 0)
        return traefik_tools.TraefikCommandResult("", "unexpected command", 1)


def route(row_id, rule, source="custom", target="http://backend:19980", enabled=True):
    return {
        "id": row_id,
        "name": row_id,
        "description": "",
        "source": source,
        "scope": "global",
        "rule": rule,
        "target": target,
        "target_url": target,
        "managed_by": "http-provider",
        "enabled": enabled,
        "issues": [],
    }


def view(routes, issues=None):
    return {
        "routes": routes,
        "issues": list(issues or []),
        "stats": {
            "total": len(routes),
            "enabled": sum(1 for item in routes if item.get("enabled")),
            "disabled": sum(1 for item in routes if not item.get("enabled")),
            "issues": len(list(issues or [])),
        },
        "defaults": {"domain": "example.test"},
    }


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Never read the real container/config of the machine running the tests."""
    monkeypatch.delenv(traefik_tools.CONTAINER_ENV, raising=False)
    monkeypatch.setenv(
        traefik_tools.CONFIG_PATH_ENV, str(tmp_path / "missing-traefik.yaml")
    )
    monkeypatch.setattr(traefik_tools, "_docker", DockerStub())


def test_unknown_action_is_rejected():
    response = traefik_tools.traefik(action="delete-everything")

    assert "Unknown action" in response.user_response
    assert "status" in response.user_response


def test_invalid_tail_is_rejected():
    response = traefik_tools.traefik(action="logs", tail="many")

    assert "`tail` must be a number" in response.user_response


def test_status_reports_container_command_and_route_stats(monkeypatch):
    monkeypatch.setattr(
        traefik_tools.traefik_manager,
        "build_view",
        lambda project_id=None, refresh=False: view(
            [route("r1", "Host(`a.test`)")], issues=["r1: Rule expression is empty"]
        ),
    )

    text = traefik_tools.traefik(action="status").user_response

    assert "traefik-codx-junior" in text
    assert "traefik:latest" in text
    assert "| Restarts | 2 |" in text
    assert "healthy" in text
    # The effective provider endpoint comes from the container command line.
    assert "http://codx-junior-api:19980/api/traefik/config" in text
    assert "--configFile=/config/traefik.yaml" in text
    assert "1 total · 1 enabled" in text
    assert "Rule expression is empty" in text


def test_status_without_container_is_actionable(monkeypatch):
    monkeypatch.setattr(
        traefik_tools, "_docker", DockerStub(inspect_payload=None, ps_output="")
    )
    monkeypatch.setattr(
        traefik_tools.traefik_manager,
        "build_view",
        lambda project_id=None, refresh=False: view([]),
    )

    text = traefik_tools.traefik(action="status").user_response

    assert "No Traefik container found" in text
    assert traefik_tools.CONTAINER_ENV in text


def test_routes_filters_by_search(monkeypatch):
    monkeypatch.setattr(
        traefik_tools.traefik_manager,
        "build_view",
        lambda project_id=None, refresh=False: view([
            route("api", "Host(`a.test`) && PathPrefix(`/api`)"),
            route("docs", "Host(`a.test`) && PathPrefix(`/docs`)"),
        ]),
    )

    text = traefik_tools.traefik(action="routes", search="/api").user_response

    assert "1 of 2 route(s)" in text
    assert "`/api`" in text
    assert "`/docs`" not in text
    assert "example.test" in text


def test_routes_output_is_bounded(monkeypatch):
    many = [route(f"r{index}", f"Host(`a.test`) && PathPrefix(`/p{index}`)") for index in range(60)]
    monkeypatch.setattr(
        traefik_tools.traefik_manager,
        "build_view",
        lambda project_id=None, refresh=False: view(many),
    )

    text = traefik_tools.traefik(action="routes").user_response

    assert f"60 of 60 route(s)" in text
    assert "more route(s); narrow the list with `search`" in text
    assert text.count("`r") < 60


def test_generated_config_is_the_polled_payload(monkeypatch):
    config = {
        "http": {
            "routers": {"r1": {"rule": "Host(`a.test`)", "service": "s1"}},
            "services": {"s1": {"loadBalancer": {"servers": [{"url": "http://api:19980"}]}}},
            "middlewares": {"m1": {"headers": {}}},
        }
    }
    monkeypatch.setattr(
        traefik_tools.traefik_manager, "build_config", lambda: config
    )

    text = traefik_tools.traefik(action="config").user_response

    assert "1 router(s), 1 service(s), 1 middleware(s)" in text
    assert '"r1"' in text
    assert "Host(`a.test`)" in text


def test_static_config_file_is_redacted(tmp_path, monkeypatch):
    config_file = tmp_path / "traefik.yaml"
    config_file.write_text(
        "api:\n  dashboard: true\ncertificatesResolvers:\n  ssl:\n    acme:\n"
        "      storage: /config/acme.json\n      keyPassword: hunter2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(traefik_tools.CONFIG_PATH_ENV, str(config_file))

    text = traefik_tools.traefik(action="config", source="file").user_response

    assert "dashboard: true" in text
    assert "hunter2" not in text
    assert "keyPassword: ***" in text


def test_logs_filter_and_count_failures(monkeypatch):
    logs = "\n".join([
        '{"level":"info","msg":"Configuration loaded"}',
        '{"ClientHost":"1.2.3.4","RouterName":"codx-junior@docker","DownstreamStatus":200}',
        '{"RouterName":"codx-junior-api@docker","DownstreamStatus":502,"msg":"error"}',
    ])
    monkeypatch.setattr(traefik_tools, "_docker", DockerStub(logs_output=logs))

    text = traefik_tools.traefik(action="logs", search="codx-junior-api").user_response

    assert "1 shown" in text
    assert "DownstreamStatus\":502" in text
    assert "Configuration loaded" not in text
    assert "1 with an error/status marker" in text


def test_logs_without_container_reports_the_env_var(monkeypatch):
    monkeypatch.setattr(
        traefik_tools, "_docker", DockerStub(inspect_payload=None, ps_output="")
    )

    text = traefik_tools.traefik(action="logs").user_response

    assert "No Traefik container found" in text
    assert traefik_tools.CONTAINER_ENV in text
