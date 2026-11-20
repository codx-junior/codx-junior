"""
Meets subsystem – WebRTC calls and company meetings.

This package contains:

* :mod:`codx.junior.meets.model` – Pydantic models (meet, participants,
  transcript, minutes, recording, floor, API payloads).
* :mod:`codx.junior.meets.meet_manager` – process-wide in-memory registry.
* :mod:`codx.junior.meets.store` – durable meeting documents and recordings.
* :mod:`codx.junior.meets.service` – chat bridge, transcript, profile turns,
  minutes, task creation and recording.
* :mod:`codx.junior.meets.orchestrator` – one-speaker-at-a-time scheduler with
  human barge-in.
* :mod:`codx.junior.meets.agents` – profile answers and the minute taker.
* :mod:`codx.junior.meets.context` – bounded task-status snapshot.
* :mod:`codx.junior.meets.profiles` – profile participant and voice resolution.

Made with ❤️ by codx-junior
"""
