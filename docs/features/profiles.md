# Profiles

Profiles are reusable instructions that tell the AI how to behave. They encode your coding standards, your architecture and your preferences.

![Profiles](/images/NOV2025/codx-junior-profiles.png)

## What a profile contains

| Field | Description |
| --- | --- |
| Name, avatar, description | How the profile appears in chats and mentions. |
| Category | `global`, `file`, `coding` and others. |
| File match | A pattern; the profile is applied automatically to matching files. |
| Content | The instructions, in Markdown. |
| Linked profiles | Other profiles to include. |
| Model | The LLM the profile uses. |
| Tools | The [agent tools](/features/chats-and-agents#agent-tools) it may call. |
| Use knowledge | Whether the profile searches the project's knowledge. |
| Chat mode | Changes how the conversation works, for example writing a document instead of chatting. |
| API settings | Expose the profile as a model through the [OpenAI-compatible API](/features/api). |
| Autonomy | Turn the profile into an [autonomous agent](/features/autonomous-agents) that takes its own shifts. |
| Voice | How the profile sounds when it narrates in the Project's Channel. |

## Built-in profiles

codx-junior ships with profiles such as **analyst**, **software developer**, **teacher**, **browser**, **project** and **wiki**. Use them as they are or as a starting point.

## Using profiles

* Pick profiles for a chat or a Kanban task.
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

