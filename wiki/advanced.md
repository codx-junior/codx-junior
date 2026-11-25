---
title: Advanced platform (in development)
description: The wider codx-junior platform, beyond the focused 0.0.1 release.
---

# Advanced platform — under development

::: warning This is not 0.0.1
The panels in this preview are **hidden in the public 0.0.1 release**. This is a
real development instance of the wider platform, published so the roadmap is
concrete instead of a list of promises. For the release you can install today,
read [What ships in 0.0.1](/0.0.1).
:::

![The advanced codx-junior platform](/images/advanced/advanced-platform.png)

*A development instance, not a clean demo: 1,912 tasks across projects, autonomous
shifts, CISO posture, infrastructure, file explorer and the team panel in one
desktop. The CISO gate is blocked at 27/100 — that is the gate doing its job, not
a marketing score.*

## What you are looking at

* **A multi-panel desktop.** Tasks, Git, CISO, infrastructure, file explorer and
  the team panel open as windows/tabs with a saved layout. 0.0.1 keeps the
  simpler single-workspace shell.
* **An autonomous work queue.** Shifts, proposed patches, wake-ups and
  approval gates. The agents work a board across projects; 0.0.1 ships the
  human-directed version of the same board.
* **The CISO gate.** Posture, severity mix, findings, rules and a decision
  ledger. The development instance is blocked at 27/100: the security gate is
  not a badge, it stops releases until the findings are addressed.
* **The team panel.** Human members and the AI profiles (PM, developer, QA,
  security, analyst) in one roster, with autonomy settings per team.
* **Infrastructure.** Containers, images, volumes, networks, stacks, builds and
  maintenance from the browser.
* **The file explorer** with the project tree and code viewer next to the work.

## Why it is not in 0.0.1

The first release is deliberately one sharp loop: install, connect a model, add a
repository, chat, review, commit. The advanced platform adds whole new workflows
(autonomy, security gates, multi-user teams, containers). Shipping all of it at
once is how the product became hard to explain.

0.0.1 keeps the code and the data model behind the install-time feature list. The
public release turns on the parts that are ready; the rest is promoted feature by
feature.

## What comes first

1. **Workspaces** — run the app, not only edit it.
2. **MCP connections** — connect the agent to real systems.
3. **The human team** — invitations, shared channels and the messenger.
4. **The CISO gate** — security review as a first-class step in the delivery loop.
5. **Autonomous shifts** — the agent team working the board on a schedule.

Each one has a readiness note and the required fixes in the
[0.0.1 feature matrix](https://github.com/codx-junior/codx-junior/blob/main/docs/product/v0.0.1-feature-matrix.md).

## A note on this screenshot

This is a development instance, so the data is real working data, not a curated
demo. An internal request-log line has been redacted from the first task card.
No API keys, credentials or personal data are visible. The CISO score is the
development instance's posture; a clean production install starts from its own
scan.

---

**Next:** [What ships in 0.0.1](/0.0.1) · [Install](/install) ·
[Feature reference](/codx-junior.features)
