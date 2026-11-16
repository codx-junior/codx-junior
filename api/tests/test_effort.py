"""
Tests for the effort policy (``codx.junior.ai.smol.effort``).

The ``codx.junior.ai.smol`` package ``__init__`` imports ``smol_agent`` (a few
thousand lines plus every provider), so a plain ``import`` of the package is
slow and pulls half the AI stack just to read a dataclass. The two modules
under test are therefore loaded DIRECTLY from their files with ``importlib``
and the package parents are stubbed in ``sys.modules``: this suite is offline,
deterministic and finishes in milliseconds.

Run with::

    cd api && .venv/bin/python -m pytest tests/test_effort.py -q
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SMOL_DIR = Path(__file__).resolve().parents[1] / "codx" / "junior" / "ai" / "smol"
PACKAGE = "codx.junior.ai.smol"


_STUBBED: list = []
#: module name -> value it had BEFORE this file loaded its private copy (None
#: means it was not imported). Restored by ``_remove_package_stubs`` so a private
#: import never leaks into the modules the rest of the suite will import.
_ORIGINAL_MODULES: dict = {}


def _install_package_stubs():
    """Register empty parent packages so nothing is imported from disk."""
    for parent in ("codx", "codx.junior", "codx.junior.ai", PACKAGE):
        if parent not in sys.modules:
            stub = types.ModuleType(parent)
            stub.__path__ = []
            sys.modules[parent] = stub
            _STUBBED.append(parent)


def _remove_package_stubs():
    """Undo everything this module registered in ``sys.modules``.

    Two leaks would break the rest of the suite depending on its order:

    * a stub ``codx`` package has an empty ``__path__``, so every later
      ``import codx.junior.…`` fails with ``ModuleNotFoundError``;
    * the private copies of ``constants``/``effort`` replace the real modules
      when they were already imported by an earlier test.
    """
    for name, previous in _ORIGINAL_MODULES.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    _ORIGINAL_MODULES.clear()
    for parent in reversed(_STUBBED):
        sys.modules.pop(parent, None)
    _STUBBED.clear()


def _load(name, path):
    """Load a single module from a file path and register it in sys.modules."""
    _ORIGINAL_MODULES.setdefault(name, sys.modules.get(name))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_install_package_stubs()
constants = _load(f"{PACKAGE}.constants", SMOL_DIR / "constants.py")
effort = _load(f"{PACKAGE}.effort", SMOL_DIR / "effort.py")
_remove_package_stubs()

effort_for = effort.effort_for
is_agile = effort.is_agile
agile_policy = effort.agile_policy
max_policy = effort.max_policy
default_policy = effort.default_policy


@pytest.fixture(autouse=True)
def _no_agile_env(monkeypatch):
    """Keep the agile deadline deterministic (the env var wins over constants)."""
    monkeypatch.delenv(constants.ENV_AGILE_WALL_CLOCK_SECONDS, raising=False)


# ── Agile tier (the DEFAULT for the ``codx-junior`` harness) ──────────────────
def test_agile_profile_resolves_to_agile_tier():
    policy = effort_for(["codx-junior"])

    assert policy.name == "agile"
    assert policy.continue_on_tool_limit is True
    assert policy.continuations == constants.AGILE_CONTINUATIONS
    assert policy.continuations > 0
    assert policy.wall_clock_seconds == 300.0
    assert policy.has_deadline is True
    assert policy.validation == effort.AGILE_VALIDATION == "targeted+qa_review_changes"


def test_agile_accepts_a_single_string():
    assert effort_for("codx-junior").name == "agile"


def test_agile_budget_is_smaller_than_max():
    agile = effort_for(["codx-junior"])
    maxi = effort_for(["codx-junior-max"])

    assert agile.max_iterations < maxi.max_iterations
    assert agile.max_tool_rounds < maxi.max_tool_rounds
    assert agile.wall_clock_seconds < maxi.wall_clock_seconds


# ── Max tier (the only tier allowed to run unbounded) ────────────────────────
def test_max_profile_resolves_to_max_tier():
    policy = effort_for(["codx-junior-max"])

    assert policy.name == "max"
    assert policy.continue_on_tool_limit is True
    assert policy.continuations == constants.TOOL_LIMIT_CONTINUATIONS
    assert policy.wall_clock_seconds == 3600.0
    # A long ADVISORY target: no built-in tier stops a turn by time.
    assert policy.target_seconds == 3600.0
    assert policy.enforces_wall_clock is False
    assert policy.is_time_advisory is True
    assert policy.validation == effort.MAX_VALIDATION == "full+coverage+security"


def test_max_wins_over_agile_when_both_are_present():
    assert effort_for(["codx-junior", "codx-junior-max"]).name == "max"


# ── Plain chat (no effort profile) ───────────────────────────────────────────
@pytest.mark.parametrize("names", [None, [], ["some-other-profile"]])
def test_plain_chat_gets_the_default_tier(names):
    policy = effort_for(names)

    assert policy.name == "default"
    assert policy.continue_on_tool_limit is False
    assert policy.continuations == 0
    assert policy.wall_clock_seconds == 0.0
    assert policy.has_deadline is False


# ── Orchestration worker/supervisor keeps the old harness behaviour ──────────
def test_orchestration_turns_stay_on_the_harness_continuation():
    policy = effort_for([], orchestration_info={"id": "x"})

    assert policy.name == "max"
    assert policy.continue_on_tool_limit is True
    assert is_agile([], orchestration_info={"id": "x"}) is False


def test_orchestration_wins_over_the_agile_profile():
    policy = effort_for(["codx-junior"], orchestration_info={"role": "worker"})

    assert policy.name == "max"


# ── Profile-name normalisation ───────────────────────────────────────────────
def test_profile_names_are_normalised():
    assert effort_for(["@codx-junior-max"]).name == "max"
    assert effort_for([" Codx-Junior "]).name == "agile"
    assert effort_for(["@Codx-Junior"]).name == "agile"


# ── Convenience helper ──────────────────────────────────────────────────────
def test_is_agile():
    assert is_agile(["codx-junior"]) is True
    assert is_agile("codx-junior") is True
    assert is_agile(["codx-junior-max"]) is False
    # A plain chat is neither agile nor max: it keeps its own tier.
    assert is_agile(None) is False


def test_both_effort_tiers_continue_after_a_tool_limit():
    """No tier may dead-end a task; only the explicit override can."""
    assert effort_for(["codx-junior"]).continue_on_tool_limit is True
    assert effort_for(["codx-junior-max"]).continue_on_tool_limit is True
    assert effort_for(None).continue_on_tool_limit is False


def test_no_builtin_tier_enforces_its_clock():
    """Only the user ends a task: the wall clock is advisory in both tiers."""
    for names in (["codx-junior"], ["codx-junior-max"], None):
        assert effort_for(names).enforces_wall_clock is False


def test_an_enforced_clock_is_opt_in():
    policy = effort_for(["codx-junior"], settings={"stop_on_wall_clock": True})

    assert policy.enforces_wall_clock is True


# ── Explicit override (caller config beats the tier default) ─────────────────
def test_explicit_true_grants_the_continuation_to_an_agile_turn():
    policy = effort_for(["codx-junior"], explicit=True)

    assert policy.continue_on_tool_limit is True
    assert policy.continuations == constants.TOOL_LIMIT_CONTINUATIONS
    assert is_agile(["codx-junior"]) is True


def test_explicit_false_disables_the_continuation_of_a_max_turn():
    policy = effort_for(["codx-junior-max"], explicit=False)

    assert policy.continue_on_tool_limit is False


def test_settings_mapping_overrides_the_tier_defaults():
    policy = effort_for(["codx-junior"], settings={"wall_clock_seconds": 42.0,
                                                   "max_tool_calls": 2})

    assert policy.name == "agile"
    assert policy.wall_clock_seconds == 42.0
    assert policy.max_tool_calls == 2


def test_settings_none_values_keep_the_default():
    policy = effort_for(["codx-junior"], settings={"wall_clock_seconds": None})

    assert policy.wall_clock_seconds == 300.0


def test_factories_accept_settings_directly():
    assert agile_policy({"validation": "custom"}).validation == "custom"
    assert agile_policy().validation == effort.AGILE_VALIDATION
    assert max_policy().name == "max"
    assert default_policy().name == "default"


def test_overrides_are_not_shared_between_calls():
    overridden = effort_for(["codx-junior"], settings={"max_tool_calls": 1})

    assert overridden.max_tool_calls == 1
    assert effort_for(["codx-junior"]).max_tool_calls == constants.AGILE_MAX_TOOL_CALLS


# ── Module surface ──────────────────────────────────────────────────────────
def test_public_surface():
    assert effort.AGILE_PROFILE == "codx-junior"
    assert effort.MAX_PROFILE == "codx-junior-max"
    for name in ("EffortPolicy", "agile_policy", "max_policy", "default_policy",
                 "effort_for", "is_agile"):
        assert name in effort.__all__
