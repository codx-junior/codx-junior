"""
Company channels are Messenger chats hosted by a project the company links.

The channel modal offers the company's linked projects, but a link is only a
reference: the project must also be discovered by this installation. When it is
not (the folder was removed, moved, or the discovery scan did not reach it) the
owner picked a project from the selector and got a technical
"Project '...' is not available (not discovered)" 502. Channel creation now
fails with an actionable conflict, and the happy path is pinned here too.

Run with::

    cd api && pytest tests/test_company_chat.py -q
"""

import os
import tempfile

import pytest

# The tests create real company documents: keep them out of the installation
# store even when a test builds its own CompanyManager directly.
os.environ.setdefault("CODX_JUNIOR_COMPANIES_PATH", tempfile.mkdtemp())

from codx.junior.company.manager import (  # noqa: E402
    CompanyConflict,
    CompanyManager,
)


@pytest.fixture(autouse=True)
def _isolated_companies_root(tmp_path, monkeypatch):
    """Every test gets a private companies root (never the installation one)."""
    monkeypatch.setenv(
        "CODX_JUNIOR_COMPANIES_PATH", str(tmp_path / "companies")
    )


def _catalog():
    return [
        {
            "project_id": "p1",
            "project_name": "Project One",
            "project_path": "/tmp/project-one",
            "codx_path": "/tmp/project-one/.codx",
        }
    ]


class FakeChatManager:
    """Chat storage double: records the saved chats and assigns ids."""

    def __init__(self):
        self.saved = []

    def save_chat(self, chat=None, **_kwargs):
        chat.id = f"chat-{len(self.saved) + 1}"
        self.saved.append(chat)
        return chat


def _manager(project_settings=None, chat_manager=None):
    return CompanyManager(
        projects_provider=_catalog,
        project_settings_provider=lambda project_id: project_settings,
        chat_manager_provider=lambda project_id: chat_manager,
    )


def test_channel_creation_explains_a_linked_project_that_is_not_discovered():
    manager = _manager(project_settings=None)
    company = manager.create_company(
        {"name": "Acme"}, actor="tester", project_ids=["p1"]
    )

    with pytest.raises(CompanyConflict) as error:
        manager.create_company_chat(
            company.id, name="platform", project_id="p1", actor="tester"
        )

    message = str(error.value)
    assert "not discovered" in message
    assert "refresh the project list" in message.lower()
    # Nothing half-created: no channel reference, no hosted chat.
    assert manager.get_company(company.id).chats == []


def test_channel_creation_uses_the_linked_project_as_the_host():
    chat_manager = FakeChatManager()
    manager = _manager(project_settings=object(), chat_manager=chat_manager)
    company = manager.create_company(
        {"name": "Acme"}, actor="tester", project_ids=["p1"]
    )

    saved = manager.create_company_chat(
        company.id, name="platform", project_id="p1", actor="tester"
    )

    assert [chat.chat_id for chat in saved.chats] == ["chat-1"]
    hosted = chat_manager.saved[0]
    assert hosted.name == "platform"
    assert hosted.mode == "channel"
    assert hosted.board == "messenger"
    assert hosted.owner_project_id == "p1"
    assert hosted.project_id == "p1"
