# Chopify interfaces for developers and local agents

Chopify is a local Python video clipper. Use the CLI for scripts; the dashboard
HTTP API is an authenticated loopback interface. No hosted API is provided.
Documentation updated 2026-10-23; implementation is the source of truth.

## CLI contract

```powershell
python chopify.py SOURCE --action prepare --workdir work
python chopify.py --project work --action render --clips 1,3 --preview
python chopify.py --project work --action render --clips 1,3
python chopify.py --batch batch.json --projects-dir projects
python chopify.py --help
```

`SOURCE` is a local video path or yt-dlp-supported HTTP(S) URL. Actions: `prepare`
resolves/downloads, transcribes and scores; `render` uses saved transcript and
candidates; `all` does both. Resume is enabled unless `--no-resume`. Existing
projects keep settings unless explicit CLI flags change them. Use absolute
project paths when working from another directory.

Single-project output defaults to `clips/` or `CHOPIFY_OUT`; `--out` overrides it.
Batch outputs stay inside isolated project folders. Batch JSON is a nonempty
array of source strings. Exit 0 means the requested action completed; errors exit
nonzero, including partial render/batch failures. Standard output is a human log,
not a JSON-only protocol. Read project files for structured state.

## Project data

`project.json` contains `id`, `source`, `options`, `status`, `stage`, `created`,
`updated`, `cache`, `renders`, `error`, and optionally `video`, `source_identity`,
and `last_outputs`. Timestamps are Unix seconds. Statuses include `new`, `queued`,
`running`, `review`, `complete`, `failed`, and `interrupted`.

`transcript.json` contains `video` (absolute path), `duration` (seconds), `language`,
`model`, `segments`, and `words`:

```json
{"word": "Hello", "start": 1.0, "end": 1.4}
```

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
cache_dir)`. `projects.validate_options` validates options. These repository-level
interfaces are not a separately versioned SDK.

## Local HTTP API

Start `python dashboard.py --no-browser`. Use the printed origin exactly
(`http://127.0.0.1:8765` by default). No CORS access or remote binding is supported.
Requests with another Host are rejected.

`GET /api/config` returns `token`, `defaults`, and saved brand names. The random
token is scoped to the server process. All POST requests require
`X-Chopify-Token: TOKEN` and JSON bodies. Browser POST requests also require a
matching Origin. Never publish a running session's token.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/api/projects` | `{ "projects": [project, ...] }` |
| POST | `/api/projects` | `{ "source": "C:/video.mp4", "options": {} }`; creates without starting |
| POST | `/api/batch` | `{ "sources": ["C:/video.mp4"], "options": {} }`; creates and queues preparation, up to 100 sources |
| GET | `/api/projects/ID` | `{ "project": ..., "busy": false, "candidates": [], "transcript": ..., "files": [], "log": "..." }` |
| POST | `/api/projects/ID/settings` | Partial settings, e.g. `{ "aspect": "9:16", "layout": "split" }`; `brand_name` selects a preset |
| POST | `/api/projects/ID/transcript` | `{ "words": [{ "word": "Hello", "start": 0, "end": 0.5 }] }` |
| POST | `/api/projects/ID/candidates` | `{ "candidates": [{ "start": 0, "end": 20, "hook": "Title", "overall": 7 }] }` |
| POST | `/api/projects/ID/run` | `{ "action": "render", "preview": true, "pick": "1,3" }`; queues job |
| POST | `/api/brands` | `{ "name": "channel", "preset": { "font": "Arial", "highlight": "#FFFF00" } }` |
| GET | `/media/ID/source` | Source video for a prepared project; byte-range support |
| GET | `/media/ID/clips/FILE` | Project exports; `previews` also supported |

Mutations return `{ "ok": true }` unless a created object is documented. Errors
use `{ "error": "message" }` with 400, 403, or 404 status. Busy projects reject
edits and duplicate runs. Poll detail for completion; enqueueing is not success.
On restart, unfinished jobs become `interrupted` and need explicit retry.

Project files expose local paths and transcripts; keep them private. Studio is
for one trusted local user, not a multi-tenant service. Do not expose it through
a proxy or tunnel.

## Image-to-PDF API

The same session token/Origin/Host requirements apply. This is a separate image
workflow; `chopify.py SOURCE` remains the video CLI.

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/api/pdf/config` | Image/PDF defaults, uploaded asset metadata, recent PDF summaries |
| POST | `/api/pdf/assets` | Raw image bytes, `Content-Type: application/octet-stream`, URL-encoded filename in `X-File-Name`; returns asset `id`, `name`, `width`, `height` |
| GET | `/api/pdf/assets/ID/image` | Normalized PNG |
| GET | `/api/pdf/assets/ID/thumbnail` | Thumbnail PNG |
| POST | `/api/pdf/preview` | `{ "page": { "asset": "ID", "settings": {} }, "options": {}, "index": 1, "total": 1 }`; returns JPEG |
| POST | `/api/pdf/export` | `{ "pages": [{ "asset": "ID", "settings": {} }], "options": {} }`; returns manifest with download `url` |
| GET | `/api/pdf/documents/ID` | Saved page order, image settings, options and export metadata |
| GET | `/api/pdf/documents/ID/download` | PDF attachment with chosen filename |

Defaults and accepted keys are defined in `pdf_images.IMAGE_DEFAULTS` and
`PDF_DEFAULTS`. The export's `pages` array defines the exact order and may repeat
asset IDs. Upload limits are 20 MB and 24 megapixels per image; documents contain
1–100 pages. Numeric values must be finite and within supported ranges. Export
is synchronous, and the server serializes PDF preview/export operations.

Python entry points: `pdf_images.upload(root, filename, data)`,
`pdf_images.preview(root, page, options, index=1, total=1)`, and
`pdf_images.export(root, pages, options)`. `root` is the project storage directory.
Saved assets/exports are local private data, not public site resources.
