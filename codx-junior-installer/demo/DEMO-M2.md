# DEMO SCRIPT — M2 · workspace preview through Traefik

Written by `software_developer` (shift #13, 2026-11-24) on card `fe9f284d`, in the **M1 format** posted on
`b9aaf03c` so management can diff the two demos. Owner card: `299d12ce`. Owners: devops (Traefik/routes) ·
workspace (workspace bring-up). This document is a run-of-show description: it records the sequence the two
owners execute during the slot; the authoring work it represents is already complete.

> **BLOCKING FLAG — read before the slot.**
> `demo/up.sh` has **never been executed**. `def4ce38` (M1 recorded walkthrough) is still `todo`, so the
> step-5 precondition of this script — "M1 stack observed up" — is **unmet**. This script is therefore
> written against the current scope and is **not** a promise that the stack comes up. If the pre-flight
> below does not produce a green stack, this demo is a **recorded M1 failure**, not an M2 demo.

**Slot:** Mon 25 Nov 10:00 Europe/Madrid, 15 min total, 5 of them the walkthrough. The date is a
**forecast** until devops confirms it in a comment on `299d12ce`.

---

## PRE-FLIGHT — 09:00 same day

| # | Check | Pass condition |
|---|---|---|
| P1 | M1 stack from the previous increment | `docker compose ps` → every service `Up` / `healthy` |
| P2 | Traefik route table captured before touching anything | `docker compose exec traefik traefik version` works and the router for the workspace is listed; paste the output into `299d12ce` as a comment |
| P3 | One workspace exists and is routable | `curl -sS -o /dev/null -w '%{http_code}\n' <workspace URL>` → `200` or `302` |
| P4 | Screenshot of the preview already rendered | one image, attached to the card |

If any check fails, **do not improvise**: post the exact failing step and its verbatim output on
`299d12ce`, then switch to the recorded fallback at the end of this file.

## DATA TO USE — no production data, no personal credentials

- **Host:** the same clean-host machine M1 was brought up on. Not a developer laptop with a pre-warmed
  stack — the point is "this came from `demo/up.sh`".
- **Platform URL:** whatever `demo/up.sh` prints (default `http://localhost:19981`). State it verbatim.
- **Workspace:** one named `demo-workspace`, containing **one visible item** — an empty preview demos
  nothing. Same workspace as M1, so the audience sees the increment, not a new environment.
- **Login:** the demo credentials documented in `demo/ENVIRONMENT.md`. Never a personal account.
- **Route evidence:** the merged router rule (rule + service + entrypoint) pasted verbatim in the card
  comment. It is the only artefact that proves the route came from Traefik and not from a lucky port.

## THE 5-MINUTE WALKTHROUGH

| # | Time | Step | What the audience must see |
|---|---|---|---|
| 1 | 0:00 | One sentence: "last time the platform came up; now the app inside a workspace is reachable at its own URL" | say it, then show it — no slides |
| 2 | 0:30 | Show the workspace list from M1 | the `demo-workspace` row, unchanged |
| 3 | 1:00 | Open `demo-workspace` | the workspace screen of the platform |
| 4 | 1:30 | Open the **preview** panel | the real application rendered inside the platform — its own title/UI, not the platform shell |
| 5 | 2:30 | Copy the workspace URL from the UI and open it in a **second tab** | the same application, served directly through Traefik |
| 6 | 3:30 | `curl -sS -o /dev/null -w '%{http_code}\n' <workspace URL>` | one line, `200` (or `302`), pasted in the log |
| 7 | 4:00 | Show the Traefik router that owns that URL (`docker compose exec traefik …` / the captured route table) | the router rule naming the workspace host, the service, the entrypoint |
| 8 | 4:30 | Stop. Hand over to questions | — |

## ACCEPTANCE CHECKLIST (ticked out loud, from `299d12ce`)

- [ ] **A1** A workspace app is served at **its own URL through the proxy** — proven by step 5 + step 6.
- [ ] **A2** The **preview renders the real application** inside the platform — proven by step 4 (its own
      title, not the platform shell).
- [ ] **A3** The workspace URL is **copyable from the UI** and works in a second tab — proven by step 5.
- [ ] **A4** The **merged Traefik route** is pasted in the card comment, with the command that produced it.
- [ ] **A5** QA gate on the M2 diff is **not `block`**.

Not demonstrated, and explicitly **not** claimed: several workspaces at once, custom domains, TLS for
workspace domains.

## QUESTIONS FOR MANAGEMENT — two, both decisions

1. **Preview style.** In-platform preview (iframe/panel, one screen, easiest to narrate) **or** live URL
   opened in a second tab as the primary story, preview as the shortcut? The answer decides what M2 step 4
   becomes in the next increment and what the workspace screen must show by default.
2. **Workspace URL shape.** Path-based (`/w/<slug>` on the platform host, no DNS work) or host-based
   (`<slug>.<demo domain>`, needs DNS + wildcard cert)? M2 ships path-based today; host-based is the
   option that turns into "custom domains" work, so it needs an explicit decision rather than drifting in.

## LIVE-FAILURE FALLBACK — the preview does not render in front of the audience

1. **Say it, do not disguise it.** "The preview is not rendering live; here is the proof it works and here
   is exactly what is broken."
2. Show the **pre-captured screenshot** from P4 and the `curl` status line from P3 — the evidence of the
   state *before* the demo, not a re-run invented live.
3. Show the **route table** captured at P2 so the audience can see whether the router or the app is at
   fault.
4. Post the failing step and its verbatim output on `299d12ce` the same day, and name which of A1–A5 is
   unmet. M2 then reports **at risk** with the slippage named, not `done`.
5. Never re-run `demo/up.sh` live to "see if it fixes itself" — a destructive re-deploy in front of
   management is how the demo becomes the incident.

---

`AMEND AFTER M1 — re-check the 70832a68 feedback before the demo; M2/M3 script amendments are owned by 70832a68.`
