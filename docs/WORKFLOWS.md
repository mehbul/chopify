# Chopify workflow guide

Chopify processes local recordings or supported video URLs into short, captioned
clips. All transcription, scoring, editing, and rendering run on your computer.

## Review a video in Studio

1. Run `python dashboard.py` and open the printed `127.0.0.1` address.
2. Enter an absolute local video path or URL. Add one source per line for a batch.
3. Choose aspect ratio, speech model, and caption style. Click **Find clips**.
4. Watch stage progress in the project list and open **Job log** for details.
5. Click **Watch** beside a candidate to play that range in the source video.
6. Change titles, start/end times, and selected checkboxes. Save clip edits.
7. Save settings, then **Preview selected** to render small drafts.
8. **Export selected** creates MP4 + SRT + PNG poster + metadata JSON.

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
| Keyword/semantic query, embedding model, scoring settings | Candidates and renders |
| Style, brand, logo, layout, tightening, normalization | Renders |
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
that exported clip, are clamped to its bounds, and group up to eight words per
cue. `--tighten` remaps both burnt captions and SRT to the shortened timeline.
Changing caption text does not change spoken audio. Translation and dubbing are
not included.

## Create a reusable brand preset

Use Studio's **Create a brand preset**, then select it and **Save settings**.
Presets live under `projects/brands/` and are excluded from Git. CLI presets are
ordinary JSON files; see [examples/brand.json](../examples/brand.json).

| Field | Accepted value |
| --- | --- |
| `font` | Installed font family name, up to 80 characters; letters, digits, spaces, `_`, `.`, `-` |
| `color`, `highlight` | `#RRGGBB` colors |
| `font_size` | Integer 12–200, measured at 1080px output height |
| `margin` | Integer 0–700, bottom caption margin at 1080px height |
| `logo` | Existing PNG/JPEG/WebP path, or `null`; relative paths resolve beside the JSON |
| `logo_width` | Integer 20–600, measured at 1920px output width |

Sizes scale to the export resolution, including drafts. The logo is placed at
the upper right. Brand caption settings override the selected caption style.
Colors, font names, and ranges are validated before rendering.

```powershell
python chopify.py recording.mp4 --brand examples/brand.json --aspect 9:16
```

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
    segments.json      # editable clip candidates
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
