"""
Shared fixtures for the application-log explorer tests.

Source discovery now also lists docker containers (``sudo docker ps``). Docker
is available on a developer machine and inside the API container, which would
make the source list depend on what happens to be running, so the command
runner is stubbed out for EVERY test. A test that wants containers overrides
``sources.command_runner`` with its own fake.
"""

import pytest

from codx.junior.logs import sources


@pytest.fixture(autouse=True)
def no_docker(monkeypatch):
    """
    Make docker unavailable and reset the container cache for each test.

    Returns:
        The fake runner (raises, like a missing docker CLI).
    """

    def _unavailable(command, timeout=15):
        raise RuntimeError("docker is not available in tests")

    monkeypatch.setattr(sources, "command_runner", _unavailable)
    monkeypatch.setattr(sources, "_containers", sources._ContainersCache())
    return _unavailable
