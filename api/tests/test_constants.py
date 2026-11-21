"""
Tests for the effort-tier constants (``codx.junior.ai.smol.constants``).

The ``codx.junior.ai.smol`` package ``__init__`` imports ``smol_agent`` (a few
thousand lines plus every provider), so a plain ``import`` of the package is
slow and pulls half the AI stack just to read module constants. The module
under test is therefore loaded DIRECTLY from its file with ``importlib`` and
the package parents are stubbed in ``sys.modules`` (same technique as
``tests/test_effort.py``): this suite is offline, deterministic and finishes in
milliseconds.

The values asserted here are the contract the effort policy relies on
(``codx.junior.ai.smol.effort``): AGILE is the DEFAULT tier (small base budget,
bounded course corrections, ADVISORY clock) and MAX is the heavier tier. Neither
tier may dead-end a task.

Run with::

    cd api && .venv/bin/python -m pytest tests/test_constants.py -q
"""
import importlib.util
import sys
import types
from pathlib import Path

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
_remove_package_stubs()


# ── AGILE tier: the DEFAULT for the ``codx-junior`` harness profile ──────────
def test_agile_budget_is_small_and_int():
    # HIGH effort: enough room to finish normal multi-step work in one turn,
    # while staying clearly below the max tier.
    assert constants.AGILE_MAX_ITERATIONS == 30
    assert constants.AGILE_MAX_TOOL_CALLS == 8
    assert constants.AGILE_MAX_TOOL_ROUNDS == 60
    for value in (
        constants.AGILE_MAX_ITERATIONS,
        constants.AGILE_MAX_TOOL_CALLS,
        constants.AGILE_MAX_TOOL_ROUNDS,
    ):
        assert isinstance(value, int) and value > 0


def test_agile_continues_after_a_tool_limit():
    """A budget limit must never dead-end a task: agile gets corrections too."""
    assert constants.AGILE_CONTINUATIONS == 3
    assert constants.AGILE_CONTINUATIONS > 0


def test_agile_wall_clock_is_short_and_overridable_by_env():
    assert constants.AGILE_WALL_CLOCK_SECONDS == 300.0
    assert isinstance(constants.AGILE_WALL_CLOCK_SECONDS, float)
    assert constants.ENV_AGILE_WALL_CLOCK_SECONDS == (
        "CODX_JUNIOR_AGILE_WALL_CLOCK_SECONDS"
    )


# ── MAX tier: the ONLY tier allowed to run unbounded (``codx-junior-max``) ───
def test_max_budget_is_larger_than_agile():
    # Deliberate product decision: the max tier runs 80 tool iterations with 12
    # calls per round, 160 rounds and 6 course corrections. It is a long-horizon
    # tier, not "agile with bigger numbers".
    assert constants.MAX_MAX_ITERATIONS == 80
    assert constants.MAX_MAX_TOOL_CALLS == 12
    assert constants.MAX_MAX_TOOL_ROUNDS == 160

    assert constants.MAX_MAX_ITERATIONS > constants.AGILE_MAX_ITERATIONS
    assert constants.MAX_MAX_TOOL_ROUNDS > constants.AGILE_MAX_TOOL_ROUNDS
    # Its own course corrections: a phased plan is not collapsed by a limit.
    assert constants.MAX_CONTINUATIONS == 6
    assert constants.MAX_CONTINUATIONS > constants.TOOL_LIMIT_CONTINUATIONS
    assert (
        constants.MAX_CONTINUATION_MAX_ITERATIONS
        > constants.TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS
    )


def test_max_wall_clock_is_long():
    assert constants.MAX_WALL_CLOCK_SECONDS == 14400.0
    assert constants.MAX_TARGET_SECONDS == 7200.0
    assert constants.MAX_WALL_CLOCK_SECONDS > constants.AGILE_WALL_CLOCK_SECONDS
    assert constants.MAX_TARGET_SECONDS > constants.AGILE_TARGET_SECONDS


# ── Shared continuation budget: reused by MAX, unchanged by this change ──────
def test_tool_limit_continuation_constants_are_unchanged():
    assert constants.TOOL_LIMIT_CONTINUATIONS == 2
    assert constants.TOOL_LIMIT_CONTINUATION_MAX_ITERATIONS == 6
    assert constants.TOOL_LIMIT_CONTINUATION_MAX_TOOL_CALLS == 3


def test_default_tier_constants_still_exist():
    """The plain-chat tier of ``effort.default_policy`` keeps its constants."""
    assert isinstance(constants.DEFAULT_MAX_ITERATIONS, int)
    assert isinstance(constants.DEFAULT_MAX_TOOL_CALLS, int)
    assert isinstance(constants.MAX_TOOL_ROUNDS, int)
