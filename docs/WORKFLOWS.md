# Chopify workflow guide

Chopify processes local recordings or supported video URLs into short, captioned
clips. All transcription, scoring, editing, and rendering run on your computer.

## Review a video in Studio

1. Run `python dashboard.py` and open the printed `127.0.0.1` address.
2. Enter an absolute local video path or URL. Add one source per line for a batch.
3. Choose aspect ratio, speech model, and starting caption style. In
   **Transcription & scoring**, set the minimum score (default 8.0) and an
   optional preferred length. Click **Find clips**.
4. Watch stage progress in the project list and open **Job log** for details.
5. Read the selection summary above the candidates. Use **Previous** / **Next**
   to page through 25 cards at a time, and **Select all** / **Select none** to
   change the export set quickly. Click **Watch** beside a candidate to play that
   range in the source video.
6. Change titles, start/end times, and selected checkboxes. Save clip edits.
7. Open **Captions**, choose a look, tune it, and **Save caption style**. Use
   **Preview 5s** to check motion over a selected range.
8. Save settings, then **Preview selected** to render small drafts.
9. **Export selected** creates MP4 + SRT + PNG poster + metadata JSON.

Jobs execute serially to avoid simultaneous model/encoder contention. You can add
more videos while one is processing. Each project appears in the left rail.
Current jobs cannot be edited. After a server restart, interrupted jobs can be
queued again explicitly with **Find / refresh clips**, **Preview**, or **Export**.

## Local input and batch files

Supported local extensions: `.mp4`, `.mov`, `.mkv`, `.webm`, `.m4v`, `.avi`.
Local footage is read in place, so keep the source available until you finish.
The browser accepts paths, not file uploads; the path must exist on the server's
computer. Use absolute paths in Studio and quote paths containing spaces in CLI.

For the CLI, create a UTF-8 JSON file (Windows paths need doubled backslashes):

```json
["C:\\Videos\\episode-1.mp4", "C:\\Videos\\episode-2.mov"]
```

```powershell
python chopify.py --batch batch.json --projects-dir projects --aspect 9:16
```

Each invocation creates separate projects, even for repeated sources. To retry
one without starting a new project, use its saved directory:

```powershell
python chopify.py --project projects/PROJECT_ID --action all
```

Relative paths resolve from the current working directory. Batch output stays
in each project folder; `--out` applies to single-project runs.

## Resume and stage invalidation

Each `project.json` records the source, options, status, stage, successful stage
signatures, and completed render paths. Writes use atomic replacement. Resume is
enabled by default; `--no-resume` redoes the chosen action.

| Change | Recomputed work |
| --- | --- |
| Source path/URL, local file size or modification timestamp | Source resolution, transcription, candidates, renders |
| Speech model or compute device | Transcription and downstream stages |
| Caption words/times | Candidate selection on next prepare; rendered captions on export |
| Keyword/semantic query, embedding model, length range, preferred length | Discovery and selection |
| Minimum score, maximum clip cap | Selection only; discovery is reused |
| Style, brand, logo, layout, caption look, tightening, normalization | Renders |
| Selected clip subset | Only requested clips are rendered |

Cache identity uses path, size, and nanosecond modification time, not a full hash
of large videos. Replacing files without changing those values is not detected.
Rendered variants have distinct names so styles do not overwrite earlier cached
variants. Missing MP4/sidecar files cause that clip to rerender. A failure retains
successful clips and exits nonzero. Use only one writer per project directory.

Use `--action prepare` to discover candidates, `--action render` to preserve your
manual clip edits, and `--action all` for the whole pipeline. If a selection input
has changed, preparing again can replace manually edited candidate ranges.

## Edit captions and export SRT

Studio's **Transcript** tab edits individual word text and start/end times in
seconds. Words must be nonempty, have finite times within the video's duration,
and remain chronological without overlaps. The table is paginated in 100-word
pages. Click a word's number to seek the source video to it.

Save captions before export. Saving rebuilds sentence text from edited words.
Candidate ranges stay available until you request new selection.

Every draft and final clip writes `<clip>.srt`. Subtitle times begin at zero for
that exported clip, are clamped to its bounds, and group cues with the caption
style's words-per-cue value (1–8; the standalone SRT writer defaults to eight).
`--tighten` remaps both burnt captions and SRT to the shortened timeline.
Changing caption text does not change spoken audio. Translation and dubbing are
not included.

Transcript words may also carry `hidden: true` or `emphasis: true` in
`transcript.json`. Hidden words are skipped by burnt captions, the Studio overlay,
and SRT. Forced emphasis is colored even when automatic emphasis is off. Studio's
word table edits text and times; the flags survive a save.

## Style captions in Caption Studio

Open **Captions** after preparing a project. Pick a preset in the gallery or the
**Starting preset** menu, then adjust any field. The source player shows the look
at the current timestamp. **Preview 5s** jumps to the first selected candidate (or
the first candidate) and plays five seconds.

| Preset | Look |
| --- | --- |
| `default` | Arial Black, white with a yellow active word, 3 words per cue. |
| `clean` | Arial with a thin outline and amber active word, 3 words. |
| `minimal` | Arial in a translucent dark box, 5 words. |
| `bold` | Uppercase Impact with pop motion and orange active word, 2 words. |
| `boxed` | Uppercase Arial Black in an 80% black box, 3 words. |
| `karaoke` | Active and earlier words are highlighted, 4 words. |
| `pop` | Uppercase Impact with pop motion and orange active word, 2 words. |
| `neon` | Uppercase Arial with cyan outline and highlight plus fade motion. |
| `typewriter` | Courier New that reveals one word at a time, 4 words. |
| `retro` | Cream Georgia with a deep shadow, 3 words. |
| `hormozi` | Arial Black with a red active word and pop motion, 2 words. |
| `mrbeast` | Arial Black with a green active word and pop motion, 2 words. |
| `podcast` | Arial with karaoke highlighting, 4 words. |

| Field | Accepted value |
| --- | --- |
| `font` | Installed font family, up to 80 characters; letters, digits, spaces, `_`, `.`, `-` |
| `font_size` | Integer 12–200 at 1080px output height; blank inherits the aspect default |
| `margin` | Integer 0–700 at 1080px output height; blank inherits the aspect default |
| `color`, `highlight`, `emphasis_color`, `outline_color` | `#RRGGBB` |
| `uppercase` | `true` or `false` |
| `words_per_cue` | Integer 1–8 |
| `position` | `bottom`, `center`, or `top` |
| `outline`, `shadow` | Integer 0–12 at 1080px output height |
| `background` | `none` or `box` |
| `background_color` | `#RRGGBB` |
| `background_opacity` | Integer 0–100 |
| `animation` | `none`, `pop`, `bounce`, `fade`, or `typewriter` |
| `highlight_mode` | `word` (active word only), `karaoke` (active and earlier words), or `none` |
| `emoji` | `true` or `false` |
| `emphasis` | `true` or `false` |

Aspect defaults are 72px font / 95px margin at 16:9, 96/300 at 9:16, and 82/120
at 1:1. Values scale to drafts and final output.

Style resolution is a three-layer merge: named preset, then brand preset, then
the project's `options.caption` override. Any concrete value in a later layer
wins; blank or `null` values inherit (brand presets keep historical defaults for
their original fields; see below). **Save caption style** stores the whole look
as the project override and records the starting preset in `options.style`.
**Save as preset** writes a validated brand JSON file under `projects/brands/`;
select it under **Brand preset** and save settings to use it on another project.

The Studio overlay is a close DOM approximation of libass output. Player size,
font availability, stroke, spacing, and box padding can differ slightly; the
exported MP4 is the source of truth.

Automatic emphasis colors at most two words per cue: numbers, money amounts,
ALL-CAPS words longer than two letters, and a small English power-word list.
Forced per-word emphasis flags count first. Emoji mode adds at most one emoji per
cue from deterministic English keyword rules. Emoji glyphs require an installed
system emoji font; otherwise they can render monochrome or as a missing glyph.

CLI equivalents:

```powershell
python chopify.py recording.mp4 --style bold --words-per-cue 2 --caption-position top
python chopify.py recording.mp4 --style pop --emoji --emphasis --caption-animation pop
python chopify.py recording.mp4 --caption-json my-caption-style.json
```

`--style` chooses the base preset. Shared field flags override it. `--caption-json`
accepts a full or partial validated object; explicit flags win over JSON fields.
`--no-uppercase`, `--no-emoji`, and `--no-emphasis` force those options off.
`--caption-json` is the only CLI way to set `font_size`, `margin`,
`highlight_mode`, `background_color`, `background_opacity`, or `outline_color`.

## Create a reusable brand preset

Use Studio's **Create a brand preset**, then select it and **Save settings**.
Presets live under `projects/brands/` and are excluded from Git. CLI presets are
ordinary JSON files; see [examples/brand.json](../examples/brand.json).

Brand presets accept every caption field in the table above, plus `logo` and
`logo_width`. The original fields (`font`, `color`, `highlight`, `font_size`,
`margin`) keep their historical defaults when omitted, so existing brand files
render unchanged. Omitted new fields inherit the selected preset. Set any field
to `null` to inherit it explicitly.

| Field | Accepted value |
| --- | --- |
| `logo` | Existing PNG/JPEG/WebP path, or `null`; relative paths resolve beside the JSON |
| `logo_width` | Integer 20–600, measured at 1920px output width |

```json
{
  "font": "Impact",
  "color": "#FFFFFF",
  "highlight": "#FF7A00",
  "uppercase": true,
  "words_per_cue": 2,
  "position": "top",
  "animation": "pop",
  "outline": 6,
  "shadow": 2,
  "logo": "logo.png",
  "logo_width": 180
}
```

Sizes scale to the export resolution, including drafts. The logo is placed at
the upper right. Brand caption settings override the selected caption preset;
the project's caption override wins over both. Colors, font names, and ranges are
validated before rendering.

```powershell
python chopify.py recording.mp4 --brand examples/brand.json --aspect 9:16
```

## How clip discovery and scoring work

Discovery is exhaustive rather than greedy. Every sentence is treated as a
possible start, and the engine evaluates a duration ladder of 15s, 30s, 45s,
60s, 90s, 2m, 3m, and 5m (plus your preferred length). Endpoints snap to a
complete sentence, then refine by up to two sentences around the best result.
Each start keeps at most two records: its highest-quality endpoint and its
best endpoint near your preferred length. Candidates may overlap; deduplication
removes a lower-scoring window when it overlaps more than 55% of the shorter
clip, and a tighter clip can replace a loose one when the scores are within 0.25.

Scores are absolute 0–10 values, not a ranking normalized to the current video.
The rubric weighs hook, payoff, self-containment, story arc, energy,
quotability, emotion, controversy, and humour, then subtracts penalties for
padding and weak endings. The raw weighted score is calibrated with:

```text
text_score = 10 * clamp(1.35 * raw_score - 0.08, 0, 1)
```

The score is an editing suggestion, not a prediction or guarantee of virality.

- The default minimum is **8.0 inclusive**. A clip at exactly 8.0 passes and is
  displayed as **8.0+**.
- Every passing clip is returned. `--max-clips` is an optional safety cap, not a
  default limit.
- `--preferred-len` is a soft prior. The engine may choose another length when
  that endpoint scores better.
- The default range is 15–300 seconds. Widen `--min-len` / `--max-len` for
  longer-form material.
- If nothing passes, the result is an empty clip list plus the real score range.
  Chopify never adds a fallback clip.

### Audio evidence and speech-sparse sources

Audio evidence runs locally. A one-time ffmpeg decode builds a 1 Hz timeline
(loudness, peaks, spectral flux, zero-crossing rate, high-band ratio, and burst
flags) and caches it as `audio_features.npz`. Later prepares reuse that cache;
only candidate ranges are scored against it.

The source profile is internal (`talk`, `mixed`, or `gameplay/action`). It
adjusts the evidence weight, the duration tie-break band, and the evidence floor.
It never changes the user-facing 8.0 default or the minimum you choose.

- Speech-sparse candidates use a 7.0 text floor and at least 0.75 evidence
  weight, so gameplay and streams with little commentary can pass on strong
  audio events.
- A wordless source uses audio peaks as event seeds and the 7.0 text floor.
  Varying tracks without a sharp peak fall back to a grid of action seeds.
  Hooks identify `event at 83s` or `action at 83s`; they never claim a semantic
  event that was not detected.
- A flat source with no words and no media evidence stops with
  `No speech and no media evidence; this source cannot be scored.`
- Missing or unreadable sources still score from text with neutral evidence.
- Visual evidence is Phase 2B. Phase 2A is audio-only.

Candidates and passing segments carry an `evidence` object. `selection.json`
also reports `evidence_available` and the internal profile.

Interactive CLI runs ask for the score and preferred length once:

```text
Minimum clip score (0-10, Enter = 8.0):
Preferred clip length (e.g. 45s, 2m; Enter = AI chooses):
```

`--min-score` and `--preferred-len` skip their questions. `--no-input` skips
both. Interactive `--action all` prepares first, prints the selection summary,
and asks before rendering more than ten clips. Answering no leaves the project
in review; render later with `--action render`. Batch mode never prompts.

```powershell
python chopify.py recording.mp4 --action prepare --no-input
python chopify.py --project work --action render --clips 1,3 --preview --no-input
```

Preparing writes three artifacts:

| File | Role |
| --- | --- |
| `candidates.json` | Every deduplicated candidate, independent of the threshold. |
| `selection.json` | The threshold, score range, counts, durations, and profile metadata. |
| `segments.json` | The passing clips, editable in Studio and consumed by the renderer. |

Changing only the minimum score re-filters `candidates.json` without rescanning
the transcript. Changing discovery inputs (length range, preferred length,
search settings) runs discovery again. In Studio, the summary shows the passing
count, score range, and duration range; candidates are paginated 25 per page and
all passing clips start selected.

## Find clips by meaning

Install and start Ollama, then download an embedding model:

```powershell
ollama pull nomic-embed-text
python chopify.py recording.mp4 --semantic "advice about handling rejection"
```

Choose **Search by meaning** in Studio, enter a topic, save settings, and refresh
candidates. Sentences are embedded in batches through local Ollama's `/api/embed`,
ranked by cosine similarity, and grown into sentence-ended clip windows.
`embeddings.json` caches document vectors by model and sentence text; query
embeddings are recalculated for each search.

Similarity is relative to the input video; a ranked result is not evidence that
the topic is discussed. Missing models fail with setup instructions, without a
cloud fallback or silent keyword substitution. Keyword and semantic search are
mutually exclusive. A chat model for `--llm` is separate from embedding search.

## Frame a two-person podcast

```powershell
python chopify.py podcast.mp4 --aspect 9:16 --layout split --centers 0.25 0.75
```

Centers are horizontal fractions of the source width. The first controls the top
panel, the second the bottom panel. Both range from 0 to 1; crops are clamped
inside the source frame. For a host at 30% and guest at 70%, try
`--centers 0.30 0.70`, preview, then adjust. Each panel is vertically centered.

Split-screen is for fixed-camera footage. It does not identify speakers, follow
two moving faces, or change panels at speaker turns. Single-frame mode retains
automatic face tracking for horizontal crops.

## Files on disk

```text
projects/
  brands/channel.json
  <id>/
    project.json       # source, settings, stage status, cache records
    transcript.json    # word timestamps, sentences, source path
    candidates.json    # every deduplicated candidate before thresholding
    selection.json     # threshold, score range, counts, durations
    segments.json      # editable clip candidates (the render contract)
    embeddings.json    # optional semantic-search cache
    job.log            # dashboard job output
    source.mp4         # downloaded footage; local files stay in place
    previews/*.mp4     # drafts, each with .srt
    clips/*.mp4        # final exports, .srt, .png, .meta.json
```

The default single-video CLI directory is `work/`; latest exports are also copied
to `clips/` (or `--out`). Filenames include a clip number and render signature,
followed by the title. Studio shows saved variants for comparison. Automatic
social uploads and scheduling are not included.
