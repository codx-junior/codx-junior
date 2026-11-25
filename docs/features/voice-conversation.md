# Voice conversation mode

Voice conversation mode turns a chat with codx-junior into a phone call: you
speak an instruction, codx-junior executes it through the normal agent pipeline
and reads the answer back. The transcript is **not** a separate recording
session — every utterance and every answer is a regular chat message, so the
conversation can be read, searched, resumed and exported like any other chat.

## Starting a call

There are three entry points, all leading to the same call panel:

| Action | Where |
| --- | --- |
| **Call this chat** | Phone button in the chat header, or *Voice call* in the composer ⋯ menu — the call starts immediately |
| **New conversation** | *New conversation* in the call setup panel (creates a new chat) |
| **Continue a call** | *Voice calls…* in the composer ⋯ menu, or the home-screen button, opens the setup with the *Recent calls* list |

When a call starts, the call panel is shown, the chat is opened next to it and a
short notice is added to the transcript. The call can be minimized to a floating
pill and keeps running while you navigate to another chat or panel.

### Language

The call speaks and listens in the codx user's **Assistant language**:

- an explicit setting (`es`, `pt`, `ja`, …) wins and is mapped to a speech
  locale (`es` + a Spanish browser → `es-MX`, otherwise `es-ES`);
- `Automatic` (or no value) uses the browser language;
- the setup panel can override it for the current call.

Admins can set the same field in **Global Settings → Users** (the user's
*Assistant language* select), and users can change it in **Account settings**.
The value is stored on the `CodxUser` profile, so it follows the account across
browsers.

## The call panel

- **Orb + status**: listening, transcribing, thinking, speaking, switching or
  needs attention.
- **Live captions**: the last messages of the call plus the current interim
  transcript.
- **Microphone**: mute/unmute without ending the call.
- **Captions**: hide/show the transcript in the call panel (the chat always has
  it).
- **Language**: the language icon in the call controls switches the language of
  the running call (microphone recognition and spoken answers) and persists it
  on the session, so a resumed call keeps the new language.
- **Repeat / stop**: replay the last answer or stop the current playback.
- **Barge-in**: if you start speaking while codx-junior is answering, playback
  stops immediately and the microphone listens again. Only the user's speech
  triggers it: the assistant's own words picked up by the microphone are
  filtered out. It uses the speech recognizer (with a microphone-level
  fallback) and is skipped when muted or when voice input is unavailable.
- **Spoken progress**: while the agent is thinking or running tools, it says a
  short phrase every few seconds (e.g. *“Checking the project files.”*). The
  bullhorn button in the call controls turns this feedback on/off; it is on by
  default and deliberately sparse (first phrase after ~5s, at most 5 per turn).
- **Switch conversation**: pick another chat, or say *"switch to the payments
  task"*.
- **End**: ends the call and stores a final notice in the chat.
- **Type instead**: every call has a text input. It is the fallback when the
  browser has no microphone/SpeechRecognition, and it is useful in noisy places.

## Switching the conversation

A running call can move to another chat in two ways:

1. **The user asks for it** — the panel has a chat picker, and the deterministic
   voice commands *"switch to &lt;chat&gt;"*, *"change the conversation to
   &lt;chat&gt;"*, *"go back to &lt;chat&gt;"* (English) and *"cambia la
   conversación a &lt;chat&gt;"* (Spanish) are recognised before the message
   reaches the model. If the name is ambiguous, codx-junior asks for the full
   name instead of guessing.
2. **The assistant asks for it** — the `voice_switch_chat` tool records a
   pending switch on the call. The client applies it as soon as the answer that
   requested it has been spoken, so the answer stays in the chat where the user
   asked and the call then continues in the destination.

Either way both chats are informed:

- the destination gets *"Voice conversation switched here from …"*,
- the previous chat gets *"Voice conversation moved to …"*,
- the call panel and the chat header follow the move.

## How the transcript is stored

- Spoken user messages are normal `Message` rows with
  `meta_data.voice = { session_id, language, source: 'voice' }`; the chat
  renders a small **voice** badge next to them.
- Assistant answers are normal streamed messages. Nothing is duplicated.
- Call state (current chat, switched-chat trail, turn counters, pending switch)
  lives in the generated data plane, `<codx_path>/voice/sessions.json` by
  default (it moves with `CODX_JUNIOR_CODX_DATA_PATH` and is git-ignored), and
  is mirrored into `Chat.meta_data.voice_session`, so the chat knows the call is
  active.
- The call UI is global (`VoiceCallOverlay`), because a call may outlive the
  panel/route where it started.

## Speech engines

The default is entirely client-side and needs no configuration:

- **Input**: the browser `SpeechRecognition` API when available.
- **Output**: the browser `SpeechSynthesis` API.

When the browser cannot do one of them, codx-junior degrades gracefully:

| Situation | Behaviour |
| --- | --- |
| No SpeechRecognition, server transcription configured | records one utterance at a time (MediaRecorder + silence detection) and posts it to `POST /api/voice/transcribe` |
| No microphone or no transcription backend | the call still works as a text + voice-output conversation |
| No SpeechSynthesis and no server TTS | answers stay in the chat (captions) |
| Server TTS fails mid-call | the player falls back to the browser voice |

### Optional server backends

**Text-to-speech** (OpenAI-compatible `/audio/speech`):

```bash
CODX_JUNIOR_TTS_BASE_URL=https://api.openai.com/v1
CODX_JUNIOR_TTS_API_KEY=sk-...
CODX_JUNIOR_TTS_MODEL=tts-1
CODX_JUNIOR_TTS_VOICE=alloy
CODX_JUNIOR_TTS_FORMAT=mp3
```

`GET /api/voice/capabilities` reports what is available; the client only tries
the server voice when it says so and always falls back to the browser.

**Speech-to-text** (`faster-whisper`): install the optional dependency
(`pip install faster-whisper`) and, optionally, tune:

```bash
CODX_JUNIOR_WHISPER_MODEL=small
CODX_JUNIOR_WHISPER_DEVICE=cpu
CODX_JUNIOR_WHISPER_COMPUTE_TYPE=float32
```

The model is loaded lazily on the first transcription and cached. Without it,
`POST /api/voice/transcribe` answers `503` with a reason instead of failing the
call.

## API

All routes are under `/api/voice`:

| Route | Purpose |
| --- | --- |
| `GET /capabilities` | server speech backends, provider list and reasons |
| `GET /sessions` | recent calls (`status`, `chat_id`, `limit`) |
| `POST /sessions` | start/continue/resume a call (`chat_id`, optional `session_id`) |
| `GET /sessions/{id}` | session + current chat |
| `POST /sessions/{id}/switch` | move the call (`chat_id`, `reason`, `announce`) |
| `POST /sessions/{id}/language` | change the call language (`language`) |
| `POST /sessions/{id}/end` | end the call (`reason`, `announce`) |
| `POST /sessions/{id}/turns` | record the chat message ids of one turn |
| `POST /transcribe` | audio clip → text (optional local backend) |
| `POST /speech` | text → audio (optional provider; `503` when absent) |

The AI turn itself uses the existing chat endpoints/socket event. The voice
router never runs an AI call, which keeps the normal streaming, tools, cost
accounting and recovery behaviour intact.

## Assistant tool

`voice_switch_chat(chat, reason)` is available to the model during a chat. It
only acts when there is an **active voice call** for the current chat:

- resolves the destination by id, exact name or distinctive name fragment,
- rejects ambiguous matches with the candidate list,
- records `pending_switch` on the session and confirms the destination in the
  answer.

Outside a voice call it explains how to start one and does nothing.

## Limitations

- Browser `SpeechRecognition` availability depends on the browser; Chrome/Edge
  are the best supported. Firefox/Safari rely on the optional server
  transcription backend.
- The microphone stays open while codx-junior speaks, so barge-in keeps working:
  the assistant's own audio is filtered out of the transcript instead of the
  microphone being paused.
- A call can only switch between chats that are visible/loadable in the active
  project; the panel and the tool use the project's chat catalogue.
