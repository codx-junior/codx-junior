# Company-first shell — review & refactor proposal

**Status:** DRAFT for validation · 2026-11-21 · **no code changed**
**Scope:** make the company the top-level product surface for a solopreneur,
remove the solopreneur shell, define child-project visibility, scope the global
Follow-up view and add a simplified setup/settings experience.

**Product context:** this document is **Phase 0** of a larger product direction —
codx-junior as an **AI co-founder** that runs a business with humans. codx-junior
is installed on a server with Docker and is **business-agnostic**: its built-in
job is to install, configure, develop and expose the tools each company needs,
with a broad built-in toolset and MCP for company-built tools. A company such as
`codx-junior-cloud` is one example of a business that can be run on it, not a
core platform requirement. The capability map, gap analysis and phased
improvement backlog live in
[ai-cofounder-product.md](ai-cofounder-product.md). The shell refactor is the
foundation that makes the co-founder the home of the product; it is not the
whole product.

This document is deliberately a *review first*: it inventories what exists,
proposes a target model, lists the changes and ends with the decisions I need
from you before touching code.

---

## 0. TL;DR

The **backend already stores the company as the top-level entity**. A default
company is provisioned automatically, links every discovered project and every
user is enrolled in it (`api/codx/junior/company/manager.py`,
`api/codx/junior/app.py:295`). What is still project-first is the **product
shell and the routing**:

1. The shell is reached from a project (`/:projectName/company`) and through a
   role/mode switch (`workspace_mode`, `isCompanyMode`), not from `/`.
2. The default company is called **“My Company”** (`manager.py:133`) and its
   “all projects” behaviour is an implementation detail (`auto_sync_projects`),
   not a first-class setting.
3. Project-to-company links have **no child-project policy**; child projects
   are derived by path and shown implicitly everywhere.
4. The **Follow-up** global view searches every project the user can access,
   including child projects, not the company’s project set.
5. The **Solopreneur** shell exists in parallel and is dead weight.
6. “Global settings” is the 13-tab technical page; there is no 3-minute
   business setup.

**Proposed direction:** `/` always resolves the default company to
`/<company-slug>` (e.g. `/acme`); the company is the home, with an explicit
“Advanced view” at `/<slug>/workspace/<project>/...` for the existing project
workspace; project lists everywhere are filtered by company visibility; the
global Follow-up is scoped to the company’s **root** projects; Solopreneur is
deleted; a new simplified **Settings** section at `/<slug>/settings` replaces
the current company settings section.

**Decisions already taken:** no `/company` prefix; slug-only company segment;
per-link `include_children` inheritance; Follow-up roots only; ACME only on
fresh installs; settings as a company shell section. The remaining questions
are in §9.

---

## 1. What exists today (facts, not proposals)

### 1.1 The domain is already top-level

- `Company` is a standalone document (`api/codx/junior/company/models.py:1496`)
  stored one folder per company under `CODX_JUNIOR_COMPANIES_PATH`
  (default `/home/codx-junior/companies`), not inside a project.
- `CompanyProject` (`models.py:721`) stores the link: `project_id`, cached name,
  role, description, owning team, priority, status, budget.
- `CompanyManager.ensure_default_company()` (`manager.py:707`) creates the
  default company on boot (`app.py:295`) and on `GET /api/companies`
  (`api/codx/junior/api/company.py:521`). It links every discovered project and
  keeps linking newly discovered ones while `auto_sync_projects` is true
  (`manager.py:603`). The first explicit link/unlink turns that flag off
  (`manager.py:1569`, `manager.py:1630`).
- Existing users are enrolled in the default company on creation and on login
  (`manager.py:668`, `api/codx/junior/api/users.py:193`).
- `company.child_projects()` (`manager.py:2884`) derives nested projects by
  path (`os.path.commonpath`), not by an explicit parent field.
- The company snapshot (`manager.py:3062`) already returns `projects`,
  `child_projects`, `members`, `attention`, `journal`, `budget`.

So this is **not** a database migration from “project-owned companies” to
“top-level companies”. It is a **product-shell, routing, visibility and
settings refactor** on top of a domain that is already shaped correctly. That
reduces the risk substantially and should be stated in the plan so nobody
rewrites the store.

### 1.2 The UI still treats the company as a project feature

- Routes:
  - `/company/:companyId?` — project-independent shell
    (`client/src/router/index.ts:102`).
  - `/:projectName/company/:companyId?` — the same shell inside a project
    (`client/src/router/index.ts:260`).
  - `/` redirects to the company only when `users.isCompanyMode` is true;
    otherwise to the project home (`client/src/router/navigate.js:20`,
    `client/src/App.vue:37`).
- The company is also a desktop panel (`config/apps.js:141`,
  `config/appComponentsMap.js:67`), so it can be opened inside the project
  workspace.
- Landing is role/mode-driven: global admins default to working mode, company
  admins/managers/members to company mode (`api/codx/junior/model/user.py:485`,
  `client/src/store/users.js:45`). `workspace_mode` is a persisted user
  preference with a UI selector (`components/main-menu/SettingsMenu.vue:39`,
  `components/security/AccountSettings.vue:42`).
- Company selection is stored in `localStorage` and falls back to the first
  company (`views/CompanyView.vue:224`), not to the URL.
- Exit company offers “simple mode” (Solopreneur) or “desktop workspace”
  (`views/CompanyView.vue:500`).

### 1.3 Default company

- Name: `"My Company"`, slug: `"default"` (`manager.py:133`, `manager.py:137`),
  overridable with `CODX_JUNIOR_DEFAULT_COMPANY_NAME`. On a **fresh**
  installation the proposal changes this to `ACME` / `acme`; an existing
  installation keeps its current name (decided).
- The default company is created with **every discovered project already
  linked**, but they are materialized as individual `CompanyProject` rows
  (`manager.py:777`). There is no “* All projects” concept in the UI.
- A fresh installation has no projects until discovery runs; the company is
  still created and synced later.

### 1.4 Project → company association

- The company editor (`components/company/CompanyProjectsPanel.vue`) links one
  discovered project at a time; there is **no child-project choice**.
- Child projects are inferred by path in three different places:
  - backend `CompanyManager.child_projects()`;
  - client store getters `childProjects` / `parentProject`
    (`store/project.js:540`, `:549`);
  - project selector `ProjectDetailt.vue` computes parent/children itself.
- Because children are inferred, they are **implicitly visible** to the
  company (assistant context, security view, counts) even when they are not
  linked.

### 1.5 Global views

- **Kanban → Follow-up** (`components/kanban/board/FollowupView.vue`) is the
  “global, not board scoped” view. Its data comes from
  `store/planning.js`, which calls `POST /api/chats/search` with a date range
  but **no `project_ids`** (`store/planning.js:483`). The backend then searches
  every project the authenticated user can access
  (`api/codx/junior/chat_manager.py:834`), including child projects.
- **Tasks** (`views/GlobalTasksView.vue`) is a second global table. Its project
  filter lists every project; its default scope is only the active project
  (the comment says “+ children” but the code only pushes the active project —
  `views/GlobalTasksView.vue:96`), and “All projects” means all user projects.
- Board management and calendar have their own project filters; they are
  global too, but are not named in the request.

### 1.6 Solopreneur

- `views/SolopreneurView.vue` + `components/solopreneur/**` (16 files) + route
  `/solopreneur/:module?` (`router/index.ts:88`) + app registry
  (`config/apps.js:57`, `config/appComponentsMap.js:69`) + mobile entries
  (`config/mobile.js:326`, `:389`) + the “simple mode” exit in `CompanyView`.
- Nothing else imports the solopreneur components: deleting the view, the
  components folder, the route and the registry entries is safe. Comments in
  `store/project.js`, `store/chats.js`, `store/kanban.js` and
  `SprintBoardModals.vue` only mention it.
- `components/company/CompanySimpleHome.vue` is also unused (a previous
  “one company screen” experiment).

### 1.7 Settings

- **Company settings** is a section inside the company shell
  (`components/company/CompanySettings.vue`): company context (name,
  description, mission, vision, values, website), one AI provider/model/key,
  autonomy, plan counters, new company, delete.
- **Global settings** (`views/GlobalSettings.vue`) is the installation-wide
  technical page with 13 tabs (General, AI Models, Agents, Chat Prompts,
  Efforts, Plugins, Workspaces, Traefik, DSH, Users, Scripts, OAuth, Secrets).
  It is only reachable from the project route `/:projectName/global-settings`
  (`router/index.ts:321`), and “Advanced” in the company AI block requires an
  active project (`CompanySettings.vue:232`).
- There is no people editor in company settings; adding a person goes through
  the assistant (`CompanyPeoplePanel.vue` is read-first).
- There is no single “set up my business” path with a progress/checklist.

### 1.8 Security findings (business-first makes these urgent)

These are not new, but the refactor will make companies multi-user and
multi-tenant in the UI, so they should be fixed in the same effort:

1. **Any authenticated user can list every company** and the list summary
   includes business numbers (revenue target/actual, pipeline) —
   `api/company.py:521` + `Company.summary()` (`models.py:1739`).
2. **Any authenticated user can read any company** (document and snapshot):
   `GET /api/companies/{id}` and `/snapshot` have no membership check
   (`api/company.py:598`, `:640`).
3. **The default company has no owner** when created at boot with
   `actor="system"` (`manager.py:772`). `_assert_can_edit` and
   `_assert_company_admin` treat an ownerless company as open, so any
   authenticated user can edit it or add users (`api/company.py:467`, `:484`).
4. Company membership is not used to filter the project catalog returned by
   `GET /api/companies/references` (`manager.py:3277`), so a member can see
   project names/paths they may not be allowed to open.

Recommendation: make read access membership-based (global admins see all;
default company enrolls everyone, so a solo install is unaffected), assign the
default company an owner on first admin login, and strip `business` from list
payloads for non-members. Treat this as part of Phase 1, not a later hardening
pass.

---

## 2. Gaps vs the direction

| Desired | Today | Gap |
| --- | --- | --- |
| Company is the default home | `/` → company only for company-mode users; otherwise project home | Landing logic is role/mode-based |
| Load default company unless its slug is in the path | `/company/:companyId?`; companyId only, resolved from localStorage/first; the first URL segment is always assumed to be a project | Company slug is not a route concept; no canonical company URL |
| Fresh installs get “ACME” with “all projects (*)” | “My Company”; all projects materialized as links; no wildcard UI | Name and wildcard model |
| Create companies / switch / settings from the company | Switcher only when >1; creation in settings; settings is one section | Company switcher and a real settings surface |
| Switch to Advanced view and back | “Exit company” modal; `workspace_mode`; company also a desktop panel | No explicit two-way mode switch |
| Advanced view lists only company projects | Project selector/overview/pinned lists all projects | No company visibility filter |
| Follow-up lists only company root projects | Searches all user projects incl. children | No company scope |
| Child projects opt-in per association | Children inferred and implicitly visible | No `include_children` policy |
| Remove Solopreneur | Still routed, registered and linked from exit | Dead code |
| Simplified global settings | 13-tab technical page + a company section | No business setup view |
| Company-level isolation | Reads open, ownerless default open | Security model |

---

## 3. Proposed target model

### 3.1 Vocabulary

- **Company** — the top-level tenant/business. Everything else belongs to it.
- **Default company** — the company resolved when the URL does not name one.
  On a fresh installation it is `ACME` (`acme`), with **All projects (*)**.
- **Company home** — the default landing surface (`/` resolves here).
- **Advanced view** — the existing project-oriented workspace (chats, boards,
  tasks, wiki, files, settings…). It is a *mode* of the product, not a second
  product.
- **Global views** — screens that aggregate across projects: Follow-up, Tasks,
  Board management, Calendar. They are scoped by the active company.
- **Root project** — a project directly associated with the company (the ones
  the owner picks). **Child project** — a project nested under a root by path.

### 3.2 Route map (decided: no `/company` prefix)

The URL scheme you chose:

```text
/                                             → /<default-company-slug>   (e.g. /acme)
/<companySlug>                                → company home
/<companySlug>/<section>                      → company section
                                                home | work | team | projects | business |
                                                activity | security | settings | meet |
                                                coworking | calendar
/<companySlug>/workspace/<projectName>/...    → advanced view (all existing project routes)
```

Legacy / compatibility:

```text
/<projectName>/...                → /<company-slug>/workspace/<projectName>/...
/company                          → default company home
/<projectName>/company            → company home of the project's company
/solopreneur/*                    → default company home
```

**Implementation shape**

- **One company route** (`name: 'company'`) with an optional section param
  constrained to the known sections:
  `/:companySlug/:section(home|work|team|projects|business|activity|security|settings|meet|coworking|calendar)?`.
  `CompanyView.vue` reads the section from the route instead of
  `?section=`; `setSection()` pushes the route. The section is therefore
  refresh-safe and shareable.
- **`workspace` is a reserved second segment.** It is not in the section enum,
  so `/<companySlug>/workspace/...` is unambiguous, and a project named
  “settings” or “team” can never collide with a company section.
- **Advanced routes** keep their names and tails; they gain a
  `/:companySlug/workspace/` prefix:
  `/:companySlug/workspace/:projectName/chats/:chatId/:chatName`, etc. All
  `router.push({ name })` calls keep working because the router injects the
  current `companySlug` (the same mechanism as `withProject()` today — now
  `withCompany()` + `withProject()`).
- **Legacy catch-all:** a single `/:projectName/:tail(.*)*` route at the end
  resolves the project, finds its company and redirects to
  `/<companySlug>/workspace/<projectName>/<tail>`. All old project bookmarks
  keep working without duplicating ~40 route definitions.
- **Shape collisions are resolved by the guard, not the route table:** a legacy
  project URL such as `/<project>/settings` can match the company section route
  (`companySlug=<project>, section=settings`). The guard sees that the first
  segment is not a company slug and redirects to
  `/<company>/workspace/<project>/settings` before the company shell renders.
  The same applies to `/<project>` matching the company home. This is why the
  first-segment resolver must run for *every* route shape, not only for the
  catch-all.
- **Reserved company slugs:** reject `company`, `workspace`, `auth`, all section
  names and `solopreneur` as company slugs at creation, so a company can never
  shadow a route. A *project* with one of those names is still reachable under
  `/<company>/workspace/<project>`.
- **Company vs project in the first segment:** the guard loads the company list
  and resolves the first segment as a company slug first. If it is not a
  company, it checks whether it is a project name (legacy redirect); otherwise
  it goes to the default company with a toast — never a dead end.
- **Desktop mode:** the nested path prefix applies to the URL only. The
  dockview routes (`.../workspace/<project>/desktop/...`) still render the
  workspace shell; route names (`codx-junior-split`, etc.) do not change.

**Slug details (important trade-off of the slug-only choice)**

- `companySlug` is derived from the name (`Acme Studio` → `acme-studio`) and
  unique among companies; the display name stays as typed.
- You chose **slug only** (no id in the URL). To keep shared URLs working
  after a rename, the slug must be **stable**: renaming the company changes
  the display name but **not** the slug, unless the owner explicitly edits the
  slug. If they do edit it, keep the old value in `Company.slug_aliases` and
  redirect `/<old-slug>` → `/<new-slug>`.
- If you would rather have the URL always reflect the current name, the
  alternative is to regenerate the slug on rename and keep aliases. Either way,
  the id remains the internal identity; it is simply not part of the URL.

**Why this shape:** it is the literal “company is the root” model, every URL is
shareable and multi-company safe, and old project URLs redirect cleanly. The
cost is the one-time route rewrite (prefix + guard), which is mechanical
because route names stay the same.

### 3.3 Company resolution and state

Add a real company store (`client/src/store/company.js`) instead of keeping
company state inside `CompanyView.vue`:

- **state:** `companies`, `activeSlug`, `snapshot`, `loaded`, `loading`,
  `error`.
- **getters:** `activeCompany`, `defaultCompany`, `activeRole`,
  `visibleProjectIds`, `rootProjectIds`, `rootProjects`, `hasProject(projectId)`,
  `companyProjects`, `canManage`, `canAdmin`.
- **actions:** `init/load`, `selectBySlug(slug)`, `resolveRoute(route)`,
  `create`, `update`, `linkProject`, `unlinkProject`, `setProjectLinks`,
  `refreshSnapshot`, `setAllProjects(bool)`.
- `main.ts`/store bootstrap loads companies once after login (the endpoint
  provisions the default). `/` waits for `loaded` before redirecting to
  `/<default-slug>`.
- **Bootstrap ordering:** `store/project.js:restoreLastActiveProject()`
  currently treats the first URL segment as a project and redirects to the
  project home when it does not match. That logic must run only for legacy
  project URLs and workspace paths; on `/<slug>` it must let the company
  resolver win first. Otherwise the company landing is immediately replaced by
  a project fallback (this is the single most likely regression of the route
  change).
- The guard resolves `/<companySlug>` (and `/<companySlug>/<section>`,
  `/<companySlug>/workspace/<project>/...`) into the store before rendering.
  `CompanyView.vue` becomes a thin shell; the `codx.company.id` storage
  fallback is removed. `localStorage` only remembers the last company slug for
  `/` and project→company resolution.
- The company switcher is always visible in the company shell, shows the
  `* All projects` badge when applicable, and pushes `/<new-slug>` (same
  section) on change.
- Section changes are route changes (`/<slug>/work`), not query changes. The
  section is the source of truth for the shell; the assistant/refresh keep
  working.
- After create/rename/delete, the route is updated (create → new company home,
  rename → canonical slug, delete → default company home).

### 3.4 Default company

- Change the default to **`ACME`** with slug **`acme`**
  (`manager.py:133`/`:137`). Keep the env override.
- Add a first-class **`all_projects: bool`** on `Company` (default `True` for
  the default company, `False` for a newly created company unless the creator
  chooses all).
  - `True` = wildcard: every discovered project is visible, including ones
    discovered later. No materialized project list is required.
  - `False` = selected: only direct `CompanyProject` links (+ their children
    when `include_children` is on) are visible.
  - Migration: an existing default company with `auto_sync_projects=true`
    becomes `all_projects=true`; `auto_sync_projects=false` becomes
    `all_projects=false`. Keep the old field for one release, ignored by the
    new code, so old clients/documents do not break.
- **Existing installations (decided):** do **not** rename an existing default
  company. Only new installations get `ACME`. An existing “My Company” keeps
  its name (and its data); the owner can rename it in Settings, which changes
  the display name and, if they choose, the slug. This avoids rewriting a
  company the owner may have customised.
- New company creation: the creator becomes owner + company admin (already
  true); default project mode is a creation choice (“All projects (*)” or
  “Choose projects”), defaulting to selected for a new company.

### 3.5 Child-project visibility model

Add **`include_children: bool = True`** to `CompanyProject`. It is the
“children are seen by default” switch the request describes.

**Effective visibility** (`CompanyManager.visible_project_ids(company)`):

- `all_projects = True` → every discovered project (roots + children).
- `all_projects = False` → for every direct link: the link itself, plus all
  descendants when `include_children` is true.
- A child added manually is a direct link and is always visible; it has its own
  `include_children` flag for its own descendants.
- No exclusion list in v1. If a root includes children and the owner wants one
  child hidden, they switch the root to “don’t include children” and add the
  wanted children manually. This matches the request (“if not, the user will
  have to add them manually”) and avoids a second override concept.

`child_projects(company)` changes accordingly: it returns **only inherited**
children (from links with `include_children=true`, or all when
`all_projects=true`), not every path child. This affects the assistant context,
security view, counts and the Projects panel — intentionally: a child the
company does not “see” should not appear in its intelligence either.

**Association UX** (`CompanyProjectsPanel` / new settings):

- When linking a project: checkbox **“Include its child projects”**, checked by
  default, with the discovered child count (“3 child projects”).
- Linked root row: badge “+3 child projects seen” or “children not included”;
  actions “Edit”, “Include/exclude children”, “Unlink”, “Open”.
- A separate **“Add a child project manually”** picker lists discovered
  children not already visible, so the “not included” path is one click, not a
  treasure hunt.
- Changing `all_projects` to selected keeps the current links as the initial
  selection and sets `auto_sync`-style behaviour off.

**Root list for global views / pickers:** direct links in selected mode;
top-level discovered projects in `all_projects` mode. Children are never
offered as a *root* choice. They can still appear in the advanced project
picker as “inherited” entries when visible (grouped under their root), because
that is where you actually open a project.

### 3.6 Advanced view

- A persistent **“Advanced view”** action in the company shell (topbar and
  sidebar footer, and mobile menu). It opens
  `/<companySlug>/workspace/<project>/desktop` for the company’s
  current/last/first visible project.
- A persistent **“Back to company”** action in the advanced sidebar/header
  (also in the mobile menu). It returns to the company section you left
  (`/<companySlug>/<section>`).
- The advanced **project selector, pinned projects, Projects app, project
  tree/list and chat project picker** list only the company’s effective visible
  projects. This is a single getter (`company.companyProjects`), not a patch in
  every component.
- Opening a project URL that is not part of the company:
  - if the user can access it, show a gate: “`<project>` is not part of
    `<company>`” with **Add to company** (manager+) and **Open anyway** (global
    admin) — do not silently hide a project the user owns;
  - if the user cannot access it, normal 404/forbidden.
  This is remaining decision Q3. Hiding without a path to add would trap data.
- Creating a project while a company is active auto-links it to that company
  (default `include_children`), so it does not vanish from the list.
- The company shell is **not** a desktop dockview panel anymore: drop the
  `company` app entry / `/:projectName/company` panel. The company is a route
  shell; the advanced view is the only dockview host. Old links redirect.

### 3.7 Global views (Follow-up and Tasks)

The **Follow-up** view is the one explicitly named. It should be company-scoped
end to end, and **roots only** (decided):

- `store/planning.js` sends the active company’s **root project ids** with the
  search. Recommended: a backend `company_id` parameter with a `roots_only`
  scope (or simply `project_ids = company.rootProjectIds`), so the server never
  falls back to “every project the user can access”.
- Changing company resets the planning store (`loadToken`, tasks, filters), so
  no tasks from the previous company leak in.
- The view shows the company name in its header (“Follow-up · ACME”) and the
  board filter only contains boards of the company’s root projects.
- **Child projects never appear in Follow-up**, even when the company sees
  them. This is the strict root roll-up; child-project work stays in the
  advanced board/task views and in the company’s project views. This is a
  deliberate trade-off: a task that exists only on a child project will not be
  in the global Follow-up.
- **Tasks** (`GlobalTasksView`): project filter options = company roots,
  “All projects” → “All company projects”, default scope = all company roots
  (not just the active project), and the stale `defaultProjectIds` comment/code
  is fixed. Whether the table also shows child-project tasks is a remaining
  decision (§9, Q1): either roots only (consistent with Follow-up) or roots by
  default with an explicit “include child projects” control.
- Board management / Calendar: align later (same getter), not part of the first
  cut unless you want it.

### 3.8 Simplified settings (business setup)

Replace the current company `Settings` section with a **new view** that a
non-technical owner can finish in a few minutes. This is the “global settings”
a business user sees; the existing 13-tab page becomes **Advanced settings** in
the workspace. Proposed tabs/sections:

1. **Company** — name, logo, website, one-line description, mission; currency /
   timezone / language; owner. (Uses the company document.)
2. **AI** — provider, API key, default model, “Apply to all roles”, **Test
   connection**, status (“AI connected ✓”); a single “Advanced AI settings”
   link. (Uses global settings, clearly labelled “shared by this installation”.)
3. **People** — human roster, roles (Owner / Manager / Member), add an existing
   user or create an account (the API already supports both,
   `CompanyMemberRequest`), remove/role change. No team/org editing here.
4. **Projects** — `All projects (*)` vs `Choose projects`; per-root
   “Include child projects”; add root; add a child manually; create a project;
   a live count (“ACME sees 12 projects”).
5. **Advanced** — link to the workspace **Advanced settings** (the existing
   GlobalSettings page), plus company deletion in a danger zone.

Design principles: one column on mobile, no raw JSON/YAML, every field has a
sensible default, a “setup checklist” at the top (Company ✓ · AI ✓ · People ○ ·
Projects ✓) instead of a blocking wizard, and **Save** per section. This is a
new view, not a re-skin of the 13-tab page; the technical page stays intact.

Where it lives (decided):

- A section of the company shell at `/<companySlug>/settings`, so the company
  navigation, mobile full-screen behaviour and path-section model stay
  consistent. The nav item is a link; the tab is part of the section.
- The advanced workspace keeps `/<companySlug>/workspace/<project>/global-settings`;
  rename its menu entry to **“Advanced settings”** (and the page title) so
  “Settings” in the company is unambiguous.
- Remove the `workspace_mode` selector from Account settings; the mode is now
  navigation, not a profile preference (remaining decision §9, Q4).

### 3.9 Remove Solopreneur

Delete/cleanup:

- `client/src/views/SolopreneurView.vue`
- `client/src/components/solopreneur/**` (16 files)
- route `/solopreneur/:module?` and the lazy import
- `config/apps.js` + `config/appComponentsMap.js` entries
- `config/mobile.js` app entry and route title
- `CompanyView.leaveCompany('simple')` and the “Simple mode” exit option +
  `shell.simpleMode` / `shell.simpleHint` i18n keys
- `App.vue` full-screen condition / comments
- `store/project.js` unscoped-segment comment, `store/chats.js` and
  `store/kanban.js` stale comments, `SprintBoardModals` compact comment
- also remove the unused `CompanySimpleHome.vue`
- update `client/docs/mobile/*` notes that list Solopreneur as a screen

No backend code depends on it. This is a safe first commit.

### 3.10 Permissions and company isolation

- `GET /api/companies`: return only companies the user is a member of (global
  admins see all). Include the caller’s `role` per company and `is_member` so
  the switcher does not need a second call. Strip `business` numbers for
  non-members.
- `GET /api/companies/{id}` and `/snapshot`: require membership (global admin
  bypass). The default company enrolls every user, so a solo installation is
  unchanged.
- Default company owner: assign the first company/global admin on bootstrap
  (or on first admin login) so `_assert_can_edit` no longer treats it as open.
  Keep the “ownerless during migration” fallback only for a company that has no
  members at all, and log it.
- Company create: existing behaviour (creator = owner + admin) is correct.
- Advanced view: visible project set = company visibility ∩ user-accessible
  projects. Never render a project the user cannot open just because the
  company links it.
- Company removal: only owner/company admin; already enforced.

---

## 4. Data and API changes

### 4.1 Model changes

```python
class CompanyProject:
    ...
    include_children: bool = True   # NEW: children seen by default

class Company:
    ...
    all_projects: bool = False      # NEW: "* All projects" wildcard
    slug_aliases: List[str] = []    # NEW: old slugs kept for stable URLs
    # auto_sync_projects: kept for one release, ignored by new code
```

Add to `Company.summary()`: `slug`, `all_projects`, `root_project_ids`,
`visible_project_ids` (or counts only for the list to stay light). Keep the
snapshot as the source of truth for the UI.

Slug rules: slug is created from the name, unique, and **stable by default**;
an explicit slug edit appends the old value to `slug_aliases` so old links
still resolve. Reserved slugs (`company`, `workspace`, `auth`, `solopreneur`
and every company section name) are rejected at creation and on edit.

### 4.2 API changes

| Method | Path | Change |
| --- | --- | --- |
| GET | `/api/companies` | membership filter + `role`/`is_member`; no business numbers for non-members; include `slug`, `all_projects` |
| GET | `/api/companies/default` | **new**; provisions/returns the default company (`ACME` on a fresh install) |
| GET | `/api/companies/lookup?project_id=` | **new**; owning companies for a project (legacy redirect, multi-company resolution) |
| GET | `/api/companies/{id}` | require membership |
| PUT | `/api/companies/{id}` | accept `all_projects`, `slug` (with alias history) |
| GET | `/api/companies/{id}/snapshot` | require membership; add `slug`, `all_projects`, `visible_project_ids`, `root_project_ids`, per-project `include_children` / `inherited` / `visible`, and children grouped by parent |
| POST | `/api/companies/{id}/projects` | accept `include_children` |
| PUT | `/api/companies/{id}/projects` | accept `links: [{project_id, include_children}]`; keep `project_ids` for compatibility |
| GET | `/api/tasks` | optional `company_id` + `roots_only` (Follow-up uses roots only) |
| POST | `/api/chats/search` | optional `company_id` + `roots_only` for Follow-up / planning |
| GET | `/api/companies/references` | filter projects to the caller’s accessible set; include parent/root info |

`_project_catalog()` should expose `parent_project_id` (path-derived) so the
client does not have to re-implement hierarchy rules in three places.

### 4.3 Migration / backfill

- Existing companies: `include_children` defaults to `True` (preserves today’s
  implicit behaviour); `all_projects` derived from `is_default` +
  `auto_sync_projects` (default company true, others false); no project list
  rewrite required. `slug_aliases` starts empty.
- Existing default name (decided): **unchanged**. Only a fresh installation is
  `ACME`; an existing “My Company” stays until the owner renames it.
- Existing `workspace_mode` values: stop using them for landing. Optionally
  clear them on save, or leave the field and document it as deprecated. Do not
  delete the field in the first release (old clients).
- Existing slugs: keep them. A company that already has `slug: "default"` is
  reachable at `/default`; its owner can change the slug to `acme` in Settings
  if they want, and the old slug is kept as an alias.
- Legacy URLs: redirect, do not break.

### 4.4 Backward compatibility

- Keep `auto_sync_projects` readable/writable for one release; new UI writes
  `all_projects`.
- Keep `project_ids` request support for `PUT /projects`; new UI sends
  `links`.
- Old `/company/:companyId` and `/:projectName/company` redirect to
  `/<companySlug>` (resolving the company by id or by the project).
- Old `/:projectName/...` project URLs redirect to
  `/<companySlug>/workspace/<projectName>/...` (one legacy catch-all, §3.2).
- Solopreneur URLs: redirect `/solopreneur/*` to the default company home
  (don’t 404).

---

## 5. Frontend change map

| Area | Files | Change |
| --- | --- | --- |
| Company state | `store/company.js` **(new)**, `store/index.js` | companies, active company, snapshot, visible projects, actions |
| Routing | `router/index.ts`, `router/navigate.js`, `store/project.js` | `/`→`/<default-slug>`; `/<slug>/<section>` company route; `/<slug>/workspace/<project>/...` advanced routes; legacy catch-all; `withCompany`; first-segment resolver must not treat company slugs as projects |
| App shell | `App.vue` | remove company-mode watchers, solopreneur, role-based entry; full-screen company routes |
| Company shell | `views/CompanyView.vue` | read company/section from the route; canonical switch; Advanced action; remove exit/simple mode; company panel removed |
| Company projects | `components/company/CompanyProjectsPanel.vue` | all/selected mode; include-children checkbox; manual child picker; child counts; inherited badges |
| New settings | `components/company/CompanySettings.vue` (replace/extend) | Company / AI / People / Projects / Advanced; checklist; advanced link; hosted at `/<slug>/settings` |
| Project pickers | `components/ProjectDetailt.vue`, `ProjectList.vue`, `ProjectTreeSelect.vue`, `chat/ChatSelector.vue`, `ProjectOverview.vue`, `layout/PinnedProjectsSwitcher.vue` | source from `company.companyProjects`; roots first, inherited children nested; “Add to company” gate |
| Global views | `store/planning.js`, `views/GlobalTasksView.vue`, `components/kanban/board/FollowupView.vue` | send company scope; reset on switch; root selector; header company label |
| Menus | `components/main-menu/*`, `layout/AppsSection.vue`, `mobile/*` | “Company”, “Advanced view”, “Back to company”, “Advanced settings”; remove workspace-mode switches and solopreneur |
| Config | `config/apps.js`, `config/appComponentsMap.js`, `config/mobile.js` | remove solopreneur/company panel; add advanced/settings routes |
| Cleanup | solopreneur files, `CompanySimpleHome.vue`, comments, i18n | delete unused code and keys |

**Reusable pieces:** the AI setup block in `CompanySettings.vue` (provider,
key, model, roles, draft model) is the right base for the new AI tab; extract
it into a `CompanyAISetup.vue` so the settings view and any onboarding do not
duplicate the logic. The people API already supports create-or-enroll; the
Projects panel already has the link/edit dialog — extend it rather than
rewrite.

---

## 6. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Company route shadows a legacy project route (first segment is dynamic) | High | Resolve the first segment as a company slug first; legacy project redirect; reserved slugs; explicit tests for a project named like a company |
| Route refactor breaks deep links/bookmarks | High | Legacy catch-all redirects; route-name compatibility; manual test matrix |
| Slug-only URL breaks on rename | Medium | Keep the slug stable by default; keep `slug_aliases` when explicitly changed; redirect old slugs |
| Project→company resolution is ambiguous (project in 2 companies) | Medium | Prefer the company in the URL; for legacy links prefer last active, else default; show the company in advanced |
| Child visibility changes hide existing work | Medium | `include_children=True` default; `all_projects=true` for the default company; “Add child” path; snapshot counts |
| Company isolation fixes lock out existing users | High | Default company enrolls everyone; membership backfill; global admin bypass; staged rollout |
| `all_projects` vs materialized links double source of truth | Medium | One derived `visible_project_ids()`; snapshot exposes it; migration keeps links for display |
| Settings view duplicates the technical page | Medium | Clear naming (“Settings” vs “Advanced settings”); reuse blocks; one AI editor |
| Removing `workspace_mode` surprises users who chose work mode | Low | Keep field deprecated; company is always the landing (decided) |
| Company shell depends on an active project in places (task creation, team generation) | Medium | Pass company-selected project explicitly; no implicit active project in company actions |
| Performance: loading all company snapshots for the switcher | Low | `GET /api/companies` summary only; snapshot loaded for the active company |

---

## 7. Phased plan

Each phase should be shippable and verifiable on its own.

### Phase 0 — decisions (this document)
Confirm §9. Freeze the route shape and the visibility model.

### Phase 1 — company as the default shell (no child policy yet)
- Add `store/company.js`; load companies after login; `/` → default company.
- `/<companySlug>` company home + `/<companySlug>/<section>` sections;
  company switcher; fresh installs `ACME`; remove role/mode landing
  (`isCompanyMode`, `workspace_mode` selector).
- `/<companySlug>/workspace/<project>/...` advanced routes + legacy catch-all;
  `withCompany` helper; first-segment resolver.
- Remove Solopreneur (view, components, route, registries, exit option,
  mobile entries, docs).
- Add “Advanced view” / “Back to company” navigation; advanced project lists
  filtered by the company (no child policy yet — use current links).
- Legacy redirects (`/company`, `/:projectName/company`, `/:projectName/...`,
  `/solopreneur/*`).
- Security: membership-filtered list/get/snapshot, default owner.
- **Exit criteria:** a fresh `/` lands in `/acme` (an existing install lands in
  its current default slug); `/<slug>` and `/<slug>/settings` reload;
  `/<slug>/workspace/<project>/chats` reloads; company switch changes the URL;
  advanced only lists linked projects; Solopreneur is gone; no 404s on old
  bookmarks.

### Phase 2 — project visibility
- `all_projects` + `include_children` in the model, manager, API and snapshot.
- Projects panel: all/selected, include-children, manual child, inherited
  badges and counts.
- Single `visible_project_ids()` used by the assistant, security, counts and
  the advanced pickers.
- New project auto-link; not-in-company gate with “Add to company”.
- **Exit criteria:** default sees all (`*`); deselecting all projects and
  linking one root respects the child toggle; assistant/security/advanced all
  agree on the visible set.

### Phase 3 — global views and settings
- Follow-up company scope, **roots only**; Tasks table company-root filter;
  reset on switch.
- New simplified Settings view (Company/AI/People/Projects/Advanced) at
  `/<slug>/settings`.
- Rename the technical page to “Advanced settings”; link from the new view.
- **Exit criteria:** a new owner can configure company, AI, one person and one
  project in under 5 minutes; Follow-up never shows another company’s work or
  a child-project task.

### Phase 4 — hardening/cleanup
- Company read isolation tests; ambiguous-project resolution; legacy redirect
  coverage; remove `auto_sync_projects`/`workspace_mode` in a later release;
  docs/screenshots; delete dead company components.

---

## 8. Test plan

**Backend (`api/tests`)**
- extend `test_company_default.py`: fresh install name/slug `ACME`,
  `all_projects=true`, include_children default; an existing “My Company” is
  **not** renamed; migration from `auto_sync`.
- new `test_company_visibility.py`: visible set for all/selected, root with
  children on/off, manual child, nested grandchildren, project in two
  companies.
- security tests: list/get/snapshot filtered by membership; non-member gets
  403/404; default owner is set; list summary has no business numbers for
  non-members.
- `test_task_table.py` / chat search: `company_id` + `roots_only` scope returns
  exactly the root set; child tasks are excluded from Follow-up.
- keep `test_company_business.py` green (snapshot shape).

**Frontend (`client/tests`, node:test)**
- pure helpers: route parsing/canonicalization (`/<slug>`,
  `/<slug>/<section>`, `/<slug>/workspace/<project>/...`), first-segment
  company-vs-project resolution, legacy redirect building, visible/root project
  derivations.
- optional component-level test for the projects panel toggle logic.
- `npm run type-check` and `npm run build-only`.
- manual matrix: fresh install, existing install, one company/multi company,
  desktop/single/mobile, old project URL, old `/company` URL, rename (slug
  stable and explicit slug change), delete company, no projects, all projects,
  a project named like a company slug.

**Acceptance script (headless/manual)**
1. Fresh login → `/` → `/acme`; company home shows “All projects”.
2. Existing install → `/` → its existing default slug (and name unchanged).
3. `/<company>/settings` → new settings; reload keeps the tab; `<company>/work`
   is deep-linkable.
4. Create “Studio B” with one root and “include children” off → open it by URL
   → only the root is listed; add a child manually → it appears.
5. Advanced view from Studio B → `/<studio-b>/workspace/<project>/desktop`;
   only its projects in the selector; click “Back to company” → Studio B home.
6. Follow-up → only Studio B roots, never ACME’s and never child-project tasks.
7. Rename Studio B → display name changes, slug stays by default; if the slug is
   edited, the old URL still redirects.
8. Delete Studio B → lands on the default company.
9. `/solopreneur/tasks` → default company home (redirect), not a blank screen.

---

## 9. Decisions and remaining questions

### Decided in this round

- **D1. Route shape:** no `/company` prefix. `/` redirects to
  `/<default-company-slug>` (e.g. `/acme`); company sections are
  `/<slug>/<section>`; advanced is `/<slug>/workspace/<projectName>/...`.
- **D2. Company segment:** slug only, no id in the URL.
- **D3. Child projects:** per-link `include_children` with dynamic inheritance
  (children discovered later follow the flag; when off, the owner adds them
  manually).
- **D4. Follow-up scope:** root projects only; child-project tasks do not appear
  in Follow-up.
- **D5. Existing default company:** only new installations get `ACME`; an
  existing default keeps its name. No automatic rename.
- **D6. Simplified settings:** a company shell section at `/<slug>/settings`
  with the proposed five tabs.

### Remaining questions

**Q1. Tasks table (`/acme/workspace/.../tasks`) child scope.** Should it also be
roots only (consistent with Follow-up), or list company roots but allow child
tasks through an “include child projects” control? I recommend roots by
default + an explicit include control, because Tasks is the workspace-level
table and hiding child work there would make it hard to find.

**Q2. New company default project mode.** “Choose projects” (recommended) or
“All projects (*)”? The default company is always `*`; a new company likely
starts empty and the owner picks.

**Q3. Unassociated project URL.** When a project exists but is not linked to the
company in the URL: show an “Add to company” gate (recommended), redirect to the
company home, or allow advanced access with a warning? Should global admins get
an “Open anyway” escape hatch?

**Q4. `workspace_mode`.** Remove it entirely in this refactor (company is
always the landing, advanced is navigation), or keep it as a deprecated field
and ignore it? I recommend ignore + remove the UI now, delete the field later.

**Q5. Company list visibility.** Filter `/api/companies` and company reads to
members (recommended; the default company enrolls everyone) or keep “any
authenticated user sees every company”? This is the main business-isolation
privacy decision.

**Q6. Slug on rename.** Keep the slug stable when only the name changes
(recommended, stable URLs) or regenerate it and keep the old slug as an alias?
Either way, an explicit slug edit should keep aliases.

**Q7. Company home density.** Keep the current rich dashboard as the default
home, or simplify it (today’s priorities + 3 actions) and move financials/
health deeper? Not strictly required, but it affects the “few minutes” first
impression.

---

## Appendix — files worth reading first

- Domain: `api/codx/junior/company/models.py`, `store.py`, `manager.py`
  (`ensure_default_company`, `child_projects`, `snapshot`), `api/company.py`,
  `app.py:295`.
- Client shell: `client/src/router/index.ts`, `router/navigate.js`,
  `App.vue`, `views/CompanyView.vue`, `components/company/*`,
  `store/users.js`, `store/project.js`.
- Settings: `views/GlobalSettings.vue`, `components/company/CompanySettings.vue`,
  `components/global_settings/GeneralSettings.vue`.
- Global views: `store/planning.js`, `components/kanban/board/FollowupView.vue`,
  `views/GlobalTasksView.vue`, `api/codx/junior/api/tasks.py`,
  `api/codx/junior/chat_manager.py`.
- Project lists: `ProjectDetailt.vue`, `ProjectList.vue`, `ProjectTreeSelect.vue`,
  `ProjectOverview.vue`, `PinnedProjectsSwitcher.vue`, `ChatSelector.vue`.
- Solopreneur: `views/SolopreneurView.vue`, `components/solopreneur/**`,
  `config/apps.js`, `config/appComponentsMap.js`, `config/mobile.js`.
- Existing notes: `docs/transformation/company-top-element.md`,
  `docs/transformation/company-ui.md`.

---

*Draft for discussion. No code has been changed by this document.*
