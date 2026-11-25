# Company tickets

> **Status:** implemented · **UI:** company shell → **Tickets** ·
> **API:** `/api/companies/{id}/tickets` · **Code:**
> `api/codx/junior/company/tickets.py`,
> `api/codx/junior/api/company_tickets.py`

The company shell has one workbench for the tickets of **every** linked
project. It is the company-level counterpart of the per-project ticket board:
place a request on the project that must do the work, let the autonomous worker
handle it without a human in the loop, and review the delivery when it is done.

The workbench is deliberately shaped like a coding harness session list: the
left rail groups the queues by project and the main pane is the selected
ticket — its request, run state, validation evidence, conversation and
delivery.

## Place a ticket

1. Open **Tickets** in the company sidebar.
2. **New ticket** → pick the project that will do the work.
3. Write the request with context, expected behaviour and acceptance criteria.
   This is the work order the agent reads.
4. Choose the validation gate:
   * **Project QA suite** (default) — the project's detected suite / QA policy;
   * **Explicit commands** — command lines run in the ticket workspace;
   * **No gate** — the agent can close without proof (not recommended).
5. Keep **Work this ticket unattended** on.

The ticket is placed as the company (the requester shown in the conversation),
accepted immediately because the company owns the target project, and queued
for the worker.

## Place a ticket from the assistant

The [company assistant](/features/company-assistant) is a real `ChatView`
conversation with the `codx-junior` profile. It receives the `company_ticket`
tool, so the owner can also say *"place a ticket on the Website project to fix
the broken pricing page, validate it and let me review it"*. The tool lists the
queues, places the ticket on any company project, runs/queues/cancels it,
comments and accepts or sends back the delivery — the same service the
workbench calls.

## Unattended work

The autonomous worker creates an isolated worktree/branch for the ticket, runs
the agent with the project's tools, validates the gate, feeds a red result back
as the next round and closes the ticket only when the gate is green. The
complete rules (consent, isolation, budgets, validation sources) are documented
in [Autonomous tickets](/features/autonomous-tickets).

While it runs, the workbench shows:

* the live state (`queued`, `running`, `review`, `blocked`, `done`);
* rounds / attempts, the gate and whether it passed;
* the workspace branch;
* the conversation, including the validation evidence appended by the runtime.

## Review the delivery

A green run closes the ticket (`done`) and leaves it in **To review**. The
delivery report is at the top of the selected ticket.

* **Accept delivery** records the human decision on the ticket. It does not
  rewrite the status: the run is already closed, the review is the acceptance.
* **Request changes** reopens the ticket (`accepted`), records the review note in
  the conversation, clears the previous review and queues another unattended
  run. The agent keeps the same ticket, branch and evidence history.

A new run clears the previous review, so a re-delivered ticket always comes back
to **To review** instead of silently looking accepted.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/companies/{id}/tickets` | Queues of every company project + counts. |
| `GET` | `/api/companies/{id}/tickets/summary` | Counts only (home widgets). |
| `GET` | `/api/companies/{id}/tickets/{ticket_id}` | Ticket, conversation and autonomy evidence. |
| `POST` | `/api/companies/{id}/tickets` | Place a ticket on one project. |
| `POST` | `/api/companies/{id}/tickets/{ticket_id}/comment` | Comment / instruct the agent. |
| `POST` | `/api/companies/{id}/tickets/{ticket_id}/configure` | Profile, budgets, validation gate. |
| `POST` | `/api/companies/{id}/tickets/{ticket_id}/queue` | Enable and queue for the worker. |
| `POST` | `/api/companies/{id}/tickets/{ticket_id}/run` | Run now (background unless `wait=true`). |
| `POST` | `/api/companies/{id}/tickets/{ticket_id}/cancel` | Stop the current run. |
| `POST` | `/api/companies/{id}/tickets/{ticket_id}/review` | `accept` or `changes`. |

Company reads are open to company members; writes require the company manager
or admin role.

## Permissions and isolation

* The company places tickets only on **its own visible projects**; a project
  removed from disk is shown as unavailable and cannot run work.
* The agent cannot close its own ticket: the runtime owns the validation gate
  and the final status.
* Work happens on an isolated ticket branch; the main checkout is never
  modified by the run.
* Review is target-side only (the company manager acting on the company's
  project); a requester outside the company never accepts a delivery.

## Tests

The company aggregation and the review lifecycle are covered by
`api/tests/test_company_tickets.py`; the run itself is covered by
`api/tests/test_ticket_autonomy.py`.
