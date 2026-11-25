# What is codx-junior?

> **● 0.0.1 — first public release // open source (AGPL-3.0)**

**codx-junior is the AI developer you host.** Your repo. Your model. Your
machine. A project-aware agent reads the code, makes the change, runs the tests
and shows you the diff *before* you commit.

Install one container, connect one OpenAI-compatible model and add one
repository. The product you get is deliberately focused: a real change, reviewed,
on your machine. The wider platform — the company OS, autonomous agents,
meetings and browser desktops — stays in the codebase but is hidden in this
release.

![The codx-junior project workspace](/images/0.0.1/workspace.png)

## Why it exists

AI coding tools are good at producing code. codx-junior is built around what
happens **after** the model has an idea: the context it needs, the review you
keep, and the workspace where the change becomes a commit.

| Difference | What it means for you |
| --- | --- |
| **You own the whole loop** | One Docker container on your server, one model, one project folder. Code, prompts, keys and knowledge stay yours. No seat subscription, no model lock-in. |
| **Project-aware, not file-aware** | Every chat is bound to a repository and a knowledge index built from it. Profiles carry your standards into every turn. |
| **The agent shows its work** | Tool calls, file edits, test runs and diffs are visible. The agent proposes; you review and commit. |
| **One workspace** | Chat, tasks, file explorer, knowledge search and Git review in one browser workspace, with the repo as the unit of work. |
| **Open source** | AGPL-3.0, Python/FastAPI + Vue, a real REST API and a local-first knowledge store. Read every line; bring your model. |

If you already love your editor's autocomplete, keep it. codx-junior is the
workspace around the change, not a faster Tab key.

## The 0.0.1 loop

```
install → connect model → add repository → chat → agent edits and tests
        → review the diff → commit → (optionally) track the work as a task
```

## What ships in 0.0.1

**17 features**, grouped around that loop:

* **Get started** — installer, first run, admin access, projects, provider/model settings.
* **Build** — project chat, agent tool loop, profiles, knowledge (RAG), file explorer, vibe coding.
* **Plan and ship** — tasks/Kanban, Git and code review.
* **Operate and extend** — global/project settings, REST + Socket.IO API, responsive shell, themes.

Read the full [0.0.1 release page](/0.0.1) or the
[feature matrix](https://github.com/codx-junior/codx-junior/blob/main/docs/product/v0.0.1-feature-matrix.md).

## What is not in 0.0.1

The following are documented but **hidden** in this release:

* autonomous agents, autonomous tickets and the company ticket workbench;
* teams, messenger, meetings and the company assistant;
* dev containers, browser/desktop sessions, computer use and the infrastructure panel;
* the company business dashboard, CISO, budget, analytics and the in-app updater;
* MCP connections, plugins, voice, channel content and external actors;
* multi-user roles and invitations.

They remain in the tree behind the install-time feature list, so 0.1 can enable
them feature by feature without forking the product.

## Who it is for

* **Solo developers and tech leads** who want an AI teammate that works in their
  own repository, with their own model and their own server.
* **Small teams** that need review and ownership before autonomy: the diff is the
  contract, not a black-box pull request.
* **Self-hosters** who will not send source code and prompts to a vendor they
  cannot audit.

Ready? [Install it](/getting-started), then read the
[0.0.1 release page](/0.0.1).
