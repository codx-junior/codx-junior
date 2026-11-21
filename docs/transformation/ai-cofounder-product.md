# AI co-founder — product review, capability map and migration backlog

**Status:** DRAFT for validation · 2026-11-21 · **no code changed**
**Companion docs:**
[company-first-shell.md](company-first-shell.md) — the Phase 0 shell refactor
(company top-level, default company, advanced view, simple settings).
[ai-company.md](ai-company.md) — the deep operating model (org graph, work
protocol, escalation, authority, co-working, company functions).

This document turns the product vision into a concrete capability map and a
phased improvement backlog. It deliberately does **not** re-specify the deep
runtime designs already covered by `ai-company.md`; it defines what a
solopreneur/entrepreneur experiences, what the platform must provide, and in
what order.

---

## 0. The shift in one page

Today codx-junior is an impressive **AI workspace**: projects, chats, boards,
profiles, autonomous actors, an assistant and a company shell. The target is an
**AI co-founder**:

> A human brings the expertise, the relationships, the taste and the final
> judgement. codx-junior brings the company around them — the strategy, the
> organization, the processes, the tools, the AI teammates and the operating
> rhythm — and does the work the human cannot do, or cannot do at scale.

This is **generalist**, not “travel” or “SaaS”. Every business has the same
primitives: a mission, a model, customers, offers, channels, delivery,
money, people, tools, processes and decisions. codx-junior must:

1. **Understand** the business and write it down as a living blueprint.
2. **Plan** the 30/60/90 and the operating model.
3. **Organize** humans + AI profiles into teams with explicit independence.
4. **Do** the work end to end: research, design, build, test, install, run and
   operate the tools the business needs.
5. **Ask/approve** through a single inbox when a human is needed.
6. **Report** what happened, what it means and what comes next.
7. **Learn** from decisions and outcomes, and update the company memory.

The **built-in core is not a fixed set of business apps**. It is a tool platform:
a broad built-in toolset, Docker/workspaces/containers to install services, the
ability to develop a new tool, and MCP to expose installed or company-built
tools to the right profiles. codx-junior can install/setup what exists, build
what does not, and stay business-agnostic.

The company shell refactor in [company-first-shell.md](company-first-shell.md)
is **Phase 0** of this: it gives the company a real home, a default company and
an advanced view. It is necessary, but it is not the product on its own.

---

## 1. Product definition

### 1.1 The promise

> “Tell codx-junior what you know, what you want to build and how much
> independence you want. It will set up the company around you, build the tools,
> run the work with AI teammates, and bring you the decisions that only you can
> make.”

Two examples, one pattern:

- **Domain expert (travel/tourism):** knows the market, not technology. Needs a
  website or app, offers, content, lead capture, follow-ups, bookings/referrals,
  support and a way to see whether it is working. The human is the expert; the
  AI team is the web studio, marketing department, sales assistant and support
  desk.
- **Technical founder (SaaS):** knows how to build, not how to run a company.
  Needs a CEO/COO/CCO/CMO/CFO perspective, a GTM plan, pricing, pipeline,
  support, finance and a functioning IT team. The human is the CTO; the AI team
  fills the business gaps.

The same primitives serve both: **blueprint → org → work → tools → operations →
reports**. The function packs, profiles, tools and flows are generated from the
business, not hardcoded per industry.

### 1.2 What it is not

- Not a chat window with tools bolted on.
- Not a project manager that only tracks tasks.
- Not a code assistant that stops at “here is the code”.
- Not a static dashboard that the owner must update by hand.
- Not unlimited autonomy: the human owns the mission, the money and the
  high-risk decisions.
- Not a replacement for the owner’s relationships, taste or legal
  responsibility.

**Core vs company-specific — keep the line explicit:**

| Core codx-junior (built-in, business-agnostic) | A company’s own business (built by/for that company) |
| --- | --- |
| Company model, blueprint, org, profiles, teams, missions | A specific company’s org chart, offers, pricing, brand |
| Work items, tickets, approvals, escalations, inbox | A specific CRM schema, sales playbook or support policy |
| Tool platform: built-in tools, Docker/workspaces, MCP, custom tools | codx-junior-cloud’s signup, billing, plans, tenant provisioning, public site |
| Usage/cost ledger, budgets, audit, policy | A company’s chart of accounts, invoices, tax/legal setup |
| Generic connectors (email, chat, calendar) | A specific industry API or partner integration |

If a capability is only needed by one company, it belongs to that company’s
tools/workflows, not to core.

### 1.3 Design principles

1. **Business first, technology hidden.** The owner sees Website, App, Leads,
   Customers, Cash, Campaigns — not repositories, branches and YAML.
2. **The company is the root.** Mission, vision, values, blueprint and policies
   are company-level and injected into every AI employee.
3. **One shared reality.** Humans and AI work on the same work items, boards,
   tickets, chats, decisions and reports. No separate “AI world”.
4. **AI covers the gaps, humans keep the top.** The org is designed around the
   human’s strengths; the human defines independence per role/team.
5. **End-to-end, not advice.** If the business needs a site or app, codx-junior
   can design, build, test and run it; if it needs a campaign, it can draft,
   schedule and measure it; if it needs support, it can answer and escalate.
6. **Autonomy within policy and budget.** AI acts inside an explicit envelope;
   external sends, publishing, spending, deployment and legal commitments are
   policy-gated by default.
7. **Evidence, not claims.** Numbers come from data, links and sources; work
   closes with evidence; the owner can always ask “why did you do that?”.
8. **Generalist primitives, specialized packs.** Work items, approvals,
   escalations, profiles, teams, tools and connectors are generic; function
   packs (sales, finance, travel, SaaS…) are data. A specific company such as
   codx-junior-cloud must never shape the core model.
9. **Tool platform, not tool lock-in.** codx-junior runs on a server with Docker
   and can install/configure existing tools, develop new ones, and expose them
   to profiles through the built-in toolset, workspaces/containers or MCP
   servers that the company builds.
10. **Native basics, connectors for the rest.** Calendar, boards, tickets, docs,
    files, chat and notifications are native; Gmail, Slack, WhatsApp, payments
    and industry APIs are connectors. Cloud providers and managed hosting are
    out of scope for the core.
11. **Installed, open source, business-agnostic.** codx-junior is self-hosted;
    there is no core billing/pricing. Usage is logged per company, profile,
    user, model and tool so the owner can account for AI work.
12. **One company memory.** Decisions, outcomes, brand voice, customers,
    playbooks and learnings persist and improve the next cycle.

---

## 2. What the owner experiences

### 2.1 The first session (guided wizard)

1. **Identity:** company name, mission, vision, values, stage, location,
   currency, language.
2. **Business model:** what you sell, to whom, why you, how you charge, how you
   deliver, who/what you depend on.
3. **Goals and constraints:** 90-day outcome, 12-month ambition, **AI/tool
   budget and per-action cap**, risk appetite, time available, and the
   **company success metrics** the owner wants to track.
4. **Assets:** website, social accounts, documents, spreadsheets, existing
   tools, repos, customers, content, and anything already installed on the
   server.
5. **AI review:** codx-junior proposes a **Business Blueprint**: org, teams,
   roles/profiles, tools (install/connect/build), flows, first missions,
   policies, connectors and a 30/60/90 plan.
6. **Review and edit:** the owner changes what feels wrong, sets independence
   per team/role, adjusts the budget and metrics, approves the first plan.
7. **Connect, install and start:** connect email/calendar/chat where needed;
   install the tools that already exist; queue the first build; run a dry-run
   day; activate.

The wizard is **progressive**: a 5-minute core produces a usable draft; deeper
questions can be answered later. Nothing is written to the live company until
the owner approves the proposal.

### 2.2 A day in the life

- **Today** opens with the owner inbox: decisions waiting, approvals, a draft
  to review, an SLA risk, an opportunity, a KPI off track, and a short brief of
  what the AI team did.
- The owner answers two decisions in one click, edits one paragraph and
  approves a publish; the rest is handled.
- A **strategy/follow-up meeting** (weekly) turns goals and results into
  decisions and new work items; AI prepares the agenda, evidence and options.
- The **delivery team** designs, builds, tests and deploys a new landing page,
  automation or app feature; the owner reviews the result, not the code.
- **Marketing** drafts the launch content; **sales** prepares outreach;
  **support** answers customers; **finance** updates invoices — each within its
  independence envelope.
- The day closes with a **digest**: what changed, what it cost, what needs the
  owner tomorrow.

### 2.3 A week in the life

- Monday: plan and priorities; AI standups; work items accepted.
- Tuesday–Thursday: delivery, campaigns, customer conversations; approvals in
  the inbox.
- Friday: weekly review — pipeline, revenue, delivery, support, content,
  experiments, risks; decisions for next week.
- Monthly: strategy review — goals, budget, org/independence changes, bets.
- Quarterly: blueprint revision — mission, model, offers, org, 12-month plan.

### 2.4 Generalist adaptation

The wizard and architect do not ask “are you travel or SaaS?”. They ask about
the business model and then **compose**:

- if the business sells expertise: positioning, offers, website, content,
  lead capture, outreach, booking, referrals, support;
- if the business sells software: product, pricing, onboarding, activation,
  churn, support, billing, IT/QA/DevOps;
- if the business sells physical/local services: offers, local SEO, booking,
  scheduling, reviews, vendors, operations;
- if the business is a marketplace/creator: audience, content, partners,
  referrals, sponsorships, community.

The same primitives, different **function packs** and **tool templates**.

### 2.5 Example company, not a core feature: codx-junior-cloud

`codx-junior-cloud` is one business that can be run on codx-junior: a SaaS
platform where other people build their own businesses with codx-junior. It is
useful as an example and as dogfooding, but its needs are **the company’s
needs**, not core codx-junior requirements:

- signup, billing, plans, tenant provisioning, status page, customer support and
  the public marketing site are **tools and workflows that company builds or
  installs** using the same tool platform as any other business;
- the generic company model and the core product must not acquire SaaS-specific
  fields or assumptions because of it;
- cloud providers, managed hosting and billing are **out of scope** for core
  codx-junior. A company that needs them creates/installs the required tools (or
  connects to an external service through MCP).

What **is** core is the capability underneath:

> codx-junior can install/setup an existing tool, configure it, develop a new
> tool when needed, and expose its capabilities to the right profiles — through
> the built-in toolset, Docker/workspaces/containers, or an MCP server the
> company builds itself.

That capability is generic; codx-junior-cloud is one of its customers.

---

## 3. What exists today (keep)

The substrate is stronger than it looks. The improvement strategy must reuse it,
not rebuild it.

| Capability | Where it lives | Maturity | Role in the co-founder |
| --- | --- | --- | --- |
| Company entity, org units, teams, members, missions, business cockpit, journal | `api/codx/junior/company/` | Good foundation | The company memory and org |
| Default company + project linking | `company/manager.py` (`ensure_default_company`, `child_projects`, `snapshot`) | Working | The company context |
| Profiles (instructions, tools, model, knowledge, guardrails, voice) | `profiles/` | Strong | The AI employee’s brain |
| Project/company teams | `teams/`, `company/models.py` | Good but partly documentation-only | The operating unit (needs runtime) |
| Mission dispatch to orchestration | `CompanyManager.dispatch_mission` | Working | The delivery primitive |
| Autonomous actors with shifts, schedules, budgets, boards, reports | `autonomy/` | Working, single-agent | The AI worker/manager primitive |
| Company autopilot | `company/autopilot.py` | Working, shallow | The proactive clock (needs teeth) |
| Boards, chats, tasks, channels, DMs, mentions | `task_board.py`, `chat/`, `teams/` | Strong | The shared work surface |
| Company assistant (teams, people, projects, goals, approvals) | `company/assistant.py` | Good, admin-oriented | The conversational co-founder (needs business actions) |
| Business cockpit (revenue target, pipeline, metrics) | `Company.business` + `CompanyBusiness.vue` | Manual | The money view (needs live data) |
| External actors and outreach (email + manual relay MVP) | `docs/features/external-actors.md`, `integrations/` | MVP | Sales/support conversations |
| Built-in toolset for profiles | `api/codx/junior/tools/`, `custom_tools/`, `plugins/`, `docs/features/plugins.md` | Broad | The AI employees’ hands |
| Docker workspaces/containers | `workspaces/`, `infra/`, `docs/features/workspaces.md` | Strong | Install/run business tools on the server |
| MCP connections (company-built tools too) | `api/codx/junior/api/mcp.py`, `docs/features/mcp-connections.md` | Working | Expose installed/custom tools to profiles |
| Browser/computer use | `browser/`, `computer/`, `docs/features/computer-use.md` | Strong | Operating web/GUI tools without APIs |
| AI usage analytics (tokens/tools per user/project/model/tool; chat profiles) | `analytics/`, `model/wallet.py` | Working | Cost/usage reporting; no new ledger needed |
| Calendar, meetings, calls, summaries | `calendar/`, `meets/`, `docs/features/company-meetings.md` | Working | The company rhythm |
| Budget ledger, analytics, spend | `model/wallet.py`, `analytics/` | Working | Cost control and reporting |
| CISO, secrets, audit, harness | `security/ciso/`, `harness/` | Strong | Trust and governance |
| Company-first shell (proposed) | `docs/transformation/company-first-shell.md` | Proposal | Phase 0 foundation |

**What to keep:** the company document, profiles, teams, missions, actors,
orchestration, boards/chats, external actors, MCP, browser/computer, calendar,
budgets and audit. The co-founder is a **product layer and a set of business
objects** on top, not a rewrite.

---

## 4. What is missing (gap analysis)

| Capability | Today | Gap | Why it matters |
| --- | --- | --- | --- |
| Business blueprint | Mission/vision/values + a manual business cockpit | No structured model, no wizard, no 30/60/90, no assumptions/risks | The AI cannot run a business it does not understand |
| Company architect | SaaS/blank templates, IT-heavy advisor | No generalist proposal from a business brief; no dry-run | Every business would need manual org design |
| Function packs / roles | IT, engineering, product, data, security, support, design | No sales, marketing, success, finance, legal, people, ops, strategy | Most businesses are not software teams |
| Independence / authority | Team `autonomy` string, actor budgets | No action classes, policy, approval matrix, per-role authority | “AI covers gaps” requires explicit envelopes |
| Work protocol | Missions + boards + chats | No WorkItem/request/handoff/review/incident, SLA, escalation, decision log | Cross-team work has no owner or clock |
| Inbox | Assistant + notifications | No owner/team inbox for approvals, escalations, reviews, drafts, SLA risks | The human cannot supervise AI work in one place |
| Team managers | Lead is a roster reference | No manager runtime, agenda, SLA, authority, deputy | Teams do not actually manage themselves |
| Company rhythm | Actors have individual schedules | No standups, weekly reviews, monthly strategy, digest | No operating cadence |
| Tool platform | Broad built-in tools, Docker workspaces, MCP, plugins, browser/computer | No product-level tool catalog/installer, no per-company tool registry/health, no guided “install/setup/develop/expose to profiles” flow | The platform has the pieces but the owner cannot ask for a tool and get it installed |
| Tool factory | Code agents + projects + orchestrator | No end-to-end “tool” pipeline, environments, QA gates, release, monitoring, or a simple non-technical surface | “Build any tool” is not a product yet |
| Business operations | Manual cockpit, external actors MVP | No CRM, offers, content calendar, internal tickets, invoices, referrals, KPIs; support/helpdesk later | Sales/marketing/operations are not operational |
| Native basics | Calendar, boards, chats, docs/wiki, files | No native internal ticket queue, no unified inbox/notifications | The owner still needs external tools for basics |
| Connectors | MCP, SMTP/IMAP, Slack/WhatsApp designs | Gmail, Google Calendar, Slack, WhatsApp, payments and analytics not packaged | Business runs on email, chat and money |
| Business memory | Company document + assistant context | Decisions/outcomes/playbooks/brand/customers not structured | AI repeats mistakes and questions |
| Human-AI co-working | Chat + takeover primitives in computer/browser | No join/observe/takeover/handback on work items, presence, pair mode | Humans cannot work *with* the AI team |
| Non-technical UX | Advanced project workspace | Repos, branches, YAML, code concepts leak into the business user experience | The owner is not an engineer |
| Multi-company isolation | Open company reads (see shell doc §1.8) | Membership-scoped reads, per-company data boundaries | Business-first requires privacy |
| Scale | One company JSON + journal | Work items, contacts, tickets, content, invoices will outgrow it | The company OS needs stores, not one document |

---

## 5. The co-founder operating model

### 5.1 The loop

```text
        ┌──────────────────────────────────────────────────────────────┐
        │  1. UNDERSTAND   blueprint, mission, model, customers, goals │
        └───────────────┬──────────────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  2. PLAN         30/60/90, OKRs, org, budget, policies       │
        └───────────────┬──────────────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  3. ORGANIZE     function packs, profiles, teams, independence│
        └───────────────┬──────────────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  4. DO           work items, tools, campaigns, delivery       │
        └───────────────┬──────────────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  5. ASK/APPROVE  inbox, approvals, escalations, decisions     │
        └───────────────┬──────────────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  6. REPORT       daily brief, weekly review, monthly board    │
        └───────────────┬──────────────────────────────────────────────┘
                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  7. LEARN        decisions, outcomes, playbooks, next bets    │
        └───────────────┬──────────────────────────────────────────────┘
                        └──────────────► back to 1
```

Every surface and every AI employee serves one step of this loop. A feature that
does not serve a step is either an advanced tool or out of scope.

### 5.2 Business Blueprint (the company’s brain)

A structured, versioned document (company-scoped, editable, injected into every
profile and assistant turn):

- **Identity:** name, mission, vision, values, stage, legal entity, location,
  currency, language, brand voice.
- **Market:** ICP/segments, pains/gains, alternatives, competitors,
  positioning, proof.
- **Offer:** products/services, packages, pricing, unit economics, guarantees.
- **Channels:** website, social, email, partners, referrals, marketplaces, ads.
- **Delivery:** how the promise is fulfilled, processes/SOPs, capacity, vendors,
  quality gates.
- **Money:** revenue streams, costs, budget, targets, cash constraints.
- **Team:** human roles, AI roles, org, independence, schedules.
- **Goals:** 90-day, 12-month, OKRs, **owner-defined success metrics**, leading
  indicators, budget/cap.
- **Risks/assumptions:** market, technical, legal, financial, unknowns to test.
- **Assets:** domain, brand, content, accounts, datasets, repos, installed
  tools, contracts.

The blueprint is not a one-time form. It is revised in the monthly/quarterly
strategy cycle and every material decision links back to it.

### 5.3 Organization and independence

- **Function packs** are data: org unit, teams, role templates, interfaces, KPIs,
  SLAs, default policies, required connectors, sample missions.
- **Role templates** carry instructions, tools, knowledge, expected outputs,
  KPIs and an authority envelope. A profile is generated from a role template
  and can be edited like any profile.
- **Teams** are hybrid (humans + AI), with a lead/manager, purpose, projects,
  tools, budget, schedule and independence.
- **Solo-first, multi-human ready:** the first release optimizes the single
  owner’s experience, but the model and permissions support several humans with
  roles, teams and approval rights from the start.
- **Independence** is the user-facing name for autonomy. The owner sets it per
  profile/team/action in the Team view:
  - L0 human only;
  - L1 AI recommends/drafts;
  - L2 AI acts with review;
  - L3 AI acts within policy/budget;
  - L4 AI proactively acts within caps and reports.
- The default is conservative: internal work L2, external send/publish L1,
  money/deploy/legal L0–L1 unless the owner raises it.
- The **human is at the top of every chain**; AI managers can escalate and
  decide within their envelope but never approve their own high-risk action.

### 5.4 Work protocol (the operating system)

Everything the company does is a **work item** (visible as a ticket/card/mission):

| Kind | What it is | Owner | Closes when |
| --- | --- | --- | --- |
| Mission | A business outcome | A team | Accepted + evidence |
| Request | Cross-team ask | Receiving team | Delivered/accepted |
| Handoff | Ownership transfer | Receiver | Accepted/rejected |
| Review | Quality/brand/legal gate | Reviewer | Verdict recorded |
| Incident | Something broken/urgent | On-call team | Mitigated + postmortem |
| Task | A unit inside a team | Member | Accepted + evidence |

Every work item has: owner, status, priority, SLA, budget, evidence, links to
mission/card/chat/run, decisions, approvals and escalations. The **company
inbox** is the human projection; the **board** is the team projection; the
**chat** is the discussion; the **journal** is the audit.

### 5.5 Tool platform and factory (install / configure / develop / expose)

codx-junior is installed on a server with Docker. A **Tool** is the business
capability; a **Project** is its technical substrate when it has to be developed.
The platform’s job is to make the right capability available to the right
profiles, regardless of whether it was installed, connected or built.

**Installation model:** codx-junior runs via Docker Compose on the company’s
server (API/UI, workers, workspaces and installed services). Companies add tools
as containers/workspaces with resource limits, health checks, logs, secrets,
backups and upgrades. The server is the runtime; cloud-provider orchestration is
not part of core codx-junior.

**Four ways a tool reaches a profile:**

1. **Built-in tool** — one of codx-junior’s own tools (boards, files, browser,
   computer, git, orchestration, meetings, knowledge, images, Docker/infra, …).
2. **Installed tool** — a service/container/workspace installed on the server
   (database, CMS, CRM, helpdesk, automation, internal admin app…), configured
   and health-checked by codx-junior.
3. **External tool via MCP** — an existing system exposed as a streamable HTTP
   MCP server; tools are namespaced and granted per profile.
4. **Company-built tool** — the company (with AI) develops its own service and
   exposes it as an MCP server; the core does not need to know what it is.

The built-in toolset is already broad: the default is to **use or extend it**
before building a new tool. A new tool is justified when the business needs a
capability the platform does not have, not because a profile lacks a way to act.
Publishing/registering a company-built MCP tool is **admin-only** (existing user
levels: the first user is admin); there is no extra approval workflow or
separate tool registry beyond the existing MCP server registration.

**Install/setup flow:** request → choose install/connect/build → credentials and
secrets → resource limits → health check → tool discovery → grant to profiles →
audit/logs. A company can install a tool without a code project; if the tool must
be developed, it follows the factory pipeline below.

**Factory pipeline:** request → scope/stack/cost proposal → approve → design →
build → test → security review → run/deploy → monitor → iterate. “Deploy” means
running the tool on the codx-junior server/workspace or the company’s chosen
target; **cloud-provider orchestration is out of scope** for core codx-junior.
A company that needs an external cloud, domain or SaaS service uses a tool or a
connector for that (for example the codx-junior-cloud company would build its
own deployment flow).

**Tool types:** web app/SaaS MVP, marketing website, internal dashboard,
automation/integration, booking/referral site, e-commerce, mobile app (later),
content asset, campaign, installed business service (CRM/helpdesk/billing…).

**Human + AI mix:** the owner can take any stage; AI can take any stage; the
work supports pair/review/takeover; no stage is human-only or AI-only by default.

**Non-technical surface:** the owner sees the tool, its status/URL, its health,
its usage/cost and the next iteration — never the repo/branch/YAML unless they
open Advanced view.

**Evidence:** health check, preview URL/screenshot, tests, accessibility/
performance/security reports, run/deploy log, usage stats.

### 5.6 Business operations (surfaces + connectors)

Native-lite objects the AI can reason over daily, with connectors for mature
external systems:

- **Customers:** contacts/accounts/leads, activities, source/UTM, next step.
- **Revenue:** offers/pricing, proposals, pipeline, invoices, subscriptions,
  payments, expenses, cash.
- **Marketing:** content calendar, drafts, brand voice, publishing, campaigns,
  analytics, SEO.
- **Sales:** outreach sequences, replies, follow-ups, referrals/partners,
  commissions.
- **Support:** customer support/helpdesk (tickets, knowledge answers, SLAs,
  escalation, CSAT) — **after** internal work tickets, which are the first
  ticket use case.
- **Legal/Admin:** policy pages, contracts/templates, GDPR requests,
  disclaimers, vendors, SOPs.
- **Analytics:** funnel, channel, revenue, support, content, experiments.
- **Native basics:** calendar, boards, **internal work tickets**, docs/wiki,
  files, chat, notifications.
- **Connectors (first wave):** Gmail/Google Calendar, Slack, WhatsApp, payments,
  analytics/SEO, MCP business tools. Installed services (CRM/helpdesk/CMS…)
  reach profiles through MCP; cloud-provider orchestration is out of scope.

The owner does not need to choose a CRM on day one. The native objects are the
default; a connector can become the system of record later without losing the
workflow.

### 5.7 Memory and learning

- Company memory: blueprint, decisions, outcomes, customer insights, brand
  voice, playbooks, failed bets, pricing history.
- Every AI employee receives the relevant slice (not the whole company).
- The weekly/monthly review updates the memory: what worked, what did not, what
  changes next.
- The assistant never asks the same setup question twice; it asks only for
  missing, material details.

### 5.8 Surfaces

| Surface | Answers the owner’s question |
| --- | --- |
| **Today** | What needs me now? What did the team do? Are we on track? |
| **Inbox** | Approvals, escalations, reviews, drafts, SLA risks, mentions |
| **Plan** | Mission/vision/values, blueprint, 90-day plan, OKRs, risks |
| **Business** | Revenue, costs, offers, pipeline, cash, KPIs |
| **Customers** | Leads, customers, conversations, outreach, referrals |
| **Marketing** | Website, content, campaigns, channels, performance |
| **Delivery** | Tools/projects, work items, boards, releases, quality |
| **Team** | Org, humans, AI employees, independence, budgets, capacity |
| **Reports** | Daily brief, weekly review, monthly board pack |
| **Settings** | Company, AI, people, tools, connectors, policies, advanced |
| **Ask** | The always-available co-founder conversation with actions |

The shell refactor already defines the company home and advanced view; the
surfaces above are the product backlog inside that shell.

### 5.9 Autonomy model

Action classes are the unit of policy:

```text
read → draft → internal_write → external_send → publish → spend → deploy
     → delete → hire/fire → legal/contract → policy change
```

Each class has a default independence level, a per-team/profile override, a
budget/amount limit and an approver. The policy engine returns
`allow | deny | require_approval` with a reason; high-risk classes fail closed.
The human owner is the top approver; a company admin can change policy.

**Chosen default (broad autonomy within policy/budget):**

- read, research and draft: allowed;
- internal write (boards, tickets, docs, tasks): allowed within the company;
- external send and publish: allowed within the configured budget/brand policy,
  with rate limits, suppression lists and a visible audit trail;
- spend: allowed up to the per-team/profile budget, with hard stops and alerts;
- production deploy: allowed for the tool’s owner within the release policy;
  high-risk/irreversible deploys require approval;
- legal/contracts, hiring/firing, policy changes, deleting company data:
  human sign-off;
- every action is attributable, reversible where possible and visible in the
  owner inbox (“AI did X; undo/stop”).

Because this default is permissive, the **guardrails are not optional**: budgets,
rate limits, brand/content checks, external-recipient controls, kill switch,
audit and a dry-run mode. A new company starts with the broad envelope but a
conservative spend cap; the owner can lower independence per team/profile in the
Team view.

### 5.10 Company rhythm

- **Daily:** AI standups, owner brief, inbox.
- **Weekly:** business review (pipeline, delivery, support, content, money,
  risks), decisions and next week’s plan.
- **Monthly:** strategy review (goals, budget, org, independence, bets).
- **Quarterly:** blueprint revision and 12-month plan.
- **Event-driven:** incidents, opportunities, external replies, SLA breaches,
  approvals.

Meetings use the existing Meet/Calendar surfaces; AI prepares agendas, evidence,
options, minutes and creates work items from decisions. AI managers participate
in the strategy/follow-up meetings with data and proposals; the human owner (or
the accountable human) decides, and the decision becomes a work item with an
owner and a deadline.

### 5.11 Usage and cost

codx-junior is open source and does not bill users. Usage data already exists:
token/tool events carry user, project, model, tool, tokens, cost, duration and
success; chat sessions carry profiles. There is **no new usage-ledger
workstream**; the remaining product work is presentation (company settings and
reports) and any filter/export the owner needs. The setup wizard asks for the
initial monthly budget and per-action cap; budgets/caps are enforced by the
autonomy policy.

---

## 6. Detailed improvement backlog

Phases: **P0** shell (already proposed) · **P1** co-founder foundation ·
**P2** tool platform · **P3** business operations · **P4** autonomous company ·
**P5** ecosystem/scale.

### A. Business blueprint and setup

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| A1 | Guided company setup wizard (identity, business model, goals, budget/cap, success metrics, tools) | Owner describes business in 5–20 min (progressive) | Company model fields, assistant | P1 |
| A2 | Business model canvas (offer, ICP, channels, revenue, costs, partners, activities, resources) | Structured model the AI can act on | `Company.business`, new blueprint model | P1 |
| A3 | Strategy plan 30/60/90 + OKRs + risks/assumptions | A plan, not a generic goal list | Missions, business metrics | P1 |
| A4 | Asset inventory/import (site, socials, docs, sheets, tools, repos, customers) | AI knows what exists before proposing | Knowledge, browser, CSV | P1/P3 |
| A5 | Company architect proposal (org, teams, profiles, tools, flows, policies, first missions) | One-click org design from the brief | `profile_advisor`, `teams/generator`, templates | P1 |
| A6 | Review/apply + dry-run simulation | Owner sees the company before it runs | Orchestration dry-run, assistant | P1 |
| A7 | Persisted/versioned blueprint injected into every profile | No repeated context; consistent AI employees | Company document + new store | P1 |
| A8 | Business-type templates (professional services, travel, SaaS, e-commerce, local, creator, agency) | Faster start without hardcoding | `templates.py` | P1/P3 |
| A9 | Setup checklist/progress/resume | Owner never loses the thread | Wizard UI | P1 |
| A10 | Company success metrics + baseline (owner-defined: revenue, costs, customers, traffic, pipeline, support…) | The owner chooses what “on track” means | `BusinessPlan`, analytics | P1 |

### B. Adaptive organization and people

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| B1 | Generalist function/role catalog (management, strategy, sales, marketing, success, finance, legal, people, ops, data, IT) | Any business can be staffed | `profile_advisor.ROLES` extension | P1 |
| B2 | Role template schema (instructions, tools, knowledge, outputs, KPIs, authority) | Profiles are generated consistently | Profiles, generator | P1 |
| B3 | Team builder from a business need (human + AI mix, preview/edit/apply) | The org matches the business, not IT | `teams/generator`, Team view | P1 |
| B4 | Independence controls in Team view (level, tools, budget, approvals, schedule) | Owner defines how much AI does | `CompanyTeam.autonomy`, actors | P1 |
| B5 | AI employee lifecycle (hire, onboard, probation, review, promote, retire) | AI employees are managed, not one-shot | `ai-company.md` §3.14 | P4 |
| B6 | Reporting lines/managers/deputies/sponsors/team managers | The org chart routes work | `ai-company.md` §3.2–3.4 | P4 |
| B7 | Human roles, positions, permissions and approval rights | Humans and AI share authority explicitly | User/company roles | P2 |
| B8 | Capacity, schedules, working hours and load | Realistic planning | Actors schedule, calendar | P2 |
| B9 | Performance scorecards/coaching | AI improves and is accountable | KPIs, reviews | P4 |
| B10 | Executable org chart (reporting + functional lines) | One picture of who decides/consults | Company UI | P4 |

### C. Work protocol, inbox and collaboration

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| C1 | WorkItem object (mission/request/handoff/review/incident/task) with lifecycle, owner, SLA, budget, evidence | Every cross-team thing has an owner and a clock | `ai-company.md` §3.7 | P1 |
| C2 | Native Tickets app for **internal work requests** (ops/IT/admin/approvals) | The team has a real internal queue | Boards/chats/work items | P1 |
| C3 | Company inbox (owner/team) for approvals, escalations, reviews, drafts, SLA risks | One place to supervise the company | Assistant, notifications | P1 |
| C4 | Approval matrix/policy engine (action/amount/risk/team; one-click; mobile) | Autonomy is safe and fast | `ai-company.md` §3.5 | P2 |
| C5 | Escalations with packets, options, timeouts and decision log | Blocked work gets decided | `ai-company.md` §3.8 | P2/P4 |
| C6 | Human join/observe/takeover/handback of AI work; presence; live turn | Humans can work *with* AI, not around it | Computer/browser, chats, orchestration | P2 |
| C7 | Team/company rooms (feed + board + inbox + members + metrics) | The team has one home | Chats, boards, company | P2 |
| C8 | Decisions log (options, rationale, owner, outcome) | The company learns and audits | Journal | P1/P2 |
| C9 | Notifications/digest preferences; mobile approvals | The owner can run the company from a phone | Mobile shell | P2 |
| C10 | Work item projections to mission/card/chat/run + evidence links | One object, many views | Orchestration, boards | P2 |
| C11 | Delegation/handoff human↔AI and AI↔AI; pair mode | Work moves without copy/paste | Chats, actors | P2 |
| C12 | Company clock + SLA automation | Nothing silently rots | `ai-company.md` §3.7.4 | P4 |

### D. Tool platform, installation and delivery

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| D1 | Tool object (type, install mode, owner, status, URLs, cost) | The owner sees a capability, not a repo | Projects + company | P1/P2 |
| D2 | Tool templates (web app/SaaS MVP, marketing site, internal dashboard, automation, booking/referral, e-commerce) | Faster, safer delivery | Project templates, design assets | P2 |
| D3 | End-to-end pipeline (discover, design, build, test, security, run/deploy, monitor, iterate) | “Build any tool” becomes real | Orchestration, missions, boards | P2 |
| D4 | Brand/design kit generation (logo, palette, type, UI kit) with approval | Tools look coherent | Images, design system | P2 |
| D5 | Tool install/configure pipeline: Docker/workspace/container, secrets, resource limits, health check, versions, remove | Install a business tool without a code project | Infra, workspaces, secrets | P2 |
| D6 | QA gates (functional, accessibility, performance, security) + evidence | Quality is visible | CISO, tests, browser | P2 |
| D7 | Release management (versions, changelog, monitoring, alerts) | Safe iteration | Git, infra | P2 |
| D8 | Mobile app pipeline (Expo/React Native) + store credentials | Mobile when the strategy needs it | Projects, build agents | P5 |
| D9 | Human + AI mixed delivery (pair, review, delegate, takeover) | The owner can participate at any depth | Chats, orchestration | P2 |
| D10 | Non-technical Tool/Workspace abstraction | No code jargon in the business surface | Shell, company UI | P1/P2 |
| D11 | Build/run cost guardrails | No surprise bills | Budgets, usage logs | P2 |
| D12 | Delivered-tool analytics/monitoring/alerts | The owner knows if it works | Analytics, connectors | P3 |
| D13 | Tool catalog + install wizard (“what do you need?” → install/connect/build) | The owner can ask for a tool and get it | Docker, workspaces, MCP | P1/P2 |
| D14 | Company-built tool exposure as an MCP server + per-profile grants; **admin-only publish/register** | Companies extend the platform without forking it | MCP registry, profiles, user levels | P2 |
| D15 | Server install/upgrade/backup/health (Docker Compose, volumes, resource limits) | The platform is operable by a non-sysadmin | `install.sh`, infra | P1/P2 |
| D16 | Built-in tool catalog/audit by business function; explicit gaps list | Use/extend the broad toolset before building | `tools/`, docs | P1 |

### E. Business operations

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| E1 | CRM-lite (contacts/accounts/leads, stages, activities, source/UTM, next step) | Leads are not lost in chat | Native objects, new UI | P3 |
| E2 | Offers/pricing/products and proposals/quotes | The business can sell | Business plan | P3 |
| E3 | Marketing content calendar, drafts, brand voice, approval, publish | Consistent content engine | Content objects, CMS/social connectors | P3 |
| E4 | Outreach/sequences/replies/follow-ups | Sales runs continuously | External actors, email/Slack/WhatsApp | P3 |
| E5 | Customer support/helpdesk (tickets, knowledge, SLA, CSAT) | Customers get answers | Tickets, knowledge, channels | P3/P5 (after internal tickets) |
| E6 | Finance-lite for the company’s own customers (invoices, subscriptions, payments, expenses, cash) — **not codx-junior billing** | Money is visible and chased | Stripe/accounting connectors | P3 |
| E7 | Referral/affiliate tracking (partners, links/UTM, commissions, payouts) | Referral businesses work | CRM, analytics, finance | P3/P5 |
| E8 | Legal/compliance (policy pages, contracts/templates, GDPR, disclaimers) | Less legal risk | Docs, knowledge, approvals | P3 |
| E9 | Operations/SOPs/vendors/recurring tasks | The business runs repeatably | Work items, calendar | P3 |
| E10 | Analytics/KPI dashboards (funnel, channel, revenue, support, content) | Decisions are grounded | Analytics, native objects | P3 |
| E11 | Business reports (daily/weekly/monthly/board pack) | The owner sees the truth | Assistant, metrics | P2/P3 |
| E12 | Business memory/playbooks/insights | The company gets smarter | Blueprint, decisions, outcomes | P3 |

### F. Connectors and native basics

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| F1 | Connector registry (OAuth/API key, scopes, health, test, audit, per company) | Safe, repeatable integrations | MCP, secrets | P1/P2 |
| F2 | Gmail/Google Calendar + SMTP/IMAP | Email and calendar are first-class | External actors, OAuth | P1 |
| F3 | Slack (channels, DMs, actions) | The team can live in Slack | `slack-whatsapp-integration.md` | P2/P3 |
| F4 | WhatsApp Cloud API (messages, templates, opt-in) | Customer conversations where they are | External actors design | P2/P3 |
| F5 | Native calendar/boards/**internal tickets**/docs/files | Basics without external tools | Existing + new tickets | P1/P3 |
| F6 | Browser/computer use exposed as business tools | Operate tools without APIs | Existing | P2 |
| F7 | MCP business tools/marketplace (CRM, helpdesk, billing, payments) | External systems of record | MCP | P3 |
| F8 | Docker/workspace tool installation (image catalog, compose templates, health) | Install tools on the codx-junior server without cloud providers | Infra, workspaces | P2 |
| F9 | Analytics/SEO/social publishing connectors | Measure and distribute | Connectors | P3 |
| F10 | Import/export/webhooks (CSV, site crawl, docs) | Onboarding and portability | Files, browser | P3 |

### G. Autonomy, governance and trust

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| G1 | Action classes + default policy templates (broad within budget; legal/high-risk sign-off) | Safe-enough autonomy for a real business | `ai-company.md` §9 | P1/P2 |
| G2 | Independence levels L0–L4 per profile/team/action, earned over time | The owner decides how much AI does | Team view, policy | P2 |
| G3 | Team-view independence controls (the user’s requested surface) | No hidden autonomy | Team view | P1/P2 |
| G4 | Budgets per company/team/profile/period, hard stops, forecast | Cost is controlled | Wallet, actors | P2 |
| G5 | Audit/decision/evidence trail + “why did AI do this?” | Trust and accountability | Journal, harness | P2 |
| G6 | Kill switch/pause/revoke/break-glass | The owner can always stop | Company/actor controls | P2 |
| G7 | Prompt-injection/data isolation/secret handling | External content cannot hijack the company | Security | P2 |
| G8 | Human top for high-risk; regulated actions sign-off | Legal/ethical safety | Policy | P2 |
| G9 | Claim/content verification (sources, numbers, brand) | No invented facts | Knowledge, analytics, browser | P2/P3 |
| G10 | Cost/quality guardrails, model routing, fallback | Reliable and affordable | AI manager | P2 |

### H. Experience and rhythm

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| H1 | Today/owner home (attention, KPIs, AI activity, next actions) | The owner knows what to do | Company home | P1 |
| H2 | Ask co-founder (persistent chat with actions/context) | One place to talk to the company | Assistant | P1 |
| H3 | Strategy/follow-up meetings (agenda, evidence, options, decisions, tasks) | The company has a cadence | Meet/Calendar, work items | P2 |
| H4 | Daily/weekly/monthly digests | The owner stays informed without dashboards | Assistant, reports | P2 |
| H5 | Explainability (sources, confidence, options) | Trust | Assistant, audit | P2 |
| H6 | Business-first language and progressive disclosure | Non-technical owners can use it | Shell | P1 |
| H7 | Mobile owner experience (approve/answer/chat) | Run the company from a phone | Mobile shell | P2 |
| H8 | Onboarding/education/templates/time-to-value | First value in minutes | Wizard | P1 |

### I. Platform and scale

| ID | Improvement | Outcome | Reuse | Phase |
| --- | --- | --- | --- | --- |
| I1 | Company-scoped stores for high-volume objects (work items, contacts, tickets, content, invoices) | The company OS scales beyond one JSON | New stores | P2/P3 |
| I2 | Multi-company isolation/permissions | Business data is private | Company reads/roles | P1 |
| I3 | Connector/plugin marketplace | Businesses can add tools easily | MCP/plugins | P5 |
| I4 | Templates/function-pack marketplace | Faster onboarding by business type | Templates | P5 |
| I5 | Performance/scale benchmarks | 100 teams/10k work items | `ai-company.md` §10 | P5 |
| I6 | Backup/export/portability | The owner owns their data | Export | P3 |
| I7 | Observability/testing for autonomous actions | Safe operations | Harness, tests | P2/P4 |
| I8 | Partner API/webhooks | Integrations and ecosystem | API | P5 |

---

## 7. Roadmap and exit criteria

Estimates assume a small team (≈3 engineers + design), reuse of the existing
runtimes, and can overlap; see `ai-company.md` §10.7 for the deep work estimate.
The phases below are product increments, not a promise that every workstream is
finished before the next one starts.

### Phase 0 — Company-first shell (already proposed)

- Company top-level, default `/acme`, advanced `/<slug>/workspace/<project>`,
  simplified settings, Solopreneur removed, security isolation.
- **Exit:** `/` lands in the company; advanced only lists company projects;
  settings can configure the company in minutes.

### Phase 1 — Co-founder foundation (≈6–8 weeks)

**Goal:** a new owner can describe the business and get a company that plans and
starts working internally.

Scope: A1–A7, A9–A10; B1–B4; C1–C3, C8; D1, D10, D13, D16; F1–F2, F5, F8;
G1, G3, G5; H1, H2, H6, H8; I2.

**Demo:** “I run a travel advisory / a SaaS / an agency” → wizard → blueprint →
proposed org, profiles, teams, tools and 30/60/90 → edit independence → approve
→ Today/inbox with the first missions and a daily brief. Ask for a tool; install
it from the catalog or build a small one; grant it to a profile; see the existing
usage data in the company settings.

**Exit:** blueprint generated and editable; at least 5 function packs and 20 role
templates; AI team can execute an internal mission end to end; owner can approve
and see evidence; one installed tool is exposed to a profile; existing usage
data is visible in the company settings; external/money actions are policy- and
budget-gated from day one (the broad envelope is on, with a conservative
starting cap).

### Phase 2 — Tool platform and first business tool (≈8–10 weeks)

**Goal:** a company can ask for a tool and get it installed, configured or
built, then exposed to the right profiles — end to end.

Scope: D1–D7, D9–D15; C4–C7, C10–C11; G2, G4, G6–G10; F3–F4, F7–F9;
H3–H5, H7; I1, I7.

**Demo:** the owner says “we need a CRM/helpdesk/booking service/automation”.
codx-junior proposes install vs build, installs the service in Docker/workspace
or develops it as a project, checks health, exposes its tools via MCP, grants
them to the sales/support profiles, and records the calls in the existing
analytics. A company that needs more (for example codx-junior-cloud) builds its
own tools and exposes them the same way; nothing about that case is special-cased
in core.

**Exit:** one real business tool installed or built end to end and used by AI
profiles; tool health/rollback and the existing usage data are visible; the broad
autonomy envelope is enforced by budgets/rate limits/audit; no cloud-provider
orchestration is required.

### Phase 3 — Business operations (≈8–10 weeks)

**Goal:** the business runs its commercial loop in codx-junior.

Scope: E1–E12; F10; G9; I6; D12.

**Demo:** leads from the site become CRM records; outreach and follow-ups;
internal tickets (support/helpdesk follows); invoices/payments when the business
needs them; content calendar; weekly business review with funnel/revenue/cost
metrics.

**Exit:** a lead can go from first contact to customer inside the platform;
internal ticket SLAs and content cadence run for a week; owner receives a weekly
review grounded in real data and the existing usage/cost data.

### Phase 4 — Autonomous company (≈8–12 weeks)

**Goal:** the company runs a rhythm with AI managers and humans in the inbox.

Scope: B5–B10; C9, C12; G2 (earned autonomy), G6; full policy/approval matrix;
team managers, company clock, daily/weekly/monthly rhythm; proactive autopilot
(opportunities, risks, follow-ups); hiring/performance/retirement.

**Demo:** a week of company operation: daily standups, escalations answered,
weekly review, AI manager hires/retires a teammate, owner only handles the
inbox.

**Exit:** ≥80% of owner attention items resolved the same day; zero unauthorized
external/money actions; autonomy level changes audited; company runs five
consecutive days without the owner opening a project.

### Phase 5 — Ecosystem and scale (ongoing)

- Mobile app delivery pipeline, connector/function-pack marketplace, templates
  by business type, multi-company scale, benchmarks, partner API, portability.
- **Exit:** a new business can start from a template + connectors in under an
  hour; a partner can publish a connector/function pack; 100 teams/10k work
  items benchmark passes.

### Mapping to `ai-company.md`

| Product phase | `ai-company.md` phases |
| --- | --- |
| Phase 0 shell | — (product prerequisite) |
| Phase 1 foundation | Phase 0 (schema), Phase 1 (org/work), Phase 3 lite (inbox) |
| Phase 2 tool platform | Phase 1 (work/evidence), Phase 2 (authority/approvals), Phase 3 (co-working) |
| Phase 3 business ops | Phase 4 (function packs) + new business apps |
| Phase 4 autonomous | Phase 1–4 full (managers, escalation, rhythm, HR) |
| Phase 5 ecosystem | Phase 5 (hardening/GA) + marketplace |

The deep runtime work in `ai-company.md` (work items, policy engine, escalation,
team managers) is the backbone of Phases 1–4; the product phases decide what is
visible and in which order.

### First engineering slices

These are the first concrete cuts, ordered so each one is independently useful
and does not require the whole programme.

| # | Slice | Backlog | Why first |
| --- | --- | --- | --- |
| 1 | Company tool registry + acquisition modes (built-in / installed / MCP / built) with status/health/grants | D1, D5, D13 | The tool platform needs an object before a UI |
| 2 | Docker/workspace install adapter + a first catalog manifest (e.g. database, CRM, helpdesk) | D5, F8 | Proves “install a tool on the server” end to end |
| 3 | MCP publish/register flow for company-built tools + per-profile grant (admin-only) | D14 | Proves companies can extend the platform without forking |
| 4 | Company setup wizard API + blueprint model (identity, model, goals, budget, metrics) | A1–A7 | The co-founder cannot plan without the business context |
| 5 | Generalist role/function packs + team generator extension | B1–B3 | The org must fit the business, not IT |
| 6 | Internal ticket object + board/inbox projection (work item lite) | C1, C2 | Gives the team a real queue without a full WorkItem service |
| 7 | Today/inbox lite (approvals, drafts, KPIs) | C3, H1 | The owner’s daily surface |
| 8 | Budget/cap setup in the wizard + alerts/hard stop | A1, G4 | Makes broad autonomy safe |
| 9 | Built-in tool catalog/audit by business function | D16 | Confirms the existing toolset before building more |
| 10 | Policy skeleton with broad defaults + legal/high-risk approval | G1, G8 | The default envelope must be explicit from day one |

---

## 8. Success metrics

Business success metrics are **defined by the owner during company setup** and
depend on the business. codx-junior must not impose a universal revenue, lead or
growth target. The platform metrics below are about whether codx-junior is doing
its job as a co-founder.

**Time-to-value (platform)**
- Time to first usable blueprint: < 30 minutes.
- Time to first installed/built tool available to a profile: < 1 day.
- Time to first business review with real data: < 2 weeks.

**Owner experience**
- Owner attention time: < 15 minutes/day after setup.
- ≥ 80% of inbox items resolved the same day.
- ≥ 90% of approvals completed in one click (no context switching).
- Owner NPS ≥ 40.

**AI operation**
- 100% of cross-team work items have an owner, SLA and evidence.
- Median decision latency (P2) < 4 working hours.
- ≥ 90% of blocked work produces a structured escalation/decision.
- Zero *unauthorized* external/money actions (outside policy/budget); 100% of
  legal/high-risk actions have a human approver in the audit.
- AI autonomy ratio rises while incidents stay at zero P0.

**Tool platform**
- Time to install/configure a catalog tool: < 15 minutes.
- Time to build and expose a custom tool: < 1 week for a simple case.
- Tool health/deploy success rate/rollback rate.
- Percentage of profile-requested tools available through built-in tools,
  installed services or MCP.

**Usage visibility**
- The existing token/tool analytics are surfaced in company settings/reports;
  no new attribution/ledger workstream.
- 100% of companies have a budget/cap configured at setup.

**Company-defined outcomes**
- The owner defines the KPIs and targets in the blueprint (leads, revenue,
  retention, support, delivery, content, cost…); codx-junior tracks them and
  reports against them.
- Owner-reported “the company did this without me” wins per week.

**Cost**
- Cost per delivered tool/work item flat or down while throughput rises.
- No budget overrun without an explicit policy/approval.

---

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Scope explosion (“any tool, any business”) | High | Generalist primitives + a small set of packs/templates; ship one vertical slice at a time; tool catalog + MCP keep custom work out of core |
| The co-founder becomes a bureaucratic ERP | High | Inbox-first, defaults to action, one-click decisions, measure decision latency, remove rules that do not earn their keep |
| Broad autonomy sends/publishes/spends something wrong | Critical | Policy/action classes, budgets, rate limits, content/brand checks, external-recipient controls, dry-run, kill switch, audit, human top for legal/high-risk |
| Hallucinated numbers/claims | High | Ground every number in data; sources/links; verification gates; “I don’t know” is allowed |
| Tool quality/security (installed or built) | High | Mandatory QA/security gates, templates, review, rollback, health checks, container isolation |
| Docker/tool sprawl and resource pressure on the server | Medium | Resource limits, per-company quotas, health/usage monitoring, uninstall/cleanup, image pinning |
| A custom tool/MCP server becomes a privilege or exfiltration path | High | Scoped tokens, tool allowlists, per-profile grants, network isolation, audit, security review |
| Connector fragility/ToS | Medium | Connector registry, health, fallbacks, manual relay, native-lite objects |
| Legal/compliance (travel, finance, contracts, GDPR) | High | Human sign-off for regulated actions; templates reviewed by counsel; disclaimers; jurisdiction config |
| Data leakage across companies | High | Company isolation, scoped memory, permissions, audit, pen-test |
| Owner cannot understand/trust the AI | High | Plain language, explainability, sources, previews, takeover, gradual independence |
| AI cost runaway | Medium | Per-company/team/profile budgets, forecasts, hard stops, model routing |
| Mobile/app-store deployment complexity | Medium | Phase 5, explicit credentials/consent, store review checklist |
| One-person dependency / maintenance | Medium | Reuse existing runtimes, tests as spec, document decisions, phased ownership |

---

## 10. Built-in tool audit (checked in this checkout)

The built-in toolset is broad. Grouping the registered profile tools
(`api/codx/junior/tools/`) by business capability:

| Capability | Status in this checkout |
| --- | --- |
| Web research (search, fetch page) | Built-in tools |
| Browser automation / browser scripts | Built-in tools |
| Computer/desktop control (Linux/Windows/macOS/remote) | Built-in tools |
| Files, folders, code read/write, git, repository | Built-in tools |
| Images (generate, explain), documents (convert, markdown/PDF/DOCX) | Built-in tools |
| Docker, containers, compose, image registry, infra | Built-in tools |
| QA, CISO/security, secrets, policy | Built-in tools |
| Tasks, schedules, orchestration, delegation | Built-in tools |
| Profiles, teams, company management, knowledge search | Built-in tools |
| Voice switching, tutorials, browser recipes | Built-in tools |
| Calendar / meetings | Built-in UI and API (`calendar/`, `meets/`), **not wrapped as profile tools** |
| Chat / channels / messenger / wiki / files UI | Built-in UI/API; not a tool gap |
| Email (SMTP/IMAP, Gmail) | `external-actors.md` claims an email MVP, but the referenced `external/` code is **not present in this checkout**; Gmail is spec-only. Verify before relying on it. |
| Slack / WhatsApp | Spec/design only (`slack-whatsapp-integration.md`); not implemented here |
| CRM, helpdesk, billing, CMS, industry APIs | Not native by design: install a service and/or expose an MCP server |
| Internal tickets | No dedicated object; boards/tasks/chats cover work today. A native ticket view is optional. |
| Contacts / external actors | Documented, but not present in this checkout; verify |
| Usage/cost | Token/tool analytics exist per user/project/model/tool; chat sessions carry profiles. No new ledger workstream. |

**Conclusion:** there is no broad missing-tool gap. The real items are:
1. tool wrappers for surfaces that already exist but are not exposed to profiles
   (calendar, meetings);
2. documentation/code mismatches to verify (email/external actors, Slack,
   WhatsApp);
3. optional native apps that are product decisions, not missing capabilities
   (internal tickets, CRM-lite).

No new tool framework is needed; the work is to **surface, grant and govern**
what exists, and to install/connect/build only when the business needs it.

---

## 11. Decisions and missing details

### Decided in this round

- **Installed platform:** codx-junior runs on a server with Docker, is open
  source and business-agnostic. There is no core billing/pricing.
- **Core capability:** install/setup/configure an existing tool, develop a new
  one when needed, and expose it to profiles through the built-in toolset,
  Docker/workspaces/containers or an MCP server.
- **codx-junior-cloud is an example company**, not a core platform workstream.
  Its SaaS/billing/hosting needs are its own tools and workflows; they must not
  shape the generic company model.
- **Cloud providers are out of scope** for core codx-junior. Tools run on the
  codx-junior server/workspaces; external services are reached through tools or
  connectors.
- **Wizard:** 5-minute core + progressive discovery; budget/cap and company
  success metrics are defined during setup.
- **Tickets:** internal work tickets first (ops/IT/admin/work requests);
  customer support/helpdesk later.
- **Independence:** broad autonomy within policy/budget; legal/high-risk and
  irreversible actions need human sign-off.
- **Usage:** usage data already exists (token/tool analytics and chat profiles);
  no new ledger; only surface it and let the owner define success metrics.
- **Company-built tools / MCP:** publishing/registering an MCP tool is
  **admin-only**, using the existing user levels (the first user is admin).
  No extra approval workflow or separate registry is required.
- **Human team:** solo owner first, multi-human ready.

### Tool acquisition paths (Q1 explained)

There are three ways a tool becomes available to a profile. They share the same
tool registry/UI, but each has a different first end-to-end flow:

| Path | What happens | Examples | Main work | Best when |
| --- | --- | --- | --- | --- |
| **A. Install** | codx-junior installs a ready-made service in Docker/a workspace and exposes its tools | database, CRM, helpdesk, CMS, automation, internal admin app | catalog manifests, compose/container lifecycle, health, upgrades, secrets, resource limits | a mature self-hostable tool already exists |
| **B. Connect** | the tool already runs elsewhere; codx-junior registers its MCP endpoint and grants tools | company-built MCP server, existing CRM/helpdesk, partner API | MCP registry, scopes, health/test, per-profile grants, admin UI | the company already uses a tool or another team runs it |
| **C. Build** | no existing tool fits; AI designs/builds/tests/runs a new tool | booking site, custom dashboard, automation, SaaS MVP | project pipeline, QA/security/run evidence, maintenance | the capability is a differentiator or does not exist off the shelf |

The paths are not exclusive: a company may install a CRM, connect its payment
provider, and build its own onboarding tool.

**Q1 is only about the first end-to-end flow to productize**, because that
decides what Phase 2 proves. My read of the codebase:

- **Connect (B) is the cheapest proof** — MCP is already implemented and an
  admin can register a server; the missing piece is the product-level
  registry/grants UI.
- **Install (A) is the highest value for common businesses** — Docker and
  workspaces are strong, but there is no catalog/install/health product yet.
- **Build (C) is the differentiator but the heaviest** — it needs the
  QA/security/run/evidence pipeline to be trustworthy.

Recommended sequence: **Connect → Install → Build** if speed to a working tool
matters; **Install → Connect → Build** if the first demo should be a complete
business system; **Build first** only if “build me a tool” is the flagship
promise.

### Remaining questions

1. **First tool acquisition path.** Given the three paths above, which should
   Phase 2 prove first: Connect (MCP), Install (Docker catalog) or Build?
2. **Company success metrics.** The owner defines them; do we provide templates
   by business type, or start from a blank KPI list with AI suggestions?
3. **Mobile apps.** Needed in the first year, or web + automation first?
4. **Support/helpdesk timing.** After internal tickets, or in the first release
   because codx-junior-cloud or other companies need it?
5. **Data import/portability.** Which sources must be importable first
   (spreadsheets, website, email, existing CRM, docs)?
6. **Legal jurisdiction and language.** Which market(s) first (EU/Spain/EN, US,
   global)? This drives GDPR, contracts, content disclaimers and locale, even
   without core billing.

---

## Appendix — where to look in the code

- Company domain: `api/codx/junior/company/models.py`, `manager.py`, `store.py`,
  `templates.py`, `profile_advisor.py`, `assistant.py`, `autopilot.py`.
- Company API/UI: `api/codx/junior/api/company.py`,
  `client/src/views/CompanyView.vue`, `client/src/components/company/*`.
- Teams/profiles/generation: `api/codx/junior/teams/generator.py`,
  `api/codx/junior/profiles/`, `client/src/components/teams/*`.
- Work/boards/chats: `api/codx/junior/task_board.py`,
  `api/codx/junior/api/tasks.py`, `client/src/components/kanban/*`,
  `client/src/components/chat/*`.
- Autonomy/actors/orchestration: `api/codx/junior/autonomy/`,
  `api/codx/junior/orchestration/`, `docs/features/autonomous-agents.md`.
- External channels: `docs/features/external-actors.md`,
  `docs/features/slack-whatsapp-integration.md`, `api/codx/junior/integrations/`.
- MCP/browser/computer: `api/codx/junior/api/mcp.py`,
  `docs/features/mcp-connections.md`, `docs/features/browser-use.md`,
  `docs/features/computer-use.md`.
- Tool platform: `api/codx/junior/tools/`, `api/codx/junior/custom_tools/`,
  `api/codx/junior/mcp/`, `docs/features/plugins.md`,
  `docs/features/workspaces.md`, `api/codx/junior/workspaces/`,
  `api/codx/junior/infra/`.
- Usage/cost: `api/codx/junior/analytics/`, `api/codx/junior/model/wallet.py`,
  `api/codx/junior/model/user.py` (token limits),
  `api/codx/junior/api/users.py`, `docs/features/budget-and-analytics.md`.
- Settings/shell: `docs/transformation/company-first-shell.md`,
  `client/src/views/GlobalSettings.vue`,
  `client/src/components/company/CompanySettings.vue`.
- Deep target model: `docs/transformation/ai-company.md` (org graph, work
  protocol, escalation, authority, co-working, function packs, implementation
  phases).

---

*Draft for discussion. No code has been changed by this document.*
