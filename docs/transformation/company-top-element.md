# Company as the top-level element · Tasks table · unified boards

**Implementation note · 2026-11-20**

This change closes the gap between the company domain (already stored as the
container above projects) and the product shell. The project is no longer the
top-most element: a company owns the projects, teams and boards, and a fresh
installation is usable without creating one first.

## What changed

### Company is the top element

- A **default company** is provisioned automatically on a fresh installation.
  It links **every discovered project** and is kept in sync while
  `auto_sync_projects` is on. The first explicit project-list edit turns the
  autosync off, so an unlink is respected.
- The default company is created at startup and, defensively, on the first
  `GET /api/companies`; the operation is idempotent.
- Existing users are enrolled in the default company (admins → `admin`, the
  rest → `member`) and a user is enrolled again on login if missing, so the
  company shell is reachable from the first session.
- The **Management view was removed** (route, app registry, desktop/mobile
  menu). Its old URL redirects to the Tasks table. The company shell owns the
  cross-project management story now.
- Company **Settings → Manage projects** opens the project list editor
  (`CompanyProjectsPanel`), and **Settings → New company** creates another
  company with its own project list.

API:

```text
GET    /api/companies                       provisions the default company
PUT    /api/companies/{id}/projects         replace the project list
POST   /api/companies/{id}/projects         link one project
DELETE /api/companies/{id}/projects/{pid}   unlink one project
```

## Tasks: one paged table across boards

The **Tasks** item (sidebar, main menu, mobile tab) opens
`views/GlobalTasksView.vue`:

- latest tasks of every board of every visible project, **paged server-side**;
- **Board** and **Column** are first-class columns, next to project, status,
  priority, responsible and updated time;
- the board cell (and the whole row) navigates to the board: the task's project
  is activated and the board canvas opens with that board;
- search + project/board/column/status/priority filters and a done toggle.

API: `GET /api/tasks` returns `GlobalTaskPage`; `GET /api/tasks/boards` returns
the visible boards with their owning team and lead plus the team catalog. The
implementation lives in `task_board.py` and `api/tasks.py`.

## Boards: one concept

The sprint board type was removed. A sprint is no longer a different document;
**any board can be used as a sprint** by the team that owns it.

- `KanbanStore` normalizes legacy `type: "sprint"` documents to `kanban` on
  read and write; the v1 migration also produces regular boards.
- The board canvas has a single flavour. The sprint toolbar, sprint creation
  action and "sprint" badge are no longer exposed.
- Legacy tasks that only carried sprint membership are still shown on their
  former board (mapped to the matching column/first column), so existing work
  does not disappear.
- A board has an **owning team** (`team_id`, `team_name`). When it has no team,
  the team that owns the project is used. The **team lead is the responsible
  person** for the board, resolved live from the company document and shown in
  the board header and the task table.

## Tests

```bash
cd api
pytest tests/test_company_default.py tests/test_task_table.py \
       tests/test_kanban_boards.py tests/test_company_business.py \
       tests/test_kanban_search.py -q
```

The client build (`npm run build-only` in `client/`) passes.
