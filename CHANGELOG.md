# Changelog

All notable changes to Chopify will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Local browser dashboard with project review, progress, logs, previews, caption
  editing, candidate selection and downloadable exports.
- Direct local video input (MP4, MOV, MKV, WebM, M4V, AVI).
- Resumable projects with stage invalidation, per-clip checkpoints, and distinct
  output names for render variants; partial failures retain successful clips.
- Serial batch queue with isolated folders and aggregate failure reporting.
- Word/timestamp editing and clip-relative SRT, including retimed tightened cuts.
- Validated reusable JSON brand presets for fonts, colors, size, margins and logo.
- Semantic clip search with optional local Ollama embeddings and document caching.
- Vertical podcast split-screen with two adjustable fixed crop centers.
- Workflow, troubleshooting, local API, and machine-readable product documentation.
- Static documentation website with canonical/social metadata, JSON-LD, sitemap
  and a GitHub Pages deployment workflow.

### Fixed
- Split-screen draft panels use even YUV420 dimensions to avoid crashes in older
  ffmpeg/libass builds; padding preserves the intended preview resolution.
- Changed URL downloads cannot reuse an older source file.
- LLM prompt definitions are initialized before standalone scorer execution.
- Render failures return nonzero instead of reporting success.
- Relative output paths resolve before ffmpeg changes its working directory.
- Identical clip titles cannot overwrite each other's project exports.

### Changed
- Matching successful stages resume by default; `--no-resume` forces processing.
- Docs clarify setup downloads, fixed framing, language limits and score meaning.

## [1.3.0] - 2026-09-06

### Added
- **Complete-thought clips**: every clip is grown or trimmed to end on a sentence
  with terminal punctuation, so clips never start or end mid-thought.
- **Keyword search** (`--search "pricing"`): bypass virality scoring and build
  clips around transcript sentences containing all your keywords.
- **Review workflow** (`--clips 1,3,5`): scoring prints a numbered candidate
  table; render only the rows you pick.
- **Preview drafts** (`--preview`): render fast 480p cuts (no poster or
  metadata) to check your picks before the full-quality render.
- **Dependency pre-flight check**: before any stage, verifies ffmpeg/ffprobe are
  on PATH, the ffmpeg build can encode H.264 (libx264) and AAC, and required
  Python packages are installed — each problem reported with a fix hint. Bypass
  with `--no-preflight`.

### Changed
- Candidate selection now shares a single window-growth routine
  (`_grow_window`) between virality scoring and keyword search, both enforcing
  the complete-thought rule.

[Unreleased]: https://github.com/mehbul/chopify/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/mehbul/chopify/releases/tag/v1.3.0
