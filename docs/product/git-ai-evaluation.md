# git-ai / Scion evaluation — multi-task file conflicts in codx-junior

**Status:** decision memo · **Date:** 2026-11-25
**Sources:** [feder-cr/git-ai](https://github.com/feder-cr/git-ai) (v0.4.2, README,
`doc/graft/LIMITATIONS.md`, `doc/graft/RESOLVE.md`, `doc/graft/LAND-BATCHING.md`) and
[Anthropic — Redesigning source control](https://www.anthropic.com/research/redesigning-source-control).
**Repo basis:** HEAD `a061d82`, `api/codx/junior/task_workspace.py`,
`api/codx/junior/engine/git_engine.py`, `api/codx/junior/engine/git_conflicts.py`.

---

## Verdict

**Do not adopt git-ai as infrastructure. Port five mechanisms from it.**

git-ai is a *server-side git hosting replacement* (a Gitaly fork) that optimises landing
throughput and resolves **textual** merge conflicts at landing time. codx-junior's conflict
problem is not landing throughput: it is that N independent task branches are cut from the
same base and edited by agents that never see each other. Most of those collisions are
**semantic** (both tasks edit different regions of one file, merge cleanly, and break the
build), and git-ai does not touch those at all.

The one idea from git-ai that directly fixes our worst failure mode is cheap to build in
our own tree: **a model's conflict resolution is only a proposal until a deterministic
check passes on the exact tree that would land.** That is worth more than the server.

---

## 1. The problem, as our code actually implements it

| Mechanism | Where | Consequence |
|---|---|---|
| Task branch `codx/task/<slug>-<hash>` cut from the current branch at workspace creation | `task_workspace.py:11`, `:66`, `_base_ref:332`, `ensure:391` | Every task starts from a snapshot. Whatever merges into `main` afterwards makes every open branch stale. |
| One worktree per task under `<worktrees>/tasks/…`, registry in `.codx/task_workspaces/index.json` | `task_workspace.py` | Good isolation, good attribution. No cross-task visibility. |
| Merge only: `git merge --no-ff`, conflicts abort unless `keep_conflicts=True`; squash and `no_commit` variants | `git_engine.py:4241` | Landing is a human action in the Git view, one branch at a time, in a hand-picked order. **No rebase-before-land.** |
| AI resolution validates *only* that conflict markers are gone, then writes + stages + optionally commits | `git_conflicts.py:1170` (`resolve_conflicts_with_ai`), `continue_operation:1331` | A plausible-but-wrong merge is committed. Nothing runs the tests on the merged tree. |
| No cross-task awareness anywhere | grep for overlap/lease/path-lock finds only `utils/file_lock.py` (JSON file locks) and git's own `index.lock` | Nothing warns an agent that another task already changed `api/git.py`. |

Four distinct failure modes, and they are not all the same problem:

1. **Textual conflict** — same region of a file edited by two tasks. Git stops, the human
   resolves or asks the model. *This is what git-ai solves.*
2. **Semantic conflict** — same file, different regions; merges clean, breaks behaviour or
   the test suite. *git-ai does not solve this; a post-merge test gate does.*
3. **Stale branch** — task B branched before task A merged, so B "reverts" A's change.
   *git-ai's rebase-on-land CAS solves this; so does a plain rebase-on-land.*
4. **Duplicate work** — two tasks independently add the same helper. *Neither tool solves
   it; only pre-flight path ownership does.*

Mode 2 is the expensive one, because it is invisible at merge time and shows up as a broken
`main`. Mode 1 is the visible one the team complains about. A solution that only addresses
mode 1 will feel like it did nothing six weeks later.

---

## 2. What git-ai is

- A fork of **Gitaly** (GitLab's Git RPC server; MIT). Git repos underneath, a gRPC server
  on top, plus a `graft` CLI and a Go SDK. Extensions are internally called "Graft".
- Storage: **SQLite** (one node) or **PostgreSQL** + object storage (file/S3/GCS/Azure).
  Server nodes are caches; repository state is a changeset WAL + CAS per reference.
- The Scion ideas it implements: `UserLand` = rebase onto tip + compare-and-swap; **land
  batching** through in-memory lanes; **server-side derived data** (blame, path history,
  ancestry) computed once per commit; **changeset log**; **conflict resolution protocol**
  with a resolver program/HTTP service; **`graft-verify`** (test tip, bisect, revert culprit);
  per-agent tokens + RPC allowlist + branch policy; semantic review approvals.
- Maturity: **v0.4.2**, first commit 2026-10-15, ~970 stars / ~100 forks, 3 releases in
  three weeks, "interfaces may still change before 1.0".
- Explicitly **not a hosting product**: no web UI, no issues, no review workflow, no CI.
  Standalone mode replaces GitLab access checks; it does not replace GitLab.
- Operational surface: **Go 1.26 + C toolchain + Meson + Ninja** (it compiles a bundled
  Git), a database, an object store, a cache daemon, per-agent token files. Tests and the
  benchmark run **on one machine**; S3/GCS/Azure are **untested**; pack GC is **off by
  default**. License metadata on GitHub is `NOASSERTION` even though the README says MIT
  (the `LICENSE`/`NOTICE` split inherited from Gitaly) — verify before vendoring.

## 3. What the Anthropic article actually says

Useful for calibration, and it argues *against* copying the heavy parts:

- The forcing function is scale we do not have: **2,500 → 30,000 merged changes/day** in
  four months, ~100 GB of history, 25 GB compressed working copy, ~25M checkouts/day.
- Results are real at that scale: 200× smaller infra footprint than the git-mirror fleet,
  checkouts in seconds, 25% CI CPU win, built by **7 engineers in 5 months**.
- Anthropic states plainly that Scion **is not suitable for general use**, only "truly
  enormous monorepos", and that a public version pleasing everyone "wouldn't be very useful".
  Scion is **not open source**; git-ai is an independent reimplementation of a public
  description, not a port.
- Their landing stance: **no merge queue** ("a proper merge queue doesn't make much sense
  at agent scale"); they invested in **post-merge and pre-deploy automated testing** and
  accept that concurrent changes conflict, betting that Claude fixes conflicts faster than
  a human would notice them.

Read the last point carefully: at 30k changes/day Anthropic chose *test after landing and
fix forward* over *prevent conflicts*. Our equivalent, at our scale, is a verification gate
plus fast conflict resolution — not a landing cluster.

---

## 4. Fit against our actual pain points

| Our failure mode | git-ai mechanism | Fit |
|---|---|---|
| Textual merge conflict on a shared file | resolver protocol + rules, model proposal gated by `verify.command` | **Good idea, wrong delivery vehicle.** We already have base/ours/theirs + AI; we lack the gate. |
| Semantic conflict that merges clean | post-land `graft-verify` (bisect + revert) | **Portable idea.** We can run tests before the merge commit instead of after. |
| Stale task branch reverting a merged task | rebase-on-land + CAS retry | **Portable, and we should already have it.** ~1 day. |
| Duplicate work across tasks | — | **Not addressed.** Needs a pre-flight path-ownership check we build ourselves. |
| Landing throughput (many agents, one branch) | batched lanes, CAS, 1.0 attempts/land | **Not our bottleneck.** Zero benefit at 1–20 tasks/day. |
| Large-monorepo history/blame/checkouts | server-side derived data, sparse/virtual checkouts | **Not our bottleneck.** Our repo is small and local. |
| Agent identity / branch policy / RPC allowlist | per-agent tokens, policy DSL | Interesting, but codx-junior has CISO + profiles; different threat model. |

The benchmark that carries git-ai's claim (50 agents, 250 changes, 20% conflicting: 100 vs
200 landed, 26.5 → 1.0 attempts, p99 5 s → 2.5 s) measures CAS contention on one branch.
None of those numbers transfer to a self-hosted single-container product where a human
merges a handful of branches per day.

---

## 5. Recommendation: port these five, in this order

### 5.1 Verify before land (highest value, ~2–3 days)

git-ai's rule, worth copying verbatim in spirit: *a model's resolution is only a proposal;
it lands only if the configured command passes **on the exact tree that would land**,
otherwise the agent gets the conflict package back.*

Today `resolve_conflicts_with_ai` (`git_conflicts.py:1170`) accepts any answer without
conflict markers and `continue_operation` commits it. Concretely:

1. Add `verify` config to the project: command + timeout + `paths_deny` (skip for docs,
   lockfiles, generated files). `project_scripts` (`model/model.py:155`) already holds a
   bash script per project — reuse the one named `test` by convention, no new setting needed
   for the common case.
2. In the AI-resolve path: stage the resolution, run the command **in the task worktree**
   (we already have a safe isolated checkout for free), and only then allow
   `continue_operation`. On failure, restore the conflicted state and return the failing
   output plus the model's proposal as `PROPOSED`, never applied.
3. Keep it fail-closed by default: no verify command configured → the model's resolution is
   *suggested in the UI*, not committed. Humans can still accept it explicitly. That single
   default change removes the "AI silently committed a bad merge" class of bug.

This also catches semantic conflicts from 5.2 and from plain merges, which is why it ranks
first: it is the only item that addresses failure mode 2.

### 5.2 Land by rebase, not by merge (~1–2 days)

`merge_branches` only does `--no-ff`. Add a `land_branch` path that:

1. rebases the task branch onto the current target tip (`git rebase --onto target base`),
2. on conflict, stops with `keep_conflicts` semantics and hands the conflict state to
   `GitConflictEngine` (already built), re-running the rebase after resolution,
3. retries once if the target moved while we were resolving (git-ai's CAS loop, minus the
   server), and reports `already_landed` when the tip is already an ancestor.

Benefits: linear, attributable task history; no stale-branch reverts; conflicts surface
against the *current* tree rather than an hour-old one. Keep merge as the default for now,
make rebase the default for `codx/task/*` branches.

### 5.3 A versioned conflict package, with proposals (~2 days)

git-ai's `conflict.json` + `sides/{base,ours,theirs}` + `proposals/` is a good contract and
we are ~70% there: `get_conflicts(include_content=True, include_sides=True)` already returns
stages. Formalize it as one JSON object that both the UI and the resolver consume:

```
{ version, branch, target, commit, files: [
    { path, kind: content|modify-delete|add-add|mode|binary,
      base|ours|theirs: {oid, mode, content_ref},
      merged: diff3 text, proposal: {content, origin: rule|model, explanation} } ] }
```

Then the UI can show "propose / accept / edit" instead of the model writing straight into
the worktree, and the API contract stops being an implementation detail of the Vue view.

### 5.4 Cross-task overlap pre-flight (~2 days for warn-only)

The only item that addresses failure mode 4, and the cheapest real fix for "files modified
on multiple tasks":

- On `TaskWorkspaceManager.ensure()` and before each agent turn, compute
  `git diff --name-only $(git merge-base main <branch>) <branch>` for the open workspaces.
- Intersect with the paths the calling task is about to touch (or its own changed set).
- Surface it: (a) in the agent's system prompt as a hard warning — "task X already modified
  `api/git.py`; coordinate or pick another approach"; (b) in the Git view as an overlap badge;
  (c) optionally as a soft *path lease* recorded in the registry so a second task must
  acknowledge the overlap before writing.
- Do **not** implement hard locks first. Two tasks legitimately touch the same file; a lock
  turns a merge conflict into a stalled agent. Warn, attribute, then decide.

Advisory leases are a 100-line addition to a registry that already exists and already has a
cross-process lock (`task_workspace.py:283`).

### 5.5 Changeset log and revert-the-culprit (~1 day, optional)

Commit trailers already name `Task`, `Chat`, `Workspace`, `Turn`
(`task_workspace.py:580`). Add a project-level append-only changeset log (`.codx/changesets.jsonl`)
written on every task commit and land, and a `git` API query "which task touched this path".
That turns attribution into a feature (cheap review, per-task revert) instead of a forensic
exercise, and it is the piece of git-ai's design that stays valuable at any scale.

Post-land verify + auto-revert is the natural extension once 5.1 exists: if the target tip
fails the test command and the log says which land introduced it, revert that land and
reopen the task with the failure output. That is Anthropic's "fix forward" automated.

**Total: roughly 1.5–2 weeks inside modules that already exist, no new infrastructure.**

---

## 6. What adopting git-ai would actually cost

| Area | Cost |
|---|---|
| Runtime | New Go server + cache daemon in the container, PostgreSQL or SQLite + object store, per-agent token files. The single-container "one Docker image" story gets a second stateful service. |
| Build | Go 1.26, C toolchain, Meson, Ninja, a bundled Git. Our installer is Python/Node today. |
| Rewrite | `task_workspace.py` (worktrees → `graft clone`/sparse), `git_engine.py` (4,774 lines of porcelain → gRPC), the Git view, the merge/conflict UI, CISO hooks (reference-transaction policy has no equivalent), GitHub/GitLab remote flows. |
| Product promise | "Your repo. Your git. Your machine." becomes "your gitai server". Local worktrees, the human commit flow and the GitHub push path all change shape. |
| Risk | Pre-1.0 interfaces, single-machine test coverage, untested cloud buckets, GC off by default, license metadata mismatch. |

The parts we would gain (batching, CAS, derived data, changeset WAL, policy engine) are all
answers to contention and scale that a self-hosted, small-repo, human-reviewed product does
not have.

## 7. When to revisit

Adopt git-ai (or something like it) if **all** of these become true:

1. We host the product for teams running **hundreds of concurrent tasks on one repository**,
   not tens.
2. Merge throughput, not merge correctness, is the complaint (agents queueing on `main`).
3. Repositories are large enough that local checkouts and history walks hurt.
4. We are willing to own a stateful Go service in the install footprint.

Until then, git-ai is a **reference design**, not a dependency. Its documentation
(`RESOLVE.md`, `LAND-BATCHING.md`) is the best public write-up of the land/resolve/verify
loop and is worth reading regardless.

## 8. Open questions

- Which of the four failure modes dominates in real `.codx/tasks` history? We have months of
  task JSON — measure merges that conflicted vs. merges that broke `main` before committing
  engineering time. If mode 2 dominates, 5.1 is the whole answer.
- Is `project_scripts` a reliable test command for every project, or do we need an explicit
  `verify` setting with per-path denies?
- Do we want AI resolution on by default for task branches, or opt-in per merge? (Default
  matters more than capability here.)
