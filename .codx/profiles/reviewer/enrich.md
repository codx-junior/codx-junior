## Running pytest / builds for codx-junior verification

## Backend tests (container `codx-junior-api`)
- Project is bind-mounted at the same path inside the container.
- Use the **real venv** `/home/codx-junior/.codx-junior/.venv_codx_junior_api/bin/python`; the throwaway `/tmp/.venv_codx_junior_api` lacks deps (`ddgs`) and aborts collection.
- `docker_exec` caps at ~115 s and the interpreter **never exits** after pytest (non-daemon socket.io emitter thread). Always pass `--junit-xml=/tmp/<name>.xml` and `-p no:cacheprovider`, let the exec time out, then poll `cat /tmp/<name>.xml` — the XML is written at session finish, before the hang.
- `kill` is not permitted in `docker_exec`; `timeout --signal=KILL N ...` is.
- Heavy page-import suites (`tests` at once) crawl on loaded hosts; prefer targeted modules (`tests/logs` = 121 tests/1.2 s).

## Client build (`codx-junior-default-workspace`, node 25)
- `npm --prefix <abs client path> run build-only` → vite build, exit 0.
- `npm run type-check` is permanently red: 42 errors in `vite.config.ts`, `src/main.ts`, `src/components/CodxJuniorLogo.vue`, `src/router/index.ts`. Judge new files by "no error line mentions them", not by exit code.
- `client/tmp-typecheck-report.mjs` already prints `--- files with errors ---` and `--- calendar-related errors ---`; it hardcodes `/home/codx-junior/codx-junior/client`.
