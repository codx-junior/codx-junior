# Project Atlas

The atlas is codx-junior's navigation index for a project. It answers the
questions models used to answer with `grep`/`glob`/semantic search:

- where is a symbol, route, config key, error message or document?
- what are the project's domains, entry points and reading order?
- what calls or imports this, and what breaks if I change it?
- is the answer still true for the current working tree?

It is deliberately small: one SQLite database per project, no extra service.

```text
<CODX_JUNIOR_RUNTIME_PATH>/<project-slug>/atlas/atlas.db
```

It is a rebuildable cache: the project tree never contains it, and a corrupt
file is deleted and rebuilt instead of surfacing `database disk image is
malformed`.

## How it works

```mermaid
flowchart TD
    G[git ls-files + AST/regex + frontmatter] --> I[AtlasIndexer]
    I --> DB[(SQLite: files, nodes, edges, FTS5)]
    C[ChangeManager / background worker] --> I
    Q[project_map / locate / trace / impact] --> DB
    Q --> V[Verify lines against live files]
    V --> P[Evidence packet]
```

- **Files** come from `git ls-files` (honoring `.gitignore`) with an
  `os.walk` fallback, and are then filtered by `get_ignore_patterns()`
  (`.codx`, `node_modules`, the wiki folder, `knowledge_file_ignore` and the
  child projects). It is the same list the knowledge base and the file tools
  honor, so every layer agrees on what belongs to the project.
- **Python** uses `ast` for classes, functions, methods, imports, calls and
  FastAPI/Flask-style routes with exact line ranges.
- **JS/TS/Vue** use focused regexes for classes, functions, imports, routes and
  the component itself.
- **Markdown** uses frontmatter plus headings; a `source:` field links a doc
  page back to the file it documents.
- **Edges** are high precision: `contains`, `imports`, `calls`, `handles`,
  `tests`, `documents`.
- **Search** is SQLite FTS5 over paths, names and bodies plus exact
  name/path/symbol scoring. Content hits are verified by reading the live file.

## Freshness

`ProjectAtlas.ensure()` checks the git HEAD and the working tree before a
query:

- HEAD moved or too many files changed → the current index is served and a
  background rebuild is scheduled; a read never blocks on a full rebuild;
- a few files changed → reindex only those files (and prune deleted ones);
- no changes → reuse the index (freshness check cached for
  `project_atlas_ttl_seconds`, and `git status` is scoped to the project);
- index version changed → the index is reported as not ready and rebuilt in the
  background;
- corrupt database → the file (and its WAL sidecars) is deleted and rebuilt.

Full builds run in the background worker (`ensure_project_atlas`), after a file
change (`schedule_atlas_refresh`, debounced 3s) and whenever a tool finds
`ProjectAtlas.needs_refresh == True`. While a build is running, the tools
return a bounded fallback result and ask the model to retry, so the first call
never blocks on a full index.

## Tools

| Tool | Purpose |
| --- | --- |
| `project_map` | domains, entry points, key files, reading order, related projects |
| `project_locate` | ranked `path:line` evidence for symbols, strings, routes, docs, globs (`project=` searches other projects) |
| `project_trace` | imports/calls/routes flow from a symbol |
| `project_impact` | reverse dependencies, tests and docs before a change |
| `project_health` | coverage, freshness, commit, warnings |

When `project_atlas_enabled` is true (default), the atlas tools are injected
into any profile that can read project files. Discovery is **atlas-only**:
`project_grep`, `project_glob`, `project_search` and `project_structure` are no
longer registered as tools, so there is no legacy fallback to keep in sync. The
functions remain in `project_tools.py` for internal callers and tests.

## Related projects

A project is rarely alone: it can contain **child** projects (a nested
`.codx/project.json`) and it can declare **dependency** projects by name
(`project_dependencies`). That resolution lives in
`CODXJuniorSettings.get_related_projects` and is shared with the knowledge
engine and the chat RAG pipeline.

The atlas keeps **one database per project** and never merges them, but the
tools are aware of the structure:

- `project_map` lists the related projects (name, relation, root and whether
their atlas is already built), so the model knows where it can search instead of
guessing;
- `project_locate(query, project="codx-api")` searches one related project;
- `project_locate(query, project="all")` searches this project and every
related one, and each section reports the root needed to open the files
(`project_read_file` accepts the absolute path). The fan-out is bounded (12
projects and an 8s budget per call) and only two unindexed projects are queued
for a build; the rest are reported as skipped so one call cannot take minutes;
- child discovery prunes VCS/dependency folders (`.git`, `.codx`,
`node_modules`, `dist`, ...) and ignores projects found inside them, so pytest
fixtures under `.codx/tmp` are no longer treated as related projects;
- a child project's folder is **excluded** from the parent index because it owns
its own atlas. This also keeps the parent under `project_atlas_max_files` in a
monorepo. When `search_related_projects` is disabled the children stay in the
parent index, since no other project can reach them.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `project_atlas_enabled` | `true` | Build/expose the atlas |
| `project_atlas_max_files` | `5000` | File cap per index |
| `project_atlas_ttl_seconds` | `5` | Reuse the freshness check for this long |

## API

The router exposes the same packets as JSON:

```text
GET  /api/atlas/health
GET  /api/atlas/map?query=&path=&limit=
GET  /api/atlas/locate?query=&kind=&include=&regex=
GET  /api/atlas/trace?symbol=&direction=&depth=
GET  /api/atlas/impact?paths=&depth=
POST /api/atlas/rebuild
```

## Tests

```bash
PYTHONPATH=api python3 -m unittest api.tests.test_atlas -v
```

The tests build a small git repository and cover symbol/content/route/doc/glob
locators, trace, impact, incremental refresh, health and the tool policy.
