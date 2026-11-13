"""
ChatEventBridge — attaches AgentRunContext events to the chat response message
and persists it on EVERY change (crash-safe).

During an AI turn the SmolAgent emits :class:`engine.agent_runtime.AgentEvent`
notifications (tool start/end/error, run lifecycle).

Events are ASSOCIATED with the assistant **response message** that the
ChatEngine created BEFORE calling the AI — they are never emitted as separate
chat messages:

    * ``response_message.tool_events``      → list of :class:`ToolEvent`
      (one entry per tool call, updated in place: running → done/error). Each
      event also carries the tool's user-facing ``output`` so tool output can be
      rendered collapsed/hidden instead of polluting ``Message.content``.
    * ``response_message.lifecycle_events`` → list of :class:`LifeCycleEvent`
      (one entry per agent run, updated in place)

Tool output
-----------
TOOL_END events carry the dual-response tool's user-facing ``output``
(``ToolResponse.user_response``). The bridge stores it on the matching
:class:`ToolEvent` (``ToolEvent.output``) instead of it being merged into the
chat content, so the client decides how to display it. Cached tool calls do not
re-emit output (the first execution already delivered it).

Crash-safety guarantee
----------------------
On every event change the response message is:

    1. **Persisted immediately** via the ChatManager granular merge-safe
       methods (``add_message`` on first change, ``update_message`` after).
       If the process dies mid-run, every event received so far — including
       tool errors, run errors and aggregated analytics — is already on disk.
    2. **Streamed** to clients in real time via the EventManager so the UI
       can render tool progress live.

ADDED — streamed-chunk crash-safety (:meth:`maybe_persist_stream`):
    Streaming callbacks may call :meth:`maybe_persist_stream` on every flush;
    the bridge persists the response message AT MOST once per
    ``STREAM_PERSIST_MIN_INTERVAL_SECONDS`` (monotonic clock). A hard kill
    therefore loses at most that many seconds of streamed partial text while
    keeping disk I/O bounded.

ADDED — auxiliary message crash-safety (:meth:`persist_message`):
    Any auxiliary message produced mid-run (e.g. hidden reasoning messages)
    can be persisted immediately and idempotently. Persisted ``doc_id``s are
    tracked bridge-side so the first call inserts (``add_message``) and later
    calls update (``update_message``).

ADDED — defensive ChatManager fallback:
    The idempotency guarantee assumes ``ChatManager.update_message`` matches
    by ``doc_id``. If the granular methods are missing or fail, the bridge
    falls back to a coarse ``save_chat`` full write (after making sure the
    message is on the in-memory chat) so information is never silently lost.

The engine reuses :meth:`publish` on its own error / cancellation paths so
``response_message.error`` and ``cancelled_at`` are also persisted the moment
they are known, not only at the end of the turn.

    ```mermaid
    sequenceDiagram
        participant Engine as ChatEngine
        participant SmolAgent
        participant Bridge as ChatEventBridge
        participant Response as ResponseMessage
        participant ChatManager
        participant EventManager

        Engine->>Response: create (before AI call)
        Engine->>Bridge: ChatEventBridge(chat, response_message, chat_manager)
        Engine->>Bridge: maybe_persist_stream (throttled, on stream flush)
        Bridge->>ChatManager: add/update_message (at most 1 write / interval)
        SmolAgent->>Bridge: TOOL_START (tool_call_id, tool, args)
        Bridge->>Response: tool_events.append(ToolEvent running)
        Bridge->>ChatManager: add_message (persist — crash-safe)
        Bridge->>EventManager: message_event (stream)
        SmolAgent->>Bridge: TOOL_END (tool_call_id, duration, result, output)
        Bridge->>Response: update SAME ToolEvent (done, response, output)
        Bridge->>ChatManager: update_message (persist)
        Bridge->>EventManager: message_event (stream)
        Engine->>Bridge: persist_message(hidden reasoning message)
        Bridge->>ChatManager: add_message (persist immediately)
        SmolAgent->>Bridge: RUN_ERROR (error, analytics)
        Bridge->>Response: LifeCycleEvent(error) + analytics in meta_data
        Bridge->>ChatManager: update_message (persist — error survives crash)
        Bridge->>EventManager: message_event (stream)
        Note over Bridge,ChatManager: On AttributeError / failure → fallback save_chat
    ```
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from engine.agent_runtime import AgentEvent, AgentEventType

from codx.junior.db import (
    Chat,
    LifeCycleEvent,
    Message,
    PhaseEvent,
    ToolEvent,
)

logger = logging.getLogger(__name__)

# Maximum characters of a tool response stored in the ToolEvent.
TOOL_RESPONSE_MAX_CHARS: int = 4000

# ADDED: Minimum seconds between throttled streamed-content persists.
# Bounds disk I/O while guaranteeing a hard kill loses at most this many
# seconds of streamed partial text.
STREAM_PERSIST_MIN_INTERVAL_SECONDS: float = 5.0

# Shared status values for ToolEvent / LifeCycleEvent
STATUS_RUNNING: str = "running"
STATUS_DONE: str = "done"
STATUS_ERROR: str = "error"

# Run lifecycle events tracked on the response message.
LIFECYCLE_EVENT_TYPES = {
    AgentEventType.RUN_START,
    AgentEventType.RUN_END,
    AgentEventType.RUN_ERROR,
    AgentEventType.RUN_CANCELLED,
}



def _iso_ts(value: Optional[float] = None) -> str:
    """
    ISO-8601 UTC timestamp of *value* (default: now).

    The ``+00:00`` offset is normalised to ``Z`` so every reader (browser
    ``Date``, log parsers) interprets it the same way. Without timestamps on the
    events a client could only GUESS where a call sat inside the turn — which is
    why the timeline had to spread the unaccounted time over the gaps.

    :param value: Epoch seconds; defaults to the current time.
    :return: ISO-8601 UTC string, millisecond precision.
    """
    stamp = time.time() if value is None else float(value)
    return (
        datetime.fromtimestamp(stamp, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_iso(value: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 timestamp back to epoch seconds (None when invalid)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _truncate(text: str, max_chars: int = TOOL_RESPONSE_MAX_CHARS) -> str:
    """
    Truncate *text* to *max_chars*, appending an ellipsis marker when cut.

    :param text: The text to truncate.
    :param max_chars: Maximum number of characters to keep.
    :return: The (possibly truncated) text.
    """
    if text and len(text) > max_chars:
        return text[:max_chars] + "\n… (truncated)"
    return text or ""


class ChatEventBridge:
    """
    Bridges agent runtime events onto the chat RESPONSE message with
    crash-safe persistence on every change.

    The bridge is registered as a listener on an
    :class:`engine.agent_runtime.AgentRunContext` and must **never raise**:
    any persistence/streaming failure is logged and swallowed so a broken
    notification can never kill the AI run.

    All events are stored as typed entries on the response message
    (``tool_events`` / ``lifecycle_events``). The message reference is shared
    with the ChatEngine, so mutations are visible immediately.

    Persistence idempotency is enforced BRIDGE-SIDE: the bridge tracks which
    ``doc_id``s have already been inserted and calls ``add_message`` only
    once per message, then ``update_message`` afterwards. If the ChatManager
    lacks these granular methods (or they fail), the bridge falls back to a
    coarse ``save_chat`` full write so no information is dropped.
    """

    def __init__(
        self,
        chat: Chat,
        response_message: Message,
        chat_manager,
        event_manager,
        progress_callback=None,
    ) -> None:
        """
        :param chat: The in-memory chat being processed (shared reference).
        :param response_message: The assistant response message created by the
                                 ChatEngine BEFORE the AI call; events are
                                 attached to it (mutated in place).
        :param chat_manager: ChatManager providing merge-safe
                             ``add_message`` / ``update_message`` persistence
                             (``save_chat`` used as a defensive fallback).
        :param event_manager: EventManager used to stream message events.
        """
        self.chat: Chat = chat
        self.response_message: Message = response_message
        self.chat_manager = chat_manager
        self.event_manager = event_manager
        # Progress heartbeat of the durable turn record (optional). Called on
        # every event and on every persisted streaming flush, so the recovery
        # worker can tell a SLOW turn from a DEAD one (no progress) and restart
        # the execution instead of leaving the chat on "processing" forever.
        self.progress_callback = progress_callback
        # ADDED: doc_ids already inserted on disk (idempotency enforced here,
        # covers the response message AND any auxiliary message).
        self._persisted_doc_ids: Set[str] = set()
        # ADDED: monotonic timestamp of the last successful persist; primed to
        # "now" so the initial "Processing..." placeholder is not persisted
        # immediately — the first throttled persist happens after the interval.
        self._last_persist_ts: float = time.monotonic()
        # tool_call_id (or tool name fallback) -> in-place updated ToolEvent
        self._tool_events: Dict[str, ToolEvent] = {}
        # run_id -> in-place updated LifeCycleEvent
        self._lifecycle_events: Dict[str, LifeCycleEvent] = {}
        # Phase currently waiting for the model's FIRST token (TTFT). It is the
        # only place where "we are waiting for the AI" is a recorded fact: a turn
        # with no tool running is NOT necessarily idle.
        self._open_llm: Optional[PhaseEvent] = None
        # Phase currently streaming the model response (tokens arriving).
        self._stream_phase: Optional[PhaseEvent] = None

    @property
    def response_persisted(self) -> bool:
        """True when the response message has already been inserted on disk."""
        return self.response_message.doc_id in self._persisted_doc_ids

    # ── Progress heartbeat ─────────────────────────────────────────────

    def _progress(self) -> None:
        """
        Report PROGRESS to the turn heartbeat (never raises).

        A turn that stops reporting progress stopped working: the recovery
        worker sees the stale heartbeat, stops it and restarts the execution.
        """
        if self.progress_callback is None:
            return
        try:
            self.progress_callback()
        except Exception as ex:  # pragma: no cover - a heartbeat never kills a run
            logger.debug(
                "ChatEventBridge: progress callback failed for chat '%s': %s",
                self.chat.id,
                ex,
            )

    # ── Event entry point ──────────────────────────────────────────────────────

    def on_event(self, event: AgentEvent) -> None:
        """
        Handle an :class:`AgentEvent` emitted by the agent run context.

        :param event: The emitted event.
        """
        try:
            if event.type == AgentEventType.TOOL_START:
                self._on_tool_start(event)
            elif event.type == AgentEventType.TOOL_END:
                self._on_tool_finished(event, error=False)
            elif event.type == AgentEventType.TOOL_ERROR:
                self._on_tool_finished(event, error=True)
            elif event.type in LIFECYCLE_EVENT_TYPES:
                self._on_lifecycle(event)
            elif event.type == AgentEventType.LLM_REQUEST:
                self._on_llm_request(event)
            elif event.type == AgentEventType.LLM_CHUNK:
                self._on_llm_chunk(event)
            elif event.type == AgentEventType.LLM_USAGE:
                self._on_llm_usage(event)
            elif event.type == AgentEventType.TOOL_LOGS:
                self._on_tool_logs(event)
            elif event.type == AgentEventType.WALLET_CHECK:
                self._on_wallet_check(event)
        except (OSError, ValueError, TypeError, RuntimeError) as ex:
            # A broken notification must NEVER kill the AI run.
            logger.exception(
                "ChatEventBridge: failed handling event '%s' for chat '%s': %s",
                event.type,
                self.chat.id,
                ex,
            )
        finally:
            # Every handled event is PROGRESS of the run.
            self._progress()

    # ── Tool events ────────────────────────────────────────────────────────────

    @staticmethod
    def _tool_key(event: AgentEvent) -> str:
        """Return the lookup key for the tool event of *event*."""
        return str(event.payload.get("tool_call_id") or event.payload.get("tool") or "")

    def _register_tool_event(self, event: AgentEvent, status: str) -> ToolEvent:
        """
        Create a new ToolEvent, attach it to the response message and track it.

        :param event: The originating TOOL_* agent event.
        :param status: Initial status ('running', 'done', or 'error').
        :return: The registered ToolEvent instance.
        """
        payload = event.payload
        tool_event = ToolEvent(
            tool=payload.get("tool", "unknown"),
            tool_call_id=str(payload.get("tool_call_id") or ""),
            status=status,
            request=payload.get("args") or {},
            # Real timestamps: the runtime emits them on the event, so the client
            # never has to guess where the call sits inside the turn again.
            started_at=str(payload.get("started_at") or _iso_ts(event.ts)),
            cached=payload.get("cached"),
        )
        self._tool_events[self._tool_key(event)] = tool_event
        self.response_message.tool_events.append(tool_event)
        return tool_event

    def _on_tool_start(self, event: AgentEvent) -> None:
        """Attach a new 'running' ToolEvent, then persist + stream immediately."""
        tool_event = self._register_tool_event(event, status=STATUS_RUNNING)
        logger.info(
            "ChatEventBridge: tool '%s' started (chat='%s', run='%s')",
            tool_event.tool,
            self.chat.id,
            event.run_id,
        )
        self.publish()

    def _on_tool_finished(self, event: AgentEvent, error: bool) -> None:
        """Update the matching ToolEvent in place, then persist + stream."""
        payload = event.payload
        duration_ms = payload.get("duration_ms", 0.0) or 0.0

        tool_event = self._tool_events.get(self._tool_key(event))
        if tool_event is None:
            # Defensive: TOOL_END/TOOL_ERROR without a matching TOOL_START
            tool_event = self._register_tool_event(event, status=STATUS_RUNNING)

        tool_event.duration_ms = duration_ms
        tool_event.finished_at = str(payload.get("finished_at") or _iso_ts())
        if not tool_event.started_at:
            tool_event.started_at = _iso_ts(event.ts - (duration_ms / 1000.0))
        # The model stopped generating to hand the turn over to the tool: the
        # streaming phase ends here, not at the next request.
        self._close_stream_phase()

        if error:
            tool_event.status = STATUS_ERROR
            tool_event.error = _truncate(str(payload.get("error") or "unknown error"))
        else:
            tool_event.status = STATUS_DONE
            tool_event.response = _truncate(str(payload.get("result") or ""))
            # Tool output (dual-response user_response) is attached as a
            # collapsible message property instead of being merged into the
            # chat content. Stored UNTRUNCATED so generated code/blocks are not
            # lost. Cached tool calls do not carry an output (see SmolAgent).
            output = payload.get("output")
            if output:
                tool_event.output = str(output)

        logger.info(
            "ChatEventBridge: tool '%s' %s in %.0f ms (chat='%s')",
            tool_event.tool,
            tool_event.status,
            duration_ms,
            self.chat.id,
        )
        self.publish()

    # ── Phase events (what the turn is WAITING for) ────────────────────────────

    def _phase_list(self):
        """Return the phase list of the response message, creating it if needed."""
        if self.response_message.phase_events is None:
            self.response_message.phase_events = []
        return self.response_message.phase_events

    def _register_phase(
        self,
        kind: str,
        *,
        status: str = STATUS_RUNNING,
        started_at: Optional[str] = None,
        detail: Optional[str] = None,
        label: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> PhaseEvent:
        """
        Append a new :class:`PhaseEvent` to the response message.

        :param kind: Phase kind (``llm_request``, ``llm_response``, ``notice``).
        :param status: Initial status ('running' while the wait is in flight).
        :param started_at: ISO start timestamp (defaults to now).
        :param detail: One-line description shown by the client.
        :param label: Short human label.
        :param meta: Free-form metrics (model, tokens, ...).
        :return: The appended phase.
        """
        phase = PhaseEvent(
            kind=kind,
            status=status,
            label=label,
            detail=detail,
            started_at=started_at or _iso_ts(),
            meta=meta or None,
        )
        self._phase_list().append(phase)
        return phase

    @staticmethod
    def _close_phase(
        phase: Optional[PhaseEvent],
        status: str = STATUS_DONE,
        detail: Optional[str] = None,
    ) -> Optional[PhaseEvent]:
        """
        Close *phase* in place: stamp the end and the MEASURED duration.

        :param phase: The phase to close (no-op when None or already closed).
        :param status: Final status.
        :param detail: Optional replacement of the detail line.
        :return: The same phase, for chaining.
        """
        if phase is None or phase.finished_at:
            return phase
        finished = time.time()
        phase.finished_at = _iso_ts(finished)
        started = _parse_iso(phase.started_at)
        if started is not None:
            phase.duration_ms = max(0.0, (finished - started) * 1000.0)
        phase.status = status
        if detail is not None:
            phase.detail = detail
        return phase

    def _close_stream_phase(self) -> None:
        """Close the streaming phase when it is still open."""
        self._close_phase(self._stream_phase)
        self._stream_phase = None

    def _close_open_phases(
        self, status: str = STATUS_DONE, detail: Optional[str] = None
    ) -> bool:
        """
        Close every phase still running (end of the run, end of a request).

        :param status: Final status to stamp.
        :param detail: Optional replacement of the detail line.
        :return: True when at least one phase was closed.
        """
        closed = False
        if self._open_llm is not None:
            self._close_phase(self._open_llm, status, detail)
            self._open_llm = None
            closed = True
        if self._stream_phase is not None:
            self._close_phase(self._stream_phase, status, detail)
            self._stream_phase = None
            closed = True
        return closed

    def _on_llm_request(self, event: AgentEvent) -> None:
        """
        Record that the turn is now WAITING for the model.

        This is the phase that separates "waiting 2 minutes for the AI" from
        "dead": from here until the first streamed chunk nothing else happens,
        and the phase stays ``running`` on disk, so the client can show a live
        counter for it instead of an empty timeline.

        :param event: The LLM_REQUEST agent event.
        """
        # A new request always follows the end of the previous answer.
        self._close_open_phases()
        payload = event.payload
        model = payload.get("model")
        message_count = payload.get("message_count")
        parts = []
        if model:
            parts.append(f"model {model}")
        if message_count:
            parts.append(f"{message_count} messages")
        self._open_llm = self._register_phase(
            "llm_request",
            status=STATUS_RUNNING,
            started_at=_iso_ts(event.ts),
            label="Waiting for the model",
            detail=" · ".join(parts) or "Waiting for the first token",
            meta={"model": model, "message_count": message_count},
        )
        logger.info(
            "ChatEventBridge: waiting for model (chat='%s', run='%s', model=%s)",
            self.chat.id,
            event.run_id,
            model,
        )
        self.publish()

    def _on_llm_chunk(self, event: AgentEvent) -> None:
        """
        First chunk of a request: close the wait and open the streaming phase.

        Chunks arrive many times per second, so this only does work ONCE per
        request; the rest stay in the log as plain progress.

        :param event: The LLM_CHUNK agent event.
        """
        if self._stream_phase is not None:
            return
        now = event.ts
        ttft_ms = None
        if self._open_llm is not None:
            started = _parse_iso(self._open_llm.started_at)
            if started is not None:
                ttft_ms = max(0.0, (now - started) * 1000.0)
            self._close_phase(self._open_llm, STATUS_DONE)
            self._open_llm = None
        detail = "Streaming the response"
        if ttft_ms is not None:
            detail = f"First token after {ttft_ms / 1000.0:.1f}s"
        self._stream_phase = self._register_phase(
            "llm_response",
            status=STATUS_RUNNING,
            started_at=_iso_ts(now),
            label="Reading the response",
            detail=detail,
            meta={"ttft_ms": round(ttft_ms) if ttft_ms is not None else None},
        )
        logger.info(
            "ChatEventBridge: model response started (chat='%s', ttft=%sms)",
            self.chat.id,
            round(ttft_ms) if ttft_ms is not None else "?",
        )
        self.publish()

    def _on_llm_usage(self, event: AgentEvent) -> None:
        """
        Attach token usage to the phase in flight (no persist: it is metadata).

        :param event: The LLM_USAGE agent event.
        """
        phase = self._stream_phase or self._open_llm
        if phase is None:
            return
        meta = dict(phase.meta or {})
        meta["prompt_tokens"] = int(event.payload.get("prompt_tokens") or 0)
        meta["completion_tokens"] = int(event.payload.get("completion_tokens") or 0)
        meta["total_tokens"] = meta["prompt_tokens"] + meta["completion_tokens"]
        phase.meta = meta

    def _on_tool_logs(self, event: AgentEvent) -> None:
        """
        Record a runtime notice (loop guard, course correction) as a phase.

        :param event: The TOOL_LOGS agent event.
        """
        payload = event.payload
        logs = payload.get("logs") or []
        text = "; ".join(str(item) for item in logs) if isinstance(logs, (list, tuple)) else str(logs)
        phase = self._register_phase(
            "notice",
            status=STATUS_DONE,
            started_at=_iso_ts(event.ts),
            label=str(payload.get("tool") or "Notice"),
            detail=_truncate(text, 400),
        )
        phase.finished_at = phase.started_at
        self.publish()

    def _on_wallet_check(self, event: AgentEvent) -> None:
        """
        Record the budget pre-flight that runs before the agent starts.

        :param event: The WALLET_CHECK agent event.
        """
        payload = event.payload
        phase = self._register_phase(
            "wallet_check",
            status=STATUS_DONE,
            started_at=_iso_ts(event.ts),
            label="Budget check",
            detail=f"balance {payload.get('balance')} · estimated {payload.get('estimated_cost')}",
            meta={
                "balance": payload.get("balance"),
                "estimated_cost": payload.get("estimated_cost"),
            },
        )
        phase.finished_at = phase.started_at
        self.publish()

    # ── Lifecycle events ───────────────────────────────────────────────────────

    def _on_lifecycle(self, event: AgentEvent) -> None:
        """
        Update (in place) the LifeCycleEvent of this run, then persist + stream.

        One LifeCycleEvent is kept per ``run_id``; recursive agent iterations
        that reuse the same bridge get one entry per run. RUN_ERROR /
        RUN_CANCELLED details are persisted the moment they arrive so no
        failure information is lost on a subsequent crash.
        """
        payload = event.payload
        lifecycle_event = self._lifecycle_events.get(event.run_id)
        if lifecycle_event is None:
            lifecycle_event = LifeCycleEvent(
                status=STATUS_RUNNING,
                run_id=event.run_id,
                started_at=_iso_ts(event.ts),
            )
            self._lifecycle_events[event.run_id] = lifecycle_event
            self.response_message.lifecycle_events.append(lifecycle_event)

        if event.type == AgentEventType.RUN_END:
            lifecycle_event.status = STATUS_DONE
            lifecycle_event.duration_ms = payload.get("duration_ms")
            lifecycle_event.finished_at = _iso_ts(event.ts)
            self._close_open_phases()
        elif event.type in (AgentEventType.RUN_ERROR, AgentEventType.RUN_CANCELLED):
            lifecycle_event.status = STATUS_ERROR
            lifecycle_event.duration_ms = payload.get("duration_ms")
            lifecycle_event.error = str(
                payload.get("error") or payload.get("reason") or "unknown error"
            )
            lifecycle_event.finished_at = _iso_ts(event.ts)
            # The run died with work in flight: close every open phase as an
            # error so the client never shows a wait that cannot be running.
            self._close_open_phases(
                status=STATUS_ERROR, detail=lifecycle_event.error
            )
            for open_tool in self.response_message.tool_events:
                if open_tool.status == STATUS_RUNNING:
                    open_tool.status = STATUS_ERROR
                    open_tool.error = open_tool.error or "Interrupted by a run error"
                    open_tool.finished_at = open_tool.finished_at or _iso_ts(event.ts)

        # Aggregated run analytics are supplementary metadata on the response.
        analytics = payload.get("analytics")
        if analytics is not None:
            meta = dict(self.response_message.meta_data or {})
            meta["analytics"] = analytics
            self.response_message.meta_data = meta

        logger.info(
            "ChatEventBridge: lifecycle '%s' status=%s (chat='%s', run='%s')",
            event.type,
            lifecycle_event.status,
            self.chat.id,
            event.run_id,
        )
        self.publish()

    # ── Persist + stream ───────────────────────────────────────────────────────

    def publish(self) -> None:
        """
        Persist the response message (crash-safe) and stream it to clients.

        Persistence is idempotent by ``doc_id``: the first call inserts the
        message via ``add_message``, subsequent calls update it in place via
        ``update_message``. The ChatEngine also calls this method on its own
        error / cancellation paths so failure information is written to disk
        the moment it is known.
        """
        self._persist_message(self.response_message)
        self._stream()

    def persist_message(self, message: Message) -> None:
        """
        ADDED: Crash-safe persistence of an AUXILIARY message.

        Used by the engine to persist hidden reasoning messages (and any other
        mid-run message) the moment they are produced instead of waiting for
        the end-of-turn chat save. Idempotent by ``doc_id`` and never raises.

        :param message: The auxiliary Message to persist immediately.
        """
        self._persist_message(message)

    def maybe_persist_stream(
        self,
        min_interval_seconds: float = STREAM_PERSIST_MIN_INTERVAL_SECONDS,
    ) -> bool:
        """
        ADDED: Throttled persist of the response message for streamed content.

        Intended to be called from streaming callbacks on every flush; the
        response message is written to disk AT MOST once per
        *min_interval_seconds* (measured with a monotonic clock across ALL
        persists, including event-driven ones). This bounds disk I/O while
        guaranteeing a hard kill loses at most *min_interval_seconds* of
        streamed partial text. Never raises.

        :param min_interval_seconds: Minimum seconds between persists.
        :return: True if a persist was performed, False if throttled.
        """
        now = time.monotonic()
        if now - self._last_persist_ts < min_interval_seconds:
            return False
        logger.debug(
            "ChatEventBridge: throttled stream persist for chat '%s'",
            self.chat.id,
        )
        self._persist_message(self.response_message)
        # Persisted content is progress: refresh the turn heartbeat.
        self._progress()
        return True

    def _persist_message(self, message: Message) -> None:
        """
        Write *message* to disk idempotently (never raises).

        Uses the granular merge-safe ChatManager methods when available.
        DEFENSIVE: if the manager lacks ``add_message`` / ``update_message``
        or they fail, falls back to :meth:`_fallback_save_chat` so no
        information is silently dropped.

        :param message: The message (response or auxiliary) to persist.
        """
        try:
            if message.doc_id not in self._persisted_doc_ids:
                self.chat_manager.add_message(chat=self.chat, message=message)
                self._persisted_doc_ids.add(message.doc_id)
            else:
                self.chat_manager.update_message(chat=self.chat, message=message)
            self._last_persist_ts = time.monotonic()
        except AttributeError as ex:
            logger.warning(
                "ChatEventBridge: chat_manager lacks granular persistence "
                "(add_message/update_message): %s — falling back to full "
                "chat save for message '%s' (chat '%s')",
                ex,
                message.doc_id,
                self.chat.id,
            )
            self._fallback_save_chat(message)
        except (OSError, ValueError, TypeError, RuntimeError) as ex:
            logger.exception(
                "ChatEventBridge: error persisting message '%s' for chat "
                "'%s': %s — attempting full chat save fallback",
                message.doc_id,
                self.chat.id,
                ex,
            )
            self._fallback_save_chat(message)

    def _fallback_save_chat(self, message: Message) -> None:
        """
        DEFENSIVE fallback: persist the full chat via ``save_chat``.

        Ensures *message* is present on the in-memory chat before the coarse
        write so the fallback still captures it. Never raises.

        :param message: The message that triggered the fallback.
        """
        try:
            save_chat = getattr(self.chat_manager, "save_chat", None)
            if save_chat is None:
                logger.error(
                    "ChatEventBridge: no persistence method available on "
                    "chat_manager for chat '%s' — message '%s' NOT persisted",
                    self.chat.id,
                    message.doc_id,
                )
                return
            if not any(m.doc_id == message.doc_id for m in self.chat.messages):
                self.chat.messages.append(message)
            save_chat(chat=self.chat)
            self._persisted_doc_ids.add(message.doc_id)
            self._last_persist_ts = time.monotonic()
            logger.info(
                "ChatEventBridge: fallback full chat save succeeded for "
                "chat '%s' (message '%s')",
                self.chat.id,
                message.doc_id,
            )
        except (OSError, ValueError, TypeError, RuntimeError) as ex:
            logger.exception(
                "ChatEventBridge: fallback save_chat failed for chat '%s': %s",
                self.chat.id,
                ex,
            )

    def _stream(self) -> None:
        """Stream the current response message state to clients (never raises)."""
        try:
            self.event_manager.message_event(
                chat=self.chat, message=self.response_message
            )
        except (OSError, ValueError, TypeError, RuntimeError) as ex:
            logger.exception(
                "ChatEventBridge: error streaming response message '%s' "
                "for chat '%s': %s",
                self.response_message.doc_id,
                self.chat.id,
                ex,
            )

# Made with ❤️ by codx-junior