# Changelog

All notable changes to Chopify will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Local video-type detection (podcast, gaming, movie, livestream, interview,
  tutorial, music, unknown) with reusable clipping formats that set length,
  scoring profile, captions, layout and live thresholds.
- `--video-type`, `--format`, `--video-type-model` and `--list-formats` flags,
  plus an interactive builder for custom formats when detection is uncertain.
- Studio detection banner, format selector and **Build new format** action.
- Live sessions and the agent interface accept type/format options.
- Fast local transcription with sherpa-onnx/Parakeet (port of PeakCut's ASR
  engine): silence skipping, quiet-point chunking and parallel recognizers for
  much faster prepares on CPU. Select with `--asr parakeet|whisper|auto`
  (default `parakeet`); faster-whisper remains as an automatic fallback.
- Long-source transcription now runs 30-minute parts in parallel through the
  shared recognizer pool (`CHOPIFY_ASR_PARTS`), and `--asr none` skips speech
  entirely for audio/visual-only scoring.
- Transcript block scans run in parallel (`CHOPIFY_SCAN_WORKERS`), and the
  long-URL discovery audio format is configurable (`CHOPIFY_DISCOVERY_AUDIO`).
- Clip rendering now runs in parallel (`CHOPIFY_RENDER_WORKERS`). Every render
  uses an isolated scratch directory so caption, track and tighten files cannot
  collide; both the CLI and the project/Studio render path benefit.
- Long-URL downloads use concurrent fragments (`CHOPIFY_YTDLP_FRAGMENTS`) and
  render sections are fetched in parallel (`CHOPIFY_SECTION_WORKERS`), with
  cache checks so retries only fetch what is missing.

### Fixed
- Brief provider failures now name the specific agent and reason (for example,
  "claude found but not authenticated") instead of a generic error, and
  `--brief-provider none` forces deterministic keyword rules.
- Live capture restarts a stalled stream and re-resolves expiring page URLs
  instead of silently stopping when ffmpeg exits.
- Live sessions now decode only completed HLS segments instead of the segment
  being written, avoiding partial-file decode failures.
- Visual signal frames are reshaped from raw ffmpeg bytes before analysis.
- Live clip rendering passes `do_tighten` to the renderer with its actual name.
- Live capture maps only the first video and audio streams, avoiding DTS muxer
  failures on streams that expose extra data streams, and logs ffmpeg errors to
  a file instead of an undrained pipe.

### Added
- Agent-native interface: `python agent.py describe` exposes a self-describing
  operation catalog with JSON Schemas; all commands emit NDJSON with typed
  errors and stable exit codes. An installable `SKILL.md` teaches agents the
  full create → prepare → review → render → verify workflow. No MCP server or
  new dependency is required.
- Live reliability: page-URL streams are re-resolved every 10 minutes and on
  capture exit, stalled capture restarts with backoff, and a `health` object
  (`state`, segment age, restarts, refreshes, warnings, brief source/provider)
  is saved to `live_session.json` and shown in Studio and the CLI.
- Best-effort ad-slate suppression with `--live-skip-ads` (off by default). It
  only suppresses clip triggers; footage is still buffered and never discarded.
- An offline HLS fixture integration test drives the live pipeline with local
  ffmpeg — no network, account, or API key required.
- Campaign brief clipping: paste requirements in Studio or via `--brief` and a
  local LLM agent (Ollama, Claude, Codex, Hermes, or custom command) extracts
  structured rules into `brief_rules.json`. Batch and live clips are filtered
  and ranked by those rules, with a keyword fallback when no agent is available.
- Gaming `stacked` layout: facecam above gameplay with captions at the seam,
  available in batch and live modes from CLI and Studio. `--facecam-box` sets an
  explicit normalized rectangle; auto-detection is used when it is omitted.
- `--fit contain` renders vertical clips without cropping: the whole 16:9 frame
  is scaled into 9:16 with black bars, useful for gameplay and screen captures.
  Live sessions forward the setting through CLI and Studio options.
- Live clipping from HLS/RTMP URLs: `chopify --live URL` watches a stream with a
  rolling segment buffer, scores audio, visual motion, transcript and chat hype
  in real time, and automatically saves finished 9:16 captioned clips. A Live
  tab in Studio starts/stops sessions and shows the score and clip feed.
- `docs/LIVE.md` covers live sources, signals, options, session files, and the
  rights notice; linked from README, CLI reference, and `llms.txt`.
- `python chopify.py --version` prints the release version and exits 0 without
  requiring a source, ffmpeg or network.
- `python chopify.py --list-styles` prints the 13 caption preset names and
  exits 0 before any source check or preflight.
- `docs/CLI.md` documents every CLI flag, grouped by input, scoring/discovery,
  captions, output, batch/project and diagnostics; linked from the README and
  embedded in `llms-full.txt`.

## [1.4.0] - 2026-11-05

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
  audio; an audio track with no speech also switches to the low-resolution video
  so silent gameplay and streams can score from visual evidence. Local long files
  are read in place.
- Long-source Studio review: project detail exposes source metadata, progress,
  per-clip render errors, and whether the full source is playable. Watch on a
  long URL candidate queues a 480p preview from its downloaded section and plays
  the result.
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
- One-command installers for Windows and macOS/Linux, a contributor guide,
  issue templates, and a 1280x640 social preview for the documentation site.

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
- Long-source preview playback no longer draws the source caption overlay over
  the already-rendered draft.
- A stalled long-source section download falls back to a full-source download
  and continues rendering with `source_offset=0`.

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

[Unreleased]: https://github.com/mehbul/chopify/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/mehbul/chopify/releases/tag/v1.4.0
[1.3.0]: https://github.com/mehbul/chopify/releases/tag/v1.3.0
