import os
import time
import asyncio
import logging

from threading import Thread
from typing import List, Optional
from pydantic import BaseModel, Field

from concurrent.futures import ThreadPoolExecutor

CODX_JUNIOR_API_BACKGROUND = os.environ.get("CODX_JUNIOR_API_BACKGROUND")

logger = logging.getLogger(__name__)

class IOEvent(BaseModel):
    sid: Optional[str] = Field(default=None) 
    event: str
    data: dict

EVENT_QUEUE: List[IOEvent] = []

# Emits must run on ONE long-lived loop owned by the dispatcher thread. Creating
# a new loop per event (``asyncio.run`` in the loop body) tore down and rebuilt
# an event loop for every streamed chunk, which is pure overhead and can leave
# the Socket.IO server attached to a closed loop.
_EMIT_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _emit_loop() -> asyncio.AbstractEventLoop:
    """Return the dispatcher thread's event loop, creating it once."""
    global _EMIT_LOOP
    if _EMIT_LOOP is None or _EMIT_LOOP.is_closed():
        _EMIT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_EMIT_LOOP)
    return _EMIT_LOOP


def process_event_queue():
    from codx.junior.sio.sio import sio
    from codx.junior.sio.sio_background import sio_client

    while True:
        # Drain the whole queue per wake-up: streaming produces bursts of events
        # and one ``asyncio.run`` per event was the slow path.
        batch: List[IOEvent] = []
        while EVENT_QUEUE:
            batch.append(EVENT_QUEUE.pop(0))
        if batch:
            for event in batch:
                try:
                    if CODX_JUNIOR_API_BACKGROUND:
                        resend_event = {"event": event.event, "data": event.data}
                        logger.info(f"Sending event bacground: {resend_event}")
                        sio_client.emit("background-event", resend_event)
                    else:
                        _emit_loop().run_until_complete(
                            sio.emit(event.event, event.data)
                        )
                except Exception as e:
                    logger.error(f"Error processing event: {e}")
        else:
            # 50 ms instead of 200 ms: the previous poll added up to 200 ms of
            # latency to every streamed chunk the user waits for.
            time.sleep(0.05)


# daemon=True is REQUIRED: a non-daemon thread keeps the interpreter (and every
# test run / container stop) alive forever, because this loop never returns.
Thread(
    target=process_event_queue,
    name="SessionChannelEvents",
    daemon=True,
).start()

class SessionChannel:
    def __init__(self, sio, sid=None):
        self.sid = sid
        self.sio = sio

    def send_event(self, event, data):
        data["ts"] = int(time.time())
        EVENT_QUEUE.append(IOEvent(sid=self.sid, event=event, data=data))

        
