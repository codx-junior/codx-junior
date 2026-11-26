"""Quality subsystem.

The QA control plane lives in :mod:`codx.junior.quality.qa`: deterministic
runners, coverage readers, a change→test mapper, a policy ladder and the gate a
process calls before it claims a change is verified.

The **quality guardians** live in :mod:`codx.junior.quality.guardians`: they are
file-scoped profiles (``file_match``) with deterministic checks and an
evidence-bound AI review, created automatically from the detected stack,
triggered by file changes/staged changes/manual runs and persisted as structured
findings with a verdict ladder.

This module stays empty on purpose: importing the QA package pulls
``subprocess``/``git`` helpers that the API does not need at import time.

Made with ❤️ by codx-junior
"""
