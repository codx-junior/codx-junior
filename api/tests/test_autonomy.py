"""
Offline tests of the actor runtime.

These tests exercise the parts that used to fail in production without an AI
provider:

* the shift never starts when the daily budget is already exhausted,
* a single turn cannot silently overspend the shift budget,
* a failed first turn still closes its shift card,
* agenda wakes survive failure and dry runs and are only consumed for closed
  tasks,
* disabled actors remain visible in the catalogue but never become due,
* a long shift is not immediately due again,
* board-only actors get a structural tool allowlist, and
* generated autonomous teams start with a budget.

Run with::

    cd api && pytest tests/test_autonomy.py -q
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from codx.junior.autonomy import constants as C
from codx.junior.autonomy.models import (
    ActorConfig,
    ActorState,
    ScheduleWindow,
    WeeklySchedule,
)
from codx.junior.autonomy.service import ActorService


class _Settings:
    """Minimal settings object accepted by ActorService."""

    codx_path = "/tmp/codx-junior-actor-tests"
    project_id = "project-1"
    project_name = "Project"
    project_path = "/tmp"


class _Chat:
    """The subset of Chat the runtime touches."""

    def __init__(self, chat_id: str, *, wake_at: str = "", done: bool = False):
        self.id = chat_id
        self.name = chat_id
        self.description = ""
        self.board = "Work"
        self.column = "Todo"
        self.status = "done" if done else "todo"
        self.done = done
        self.wake_at = wake_at
        self.progress = 0
        self.messages: List[Any] = []


class _Manager:
    """In-memory ChatManager."""

    def __init__(self, chats: Optional[List[_Chat]] = None):
        self.chats = {chat.id: chat for chat in (chats or [])}
        self.saved: List[str] = []

    def list_chats(self):
        return list(self.chats.values())

    def find_by_id(self, chat_id: str):
        return self.chats.get(chat_id)

    def save_chat(self, chat: Any, **_: Any):
        self.chats[chat.id] = chat
        self.saved.append(chat.id)
        return chat

    def add_message(self, chat: Any, message: Any):
        chat.messages.append(message)
        return chat


class _Store:
    """Minimal state store for status tests."""

    def __init__(self, states: Optional[Dict[str, ActorState]] = None):
        self.states = states or {}

    def load(self):
        return {
            "version": 1,
            "agents": {name: state.model_dump() for name, state in self.states.items()},
        }

    def get_state(self, name: str) -> ActorState:
        return self.states.get(name, ActorState(actor=name))

    def put_state(self, name: str, state: ActorState) -> ActorState:
        self.states[name] = state
        return state

    def worker_present(self) -> bool:
        return True


def _service(
    *,
    manager: Optional[_Manager] = None,
    store: Optional[_Store] = None,
) -> ActorService:
    service = ActorService(settings=_Settings(), store=store or _Store())
    if manager is not None:
        service.chat_manager = manager
    return service


def _profile(name: str = "actor", enabled: bool = True):
    return SimpleNamespace(
        name=name,
        content="Do the job.",
        description="An actor",
        avatar="",
        profiles=[],
        llm_model="",
        actor={"enabled": enabled},
    )


def _execute_config(**overrides: Any) -> ActorConfig:
    values = {
        "enabled": True,
        "role": C.ROLE_COWORKER,
        "session": C.SESSION_SINGLE,
        "budget_cxjcoins": 1.0,
        "daily_budget_cxjcoins": 5.0,
    }
    values.update(overrides)
    return ActorConfig(**values).normalized()


def test_daily_budget_exhausted_prevents_the_first_turn():
    """An actor over its daily budget must not pay for even one model turn."""

    calls: List[str] = []

    async def active_turn(**_: Any):
        calls.append("turn")
        return "should not run", 0.0, _Chat("shift")

    service = _service()
    service._active_turn = active_turn  # type: ignore[assignment]
    state = ActorState(
        actor="actor",
        day=datetime.now(timezone.utc).date().isoformat(),
        day_cost=5.0,
    )
    config = _execute_config(daily_budget_cxjcoins=5.0)
    report = asyncio.run(
        service._execute(
            name="actor",
            profile=_profile(),
            config=config,
            trigger="test",
            state=state,
        )
    )

    assert calls == []
    assert report.turns == 0
    assert report.status == C.STATE_BUDGET
    assert report.stop_reason == C.STOP_DAILY_BUDGET


def test_global_worker_heartbeat_is_visible_before_the_first_project_scan():
    """The UI must not say "worker offline" while the worker is still scanning."""

    from codx.junior.autonomy.store import (
        global_worker_present,
        write_global_worker_heartbeat,
    )

    write_global_worker_heartbeat({"state": "scanning"})
    assert global_worker_present() is True


def test_actor_service_builds_an_event_manager():
    """The background worker has no request session: the service must build one."""

    service = _service()
    manager = service._event_manager()
    assert manager is not None
    assert hasattr(manager, "chat_event")


def test_dry_run_never_creates_or_consumes_a_card():
    """A dry run is read-only: no shift card and no agenda settlement."""

    service = _service()
    consumed: List[Any] = []
    created: List[Any] = []
    service._consume_agenda = lambda agenda: consumed.append(agenda)  # type: ignore[assignment]
    service._create_shift_card = lambda **kwargs: created.append(kwargs)  # type: ignore[assignment]

    async def dry_run(**_: Any):
        return "## Shift report\nThis is a preview.", 0.1, None

    service._dry_run = dry_run  # type: ignore[assignment]
    report = asyncio.run(
        service._execute(
            name="actor",
            profile=_profile(),
            config=_execute_config(mode=C.MODE_DRY_RUN),
            trigger="test",
            state=ActorState(actor="actor"),
        )
    )

    assert report.turns == 1
    assert created == []
    assert consumed == []


def test_shift_budget_stops_after_an_overspending_turn():
    """A turn that costs more than the shift cap is reported as a budget stop."""

    async def active_turn(**_: Any):
        return "## Shift report\nDone: expensive", 2.0, _Chat("shift")

    service = _service()
    service._active_turn = active_turn  # type: ignore[assignment]
    config = _execute_config(budget_cxjcoins=1.0)
    report = asyncio.run(
        service._execute(
            name="actor",
            profile=_profile(),
            config=config,
            trigger="test",
            state=ActorState(actor="actor"),
        )
    )

    assert report.turns == 1
    assert report.status == C.STATE_BUDGET
    assert report.stop_reason == C.STOP_BUDGET


def test_failed_first_turn_closes_the_shift_card():
    """The card must not stay in Working when the first turn raises."""

    manager = _Manager()
    service = _service(manager=manager)
    chat = _Chat("shift")
    service._create_shift_card = lambda **_: chat  # type: ignore[assignment]

    async def runner(**_: Any):
        raise RuntimeError("provider exploded")

    service._runner = runner
    try:
        asyncio.run(
            service._active_turn(
                name="actor",
                board="Work",
                system="system",
                prompt="prompt",
                agenda=[],
                timeout_seconds=5,
            )
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - the runner must raise
        raise AssertionError("the runner error was swallowed")

    assert chat.status == "error"
    assert chat.column == C.SHIFT_DONE_COLUMN
    assert "provider exploded" in chat.description
    assert chat.id in manager.saved


def test_abandoned_shift_cards_are_recovered():
    """Cards left in Working by a crash are moved out before the next shift."""

    stale = _Chat("shift")
    stale.board = "Work"
    stale.column = C.SHIFT_COLUMN
    stale.tags = ["actor", "shift"]
    stale.meta_data = {"actor": {"actor": "actor"}}
    stale.updated_at = "2020-01-01T00:00:00+00:00"
    manager = _Manager([stale])
    service = _service(manager=manager)

    service._close_stale_shift_cards("actor", "Work")

    assert stale.column == C.SHIFT_DONE_COLUMN
    assert stale.status == "error"
    assert "abandoned" in stale.description.lower()


def test_agenda_keeps_open_tasks_and_clears_closed_tasks():
    """Open tasks stay due; a task the actor closed is consumed."""

    open_task = _Chat("open", wake_at="2026-01-01T00:00:00+00:00")
    closed_task = _Chat("closed", wake_at="2026-01-01T00:00:00+00:00", done=True)
    future_task = _Chat("future", wake_at="2099-01-01T00:00:00+00:00")
    manager = _Manager([open_task, closed_task, future_task])
    service = _service(manager=manager)

    service._consume_agenda(
        [
            {"id": "open"},
            {"id": "closed"},
            {"id": "future"},
        ]
    )

    assert open_task.wake_at  # still due, will be delivered again
    assert closed_task.wake_at == ""
    assert future_task.wake_at  # rescheduled by the actor


def test_disabled_actor_is_listed_but_not_due():
    """Paused actors stay manageable without becoming runnable."""

    enabled_profile = _profile("live", enabled=True)
    paused_profile = _profile("paused", enabled=False)
    service = _service()
    service._profiles = lambda: SimpleNamespace(  # type: ignore[assignment]
        list_all_profiles=lambda: [enabled_profile, paused_profile]
    )

    assert set(service.actors()) == {"live"}
    assert set(service.actors(include_disabled=True)) == {"live", "paused"}

    paused = ActorConfig.from_profile(paused_profile)
    paused_state = ActorState(actor="paused")
    assert service.is_due(paused, paused_state) is False


def test_status_lists_paused_actors():
    """The panel can render and re-enable a paused actor."""

    service = _service(
        store=_Store({"paused": ActorState(actor="paused")})
    )
    service._profiles = lambda: SimpleNamespace(  # type: ignore[assignment]
        list_all_profiles=lambda: [
            _profile("live", enabled=True),
            _profile("paused", enabled=False),
        ]
    )
    status = service.status()
    by_name = {entry["actor"]: entry for entry in status["actors"]}

    assert set(by_name) == {"live", "paused"}
    assert by_name["paused"]["config"]["enabled"] is False
    assert by_name["paused"]["due"] is False


def test_board_manager_defaults_are_budgeted():
    """Legacy board automation gets the same money guardrails as actors."""

    from codx.junior.board_manager.models import BoardManagerConfig

    config = BoardManagerConfig.from_board({"manager": {"enabled": True}})
    assert config.budget_cxjcoins == 2.0
    assert config.daily_budget_cxjcoins == 10.0
    actor = config.to_actor_config("Maintenance")
    assert actor.budget_cxjcoins == 2.0
    assert actor.daily_budget_cxjcoins == 10.0


def test_board_manager_daily_ledger_accumulates():
    """Two reviews in one UTC day share the daily budget counter."""

    from codx.junior.board_manager.models import ManagerState

    state = ManagerState(board="B", board_id="b", day="2026-01-05", day_cost=1.0)
    state.last_run_at = "2026-01-05T10:00:00+00:00"
    state.mark_finished(status="ok", cost=0.25)
    assert state.day_cost == 1.25
    assert state.day_spent("2026-01-05") == 1.25
    assert state.day_spent("2026-01-06") == 0.0


def test_future_manual_wake_is_not_due_yet():
    """``wake(when='+2d')`` must wait for the instant, not run immediately."""

    service = _service()
    now = datetime.now(timezone.utc)
    state = ActorState(
        actor="actor",
        wake_requested=True,
        wake_at=(now + timedelta(days=2)).isoformat(),
    )
    assert service.is_due(ActorConfig(enabled=True), state, now=now) is False

    state.wake_at = (now - timedelta(seconds=1)).isoformat()
    assert service.is_due(ActorConfig(enabled=True), state, now=now) is True


def test_wake_now_clears_a_future_scheduled_wake(tmp_path):
    """Pressing "Run now" must not be ignored by a future wake_at."""
    from codx.junior.autonomy.store import AutonomyStore

    store = AutonomyStore(str(tmp_path))
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    store.set_wake_at("actor", future)

    store.request_wake("actor")

    state = store.get_state("actor")
    assert state.wake_requested is True
    assert state.wake_at == ""
    service = _service()
    assert service.is_due(ActorConfig(enabled=True), state) is True


def test_next_wake_is_measured_from_the_shift_finish():
    """A shift longer than the cadence must not be due again immediately."""

    service = _service()
    now = datetime.now(timezone.utc)
    state = ActorState(
        actor="actor",
        last_shift_at=(now - timedelta(minutes=20)).isoformat(),
        last_finished_at=(now - timedelta(minutes=1)).isoformat(),
        running=False,
        wake_at="",
    )
    config = ActorConfig(enabled=True, interval_minutes=5)
    next_wake = service.next_wake_at(config, state, now=now)

    assert next_wake
    assert not service.is_due(config, state, now=now)
    state.wake_at = next_wake
    assert service.is_due(config, state, now=now) is False


def test_board_only_policy_returns_a_structural_allowlist():
    """Board-only is a tool allowlist, not a prompt sentence."""

    service = _service()
    board_config = ActorConfig(enabled=True, policy=C.POLICY_BOARD)
    full_config = ActorConfig(enabled=True, policy=C.POLICY_FULL)

    allowlist = service.tool_allowlist(board_config)
    assert allowlist is not None
    assert allowlist == set(C.BOARD_ONLY_TOOLS)
    assert "project_write_file" not in allowlist
    assert "docker_exec" not in allowlist
    assert service.tool_allowlist(full_config) is None


def test_invalid_schedule_fails_closed():
    """A malformed HH:MM must not widen the working day to midnight."""

    window = ScheduleWindow(days=["mon"], start="9am", end="18:00").normalized()
    assert window.open_at("mon", 10 * 60) is False

    schedule = WeeklySchedule(
        enabled=True,
        windows=[ScheduleWindow(days=["mon"], start="9am", end="18:00")],
    )
    monday = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    assert schedule.is_open(monday) is False


def test_generated_actor_has_a_budget():
    """A generated teammate is never an unbounded spender by default."""

    from codx.junior.teams.generator import _actor_block

    block = _actor_block(
        {"profile_name": "generated", "title": "Generated"},
        autonomous=True,
        board="",
        schedule=None,
    )
    assert block["budget_cxjcoins"] == 2.0
    assert block["daily_budget_cxjcoins"] == 10.0


def test_generated_team_is_synced_into_the_company(monkeypatch):
    """A generated autonomous team becomes a CompanyTeam, not only a project team."""

    from codx.junior.teams import generator

    class _Team:
        def __init__(self, team_id: str, name: str):
            self.id = team_id
            self.name = name

    class _Company:
        id = "cmp-1"
        teams: List[Any] = []

        def project(self, project_id: str):
            return object() if project_id == "proj-1" else None

    class _FakeManager:
        def __init__(self, *args: Any, **kwargs: Any):
            self.company = _Company()
            self.added: List[Any] = []
            self.members: List[Any] = []

        def get_company(self, company_id: str):
            return self.company

        def add_team(self, company_id: str, payload: Any, actor: str = "system"):
            self.added.append(payload)
            self.company.teams.append(_Team("team-1", payload["name"]))
            return self.company

        def add_team_members(self, company_id, team_id, members, actor="system"):
            self.members.extend(members)
            return self.company

    monkeypatch.setattr(
        "codx.junior.company.manager.CompanyManager", _FakeManager
    )
    errors: List[str] = []
    result = generator._sync_company_team(
        company_id="cmp-1",
        team_name="Maintenance",
        need="Keep the maintenance board clean",
        members=[
            {"profile": "maintainer", "role": "lead", "responsibility": "Tickets", "order": 0},
            {"profile": "reviewer", "role": "member", "responsibility": "Review", "order": 1},
        ],
        project_id="proj-1",
        errors=errors,
    )

    assert errors == []
    assert result["created"] is True
    assert result["team_id"] == "team-1"


def test_chat_engine_applies_the_board_only_allowlist():
    """The engine filter is pure and removes every non-board tool."""

    from codx.junior.chat.chat_engine import ChatEngine

    collected = [
        "project_manager",
        "project_write_file",
        "docker_exec",
        "create_pr_branch",
    ]
    assert ChatEngine._apply_tool_allowlist(
        collected, {"project_manager"}
    ) == ["project_manager"]
    assert ChatEngine._apply_tool_allowlist(collected, None) == collected
    assert ChatEngine._apply_tool_allowlist(collected, set()) == []
