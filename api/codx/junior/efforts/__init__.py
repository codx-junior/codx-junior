"""
Effort storage: named effort definitions that profiles are linked to.

The package exposes :class:`~codx.junior.efforts.effort_manager.EffortManager`,
which owns ``.codx/efforts/<Name>/<Name>.effort`` and resolves the values that
:func:`codx.junior.ai.smol.effort.effort_for` applies to a turn.

Made with ❤️ by codx-junior
"""
from codx.junior.efforts.effort_manager import (
    EFFORT_EXTENSION,
    EffortManager,
    apply_effort_to_profile,
)

__all__ = [
    "EFFORT_EXTENSION",
    "EffortManager",
    "apply_effort_to_profile",
]

# Made with ❤️ by codx-junior
