# Chopify interfaces for developers and local agents

Chopify is a local Python video clipper. Use the CLI for scripts; the dashboard
HTTP API is an authenticated loopback interface. No hosted API is provided.
Documentation updated 2026-11-03; implementation is the source of truth.

## CLI contract

```powershell
python chopify.py SOURCE --action prepare --workdir work
python chopify.py --project work --action render --clips 1,3 --preview
python chopify.py --project work --action render --clips 1,3
python chopify.py --batch batch.json --projects-dir projects
python chopify.py recording.mp4 --style bold --words-per-cue 2 --caption-position top
python chopify.py recording.mp4 --caption-json caption-style.json
python chopify.py --help
```

`SOURCE` is a local video path or yt-dlp-supported HTTP(S) URL. Actions: `prepare`
resolves/downloads, transcribes and scores; `render` uses saved transcript and
candidates; `all` does both. Resume is enabled unless `--no-resume`. Existing
projects keep settings unless explicit CLI flags change them. Use absolute
project paths when working from another directory.

`--style` selects one of the 13 caption presets. Caption field flags override it;
`--caption-json PATH` loads a validated full or partial style object, and explicit
flags win over JSON fields. The effective style is preset, then brand preset, then
the project's `options.caption` override. See
[Style captions in Caption Studio](WORKFLOWS.md#style-captions-in-caption-studio)
for fields and ranges.

Single-project output defaults to `clips/` or `CHOPIFY_OUT`; `--out` overrides it.
Batch outputs stay inside isolated project folders. Batch JSON is a nonempty
array of source strings. Exit 0 means the requested action completed; errors exit
nonzero, including partial render/batch failures. Standard output is a human log,
not a JSON-only protocol. Read project files for structured state.

## Long sources

There is no long-source mode flag. A source uses the long path when its duration
is unknown or at least 2 hours, or its estimated download is at least 8 GiB.
Long URLs download audio only. If the source has no audio, or its audio track
produces no transcript words, Chopify downloads the lowest-resolution video and
scores visual evidence instead. Long URL transcription runs in 30-minute chunks
with a 30-second overlap; completed parts are saved as `transcript.partNNN.json`.
Discovery scans 30-minute ownership blocks and caches `candidates.partNNN.json`.
Rendering downloads only the selected padded ranges into `sections/`, reuses them
for previews, and deletes them after a clean final render. Local long files render
in place. `segments.json` times stay global source seconds; section-local ffmpeg
seeks are internal through `source_offset`.

## Project data

`project.json` contains `id`, `source`, `options`, `status`, `stage`, `created`,
`updated`, `cache`, `renders`, `error`, and optionally `video`, `source_identity`,
`source_meta`, `progress`, `render_errors`, and `last_outputs`. Timestamps are
Unix seconds. Statuses include `new`, `queued`, `running`, `review`, `complete`,
`failed`, and `interrupted`.

`source_meta` records probe data including `duration`, `title`, URL and long
flags, dimensions, `estimated_size`, `media_kind`, `render_from_sections`, and
the downloaded `discovery` path for URL sources. `progress` is
`{stage, done, total, detail}` for the current stage. `render_errors` is a list
of `{clip, error}` entries for section or clip failures; successful outputs are
kept for retry.

`options.style` is the base caption preset. `options.caption` is a partial,
validated caption override or `null`. The effective style resolves preset, then
brand, then project override; unset font size and margin fall back to the aspect
defaults.

`transcript.json` contains `video` (absolute path), `duration` (seconds), `language`,
`model`, `segments`, and `words`:

```json
{"word": "Hello", "start": 1.0, "end": 1.4}
```

Long projects also record `source` (the original URL used for section downloads),
`source_duration`, `media_kind`, `render_from_sections`, `source_identity`, and
`chunked` with `chunks`. A silent or wordless source has an empty `words` list and
is scored from media evidence; `video` points at the low-resolution discovery
file while `source` remains the URL.

Words may also include optional `hidden` and `emphasis` booleans. Hidden words are
omitted from burnt captions and SRT. Forced emphasis is colored even when
automatic emphasis is disabled.

`segments.json` is an array with `start`, `end`, `hook`, and `overall`; semantic
selection may include `similarity`. Times are source-relative seconds. `--clips`
uses one-based row numbers. Scores are suggestions, not audience performance data.

## Python entry points

```python
from pathlib import Path
from projects import create_project, project_path, run_project

root = Path("projects").resolve()
state = create_project(root, "C:/Videos/podcast.mp4", {"aspect": "9:16"})
path = project_path(root, state["id"])
run_project(path, action="prepare")
run_project(path, action="render", pick="1,3", preview=True)
```

Other interfaces: `projects.run_batch(sources, root, options, **kwargs)`,
`projects.edit_transcript(path, words)`, `captions.update_transcript(data, words)`,
`branding.load_brand(path)`, and `semantic.search(sentences, query, model,
cache_dir)`. `branding.validate_caption(style)` validates a partial caption
override; `render_clips.resolve_caption(style, brand, caption)` returns the
concrete merged style. `projects.validate_options` validates options. These
repository-level interfaces are not a separately versioned SDK.

## Local HTTP API

Start `python dashboard.py --no-browser`. Use the printed origin exactly
(`http://127.0.0.1:8765` by default). No CORS access or remote binding is supported.
Requests with another Host are rejected.

`GET /api/config` returns `token`, `defaults`, saved brand names,
`caption_presets`, `caption_defaults`, `caption_aspects`, `caption_power_words`,
`caption_emoji_rules`, and `caption_brands` (non-null caption fields per valid
saved brand). The random token is scoped to the server process. All POST requests
require `X-Chopify-Token: TOKEN` and JSON bodies. Browser POST requests also
require a matching Origin. Never publish a running session's token.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/api/projects` | `{ "projects": [project, ...] }` |
| POST | `/api/projects` | `{ "source": "C:/video.mp4", "options": {} }`; creates without starting |
| POST | `/api/batch` | `{ "sources": ["C:/video.mp4"], "options": {} }`; creates and queues preparation, up to 100 sources |
| GET | `/api/projects/ID` | `{ "project": ..., "busy": false, "candidates": [], "transcript": ..., "selection": {}, "source_meta": {}, "progress": {}, "render_errors": [], "playable_source": true, "files": [], "log": "..." }` |
| POST | `/api/projects/ID/settings` | Partial settings, e.g. `{ "aspect": "9:16", "layout": "split", "style": "bold", "caption": { "words_per_cue": 2 } }`; `brand_name` selects a preset |
| POST | `/api/projects/ID/transcript` | `{ "words": [{ "word": "Hello", "start": 0, "end": 0.5 }] }` |
| POST | `/api/projects/ID/candidates` | `{ "candidates": [{ "start": 0, "end": 20, "hook": "Title", "overall": 7 }] }` |
| POST | `/api/projects/ID/run` | `{ "action": "render", "preview": true, "pick": "1,3" }`; queues job |
| POST | `/api/brands` | `{ "name": "channel", "preset": { "font": "Arial", "highlight": "#FFFF00", "animation": "pop" } }`; caption fields are optional |
| GET | `/media/ID/source` | Full source video for a project with `playable_source: true`; byte-range support |
| GET | `/media/ID/clips/FILE` | Project exports; `previews` also supported |

`GET /api/projects/ID` exposes `source_meta`, `selection`, `progress`,
`render_errors`, and `playable_source`. `playable_source` is true only when a
full local video exists and the project does not render from sections. When it is
false, **Watch** in Studio saves candidate edits and queues a 480p preview for
that one candidate through the run route (`action: "render"`, `preview: true`,
`pick: "<row>"`), then plays the resulting draft. That draft was rendered from a
downloaded section.

Mutations return `{ "ok": true }` unless a created object is documented. Errors
use `{ "error": "message" }` with 400, 403, or 404 status. Invalid caption fields,
unknown keys, or out-of-range values return 400 without changing the project.
Busy projects reject edits and duplicate runs. Poll detail for completion;
enqueueing is not success. On restart, unfinished jobs become `interrupted` and
need explicit retry.

Project files expose local paths and transcripts; keep them private. Studio is
for one trusted local user, not a multi-tenant service. Do not expose it through
a proxy or tunnel.
