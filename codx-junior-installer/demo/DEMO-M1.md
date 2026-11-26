# DEMO SCRIPT — M1 · one-command demo bring-up

Written by `software_developer` (shift #20, 2026-11-26) on card `def4ce38`. This is the **M1 run-of-show**
that `DEMO-M2.md` and `DEMO-M3.md` refer to as "the M1 format": until now M1 had only the technical
reference (`demo/ENVIRONMENT.md`, 326 lines) and no script, even though M1 is the milestone that was
actually demoed. Owner card: `b9aaf03c`. Owners: devops (stack + `up.sh`) · web_master (landing/login
verification).

> **BLOCKING FLAG — read before the slot.**
> **No M1 stack has ever been observed answering.** As of 2026-11-26T08:00Z both probes fail:
> `http://localhost:19981/api/health` and `http://localhost:19981/` return `Connection refused (Errno 111)`,
> so QA's **NO-GO** on `de6906e4` still stands. This file is a **runbook**, not the recorded walkthrough
> card `def4ce38` asks for: the recording is blocked on the same missing stack. Every command below is
> quoted from `demo/ENVIRONMENT.md` and `demo/up.sh`; none of the expected outputs in this file has been
> observed on a running stack yet.

**Slot:** the M1 demo slot already ran (forecast 2026-11-21, re-checked 2026-11-25 13:00Z) and produced
**no-go evidence, not a go**. This script is the artefact to execute for the next slot; the date becomes a
commitment only when devops confirms it in a comment on `b9aaf03c`.

---

## PRE-FLIGHT — 90 minutes before the slot

| # | Check | Command | Pass condition |
|---|---|---|---|
| P0 | `up.sh` contract (no Docker needed) | `bash demo/tests/test_up.sh` | `1..26` and `# all 26 checks passed`, exit `0`. **Never yet executed** — the QA runner cannot start its suites (`de08e8d9`); treat a failure here as "contract changed", not "machine broken" |
| P1 | Docker + compose v2 are present | `docker info` / `docker compose version` | both succeed; `docker-compose` (v1) is not accepted |
| P2 | `demo/.env` exists and is filled | `test -f demo/.env && grep -c . demo/.env` | file present; `CODX_JUNIOR_AI_PROVIDER` and the matching key set, or `none` |
| P3 | Stack up and healthy | `./demo/up.sh` then `docker compose -f demo/docker-compose.yaml ps` | the `codx-junior-demo` container `Up (healthy)` |
| P4 | The two status codes, captured before the audience | `curl -fsS -o /dev/null -w '%{http_code}\n' "$(./demo/up.sh --url)/api/health"` and the same for `/` | **`200`** for `/api/health`; **`200`, `301` or `302`** for `/`. Paste both lines in the card comment |

P4 is the pair QA applied on `de6906e4`: a missing code is a **NO-GO**, not a "we will see live". If P1–P4 are
not all green before the slot, do not open the demo on a promise — use the fallback at the end of this file.

`./demo/up.sh --url` prints **only** the URL and exits `0` (`up.sh:182-185`), so it composes safely inside `$( )`.
It does run `require_docker` first (`up.sh:170`), so on a host without Docker it fails and the substitution becomes
an empty string — `curl` would then probe `/api/health` on the local machine and print something that is not the
demo stack's status. If the command prints nothing, read the port from `CODX_JUNIOR_WEB_PORT` in `demo/.env`
(default `19981`) and `curl` `http://localhost:<port>/…` directly. A status code is only evidence when you know
which process answered it.

## DATA TO USE — no production data, no personal credentials

- **Host:** a clean-host machine, not a developer laptop with a pre-warmed stack. The claim being demoed is
  "one command on a clean host", so a pre-warmed machine demos nothing.
- **Platform URL:** whatever `./demo/up.sh --url` prints (default `http://localhost:19981`, set by
  `CODX_JUNIOR_WEB_PORT`). Never hardcode the port in the narration — read it from the command.
- **Login:** the dedicated demo identity seeded from `CODX_JUNIOR_ADMIN_*` on **first boot only** — see
  `demo/ENVIRONMENT.md` §5. Never a personal account, never a colleague's mailbox.
- **AI key:** a demo-scoped key, or `CODX_JUNIOR_AI_PROVIDER=none` if the demo does not need AI. Values live
  only in `demo/.env`; never in this file, a card or a chat message.
- **Workspace:** one workspace named `demo-workspace` containing **one visible item** — an empty list demos
  nothing.

## THE 5-MINUTE WALKTHROUGH

| # | Time | Step | What the audience must see |
|---|---|---|---|
| 1 | 0:00 | One sentence: "this repository, on a clean host, becomes a running platform with one command" | say it, then show it — no slides |
| 2 | 0:30 | Show the clean machine: `docker compose -f demo/docker-compose.yaml ps` | nothing running, so the next step is the only thing that brings the stack up |
| 3 | 1:00 | `./demo/up.sh` | the ordered output: image resolve → `compose pull` → `up -d` → health wait → the printed web URL |
| 4 | 2:00 | `curl -fsS -o /dev/null -w '%{http_code}\n' "$(./demo/up.sh --url)/api/health"` | one line, `200` |
| 5 | 2:30 | Open the printed URL in the browser | the **login page** (SPA served by nginx on `/`) |
| 6 | 3:00 | Log in as the demo identity | the **workspace list** renders, with `demo-workspace` in it |
| 7 | 3:30 | Open `demo-workspace` | the workspace screen of the platform |
| 8 | 4:00 | `curl -fsS -o /dev/null -w '%{http_code}\n' "$(./demo/up.sh --url)/"` and `docker compose -f demo/docker-compose.yaml ps` | `200`/`301`/`302`, then `Up (healthy)` |
| 9 | 4:30 | Stop. Hand over to questions | — |

## ACCEPTANCE CHECKLIST (ticked out loud, from `b9aaf03c`)

- [ ] **A1** `demo/up.sh` brings the stack up **from a clean host with one command** — proven by step 3.
- [ ] **A2** The **landing page is reachable** at the printed URL — proven by step 5.
- [ ] **A3** **Login works** with the demo identity — proven by step 6.
- [ ] **A4** The **workspace list renders**, and one workspace opens — proven by steps 6 and 7.
- [ ] **A5** `demo/ENVIRONMENT.md` is complete and agrees with `up.sh` — a doc claim that disagrees with the
      script is a bug in the doc (`ENVIRONMENT.md` line 7).
- [ ] **A6** QA gate on the M1 diff is **not `block`**.

Not demonstrated, and explicitly **not** claimed in M1: workspace preview (M2), efforts settings (M3), cloud
bootstrap, dind, vllm.

## QUESTIONS FOR MANAGEMENT — two, both decisions

1. **Clean-host or pre-warmed host for the recurring slot?** A truly clean host costs ~3–5 minutes of image
   pull inside the slot; a pre-warmed host makes the demo fast but weakens the "one command from zero" claim.
2. **What happens when `/api/health` is not `200` at slot time?** Options: present the recorded walkthrough
   and the captured status lines (this file's fallback), or cancel. This needs an explicit ruling, because
   the 2026-11-25 slot resolved it by accident — nobody had decided.

## LIVE-FAILURE FALLBACK — the stack does not come up in front of the audience

1. **Say it, do not disguise it.** "The stack is not answering; here is the proof of what works and here is
   the exact failing step."
2. Show the **P4 status lines** captured before the slot and the verbatim failing output of `./demo/up.sh` —
   evidence of the state *before* the demo, not a re-run invented live.
3. Show `demo/ENVIRONMENT.md` §7 against the failing symptom (port in use, unhealthy container, missing key)
   so the audience sees a diagnosis, not an apology.
4. Post the failing step and its verbatim output on `b9aaf03c` the same day, name which of A1–A6 is unmet,
   and report M1 **at risk** with the slippage named — not `done`.
5. Never re-run `./demo/up.sh` live "to see if it fixes itself". A destructive re-deploy in front of
   management is how the demo becomes the incident; `./demo/up.sh --down` keeps volumes but still costs the
   slot.

## THE RECORDED WALKTHROUGH — the part of `def4ce38` still missing

The point of the recording is that the demo does not depend on an audience, a host or a working stack on the
day. Two layers, both cheap once the stack answers:

```bash
# 1. terminal layer: the command, its ordered output, the status codes
asciinema rec demo/M1-walkthrough.cast --title 'M1 one-command bring-up'

# 2. browser layer: login, workspace list, one workspace
#    screen recorder of choice; the artefact is the file, the tool is not specified
```

Then, on the card comment, the three artefacts that let a reader verify without replaying anything:

- the `.cast` (or the `ffmpeg` recording) attached to `def4ce38`, plus the exact `curl` status lines;
- `./demo/up.sh --url` output and `docker compose -f demo/docker-compose.yaml ps` output;
- the commit SHA the recording was made from, so the artefact cannot drift from the code.

**Do not commit the video.** Attach it to the card; the repository keeps the script, not the megabyte.

---

`AMEND AFTER A REAL RUN — every expected output above is quoted from the scripts and has never been observed
on a running stack. The first person to execute P3–P4 replaces "expected" with the observed line and dates it.`
