# DEMO SCRIPT — M3 · efforts feature visible in the UI (SKELETON)

Written by `software_developer` (shift #13, 2026-11-24) on card `fe9f284d`, in the **M1 format** posted on
`b9aaf03c`. Owner card: `140668fe`. **This is deliberately a skeleton, not a script.**

## Why this file is a skeleton

Card `140668fe` (M3) is entirely application code inside `codx-junior-platform`
(`api/codx/junior/efforts/*`, `model/effort.py`, `api/efforts.py`, `efforts.js`, `EffortsSettings.vue`).
Management decision `e70e0272` may place that repository **out of scope** for these shifts. Writing a
confident Settings walkthrough for code that may not be in scope would fabricate a demo for a milestone
that can be re-sliced — so the format, timings, acceptance checklist, questions and fallback are fixed
here and **only the steps are pending**.

> **BLOCKING FLAG — read before the slot.**
> `demo/up.sh` has **never been executed** (`def4ce38` still `todo`), so nothing below has an observed
> stack under it. This skeleton is written against the current scope and is **not** a promise that the
> platform comes up on the day.

**Slot:** Mon 30 Nov 10:00 Europe/Madrid, 15 min total, 5 of them the walkthrough. **Forecast** until the
owner confirms it in a comment on `140668fe`.

---

## PRE-FLIGHT — 09:00 same day

| # | Check | Pass condition |
|---|---|---|
| P1 | M1 + M2 stack | `docker compose ps` → every service `Up` / `healthy` |
| P2 | The efforts flag is set in the running container | the flag is visible in the container env/config — paste the line, redacted if it carries a key |
| P3 | The efforts screen is reachable for the demo user | `curl -sS -o /dev/null -w '%{http_code}\n' <efforts screen URL>` → `200` |
| P4 | Screenshot of the populated screen | one image, attached to the card |

## DATA TO USE — no production data, no personal credentials

- **Host:** the same clean-host machine M1/M2 ran on.
- **Platform URL:** whatever `demo/up.sh` prints (default `http://localhost:19981`).
- **Flag:** the efforts feature flag documented by the owner on `140668fe`. Off by default is the point —
  show it off, then on.
- **Data:** one effort record with a **non-round** value (not `0`, not `100`), so the screen cannot be
  confused with an empty state. Created through the UI, not injected into a database.
- **Login:** the demo credentials documented in `demo/ENVIRONMENT.md`.

## THE 5-MINUTE WALKTHROUGH

`DEMO STEPS PENDING e70e0272 (20 Nov 12:00Z) — if A, M3 is re-sliced and these steps are void.`

Fixed shape, to be filled with the owner's real screens and values (same table as M1/M2):

| # | Time | Step | What the audience must see |
|---|---|---|---|
| 1 | 0:00 | One sentence: "the platform can now track effort, and management can see it" | say it, then show it |
| 2 | 0:30 | Show the flag **off** | the screen/feature is absent or disabled |
| 3 | 1:00 | Enable the flag | the feature appears without a rebuild |
| 4 | 1:30 | Open the efforts screen | the real screen, real data |
| 5 | 3:00 | Create or edit one effort with the non-round value | the value persists after reload |
| 6 | 4:00 | Show where the effort is aggregated/visible to a manager | the number the audience is expected to trust |
| 7 | 4:30 | Stop. Hand over to questions | — |

## ACCEPTANCE CHECKLIST (ticked out loud, derived from `140668fe`'s own DoD)

- [ ] **A1** The efforts feature is **visible in the UI** — not only in an API response.
- [ ] **A2** It is gated by the **flag** — proven by step 2 → step 3 (off, then on, same build).
- [ ] **A3** Real data round-trips: value persists across a reload — proven by step 5.
- [ ] **A4** Deployable + demoable with one command, as M1/M2 were.
- [ ] **A5** QA gate on the M3 diff is **not `block`**.

## QUESTIONS FOR MANAGEMENT — two, both decisions

1. **Who owns an effort?** The user who logs it, or the workspace it belongs to? This decides the data
   model (`model/effort.py`) and cannot be retrofitted cheaply once real records exist.
2. **Is effort a reporting surface or an input surface** for the first release — do managers read
   aggregates (read-only screen, ships faster) or must users fill it in (write path, needs validation and
   edit history)? The answer decides whether M3 ships one screen or two.

## LIVE-FAILURE FALLBACK — the screen does not render in front of the audience

1. Say it plainly: "the screen is not rendering live; here is the evidence it works and here is what is
   broken."
2. Show the **pre-captured screenshot** (P4) and the status line (P3) — state before the demo, not an
   invented re-run.
3. Show the **flag value from P2** so the audience can tell flag-misconfiguration from a code defect.
4. Post the failing step and its verbatim output on `140668fe` the same day, naming which of A1–A5 is
   unmet. M3 then reports **at risk** with the slippage named, not `done`.
5. Never toggle the flag repeatedly or re-deploy live to "have another go" — that turns a demo into an
   incident.

---

`AMEND AFTER M1 — re-check the 70832a68 feedback before the demo; M2/M3 script amendments are owned by 70832a68.`
