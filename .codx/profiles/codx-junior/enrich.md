## AI: harness=False for single-shot prompts

- **Single-shot LLM calls must opt out of the agent harness.** `AI(..., harness=False)` (forwarded to `SmolAgent`) skips the agent system prompt and every tool schema. Without it the model believes it is a coding agent, emits a tool call that no loop executes, and the answer reaches the user as raw provider tool-call markup (DeepSeek `<｜｜DSML｜｜invoke …>`).
- **Never render provider-inline tool-call markup.** Use `codx.junior.ai.utils.strip_tool_call_markup(text)` for any assistant text (already applied to the final answer in `SmolAgent._run_chat_loop`).
- **Wiki docs assistant** = `client/src/views/WikiView.vue` → `POST /api/wiki/assistant` → `codx/junior/wiki/assistant.py::WikiAssistant` (pure RAG over the generated wiki, no tools).

## Runtime code drift: verify the loaded code, not the tree

`scripts/run_api.sh` starts uvicorn **without --reload**, so the running API keeps
the code it imported at boot. A source change (even committed) has no effect
until the API is restarted, and the symptom is misleading: orchestration runs stay
"Running" at 0 % while every card creation fails with
`TaskManager.create_task() got an unexpected keyword argument ...`.

Before diagnosing any "harness/run is blocked" symptom, verify the *loaded* code:

1. `docker_container(action="exec", container_name="codx-junior-api",
   command="grep -n <symbol> <path inside the container>")`
2. If the symbol is missing there but present in the checkout, the process is
   stale — restart `codx-junior-api` **and** `codx-junior-api-background`
   (never from the chat that the API is serving: the restart kills the turn).
3. Compose lives in `scripts/docker-compose.yaml`; docker compose tools run from
   the project root, so pass `-f scripts/docker-compose.yaml`.

Orchestration card creation is signature-tolerant since `RunBoard._supported_task_kwargs`
(drops kwargs the loaded `create_task` lacks and logs the stale runtime), so a
future drift degrades instead of deadlocking the run.

## client/src is not indexed by project_grep/project_glob

`client/src` is excluded from `project_grep` / `project_glob` / `project_search` (custom ignore patterns), so searching it there returns "no matches" even for strings that exist.

Workarounds that DO work:
- `project_read_file` with `pattern=` (regex) on a concrete file (or a list of files) — case-sensitive.
- `read_folder` / `project_structure` to discover paths.
- `project_changes` with `paths=["client/src"]` for diffs.

Navigation/IA of the web client lives in `client/src/config/apps.js` (app registry), `client/src/config/mobile.js` (mobile tabs + "More" launcher groups), `client/src/config/appComponentsMap.js` (app → component), `client/src/router/index.ts` (routes + `meta.mobile`) and `client/src/components/mobile|layout|main-menu/*` (nav surfaces). Adding an app to mobile = APPS entry + route + MOBILE_APP_GROUPS tile + MOBILE_ROUTE_TITLES.

## Repo search gotcha: project_grep under-scans

In `/home/codx-junior-projects/codx-junior`, `project_grep` is unreliable: it returns "No matches" even for literal strings that exist (e.g. it does not find `ProjectDetailt` inside `client/src`, and it never scans `api/`). Do not conclude a symbol is absent from grep results.

Reliable discovery:
- `project_glob` with explicit patterns (works).
- `read_folder` to map folders, then `project_read_file` with `pattern=` / `start_line`/`end_line`.
- `project_read_file(pattern=...)` finds symbols inside a file reliably.

Client conventions: `@` alias → `client/src`. `$storex`, `$projects`, `$project`, `$ui` etc. are global computed props from a mixin in `client/src/main.ts`, so any Options-API component can use them. Settings pages are Options API-friendly; `ProjectSettings.vue` is `<script setup>`.

## Brand & design source of truth (codx-junior)

Design documentation for this project lives in `design/` (project `codx-junior-design`); read `design/README.md` first.

Hard rules:
- Brand magenta is **#CC0099** (secondary **#8F008F**, accent/pink **#FF69B4**). `#CC049C` is a retired near-miss; **indigo #6366F1 is NOT part of the brand** (it survives only in an old `Designer` profile).
- Brand text on the dark canvas must use pink `#FF69B4` (5.5:1) or the OKLCH-boosted primary `oklch(65% .32 320)`; `#CC0099` as text on `#1F2937` is only 3.1:1 and fails WCAG AA. `#8F008F` is fill/gradient only (1.8:1 on dark).
- Only two themes are allowed: `codx-junior-dark` (default) and `codx-junior-light`. Do not offer all 35 daisyUI themes.
- `client/src/assets/daisy-override.css`, `client/src/assets/themes/*.css` and `client/src/assets/logo.svg` are dead/scaffolding code (the svg is the Vue logo) — safe to delete, do not build on them.
- Interface = system UI font; anything machine-generated (paths, hashes, diffs, counts) = monospace. No third family.
- `isMobile` is decided in JS at `window.innerWidth <= 1024`, so tablets get the mobile shell; verify layouts with `isMobile` true AND false.
- z-index ladder: chrome 50, backdrop 100, toast 130, nav 150, **modal 160** (currently a bug at 50), popper 200.
- Use tokens from `design/tokens/design-tokens.json`; no raw hex or arbitrary px in components.

## Verifying code in this project (node/python)

- Verifying JS/ESM syntax of `client/**`: run it inside container `codx-junior-default-workspace` (has `node`, mounts the whole project at `/home/codx-junior-projects/...`). `codx-junior-api` has Python but NO node; `codx-junior-client` is nginx-only and `codx-junior-docs` has node but no project mount.
  - `docker exec` runs without a shell: copy to a `.mjs` first (two calls, no `;`): `cp <file> /tmp/x.mjs` then `node --check /tmp/x.mjs`. `node --check` on a `.js` file fails on `import` (CommonJS mode).
- Verifying Python: `python3 -m py_compile <files>` in `codx-junior-api` (exit 0 = ok).
- NEVER restart the `codx-junior-api` container to pick up a code change: the harness/AI processing of the current chat runs inside it, so a restart kills the running turn.

## Git worktrees must live inside the project

**Worktrees for tasks:** `create_pr_branch(worktree=true)` puts the worktree under `~/codx-junior-worktrees/`, which the file tools cannot reach (sandbox = project root). Move it with `git worktree move` into `<project>/.codx/worktrees/<name>` before writing files.

**docker_exec guards:** one command per call, no `;`, no `rm`. Delete temp files with `python3 -c` using `pathlib`, and read git state with `git -C <abs path> status --short`.

**Long commit messages:** `git commit -m "<multi-line>"` is rejected by the guard. Write the message to a file and use `git commit -F <file>`.

## Backend verification in the api container

## Verifying backend changes in this project

- `pytest` cannot be run through `docker_exec` in `codx-junior-api`: importing `codx.junior.ai` pulls the whole AI stack (torch/vllm) and exceeds the 300 s tool timeout. `docker_exec` also refuses `;`, newlines and `rm` (use `find <dir> -name <file> -delete` to clean temp files).
- Venv: `/home/codx-junior-projects/codx-junior/codx-junior-platform/api/.venv/bin/python`, needs `env PYTHONPATH=<api>`; the project is mounted in the container.
- Practical verification: `python -m py_compile` on every changed file, plus a scratch script that stubs `codx`, `codx.junior`, `codx.junior.ai` and `codx.junior.ai.smol` in `sys.modules` (with `__path__` set) so light modules such as `ai/smol/loop_guard.py`, `orchestration/prompts.py`, `orchestration/executor.py` import in seconds. Delete the script afterwards.
- The project atlas index (`project_locate`) is empty/stale for the `api/` tree: use `project_read_file(pattern=...)` with exact paths instead.

## apply_file_changes line-collapse gotcha

Batch edits (`apply_file_changes`) can collapse the line right after an inserted banner comment onto the `//` comment line, silently commenting out the statement (real case: `// ── Transport state ──  const loading = ref(false)` in `client/src/composables/useLogExplorer.js` → `ReferenceError: loading is not defined` at `useLogExplorer()` return, blank Logs app). `vite build` and `node --check` do NOT catch it.

After any multi-line batch edit of JS/Vue/Python files:
- re-read the edited region (`sed -n a,bp` / `project_read_file` with line numbers) and confirm every declaration still starts its own line;
- scan for swallowed statements: regex `//\s*[^\n]*\b(const |let |return |function )` (JS) and `#.*\b(self\.|return |raise |def )` (Python);
- cross-check that every key returned by a composable is actually declared (that is how this bug surfaces).

## Secrets must never reach .codx JSON or git history

GitHub push protection blocks `.codx/tasks/**` and `.codx/chat_turns/**` commits that contain captured tool output with credentials.

Rules:
- Tool outputs printed into chat (e.g. repository_overview remote_url, git remotes with `https://user:token@...`) are persisted verbatim into `.codx` JSON, which is tracked by git. Always redact credentials in tool output before persisting/echoing.
- Never commit `.codx` JSON without scanning: `git grep -n 'github_pat_\|ghp_\|gho_\|ghs_'`.
- Repository remotes must not embed tokens; use a credential helper or env var instead.
- Rewriting local-only commits: `git filter-branch --index-filter` in a **separate clean worktree** (the main tree is dirty), then `git update-ref refs/heads/main <new>`, realign the index with `git reset HEAD -- <path>`, and delete `refs/original/*` afterwards.
- In this environment `sed -E 's/[A-Za-z0-9_]+/.../'` silently no-ops through docker_exec; use the BRE form `s/github_pat_[A-Za-z0-9_]*/.../g`. docker_exec rejects `|`, `;`, `rm`, `unlink`.

## Project's Channel — product decisions

**Project's Channel** (`design/project-channel.md`) — the wiki becomes the *Library* tab of a channel with Episodes, Shorts (9:16) and Learn (teacher chat). Produced by the **Multimedia AI manager** (`api/codx/junior/media/`, started from `background.py`), local-first behind one `MediaProvider` interface.

Two locked decisions (do not re-litigate):
1. **Voice is per profile**, not per channel: `Profile.voice` (`ProfileVoice` in `model/profile.py`) → resolved via `media/voice.py::resolve_voice` (shot → profile → project → system) and snapshotted into the Episode IR. This is why profiles need a dedicated **full-page editor** (`/profiles/:name/edit`) instead of the 32rem `TeamMemberDrawer.vue`.
2. **Mobile first**: design the channel at 360 px (bottom tab bar, 9:16 shorts primary, full-bleed player); desktop is the enlarged mobile layout.

Guards that must never be dropped: citation gate (narration without evidence refs = build error) and redaction before render (task cards contain `.env` values and client names; publishing defaults to `never`).

## Client build & UI state persistence

- The client (`client/`) is served by the `codx-junior-client` container via `scripts/run_client.sh` → `npm run preview` on the **built** dist. A source change is not live until it is rebuilt (`vite build`). Node lives in the container under `~/.nvm/versions/node/v24.12.0/bin/node` (nvm is not on the exec PATH).
- Safe verification of a client change: `node .../node_modules/vite/bin/vite.js build <client> --outDir /tmp/dist-check` (never build into `dist/` for a check — it empties the live bundle).
- UI screen state persistence: dockview layout in `localStorage` key `projectLayout_<project>` (store `client/src/store/views.js`, saved via `onLayoutChanged`, debounced); UI prefs in `localStorage` key `uiState` (`client/src/store/ui.js`). Per-panel details (board, view, open task) travel in the panel `params` through `$storex.ui.updateAppParams`.
- Board panels: `client/src/components/kanban/board/KanbanBoardView.vue` owns `activeView` (`board|table|gantt|planning|followup`); new panels default to the table (`DEFAULT_VIEW`).
