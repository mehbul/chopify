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

## Images to PDF

1. Start `python dashboard.py` and select **Images → PDF** in the sidebar, or open
   `http://127.0.0.1:8765/images-to-pdf`.
2. Click **Add pictures** or drop files in the upload area. Images are added in the
   order supplied by the file picker; operating systems may order selections
   differently, so review the numbered list before exporting.
3. Drag cards or use their up/down buttons to reorder. Natural filename sorting,
   reverse order, duplicate and remove are available. Remove only takes the image
   out of the current document; saved PDFs and uploaded assets remain available.
4. Select a page and choose original, grayscale, black & white, document scan or
   sepia. Adjust brightness, contrast, saturation, sharpness, threshold, rotation,
   flips, auto contrast, invert and crop. **Apply to all pages** copies all image
   adjustments, including crop and rotation; **Reset image** restores defaults.
5. Open **PDF settings** for paper size, orientation, fit, margins, background,
   resolution, JPEG quality, title, filename and page numbers.
6. Click **Create PDF** and use **Download PDF** beneath the preview. Reopen older
   exports from the Recent PDFs rail to change them and generate a new PDF.

Crop bounds are percentages `[left, top, right, bottom]` of the original,
EXIF-oriented image, before rotation. At least 1% of each dimension must remain.
Rotation is clockwise in 90-degree increments. Threshold affects black & white
and document scan only. Scan enhances contrast and sharpness before thresholding;
it does not detect page edges or correct perspective automatically.

**Fit whole image** keeps all pixels with possible whitespace. **Fill page**
center-crops to fill the area inside the margins. **Stretch** changes the image's
aspect ratio. Auto orientation chooses portrait/landscape per processed image.
Match-image paper sizes derive physical dimensions from image pixels and the DPI
setting, then add margins and page-number space. The page number footer reserves
18 points. Page rasters are capped at a 3508px long edge to bound memory use;
requested DPI can be reduced for very large/custom pages. Preview is capped at
96 DPI and uses the selected JPEG quality.

Accepted uploads: JPEG, PNG, WebP, BMP and TIFF, up to 20 MB / 24 megapixels per
image and 100 pages per PDF. First frame only for multi-frame files. Photos are
EXIF-oriented and re-encoded without EXIF/GPS/text metadata. Transparent areas
become white. PDF pages are raster images, including page numbers; no OCR,
searchable text, HEIC import or automatic perspective correction is included.

Images and PDF exports stay under the ignored `projects/pdf/` directory:

```text
projects/pdf/
  assets/<id>.png          # normalized image
  assets/<id>.thumb.png    # page-list thumbnail
  assets/<id>.json         # filename and dimensions
  exports/<id>.pdf         # generated PDF
  exports/<id>.json        # saved order, per-image settings and PDF options
```

The browser saves a draft in local storage, using asset IDs rather than image
bytes. A private browser session or clearing site storage can remove that draft;
saved server-side exports remain available. These local files are not part of
the public documentation site or Git commits.
