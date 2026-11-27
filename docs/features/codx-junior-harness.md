# codx-junior harness — the @codx-junior profile

> **Status:** implemented · **Profile:** `codx-junior` (built-in) ·
> **Code:** `api/codx/junior/harness/`, `api/codx/junior/orchestration/` ·
> **API:** `/api/harness/*`, `/api/orchestration` ·
> **UI:** Harness mode (sidebar → *Harness*) and Harness diagnostics

The **codx-junior harness** is the native, self-contained way to hand a whole
task to the platform and get notified when it is done. It is modeled on how a
modern AI harness works (durable runs, background workers, notifications,
audit trail) but it is built entirely inside codx-junior: no external harness,
no bridge, nothing to install.

## The @codx-junior profile

`codx-junior` is a **built-in profile** — the main contact between the user and
every other profile and capability of the platform. It is granted **all tools**
(the profile allowlist contains the `*` marker, expanded to the whole tool
registry at request time) and its `team` lists the profiles it can put to work:
`software_developer`, `analyst`, `reviewer`, `TaskManager`, `ProjectManager`,
`devops`, `repository`, `web_master`, `workspace`, `browser`, `wiki`,
`teacher` and `support`.

### Give it a task

1. Open any chat and write `@codx-junior <your task>`.
2. The harness profile registers the request (audited as `task_requested`) and
   decides how to run it:
   - quick, independent questions are answered directly or through a `delegate`
     card,
   - everything non-trivial becomes a **run** via
     `orchestrator(action="start")` with `profiles=["codx-junior"]`, so the
     harness profile itself supervises the work.
3. The background worker plans the steps and executes them **in parallel,
   isolated task cards**, each with the best profile for the job.
4. The user can watch every card live on the board and in the run sprint.
5. When the run finishes (or fails/stops/burns its budget), codx-junior
   publishes the **delivery report on the task** and sends the user a
   **notification**: *"Run X finished (completed). The delivery report is on
   the task."* The notification is sent exactly once per terminal status, even
   across worker restarts.

## Harness mode (the session workspace)

**Harness mode** is the full-screen session workspace of the project. It is the
surface a ticket is written on and the place its result is read; it is a lens
over the existing task chat, not a second agent engine.

```
┌───────────────────────┬─────────────────────────────────────────────────┐
│ codx-junior  HARNESS  │  Install failures register a support ticket     │
│ + New Session         │  Completed · 13m 41s · 22.4k tokens · 8 tools  │
│                       │  ┌ Ticket ───────────────────────────────────┐  │
│ WORKSPACES            │  │ When an installation fails, register…     │  │
│  ▸ codx-junior        │  └───────────────────────────────────────────┘  │
│                       │  ▸ Read files, searched the code, edited…      │
│ SESSIONS              │                                                 │
│  ● Add project…   2m  │  ## Problem                                     │
│  ● Install failures…  │  ## Root cause                                  │
│                       │  ## What I changed                              │
│                       │  ## Tests / Verification                        │
│                       │  ┌ 7 files +361 -12 ─ codx/task/… ──────────┐  │
│ Diagnostics · Settings│  └───────────────────────────────────────────┘  │
│                       │  Message or run a task…                         │
└───────────────────────┴─────────────────────────────────────────────────┘
```

### Start a session

1. Sidebar → **Harness**.
2. Keep or change the **workspace** (the project the session works on).
3. Choose the tier:
   - **Standard mode** — `codx-junior`, high effort, one turn;
   - **Deep work** — `codx-junior-max`, max effort, long multi-batch work.
4. Write the ticket: what is broken, the expected behaviour and the acceptance
   criteria. A model can also be picked in the composer.
5. The session opens immediately. The ticket card, the live activity and the
   final report are the same conversation: tool calls and text stream in place.

The left rail lists the sessions of the project, newest first; a green dot is a
running turn, a red dot a failed one and a grey dot a finished one. The list
keeps the last activity time, so “2h” means the last thing the harness wrote.

### What a session does

A session is a normal task chat executed by the `codx-junior` (or
`codx-junior-max`) profile in `task` mode. The harness contract is appended to the **first user message**
(`api/codx/junior/harness/prompts.py`), so the same agent loop becomes a coding
harness without a second engine:

| Step | What the agent does | Evidence stored |
| --- | --- | --- |
| **Survey** | `project_structure`, `project_search`, `project_grep`, `project_glob`, `project_read_file` | one `tool_event` per call |
| **Reproduce** | reads the failing path/tests, runs the narrowest check | command + output in the event |
| **Analyse** | names the root cause and the rejected alternatives | `## Root cause` in the report |
| **Implement** | writes every layer the change needs | `project_write_file` / `apply_file_changes` |
| **Test** | adds/updates tests, runs `qa_run_tests`, sets up a service with the Docker tools when needed | test files + run output |
| **Verify** | `qa_review_changes`, `security_review_changes` when relevant | verdict + evidence |
| **Report** | the platform delivery contract: `## What happened` (problem + reproduction), `## What I did` (root cause + files), `## Demo` (tests + evidence), `## Next tasks` | the assistant message |

The turn runs through the **same chat engine/call** (`chat_with_project`) as
every other chat: streaming events, cancellation, crash recovery, budgets and
the CISO gate are shared. Nothing in harness mode bypasses the normal execution
path; `POST /api/harness/sessions` schedules it in the background so a closed
tab cannot leave a ticket idle.

### Read a session

* **Transcript** — the conversation uses the same `ChatMessageList` +
  `ChatEntry` components as every chat, adapted to harness mode: every message
  is visible (task-mode cleanup never archives a harness turn), the appended
  harness contract is stripped from the ticket, and images/attachments,
  tool events and the turn timeline are the standard ones.
* **Activity** — the turn timeline summarizes what happened
  (`Read files, searched the code, edited files, ran tests`); expand it to see
  every tool call, its arguments, duration, status and output.
* **Report** — the assistant messages are rendered as Markdown. The last one
  carries the delivery contract; the report can be copied from the caption.
* **Continue** — suggested replies emitted by the model (the `options` block)
  stay clickable on every assistant turn, so continuing the task never depends
  on retyping it. The effort tier (Standard / Deep work) is selectable in the
  session header while the session runs; the next follow-up uses it.
* **Files changed** — the branch diff of the task worktree
  (`Edited 7 files +361 -12`), with one row per file. The branch name is shown,
  so the change can be reviewed/merged in the Git view. The rows are a summary,
  not a main-checkout file link: the worktree holds the delivered version.
* **Session log** — the audit entries of this session (`task_requested`,
  run/step events, notifications).
* **Follow-up** — the composer at the bottom appends a message to the same
  session and starts the next turn; the profile, the task worktree and the
  whole conversation are kept. Pasted screenshots and uploaded files use the
  standard chat attachment pipeline and are stored on the user message.
* **Task / Git / Diagnostics** — the header opens the card with the full chat
  UI, the Git view directly on the **Review** tab comparing the task branch
  with the project's active branch, or the read-only harness diagnostics.

The session branch is a normal task worktree: the main checkout is never
modified by the harness. Review it in **Git** and merge it as any other task.
When the project is not a git repository (or task-workspace isolation is
disabled), the turn falls back to the project checkout and the files block
reports the files the session wrote.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/harness/sessions` | List the sessions of the project (phase, timings, cost, preview). |
| `POST` | `/api/harness/sessions` | Create a session (`prompt`, optional `name`, `profiles`, `model`, `budget_cxjcoins`, `start` default true). |
| `GET` | `/api/harness/sessions/{id}` | Session detail: chat, metrics, worktree, diff/files, run, audit. |
| `POST` | `/api/harness/sessions/{id}/prompt` | Append a follow-up and schedule its turn (`start=false` disables). |

The create/prompt call persists the chat and **schedules the turn in the
background** with the same `CODXJuniorSession.chat_with_project` the chat socket
uses (`start=false` disables it for tests/tools). The HTTP response does not
wait for the model: the client follows the streamed chat events. This is what
keeps harness sessions crash-recoverable, visible on the board and immune to a
closed tab, without a second runtime.

### Models

The harness uses the project's configured model (any OpenAI-compatible provider,
including DeepSeek). The model is chosen in the composer or inherited from the
profile; `DeepSeek-V4.1-Flash` and the other DeepSeek models work through the
existing OpenAI-compatible route, so no additional SDK is required. A provider
that is not OpenAI-compatible can still be used through its own adapter in
`api/codx/junior/ai/` without changing the session contract.

## Observability: navigate and review everything

- **Every step is a task card.** Open a card to read the worker conversation,
  move it, retry it or take over manually.
- **The run sprint** groups all the cards of a run by status.
- **The Harness app** (sidebar → *Harness*) shows, per project:
  - the worker heartbeat and watchdog state (restarts, incidents),
  - every run with status, progress, board/sprint, budget and a one-click
    *Open task* link to the run dashboard,
  - the full **audit journal** (see below), filterable by event type.

## Audit: all work is registered

Everything the harness does is appended to an immutable journal at
`<project>/.codx/harness/audit.jsonl` (one JSON object per line, atomic
appends):

| Event | Meaning |
| --- | --- |
| `task_requested` | the user gave `@codx-junior` a task (chat, user, request text) |
| `run_event` | every run transition: `run_created`, `run_started`, `step_started`, `step_finished`, `step_retried`, `run_completed`, `run_failed`, `run_stopped`, `run_budget_exhausted`, `run_stalled`, `report`, … with run id, status and message |
| `notification_sent` | the user was notified of a terminal status |
| `turn_resumed` / `turn_recovered` | an interrupted chat turn was resumed by the recovery worker (attempt n/m) and completed |
| `turn_failed` / `turn_cancelled` | an interrupted turn was given up after the retry bound (the user is notified), or stopped by the user |
| `worker_recovered` | the background worker (re)started and took over a project |
| `worker_stalled` | the watchdog detected a dead/wedged worker with active runs |
| `worker_restarted` / `worker_restart_failed` | supervision actions and their outcome |

The journal is read-only through the API (`GET /api/harness/audit`); writing
never raises, so auditing can never break the work it records.

## Restart: processes survive errors and interruptions

Three layers guarantee the harness comes back on its own:

1. **Durable state.** Runs, steps, budgets and reports are persisted on disk
   with atomic writes; run locks are OS `flock`s released on process death; the
   worker tick recovers steps whose owner died. A restart resumes in-flight
   work instead of losing it.
2. **Process supervision.** `supervisor.conf` (monolithic deployment) runs
   `codx-junior-api`, `codx-junior-api-background` and `codx-junior-client`
   with `autorestart=true` and bounded retries. The Docker Compose deployment
   runs the same three services with `restart: unless-stopped`.
3. **Harness watchdog.** Runs inside the API process and monitors the durable
   worker heartbeat: when a project has **active runs but no live worker**, it
   registers a `worker_stalled` audit entry, notifies connected users and
   restarts `codx-junior-api-background` (`supervisorctl` in the monolith,
   `docker restart` in Compose), with a per-project cooldown so a storm of
   restarts is impossible.

Environment switches: `CODX_JUNIOR_HARNESS_WATCHDOG` (on by default),
`CODX_JUNIOR_HARNESS_WATCHDOG_SECONDS`, `CODX_JUNIOR_HARNESS_WATCHDOG_STALE_SECONDS`,
`CODX_JUNIOR_HARNESS_WATCHDOG_RESTART`.

### Never let the API restart itself while it works

The API must not run with source reload on a server where the AI agents edit
the project: `uvicorn --reload` restarts the process on every file the agent
writes, which interrupts the in-flight turn it is itself executing (and the
recovery worker then has to retry it). Reload is therefore **opt-in**:
`CODX_JUNIOR_API_RELOAD=1` (development machines only). `DEBUG=1` only raises
verbosity. Restarts are owned by `supervisor.conf` / Docker `restart:
unless-stopped`, and interrupted turns are resumed by the recovery worker
(3 attempts, audited and notified).

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/harness/audit?limit&offset&event` | read the audit journal (newest first) |
| `GET` | `/api/harness/status` | worker presence, active runs, watchdog state |
| `GET` | `/api/harness/sessions` | harness mode session list |
| `POST` | `/api/harness/sessions` | create a harness session |
| `GET` | `/api/harness/sessions/{id}` | session detail (chat, diff, files, audit) |
| `POST` | `/api/harness/sessions/{id}/prompt` | append a follow-up message |
| `GET` | `/api/orchestration` | list runs (the Harness UI table) |
| `POST` | `/api/orchestration/{run_id}/tick` | wake the worker (admin) |

## Related docs

- [`orchestration.md`](../../api/docs/orchestration.md) — the board-native run
  runtime underneath the harness.
- [`profiles.md`](profiles.md) — how profiles and tool allowlists work.
