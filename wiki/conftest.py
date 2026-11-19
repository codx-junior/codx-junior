"""Pytest bootstrap for the standalone wiki project.

Puts the project root (the folder holding ``server.py``) on ``sys.path`` so the
test run works whatever directory pytest is started from, without installing the
project as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
