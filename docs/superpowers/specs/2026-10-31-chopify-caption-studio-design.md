# Chopify Caption Studio design

## Goal

Close the caption gap between Chopify and Klap without giving up Chopify's
local-only, zero-paid-API model. The work adds a styled, animated caption system,
a live synced preview in Studio, automatic emphasis and emoji, reusable caption
presets, CLI controls, tests, and documentation.

The feature is for the creator who already has a transcript and wants the clip to
look finished before exporting. It is not a cloud editor, a translation service,
or a social publisher.

## What we take from Klap

Klap's caption editor offers word-by-word animated captions, font/color/size/
position controls, stroke and shadow, background and inactive-word treatment,
per-line visibility, emoji, reusable style presets, and line-length controls.
Chopify will adapt the parts that fit its renderer and workflow:

- A preset gallery with distinct looks, not just accent colors.
- Full typography and layout controls, including line length and position.
- Word-level motion: pop, bounce, fade, and typewriter.
- Karaoke and active-word highlight modes.
- A background box, outline, and shadow.
- Automatic emphasis for numbers, money, superlatives, and power words.
- Optional automatic emoji from a local keyword map.
- Save any look as a reusable preset.
- A live preview over the source video while scrubbing or playing.

Not in this pass: translation and bilingual captions, speaker diarization,
intro/outro clips, AI reframe layouts for screencasts/gaming, and direct social
publishing. Those need separate designs and, in some cases, external services or
models.

## Architecture

### Caption style model

A caption style is one JSON object with these optional fields. `null` or a
missing key means "inherit from the preset":

| Field | Type | Range / values |
| --- | --- | --- |
| `font` | string | Installed family name, 1-80 chars, letters/digits/space/`_`/`.`/`-` |
| `font_size` | integer | 12-200, measured at 1080px output height |
| `color` | `#RRGGBB` | Main caption text |
| `highlight` | `#RRGGBB` | Active-word color |
| `emphasis_color` | `#RRGGBB` | Automatic emphasis color |
| `outline_color` | `#RRGGBB` | Stroke color |
| `uppercase` | boolean | Force uppercase captions |
| `words_per_cue` | integer | 1-8 words shown together |
| `position` | string | `bottom`, `center`, `top` |
| `margin` | integer | 0-700, measured at 1080px output height |
| `outline` | integer | 0-12, measured at 1080px output height |
| `shadow` | integer | 0-12, measured at 1080px output height |
| `background` | string | `none`, `box` |
| `background_color` | `#RRGGBB` | Box color |
| `background_opacity` | integer | 0-100 |
| `animation` | string | `none`, `pop`, `bounce`, `fade`, `typewriter` |
| `highlight_mode` | string | `word`, `karaoke`, `none` |
| `emoji` | boolean | Add one relevant emoji per cue |
| `emphasis` | boolean | Emphasize numbers/money/power words |

The same object shape is accepted in three places, with this precedence:

1. A named preset in `render_clips.CAPTION_STYLES`.
2. A brand preset selected on the project (existing `brand` option).
3. Per-project `caption` overrides saved by Studio.

Brand presets keep their existing fields (`font`, `color`, `highlight`,
`font_size`, `margin`, `logo`, `logo_width`) and gain the new fields with `null`
defaults. Existing brand JSON files remain valid. A brand preset still overrides
the named preset for any field it sets.

### Rendering

`render_clips.resolve_caption(style_name, brand=None, caption=None)` merges the
three layers into one concrete style. `build_ass` consumes that style and writes
the ASS file. The existing ffmpeg pipeline, preview scaling, logo overlay, and
split-screen paths are unchanged.

Rendering rules:

- `font_size` and `margin` are interpreted at 1080px height and scaled to the
  actual output height, matching the current brand behavior.
- `outline` and `shadow` scale the same way. `outline` becomes the ASS
  `Outline` field; `shadow` becomes `Shadow`.
- `background: box` sets `BorderStyle: 3`, uses `background_color` with an alpha
  derived from `background_opacity`, and uses `outline` as box padding.
- `position` maps to ASS alignment: bottom `2`, center `5`, top `8`, with
  `margin` applied to the relevant edge.
- `words_per_cue` groups the words shown in each event. The default remains 3.
- `uppercase` transforms displayed words, not the transcript or SRT source.
- `highlight_mode` controls the color of words in each event:
  - `word`: only the active word uses `highlight`.
  - `karaoke`: the active word and all earlier words in the cue use `highlight`.
  - `none`: every word uses `color`.
- `emphasis` marks numbers, percentages, currency, all-caps words longer than
  two characters, and a curated power-word list. At most two words per cue are
  marked. Unspoken emphasized words use `emphasis_color`; the active word always
  uses `highlight`.
- `emoji` attaches at most one emoji per cue to the first word that matches the
  local keyword map. The emoji is rendered with a `Segoe UI Emoji` font override
  when the caption font differs. If the platform lacks that font, libass falls
  back to an available emoji font; a missing glyph does not fail the render.
- `animation` affects the active word:
  - `pop`: scale the active word up and settle back.
  - `bounce`: squash and stretch the active word.
  - `fade`: fade the active word in.
  - `typewriter`: show only the words spoken so far in the cue.
  - `none`: no motion.
- Hidden words (`hidden: true` on a transcript word) are skipped by both ASS and
  SRT output. Studio does not expose a hide control in this pass; the field is
  preserved by transcript saves for future editing and for programmatic use.
  A per-word `emphasis: true` flag is also preserved and forces that word into
  the emphasis treatment.

`captions.write_srt` gains an optional `group_size` parameter. `render` passes
the resolved `words_per_cue` so SRT cues match the burnt captions. SRT keeps the
original casing, omits emoji, and skips hidden words.

### Presets

`CAPTION_STYLES` gains complete looks while keeping the existing names:

| Preset | Look |
| --- | --- |
| `default` | Arial Black, white/yellow, 3 words, bottom |
| `clean` | Arial, white/amber, soft outline and shadow, 3 words |
| `minimal` | Lowercase Arial, white, dark translucent box, 5 words |
| `bold` | Impact, white/orange, thick outline, 2 words, pop |
| `boxed` | Arial Black, white/yellow on a black box, 3 words |
| `karaoke` | Arial Black, white/yellow, 4 words, karaoke mode |
| `pop` | Impact, white/orange, 2 words, pop animation |
| `neon` | Arial, white/cyan with a cyan outline, 3 words, fade |
| `typewriter` | Courier New, white/green, 4 words, typewriter |
| `retro` | Georgia, cream/amber, thick shadow, 3 words |
| `hormozi` | Existing red-accent look, 2 words, pop |
| `mrbeast` | Existing green-accent look, 2 words, pop |
| `podcast` | Existing Arial look, 4 words, karaoke |

Preset values are hex colors in Python; conversion to ASS happens in
`build_ass`. Brand presets loaded through `branding.load_brand` keep returning
ASS colors for compatibility, and the style resolver accepts either form.

### Studio

The dashboard gets a third review tab, **Captions**, and a caption style form in
the settings rail.

- The source video gains a caption overlay. It is visible while the Captions tab
  is active and the playhead is inside a spoken cue.
- The overlay shows the current cue, groups words using `words_per_cue`, and
  updates as the video plays or seeks. It applies uppercase, font size, position,
  margin, colors, outline, shadow, background, emphasis, emoji, animation, and
  karaoke/highlight mode.
- The overlay is an approximation of the ASS render, not a pixel-perfect
  preview. A short note says so. The exported MP4 remains the source of truth.
- The caption form includes preset, words per line, position, animation,
  highlight mode, uppercase, background box/color/opacity, outline, shadow,
  font, font size, margin, text/highlight/emphasis colors, auto emphasis, and
  auto emoji.
- Changing a control updates the overlay immediately. **Save caption style**
  persists the full style to `project.json` as `options.caption`.
- **Preview 5s** seeks to the first selected candidate and plays it with the
  overlay active.
- **Save as preset** uses a name field beside the button, then stores the current
  style under `projects/brands/<name>.json` through the existing brand-preset
  endpoint. The preset becomes selectable in the brand dropdown and on future
  projects.
- The caption tab is keyboard reachable, controls have labels, and the overlay
  has `aria-hidden="true"` so screen readers do not read duplicate caption text.
- The existing job-busy rules disable caption controls while a render is
  running.

`GET /api/config` gains `caption_presets` so the browser can apply a preset
without hardcoding it. No new mutation route is needed; caption saves use the
existing `POST /api/projects/<id>/settings` route with `{"caption": {...}}`.

### CLI and API

New CLI flags, all optional:

- `--caption-json PATH` — load a full caption style object.
- `--words-per-cue N`
- `--uppercase` / `--no-uppercase`
- `--caption-position bottom|center|top`
- `--caption-animation none|pop|bounce|fade|typewriter`
- `--caption-background none|box`
- `--caption-outline N`
- `--caption-shadow N`
- `--emoji` / `--no-emoji`
- `--emphasis` / `--no-emphasis`
- `--caption-font FAMILY`
- `--caption-color #RRGGBB`
- `--caption-highlight #RRGGBB`
- `--caption-emphasis-color #RRGGBB`

`--style` continues to select the named preset. `--caption-json` loads a base
caption object first; any other caption flag overrides that object. Existing
projects keep their saved caption style unless a caption flag or
`--caption-json` is passed.

`POST /api/projects/<id>/settings` accepts a `caption` object and validates it
with `branding.validate_caption`. Invalid fields return HTTP 400 with the same
style of message as other settings errors.

### Files touched

- `render_clips.py`: style resolution, ASS generation, emoji/keyword maps,
  animation tags, SRT group size, CLI flags.
- `branding.py`: new caption fields, validation, `validate_caption`.
- `projects.py`: `caption` option default and validation, pass-through to render.
- `chopify.py`: caption CLI flags and project-update detection.
- `dashboard.py`: config response and settings pass-through (no new route).
- `assets/studio/index.html`, `app.js`, `style.css`: Captions tab, overlay,
  style form, preset save, preview controls.
- `captions.py`: hidden-word filtering and configurable SRT grouping.
- `tests/`: unit, HTTP, and real-ffmpeg coverage.
- `README.md`, `docs/WORKFLOWS.md`, `docs/AGENT-API.md`, `CHANGELOG.md`,
  `docs/product.json`, `llms.txt`, and the static landing page where feature
  claims are listed.

## Error handling

- Caption objects are validated before they reach ffmpeg. Unknown keys, bad
  colors, non-finite numbers, and out-of-range values raise `ValueError` and
  surface as HTTP 400 or a CLI error with the field name.
- A missing `--caption-json` file, invalid JSON, or a JSON value that is not an
  object fails before any project work starts.
- The Studio preview degrades gracefully: if the browser lacks a font or emoji
  glyph, it uses the browser fallback stack without breaking the overlay.
- A caption style change invalidates the render cache through the existing
  `options` digest. Old cached renders remain on disk but are not reused for the
  new style.
- No new network calls, services, or API keys are introduced.

## Testing

Python:

- `test_caption_style_resolution`: preset < brand < project override precedence.
- `test_ass_caption_layout`: alignment, outline, shadow, background box,
  uppercase, words per cue, and color conversion.
- `test_ass_caption_motion`: pop/bounce/fade/typewriter tags; karaoke vs word
  highlighting; emphasis color; emoji insertion.
- `test_srt_grouping_and_hidden_words`: configurable group size and hidden-word
  filtering.
- `test_caption_validation`: every field accepts valid values and rejects
  unknown keys, bad colors, booleans where integers are required, and out-of-range
  numbers.
- `test_project_caption_roundtrip`: `projects.validate_options` preserves a
  caption object and `run_project` passes it to the renderer.
- HTTP: `/api/config` exposes presets; settings save/read a caption object;
  invalid caption returns 400; busy projects reject caption edits.
- Real ffmpeg: one preview render with a boxed/pop caption style produces H.264
  + AAC video and an SRT with matching cue groups.

Manual:

- Start Studio with a synthetic project, open the Captions tab, scrub and play,
  change every control, save, reload, export a preview, and confirm the overlay
  and exported captions match.
- Check keyboard focus, disabled states during a job, and a narrow viewport.

## Rollout

The feature ships behind no flag: existing projects keep their current output
because the default caption style reproduces today's look. Documentation calls
out the preview approximation, the local emoji map, and the fields that are not
implemented (translation, diarization, publishing).
