# Chopify Studio design

Build the eight features requested on 2026-10-23 while preserving Python 3.11,
local processing, the existing CLI, and zero paid API requirements.

## Architecture and behavior

- A shared project runner accepts a local video or yt-dlp URL. A project stores
  its source identity, options, stage signatures, transcript, candidates, and
  rendered outputs. Atomic state writes make interrupted work resumable.
- Resume reuses only successful stages with matching inputs/options and existing
  outputs. Caption edits invalidate downstream renders. A failed render fails the
  job; completed clips remain reusable on retry. Distinct segment IDs prevent
  duplicate titles overwriting each other.
- Batch input is a JSON list of sources, processed serially into isolated project
  folders. One failure does not discard later jobs; the command exits nonzero if
  any fail. The dashboard uses the same serial queue to avoid GPU contention.
- A loopback-only stdlib HTTP server serves a self-contained browser dashboard.
  Browser mutations require a per-process token and same-origin requests. Routes
  expose only registered project files. Jobs can be prepared, reviewed, then
  rendered as drafts or final clips. Progress, logs, failures, and saved projects
  remain visible. The UI provides local path input; it does not upload video.
- A transcript editor changes word text and start/end timestamps, validates
  ordered finite times within source duration, and rebuilds sentence text.
  Clip exports include SRT using the same retimed words as burnt captions.
- JSON brand presets customize caption font, highlight/text colors, bottom
  margin, size, and optional logo. Persisted presets are reusable by CLI/UI.
- Semantic search embeds sentences and the query through local Ollama's embed
  API, caches source embeddings by model and transcript identity, ranks cosine
  similarity, and grows complete sentence windows. Missing models produce
  actionable errors; no silent keyword substitution or cloud fallback.
- Podcast split-screen stacks two crops in vertical output. Two adjustable
  horizontal face centers default to left/right quarter positions; this is
  framing for fixed-camera two-person footage, not speaker diarization.

## UI

Dark editing workspace, warm white type, lime action accent, a left project rail,
an input/queue section, candidate previews, and an editable transcript panel.
Responsive layouts and labelled controls support keyboard and mobile use.
All assets are served locally, with no CDN or external font requirement.

## Discoverability

Update README, changelog, task guides, and llms.txt with accurate commands and
limitations. Add a static GitHub Pages landing page with semantic HTML, social
metadata, SoftwareApplication structured data, sitemap and robots.txt, and a
Pages workflow. Do not promise rankings, virality, or universal platform support.
Replace stale pricing comparisons and unsupported "always offline" claims with
precise first-run model/download requirements. Machine-readable API and project
documentation let agents use actual interfaces rather than promotional prompts.

## Validation

Keep existing tests green; add behavior tests for local inputs, cache invalidation,
partial failures, isolation, transcript validation, subtitle retiming, semantic
ranking/cache, preset validation, and HTTP origin/path checks. Render synthetic
audio/video with real ffmpeg for landscape, vertical split-screen, brand/logo,
preview and final output. Inspect dashboard in browser. Run compilation, tests,
CLI help, JSON-LD validation and git diff checks before committing and pushing.
