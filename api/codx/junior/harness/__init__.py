"""
codx-junior harness: the native harness runtime of codx-junior.

The ``codx-junior`` profile is the main contact between the user and the rest
of the platform. This package provides the harness infrastructure around the
existing durable orchestration runtime:

* :mod:`.audit` — the append-only audit journal (``.codx/harness/audit.jsonl``)
  where every user request, run, step and completion is registered.
* :mod:`.watchdog` — the harness watchdog that keeps the orchestration worker
  alive: it detects a stalled worker and restarts it (or raises the alarm).
* :mod:`.prompts` — the harness-mode contract appended to the first message of
  a session (survey → reproduce → analyse → implement → test → verify → report).
* :mod:`.sessions` — the session composition layer behind the DSH-like harness
  mode UI: create/list/inspect normal task chats, read their task worktree and
  branch diff, and append follow-ups.
* :mod:`codx.junior.api.harness` — the REST endpoints that expose the audit
  journal, the worker health and the harness sessions.

Everything is best-effort and crash-safe by construction: records are appended
atomically, the journal is JSONL (one line per event), and the watchdog only
relies on the durable worker heartbeat files already written by the
orchestration worker.
"""
