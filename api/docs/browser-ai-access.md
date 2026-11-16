# Faster AI access to the shared browser (co-driving)

> Goal: the human and the AI can alternate on the same page without the AI
> stalling, failing, or needing an extra model turn every time.

## What was slow

Measured from the recorded session journals
(`.codx/browser_sessions/*/activity.jsonl`, 313 AI actions):

| Signal | Measurement |
| --- | --- |
| Browser action (inside the tool) | `execute` 130 ms median, `click` 439 ms, `navigate` 545 ms |
| Gap between two AI actions | **~5 s median** (the model turn, not the browser) |
| AI failures caused by co-driving | **11 of 29** failures were `The user is interacting with the page…` |
| Cost of one such failure | 3 s stall + one extra model turn (~5 s) + retry |

Root cause was the control lease: every human click/keypress opened a **10 s**
`user_active_until` window, while the AI only waited **3 s** and then raised
`BrowserControlError`. In practice the AI was locked out for the whole 10 s,
failed anyway, and paid an extra turn. The browser itself was never the
bottleneck.

## What changed

### 1. Quiet-gap gating instead of a 3 s hard failure

`BrowserSession` now separates two questions:

- `user_is_active()` — the broad 10 s cooldown used by the UI ("the human is
  driving").
- `user_is_typing(quiet_seconds=1.5)` / `ai_may_act_now()` — the narrow
  debounce the AI needs: has the human **stopped feeding events**?

`BrowserRuntime._wait_for_control` and `DesktopRuntime.run` now:

- fail immediately on a real hand-off (`controller="user"` / `"none"`);
- otherwise wait for a **1.5 s quiet gap** (polled every 150 ms, up to 15 s by
  default) and then run the action;
- return as soon as the user presses **Give back to AI** (that clears
  `user_active_until`, so `ai_may_act_now()` is immediately true).

The deadline is configurable with `CODX_BROWSER_USER_WAIT_SECONDS` (1–120 s).

`browser_status` / `computer_status` now expose `user_typing` and `ai_may_act`
next to the broad `user_active` flag, and append a one-line instruction ("the
page is quiet — you may act now" / "the user is typing — park a wait"). This
stops the model from treating a recent click as a hand-off and pausing itself.

### 2. `browse_action(action="wait")` — park one call instead of polling

With no `text` / `selector` / `url`, `wait` now blocks until the human acts or
the page navigates, then returns:

- `changed` + `reason` (`user` / `navigation` / `timeout`),
- current `url` / `title` / `controller`,
- the activity entries recorded during the wait,
- a compact page snapshot (refs + text) when something changed.

So a co-driving turn becomes:

```text
browse_action(action="wait", timeout=20000)
  -> "page changed (user) after 1840 ms"
  -> activity: user: click · Sign in
  -> page: { interactive: [e1 Sign in ...], text: ... }
```

instead of `browser_activity` polled once per model turn. Waits are read-only,
so they also work while the human owns the control lease.

### 3. `execute` accepts top-level `await`

`normalize_script` now wraps scripts that contain a top-level `await` (or a
single-line statement such as `const x = 1`) in an `async` arrow. Playwright
awaits the returned promise, so model-written code such as:

```js
const r = await fetch('/api/session'); return r.status
```

works instead of failing with
`SyntaxError: await is only valid in async functions` and costing a retry turn
(4 recorded failures). Single-line `const`/`let`/`var` statements are wrapped
too, fixing the `Unexpected token 'const'` failures.

### 4. Desktop sessions use the same rule

`computer_action` raised its `user_wait` from 3 s to 15 s, and
`DesktopRuntime` now waits for the same quiet gap. A human who just clicked on
the shared desktop no longer blocks the AI for 10 s.

## Files

- `codx/junior/browser/model.py` — `seconds_since_user_event`,
  `user_is_typing`, `ai_may_act_now`.
- `codx/junior/browser/runtime.py` — quiet-gap `_wait_for_control`,
  `wait_for_change`, user-event sequence.
- `codx/junior/browser/browser.py` — `normalize_script` async/statement
  wrapping.
- `codx/junior/tools/browser_tools.py` — `wait` without a target, read-only
  waits, snapshot in the wait result, clearer "still interacting" guidance.
- `codx/junior/computer/runtime.py`, `codx/junior/tools/computer_tools.py` —
  desktop parity.
- `codx/junior/profiles/browser.profile` — co-driving instructions for new
  installs.
- `tests/browser/test_browser_ai_flow.py` — regression tests for the above.

## How to verify

1. Start a browser session, then type/click in the live view.
2. Ask the AI to act: it should wait for a short pause and complete the action,
   not fail after 3 s.
3. Ask the AI to wait for you (`browse_action(action="wait")`), do something in
   the live view, and confirm the call returns with the activity and a fresh
   snapshot.
4. Run a script with `await` through `browse_action(action="execute")`.
