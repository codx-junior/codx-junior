# Cross-project ticketing

A project can **place a ticket** on another project: a request for a bug fix, a
feature or a change that must be implemented in a codebase it does not own.

The reference case is the SaaS platform: while developing `codx-junior-cloud` a
bug is found in `codx-junior`. Instead of editing the other codebase, the
platform places a ticket there, receives an id and keeps using that id to query,
comment on and manage the request. The target project keeps **full control of its
queue**: it reviews the ticket, accepts or declines it, schedules it, assigns
profiles, asks for a human review or implements it right away.

## 1. What a ticket is

A ticket **is a chat** (`Chat` with `mode = "ticket"`), stored by the **target**
project on its `tickets` board:

```
<target-project>/.codx/tasks/tickets/Inbox/<slug>.<ticket-id>.json
```

Because it is a chat, the conversation between the two projects is the protocol:
the requester describes the request, both sides comment, the target answers and
the target's own tooling (boards, profiles, autonomy, orchestration) can process
it like any other card.

The requester keeps a **ledger** with one entry per placed ticket:

```
<source-project>/.codx/tickets/tickets.json
```

The ledger is a fast index (ticket id → target project → last known status); the
ticket chat remains the source of truth and every read refreshes the entry.

## 2. Who may be ticketed

| Relationship | Allowed | Why |
| --- | --- | --- |
| Child project (nested `.codx/project.json`) | ✅ | It lives inside the parent's tree and the parent may already write there |
| Linked project with **read/write** permission (`read_write_project_dependencies`) | ✅ | Placing a ticket writes a chat in the target |
| Linked project with **read-only** permission (`read_project_dependencies`) | ✅ | A ticket is a *request*: the target owns the resulting chat and decides what to do, so no file write permission is needed |
| Linked project from the legacy `project_dependencies` list | ✅ | The same request channel, without granting direct file access |
| Parent project (the project whose control plane contains this one) | ✅ | A child project may need to ask its parent for a change |
| The project itself | ❌ | Handle the change directly |

The target is resolved by **name, id or folder name**, and an unknown target
answers with the list of valid ones — the model never guesses.

## 3. Lifecycle

```mermaid
stateDiagram-v2
    [*] --> open: ticket_create (source)
    open --> accepted: target triage
    open --> declined: target triage
    open --> done: target resolves
    open --> abandoned: ticket_abandon (source)
    accepted --> done: target resolves
    accepted --> declined: target refuses
    accepted --> abandoned: source withdraws
    declined --> accepted: target reopens
    declined --> abandoned: source withdraws
    done --> [*]
    abandoned --> [*]
```

The status lives in **two places that must not be confused**:

* `Chat.status` — the kanban workflow state (`todo`, `doing`, `onhold`, `done`,
  `rejected`), owned by `codx.junior.planning_manager`;
* `Chat.ticket.status` — the ticketing lifecycle (`open`, `accepted`, `declined`,
  `done`, `abandoned`).

The **kanban column is the visible state**: every status maps to a column of the
`tickets` board (`Inbox`, `Accepted`, `Declined`, `Done`, `Abandoned`), so a human
dragging the card to `Accepted` really accepts the ticket — the service
reconciles `Chat.ticket.status` from the column on every read.

## 4. Who decides what

| Action | Source (requester) | Target (queue owner) |
| --- | --- | --- |
| comment | ✅ | ✅ |
| rename / kind / priority | ✅ | ✅ |
| accept / decline / done | ❌ | ✅ |
| abandon (withdraw) | ✅ | ❌ (it declines or closes instead) |

The rule is enforced by `TicketService`, not by the prompt, so an LLM cannot
accept its own request.

## 5. LLM tools

| Tool | Purpose |
| --- | --- |
| `ticket_create(target_project, name, description, kind, priority, reference, profiles, files, tags, assignees)` | Place a ticket and get its id |
| `ticket_update(ticket_id, comment, status, kind, priority, name, profiles, assignees, note)` | Comment and/or move the ticket |
| `ticket_query(ticket_id, direction, status, target_project, limit, include_closed)` | Read one ticket or list the queue of both directions |
| `ticket_abandon(ticket_id, reason)` | Withdraw a ticket this project placed |
| `ticket_autonomy(action, ticket_id, ...)` | Target-side: configure, queue, run, cancel and inspect an autonomous ticket |

`direction` is `outgoing` (placed by this project), `incoming` (placed here) or
`all`.

Example — the platform asks `codx-junior` for a fix:

```
ticket_create(
    target_project="codx-junior",
    name="Login times out after 30s",
    description="## Context\n...\n## Expected\n...\n## Done\n- 2h timeout",
    kind="bug",
    priority="high",
    reference="INC-42",
)
# → ticket_id: c4b9ef9f-c5c2-4f07-b438-0db28ef74711
```

Later:

```
ticket_query(ticket_id="c4b9ef9f-...")        # status: accepted
ticket_update(ticket_id="c4b9ef9f-...", comment="It only happens on 1.4.0")
ticket_abandon(ticket_id="c4b9ef9f-...", reason="fixed in the platform")
```

## 6. HTTP API

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/tickets` | List the tickets of the project (both directions) |
| GET | `/api/tickets/targets` | Projects a ticket can be placed on |
| POST | `/api/tickets` | Place a ticket (returns `ticket_id`) |
| GET | `/api/tickets/{ticket_id}` | Read one ticket |
| POST | `/api/tickets/{ticket_id}/comment` | Append a message to the conversation |
| POST | `/api/tickets/{ticket_id}/status` | Move the ticket (accept / decline / done) |
| POST | `/api/tickets/{ticket_id}/abandon` | Withdraw a placed ticket |
| GET | `/api/tickets/{ticket_id}/ledger` | Raw ledger entry of the requester |
| GET | `/api/tickets/autonomy` | Autonomous tickets of the project + worker heartbeat |
| GET | `/api/tickets/{ticket_id}/autonomy` | Autonomy state of one ticket (readable from both sides) |
| POST | `/api/tickets/{ticket_id}/autonomy` | Configure autonomy (target only) |
| POST | `/api/tickets/{ticket_id}/autonomy/queue` | Enable + queue the ticket |
| POST | `/api/tickets/{ticket_id}/autonomy/run?wait=true` | Run now (optionally blocking) |
| POST | `/api/tickets/{ticket_id}/autonomy/cancel` | Stop a queued/running ticket |

## 7. How a target processes its queue

Nothing special is required: the tickets are on a normal board.

1. Open the `tickets` board of the target project — the `Inbox` column is the
   queue.
2. Move the card to `Accepted` (or call `ticket_update(status="accepted")`), or
   to `Declined` with a comment explaining why.
3. Optionally create a task from the ticket, attach profiles to the card or let
   an autonomous actor own the board.
4. Move the card to `Done` when the change ships: the requester sees it on the
   next `ticket_query`.

## 8. Autonomous tickets

A ticket can be worked by its target **without a human in the loop**. The
requester asks for it with `ticket_create(..., autonomous=True)`; the target
accepts the request (the safe gate), owns the profile and declares the
validation gate. A trusted target may set `auto_accept=True` to skip the
explicit acceptance:

```python
ticket_autonomy(
    action="configure",
    ticket_id="c4b9ef9f-...",
    enabled=True,
    profile="software_developer",
    max_rounds=6,
    validation={"source": "commands", "commands": ["pytest tests/test_login.py -q"]},
)
```

The runtime (`codx/junior/ticketing/autonomy.py`) accepts the ticket, creates an
isolated git worktree, then loops:

1. the agent dives into the repository and implements the request;
2. the runtime runs the validation gate (**never** the model's own claim);
3. a red gate is fed back as the next round; a green gate closes the ticket
   (`done`) with the delivery report and the evidence;
4. `max_rounds`, `max_runtime_minutes` and `budget_cxjcoins` stop a runaway run;
   a missing gate is reported as `blocked`, never as done.

The background worker (`codx/junior/ticketing/worker.py`) takes the queued
tickets; the API can also run one inline (`?wait=true`) for debugging. A
cross-process lock guarantees one run per ticket. See
[docs/features/autonomous-tickets.md](../../docs/features/autonomous-tickets.md)
for the full contract.

## 9. Code map

| Path | Responsibility |
| --- | --- |
| `codx/junior/ticketing/model.py` | `ChatTicket`, `ChatTicketEvent`, `TicketRecord`, lifecycle constants and transitions (pydantic only, so `Chat` can type its field) |
| `codx/junior/ticketing/service.py` | `TicketService`: targets, board bootstrap, ledger, create/update/query/abandon and the ownership rules |
| `codx/junior/ticketing/__init__.py` | Model re-exports (`TicketService` is imported from `.service` to keep imports cycle-free) |
| `codx/junior/tools/ticket_tools.py` | The four LLM tools + their JSON definitions |
| `codx/junior/api/tickets.py` | The HTTP router (auto-discovered under `/api`) |
| `codx/junior/ticketing/autonomy.py` | `TicketAutonomyService`: acceptance, rounds, validation gate, workspace and completion |
| `codx/junior/ticketing/worker.py` | Background worker + heartbeat for queued autonomous tickets |
| `codx/junior/ticketing/prompts.py` | Work-order and failure prompts of the autonomous loop |
| `codx/junior/db.py` | `Chat.ticket` field (the ticketing payload of a chat) |
| `tests/test_ticketing.py` | Lifecycle, ownership, board, ledger and tool coverage |
| `tests/test_ticket_autonomy.py` | Autonomous loop, validation gate, candidates and requester view |

## 10. Invariants

* A ticket id **is** a chat id: one identity, no second lookup table.
* The ticket chat is stored by the **target** (`owner_project_id`), never by the
  requester.
* The target decides `accepted`/`declined`/`done`; the requester only abandons.
* `done` and `abandoned` are terminal: a closed ticket never changes again.
* The column is the visible state; `Chat.ticket.status` mirrors it.
* A ticket never deletes information: abandoning moves the card, it does not
  remove the conversation.

Made with ❤️ by codx-junior
