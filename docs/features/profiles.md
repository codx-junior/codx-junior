# Profiles — the agent team

Profiles are reusable instructions that tell the AI how to behave. They encode
your coding standards, your architecture and your preferences — and they are how
codx-junior composes a **team** instead of one generalist assistant.

A fullstack slice is not one prompt: it is a schema change, an API, a UI, a test
pass and a security check. Each step can be a different profile with its own
instructions, tools and model, while the shared board carries the handoff and a
human reviews the diff.

| Profile | What it owns | What it hands back |
| --- | --- | --- |
| **project / PM** | Scope, acceptance criteria and the task breakdown. | A board card the team can work. |
| **software_developer** | The implementation across backend and frontend. | A diff and the tests that prove it. |
| **qa / qa_automation** | The verification plan and the regression suite. | A test run and a list of what still fails. |
| **ciso** | Secrets, dependencies and the security gate. | Findings and a pass/block verdict. |
| **analyst** | Requirements and architecture context. | A sharper task, less rework. |
| **human (you)** | Direction, trade-offs and the merge. | The final commit. |

Fifteen profiles ship by default. The **human** multi-user team (invitations,
shared channels, messenger) is 0.1; the AI team works today.

![Profiles](/images/NOV2025/codx-junior-profiles.png)

![The shared team board with role chips](/images/0.0.1/team-board.png)

*The shared board: each task carries its role (project, developer, QA, CISO,
analyst), status, priority and project.*

## What a profile contains

| Field | Description |
| --- | --- |
| Name, avatar, description | How the profile appears in chats and mentions. |
| Category | `global`, `file`, `coding` and others. |
| File match | A pattern; the profile is applied automatically to matching files. A profile with a `guardian` block (or the `guardian` category) is a file-scoped [quality guardian](/features/quality-guardians). |
| Content | The instructions, in Markdown. |
| Linked profiles | Other profiles to include. |
| Model | The LLM the profile uses. |
| Tools | The [agent tools](/features/chats-and-agents#agent-tools) and [MCP connections](/features/mcp-connections) it may call. |
| Use knowledge | Whether the profile searches the project's knowledge. |
| Chat mode | Changes how the conversation works, for example writing a document instead of chatting. |
| API settings | Expose the profile as a model through the [OpenAI-compatible API](/features/api). |
| Autonomy | Turn the profile into an [autonomous agent](/features/autonomous-agents) that takes its own shifts. |
| Voice | How the profile sounds when it narrates in the Project's Channel. |

An empty **Model** means the profile inherits the project's default model (and
the global default when the project has none). The turn resolves that fallback
explicitly and marks the response with a warning naming every profile that did
not pin a model, so inheriting is never silent; pin a model on the profile when
it must use a specific one.

## Built-in profiles

codx-junior ships with 15 profiles, including **project** (PM), **software_developer**, **qa**, **qa_automation**, **ciso**, **analyst**, **orchestrator** and **wiki**. Use them as they are, edit them, or add your own.

## Using profiles

* Pick a profile when you create a chat or a [Kanban task](/features/kanban); the card shows which role owns it.
* Mention a profile in a message to bring it into the conversation.
* Let file-matching profiles apply automatically when the AI edits matching files.
* Edit profiles from the profile view, or ask the AI to improve a profile from a chat.

## Voice

The voice is a **property of the profile**, not of a single video: every episode the profile narrates in the Project's Channel uses the same voice.

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | `false` means the profile **never speaks** in a channel video. There is no fallback voice. |
| `provider` | `""` | Voice provider (for example `piper`). Empty inherits the default. |
| `voice_id` | `""` | Voice of the provider (for example `en_US-amy-medium`). |
| `model` | `null` | Model override for the provider. |
| `speed` | `1.0` | Speaking rate. |
| `style` | `null` | Optional style hint (for example `narration`). |
| `language` | `null` | Language/locale of the narration. |
| `reference_asset` | `null` | Asset id used as a voice reference (cloning). |

Resolution: an omitted (or empty) voice field inherits the **project** voice setting, and when the project does not define one the **system default** applies. `enabled: false` is an explicit choice and is never overridden by that inheritance, so a silent profile stays silent.

Omitted fields are never rewritten: editing a profile without touching `voice` leaves the stored profile file byte-identical, so a voice can be set once and kept.

Set the voice from the profile editor, ask an assistant to do it, or use the API:

* `GET /api/profiles/{name}/voice` — **resolve** the effective voice of the profile: its own value with the project and then the system defaults filled in, plus the source the value came from.
* `POST /api/profiles/{name}/voice/preview` — **preview** the voice: renders a short audio sample so you can hear it before it is used in a video. It answers `503` when no speech provider is available instead of returning fake audio.


## Publishing a profile to third-party apps

A profile can be shared through the API (**Advanced → Shareable via API**) and
called by an external application with a [personal access
token](/features/personal-access-tokens). The token selects the project and,
optionally, the teammates it may use.

Before a profile is reachable by public users, enable **Public Exposure
Protection** in the teammate drawer. It adds a deterministic layer (scope,
keywords, blocked topics, tool allowlist, size limits) and an optional **decision
model** that classifies each request and answer as on-topic, off-topic, prompt
injection, misbehaviour, secret extraction or unsafe. A token can require this
protection, so a shared-but-unprotected profile is never called by a public
application.
