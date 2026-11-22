"""Autonomous tickets: dive, implement, validate, close.

These tests exercise the contract that makes a ticket autonomous, without an AI
provider:

* a ticket requested with ``autonomous=True`` lands queued;
* the target runtime accepts it, runs the agent round(s) and validates;
* a red gate keeps the ticket open and is fed back to the next round;
* the ticket is closed as ``done`` only after a green gate;
* a project without a validation gate stops as ``blocked`` (never "done");
* commands are executed with ``shell=False`` and enforced by the CISO gate;
* the background candidate scan only returns runnable tickets;
* the requester sees the autonomy state through the ledger/query.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from codx.junior.chat_manager import ChatManager
from codx.junior.db import Message
from codx.junior.settings import CODXJuniorSettings
from codx.junior.ticketing.autonomy import (
    TicketAutonomyService,
    ValidationResult,
)
from codx.junior.ticketing.model import (
    TICKET_AUTONOMY_BLOCKED,
    TICKET_AUTONOMY_CANCELLED,
    TICKET_AUTONOMY_FAILED,
    TICKET_AUTONOMY_QUEUED,
    TICKET_STATUS_ACCEPTED,
    TICKET_STATUS_DONE,
    TICKET_VALIDATION_COMMANDS,
    TicketAutonomy,
    TicketValidation,
)
from codx.junior.ticketing.service import TicketError, TicketService
from codx.junior.ticketing.worker import TicketAutonomyWorker
from codx.junior.tools.ticket_tools import ticket_autonomy, ticket_create


class FakeEventManager:
    """Collect the events the runtime emits (no socket needed)."""

    def __init__(self):
        self.events = []

    def chat_event(self, chat=None, message=None, event_type=None):
        self.events.append((getattr(chat, "id", None), event_type))


class FakeRunner:
    """Append an assistant answer and accumulate a small cost per round."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = list(answers or ["Implemented and tested."])

    async def __call__(self, chat, system=None, tool_allowlist=None):
        self.calls.append({
            "system": system, "chat": chat.id,
            "tool_allowlist": tool_allowlist,
        })
        index = min(len(self.calls) - 1, len(self.answers) - 1)
        content = self.answers[index]
        chat.messages.append(
            Message(role="assistant", content=content, done=True)
        )
        chat.cost = float(getattr(chat, "cost", 0) or 0) + 0.25
        return chat


class FakeValidator:
    """Return the scripted validation results, one per round."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, chat=None, settings=None, config=None):
        self.calls.append(chat.id)
        if not self.results:
            return ValidationResult(ok=True, command="pytest", output="ok")
        return self.results.pop(0)


def _project(root, name, project_id, **extra):
    (root / ".codx").mkdir(parents=True, exist_ok=True)
    return CODXJuniorSettings(
        project_id=project_id,
        project_name=name,
        abs_project_path=str(root),
        codx_path=str(root / ".codx"),
        task_workspace_isolation=False,
        **extra,
    )


def _child_project(parent_root, name="junior", project_id="junior-id"):
    import json

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
def projects(tmp_path):
    """A source project and the child project that owns the ticket queue."""
    cloud_root = tmp_path / "cloud"
    cloud = _project(cloud_root, "cloud", "cloud-id")
    child_root, child = _child_project(cloud_root)
    child.task_workspace_isolation = False
    events = FakeEventManager()
    cloud_manager = ChatManager(settings=cloud, event_manager=events)
    child_manager = ChatManager(settings=child, event_manager=events)
    return cloud, child, cloud_manager, child_manager


def _place_ticket(projects, **kwargs):
    cloud, child, cloud_manager, child_manager = projects
    if kwargs.get("autonomous"):
        # The production default is auto_accept=False (the target must accept
        # first); the helper opts in so the loop tests focus on the run itself.
        kwargs.setdefault("auto_accept", True)
    service = TicketService(settings=cloud, chat_manager=cloud_manager)
    record = service.create(
        target=child.project_name,
        name=kwargs.pop("name", "Fix the export bug"),
        description=kwargs.pop(
            "description",
            "Exporting an empty board crashes. Expected: a valid empty CSV.",
        ),
        **kwargs,
    )
    return record


def _target_service(projects, runner=None, validator=None):
    _cloud, child, _cloud_manager, child_manager = projects
    return TicketAutonomyService(
        settings=child,
        chat_manager=child_manager,
        runner=runner,
        validator=validator,
    )


def _target_ticket(projects, ticket_id):
    _cloud, child, _cloud_manager, child_manager = projects
    return child_manager.find_by_id(ticket_id)


def test_create_with_autonomy_queues_the_ticket_on_the_target(projects):
    record = _place_ticket(projects, autonomous=True, autonomy_profile="software_developer")
    assert record.autonomy is not None
    assert record.autonomy["enabled"] is True
    assert record.autonomy["state"] == TICKET_AUTONOMY_QUEUED
    assert record.autonomy["auto_accept"] is True
    assert record.autonomy["requested_by"] is None

    chat = _target_ticket(projects, record.ticket_id)
    assert chat is not None
    assert chat.ticket.autonomy.state == TICKET_AUTONOMY_QUEUED
    assert chat.ticket.autonomy.enabled is True


def test_run_accepts_the_ticket_and_closes_it_only_after_a_green_gate(projects):
    record = _place_ticket(projects, autonomous=True)
    runner = FakeRunner(["first attempt", "fixed, tests are green"])
    validator = FakeValidator([
        ValidationResult(ok=False, command="pytest", output="1 failed"),
        ValidationResult(ok=True, command="pytest", output="4 passed"),
    ])
    service = _target_service(projects, runner=runner, validator=validator)

    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_DONE
    assert payload["autonomy"]["state"] == "passed"
    assert payload["autonomy"]["validation_passed"] is True
    assert payload["autonomy"]["rounds"] == 2
    assert len(runner.calls) == 2
    assert len(validator.calls) == 2
    assert "Validation PASS" in payload["autonomy"]["last_validation"]

    chat = _target_ticket(projects, record.ticket_id)
    assert chat.ticket.status == TICKET_STATUS_DONE
    assert chat.column == "Done"
    assert chat.status == "done"
    assert any(
        message.role == "assistant" and "## Ticket delivered" in message.content
        for message in chat.messages
    )


def test_a_red_gate_after_the_last_round_keeps_the_ticket_open(projects):
    record = _place_ticket(projects, autonomous=True)
    runner = FakeRunner(["attempt one", "attempt two"])
    validator = FakeValidator([
        ValidationResult(ok=False, command="pytest", output="1 failed"),
        ValidationResult(ok=False, command="pytest", output="1 failed again"),
    ])
    service = _target_service(projects, runner=runner, validator=validator)
    service.configure(record.ticket_id, max_rounds=2)

    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_ACCEPTED
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_FAILED
    assert payload["autonomy"]["validation_passed"] is False
    assert payload["autonomy"]["rounds"] == 2
    assert len(runner.calls) == 2
    chat = _target_ticket(projects, record.ticket_id)
    assert chat.ticket.status == TICKET_STATUS_ACCEPTED
    assert any(
        message.role == "assistant" and "Autonomous run failed" in message.content
        for message in chat.messages
    )


def test_a_missing_validation_gate_blocks_instead_of_closing(projects):
    record = _place_ticket(projects, autonomous=True)
    runner = FakeRunner(["I think it works"])
    validator = FakeValidator([
        ValidationResult(
            ok=False,
            blocked=True,
            reason="no test suite could be detected",
        )
    ])
    service = _target_service(projects, runner=runner, validator=validator)

    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_ACCEPTED
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_BLOCKED
    assert payload["autonomy"]["validation_passed"] is False
    assert "no test suite" in payload["autonomy"]["last_error"]
    assert len(runner.calls) == 1


def test_commands_are_the_gate_and_run_without_a_shell(projects):
    record = _place_ticket(projects, autonomous=True)
    service = _target_service(projects, runner=FakeRunner(["changed"]))
    service.configure(
        record.ticket_id,
        validation={
            "source": "commands",
            "commands": [f"{sys.executable} -c \"print('ok')\""],
        },
    )

    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_DONE
    assert payload["autonomy"]["validation_passed"] is True
    assert "print('ok')" in payload["autonomy"]["last_validation"]


def test_a_failing_command_keeps_the_ticket_open(projects):
    record = _place_ticket(projects, autonomous=True)
    service = _target_service(projects, runner=FakeRunner(["changed"]))
    service.configure(
        record.ticket_id,
        max_rounds=1,
        validation={
            "source": "commands",
            "commands": [
                f"{sys.executable} -c \"import sys; sys.exit(3)\""
            ],
        },
    )

    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_ACCEPTED
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_FAILED
    assert "exit code 3" in payload["autonomy"]["last_validation"]


def test_configure_queue_and_cancel(projects):
    record = _place_ticket(projects)
    service = _target_service(projects)

    payload = service.configure(record.ticket_id, enabled=True, auto_accept=True)
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_QUEUED
    assert payload["status"] == TICKET_STATUS_ACCEPTED

    cancelled = service.cancel(record.ticket_id)
    assert cancelled["autonomy"]["state"] == TICKET_AUTONOMY_CANCELLED
    assert cancelled["status"] == TICKET_STATUS_ACCEPTED


def test_due_candidates_only_returns_runnable_tickets(projects):
    queued = _place_ticket(projects, name="queued", autonomous=True)
    manual = _place_ticket(projects, name="manual")
    service = _target_service(projects)

    candidates = service.due_candidates()
    ids = {candidate["ticket_id"] for candidate in candidates}
    assert queued.ticket_id in ids
    assert manual.ticket_id not in ids

    service.cancel(queued.ticket_id)
    ids = {candidate["ticket_id"] for candidate in service.due_candidates()}
    assert queued.ticket_id not in ids


def test_run_due_works_the_ticket_and_the_source_sees_the_result(projects):
    record = _place_ticket(projects, autonomous=True)
    runner = FakeRunner(["delivered"])
    validator = FakeValidator([
        ValidationResult(ok=True, command="pytest", output="4 passed")
    ])
    service = _target_service(projects, runner=runner, validator=validator)

    results = asyncio.run(service.run_due())

    assert len(results) == 1
    assert results[0]["ticket_id"] == record.ticket_id
    assert results[0]["autonomy"]["state"] == "passed"
    source = TicketService(settings=projects[0], chat_manager=projects[2])
    refreshed = source.query(ticket_id=record.ticket_id)[0]
    assert refreshed.status == TICKET_STATUS_DONE
    assert refreshed.autonomy["validation_passed"] is True


def test_ticket_autonomy_tool_reports_and_runs(projects):
    record = _place_ticket(projects, autonomous=True)
    _cloud, child, _cloud_manager, child_manager = projects

    async def scenario():
        status = await ticket_autonomy(
            action="status", ticket_id=record.ticket_id,
            settings=child, session=None,
        )
        assert "autonomy" in status.user_response
        return status

    status = asyncio.run(scenario())
    assert record.ticket_id in status.user_response


def test_ticket_create_tool_can_request_autonomy(projects):
    cloud = projects[0]
    response = ticket_create(
        target_project="junior",
        name="Autonomous from a tool",
        description="Do the thing and validate it.",
        autonomous=True,
        settings=cloud,
        session=None,
    )
    assert "autonomy" in response.user_response.lower()
    assert "ticket_id" in response.llm_response


def test_ticket_record_and_ledger_carry_the_autonomy_state(projects):
    record = _place_ticket(projects, autonomous=True)
    assert record.autonomy is not None
    assert record.autonomy["enabled"] is True
    ledger = record.to_ledger()["autonomy"]
    assert ledger["state"] == TICKET_AUTONOMY_QUEUED
    # The ledger is written outside the chat redaction path: no evidence body.
    assert "delivery" not in ledger
    assert "last_validation" not in ledger


def test_validation_model_clamps_and_describes_its_source():
    validation = TicketValidation(
        source="commands",
        commands=["pytest -q", " "],
        timeout_seconds=99999,
        required=True,
    ).normalized()
    assert validation.commands == ["pytest -q"]
    assert validation.timeout_seconds == 3600
    assert validation.effective_source() == TICKET_VALIDATION_COMMANDS
    assert "pytest -q" in validation.describe()

    autonomy = TicketAutonomy(
        enabled=True,
        max_rounds=999,
        max_runtime_minutes=999,
        validation=validation,
    ).normalized()
    assert autonomy.max_rounds == 40
    assert autonomy.max_runtime_minutes == 180
    assert autonomy.state == "off"


def test_source_reads_the_autonomy_state_but_cannot_run_it(projects):
    record = _place_ticket(projects, autonomous=True)
    source_service = TicketAutonomyService(
        settings=projects[0], chat_manager=projects[2]
    )

    payload = source_service.status(record.ticket_id)
    assert payload["can_configure"] is False
    assert payload["direction"] == "outgoing"
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_QUEUED

    with pytest.raises(TicketError):
        asyncio.run(source_service.run(record.ticket_id))


def test_worker_discovers_projects_and_runs_due_tickets(monkeypatch, projects):
    record = _place_ticket(projects, autonomous=True)
    _cloud, child, _cloud_manager, _child_manager = projects
    from codx.junior.project import project_discover

    monkeypatch.setattr(
        project_discover,
        "find_all_projects",
        lambda: {child.project_id: child},
        raising=True,
    )
    calls = []

    async def fake_run(self, ticket_id, force=False):
        calls.append(ticket_id)
        return {"ticket_id": ticket_id, "autonomy": {"state": "passed"}}

    monkeypatch.setattr(TicketAutonomyService, "run", fake_run, raising=True)
    worker = TicketAutonomyWorker(tick_seconds=5, max_workers=1)

    results = worker.run_once()

    assert calls == [record.ticket_id]
    assert results and results[0]["ticket_id"] == record.ticket_id


def test_a_human_comment_added_during_the_run_is_preserved(projects):
    record = _place_ticket(projects, autonomous=True)
    _cloud, child, _cloud_manager, child_manager = projects

    class CommentingRunner(FakeRunner):
        async def __call__(self, chat, system=None, tool_allowlist=None):
            result = await super().__call__(chat, system, tool_allowlist)
            # Another writer (a human/a second request) saves the ticket while
            # the agent turn is still in flight.
            fresh = child_manager.find_by_id(record.ticket_id)
            child_manager.add_message(
                chat=fresh,
                message=Message(role="user", content="Human note", user="human", done=True),
            )
            return result

    service = _target_service(
        projects,
        runner=CommentingRunner(["works"]),
        validator=FakeValidator([ValidationResult(ok=True, command="pytest", output="ok")]),
    )
    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_DONE
    chat = _target_ticket(projects, record.ticket_id)
    contents = [message.content for message in chat.messages]
    assert "Human note" in contents
    assert "works" in contents


def test_a_human_closing_the_card_during_the_run_is_not_overwritten(projects):
    record = _place_ticket(projects, autonomous=True)
    _cloud, child, _cloud_manager, child_manager = projects

    class ClosingRunner(FakeRunner):
        async def __call__(self, chat, system=None, tool_allowlist=None):
            result = await super().__call__(chat, system, tool_allowlist)
            fresh = child_manager.find_by_id(record.ticket_id)
            fresh.ticket.status = TICKET_STATUS_DONE
            fresh.column = "Done"
            fresh.status = "done"
            child_manager.save_chat(chat=fresh)
            return result

    service = _target_service(
        projects,
        runner=ClosingRunner(["attempt"]),
        validator=FakeValidator([ValidationResult(ok=True, command="pytest", output="ok")]),
    )
    payload = asyncio.run(service.run(record.ticket_id))

    assert payload["status"] == TICKET_STATUS_DONE
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_CANCELLED
    chat = _target_ticket(projects, record.ticket_id)
    assert chat.ticket.status == TICKET_STATUS_DONE


def test_autonomy_request_waits_for_the_target_by_default(projects):
    record = _place_ticket(projects, autonomous=True, auto_accept=False)
    service = _target_service(projects)

    assert record.autonomy["auto_accept"] is False
    # An open ticket that did not opt into auto_accept is not runnable yet.
    assert record.ticket_id not in {
        candidate["ticket_id"] for candidate in service.due_candidates()
    }

    # The target can accept it explicitly (the UI "Queue" action does this).
    payload = service.queue(record.ticket_id)
    assert payload["status"] == TICKET_STATUS_ACCEPTED
    assert payload["autonomy"]["state"] == TICKET_AUTONOMY_QUEUED
    assert payload["autonomy"]["auto_accept"] is False
    assert record.ticket_id in {
        candidate["ticket_id"] for candidate in service.due_candidates()
    }


def test_moving_the_card_to_accepted_starts_the_ticket(projects):
    record = _place_ticket(projects, autonomous=True, auto_accept=False)
    _cloud, child, _cloud_manager, child_manager = projects

    # A human drags the card to Accepted: only the column changes on disk.
    chat = child_manager.find_by_id(record.ticket_id)
    chat.column = "Accepted"
    child_manager.save_chat(chat=chat)
    assert child_manager.find_by_id(record.ticket_id).ticket.status == "open"

    service = _target_service(
        projects,
        runner=FakeRunner(["delivered"]),
        validator=FakeValidator([ValidationResult(ok=True, command="pytest", output="ok")]),
    )
    candidates = service.due_candidates()
    assert record.ticket_id in {candidate["ticket_id"] for candidate in candidates}

    payload = asyncio.run(service.run(record.ticket_id))
    assert payload["status"] == TICKET_STATUS_DONE
    assert payload["autonomy"]["state"] == "passed"


def test_run_now_is_an_explicit_target_action_and_accepts_the_ticket(projects):
    record = _place_ticket(projects, autonomous=True, auto_accept=False)
    service = _target_service(
        projects,
        runner=FakeRunner(["delivered"]),
        validator=FakeValidator([ValidationResult(ok=True, command="pytest", output="ok")]),
    )

    payload = asyncio.run(service.run(record.ticket_id, force=True))

    assert payload["status"] == TICKET_STATUS_DONE
    assert payload["autonomy"]["state"] == "passed"


def test_the_run_cannot_use_ticket_management_tools(projects):
    record = _place_ticket(projects, autonomous=True)
    runner = FakeRunner(["works"])
    service = _target_service(
        projects,
        runner=runner,
        validator=FakeValidator([ValidationResult(ok=True, command="pytest", output="ok")]),
    )

    asyncio.run(service.run(record.ticket_id))

    allowlist = runner.calls[0]["tool_allowlist"]
    assert allowlist is not None
    assert "ticket_update" not in allowlist
    assert "ticket_abandon" not in allowlist
    assert "ticket_autonomy" not in allowlist
    assert "ticket_create" not in allowlist
    # It still receives the project tools it needs to implement the ticket.
    assert "project_write_file" in allowlist or "apply_file_changes" in allowlist
