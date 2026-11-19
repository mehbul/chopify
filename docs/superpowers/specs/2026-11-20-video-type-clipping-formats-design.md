# Video-type detection and clipping formats design

## Goal

When a source (local video, URL, or live stream) is given to Chopify, detect
**what kind of video it is** — podcast, gaming, movie, livestream, interview,
tutorial, music, or unknown — and use that type to choose the **clipping format**
for the whole job. A format is a reusable recipe that bundles the decisions
currently scattered across options: length band, scoring profile, caption look,
layout, tighten/loudnorm, and hook wording (plus live thresholds for streams).

If no format fits — detection is uncertain, the type is unknown, or the user
disagrees — the interactive surfaces **ask the user to build a new format**
instead of silently guessing. Non-interactive surfaces never block.

Example flow the user asked for:

```
source in -> detect type -> pick matching format
                          \-> uncertain/unknown -> ask user to choose or build a new format
                      -> clip the long-form video / stream with that format
```

## Decisions made with the user

| Decision | Choice |
| --- | --- |
| What the type controls | A full format recipe, shown to the user and overridable. |
| What a "format" is | A reusable, named JSON recipe with built-ins per type and user-created custom formats. |
| When no format fits | Interactive CLI/Studio asks the user to build one; non-interactive runs use a best guess and record a warning. |
| Detection engine | Deterministic local signals first (reuse `evidence.content_profile` inputs); optional local Ollama tie-break, opt-in. No cloud. |
| Live streams | In scope: same type/format layer feeds live caption/layout and live thresholds. |

## Non-goals

- No cloud service, paid API, API key, or downloaded video-understanding model.
  Named types are inferred from local audio/visual/transcript/metadata signals.
- No replacing the human review step or auto-publishing. Clip scores stay
  editing suggestions, not virality predictions.
- No new third-party Python dependency; stdlib plus what the project already
  uses (OpenCV, ffmpeg, optional Ollama over HTTP).
- No rewiring of the render internals: a format only supplies values for
  options that already exist.
- No drag-and-drop visual builder in v1; "build a new format" is a guided
  form with pre-filled defaults.

## Why this fits the current architecture

Chopify already classifies sources internally: `evidence.content_profile()`
in `evidence.py` returns `talk`, `mixed`, or `gameplay/action` from speech
density, visual baseline value, cut rate, and audio burst rate. Those profiles
already drive `PROFILE_BANDS` (length targets), `PROFILE_FLOORS` (evidence
influence), and duration tie-breaks inside `score_clips`. This feature is a
**thin named layer above that machinery**, not a new scoring engine:

- It maps the coarse profile plus a few extra local signals to named types.
- It turns a named type into a format that supplies existing option values.
- It persists the decision and exposes it in CLI, Studio, and the agent API.

## Architecture

```
source (file / URL / live stream)
        |
        v
download + transcribe -> audio timeline + visual baseline (already exist)
        |
        v
video_types.classify()  -> {type, confidence, signals, reason}
        |
        v
video_types.resolve_format() -> built-in or custom recipe (+ provenance)
        |         \
        |          \-- uncertain/unknown -> interactive: choose or build new
        v
working options = explicit user flags > format values > defaults
        |
        v
score_clips.discover/select (band + profile from format) -> candidates/segments
        |
        v
render (caption style, layout, fit, tighten, loudnorm from format)
```

## Components

### 1. `video_types.py` (new module)

Pure, testable functions with no I/O beyond reading already-computed
measurements and JSON format files.

- `VIDEO_TYPES`: `podcast`, `gaming`, `movie`, `livestream`, `interview`,
  `tutorial`, `music`, `unknown`.
- `classify(signals, llm=None)` ->
  `{"type", "confidence", "signals", "reason"}`.
  Input signals: speech density (words/sec), audio variability/burst rate,
  visual baseline value, cut rate, facecam presence, duration, and source
  metadata (host/filename hints, e.g. a Twitch URL or a filename containing
  "stream"). Scoring is a small weighted rule table seeded from
  `evidence.content_profile`; `confidence` is the margin between the top two
  type scores. `unknown` and `needs_confirmation=True` when no type clears a
  minimum threshold or the margin is below a threshold.
- `llm_classify(signals, transcript_sample, model, host)` (optional, opt-in
  via `--video-type-model MODEL`): ask a local Ollama chat model to pick one
  of `VIDEO_TYPES` from a compact prompt. Used only as a tie-break when the
  heuristic is uncertain. Unreachable model or junk output -> heuristic result
  plus a warning; never a cloud fallback.
- `FORMATS`: built-in recipes, one per named type (table below).
- `validate_format(data)` -> normalized recipe; rejects unknown type names,
  invalid caption styles/layouts/bands, and out-of-range live values with
  field-specific errors.
- `load_custom_formats(root)` / `save_custom_format(root, data)` -> JSON files
  under `<root>/formats/<name>.json`. Names are slug-validated; a custom format
  may override any built-in field but may not shadow a built-in name.
- `resolve_format(detection, requested_type, requested_format, root)` ->
  `{"format", "source": "detected"|"override"|"custom", "needs_confirmation"}`.
  An explicit `video_type` or `format` always wins over detection.

### 2. Built-in format recipes

| Type | Band (s) | Profile | Caption style | Layout / fit | Tighten | Loudnorm | Live overrides |
| --- | --- | --- | --- | --- | --- | --- | --- |
| podcast | 45–180 | talk | podcast | single / cover | yes | yes | — |
| interview | 45–180 | talk | podcast | single / cover | yes | yes | — |
| gaming | 15–60 | gameplay/action | bold | stacked* / cover | no | no | threshold 7.5, pre-roll 30, tail 8 |
| livestream | 15–90 | gameplay/action | bold | single / cover | no | no | threshold 7.5, pre-roll 30, tail 8, cooldown 45 |
| movie | 60–300 | mixed | minimal | single / contain | no | no | — |
| tutorial | 30–180 | mixed | clean | single / cover | no | yes | — |
| music | 15–90 | mixed | neon | single / cover | no | yes | — |
| unknown | 30–120 | mixed | default | single / cover | no | no | — |

\* A format's layout is a **preference** applied only when it is compatible with
the current aspect (stacked/split require 9:16). Otherwise `single` is used and
the reason is recorded in the detection stats. Explicit user layout always wins.
The same "explicit flags win" rule applies to every format field.

A recipe JSON shape:

```json
{
  "name": "gaming",
  "video_type": "gaming",
  "min_len": 15,
  "max_len": 60,
  "profile": "gameplay/action",
  "caption_style": "bold",
  "layout": "stacked",
  "fit": "cover",
  "tighten": false,
  "loudnorm": false,
  "hook_style": "event",
  "live": {"live_threshold": 7.5, "live_pre_roll": 30, "live_tail": 8,
           "live_cooldown": 45},
  "description": "Fast action cuts with bold captions."
}
```

### 3. Options, state, and cache plumbing (`projects.py`)

- Add `video_type="auto"` and `format=None` to `DEFAULT_OPTIONS`; both pass
  through `validate_options` (type name or `auto`; format name/path or `None`).
- Include both in `score_keys()` so changing the type/format invalidates the
  discovery and selection caches rather than reusing stale candidates.
- `run_project(..., on_detection=None)` gains an optional callback. During the
  prepare stage, after transcription and media evidence are available but
  before discovery, it calls `video_types.classify`, resolves the format,
  persists `state["detection"]` and `state["format"]`, and applies the
  format-derived values to a **working copy** of options (explicit user flags
  take precedence). The callback may return a different format choice; that
  choice is persisted and used for this run.
- `candidates.json` / `selection.json` stats gain `video_type`, `format`,
  `detection` (type, confidence, signals, reason), and `format_source`.

### 4. Scoring (`score_clips.py`)

- `discover(..., band=None, profile=None)` accepts an explicit profile and band;
  when supplied by a format they override the auto-derived values. Existing
  callers and defaults are unchanged.
- `source_evidence()` returns the same measurements used for classification,
  so no extra ffmpeg/OpenCV pass is introduced.
- `select()` continues to use `profile_band()` from the discovery stats.

### 5. CLI (`chopify.py`)

- New flags: `--video-type {auto,podcast,gaming,movie,livestream,interview,tutorial,music,unknown}`,
  `--format NAME_OR_PATH`, `--video-type-model MODEL` (optional local Ollama
  tie-break), `--list-formats`.
- Interactive run: the `on_detection` callback prints
  `Detected: Gaming (92%) · Format: gaming`. When `needs_confirmation` is true,
  prompt **use suggested / pick a built-in or custom format / build new**.
  "Build new" asks for a name and the recipe fields with defaults pre-filled
  from the detected type, saves it via `save_custom_format`, and continues.
- `--no-input`, `--batch`, and agent calls pass no callback: auto mode uses the
  best guess, writes a warning into the selection stats (`detection_uncertain`),
  and never blocks.

### 6. Studio (`dashboard.py`, `assets/studio/*`)

- `/api/config` returns `video_types` and the list of built-in + custom format
  names (plus each format's fields for the builder form).
- Project detail returns `detection` and `format` so a detection banner can be
  shown above the queue/prepare controls: `Detected: Podcast (88%) · Using format: podcast`
  with **Change format** and **Build new format** actions.
- New `POST /api/formats` validates and saves a custom format, guarded by the
  existing loopback/token/CSRF checks. A new "formats" section in the project
  settings lets the user pick type/format. All UI follows the existing
  Signal Desk tokens and accessibility rules.
- Live view shows the detected type/format alongside session state.

### 7. Live (`live.py`)

- `run_live` resolves the format before starting the session and applies format
  caption/layout/fit and `live` overrides to the working options. Explicit
  `--live-*` flags win.
- Detection for a stream is best-effort from the first buffered audio/visual
  and transcript signals; if insufficient, the type stays `unknown` with low
  confidence and the neutral format is used until the user picks one.
- Detected type/format are written into `live_session.json` and the live state
  API.

### 8. Agent API (`agent.py`, `docs/AGENT-API.md`)

- `video_type` and `format` are accepted in `--options-json` and appear in
  `describe` schemas and project state. Auto mode is silent (no callbacks, no
  prompts); agents can set an explicit type/format or read back the detection.

## Error handling

- Invalid format JSON, unknown type, unknown caption style/layout, bad band or
  live range -> field-specific error before any work starts.
- Custom format name colliding with a built-in -> rejected with the list of
  reserved names.
- Ollama unreachable/junk/invalid label -> heuristic result plus warning; the
  job continues.
- Media unreadable -> classify from transcript + metadata only with reduced
  confidence; existing transcript-only fallback still works.
- Missing `formats/` directory -> created lazily on first save; a corrupt
  custom format file is skipped with a warning, not fatal.
- Non-interactive surfaces never block on confirmation.

## Testing

- Unit tests (`tests/test_video_types.py`): classify synthetic signal sets for
  every named type; confidence/unknown thresholds; metadata hints; format
  validation and name collision; resolve precedence (explicit flag > custom >
  detected); heuristic fallback when LLM output is junk.
- Integration tests: `score_clips.discover/select` honor a format-supplied band
  and profile; stats contain detection/format fields; custom format files load
  and survive a project resume.
- CLI tests: flag parsing, `--list-formats`, interactive prompt vs `--no-input`
  never blocking, build-new-format save flow.
- Studio/API tests: config fields, `/api/formats` validation and token guard,
  banner data in project detail.
- Live tests: format overrides applied without clobbering explicit `--live-*`
  flags; detection recorded in session state.
- Full suite must stay green (baseline: 373 passing tests) plus compile checks.

## Documentation

- `README.md`: feature row, quick example, FAQ entry on detection accuracy and
  override.
- `docs/CLI.md`: new flags and format authoring.
- `docs/AGENT-API.md`: new option fields and state fields.
- `docs/WORKFLOWS.md`: when/how to build a format.
- `CHANGELOG.md` and `docs/product.json`: new capability.

## Risks and mitigations

- **Misclassification** (dialogue-heavy movie reads as podcast): confidence and
  the explicit override/build path are the contract; document that names are
  inferred, not guaranteed. `contain` fit for movies preserves wide frames.
- **Cache staleness**: include type/format in `score_keys` so a changed format
  cannot silently reuse old candidates.
- **Scope creep**: formats only set existing option values; no new render code,
  no new dependencies, no visual builder in v1.
