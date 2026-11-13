# Autonomous agents (actors)

An **actor** is a profile with initiative. It is a normal profile — same instructions, same
tools, same knowledge, same model — plus a declaration that tells codx-junior **when** it works,
**where** it works and **how much** it may spend.

Use actors for the jobs nobody wants to remember:

* take care of the maintenance tickets of the week;
* keep the agenda of a team in order;
* check a dependency, a certificate or a deployment again in two days;
* review a board every morning and report.

::: tip One runtime, two roles
There is no separate "board manager" feature any more. A **manager** that curates a board and a
**coworker** that owns a job are the same actor with a different role, tool policy and session
shape (see [Roles](#roles-manager-and-coworker)).
:::

## Declare an actor

The declaration lives **inside the profile** (`.codx/profiles/<Name>/<Name>.profile`), so it is
versioned with everything else the profile carries and it is reviewed like any other change.

```json
{
  "name": "ciso",
  "description": "Takes care of the security posture of the project.",
  "actor": {
    "enabled": true,
    "role": "coworker",
    "policy": "full",
    "session": "until_done",
    "boards": ["Security"],
    "board": "Agent: CISO",
    "interval_minutes": 1440,
    "daily_budget_cxjcoins": 20,
    "budget_cxjcoins": 5,
    "max_turns": 6,
    "max_agenda": 10,
    "instructions": "Focus on dependencies with known CVEs and on exposed secrets.",
    "schedule": {
      "enabled": true,
      "timezone": "Europe/Madrid",
      "windows": [{ "days": ["mon", "tue", "wed", "thu", "fri"], "start": "09:00", "end": "18:00" }]
    }
  }
}
```

| Field | Description |
| --- | --- |
| `enabled` | Turns the profile into an actor. |
| `role` | `coworker` (owns a job end to end) or `manager` (curates the boards it is responsible for). |
| `policy` | `full` (every tool of the profile) or `board_only` (boards and tasks only, never code). |
| `session` | `single` (one turn per shift) or `until_done` (keeps turning while work remains, bounded by `max_turns`). |
| `boards` | Boards the actor is **responsible for**: their tasks are its agenda. |
| `board` | **Desk** board where the shift reports are written. Defaults to the first watched board, or `Agent: <name>` (created on demand). |
| `interval_minutes` | Fallback cadence when nothing is scheduled (5 minutes – 30 days). |
| `mode` | `active` executes the tools, `dry_run` only reports what it *would* do. |
| `budget_cxjcoins` | Cap for one shift (`0` = project/chat default). |
| `daily_budget_cxjcoins` | Cap for one day across shifts (`0` = no cap). This is what makes "work until the job is done" safe. |
| `max_runtime_minutes` | Hard limit of one shift (default 30, maximum 180). |
| `max_turns` | Upper bound of turns inside an `until_done` shift. |
| `max_agenda` / `max_tasks` | How many due tasks the shift must handle / how many board tasks are given as context. |
| `include_done` | Whether closed tasks are also included as context. |
| `instructions` | Standing orders: the mandate of the actor, appended to the profile instructions. |
| `wake_at` | Next planned shift (ISO instant or an expression such as `+2d`). Empty derives it from the cadence. |
| `notify` | Comment the shift report on the desk board. |
| `schedule` | Weekly availability window (see [Quiet hours](#quiet-hours-a-weekly-schedule)). |

The same declaration can be written from a chat:

> Enable the actor for the profile `ciso`, let it work weekdays from 09:00 to 18:00 in
> Europe/Madrid, five cxjcoins per shift and twenty per day, and focus it on CVEs.

Becomes `autonomy_management(action="configure", profile_name="ciso", ...)`; the tool also
accepts `enable`, `disable`, `wake`, `schedule`, `run`, `status` and `list`.

## A shift

A **shift** is one bounded unit of autonomous work:

1. the runtime builds the agenda — the tasks of the actor boards whose `wake_at` is due;
2. it creates a **shift card** on the desk board and runs the first turn on it;
3. with `session: "until_done"` it keeps turning while work remains, until the agenda is empty,
   the per-shift budget, the daily budget or the clock runs out;
4. it writes the report on the shift card and moves it to `Done`;
5. it consumes the `wake_at` of the tasks it handled (a task the actor rescheduled for later
   during the shift keeps its new wake), and records the cost.

The desk board is created on first wake with four columns:

| Column | Meaning |
| --- | --- |
| `Inbox` | Work that arrived without a schedule. |
| `Scheduled` | Tasks with a future `wake_at`. |
| `Working` | The card of the shift in progress. |
| `Done` | Finished shifts and tasks. |

If the process dies mid-shift the card stays in `Working`; after `STALE_SHIFT_SECONDS` (15
minutes) the next shift may run, so a crash never blocks an actor forever.

## Scheduling: `wake_at`

`wake_at` is the alarm clock of a task (a *deadline* is `due_date`). Any task can carry one, and
the actor responsible for its board is woken when it becomes due:

```python
project_manager(action="create_task", name="Check the TLS certificate",
                description="Renew it if it expires before the next check.",
                board="Security", column="Scheduled", wake_at="+2d")
```

Accepted expressions are relative (`+2d`, `6 hours`, `2 weeks`, `in 90m`), a date (`2026-04-03`,
that day at 00:00 UTC) or an ISO instant. `project_manager(action="update_task", wake_at="")`
clears it.

This is the mechanism behind "an actor decides by itself what to do next": the prompt requires
every follow-up to be written as a task with `wake_at`, because an unscheduled follow-up is a
lost follow-up.

## Quiet hours: a weekly schedule

Actors are powerful and can be wrong. A schedule is how a team keeps them out of the hours where
**nobody is online to review their work**.

* `enabled: false` (default) means always available — what an agent needs to catch up after a
  downtime.
* When enabled, a shift only starts inside a window; a due task that falls outside is **kept**
  (its `wake_at` is not consumed) and handled at the next opening. `next_opening()` is what the
  UI shows as "next wake".
* `end <= start` is an **overnight** window (`22:00`–`06:00` is the night shift, owned by the
  listed days).
* Windows are read in `timezone` (IANA name, `Europe/Madrid`), never in server time.
* An **enabled schedule without windows** means *paused*.
* A **manual** wake (`POST /actors/{name}/wake`) always wins over the schedule: it is an explicit
  human decision.

An operator who needs everything to run right now can bypass the windows globally with
`CODX_JUNIOR_ACTORS_SCHEDULE=false`.

## Roles: manager and coworker

| | `role: "manager"` | `role: "coworker"` |
| --- | --- | --- |
| Sees the board as | the queue it **curates** | its **desk** |
| Tool policy | `board_only` | `full` |
| Session | `single` (one review) | `until_done` |
| Typical use | review the status of the tasks, request information, reorganise the board | own a job ("CISO", "maintenance"), work until done or budget out |

The role only changes the prompt; the policy changes the tool set; the session changes how many
turns a shift may take. Everything else — worker, lock, state, ledger, schedule — is shared.

A board that still carries the legacy `board.manager` block keeps working: it is translated into
an actor on load (`BoardManagerConfig.to_actor_config`), with the reviewed board as its subject
and its own desk board for the reports.

## Generate a team from a need

Writing five profiles by hand to get a team is the wrong entry point. **Team View → Generate**
(and the company assistant) accept a sentence:

> I need someone to take care of the maintenance tickets, only during office hours.

Two steps, so nothing is written blindly:

1. `POST /api/teams/generate/suggest` returns a **proposal**: the roles the project advisor mapped
   from the need, which of them already exist, and the `actor` block each new profile would get
   (role, session, desk board, availability window);
2. `POST /api/teams/generate/apply` writes the missing profiles, then the team and its charter.

Existing profiles are **never modified** by the generator: a role whose profile already exists is
reused as-is, and its actor block stays exactly the way you configured it.

## Guardrails

* **Dry run first.** `mode: "dry_run"` gives you the reports without a single write.
* **Budget per shift and per day.** `budget_cxjcoins` caps one shift, `daily_budget_cxjcoins` caps
  the day across shifts (the day is UTC). The project budget still applies.
* **Time.** `max_runtime_minutes` stops a shift that runs away; `max_turns` bounds an
  `until_done` session.
* **One shift at a time.** Every actor takes a `flock` derived from its name, so two workers (API
  and background process, or a restart overlap) never run the same actor at once. This matters
  because an actor has write access to the repository.
* **Policy.** `board_only` restricts an actor to boards and tasks — a structural guarantee, not a
  post-hoc filter.
* **Environment switches.** `CODX_JUNIOR_ACTORS=0` disables the feature,
  `CODX_JUNIOR_ACTORS_WORKER=0` keeps the API but stops the background worker,
  `CODX_JUNIOR_ACTORS_SCHEDULE=false` ignores every availability window,
  `CODX_JUNIOR_ACTORS_TICK_SECONDS` and `CODX_JUNIOR_ACTORS_MAX_AGENTS` tune the loop.
  (The older `CODX_JUNIOR_AUTONOMY*` names are still read.)

## API

The router is mounted under `/api/actors` (`/api/autonomy` is kept as a hidden alias):

| Endpoint | Purpose |
| --- | --- |
| `GET /actors/status` | Every actor: configuration, state, schedule, due flag, next wake, daily spend. |
| `GET /actors` | Only the actors. |
| `GET /actors/boards` | Boards that can be watched (title, id, columns). |
| `PUT /actors/{profile}` | Partial update of the actor block (upsert; the rest of the profile is untouched). |
| `POST /actors/{profile}/enable`, `/disable` | Toggle the actor. |
| `POST /actors/{profile}/wake?when=+2d` | Request a shift (queued; the worker runs it). |
| `POST /actors/{profile}/schedule?when=+2d` | Set the next planned shift. |
| `POST /actors/{profile}/run` | Run one shift now (blocking, for tests and debugging). |

The API process never runs a shift inline: it reads state and writes the "take a shift" intent.
The background worker (started with the other background services) finds the actors that are due
and runs their shifts, a few at a time.

Connected clients receive live updates on the `codx-junior-actors` channel (`started`, `finished`,
`error`, `wake`, `scheduled`, `skipped`).

## Cost and accountability

Every shift produces exactly one card, so the history of an actor is readable on its board: what
it saw, what it did, what it cost and why it stopped (`single`, `done`, `turns`, `budget`,
`daily_budget`, `runtime`, `schedule`). The per-actor state (`.codx/actors/state.json`) keeps the
same ledger in machine-readable form — shifts, last status, last stop reason, total cost, daily
cost — which is the raw material for scoring and rewarding the actors later on.
