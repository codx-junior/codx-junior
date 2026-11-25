---
title: codx-junior
description: The AI developer you host — self-hosted, project-aware and review-first.
tags: [overview, install, 0.0.1]
---

# codx-junior

**The AI developer you host.** Your repo. Your model. Your machine.

codx-junior is a self-hosted AI development workspace. Install one container,
connect one OpenAI-compatible model and add one repository. A project-aware agent
reads the code, makes the change, runs the tests and shows you the diff before
you commit.

**0.0.1 is deliberately focused:** 17 features, one loop, no company OS. The
wider platform (autonomous agents, meetings, dev containers, multi-user) is in
the codebase but hidden behind the install-time feature list. See
[what ships in 0.0.1](/0.0.1).

![The project workspace: tasks, the agent and the project in one place](/images/0.0.1/workspace.png)

## Why codx-junior

AI coding tools are good at producing code. codx-junior is built around what
happens **after** the model has an idea: the context it needs, the review you
keep, and the workspace where the change becomes a commit.

* **You own the whole loop.** One Docker container, one model, one project
  folder. Code, prompts, keys and knowledge stay on your machine. No seat
  subscription, no model lock-in.
* **Project-aware, not file-aware.** Every chat is bound to a repository and a
  knowledge index built from it. Profiles carry your standards into every turn.
* **The agent shows its work.** Tool calls, edits, test runs and diffs are
  visible. The agent proposes; you review and commit.
* **One workspace.** Chat, tasks, file explorer, knowledge search and Git review
  in one browser workspace, with the repository as the unit of work.
* **Open source.** AGPL-3.0, Python/FastAPI + Vue, a real REST API and a
  local-first knowledge store. Bring the model you already pay for.

## The 0.0.1 loop

```
install → connect model → add repository → chat → agent edits and tests
        → review the diff → commit → (optionally) track the work as a task
```

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
# -> http://<server>:19981
```

1. Sign in as `admin`, set a strong password and finish setup.
2. Add a repository (Git URL or a folder on the server).
3. Open **AI Models**, add the provider and pick the default model.
4. Ask for a real change, review the diff and commit.

Full instructions: [Install](/install) · [What ships in 0.0.1](/0.0.1).

## The 0.0.1 tour

### The project workspace

![Project workspace with tasks](/images/0.0.1/workspace.png)

The task board is the work queue. Each card is a task/chat bound to the project,
with the profile, priority and status visible.

### Review before you commit

![Git review with a diff](/images/0.0.1/git-review.png)

Every change is a review: the tool activity, the unified diff and the commit
button in one screen. No black-box pull request.

## What is not in 0.0.1

The following are documented in this wiki but **hidden** in the release:
autonomous agents and tickets, teams and meetings, workspaces and browser
desktops, the company assistant/business dashboard/CISO, budget and analytics,
MCP, plugins, voice, multi-user and the in-app updater. They remain in the tree
behind the install-time feature list; 0.1 turns them on feature by feature.

## Documentation map

* [What ships in 0.0.1](/0.0.1) — the release definition.
* [Install](/install) — one command to a running instance.
* [Feature reference](/codx-junior.features) — the long catalogue, including the
  roadmap features that 0.0.1 hides.
* [Project overview](/setup-and-installation) — repository and architecture.
* [Setup and operations](/setup-and-installation) — deployment modes and
  maintenance.
* [Public product docs](https://github.com/codx-junior/codx-junior/tree/main/docs)
  — the curated site with feature guides.

::: tip
The navigation is curated for the public release. Internal engineering notes
stay in the tree but are not linked here.
:::
