# 002 — `update_task` drops `profiles`

*Card: `a5f95606-7c81-4e80-bffb-d152c8e86132` (`IT team`) · authored shift 4, corrected turn 5.*
*Artifact: `docs/patches/002-update-task-profiles.patch` (5 hunks, +8 / −2).*

## Symptom

`project_manager(action="update_task", profiles=["software_developer"])` returns success and the
task keeps `profiles: []`. The schema documents the parameter, the parameter is accepted, it is
never persisted.

## Chain (verified in the working tree)

| # | Location | Finding |
|---|---|---|
| 1 | `codx/junior/tools/project_manager_tools.py:3483-3491` | Schema advertises `profiles` — *"Taken ONLY from this argument … On update it REPLACES the list"* was a promise without an implementation. |
| 2 | `…:3208-3224` | Dispatcher `action == "update_task"` forwards `board … assignees, tags, schedule, schedule_context` — **`profiles` missing**, although `profiles` is in scope (it is passed to `_action_request_changes` a few lines below). |
| 3 | `…:2529-2546` | `_action_update_task` signature has **no `profiles` parameter** (`assignees`, `tags`, then `schedule=None`). |
| 4 | `…:2615-2634` | Metadata block sets status/board/column/priority/due_date/wake_at/progress/assignees/tags — **no `profiles` key**. |
| 5 | `codx/junior/chat_manager.py:1232-1247` | Write path needs **no engine change**: `update_chat_metadata` loops `setattr(chat, key, value)` for every key with `hasattr` and saves with `chat_only=True`. `Chat.profiles: List[str]` exists (`codx/junior/db.py:460`). |
| 6 | `…:393-404` | `_to_profiles(value)` is the correct normalizer: `None → []`, string → `[string]`, strips, de-duplicates and removes a leading `@` (names are re-emitted as `@name` mentions). Already used by `create_task`. |

## Hunks (all five anchors re-verified against the file in shift 4, turn 5)

| Hunk | Old range (verified content) | Change |
|---|---|---|
| 1 | `2542-2546` `assignees: Any,` / `tags: Any,` / `schedule: Optional[str] = None,` / `schedule_context…` / `) -> ToolResponse:` | add keyword `profiles: Any = None` (legal: every later parameter already has a default) |
| 2 | `2615-2634` `metadata: Dict[str, Any] = {}` … `metadata["tags"] = _to_list(tags)` | add `if profiles is not None: metadata["profiles"] = _to_profiles(profiles)` |
| 3 | `2645-2649` `if not metadata:` / `raise ValueError(` / the two message strings / `)` | extend the "Nothing to update" message with `profiles` |
| 4 | `3216-3225` `status=status,` … `)` inside the dispatcher call | forward `profiles=profiles` |
| 5 | `3486-3490` schema `"description": (` … `)` | document REPLACES / empty-array-clears |

Header arithmetic is exact: net additions +1 (h1), +3 (h2), +1 (h3), +1 (h4) → new-side starts
`2616 / 2649 / 3221 / 3492`. Counts were wrong in the first draft of this artifact (hunk 2 declared
`22` with 23 lines; hunk 3+ starts off by one), which `git apply` rejects as *corrupt patch*.
The file in the tree is the corrected one.

## Apply

```bash
cd <project root>
git apply --check docs/patches/002-update-task-profiles.patch   # must print nothing
git apply         docs/patches/002-update-task-profiles.patch
python -c "import ast,sys; ast.parse(open('codx/junior/tools/project_manager_tools.py').read())"
```

Then reload the API (the tool registry is built at import time) and confirm:

```
project_manager(action="get_task", chat_id="<id>")      # profiles: []
project_manager(action="update_task", chat_id="<id>", profiles=["software_developer"])
project_manager(action="get_task", chat_id="<id>")      # profiles: ["software_developer"]
```

## Regression test (none exists today for `update_task`)

Create a task, then `update_task(profiles=["software_developer"])` and assert `chat.profiles ==
["software_developer"]`; then `update_task(profiles=[])` and assert it is cleared. That single test
covers hunks 2, 3 and 4; hunk 1 is covered by the call succeeding at all, hunk 5 is documentation.

## Status

Artifact authored and statically verified; **not applied to the source**. This checkout exposes no
`validate_file_patch` / `apply_file_patch` tool, no shell tool, and `codx/junior/file_patch/` does not
exist — the only write primitive available to an actor is a whole-file rewrite of a 3604-line module,
which is refused by design (see card `76d97f04`). First operator step is `git apply --check` above.
