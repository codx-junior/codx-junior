"""
Tests for the ``### TEAM`` section of the system prompt.

The team is a list of profiles the model can delegate to. The old section told
the model to stop and ask the user to re-run the task with a profile; that is
the "half development + do you want me to continue?" behaviour. The section must
point at the delegation tools instead.

Run with::

    cd api && pytest tests/chat/test_team_section.py -q
"""

import os

# ``codx.junior.main`` wires the module import order used by the app; importing
# ``chat_engine`` directly hits its circular import with ``context``.
os.environ.setdefault("CODX_JUNIOR_STATIC_FOLDER", "/tmp/codx-junior-static-tests")
import codx.junior.main  # noqa: E402,F401

from codx.junior.chat.chat_engine import ChatEngine  # noqa: E402
from codx.junior.model.profile import Profile  # noqa: E402


def _team():
    return [
        Profile(name="software_developer", description="Writes and validates code."),
        Profile(name="qa", description="Runs the quality gate."),
    ]


def test_team_section_offers_delegation_not_a_user_handoff():
    text = ChatEngine._build_team_system_section(_team())

    assert "### TEAM" in text
    assert "software_developer" in text
    assert "qa" in text
    assert "delegate" in text
    # The old sentence is the regression: it handed the task back to the user.
    assert "ask the user to run" not in text
    assert "say so and ask" not in text


def test_team_section_is_empty_without_members():
    assert ChatEngine._build_team_system_section([]) == ""
