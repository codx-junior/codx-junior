"""
Company ticket workbench: place-as-company and human review of a delivery.

The workbench adds two behaviours on top of the per-project ticketing service:

* a ticket can be placed by the **company** on one of its own projects (the
  service is bound to the target, so the normal "a project cannot ticket
  itself" rule is bypassed explicitly, and no source ledger is written);
* a finished delivery can be **accepted** (the review is recorded on the ticket
  without reopening it) or **sent back** (the ticket reopens, is queued again
  and keeps its conversation/branch history).

Run with::

    cd api && pytest tests/test_company_tickets.py -q
"""

from __future__ import annotations

import json

import pytest

from codx.junior.chat_manager import ChatManager
from codx.junior.settings import CODXJuniorSettings
from codx.junior.ticketing.model import (
    TICKET_BOARD,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DONE,
    column_for_status,
)
from codx.junior.ticketing.service import TicketError, TicketService


def _project(root, name, project_id):
    """Build a project rooted at *root* (same shape the ticketing tests use)."""
    (root / ".codx").mkdir(parents=True, exist_ok=True)
    (root / ".codx" / "project.json").write_text(
        json.dumps({"project_id": project_id, "project_name": name}),
        encoding="utf-8",
    )
    return CODXJuniorSettings(
        project_id=project_id,
        project_name=name,
        abs_project_path=str(root),
        codx_path=str(root / ".codx"),
    )


@pytest.fixture
def target_project(tmp_path):
    """A single project that receives a company ticket."""
    root = tmp_path / "platform"
    return _project(root, "platform", "platform-id"), root


def _company_ticket(settings):
    """Place a company-source ticket on *settings* and return the record."""
    service = TicketService(settings=settings, user="owner")
    return service, service.create(
        target="platform-id",
        target_settings=settings,
        source_project_id="company:cmp-acme",
        source_project_name="Acme (company)",
        track_ledger=False,
        name="Export an empty board",
        description="Exporting an empty board crashes. Expected: a header row.",
        kind="bug",
        autonomous=True,
        auto_accept=True,
    )


def test_a_company_can_place_a_ticket_on_its_own_project(target_project):
    settings, root = target_project
    service, record = _company_ticket(settings)

    assert record.target_project_id == "platform-id"
    assert record.source_project_id == "company:cmp-acme"
    assert record.source_project_name == "Acme (company)"
    assert record.board == TICKET_BOARD

    # A company ticket is not a project-to-project request: there is no source
    # ledger for it, and the ticket is stored on the target board.
    assert not (root / ".codx" / "tickets" / "tickets.json").exists()
    files = sorted((root / ".codx" / "tasks" / TICKET_BOARD).rglob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["ticket"]["source_project_id"] == "company:cmp-acme"
    assert stored["ticket"]["autonomy"]["enabled"] is True
    assert stored["ticket"]["autonomy"]["auto_accept"] is True


def _finish(settings, ticket_id):
    """Simulate a green autonomous run that closed the ticket."""
    manager = ChatManager(settings=settings)
    chat = manager.find_by_id(ticket_id)
    chat.ticket.status = TICKET_STATUS_DONE
    chat.column = column_for_status(TICKET_STATUS_DONE)
    chat.status = "done"
    chat.ticket.autonomy.mark_passed("## What happened\nExported the empty board.")
    manager.save_chat(chat=chat)


def test_review_accepts_a_delivery_without_reopening_it(target_project):
    settings, _root = target_project
    service, record = _company_ticket(settings)
    _finish(settings, record.ticket_id)

    reviewed = service.review(
        record.ticket_id, decision="accept", note="Looks good", reviewed_by="owner"
    )

    assert reviewed.status == TICKET_STATUS_DONE
    autonomy = reviewed.autonomy
    assert autonomy["reviewed_by"] == "owner"
    assert autonomy["reviewed_at"]
    assert autonomy["review_note"] == "Looks good"

    # The review is a message in the same conversation (the agent and the
    # requester both see it) and the ticket did not move.
    chat = ChatManager(settings=settings).find_by_id(record.ticket_id)
    assert chat.ticket.status == TICKET_STATUS_DONE
    assert any("Looks good" in (message.content or "") for message in chat.messages)


def test_review_changes_reopens_and_queues_the_same_ticket(target_project):
    settings, _root = target_project
    service, record = _company_ticket(settings)
    _finish(settings, record.ticket_id)

    reopened = service.review(
        record.ticket_id,
        decision="changes",
        note="The CSV has no header",
        reviewed_by="owner",
    )

    assert reopened.status == TICKET_STATUS_ACCEPTED
    assert reopened.column == column_for_status(TICKET_STATUS_ACCEPTED)
    assert reopened.autonomy["enabled"] is True
    assert reopened.autonomy["state"] == "queued"
    # A new run must be reviewed again when it finishes.
    assert reopened.autonomy["reviewed_at"] is None

    chat = ChatManager(settings=settings).find_by_id(record.ticket_id)
    assert chat.ticket.status == TICKET_STATUS_ACCEPTED
    assert chat.status == "doing"
    assert any("no header" in (message.content or "") for message in chat.messages)


def test_only_the_target_reviews_a_delivery(tmp_path, target_project):
    settings, _root = target_project
    service, record = _company_ticket(settings)
    _finish(settings, record.ticket_id)

    other_root = tmp_path / "other"
    other = _project(other_root, "other", "other-id")
    other_service = TicketService(settings=other, user="intruder")

    with pytest.raises(TicketError):
        other_service.review(record.ticket_id, decision="accept")


# ---------------------------------------------------------------------------
# Company aggregation
# ---------------------------------------------------------------------------


class _FakeCompany:
    """Minimal company document used by CompanyTicketService."""

    def __init__(self, projects):
        self.id = "cmp-acme"
        self.name = "Acme"
        self.owner = "owner"
        self.projects = projects

    def project(self, project_id):
        return next(
            (project for project in self.projects if project.project_id == project_id),
            None,
        )


class _FakeSettings:
    def __init__(self, project_id, project_name):
        self.project_id = project_id
        self.project_name = project_name


class _FakeProjectRef:
    def __init__(self, project_id, name):
        self.project_id = project_id
        self.name = name
        self.role = ""


class _FakeTicketService:
    """Returns canned records for the project it is bound to."""

    def __init__(self, records, project_id):
        self.records = records
        self.project_id = project_id

    def query(self, **_kwargs):
        return self.records.get(self.project_id, [])

    def own_manager(self):
        return type("M", (), {"find_by_id": lambda self, _id: None})()


def test_company_listing_groups_and_deduplicates_project_queues():
    from codx.junior.company.tickets import CompanyTicketService
    from codx.junior.ticketing.model import TicketRecord

    company = _FakeCompany(
        [
            _FakeProjectRef("p1", "Platform"),
            _FakeProjectRef("p2", "Website"),
        ]
    )
    p1 = _FakeSettings("p1", "Platform")
    p2 = _FakeSettings("p2", "Website")

    class Manager:
        def get_company(self, _company_id):
            return company

        def visible_project_ids(self, _company):
            return ["p1", "p2"]

        def project_settings(self, project_id):
            return {"p1": p1, "p2": p2}.get(project_id)

        def _project_catalog(self):
            return [
                {"project_id": "p1", "project_name": "Platform"},
                {"project_id": "p2", "project_name": "Website"},
            ]

    records = {
        "p1": [
            TicketRecord(
                ticket_id="t-review",
                name="Review me",
                status="done",
                autonomy={"state": "passed", "reviewed_at": None},
            ),
            TicketRecord(
                ticket_id="t-blocked",
                name="Blocked",
                status="accepted",
                autonomy={"state": "blocked"},
            ),
        ],
        # The same ticket is visible from another linked project (a ticket
        # placed by p1 on p2 appears in both queues): the company lists it once.
        "p2": [
            TicketRecord(
                ticket_id="t-review",
                name="Review me",
                status="done",
                autonomy={"state": "passed", "reviewed_at": None},
            )
        ],
    }

    def factory(settings):
        return _FakeTicketService(records, settings.project_id)

    listing = CompanyTicketService(
        manager=Manager(), ticket_service_factory=factory
    ).list_tickets(company)

    assert [ticket["ticket_id"] for ticket in listing["tickets"]] == [
        "t-review",
        "t-blocked",
    ]
    assert listing["counts"]["all"] == 2
    assert listing["counts"]["review"] == 1
    assert listing["counts"]["blocked"] == 1
    assert {project["project_id"]: project["ticket_count"] for project in listing["projects"]} == {
        "p1": 2,
        "p2": 0,
    }


def test_the_company_ticket_tool_is_registered():
    """The assistant grants it through the all-tools marker (`*`)."""
    from codx.junior.tools import all_tool_names

    assert "company_ticket" in all_tool_names()


def test_a_non_member_cannot_read_or_place_company_tickets():
    from types import SimpleNamespace

    from codx.junior.company.tickets import CompanyTicketError, CompanyTicketService

    company = _FakeCompany([_FakeProjectRef("p1", "Platform")])
    p1 = _FakeSettings("p1", "Platform")

    class Manager:
        def get_company(self, _company_id):
            return company

        def visible_project_ids(self, _company):
            return ["p1"]

        def project_settings(self, project_id):
            return p1 if project_id == "p1" else None

        def _project_catalog(self):
            return [{"project_id": "p1", "project_name": "Platform"}]

    stranger = SimpleNamespace(username="stranger", role="user", companies=[])

    def factory(settings):
        return _FakeTicketService({}, settings.project_id)

    service = CompanyTicketService(
        manager=Manager(), user=stranger, ticket_service_factory=factory
    )
    with pytest.raises(CompanyTicketError, match="not a member"):
        service.list_tickets(company)
    with pytest.raises(CompanyTicketError, match="not a member"):
        service.create(company, {"project_id": "p1", "name": "x", "description": "y"})


# ---------------------------------------------------------------------------
# Company assistant chat
# ---------------------------------------------------------------------------


def test_assistant_chat_id_is_deterministic_and_filesystem_safe():
    from codx.junior.company.assistant_chat import assistant_chat_id

    assert assistant_chat_id("cmp-acme") == "assistant-cmp-acme"
    assert assistant_chat_id("cmp/acme:one") == "assistant-cmp-acme-one"
    assert assistant_chat_id("") == "assistant-company"


class _AssistantCompany:
    def __init__(self):
        self.id = "cmp-acme"
        self.name = "Acme"
        self.owner = "owner"
        self.teams = []
        self.missions = []
        self.description = ""
        self.mission = ""
        self.vision = ""

    def project(self, _project_id):
        return None


class _AssistantChatManager:
    def __init__(self):
        self.chat = None
        self.saves = 0

    def find_by_id(self, chat_id):
        if self.chat and self.chat.id == chat_id:
            return self.chat
        return None

    def save_chat(self, chat):
        self.saves += 1
        self.chat = chat
        return chat


class _AssistantManager:
    def __init__(self):
        self.chat_manager = _AssistantChatManager()
        self.company = _AssistantCompany()

    def get_company(self, _company_id):
        return self.company

    def visible_project_ids(self, _company):
        return ["p1"]

    def project_settings(self, project_id):
        return object() if project_id == "p1" else None

    def _chat_manager_for(self, _project_id):
        return self.chat_manager


def test_ensure_assistant_chat_provisions_one_codx_junior_chat():
    from codx.junior.company.assistant_chat import ensure_assistant_chat

    manager = _AssistantManager()
    first = ensure_assistant_chat(manager, "cmp-acme", actor="owner")

    assert first["created"] is True
    assert first["chat_id"] == "assistant-cmp-acme"
    assert first["project_id"] == "p1"
    chat = manager.chat_manager.chat
    assert chat is not None
    assert chat.profiles == ["codx-junior"]
    assert chat.mode == "chat"
    assert chat.meta_data["company_assistant"] is True
    assert chat.meta_data["company_id"] == "cmp-acme"
    assert chat.messages and chat.messages[0].role == "assistant"
    assert "codx-junior" in chat.messages[0].content

    # Idempotent: the same chat is reused, not duplicated.
    second = ensure_assistant_chat(manager, "cmp-acme", actor="owner")
    assert second["created"] is False
    assert second["chat_id"] == first["chat_id"]
    assert manager.chat_manager.saves == 1


def test_read_only_company_member_gets_investigation_tools_only(monkeypatch):
    from types import SimpleNamespace

    from codx.junior.company import assistant_chat

    manager = _AssistantManager()

    def company_role(_company_id):
        return "member"

    viewer = SimpleNamespace(username="viewer", role="user", company_role=company_role)
    monkeypatch.setattr(assistant_chat, "_manager", lambda: manager)

    allowed = assistant_chat.company_assistant_tool_allowlist("cmp-acme", viewer)

    assert allowed is not None
    assert "project_read_file" in allowed
    assert "ticket_query" in allowed
    assert "company_ticket" not in allowed
    assert "company_management" not in allowed
    assert "project_write_file" not in allowed


def test_company_manager_keeps_the_full_profile_tool_set(monkeypatch):
    from types import SimpleNamespace

    from codx.junior.company import assistant_chat

    manager = _AssistantManager()

    def company_role(_company_id):
        return "manager"

    boss = SimpleNamespace(username="boss", role="user", company_role=company_role)
    monkeypatch.setattr(assistant_chat, "_manager", lambda: manager)

    assert assistant_chat.company_assistant_tool_allowlist("cmp-acme", boss) is None
