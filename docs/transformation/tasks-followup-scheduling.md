# Follow-up task list, scheduled tasks and splitter widths

**Implementation note · 2026-11-20**

## Tasks = Follow-up style

`views/GlobalTasksView.vue` now uses the visual language of the kanban
**Follow-up** view and is rendered full width:

- headline cards (total / active / done / overdue / due today) computed
  server-side over the whole filtered dataset;
- a flat task list with priority, status, project, **board**, **column**,
  responsible, assignees, due/wake state and updated time;
- the default project scope is the **active project and its children**
  (`$storex.projects.childProjects`); the user can switch to **All projects**;
- clicking a task opens it in the **right pane of a `VerticalSplitter`**
  (`ChatView`), exactly like the board inline task detail. On mobile the task
  opens as a normal chat route because there is no room for a split pane.

API additions: `GET /api/tasks` accepts `project_ids` (comma separated),
`assignee`, `from_date`/`to_date`, and returns the `metrics` block;
`GET /api/tasks/boards` still returns board/team ownership.
`owner_project_id` and `wake_at` are included so the right pane can load a task
from a child project.

The kanban **Follow-up** view (`components/kanban/board/FollowupView.vue`) uses
the same paged endpoint for its general task review: it requests
`/api/tasks?project_ids=<active project + children>` page by page, keeps the
server counters/metrics, and loads the next page on scroll. Its date-range
filter is sent as `from_date`/`to_date`, so the review never falls back to
“every project the user can access”.

## New chat → codx-junior assistant, in a modal

The left bar **New chat** entry now opens `BoardQuickChat` globally in a modal
(App.vue) configured with:

- board `codx-junior`;
- assistant profile `codx-junior`;
- mode `task`;
- a schedule selector: **Start now**, **Non-working hours**, or a concrete
  instant.

A scheduled card is created and saved but **not processed immediately**: its
`wake_at` is written and the autonomy worker picks it up when due.

## Non-working hours

Human working hours are an installation-wide global setting:

`GlobalSettings.working_hours` → enabled, timezone, days, start, end
(default Mon–Fri 09:00–18:00 UTC), editable in Global Settings → General.

`working_hours.py` resolves `non-working-hours` to:

- **now**, when humans are already offline (night/weekend), or
- the **closing instant** of the current working window.

The token is understood by `TaskManager.to_wake_at`, `create_task` tool,
`project_manager`, `schedule_task` and `POST /api/chats/tasks`. The client gets
the resolved instant from `GET /api/tasks/schedule?when=non-working-hours`, so
the timezone logic lives in one place.

## VerticalSplitter widths

`components/layout/VerticalSplitter.vue` already knew how to persist a layout
when given `persistKey`. Now:

- every current splitter passes an explicit stable key (tasks, kanban, chat,
  harness, git, meet, wiki, CISO, browser, file explorer, PR/diff, …);
- when no key is supplied, the component derives one from the route + parent
  component, so future splitters remember their width too.

The user width is restored when the splitter (or its optional side panel) is
shown again; otherwise the configured `defaultSize` is used.
