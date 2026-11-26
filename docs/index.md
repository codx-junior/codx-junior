---
layout: home

hero:
  name: "codx-junior"
  text: "The AI developer you host."
  tagline: "Your repo. Your model. Your machine. A project-aware agent reads the code, makes the change, runs the tests and shows you the diff before you commit."
  image:
    src: /only_icon.png
    alt: codx-junior
  actions:
    - theme: brand
      text: "What is 0.0.1?"
      link: /0.0.1
    - theme: alt
      text: "Self-host it"
      link: /getting-started
    - theme: alt
      text: "Feature matrix"
      link: https://github.com/codx-junior/codx-junior/blob/main/docs/product/v0.0.1-feature-matrix.md

features:
  - icon: 🔑
    title: "Own the whole loop"
    details: "One Docker container on your server, one OpenAI-compatible model, one project folder. Code, prompts, keys and the knowledge index stay with you. No seat subscription, no vendor model lock-in."
  - icon: 👥
    title: "A team, not a chat"
    details: "PM, developer, QA and security profiles each own their instructions, tools and model. A task picks the role; the shared board carries the handoff; you review the diff."
  - icon: 🧭
    title: "Project-aware, not file-aware"
    details: "Every chat is bound to a repository and a knowledge index built from it. Profiles carry your standards into every turn, so you stop pasting files and re-explaining the architecture."
  - icon: 🔍
    title: "The agent shows its work"
    details: "Tool calls, file edits, test runs and diffs are visible. The agent proposes; you review the diff and make the commit. No black-box pull request to trust."
  - icon: 🗂️
    title: "One workspace, not another tab"
    details: "Chat, tasks, file explorer, knowledge search and Git review in the same browser workspace, with the repository as the unit of work."
  - icon: 🛠️
    title: "Open source and inspectable"
    details: "AGPL-3.0, Python/FastAPI + Vue, a real REST API and a local-first knowledge store. Self-host for free, read every line, bring the model you already pay for."
  - icon: 🎯
    title: "Focused by design"
    details: "0.0.1 ships 17 features, not 49. The company OS, autonomous agents and browser desktops stay in the codebase behind the feature list until they are ready."
---

![The first-run workspace wizard](/images/0.0.1/first-run.png)

![codx-junior project workspace with tasks](/images/0.0.1/workspace.png)

![Review a change before committing](/images/0.0.1/git-review.png)

![The shared team board with roles](/images/0.0.1/team-board.png)

<section class="mt-12 mx-auto max-w-4xl">
  <h2 class="text-2xl sm:text-3xl font-semibold text-center">Why this release exists</h2>
  <p class="mt-6 text-lg">
    codx-junior grew into a company operating system: goals, meetings, autonomous
    teammates, desktops, tickets, a business dashboard. All of that is real — and
    all of it made the product hard to explain.
  </p>
  <p class="mt-4 text-lg">
    <strong>0.0.1 is the sharp edge.</strong> Install one container, connect one
    model, add one repository, and ship a reviewed change. Everything else is
    still in the tree, hidden behind the install-time feature list, and will come
    back feature by feature when it is ready to be demoed.
  </p>
  <p class="mt-4 text-lg">
    The differentiator is the <strong>team</strong>: a fullstack slice gets a PM
    task, a developer pass across API and UI, a QA run and a security check, with
    a human review before the merge. One assistant loses the thread; the board
    keeps it.
  </p>


  <div class="mt-8 grid grid-cols-2 sm:grid-cols-3 gap-3">
    <div class="rounded-xl border p-4 text-center">🔑 <strong>Self-hosted</strong></div>
    <div class="rounded-xl border p-4 text-center">🧠 <strong>Project context</strong></div>
    <div class="rounded-xl border p-4 text-center">🔍 <strong>Review gate</strong></div>
    <div class="rounded-xl border p-4 text-center">🗂️ <strong>Tasks + Git</strong></div>
    <div class="rounded-xl border p-4 text-center">🧩 <strong>Profiles</strong></div>
    <div class="rounded-xl border p-4 text-center">⚖️ <strong>AGPL-3.0</strong></div>
  </div>

  <p class="mt-8 text-lg text-center">
    A real change, reviewed, on your machine. That is the 0.0.1 promise.
  </p>

  <div class="mt-8 text-center">
    <a class="inline-block rounded-lg bg-sky-600 px-4 py-2 font-semibold text-white" href="/0.0.1">Read the 0.0.1 release</a>
    <a class="inline-block rounded-lg border border-slate-400 px-4 py-2 font-semibold" href="/getting-started">Install it</a>
  </div>
</section>


| Profile | Owns | Hands back |
| --- | --- | --- |
| **project / PM** | Scope, acceptance criteria, task breakdown | A board card the team can work |
| **software_developer** | Backend and frontend implementation | A diff and the tests that prove it |
| **qa / qa_automation** | Verification plan and regression suite | A test run and remaining failures |
| **ciso** | Secrets, dependencies and the security gate | Findings and a pass/block verdict |
| **human (you)** | Direction, trade-offs and the merge | The final commit |

## The platform behind 0.0.1 (in development)

0.0.1 is the sharp edge. The wider platform is already running in development:
autonomous shifts, the CISO gate, infrastructure, the file explorer and the team
panel in one desktop.

![The advanced platform under development](/images/advanced/advanced-platform.png)

*Development instance, not part of 0.0.1. The CISO gate is blocked at 27/100 —
the gate doing its job. [See the advanced preview →](/advanced)*

<section class="pt-2">
  <div class="bg-gradient-to-br from-sky-800 via-sky-900 to-violet-400 p-6 sm:p-10 rounded-2xl w-full text-white flex items-center justify-between mx-auto my-10">
    <div class="flex flex-col gap-6">
      <div>
        <span class="text-gray-200">Early partners and the 0.0.1 release</span>
        <br>
        <span class="text-4xl text-white font-semibold">join@codx-junior.ai</span>
      </div>
    </div>
  </div>
</section>

<div class="mt-2 flex justify-between">
<div>Your repo. Your model. Your machine. ❤️</div>
</div>
