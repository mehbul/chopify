# Chopify gaming facecam layout design

## Goal

Add a 9:16 **gaming layout** that stacks the streamer's facecam above the
gameplay, with burned captions sitting at the seam between the two panels —
matching the common Shorts/Reels format shown in the user's reference image.
The layout must work for both batch and live clips, from CLI and Studio.

The source is a single 16:9 stream that already contains the facecam as an
overlay (typically bottom-left). Chopify splits that one frame into two panels;
it does not need two input sources.

## Decisions made with the user

| Decision | Choice |
| --- | --- |
| Facecam location | Both: explicit normalized box wins; auto-detect when omitted |
| Gameplay framing | Crop to fill the lower panel, no black bars |
| Caption position | At the seam between facecam and gameplay |
| Availability | Batch + live, CLI flags + Studio setting |

## Non-goals

- Two independent input sources (separate cam and gameplay files).
- Diarization, multiple facecams, or tracking the cam position over time.
- Changing the existing `single`, `split`, or `contain` behavior.
- Automatic removal of the facecam overlay from the gameplay panel (the overlay
  may still appear small inside gameplay; the stacked panels remain legible).

## Architecture

A new `layout` value, `stacked`, is handled inside `render_clips.render()`. It
reuses the same `split` + `vstack` filter-graph pattern already used by
`layout=split`, with a facecam crop and a gameplay cover-crop. No new module,
no new dependency, no extra encode pass.

### Options

Two settings are added to `projects.DEFAULT_OPTIONS` and validated in
`projects.validate_options()`:

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `facecam_box` | list of 4 floats or `null` | `null` | Normalized `x, y, w, h` rectangle in the source frame, each 0–1 |
| `facecam_height` | float | `0.34` | Fraction of output height used by the facecam panel, 0.20–0.60 |

`layout` gains the enum value `stacked`. Like `split`, it requires
`aspect == "9:16"`. `fit=contain` is ignored for `stacked` (gameplay always
covers its panel); this is documented, not an error.

### Facecam box resolution

`resolve_facecam_box(source, W, H, workdir, box, detector=None)` returns a
clamped normalized `(x, y, w, h)`:

1. If `box` is supplied, validate exactly four finite numbers in 0–1. Clamp in
   this order: `x, y` to `[0, 0.95]`; `w` to `[0.05, 1 - x]`; `h` to
   `[0.05, 1 - y]`. Return the clamped tuple.
2. Otherwise sample one frame with ffmpeg at 1 fps, run the existing YuNet
   detector (`render_clips._make_detector`) on it. If a face is found, expand
   the detected box by 25% on each side (to include shoulders/background),
   convert to normalized coords, and clamp.
3. If no face is found or detection raises, fall back to the default box
   `(0.0, 0.62, 0.26, 0.30)` — bottom-left, matching typical streamer overlays —
   and return a warning string that the caller logs. Detection never fails a
   render.

The detector and its ONNX model are already used by speaker tracking, so there
is no new download.

### Render graph

Inside `render()`, after computing `tw, th` and before building `vf`:

- `facecam_h = even(round(th * facecam_height))`, `game_h = th - facecam_h`
  (both even).
- Convert the normalized facecam box to source pixels, then crop and scale it
  to `tw × facecam_h`.
- Build the gameplay crop with the existing `crop_for(W, H, tw, game_h)` and
  scale it to `tw × game_h`.
- Graph:

```
[0:v]split=2[cam][game];
[cam]crop=fcw:fch:fcx:fcy,scale=TW:FH[top];
[game]crop=gcw:gch:gcx:gcy,scale=TW:GH[bottom];
[top][bottom]vstack=inputs=2[stack];
[stack]subtitles=_caption.ass[v]
```

- `mode = "gaming facecam + gameplay (stacked)"`.
- `build_ass()` is called with `margin_v = th - facecam_h - seam_offset` where
  `seam_offset = 12`. With ASS bottom alignment, the caption's bottom edge then
  lands 12px below the seam. Any per-caption
  `margin` override is neutralized for this layout by passing a copy of the
  caption dict with `margin = None`.
- Brand logo overlay, when present, continues to work: the graph already ends
  at `[v]` and the existing logo branch overlays onto it.

### Failure handling

- Invalid `facecam_box` values are clamped, never rejected mid-render.
- Detection failure falls back to the default box and logs.
- A source smaller than the requested crop is handled by `crop_for` clamping
  and even-dimension rounding, as in existing layouts.

## Interfaces

- `render_clips.resolve_facecam_box(source, W, H, workdir, box, detector=None)`
  → `((x, y, w, h), warning_or_none)` in normalized coords.
- `render(..., layout="stacked", facecam_box=None, facecam_height=0.34, ...)`
  produces the same artifacts as other layouts (MP4, SRT, poster, metadata).
- `live_clips.LiveClipWriter.write()` forwards `facecam_box` and
  `facecam_height` from the session options.
- CLI: `--layout stacked`, `--facecam-box X Y W H` (4 floats),
  `--facecam-height N` (0.20–0.60).
- Studio: `stacked` added to the layout selector; two fields on the settings
  form for facecam box (comma/space separated) and height.

## Testing

- Pure tests: box clamping (out-of-range, too-small, overflow), auto-detect
  expansion, fallback box, even-dimension rounding, graph contains `vstack`
  and correct crop/scale values, caption seam margin.
- Real ffmpeg integration test (skipped without ffmpeg): synthetic 640×360
  source with a colored corner block, render `9:16` `stacked`, assert output
  `1080×1920`, top panel carries the corner color, bottom panel does not.
- CLI/Studio option tests: defaults, validation, `stacked` requires `9:16`.
- Full suite must stay green.

## Docs

- `docs/CLI.md`: new flags and layout value.
- `docs/LIVE.md` and `README.md`: gaming layout example and link.
- `CHANGELOG.md` `[Unreleased] → Added`.
- Studio layout dropdown options list wherever documented.
