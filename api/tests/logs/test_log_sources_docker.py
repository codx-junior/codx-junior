"""
Docker container sources for the log explorer.

The legacy ``/api/system/logs`` endpoint listed running containers next to the
log files; the explorer must do the same (``kind="docker"``, id
``docker:<name>``) and must never break when docker is unavailable.
"""

import pytest

from codx.junior.logs import service, sources

CONTAINERS = "codx-junior-api\nbrowser_x\n\n"

CONTAINER_LINES = [
    '[2026-01-02 10:11:12,000] INFO [codx.junior.app:10] '
    'Request http://host/api/chat - 12.5 ms "GET /api/chat HTTP/1.1" 200',
    '[2026-01-02 10:11:13,000] ERROR [codx.junior.app:11] '
    'Request http://host/api/chat - 2.0 ms "POST /api/chat HTTP/1.1" 500',
]


def _fake_runner(outputs):
    """Return a command runner answering from *outputs* (substring match)."""
    calls = []

    def runner(command, timeout=15):
        calls.append(list(command))
        joined = " ".join(command)
        for needle, output in outputs.items():
            if needle in joined:
                return output
        raise RuntimeError(f"unexpected command: {joined}")

    runner.calls = calls
    return runner


@pytest.fixture()
def docker(monkeypatch):
    """Point the runner at a fake docker CLI exposing one container."""
    runner = _fake_runner(
        {"docker ps": CONTAINERS, "docker logs": "\n".join(CONTAINER_LINES) + "\n"}
    )
    monkeypatch.setattr(sources, "command_runner", runner)
    monkeypatch.setattr(sources, "_containers", sources._ContainersCache())
    return runner


@pytest.fixture()
def empty_folder(tmp_path, monkeypatch):
    """Point file discovery at an empty folder (containers only)."""
    folder = tmp_path / "logs"
    folder.mkdir()
    monkeypatch.setenv("CODX_JUNIOR_API_LOGS", str(folder))
    return folder


# ── Discovery ─────────────────────────────────────────────────────────────────


def test_containers_become_docker_sources(docker):
    found = sources.list_docker_sources()

    assert [source.id for source in found] == [
        "docker:browser_x",
        "docker:codx-junior-api",
    ]
    assert {source.kind for source in found} == {sources.KIND_DOCKER}
    assert found[1].path == "codx-junior-api"
    assert found[1].entries_estimate == 0


def test_container_list_is_cached(docker, monkeypatch):
    sources.list_docker_sources()
    sources.list_docker_sources()

    ps_calls = [call for call in docker.calls if "ps" in call]
    assert len(ps_calls) == 1


def test_cache_can_be_bypassed(docker):
    sources.list_docker_sources()
    sources.docker_container_names(force=True)

    assert len([call for call in docker.calls if "ps" in call]) == 2


def test_unavailable_docker_yields_no_containers():
    """The autouse conftest stub raises, exactly like a missing docker CLI."""
    assert sources.list_docker_sources() == []
    assert all(source.kind != sources.KIND_DOCKER for source in sources.list_all_sources())


def test_list_all_sources_lists_files_before_containers(tmp_path, docker, monkeypatch):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "api.log").write_text("[2026-01-02 10:11:12] INFO [m:1] hello\n", encoding="utf-8")
    monkeypatch.setenv("CODX_JUNIOR_API_LOGS", str(folder))

    ids = [source.id for source in sources.list_all_sources()]

    assert ids == ["api.log", "docker:browser_x", "docker:codx-junior-api"]


def test_docker_id_is_resolvable(empty_folder, docker):
    assert sources.get_source("docker:codx-junior-api", str(empty_folder)).name == "codx-junior-api"
    assert sources.get_source("nope", str(empty_folder)) is None


def test_container_names_are_read_from_the_cli(docker):
    assert sources.read_container_tail("codx-junior-api") == CONTAINER_LINES


# ── Query / entry / context / tail over a container ───────────────────────────


def test_query_scans_a_container(empty_folder, docker):
    payload = service.query_logs(
        ["docker:codx-junior-api"], q="", page_size=10, folder=str(empty_folder)
    )

    assert payload["total"] == 2
    assert {entry["source"] for entry in payload["entries"]} == {"docker:codx-junior-api"}
    assert payload["entries"][0]["id"] == "docker:codx-junior-api#1"
    assert payload["stats"]["scanned_lines"] == 2


def test_query_filters_a_container_by_level(empty_folder, docker):
    payload = service.query_logs(
        ["docker:codx-junior-api"], levels=["error"], folder=str(empty_folder)
    )

    assert payload["total"] == 1
    assert payload["entries"][0]["level"] == "error"


def test_entry_and_context_work_on_a_container(empty_folder, docker):
    entry = service.get_entry("docker:codx-junior-api#0", folder=str(empty_folder))
    assert entry is not None
    assert "Request http://host/api/chat" in entry["raw"]

    context = service.get_context(
        "docker:codx-junior-api#1", before=1, after=1, folder=str(empty_folder)
    )
    assert [item["id"] for item in context["entries"]] == [
        "docker:codx-junior-api#0",
        "docker:codx-junior-api#1",
    ]


def test_tail_uses_the_entry_count_as_cursor(empty_folder, docker):
    first = service.tail("docker:codx-junior-api", folder=str(empty_folder))
    assert first["cursor"] == "2"
    assert len(first["entries"]) == 2

    second = service.tail(
        "docker:codx-junior-api", since_cursor=first["cursor"], folder=str(empty_folder)
    )
    assert second["cursor"] == "2"
    assert second["entries"] == []


def test_tail_falls_back_to_default_source(empty_folder, docker):
    payload = service.tail(folder=str(empty_folder))

    assert payload["cursor"] == "2"
