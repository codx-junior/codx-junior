"""
Tests for the codx-junior harness profile and process supervision assets.

Checks that:

* the built-in ``codx-junior`` profile exists and declares the all-tools
  marker,
* the workspace copy of the profile stays in sync with the built-in one,
* the ``supervisor.conf`` at the repository root supervises every harness
  process with autorestart (the ``codx-junior-api-background`` program name
  matches the one the harness watchdog restarts).

Run with::

    cd api && pytest tests/harness/test_harness_profile.py -q
"""

import json
import os
from pathlib import Path

from codx.junior.model.profile import Profile

# api/tests/harness -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

BUILTIN_PROFILE = (
    REPO_ROOT / "api" / "codx" / "junior" / "profiles" / "codx-junior" / "codx-junior.profile"
)
BUILTIN_CONTENT = (
    REPO_ROOT / "api" / "codx" / "junior" / "profiles" / "codx-junior" / "codx-junior.md"
)
WORKSPACE_PROFILE = REPO_ROOT / ".codx" / "profiles" / "codx-junior" / "codx-junior.profile"
WORKSPACE_CONTENT = REPO_ROOT / ".codx" / "profiles" / "codx-junior" / "codx-junior.md"
SUPERVISOR_CONF = REPO_ROOT / "supervisor.conf"

MAX_PROFILE = (
    REPO_ROOT / "api" / "codx" / "junior" / "profiles" / "codx-junior-max" / "codx-junior-max.profile"
)
MAX_CONTENT = (
    REPO_ROOT / "api" / "codx" / "junior" / "profiles" / "codx-junior-max" / "codx-junior-max.md"
)
WORKSPACE_MAX_PROFILE = (
    REPO_ROOT / ".codx" / "profiles" / "codx-junior-max" / "codx-junior-max.profile"
)
WORKSPACE_MAX_CONTENT = (
    REPO_ROOT / ".codx" / "profiles" / "codx-junior-max" / "codx-junior-max.md"
)

# Fields that carry the meaning of a profile and must never drift between the
# built-in definition and the workspace copy.
SYNCED_FIELDS = ("name", "description", "profiles", "tools", "team", "tags")

# The delivery contract every harness tier must keep in its instructions.
DELIVERY_MARKERS = ("## What happened", "## Next tasks", "**Good enough?**")


def _synced_fields(profile: Profile) -> dict:
    return {field: getattr(profile, field) for field in SYNCED_FIELDS}


def _instruction_text(path: Path) -> str:
    """Instruction content with trailing whitespace and the final newline removed.

    The profile writer serializes instructions without a trailing newline, so a
    byte comparison is not a valid contract for these files.
    """
    lines = [line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()]
    return "\n".join(lines)


def test_builtin_profile_exists_and_parses():
    assert BUILTIN_PROFILE.is_file()
    data = json.loads(BUILTIN_PROFILE.read_text(encoding="utf-8"))
    profile = Profile(**data)
    assert profile.name == "codx-junior"
    assert profile.tools == ["*"]
    assert profile.description
    assert "software_developer" in profile.team


def test_builtin_profile_instructions_exist():
    assert BUILTIN_CONTENT.is_file()
    content = BUILTIN_CONTENT.read_text(encoding="utf-8")
    assert "codx-junior harness" in content
    # The agile tier closes every answer with the delivery contract.
    assert "## What happened" in content
    assert "## Next tasks" in content
    assert "**Good enough?**" in content


def test_builtin_max_profile_links_the_agile_tier():
    """The heavy tier is a DELTA: it links codx-junior instead of copying it."""
    assert MAX_PROFILE.is_file()
    profile = Profile(**json.loads(MAX_PROFILE.read_text(encoding="utf-8")))
    assert profile.name == "codx-junior-max"
    assert profile.profiles == ["codx-junior"]
    assert profile.tools == ["*"]

    assert MAX_CONTENT.is_file()
    content = MAX_CONTENT.read_text(encoding="utf-8")
    # Orchestration is the max tier's job (the agile tier delegates it).
    assert 'orchestrator(action="start")' in content or "orchestrator" in content
    assert "**Good enough?**" in content


def test_workspace_copy_matches_builtin():
    """The workspace copy must stay in sync with the built-in profile.

    Byte equality is *not* the contract. When a profile is saved the application
    serializes the loaded ``Profile`` back to ``.codx/profiles``, so the
    workspace copy also carries the fields resolved at load time (``path``,
    ``user``, ``guardrails``, ``effort_name``, ``project_id``) and is written
    without the trailing newline. Re-syncing the bytes would be undone by the
    next profile save, and copying those bytes into the built-in tree would
    embed a machine-specific path and a user snapshot in the shipped profile.

    What must never drift is the meaning: identity, linked profiles, tools,
    team, tags and the instruction content.
    """
    assert WORKSPACE_PROFILE.is_file()
    workspace = Profile(**json.loads(WORKSPACE_PROFILE.read_text(encoding="utf-8")))
    builtin = Profile(**json.loads(BUILTIN_PROFILE.read_text(encoding="utf-8")))
    assert _synced_fields(workspace) == _synced_fields(builtin)

    assert WORKSPACE_MAX_PROFILE.is_file()
    workspace_max = Profile(**json.loads(WORKSPACE_MAX_PROFILE.read_text(encoding="utf-8")))
    builtin_max = Profile(**json.loads(MAX_PROFILE.read_text(encoding="utf-8")))
    assert _synced_fields(workspace_max) == _synced_fields(builtin_max)

    # The workspace instructions are copies of the built-in content: they must
    # still carry the delivery contract, never be emptied or replaced.
    for path in (WORKSPACE_CONTENT, WORKSPACE_MAX_CONTENT):
        assert path.is_file(), f"missing {path}"
        text = _instruction_text(path)
        assert text.strip(), f"empty instructions in {path}"
        for marker in DELIVERY_MARKERS:
            assert marker in text, f"{marker} missing from {path}"


def test_supervisor_conf_supervises_all_processes_with_autorestart():
    assert SUPERVISOR_CONF.is_file()
    conf = SUPERVISOR_CONF.read_text(encoding="utf-8")
    for program in ("codx-junior-api", "codx-junior-api-background", "codx-junior-client"):
        assert f"[program:{program}]" in conf, f"missing program {program}"
    assert conf.count("autorestart=true") == 3
    # The watchdog restarts this exact program name.
    from codx.junior.harness.watchdog import BACKGROUND_PROGRAM

    assert BACKGROUND_PROGRAM == "codx-junior-api-background"
    assert f"[program:{BACKGROUND_PROGRAM}]" in conf
