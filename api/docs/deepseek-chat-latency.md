# Why DeepSeek chats were slow (and what changed)

> **Symptom.** A chat turn in codx-junior took tens of minutes while the same
> model answered quickly in the DeepSeek Harness (DSH). The model itself was
> not the only problem: the app repeatedly did seconds of blocking work
> *around* each model round.

This document records the measurements, the errors found, and the fixes. It is
the reference for the next investigation: the numbers below are from a live
turn (`chat 144f12b5`, 2026-11-16) and can be reproduced with the commands at
the end.

---

## 1. Executive summary

| Finding | Measured cost | Fix |
| --- | ---: | --- |
| CISO redaction scanned screenshot `data:image/png;base64,…` bodies on **every** chat save | **26.8 s per save** (17 screenshots ≈ 2.5 MB) | skip inline binary bodies; cache redaction results by digest |
| The same chat was written on every stream flush and every tool event (4.5 MB JSON + `fsync`) | ~0.1 s write, but **+27 s redaction** each time | the redaction fix brings a repeat save to **~0.4 s** |
| `OpenAI` (sync SDK) was called and iterated inside `async def` | the chat event loop was blocked for the whole response (27 s/round) | run the request and each stream read in a worker; async stream path |
| No transport timeout | a silent stream could hang for the SDK default (10 min, 2 retries) | explicit connect/read/write/pool timeouts + configurable retries |
| Tool results were unbounded and replayed on every round | one result was **121,405 chars**; a prompt reached **160,655 tokens** | Harness-style prune: head 4k + marker + tail 1k over 8k |
| No reasoning-effort control | every DeepSeek round used the provider default (thinking on) | model/provider/env `reasoning_effort` + `thinking` fields |
| Project discovery scan could run for 88 s and saturate I/O | background load slowed every request | bounded scan already exists; see “remaining work” |

The two biggest wins are the **CISO redaction** and the **non-blocking
stream**. Both were implementation errors, not model latency.

---

## 2. Evidence from the live turn

`SmolAgent` timing lines (one per model round):

```
LLM timing model=deepseek-flash request_ms=491 first_token_ms=27438
           stream_ms=32 total_ms=27470 chunks=113 chars=83 tool_calls=2
LLM timing model=deepseek-flash request_ms=538 first_token_ms=27852
           stream_ms=16 total_ms=27868 chunks=69 chars=0 tool_calls=2
LLM timing model=deepseek-flash request_ms=500 first_token_ms=29002
           stream_ms=96 total_ms=29098 chunks=127 chars=0 tool_calls=2
```

The provider was contacted in ~0.5 s and then thought for ~27 s. That part is
normal for a hard browser task. What made the turn unusable was everything
that happened **between** the rounds:

* every tool event called `ChatEventBridge.publish()` →
  `ChatManager.save_chat()` → `store_chat()` →
  `redact_payload(chat.model_dump())`;
* `redact_payload` scoured the full 4.5 MB task card, including 17 embedded
  screenshots, on every save;
* measured `redact_payload` on that card: **26.8 s**, of which **22.8 s** was
  the 17 screenshot strings alone;
* the same card was saved again on the next round, so a 3-round turn paid the
  cost several times.

The logs show the resulting gaps: `tool_start` at `17:42:43`, the tool actually
starting at `17:43:10` (27 s later), `project_locate` starting at `17:43:37`
and executing at `17:44:05` (28 s later). Those are not provider waits.

### DSH comparison

| Concern | DeepSeek Harness | codx-junior (before) | codx-junior (now) |
| --- | --- | --- | --- |
| Transport | async `fetch`, SSE, idle watchdog (`streamIdleTimeoutMs=300000`) | sync `OpenAI` client inside `async def`, no timeout | request + reads in a worker thread, 300 s idle read timeout, 1 retry |
| Stream consumption | async iterator, cancellation on every chunk | blocking `for chunk in stream` | async per-chunk read, cancellation/progress on every chunk |
| Tool results | `tool-result-pruner`: 8,192 chars = 4,096 head + marker + 1,024 tail | full result, up to 121k chars | same 8k head/tail policy |
| History pressure | `compaction-basic` at 80 % of context, 16 % retained | none | tool-result pruning (history compaction is next) |
| Reasoning control | `thinking: enabled/disabled` + `output_config.effort` (off/low/high/max) | ignored | `thinking` + `reasoning_effort` (model/provider/env) |
| Persistence | event log, compacted | rewrite + re-redact whole 4.5 MB card per event | cache + binary skip; card write remains |

---

## 3. Fixes

### 3.1 CISO redaction: skip binary bodies, cache text

`codx/junior/security/ciso/secrets.py`

* `_DATA_URL_PATTERN` splits `data:<mime>;base64,…` bodies out of the scanned
  text. The surrounding text is still scanned; the binary body is copied
  byte-for-byte.
* `redact_text` caches the result of the default policy by BLAKE2b digest.
  The cache is bounded (64 MB by default,
  `CODX_JUNIOR_CISO_REDACT_CACHE_CHARS`) and only applies to strings ≥256
  chars.
* `_redact_text_scan` no longer constructs a `CisoPolicy` per string (that
  alone cost ~1 s on the example card).

`codx/junior/security/ciso/payload.py`

* keys ending in `base64` (`image_base64`, `audio_base64`, …) are treated as
  binary;
* an oversized string is still skipped **unless** it embeds a `data:` URL:
  in that case only the text around the binary is scanned, so a screenshot
  cannot shadow a secret sitting next to it in the same tool output.

Result on the live 4.5 MB card:

```
first save:  3.86 s   (was 26.8 s)
next saves:  0.42 s   (cache hits; was 26.8 s every time)
```

### 3.2 Non-blocking model stream

`codx/junior/ai/smol/smol_agent.py`

* `_create_completion` and every `next(iterator)` run through
  `asyncio.to_thread`, so the event loop keeps serving Socket.IO, HTTP and
  cancellation while the provider is contacted/thinks.
* `_iterate_response_stream` also accepts an async iterable, so switching to
  `AsyncOpenAI` later needs no caller change.
* `AgentRunContext.guard_chunk` keeps the cancellation checkpoint and the
  throttled `LLM_CHUNK` event working for the async path.

### 3.3 Transport limits

The client is created with an `httpx.Timeout`:

| Phase | Default | Environment |
| --- | ---: | --- |
| connect | 15 s | `CODX_JUNIOR_LLM_CONNECT_TIMEOUT_SECONDS` |
| read (idle) | 300 s | `CODX_JUNIOR_LLM_READ_TIMEOUT_SECONDS` |
| write | 60 s | `CODX_JUNIOR_LLM_WRITE_TIMEOUT_SECONDS` |
| pool | 30 s | `CODX_JUNIOR_LLM_POOL_TIMEOUT_SECONDS` |
| retries | 1 | `CODX_JUNIOR_LLM_MAX_RETRIES` |

The read timeout is an **idle** timeout: a long thinking phase is allowed, a
silent connection is not.

### 3.4 Tool-result pruning (Harness parity)

`messages.make_tool_message` now passes results through
`prune_tool_result_for_model`:

```
8192 default budget = 4096 head + marker + 1024 tail
CODX_JUNIOR_TOOL_RESULT_MAX_CHARS / _HEAD_CHARS / _TAIL_CHARS
```

The full output stays in the persisted `tool_events` (the UI renders it); only
the model-facing copy is pruned. This is exactly what DSH's
`dsh-compaction-tool-result-pruner` does.

### 3.5 Reasoning effort / thinking mode

`AISettings` (and the model/provider settings in `ai_models.json` /
`ai_providers.json`) now accept:

```json
{
  "reasoning_effort": "low",
  "thinking": "enabled"
}
```

Resolution: **model > provider > environment > provider default**.

```bash
# Global fallback (useful for a fast chat deployment)
export CODX_JUNIOR_REASONING_EFFORT=low
# or disable thinking for tool-heavy interactive chats
export CODX_JUNIOR_THINKING=disabled
```

DeepSeek receives `thinking: {"type": "enabled"|"disabled"}` in `extra_body`
and `reasoning_effort` when set. Other OpenAI-compatible providers only get
`reasoning_effort`. Allowed DeepSeek efforts: `off`, `low`, `high`, `max`;
`off` (or `thinking: disabled`) removes the effort field and disables thinking.

> **Tip.** For interactive chats, `reasoning_effort: "low"` is the single
> biggest model-side latency lever. Keep `high`/`max` for `codx-junior-max`
> or orchestration work.

### 3.6 Task effort: profiles own the budget

`AIProvider`/`AIModel` no longer carry `max_tool_calls`/`max_iterations`.
Those are **agent policy**, not model capabilities, and having them in two
places is what produced the "provider says 80, the turn stopped at 10"
confusion. The single source of truth is the profile's `effort` block.

The active profiles are the effort selectors:

| Active profile | Tier | Default rounds | Default calls/round | Continuations |
| --- | --- | ---: | ---: | --- |
| none of the harness profiles | `default` | 10 | 5 | **0** |
| `codx-junior` | `agile` | 10 | 5 | 2 × (6 rounds, 3 calls) |
| `codx-junior-max` | `max` | 24 | 6 | 2 × (6 rounds, 3 calls) |

A profile can override any of those defaults with an `effort` block:

```json
"effort": {
  "max_iterations": 80,
  "max_tool_calls": 12,
  "continuations": 2,
  "continuation_max_iterations": 6,
  "continuation_max_calls": 3,
  "wall_clock_seconds": 1200,
  "target_seconds": 3600,
  "validation": "targeted+qa_review_changes"
}
```

Resolution:

1. `effort_for(profile_names, ...)` picks the tier from the harness profiles.
2. `ChatEngine._resolve_effort_budget` merges the `effort` blocks of the
   active profiles (the profile with the biggest round budget wins when
   several carry one) and sends the result in the internal `effort` header.
3. `SmolAgent` applies it over the tier defaults. The tier constants are the
   **fallback**; there is no model/provider budget anymore.

**Task/chat level** — add the profile to the task's `profiles` array. The task
card is `<project>/.codx/tasks/<board>/<column>/<slug>.<chat_id>.json`:

```json
"profiles": ["JobSeeker", "browser", "codx-junior-max"]
```

Use the profile picker in the UI for the same effect. `codx-junior-max`
links `codx-junior`, so adding max is enough.

**Per message** — mention the profile in the message; the chat engine converts
the chat profiles and the query mentions into the same list:

```text
@codx-junior-max review the LinkedIn offers and open the best three
```

**Fine-tuning** — edit the `effort` block of the profile. To tune per project
without touching the shipped file, copy the profile folder to
`<project>/.codx/profiles/<name>/<name>.profile`; project profiles override
the built-in ones. The AI Provider/Model settings no longer need the old
"Max Tool Calls"/"Max Iterations" fields; the client form removal is a
follow-up outside the API workspace.

`reasoning_effort` / `thinking` stay on the model: how long the model thinks
**inside one round** is a model capability, not a task budget.

When the guard stops a turn, the answer now starts with a deterministic notice
naming the effective limit (from the profile) and how to raise it, before the
tool-usage summary.

---

## 4. What is still slow (next work)

These are measured but intentionally not changed here:

1. **99 tool schemas.** A `codx-junior` / `"*"` profile sends all of them:
   138,537 chars (~35k tokens) on every round. DSH ships a much smaller tool
   surface. The `effort.tool_allowlist` field is documented as the future
   lever but is not applied yet.
2. **No history compaction.** Old tool results are still part of the card and
   the prompt. DSH compacts at 80 % of the context window. A bounded
   `conversation_history` renderer (or the DSH-style tool-result pruner applied
   to the history as well) is the next step.
3. **Whole-card persistence and analytics archive.** The card is a 4.5 MB JSON
   file and is rewritten on every event. With redaction cached the write is
   ~0.4 s, but a per-message store (or append-only log) would remove the
   remaining cost. The analytics archive also stores the full prompt and the
   99 tool schemas per round (`/home/codx-junior/analytics` was 5.0 GB in
   30,209 files); truncating the forensic copy would cut disk I/O and make
   `/api/analytics/me` fast again.
4. **Project discovery.** `_update_all_projects` was measured at 88.75 s in one
   sample. The discovery TTL is now 10 min (was 2 min), so that scan cannot run
   almost continuously; verify `CODX_JUNIOR_PROJECT_SCAN_ROOTS` covers only the
   project folder on slow bind mounts. A project created through the API is
   inserted into the snapshot immediately, so the longer TTL does not delay it.
5. **Stale browser process.** The live turn logged
   `'BrowserSession' object has no attribute 'user_is_typing'`; the container
   had been started before `browser/model.py` was updated. Restart the API
   process after deploying these changes so the browser runtime matches the
   code.

---

## 5. How to reproduce / verify

```bash
# 1. Redaction cost on a real card (before/after)
docker exec codx-junior-api sh -c '
  cd /home/codx-junior/codx-junior/api &&
  /home/codx-junior/.codx-junior/.venv_codx_junior_api/bin/python - <<"PY"
import json, time
from codx.junior.security.ciso.payload import redact_payload
from codx.junior.security.ciso.secrets import clear_redaction_cache
p = "/home/codx-junior-projects/.../help-me-find-a-job.<id>.json"
d = json.load(open(p))
clear_redaction_cache()
for label in ("first", "second"):
    t = time.monotonic(); redact_payload(d)
    print(label, round(time.monotonic() - t, 3), "s")
PY'

# 2. Regression tests
docker exec codx-junior-api sh -c '
  cd /home/codx-junior/codx-junior/api &&
  /home/codx-junior/.codx-junior/.venv_codx_junior_api/bin/python -m pytest \
    tests/ai/smol/test_model_transport.py \
    tests/security/test_redaction_performance.py -q'

# 3. Live timing lines
docker logs --since 15m codx-junior-api 2>&1 |
  grep -E "SmolAgent: LLM timing|model response started" | tail
```

The timing line already separates the phases, so a future regression shows up
as `request_ms`, `first_token_ms` or `stream_ms` growing, not as a vague
“the chat is slow”.

Made with ❤️ by codx-junior
