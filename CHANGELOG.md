# Changelog

All notable changes to Chopify will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Local browser dashboard with project review, progress, logs, previews, caption
  editing, candidate selection and downloadable exports.
- Caption Studio with 13 presets, full typography/layout controls, word motion,
  highlighting, boxed backgrounds, and a source-synced overlay with 5-second
  preview.
- Automatic per-cue emphasis for numbers, money, ALL-CAPS and power words, plus
  optional deterministic emoji, capped at two emphasis words and one emoji per cue.
- Reusable caption presets through brand JSON; project caption overrides persist
  in `project.json` and layer over preset and brand values.
- Shared caption CLI flags and `--caption-json` for `chopify.py` and
  `render_clips.py`.
- Optional `hidden` and `emphasis` flags on transcript words; both burnt captions
  and SRT honor them.
- Direct local video input (MP4, MOV, MKV, WebM, M4V, AVI).
- Resumable projects with stage invalidation, per-clip checkpoints, and distinct
  output names for render variants; partial failures retain successful clips.
- Automatic long-source path: sources of 2 hours or more, 8 GB estimated download,
  or unknown duration use an audio-first probe and resumable ingest. Short
  sources keep the original single-pass workflow.
- Long URLs download audio only, or lowest-resolution video when there is no
  audio; local long files are read in place.
- Resumable 30-minute transcription chunks with 30-second overlap merging and
  global word times (`transcript.partNNN.json`).
- Resumable 30-minute discovery blocks with `max_len` boundary context
  (`candidates.partNNN.json`).
- Selected-range section rendering for long URLs: 10-second padding, 30-second
  merge gap, 10-minute cap, `source_offset` global-to-local time mapping, and
  automatic cleanup after a successful final render.
- Per-clip section and render errors (`render_errors`) keep successful outputs
  and cached sections for retry.
- Exhaustive sentence-seeded discovery with an AI-chosen duration ladder,
  `candidates.json` / `selection.json` diagnostics, and paginated Studio review.
- Fully local audio-evidence timeline with cached 1 Hz features, content
  profiles, adaptive evidence weights, and a 7.0 text floor for speech-sparse
  windows.
- Action-only and speech-sparse clip discovery using audio event seeds, grid
  seeds for varying tracks without peaks, and a clear failure for flat sources.
- Fully local visual evidence from cached 2 fps analysis: motion spikes,
  scene/UI changes, facecam presence, action arcs, and boundary quiet, plus a
  sparse whole-source baseline.
- Silent and no-commentary gameplay, PC-game, and stream support: flat audio is
  treated as unavailable so visual action can score on its own.
- Visual shortlisting before frame decoding, visual-only seeds for silent
  footage, a 6.0 cap for flat action windows, and evidence-checked boundary
  refinement.
- Visual diagnostics in `candidates.json` and `selection.json`:
  `visual_baseline`, `visual_ranges`, `visual_sections`, and warnings.
- `evidence` fields in `candidates.json` and `segments.json`, plus
  `evidence_available` and profile metadata in `selection.json`.
- Guided CLI prompts for minimum score and preferred length, plus `--no-input`
  and `--explain` for scripts and review.
- Serial batch queue with isolated folders and aggregate failure reporting.
- Word/timestamp editing and clip-relative SRT, including retimed tightened cuts.
- Validated reusable JSON brand presets for fonts, colors, size, margins and logo.
  Brand files now accept every Caption Studio field.
- Semantic clip search with optional local Ollama embeddings and document caching.
- Vertical podcast split-screen with two adjustable fixed crop centers.
- Workflow, troubleshooting, local API, and machine-readable product documentation.
- Static documentation website with canonical/social metadata, JSON-LD, sitemap
  and a GitHub Pages deployment workflow.

### Changed
- Default minimum clip score is now 8.0 (was 6.5 heuristic / 8.0 LLM).
- Long sources select the audio-first, chunked, block-scanned, section-rendered
  path automatically; there is no duration ceiling or mode toggle.
- Render cache keys include the section offset and section identity, so long
  project retries reuse finished clips without re-downloading their sections.
- Every clip at or above the threshold is returned by default; the ten-clip cap
  is now opt-in through `--max-clips`.
- Clip length is selected from a duration ladder; `--preferred-len` is a soft
  preference rather than a fixed target.
- Default clip range is 15-300 seconds (was 20-75).
- Audio evidence adjusts the calibrated text score through the source profile
  and evidence weight; content profiles never change the user-facing threshold.
- Combined audio and visual evidence uses 60/40 weighting; one modality is
  shrunk toward neutral, and text remains the final fallback.
- Discovery cache keys include the visual feature version, so an analysis
  upgrade invalidates only affected work.
- Discovery and selection are cached separately, so changing the score
  re-filters without rescanning the transcript.
- Matching successful stages resume by default; `--no-resume` forces processing.
- Caption style resolution is named preset, then brand preset, then per-project
  override; aspect defaults fill unset font size and margin.
- SRT cue grouping follows the caption style's words-per-cue value on project
  renders.
- Docs clarify setup downloads, fixed framing, language limits and score meaning.

### Fixed
- Overlapping candidate windows are no longer blocked by marking covered
  sentences as taken.
- Zero passing clips now writes an empty segment list instead of a fallback clip.
- LLM reranking now adjusts the evidence-aware final score instead of the raw
  text score, and no longer compounds an earlier adjustment on rerun.
- Split-screen draft panels use even YUV420 dimensions to avoid crashes in older
  ffmpeg/libass builds; padding preserves the intended preview resolution.
- Changed URL downloads cannot reuse an older source file.
- LLM prompt definitions are initialized before standalone scorer execution.
- Render failures return nonzero instead of reporting success.
- Relative output paths resolve before ffmpeg changes its working directory.
- Identical clip titles cannot overwrite each other's project exports.
- A failed long-source section records a per-clip error instead of discarding
  successful renders; remaining sections still render and retry later.

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
