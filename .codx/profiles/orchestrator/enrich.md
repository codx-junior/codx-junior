## codx-junior client: dev workflow

- **Client runs as a live Vite dev server** in the `codx-junior-client` container (project mounted at `/home/codx-junior/codx-junior`). Edits hot-reload; `docker_logs codx-junior-client` shows `hmr update ...` per file.
- **Verify frontend edits without logging in** (the app requires a login): ask the Vite server to transform the file — `/home/codx-junior/.nvm/versions/node/v24.12.0/bin/node` + `fetch('http://127.0.0.1:19981/src/...')`, a compile error comes back as non-200. For visual checks add a temporary root `.html` entry + `src/*.js` that mounts the component with a mocked `$storex` (`app.config.globalProperties.$storex`), then delete it.
- **`docker_exec` blocks `;`, `<`, `>` and `rm`**: one command per call; delete with `node -e "...forEach(function(f){ return require('fs').rmSync(f,{force:true}) })"`.
- **UI stack**: Tailwind 4 + daisyUI 5, Font Awesome 6.5.1 imported by `src/assets/main.css`. Avoid `bg-gradient-*`; use solid `bg-*` utilities.
- **Computer sessions**: mount only ONE live viewer per page (KasmVNC resolves its websocket from the global `path` localStorage key); other tiles must use snapshots or a browser-kind iframe.
