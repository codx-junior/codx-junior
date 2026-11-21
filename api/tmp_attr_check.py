"""Throwaway probe: can the project settings hold live turn state, and does a
deep copy of it explode the way the delegated cards did?"""

import asyncio
import copy
import sys

sys.path.insert(0, "/home/codx-junior/codx-junior/api")

import pydantic  # noqa: E402
from codx.junior.settings import CODXJuniorSettings  # noqa: E402

print("pydantic:", pydantic.VERSION)
settings = CODXJuniorSettings()
for attr in ("_active_session", "_active_cancellation_token"):
    try:
        setattr(settings, attr, object())
        print(attr, "-> SETTABLE on", type(settings).__name__)
    except Exception as ex:  # noqa: BLE001
        print(attr, "-> REFUSED:", type(ex).__name__, ex)

class FakeToken:
    def __init__(self, loop):
        self._even_loop = loop
        self._future = loop.create_future()

loop = asyncio.new_event_loop()
try:
    setattr(settings, "_active_cancellation_token", FakeToken(loop))
    try:
        copy.deepcopy(settings)
        print("deepcopy of live turn state -> OK (no bug)")
    except Exception as ex:  # noqa: BLE001
        print("deepcopy of live turn state -> FAILED:", type(ex).__name__, ex)
finally:
    loop.close()
