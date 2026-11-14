"""Quality subsystem.

The QA control plane lives in :mod:`codx.junior.quality.qa`: deterministic
runners, coverage readers, a change→test mapper, a policy ladder and the gate a
process calls before it claims a change is verified.

This module stays empty on purpose: importing the QA package pulls
``subprocess``/``git`` helpers that the API does not need at import time.

Made with ❤️ by codx-junior
"""
