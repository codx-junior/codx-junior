# IT team

The IT team runs the codx-junior project. It is a delivery team, not a chat room:
work arrives as cards on the **IT team** board and leaves as a reviewed, tested
and documented increment.

## Roster and hand-off

| Order | Profile | Owns |
| --- | --- | --- |
| lead | `ProjectManager` | scope, splitting, priorities, status reports |
| 1 | `analyst` | a card that fails "could a dev start now?" goes back here |
| 2 | `software_developer` | api code + targeted tests |
| 3 | `web_master` | client code, UI consistency |
| 4 | `qa` | the gate: allow / warn / require_approval / block |
| 5 | `qa_automation` | the tests the gate needs |
| 6 | `ciso` | secrets, deps, shell, auth — review before commit |
| 7 | `reviewer` | diff + evidence review |
| 8 | `repository` | branches and 50/72 commit messages |
| 9 | `devops` | docker, compose, CI, deployment check |
| 10 | `wiki` | docs kept in sync with what ships |
| 11 | `TaskManager` | board grooming, `wake_at`, blocked cards |

## How the team works

1. **One card = one increment.** Every card states the outcome and its
   acceptance evidence (a test, a command, a URL, a file).
2. **Small slices.** A card that cannot be demoed alone is split by the lead
   before it reaches `To Do`.
3. **Validation is not optional.** A card moves to `Review` only with the gate's
   verdict attached; `warn` is acceptable only when the gap is written on the card.
4. **Security first.** Any change touching auth, secrets, dependencies or shell
   goes through `ciso` before `Review`.
5. **Nothing is closed by its author.** `reviewer` closes it.
6. **Autonomy.** Members work the board on their own shifts; the lead reports on
   the **IT team** board and writes a short summary in the messenger channel when
   an increment ships or blocks.
7. **Escalate, never guess.** A card that needs a human decision goes to
   `Blocked` with the exact question and a mention of the user.

## Board columns

`Backlog` → `To Do` → `Doing` → `Review` → `Done`, plus `Blocked` for anything
waiting on a human decision.

## Definition of done

* the change is on a branch with a 50/72 commit message,
* targeted tests pass and the gate verdict is attached,
* `ciso` has reviewed it when the change is security sensitive,
* the docs are updated when behaviour changed,
* the card carries a one-line demo (command, URL or file).
