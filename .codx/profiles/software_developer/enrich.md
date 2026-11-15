## Local dev stack: API does not hot-reload Python

The local codx-junior stack runs the API **without** `--reload` (`scripts/run_api.sh`: DEBUG=1 only raises logging; `CODX_JUNIOR_API_RELOAD=1` is needed for reload). The client container (`codx-junior-client`, vite dev) **does** hot-reload, so UI changes appear immediately while backend changes stay inactive until the API is restarted.

- Backend edits need: `docker restart codx-junior-api codx-junior-api-background`.
- The agent runs **inside** `codx-junior-api`, so never restart it during a turn; ask the user or note it.
- Verify a new query param is loaded without auth: call it with an invalid bool, e.g. `/api/git/repo/validate/context?staged=notabool`. `422` = param exists in the loaded route, `400` ("CODXJuniorSession not found") = param unknown → stale code.
- The client container mounts `/home/codx-junior/codx-junior`; it serves the same tree as the project (`/home/codx-junior-projects/codx-junior`), so a running client does not prove the API has the change.

## Chat turn execution and cancellation model

A manual chat turn runs ON the API event loop: `/api/chats` (`api/codx/junior/api/chat.py`) is `async def` and awaits `chat_with_project`. The provider call uses the SYNC `OpenAI` client (`ai/smol/smol_agent.py`, `stream=True`) consumed with a blocking `for chunk in run_context.guard_stream(...)` and no `await` inside the loop, so the loop is blocked while streaming.

Consequences to remember:
- Socket events still flow because `sio/session_channel.py` queues them and flushes from a separate thread (`asyncio.run(sio.emit(...))`).
- `async def` routes (e.g. `POST /api/chat/cancel`) cannot be served while a turn is streaming; plain `def` routes run in the threadpool and stay responsive.
- Cancellation is cooperative only: `checkpoint()` per streamed chunk and before/after each tool (`engine/agent_runtime.py`). Sync tools run in daemon threads and cannot be interrupted. Cross-process stop uses a `<turn>.cancel` file marker polled by `TurnCancellationWatcher` (1 s).
- Chat turn recovery (`chat/turn_recovery.py`) restarts turns as new records; `finalize` deletes the record file, so any new terminal transition must stay idempotent.
