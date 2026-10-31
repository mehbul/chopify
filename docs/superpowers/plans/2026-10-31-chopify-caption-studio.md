# Chopify Caption Studio Implementation Plan

**Goal:** Deliver the approved Caption Studio design: complete caption presets,
full style controls, automatic emphasis and emoji, configurable cue grouping,
a live synced Studio preview, reusable presets, CLI/API support, tests, and docs.

**Architecture:** One validated caption-style object is resolved from preset ->
brand -> project override. `render_clips.build_ass` consumes the resolved style
and the existing ffmpeg/libass pipeline is unchanged. Studio keeps the style in
`project.json` as `options.caption`, previews it with a DOM overlay, and saves
named looks through the existing brand-preset endpoint.

**Tech stack:** Python 3.11+, stdlib HTTP/JSON, ffmpeg + libass, plain
HTML/CSS/JavaScript. No new services, network calls, or API keys.

## Constraints

- Existing CLI flags, project files, brand JSON files, and the 16:9 default
  caption render stay compatible.
- ASS accepts both `#RRGGBB` and the ASS color strings returned by
  `branding.load_brand`.
- Validate every new value before it reaches ffmpeg.
- Studio CSP stays `style-src 'self'`; overlay positioning uses CSS classes and
  CSSOM custom properties, not inline style attributes.
- Hidden words and per-word emphasis flags survive transcript saves.
- Default SRT grouping follows `words_per_cue`; hidden words are omitted.

## Tasks (execute inline)

### Task 1 - Caption style model and validation

**Files:** `branding.py`

- [ ] Add `CAPTION_DEFAULTS`, `CAPTION_FIELDS`, and `validate_caption`.
- [ ] Extend `validate_brand`/`DEFAULTS` with the new optional caption fields,
  keeping existing defaults and ASS color returns unchanged.
- [ ] Add focused tests for every field, unknown keys, bad colors, booleans
  where integers are required, and out-of-range values.

**Verify:** `.venv\Scripts\python -m pytest tests\test_caption_studio.py -q`

### Task 2 - Style resolution and ASS rendering

**Files:** `render_clips.py`

- [ ] Replace the four-name palette with 13 complete presets while keeping the
  existing names and default look.
- [ ] Add `resolve_caption(style_name, brand, caption)` with preset -> brand ->
  project precedence.
- [ ] Extend `build_ass` with configurable words per cue, alignment, uppercase,
  outline, shadow, background box/opacity, highlight modes, emphasis, emoji,
  and pop/bounce/fade/typewriter animation.
- [ ] Add `ass_style_color` (accepts hex and ASS strings), `ass_box_color`, the
  emphasis rules, and the local emoji keyword map.
- [ ] Add caption CLI flags via shared helpers
  (`add_caption_arguments`, `caption_from_args`).
- [ ] Pass the resolved `words_per_cue` to SRT export.

**Verify:** unit tests for layout, motion, emphasis, emoji and preset colors.

### Task 3 - Transcript and project plumbing

**Files:** `captions.py`, `projects.py`, `chopify.py`

- [ ] Preserve `hidden`/`emphasis` word flags in `update_transcript`; reject
  non-boolean flags.
- [ ] Skip hidden words in `write_srt` and keep configurable grouping.
- [ ] Add `caption` to `DEFAULT_OPTIONS`, validate it, include it in render
  cache keys, and pass it to `render_clips.render`.
- [ ] Add caption flags to the main CLI and update existing projects when any
  caption option is supplied.
- [ ] Add tests for SRT grouping, hidden words, project roundtrip, and CLI
  argument construction.

**Verify:** `.venv\Scripts\python -m pytest tests\ -q`

### Task 4 - Studio API and preview

**Files:** `dashboard.py`, `assets/studio/index.html`, `assets/studio/app.js`,
`assets/studio/style.css`

- [ ] Add `caption_presets`, `caption_defaults`, and per-aspect base sizes to
  `GET /api/config`.
- [ ] Add the Captions tab, caption overlay, settings form, live sync on
  play/seek, preset application, save style, save named preset, and a 5-second
  preview action.
- [ ] Keep controls keyboard reachable, label every control, mark the overlay
  `aria-hidden`, and disable caption controls while a job is running.
- [ ] Extend HTTP tests for config data, caption save/read, invalid caption
  rejection, and busy-project rejection.

**Verify:** dashboard tests plus a browser pass of the Captions tab.

### Task 5 - Real render integration

**Files:** `tests/test_render_integration.py`

- [ ] Render a boxed/pop caption style with real ffmpeg.
- [ ] Assert H.264 + AAC output, a playable file, and SRT cue groups matching
  `words_per_cue`.

### Task 6 - Documentation

**Files:** `README.md`, `docs/WORKFLOWS.md`, `docs/AGENT-API.md`,
`CHANGELOG.md`, `docs/product.json`, `llms.txt`, `docs/index.html`

- [ ] Document presets, controls, precedence, CLI flags, caption JSON, the
  preview approximation, and the intentionally excluded features.
- [ ] Update machine-readable capabilities and landing-page feature claims.

**Verify:** `.venv\Scripts\python -m pytest tests\ -q` and `tools/build_site.py`.

## Commit points

1. `feat: add caption style engine and controls`
2. `feat: add caption studio preview`
3. `docs: document caption studio`
