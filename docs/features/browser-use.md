# Browser use and co-browsing

> Browser sessions are the `kind="browser"` case of
> [co-desktops](/features/computer-use). The same manager, control lease
> and activity trail now also drive Linux desktops, Windows/macOS VMs and remote
> VNC/SSH machines.

Browser use gives every chat a real Chromium browser that **you and the AI
share**. It is not an AI-only headless browser and not a second "user browser":
there is one page, one history, one cookie jar and one live view.

## What you can do

- Open a browser session from **Coworking** or from a chat and watch the
  page live while the AI works.
- Click, type, scroll and navigate directly in the live view; the AI sees the
  same page on its next tool call.
- Ask the AI to navigate, click, fill forms, run JavaScript, read the page text,
  list links, take screenshots, inspect the HTML source, or review what the user
  did recently.
- Hand control back and forth explicitly. The AI pauses when the human types or
  clicks in the live view, and can request a handoff for login/MFA/captcha.
- Share the live page with other people. **Auto** mode lets a click take the
  control lease; **Request** mode asks the current owner to approve before the
  keyboard/mouse moves. Observers keep watching, and the participant bubbles
  show who is connected.
- Keep the browser profile (cookies, logins, local storage) across restarts.
- **Reuse a login in another browser.** Save the sites' cookies/local storage as
  a *site session* and apply it to the next session (any Chromium-family image),
  and keep passwords in the same encrypted store: the AI can fill a login form
  without ever seeing the value.
- Save and replay per-site scripts in one tool call instead of one LLM turn per
  step.
- **Record a flow in the coworking screen** — do "Create a post" once, stop the
  recording, and the generated steps become a recipe the AI can replay. Record
  over an existing recipe to update it (the old version is archived).

## Architecture

```text
                   codx-junior_default Docker network
 ┌────────────────────┐        CDP (port 9223)        ┌──────────────────────┐
 │  API / SmolAgent   │ ────────────────────────────► │  Chromium container  │
 │  Playwright        │                               │  ┌────────────────┐  │
 └─────────┬──────────┘                               │  │ CDP proxy      │  │
           │                                          │  │ 9223 → 9222    │  │
           │ REST / Socket.IO                         │  └────────────────┘  │
 ┌─────────▼──────────┐      Traefik route            │  ┌────────────────┐  │
 │  User browser      │ ────────────────────────────► │  │ Selkies VNC    │  │
 │  live view iframe  │      /browser/<id>/           │  │ port 3000      │  │
 └────────────────────┘                               │  └────────────────┘  │
                                                      └──────────────────────┘
```

- **User control / viewing** — the LinuxServer.io Chromium image exposes a
  Selkies web desktop. Traefik routes it at `/browser/<session_id>/`; the
  session is configured with `SUBFOLDER` so all assets and websockets work
  behind the path prefix.
- **AI control** — Chromium binds DevTools to `127.0.0.1` and rejects non-local
  `Host` headers. A tiny in-container proxy (`cdp_proxy.py`) listens on
  `0.0.0.0:9223`, rewrites the header and tunnels the connection to
  `127.0.0.1:9222`. Playwright connects to the proxy over the shared Docker
  network; both actors therefore drive the **same page**.
- **Persistence** — the container mounts
  `.codx/browser_sessions/<session_id>/chromium_profile` at `/config`.
- **Runtime** — the API keeps one Playwright connection per session (idle
  disconnect after 5 minutes) and serializes actions with a per-session lock.
  A 1-second watcher drains the in-page recorder, so human clicks/edits/tab
  switches show up as `actor=user` activity without polling the whole log.
- **Navigation** — `domcontentloaded` plus a short best-effort `load` wait;
  `networkidle` is no longer used (it timed out on live sites and the old code
  navigated a second time).

## Using it

### From the UI

Open **Coworking**, create a browser session with a name, an optional start URL
and a browser image (recent or from the tag-filtered catalog), then select it. The live browser is embedded in the panel:

- address bar, back, forward and refresh send actions through the REST API;
- clicking and typing in the iframe controls the browser directly;
- the control badge shows who is driving (`AI`, `You active`, `You drive`);
  **Take control** pauses the AI and **Give back** resumes it;
- a handoff banner appears when the AI needs the human (login/MFA/captcha);
  complete it and click **I'm done — give back to AI**;
- the **Ask AI** button sends the current URL, title, selected text and active
  field to the browser chat;
- the **Shared activity** panel shows `User`, `AI` and `System` actions live
  (click/fill/submit/scroll/tab switch included);
- the **AI cost** panel shows the SmolAgent token usage and cxjcoins of every
  chat linked to the session;
- the session list on the left can be collapsed with the chevron on its edge,
  giving the live browser and chat more room (the state is remembered);
- **Record** starts a named recording of your clicks/typing. While recording,
  a floating red control shows the elapsed time and captured action count.
  Pressing it stops the recording and asks codx-junior to review the trace.
  **Recipes** lists every saved flow for the site with **Record**, **Run**,
  **Edit** and **Delete**;
- **Chat** at the bottom opens the chat picker: choose an existing chat,
  open one of the chats already linked to the session, or click **New chat** to
  create one. The chat gets the `browser` profile and shares the page with the
  live view, so you can ask the AI to act on what you are watching without
  leaving the screen.

### From a chat

Browser tools are available to the agent by default (`browser` tools):

| Tool | Purpose |
| --- | --- |
| `start_session` | Start or resume the chat's shared browser. Returns `session_id` and the live-view URL. |
| `browse_action` | `navigate`, `click`, `fill`, `execute`, `text`, `links`, `snapshot`, `back`, `forward`, `refresh`, `wait`, `press`, `select`, `hover`, `scroll`, `upload`, tabs, and `batch` (several steps in one call). Click/fill by `ref` from a snapshot or by selector/text. |
| `browser_agent` | Hand a whole multi-step goal ("attach this picture and publish the post") to the [browser-use](#the-browser-use-agent) agent on the same browser. It runs its own model loop, its model calls are accounted in the chat through the [LLM gateway](#model-calls-go-through-codx-junior), and it returns one final answer. |
| `browser_status` | Connection, URL/title, tabs and who controls the page. |
| `browser_control` | `status`, `handoff`, `take_control`, `give_back`, `pause`. |
| `get_screenshot` | Capture the current page (the user sees the image inline). |
| `get_source_code` | Return the page HTML (capped to keep model context small). |
| `browser_activity` | Recent user/AI actions, so the AI notices manual changes; supports `actor` and `since` filters. |
| `list_browser_scripts` / `get_browser_script` | Find/read a saved recipe. |
| `run_browser_script` | Execute a whole saved recipe in one call, on one connection. |
| `save_browser_script` / `delete_browser_script` | Maintain the per-site library (versioned, soft delete). |
| `finish_session` | Stop (or delete) the session, keeping the Chromium profile. |

The running chat id is injected automatically into these tools, so a session
started by the AI is linked to the chat the user is looking at.

## Files in and out: uploads and downloads

A browser session shares two kinds of folders with the machine: its
**downloads** folder (`.codx/browser_downloads/<session_id>/`, mounted at
`/downloads` unless it already lives inside a shared folder) and every
**shared project folder**. Files the browser downloads land in the downloads
folder, so they are immediately available to the API, the AI and the user.

Uploading is the reverse direction and it now works for the *remote* container
Chromium too: `browse_action(action='upload', selector=<file input or upload
button>, files=['photo.jpg'])`. **The browser image's own file dialog is not
used**: on the kiosk image it opens but does not deliver the picked file to the
page (the desktop image works because it has a real desktop session and file
manager). The bytes are always streamed over CDP instead.

For the **human**, the same path is in the viewer's **⋯** menu:
**Send a file to the page**. It lists the session's files, lets you upload one
from your computer into the session's downloads folder, and pushes it into the
page's uploader (with an optional selector when the page has several). The REST
equivalents are `POST /api/browsers/{id}/files/upload` (multipart `file`, into
the downloads folder) and `POST /api/browsers/{id}/files/send-to-page`
(`{path, selector?}`).

- A name is looked up in the session's downloads folder first, then in the
  shared folders; `/downloads/photo.jpg`, a folder-relative path and a host
  absolute path are accepted too. Paths outside those roots (and symlinks that
  point outside) are refused.
- The file's **bytes are streamed over CDP**, so the file does not need to
  exist inside the container and the API host path is enough. That is what
  makes a LinkedIn "add a photo" dialog, a hidden `<input type="file">` and a
  drag-and-drop upload widget all work.
- If the selector is a button, the click is intercepted: a file chooser is
  answered directly, or the input that the click opened (a modal) is filled.
  Without a selector the page's file input is used; when none exists the error
  tells you to pass the upload button's selector.
- `CODX_BROWSER_MAX_UPLOAD_BYTES` caps one file (default 64 MB).

The browser container is a kiosk Chromium with no desktop, so its **native file
dialog cannot browse the project folders** — that is why picking a Downloads
file by hand fails there while it works in a desktop session (which has a real
file manager). Uploads from a browser session go through the tool (the AI runs
it, or asks the human to confirm the file name), which is also what browser-use
does with `upload_file(...)`.

## Reusing logins: site sessions and passwords

Each browser session mounts its own profile, so a new session — and every other
browser image — starts logged out. The project keeps a small encrypted login
store instead, shared by all of its sessions:

- **Site sessions** capture the cookies and the localStorage of the sites the
  browser is logged into. Save one from the viewer (**⋯ → Sessions &
  passwords → Save this browser**), then open the next browser and press
  **Use here**: the sites come back logged in. The AI uses the same store with
  `browse_action(action="save_session", value="<name>")`,
  `restore_session` and `list_sessions`.
- **Passwords** are added in the same dialog (name, URL, username, password).
  The password is only ever written encrypted. **Copy** reveals it to the human
  (the access is audited); the AI fills a login form with
  `browse_action(action="login", value="<credential name>")` and the value is
  never returned to the model. MFA/captcha still goes through a hand-off.

Storage lives in `.codx/browser_store/`: `index.json` keeps names, origins,
users and dates; the per-entry `.enc` files keep the state/password; `audit.jsonl`
records save/apply/reveal rows with names only (never values). It is encrypted
with the project vault key (`CODX_JUNIOR_VAULT_KEY`, or the key file used by the
secrets vault) and gitignored. Without `cryptography` or the key the store
refuses to write instead of falling back to plaintext.

Limits: the store works with CDP browsers (Chromium family). Firefox images are
live-view only, so the runtime cannot import/export their cookies — use the
browser's own profile there. The current page is reloaded when it belongs to a
restored origin; other sites are logged in next time they open.

## Supported browsers

Browser sessions pick their image in the create dialog: **recent images** one
click away, and **Browse the catalog** for everything else. The catalog is one
merged feed (`GET /api/computers/images/catalog?kind=…`): one grid, one search,
one **tag** filter, no provider tabs. For `kind=browser` the grid carries the
browser images; for desktop kinds it merges desktops, applications, saved
computers and custom images.

- **Tags** are the provider (`linuxserver`, `kasmweb`, `browseruse`, `saved`,
  `custom`), the type (`desktop`, `application`, `browser`), and the
  capabilities (`chromium`, `firefox`, `cdp`, `live-view-only`, `kasmvnc`,
  `selkies`, plus the browser name). The search box matches all of them, and
  the tag chips filter with AND.
- **Recent** is per browser (`localStorage`), filtered by machine kind, and also
  filled from the sessions that already exist in the project.
- For `kind=browser` the feed contains only browser images (saved/custom images
  are desktops and cannot host a browser session); for desktop kinds it merges
  desktops, applications, saved computers and the custom gallery.

Chromium-family images expose CDP, so the AI and the human drive the same page.
Firefox-family images have no CDP: the session is **live-view only**
(`control=none`, the AI hands the page over).

| Image | Engine | AI (CDP) | CLI env |
| --- | --- | --- | --- |
| `linuxserver/chromium` | Chromium | yes | `CHROME_CLI` |
| `linuxserver/chrome` | Chromium | yes | `CHROME_CLI` |
| `linuxserver/ungoogled-chromium` | Chromium | yes | `CHROME_CLI` |
| `linuxserver/brave` | Chromium | yes | `BRAVE_CLI` |
| `linuxserver/msedge` | Chromium | yes | `EDGE_CLI` |
| `linuxserver/opera` | Chromium | yes | `OPERA_CLI` |
| `linuxserver/vivaldi` | Chromium | yes | `VIVALDI_CLI` |
| `linuxserver/firefox` | Firefox | no (live view) | `FIREFOX_CLI` |
| `linuxserver/librewolf` | Firefox | no (live view) | `LIBREWOLF_CLI` |
| `linuxserver/zen` | Firefox | no (live view) | — |
| `linuxserver/mullvad-browser` | Firefox | no (live view) | — |

- **Safari** has no Linux build: test it in a **macOS VM** session (the macOS
  images ship Safari), where the AI drives the guest through the VM control
  channel, not CDP.
- **Proton** is not a browser (Proton Mail/VPN are services): the privacy
  browsers covered here are Mullvad, LibreWolf and Zen (plus Tor via the Kasm
  Tor Browser desktop).
- Kasmweb browser *apps* (Chrome, Firefox, Edge, Brave, Tor) remain desktop
  sessions (KasmVNC + manual/desktop control), not browser-session images.
- The default is still `CODX_BROWSER_IMAGE` (`lscr.io/linuxserver/chromium`).

## The browser-use agent

`browse_action` is one action per tool call. For a long flow (attach a picture
and publish a post, fill a whole application, book the earliest slot) that costs
many rounds and a lot of context. `browser_agent` delegates the goal to
[browser-use](https://github.com/browser-use/browser-use), which brings its own
agent loop: its model writes Python cells against raw CDP in a persistent REPL,
keeps the page state itself and returns one final answer.

### One browser, two layers

There is still **one browser**: the Chromium container whose page the human sees
in the live view. browser-use attaches to that browser's DevTools endpoint - it
never launches a second Chromium, so the page, cookies, profile and live view
are shared.

What exists in two layers is the *driving* code, on purpose:

- **AI driver: browser-use.** Anything that needs reasoning over several steps
  goes through `browser_agent`, so the product has one AI browsing technology.
- **Deterministic control plane: our Playwright runtime.** The live-view UI
  buttons, the in-page recorder, recorded recipes (`run_browser_script`),
  screenshots and single `browse_action` steps need one action executed now,
  without an LLM loop. That layer is also what enforces the control lease and
  writes the activity trail.

So the AI does not have two sets of browsing behaviour to choose from: long work
is browser-use, one deterministic step is an action - both on the same page,
recorded in the same trail.

### Model calls go through codx-junior

browser-use owns its own model conversation, but it never talks to the provider
directly. When a run starts, codx-junior mints a short-lived **signed run token**
(bound to the run, chat, browser session, project, user and expiry) and points
the worker at `http://127.0.0.1:<api>/api/ai/llm/v1`; the token is the worker's
API key, so the provider key never leaves the API.

Every model call is:

- **authorized** by the run token (forged/expired tokens are rejected with 401);
- **limited** by the user's wallet (the same daily spend check chat uses), the
  chat budget (`Chat.estimation_cost`) and the optional per-run cap
  `CODX_BROWSER_AGENT_MAX_COST_CXJ` (402 as an OpenAI-shaped error, so the agent
  stops instead of spending);
- **forwarded** to the OpenAI-compatible endpoint of the **model carried in the
  token** (the chat/profile model), with that model's provider key on the server
  side;
- **logged** in analytics with the chat id, the browser session and the
  `browser-use` tag (tokens, duration, provider, prices);
- **priced** in cxjcoins with the model's `input_k_tokens_cxjcoins` /
  `output_k_tokens_cxjcoins` and appended to `.codx/llm_runs/<run_id>.jsonl`.

At the end of the run the tool reports that total as `ToolResponse.cost`, so
SmolAgent adds it to the chat exactly like a normal round: the chat header cost,
the `Chat.cost` budget and `/api/browsers/<id>/cost` all include browsing. With
`CODX_LLM_GATEWAY=off` the worker calls the provider directly and the tool says
the run was **not accounted**.

Notes:

- browser-use 1.0 calls the model without streaming; the gateway serves
  non-streaming chat completions (`"stream": false`), which is the only shape it
  sends. A streaming client gets a clear 400 instead of a half-forwarded SSE.
- The default base URL is the API loopback (`CODX_JUNIOR_API_PORT`, 19980);
  `CODX_LLM_GATEWAY_URL` overrides it for another topology.
- Run tokens are self-contained (HMAC-signed with the installation persistent
  secret) and the spend ledger is a locked append-only file, so any API worker
  can serve a call - `WEB_CONCURRENCY` can be raised, as long as the workers
  share the project folder (they do inside one container).

### Co-browsing rules for the agent

browser-use runs as part of the session, not beside it:

- **The human always wins.** A run is paused the moment the control lease moves
  or the human starts clicking/typing in the live view: the worker's process
  group is stopped, the page keeps whatever the agent had already done, and the
  trail records `browser_use_pause` (actor `user`). The assistant must not retry
  blindly - it waits (`browse_action(action="wait")`), reads
  `browser_activity(actor="user")` and continues from there.
- **Context.** The agent's system message carries the session's recent human/AI
  actions, the current URL and the rule that it shares the page with a person.
- **Hand-off.** When the task needs the human (login, MFA, captcha, payment),
  the agent stops and says exactly what is needed; SmolAgent turns that into
  `browser_control(action="handoff", reason=...)` for the UI banner.

How it is connected to SmolAgent:

- **Same browser.** The agent attaches to the session's DevTools endpoint
  (`ws://…:9223`), so it drives the page the human is watching in the live view,
  with the same cookies and tabs. Stopping the run only stops browser-use's own
  harness daemon; the Chromium container keeps running.
- **Same model as the chat turn.** The browser agent does not choose a model:
  the tool receives the model this turn resolved (`inject_llm_model`) and the
  run token carries it. The chain is the chat engine's own — `chat.llm_model`
  (the chat's selected model), else the first profile that declares
  `llm_model`, else the project default (`_resolve_profiles_and_model` in
  `chat_engine.py`) — so a chat that defines a model or a profile-with-model
  gets a browser agent with the same brain. The gateway resolves the provider,
  key and **prices for that model**, and the model cannot be overridden from the
  agent's arguments (the tool schema has no model parameter).
  `CODX_BROWSER_AGENT_MODEL` remains only as a fallback when no turn model is
  injected. Any OpenAI-compatible endpoint works; no Browser Use account/key is
  needed.
- **Same files.** The session's downloads folder is the agent's
  `workspace/downloads` (a symlink), and the shared folders are under
  `workspace/shared/`, so it attaches real files with `upload_file(...)` and its
  downloads land where the user expects them.
- **Same conversation record.** The run is written to the session activity
  trail (`browser_use_start` + `browser_use`, with steps, tokens, cost and
  downloads) and returned to SmolAgent as a normal tool result. SmolAgent keeps
  the conversation with the human; the browsing sub-conversation belongs to
  browser-use.
- **Isolated dependency.** browser-use pins its own pydantic / pydantic-ai / MCP
  versions, so it is *not* imported into the API: the released image installs it
  in `/opt/browser-use` and the API runs
  `api/scripts/browser_use_worker.py` (JSON over stdin/stdout) with that
  interpreter. `CODX_BROWSER_USE_PYTHON` points at any other interpreter that
  has it (for development `pip install 'browser-use==1.0.8'` in the API venv and
  it is used directly). When it is missing, `browser_agent` says so and
  `browse_action` (including `upload`) keeps working.
- **Paused by the human.** Every ~1 s the run checks the control lease; on the
  first human input the worker and its harness daemon are terminated (their own
  process group), so the agent cannot keep driving under the human's hands.
- **Bounded.** `max_steps` (default 30, max 200) and `timeout` (default 300 s,
  max 900 s) are sent with the task; `CODX_BROWSER_AGENT=native` disables the
  delegation completely, `CODX_BROWSER_AGENT_JUDGE=true` enables browser-use's
  own answer judge.

The browser *image* is unchanged: it is the LinuxServer Chromium that provides
the live view and the CDP endpoint. The official `browseruse/browseruse` image
is headless (no desktop and no live view), so using it as the session container
would remove the co-browsing view; browser-use is therefore the agent engine,
not the container.

## Record a flow once, replay it forever

The coworking screen already captures the human's clicks, edits, submits,
Enter keys and tab switches (that is what powers the shared activity trail).
The recorder turns that same stream into a deterministic baseline, then asks
codx-junior to review it like a Selenium/Playwright trace:

1. Open **Recipes**, find the flow (for example `Create a post`) and press
   **Record** on its card. The dialog closes and a floating red recording
   control appears over the live browser.
2. Do the task once in the live view.
3. Press the floating recording control. codx-junior receives the ordered
   transcript (selector, tag, label, value, URL, redacted secrets) plus the
   existing recipe and the current page context.
4. The model removes recorder noise (focus clicks, duplicate clicks, scrolls),
   merges click+edit into fills, improves selectors it can justify and returns
   the final steps. The recipe is updated in place: the previous body is
   archived under `versions/` and the version is bumped.
5. The result dialog shows what the AI changed, any warnings and the final
   steps. Close it, edit a selector, or run the recipe immediately.

Recording a **new** flow uses the same **Record** button in the footer: choose
the title/site first, then the floating control; the reviewed steps land in the
save dialog instead of updating an existing recipe.

If the AI provider is unavailable or returns unusable JSON, the deterministic
recorder steps are used and the dialog says so explicitly. The fallback never
loses a recording.

Recording conversion rules:

| Recorded activity | Generated step |
| --- | --- |
| navigation | `{"action": "navigate", "url": "..."}` |
| click | `{"action": "click", "selector": "..."}` |
| text edit / contenteditable input | `{"action": "fill", "selector": "...", "value": "..."}` |
| `<select>` change | `{"action": "select", "selector": "...", "value": "..."}` |
| Enter | `{"action": "press", "selector": "...", "key": "Enter", "optional": true}` |
| scroll / tab switch / submit | intentionally ignored (noisy or implied by the click) |

A password or token field is redacted by the recorder before it leaves the page;
its step becomes a `{{value}}` placeholder you pass at replay time.
Other text you typed is stored in the recipe (that is what makes it replayable),
so do not record flows that contain secrets.

### Managing recipes

- **Run** executes every step on the shared session in one backend call (no LLM
  turn per step).
- **Edit** loads the steps into the same editor, so a broken selector can be
  fixed without re-recording the whole flow.
- **Delete** is soft: the folder moves to `.codx/browser_scripts/.trash`.

The same recipe operations are available over the API — the [REST API](#rest-api)
table below lists the recording and script sub-routes of a session.

`/api/browsers` is the canonical prefix for these browser-only routes. The
published `/api/computers` route table ([computer use § REST API](/features/computer-use#rest-api))
documents the shared session routes and records that `/api/browsers` remains a
backwards-compatible alias, so a session answers on either prefix; the recording
and script sub-routes are documented under `/api/browsers` only.

## Configuration

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `CODX_BROWSER_IMAGE` | `lscr.io/linuxserver/chromium:latest` | Browser container image. |
| `CODX_BROWSER_PREFETCH` | `1` | Capture the next snapshot/screenshot in the background. |
| `CODX_BROWSER_PREFETCH_QUIET` | `0.7` | Seconds of quiet after the last action before prefetching. |
| `CODX_BROWSER_PREFETCH_TTL` | `20` | Seconds a prefetched snapshot remains valid. |
| `CODX_BROWSER_PREFETCH_SCREENSHOT` | `1` | Also prefetch a viewport screenshot. |
| `CODX_BROWSER_RECORD_AI` | `1` | Ask the project's model to review a recording and clean up the recipe steps. |
| `CODX_BROWSER_NETWORK` | `codx-junior_default` | Docker network shared with the API. |
| `CODX_BROWSER_AUTO_PULL` | `1` | Pull the image when it is missing. |
| `CODX_BROWSER_PUBLIC_BASE_URL` | — | Override the public live-view base URL (e.g. behind a different proxy). |
| `CODX_BROWSER_AUTH_MIDDLEWARE` | `codx-junior-auth` | Traefik middleware on the live-view route; set empty to disable. |
| `CODX_BROWSER_CDP_PROXY_PORT` | `9223` | In-container CDP proxy port. |
| `CODX_BROWSER_START_URL` | `about:blank` | Default URL when no session URL is supplied. |
| `CODX_BROWSER_MAX_UPLOAD_BYTES` | `67108864` | Maximum size of one uploaded file. |
| `CODX_BROWSER_AGENT` | auto | `browser-use` or `native`; auto uses browser-use when available. |
| `CODX_BROWSER_USE_PYTHON` | `/opt/browser-use/bin/python` | Interpreter that owns browser-use (the worker). |
| `CODX_BROWSER_AGENT_MAX_STEPS` | `30` | Default model requests per browser-use run. |
| `CODX_BROWSER_AGENT_TIMEOUT` | `300` | Default seconds for the browser-use loop. |
| `CODX_BROWSER_AGENT_MODEL` | — | Fallback model only when the chat turn resolved none (the chat's model always wins). |
| `CODX_BROWSER_AGENT_JUDGE` | `false` | Enable browser-use's own answer judge. |
| `CODX_BROWSER_AGENT_MAX_COST_CXJ` | — | Optional per-run cap in cxjcoins (0/empty disables it). |
| `CODX_LLM_GATEWAY` | `on` | Route delegated-agent model calls through codx-junior (on/off). |
| `CODX_LLM_GATEWAY_URL` | loopback API | Client base URL of the gateway (`…/api/ai/llm/v1`). |
| `CODX_LLM_GATEWAY_TIMEOUT_SECONDS` | `300` | Timeout of one upstream model request. |

The API process needs access to the Docker socket, as it does for workspaces.

## REST API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/browsers` | List sessions. |
| `POST` | `/api/browsers` | Create `{ name, chat_id?, initial_url? }`. |
| `GET` | `/api/computers/images/catalog` | Merged browser-image catalog for a `kind` (alias of the `/api/browsers` prefix). |
| `GET` | `/api/browsers/{id}` | Session details (live view + CDP URLs). |
| `PUT` | `/api/browsers/{id}` | Rename. |
| `DELETE` | `/api/browsers/{id}` | Stop, remove and delete the session. |
| `POST` | `/api/browsers/{id}/action` | Execute a page action. |
| `POST` | `/api/browsers/{id}/control` | Take, give back or pause session control (`take_control`, `give_back`, `pause`). |
| `POST` | `/api/browsers/{id}/screenshot` | Capture a PNG data URI. |
| `GET` | `/api/browsers/{id}/source` | Page HTML. |
| `GET` | `/api/browsers/{id}/files` | Folders mapped into the session + downloaded files. |
| `POST` | `/api/browsers/{id}/files/upload` | Store a local file in the session downloads folder (multipart). |
| `POST` | `/api/browsers/{id}/files/send-to-page` | Push a session file into the page's uploader over CDP. |
| `GET` | `/api/browser-store/sessions` | Saved site sessions (metadata only). |
| `POST` | `/api/browser-store/sessions` | Capture a running session's cookies/localStorage by name. |
| `POST` | `/api/browser-store/sessions/{id}/apply` | Restore a saved site session into a running browser. |
| `DELETE` | `/api/browser-store/sessions/{id}` | Delete a saved site session. |
| `GET` | `/api/browser-store/credentials` | Credential names/sites/users (no passwords). |
| `POST` | `/api/browser-store/credentials` | Add a credential (encrypted at rest). |
| `PUT` | `/api/browser-store/credentials/{id}` | Update a credential. |
| `DELETE` | `/api/browser-store/credentials/{id}` | Delete a credential. |
| `POST` | `/api/browser-store/credentials/{id}/reveal` | Password for the human copy button (audited). |
| `GET` | `/api/browsers/{id}/activity` | User/AI audit trail. |
| `GET` | `/api/browsers/{id}/cost` | SmolAgent cost and token summary for linked chats. |
| `GET/POST/DELETE` | `/api/browsers/{id}/recording` | Read, start, stop, save or discard a recording. |
| `POST` | `/api/browsers/{id}/recording/analyze` | Review the trace with codx-junior and optionally update the recipe. |
| `GET` | `/api/browsers/{id}/scripts` | Recipes for the session's site. |
| `GET/PUT/DELETE` | `/api/browsers/{id}/scripts/{script_id}` | Read, edit or soft-delete a recipe. |
| `POST` | `/api/browsers/{id}/scripts/{script_id}/run` | Run the whole recipe on the shared browser. |
| `POST` | `/api/browsers/{id}/chat` | Reuse/create the chat linked to the session (browser profile active). |
| `POST` | `/api/browsers/{id}/restart` | Restart the container (profile preserved). |
| `POST` | `/api/browsers/{id}/stop` | Stop the container (kept for restart). |

## Auditing and cost

- Every user/AI action is appended to
  `.codx/browser_sessions/<session_id>/activity.jsonl` with timestamp, actor,
  action, parameters, URL, title, duration and result/error. Input values are
  redacted (`value_redacted` + `value_length`) so passwords never land in the
  audit trail. The endpoint tail-reads the file and pushes new entries over
  Socket.IO (`codx-junior-browser-activity`).
- The in-page recorder captures human clicks, edits, submits, Enter keys, scrolls
  and tab switches. It tags AI-dispatched events so they are not attributed to
  the human.
- SmolAgent records token usage and cxjcoins through the existing analytics
  pipeline (`/api/browsers/{id}/cost` reconciles with the chat cost header).
- Tool calls are also archived by SmolAgent's tool-usage analytics, including
  the action, arguments and result.

## Co-browsing protocol

Control is explicit:

```text
ai_driving --user clicks/types--> user_active (AI yields)
user_active --10 s idle--> ai_driving
ai_driving --handoff(login/MFA/captcha)--> user_control (AI paused)
user_control --"I'm done" / Give back--> ai_driving
```

- `POST /api/browsers/{id}/control` (`take_control`, `give_back`, `pause`,
  `handoff`, `status`) moves the lease; the UI shows a control badge and a
  handoff banner.
- The AI must call `browser_activity(actor="user")` after a pause and must not
  act while `controller="user"` or the user is recently active.
- Before clicking, the AI can use refs from `browse_action(action="snapshot")`
  so the target is stable and the page shows what was clicked.
- The page recorder is installed in every tab; when the human switches tabs in
  the VNC view, the runtime follows the visible tab and logs `switch_tab`.

## Performance notes

### Instant browser image

The runtime keeps a background image of the browser in memory: every tab with
URL/title, the active tab's text summary (refreshed from a MutationObserver
counter) and a throttled viewport JPEG. `browse_action(action="state")` returns
it with **no CDP wait**, `start_session` includes it, and `text` / viewport
`screenshot` are served from the image when it is younger than ~3 s. The loop
refreshes once a second, skips while an action is in flight, and the result
reports `reconnects` / `connected_for_s` so a reconnect is visible instead of
guessed.

The browser round-trip is usually 0.3–1 s (p50 in the recorded activity), the
occasional multi-second outlier is a page/container hiccup; the dominant cost of
a co-working step is the **model turn** (7–12 s p50 between two AI actions in
the same session). That is why the tools push `action="state"`, `batch` and
`browser_agent` for multi-step work, and every action result now ends with
`took N ms` so the browser time and the model time are not confused.


- One Playwright connection per session removes the old ~0.45–0.55 s
  connect/disconnect cost per tool call (measured; subsequent actions are
  ~10–20 ms of overhead).
- **Prefetch while the model thinks.** After every page change the runtime
  captures a compact snapshot and (by default) a viewport screenshot in the
  background. `snapshot`, a viewport `get_screenshot`, the annotation board
  and `wait_for_change` serve those from memory when the page has not moved. The
  first action still pays the CDP cost; the follow-up reads do not.
- **Inline page brief.** `browse_action` results for navigate/click/batch/…
  include a bounded `page` summary (URL, title, refs, text). The model sees the
  new page in the same tool result, which removes the usual extra
  `browse_action(action="snapshot")` turn.
- `start_session` lists the recorded recipes for the initial URL, and a
  `navigate` result lists them for the destination site, so the model can call
  `run_browser_script` on its first turn instead of exploring.
- `run_browser_script` executes a 20-step recipe in one LLM turn and one
  connection.
- Session create/stop/delete run off the API event loop; delete uses
  `docker rm -f` and moves the profile to `.trash` before deleting it.
- Activity is tail-read and pushed over Socket.IO; the UI polls only as a
  fallback every 15 s, and cost is refreshed at most once a minute.
- Script metadata lives in `meta.json` (the old `script.json` collided with the
  `steps` code file, which made saved recipes load empty).

## Requirements and notes

- The Chromium image is large (~2 GB compressed). The first session may take a
  while if the image is not cached; `CODX_BROWSER_AUTO_PULL=0` disables pulling.
- One container per session; the live view and the API must share the Docker
  network so the CDP proxy is reachable.
- The browser runs headful inside the container so the user's VNC view and the
  AI's automation share the exact same rendering surface.
