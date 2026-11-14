# Creating content in the Project's Channel

The channel is not only a library: an admin can ask for **new** content, attach
the material it must be built from and let the AI produce it. This page is the
operator guide.

## Who can do it

Only project admins. The API enforces it (`403` on every write) and the UI hides
the section: a non-admin who deep links to `?tab=creator` still sees the formats
but every action is disabled, with a *"Creating and building content is
restricted to project admins"* notice.

## Where it is

`/:projectName/channel` → the **Create** button in the header (or the deep link
`/:projectName/channel?tab=creator`). It is deliberately **not** one of the five
public tabs (Home / Library / Episodes / Shorts / Learn): the channel stays a
reading and watching surface, creating is an admin tool inside it.

## The flow

1. **Say what you want.** One sentence is enough:
   *"A video showing how the new channel feature works in codx-junior."*
2. **Pick the outputs** — one or more:

   | Format | Output | Produced by |
   | --- | --- | --- |
   | `video` | 16:9 episode (`mp4`) | media worker (`episode` job) |
   | `short` | 9:16 clip, under a minute (`mp4`) | media worker (`short` job) |
   | `podcast` | audio (`m4a`) | builder profile |
   | `wiki` | documentation page (`md`) | builder profile |
   | `slides` | deck (`pdf`) | builder profile |
   | `doc` | written guide (`md`) | builder profile |

3. **Details** (optional, collapsed): title, audience, tone, language, target
   length and free-form notes for the builder. The title becomes the task name
   and the output file names, so it is worth filling in.
4. **Sources**: paste links, and upload images, videos, audio or documents with
   *Upload files*. **Every content item gets its own folder**, so the uploads of
   one piece never mix with another's.
5. **Save** stores the brief. Nothing is rendered yet.
6. **Ask the content creator** opens a chat with the `content_creator` profile,
   pre-filled with the intent, the outputs and the source list. It interviews
   you, grounds the story in the real project files and returns a finished brief.
7. **Build** is the hand-off:
   - the brief is frozen into `build.md` (the instructions),
   - a media job is queued for every format the worker renders
     (`video` → `episode`, `short` → `short`),
   - a task is opened with the **`codx-junior`** profile carrying the prompt, the
     brief and the sources, and it starts working immediately.

The media worker (`.codx/media/worker.py`, wired into `background.py`) picks the
jobs up with leases and retries, so a restart resumes instead of losing work.

## What lands on disk

```
.codx/media/content/<slug>/
    brief.json     the brief (source of truth; the UI always re-reads it)
    brief.md       rendered brief, what the assistant and the builder read
    build.md       build instructions generated on Build
    sources/       the uploaded files of THIS content
    outputs/       rendered artifacts (mp4, m4a, md, pdf ...)
```

Everything is content addressed by the brief, so a media file can be deleted and
regenerated: **text is the truth, video is derived**.

## Profiles involved

| Profile | Role |
| --- | --- |
| `content_creator` | Shapes the brief: interviews you, proposes formats, writes the story and the source list. Never renders, never publishes. |
| `codx-junior` | The builder: reads `brief.md` + `build.md`, produces the artifacts and enqueues the media jobs. |
| media worker | Renders the queued jobs. No reasoning, retryable, restart-safe. |

## API

All under `/api/content` (auto-discovered router, no `app.py` change):

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/formats` | output formats, the profiles used and the upload limit |
| `GET` | `/items` | every content item, newest first |
| `POST` | `/items` | create an item (folders + `brief.json` + `brief.md`) |
| `GET` | `/items/{id}` | brief, sources, outputs and the rendered markdown paths |
| `POST` | `/items/{id}` | partial update of the brief |
| `DELETE` | `/items/{id}` | delete the item folder |
| `POST` | `/items/{id}/sources` | attach a link / project reference / note |
| `POST` | `/items/{id}/uploads` | upload one file (`{name, mime, data_base64}`) |
| `DELETE` | `/items/{id}/sources/{source_id}` | detach a source (deletes the file) |
| `GET` | `/items/{id}/file/{name}` | serve a source or an output |
| `POST` | `/items/{id}/build` | plan, enqueue the jobs, write `build.md`, return the builder task |

Uploads travel as base64 inside a json body on purpose: no `python-multipart`
dependency, works behind every proxy, and one item is one folder. The limit is
**32 MB per file**.

## Safety

- **Admin only** on every write.
- Uploaded names are sanitised (no separators, no `..`), paths are resolved
  inside the item folder, and the target of a source can never escape it.
- The brief is a *description*, not a dump: `.env` values, tokens and client
  names are described, never copied — and the builder runs the redaction gate
  (`codx.junior.media.redaction.scan`) before anything is published.
- Building never publishes: rendering fills `outputs/`, publishing is a separate,
  manual step (`policy_for()` defaults to **never**).

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| *"The content API is not reachable"* | The server started before `/api/content` existed. Restart it — `discover_routers()` only runs at startup. |
| The Create button is missing | Your user is not an admin (`role: 'admin'`). |
| *"Select at least one output format before building"* | `formats` is empty; pick a chip. |
| A build stays queued | Check `GET /api/media/queue`: if `counts.queued` grows, the media worker is not running (`worker: off`). |
| Upload rejected | File bigger than 32 MB, or empty. |
