# Git and code review

codx-junior keeps humans in charge of what gets merged. Every change made by an agent is visible, explained and reviewable.

![Pull request](/images/NOV2025/codx-junior-pull-request.png)

## Changes

* See committed and uncommitted changes of the project, with diffs.
* Ask codx-junior to explain a change or to suggest improvements.
* Add comments on a diff and let the AI answer them.
* Restore a file to its last committed version.

## Task workspaces (isolated agent changes)

Autonomous work does not run in the main checkout. When
`task_workspace_isolation` is enabled (the default), every autonomy shift and
every harness/orchestration step runs in a **git worktree dedicated to its task
tree**:

* the task tree (a chat and its descendants, identified by `root_id`) gets a
  branch `codx/task/<slug>-<hash>` cut from the current branch,
* the checkout lives under `<git_worktrees_path>/tasks/`, never inside the
  project working tree,
* the turn runs with a clone of the project settings whose project root is that
  worktree, so every file and git tool writes there,
* at the end of the turn the workspace is committed on its branch,
* the main checkout is left exactly as it was.

The Git view lists task worktrees with the task name, the pending changes and
the same merge/compare/remove actions as any other worktree. Merging a task
branch is the normal review step: nothing reaches the current branch until a
human (or the configured merge flow) does it.

Enable or disable it in **Project settings → Git → Isolate autonomous agent
tasks**. The registry lives in `.codx/task_workspaces` (generated data, ignored
by git); the checkouts are outside the repository.

Notes:

* Interactive chats always use the main checkout — a human is watching and
  editing there.
* Isolation applies when the project is the root of its git repository. A
  project nested inside a larger repository falls back to the main checkout,
  because a worktree of the parent would include every sibling project.
* Dependency folders (`node_modules`, `.venv`, …) are symlinked from the main
  checkout and gitignored env files are copied, so a turn can run the tests
  without reinstalling. Nothing that git tracks is copied.
* Commits never use `--no-verify`: the [CISO](/features/ciso) pre-commit gate
  still runs on every task commit.
* A dirty workspace is never removed implicitly: the Git view shows the pending
  files and asks for confirmation.

## Quality guardians

Every changed file can be reviewed against a **guardian**: a profile whose
`file_match` selects the files it owns, with deterministic checks and an
evidence-bound AI review. Guardians are created automatically from the detected
stack, run after a file-change burst and can be reviewed/fixed from the Git
view → Quality tab. See [Quality guardians](/features/quality-guardians).

## Branches and pull requests

* Browse branches and their commits in a commit tree.
* Compare two branches or pick commits to build a **PR view**.
* Review the PR file by file, in different view modes, with an AI-generated report.
* Open a PR review as a chat in `prview` mode to discuss it with the team and the AI.

## Releases and the published image

Merging to `main` publishes the rolling `codxjuniorai/codx-junior:latest`. A
`vX.Y.Z` tag publishes the pinned release image and the GitHub release. Tag only
from a green `main`, never move an existing release tag, and verify the image
before announcing it. The full branch/tag flow, tags and provenance are in
[Docker Hub image](/docker-hub).

## GitHub issues

Paste the URL of a GitHub issue to read it inside codx-junior. The GitHub issues agent summarises the discussion, finds the related code, reasons about the problem and proposes sub-tasks to solve it.

## Plugins

Code review can be extended with plugins, for example the Azure DevOps pull requests plugin. See [Plugins](/features/plugins).
