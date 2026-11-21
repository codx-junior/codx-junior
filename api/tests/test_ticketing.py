"""Cross-project ticketing: a project requests a change on another project.

A ticket is a **chat** placed by one project on a child project or on a linked
project with read/write permission. The chat is stored by the TARGET project on
its ``tickets`` board (so the target owns the queue) and the requester keeps a
ledger entry (``.codx/tickets/tickets.json``) with the ticket id.

These tests cover the rules the service enforces, which are the reason the
feature exists:

* the ticket lands in the TARGET project, on the ``tickets`` board;
* the target decides ``accepted`` / ``declined`` / ``done``, never the requester;
* the requester comments and abandons, never the target;
* moving the card on the board (the human way) IS a status change;
* a read-only linked project cannot receive tickets, a read/write one can.
"""

import json

import pytest

from codx.junior.chat_manager import ChatManager
from codx.junior.db import Chat
from codx.junior.settings import CODXJuniorSettings
from codx.junior.ticketing.model import (
    ChatTicket,
    TICKET_BOARD,
    TICKET_STATUS_ABANDONED,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DECLINED,
    TICKET_STATUS_DONE,
    TICKET_STATUS_OPEN,
    column_for_status,
    ticket_columns,
)
from codx.junior.ticketing.service import TicketError, TicketService
from codx.junior.tools.ticket_tools import (
    ticket_abandon,
    ticket_create,
    ticket_query,
    ticket_update,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _project(root, name, project_id, **extra):
    """
    Build the settings of a project rooted at *root*.

    Args:
        root: Project folder.
        name: Project name.
        project_id: Project id (tickets compare ids, never names).
        **extra: Extra settings (dependency lists...).

    Returns:
        The project settings.
    """
    (root / ".codx").mkdir(parents=True, exist_ok=True)
    return CODXJuniorSettings(
        project_id=project_id,
        project_name=name,
        abs_project_path=str(root),
        codx_path=str(root / ".codx"),
        **extra,
    )


def _child_project(parent_root, name="junior", project_id="junior-id"):
    """
    Create a child project nested inside *parent_root*.

    ``CODXJuniorSettings.get_sub_projects`` discovers children through their
    ``.codx/project.json``, so the file is what makes the folder a project.

    Args:
        parent_root: Folder of the parent project.
        name: Child project name.
        project_id: Child project id.

    Returns:
        ``(child_root, child_settings)``.
    """
    child_root = parent_root / "plugins" / name
    (child_root / ".codx").mkdir(parents=True, exist_ok=True)
    (child_root / ".codx" / "project.json").write_text(
        json.dumps({"project_id": project_id, "project_name": name}),
        encoding="utf-8",
    )
    return child_root, CODXJuniorSettings.from_project_file(
        str(child_root / ".codx" / "project.json")
    )


@pytest.fixture
def cloud_and_child(tmp_path):
    """
    A SaaS platform project (``cloud``) and the child project it drives.

    Returns:
        ``(cloud_settings, cloud_root, child_settings, child_root)``.
    """
    cloud_root = tmp_path / "cloud"
    cloud = _project(cloud_root, "cloud", "cloud-id")
    child_root, child = _child_project(cloud_root)
    return cloud, cloud_root, child, child_root


def _linked_project(parent_root, name="linked"):
    """
    Create a project next to *parent_root* (a sibling folder).

    A linked project is a name in ``project_dependencies`` /
    ``read_write_project_dependencies``; it resolves on disk through its
    ``.codx/project.json`` file.

    Args:
        parent_root: Folder of the project declaring the link.
        name: Folder (and project) name of the linked project.

    Returns:
        The linked project root.
    """
    linked_root = parent_root.parent / name
    (linked_root / ".codx").mkdir(parents=True, exist_ok=True)
    (linked_root / ".codx" / "project.json").write_text(
        json.dumps({"project_id": f"{name}-id", "project_name": name}),
        encoding="utf-8",
    )
    return linked_root


def _ticket_files(root):
    """Return the ticket chat files stored in *root* (any column)."""
    tasks = root / ".codx" / "tasks" / TICKET_BOARD
    return sorted(tasks.rglob("*.json")) if tasks.is_dir() else []


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_create_stores_the_ticket_on_the_target_tickets_board(cloud_and_child):
    cloud, cloud_root, child, child_root = cloud_and_child
    service = TicketService(settings=cloud, user="cloud-agent")

    record = service.create(
        target="junior",
        name="Login times out after 30s",
        description="## Context\nSessions expire too early.\n## Done\n- 2h timeout",
        kind="bug",
        priority="high",
        reference="INC-42",
    )

    assert record.ticket_id == record.chat_id
    assert record.status == TICKET_STATUS_OPEN
    assert record.kind == "bug"
    assert record.source_project_id == "cloud-id"
    assert record.target_project_id == "junior-id"
    assert record.board == TICKET_BOARD
    assert record.column == "Inbox"

    files = _ticket_files(child_root)
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["id"] == record.ticket_id
    assert stored["mode"] == "ticket"
    assert stored["owner_project_id"] == "junior-id"
    assert stored["project_id"] == "junior-id"
    assert stored["ticket"]["source_project_id"] == "cloud-id"
    assert stored["ticket"]["reference"] == "INC-42"
    assert stored["ticket"]["history"][0]["action"] == "placed"
    # The request itself is the first message of the conversation.
    assert stored["messages"][0]["content"].startswith("## Context")

    # The ticket did NOT leak into the requester's own task tree.
    assert _ticket_files(cloud_root) == []


def test_create_writes_the_requester_ledger_and_returns_a_queryable_id(cloud_and_child):
    cloud, cloud_root, _child, _child_root = cloud_and_child
    service = TicketService(settings=cloud, user="cloud-agent")

    record = service.create(target="junior", name="Add SSO", description="SAML login")

    ledger = json.loads(
        (cloud_root / ".codx" / "tickets" / "tickets.json").read_text(encoding="utf-8")
    )
    assert [entry["ticket_id"] for entry in ledger["tickets"]] == [record.ticket_id]
    assert ledger["tickets"][0]["target_project_id"] == "junior-id"

    assert [r.ticket_id for r in service.query(ticket_id=record.ticket_id)] == [
        record.ticket_id
    ]


def test_the_ticket_board_is_created_with_the_status_columns(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    service = TicketService(settings=cloud)

    service.create(target="junior", name="One", description="first")
    service.create(target="junior", name="Two", description="second")

    kanban = ChatManager(settings=child).kanban_store.load()
    boards = {str(title).lower(): entry for title, entry in kanban["boards"].items()}
    assert TICKET_BOARD in boards
    titles = [column["title"] for column in boards[TICKET_BOARD]["columns"]]
    assert titles == ticket_columns()
    # Creating a second ticket does not duplicate the board.
    assert len(_ticket_files(_child_root)) == 2


def test_a_project_cannot_place_a_ticket_on_itself(cloud_and_child):
    cloud, _cloud_root, _child, _child_root = cloud_and_child
    service = TicketService(settings=cloud)

    with pytest.raises(TicketError, match="on itself"):
        service.create(target="cloud", name="nope", description="nope")


def test_an_unknown_target_lists_the_valid_ones(cloud_and_child):
    cloud, _cloud_root, _child, _child_root = cloud_and_child
    service = TicketService(settings=cloud)

    with pytest.raises(TicketError, match="unknown ticket target 'nope'"):
        service.create(target="nope", name="x", description="y")

    targets = service.targets()
    assert [target["project_name"] for target in targets] == ["junior"]
    assert targets[0]["relationship"] == "child"


def test_a_read_only_linked_project_cannot_receive_tickets(tmp_path):
    cloud_root = tmp_path / "cloud"
    _linked_project(cloud_root)
    cloud = _project(cloud_root, "cloud", "cloud-id", read_project_dependencies="linked")

    service = TicketService(settings=cloud)
    assert service.targets() == []
    with pytest.raises(TicketError, match="unknown ticket target 'linked'"):
        service.create(target="linked", name="x", description="y")


def test_a_read_write_linked_project_can_receive_tickets(tmp_path):
    cloud_root = tmp_path / "cloud"
    linked_root = _linked_project(cloud_root)
    cloud = _project(
        cloud_root, "cloud", "cloud-id", read_write_project_dependencies="linked"
    )

    service = TicketService(settings=cloud)
    assert [target["relationship"] for target in service.targets()] == ["linked"]

    record = service.create(target="linked", name="Needs a flag", description="--debug")
    assert record.target_project_name == "linked"
    assert len(_ticket_files(linked_root)) == 1


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def test_query_lists_the_ticket_from_both_sides(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)

    record = source.create(target="junior", name="Fix login", description="timeout")

    outgoing = source.query(direction="outgoing")
    incoming = target.query(direction="incoming")

    assert [(r.ticket_id, r.direction) for r in outgoing] == [
        (record.ticket_id, "outgoing")
    ]
    assert [(r.ticket_id, r.direction) for r in incoming] == [
        (record.ticket_id, "incoming")
    ]
    assert outgoing[0].available is True
    assert incoming[0].source_project_name == "cloud"


def test_query_filters_by_status_and_limit(cloud_and_child):
    cloud, _cloud_root, _child, _child_root = cloud_and_child
    service = TicketService(settings=cloud)

    first = service.create(target="junior", name="One", description="1")
    service.create(target="junior", name="Two", description="2")

    assert len(service.query(direction="outgoing", limit=1)) == 1
    assert [r.ticket_id for r in service.query(status=TICKET_STATUS_OPEN)] != []
    assert service.query(status=TICKET_STATUS_DONE) == []

    ledger = service.ledger_entry(first.ticket_id)
    assert ledger is not None and ledger["name"] == "One"


# ---------------------------------------------------------------------------
# Ownership of the decisions
# ---------------------------------------------------------------------------


def test_the_target_accepts_and_the_requester_sees_it(cloud_and_child):
    cloud, _cloud_root, child, child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)

    record = source.create(target="junior", name="Fix login", description="timeout")
    updated = target.update(
        ticket_id=record.ticket_id, status=TICKET_STATUS_ACCEPTED, comment="on it"
    )

    assert updated.status == TICKET_STATUS_ACCEPTED
    assert updated.column == "Accepted"
    # The card really moved on the target's board.
    assert "Accepted" in str(_ticket_files(child_root)[0])
    assert len(_ticket_files(child_root)) == 1

    seen = source.query(ticket_id=record.ticket_id)[0]
    assert seen.status == TICKET_STATUS_ACCEPTED
    assert seen.column == "Accepted"


def test_the_requester_cannot_accept_its_own_ticket(cloud_and_child):
    cloud, _cloud_root, _child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    record = source.create(target="junior", name="Fix login", description="timeout")

    with pytest.raises(TicketError, match="only the target project"):
        source.update(ticket_id=record.ticket_id, status=TICKET_STATUS_ACCEPTED)


def test_the_target_closes_the_ticket_and_done_is_final(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)
    record = source.create(target="junior", name="Fix login", description="timeout")

    closed = target.update(ticket_id=record.ticket_id, status=TICKET_STATUS_DONE)
    assert closed.status == TICKET_STATUS_DONE

    with pytest.raises(TicketError, match="can no longer change"):
        target.update(ticket_id=record.ticket_id, status=TICKET_STATUS_ACCEPTED)


def test_an_invalid_transition_is_refused(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)
    record = source.create(target="junior", name="Fix login", description="timeout")

    target.update(ticket_id=record.ticket_id, status=TICKET_STATUS_DECLINED)
    with pytest.raises(TicketError, match="cannot move from 'declined' to 'done'"):
        target.update(ticket_id=record.ticket_id, status=TICKET_STATUS_DONE)


def test_the_requester_abandons_and_the_target_cannot(cloud_and_child):
    cloud, _cloud_root, child, child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)
    record = source.create(target="junior", name="Fix login", description="timeout")

    abandoned = source.abandon(ticket_id=record.ticket_id, reason="fixed upstream")
    assert abandoned.status == TICKET_STATUS_ABANDONED
    assert abandoned.column == "Abandoned"
    assert "Abandoned" in str(_ticket_files(child_root)[0])

    seen = target.query(ticket_id=record.ticket_id)[0]
    assert seen.status == TICKET_STATUS_ABANDONED
    assert seen.last_message and "abandoned" in seen.last_message.lower()

    with pytest.raises(TicketError, match="only the requesting project"):
        target.abandon(ticket_id=record.ticket_id)


def test_abandon_is_refused_on_a_closed_ticket(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)
    record = source.create(target="junior", name="Fix login", description="timeout")
    target.update(ticket_id=record.ticket_id, status=TICKET_STATUS_DONE)

    with pytest.raises(TicketError, match="nothing to abandon"):
        source.abandon(ticket_id=record.ticket_id)


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


def test_both_projects_comment_in_the_same_conversation(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)
    record = source.create(target="junior", name="Fix login", description="timeout")

    source.update(ticket_id=record.ticket_id, comment="It happens on 1.4.0 only")
    target.update(ticket_id=record.ticket_id, comment="Reproduced, thanks")

    seen = target.query(ticket_id=record.ticket_id)[0]
    assert seen.message_count == 3
    assert seen.last_message == "Reproduced, thanks"

    chat = ChatManager(settings=child).find_by_id(record.ticket_id)
    assert [message.content for message in chat.messages[1:]] == [
        "It happens on 1.4.0 only",
        "Reproduced, thanks",
    ]
    actions = [event.action for event in chat.ticket.history]
    assert actions == ["placed", "commented", "commented"]


def test_an_update_without_changes_is_refused(cloud_and_child):
    cloud, _cloud_root, _child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    record = source.create(target="junior", name="Fix login", description="timeout")

    with pytest.raises(TicketError, match="nothing to update"):
        source.update(ticket_id=record.ticket_id)


def test_moving_the_card_on_the_board_changes_the_status(cloud_and_child):
    """The kanban column is the visible state: a human drag is a status change."""
    cloud, _cloud_root, child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)
    record = source.create(target="junior", name="Fix login", description="timeout")

    manager = ChatManager(settings=child)
    chat = manager.find_by_id(record.ticket_id)
    chat.column = column_for_status(TICKET_STATUS_ACCEPTED)
    manager.save_chat(chat=chat)

    seen = source.query(ticket_id=record.ticket_id)[0]
    assert seen.status == TICKET_STATUS_ACCEPTED
    assert seen.column == "Accepted"


def test_an_unknown_ticket_is_reported(cloud_and_child):
    cloud, _cloud_root, _child, _child_root = cloud_and_child
    source = TicketService(settings=cloud)

    assert source.query(ticket_id="does-not-exist") == []
    with pytest.raises(TicketError, match="not found"):
        source.update(ticket_id="does-not-exist", comment="hello")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def test_chat_round_trips_the_ticket_payload():
    ticket = ChatTicket(
        id="t-1",
        source_project_id="cloud-id",
        source_project_name="cloud",
        target_project_id="junior-id",
        target_project_name="junior",
        reference="INC-42",
    )
    ticket.append_event(action="placed", project_id="cloud-id", status=TICKET_STATUS_OPEN)

    payload = Chat(name="Fix login", ticket=ticket).model_dump()
    assert payload["ticket"]["source_project_id"] == "cloud-id"
    assert payload["ticket"]["history"][0]["action"] == "placed"

    restored = Chat(**payload)
    assert restored.ticket.status == TICKET_STATUS_OPEN
    assert restored.ticket.side_of("junior-id") == "incoming"
    assert restored.ticket.side_of("cloud-id") == "outgoing"
    assert restored.ticket.side_of("other-id") is None


def test_the_kanban_status_and_the_ticket_status_are_separate(cloud_and_child):
    cloud, _cloud_root, child, child_root = cloud_and_child
    source = TicketService(settings=cloud)
    target = TicketService(settings=child)
    record = source.create(target="junior", name="Fix login", description="timeout")

    target.update(ticket_id=record.ticket_id, status=TICKET_STATUS_ACCEPTED)
    chat = ChatManager(settings=child).find_by_id(record.ticket_id)
    assert chat.ticket.status == TICKET_STATUS_ACCEPTED
    store = json.loads(_ticket_files(child_root)[0].read_text(encoding="utf-8"))
    assert store["status"] == "todo"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_ticket_tools_place_and_follow_a_ticket(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child

    created = ticket_create(
        target_project="junior",
        name="Add a --json flag",
        description="The CLI has no machine readable output.",
        kind="feature",
        settings=cloud,
    )
    payload = json.loads(created.llm_response)
    ticket_id = payload["ticket_id"]
    assert payload["status"] == "created"
    assert ticket_id in created.user_response

    followed = ticket_query(ticket_id=ticket_id, settings=cloud)
    assert ticket_id in followed.user_response
    assert json.loads(followed.llm_response)["tickets"][0]["status"] == TICKET_STATUS_OPEN

    answered = ticket_update(
        ticket_id=ticket_id,
        comment="Useful for the CI pipeline",
        settings=cloud,
    )
    assert "updated" in answered.llm_response

    accepted = ticket_update(
        ticket_id=ticket_id, status=TICKET_STATUS_ACCEPTED, settings=child
    )
    assert json.loads(accepted.llm_response)["ticket"]["status"] == TICKET_STATUS_ACCEPTED

    withdrawn = ticket_abandon(
        ticket_id=ticket_id, reason="solved in the platform", settings=cloud
    )
    assert json.loads(withdrawn.llm_response)["status"] == "abandoned"

    listing = ticket_query(direction="incoming", settings=child)
    assert ticket_id in listing.user_response


def test_ticket_tools_report_the_ownership_rules(cloud_and_child):
    cloud, _cloud_root, child, _child_root = cloud_and_child
    created = ticket_create(
        target_project="junior", name="Fix login", description="timeout", settings=cloud
    )
    ticket_id = json.loads(created.llm_response)["ticket_id"]

    with pytest.raises(ValueError, match="only the target project"):
        ticket_update(
            ticket_id=ticket_id, status=TICKET_STATUS_ACCEPTED, settings=cloud
        )

    with pytest.raises(ValueError, match="only the requesting project"):
        ticket_abandon(ticket_id=ticket_id, settings=child)
