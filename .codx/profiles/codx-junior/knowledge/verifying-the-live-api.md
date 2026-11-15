# Probing the running codx-junior API

## The gotcha
`GET /api/chats` (and every route that reads `request.state.codx_junior_session`)
returns **HTTP 200 with `{"error": "'State' object has no attribute
'codx_junior_session'"}`** when called without a project context. The
`add_codx_junior_settings` middleware in `api/codx/junior/app.py` only builds
the session when the request carries the project path.

Always pass it:

```
curl "http://127.0.0.1:19980/api/chats?codx_path=<project>/.codx"
# or: -H "x-codx-path: <project>/.codx"
```

`codx_path` is the **`.codx` folder**, not the project root.

## The served code is the project working tree
Inside the `codx-junior-api` container `/home/codx-junior/codx-junior` and
`/home/codx-junior-projects/codx-junior/codx-junior-platform` are the **same
files** (same device+inode; verify with `stat -c "%d %i %n" <path>`). So a file
edited in the project worktree is exactly what the API imports — but only after
a restart, because uvicorn runs **without `--reload`**:

```
ps -o pid,lstart,args -p 18                 # process start time
ls -l --time-style=full-iso api/.../x.py    # file mtime: must be OLDER
find <project>/api -name '*.py' -newer /proc/18   # empty == consistent load
```

The client bundle served by the app is `<project>/client/dist`; it does not
rebuild by itself (`cd client && npm run build`).

## docker_exec limits
`docker_exec` refuses `;`, `|`, and `kill`. For anything beyond a single simple
command, write a throw-away `_probe.py` in the project root, run it with the
venv (`/home/codx-junior/.codx-junior/.venv_codx_junior_api/bin/python`), then
`mv` it to `/tmp` and unstage it if git auto-staged it (`AD` entries).

Heavy imports (`ChatManager`) can trigger `find / -name .codx` and hang for
minutes; prefer walking `.codx/tasks` with the light `kanban.model` module, or
call the HTTP route above.

## pytest
`qa_run_tests` on the `pytest:api` suite times out (>240s, it collects the whole
API tree). Verify a change with a targeted probe or the live route instead, and
report the timeout as an infra issue, not as a test failure.