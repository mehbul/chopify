# Chopify adaptive clip discovery design

## Goal

Make Chopify find every viable short-form clip in a source of any length, with
one intuitive score threshold and an AI-chosen clip length, while keeping the
pipeline local and free. The same engine must work for a podcast, a movie, a
gameplay or PC-game VOD, and a multi-hour stream; content type changes the
evidence weights, never the interface or the default threshold.

The current engine seeds on high-scoring single sentences, grows one window, and
marks every sentence it touched as taken. That blocks better overlapping windows,
caps output at ten clips, and uses a score scale whose practical ceiling is about
4.9. This design replaces the selector, adds local audio/visual evidence for
reaction-heavy creators such as MrBeast and Speed, and removes the fixed duration
ceiling from ingest, discovery, and rendering. SupoClip and AutoClip validate the
overlapping-window, per-window-score, dedupe, and rerank pattern; this design
removes their fixed 5-20 clip caps and adds evidence for visual, gameplay-led
footage.

"Any length" means 10 minutes, 10 hours, 48 hours, or more. It does not mean a
literal 1,000-hour benchmark. No stage may assume the whole source fits in one
download, one transcription call, or one in-memory candidate list.

## What changes for the user

- A normal interactive run asks for a minimum score and an optional preferred
  length. Enter accepts `8.0` and "AI chooses".
- The selector scans every sentence as a possible start and every natural
  endpoint on a duration ladder. The clip chooses its own length.
- Every distinct clip at or above the threshold is returned. There is no default
  ten-clip cap. `--max-clips` remains an optional safety cap.
- Scores are calibrated so strong clips reach 8-10. The default threshold is
  `8.0` for every source type: podcast, movie, interview, gameplay, PC game, or
  long-form stream. Content type does not change the threshold.
- The comparison is inclusive: a clip scoring exactly `8.0` passes. The UI and
  CLI label the threshold `8.0+`.
- Candidate ranges get a local audio/visual evidence pass, so screams,
  reactions, stunts, fast cuts, gunshots, explosions, boss fights, clutches,
  victory screens, and facecam reactions can outrank flat narration.
- Gameplay and PC-game VODs are first-class sources. Speech-sparse footage uses
  action arcs, audio event bursts, motion spikes, scene/UI changes, and facecam
  reactions instead of pretending a transcript is the only signal.
- Sources of two hours or more use audio-first ingest, chunked resumable
  transcription, and section-only video downloads. Short sources keep the
  current path.
- If nothing clears the threshold, Chopify reports the actual score range and
  suggests a lower threshold. It does not silently render one weak fallback clip.

## Constraints and non-goals

- Local only: no paid APIs, cloud uploads, hosted services, or API keys.
- Optional Ollama remains an enhancement. Deterministic scoring is the default.
- Existing brand JSON, caption presets, caption styling, and the default 16:9
  render appearance stay unchanged.
- Existing `segments.json` consumers keep working. New fields are additive.
- No automatic social publishing, speaker diarization, translation, or cloud
  collaboration.
- No user-facing "MrBeast mode", "game mode", or "long video mode" toggle. Source
  duration, speech density, motion, cuts, audio bursts, and facecam activity
  select the path, profile, and weights automatically.
- No OCR or game-specific event classifier is required in this pass. Gameplay
  events are detected from event arcs, audio bursts, visual spikes, scene/UI
  changes, facecam reactions, and commentary cues. An optional local OCR adapter
  can be added later behind the same evidence interface.
- `numpy` is used for audio features. It is already a transitive dependency of
  `opencv-python`; no new package is added.

## Architecture

The pipeline becomes:

```text
probe source
  -> short path: download video (URL) or use local file
  -> long path: download audio (URL) or use local file
  -> transcribe (one pass or chunked/resumable)
  -> audio timeline and sparse visual baseline
  -> content profile (talk / mixed / gameplay-action) and source energy
  -> scan transcript in blocks, every sentence as a start
  -> text score all start-best windows
  -> audio shortlist from the timeline
  -> visual evidence on padded candidate ranges
  -> boundary refinement
  -> final event-arc and evidence scoring
  -> calibrate, dedupe, threshold, rank
  -> review in CLI or Studio
  -> render from the full source or from downloaded sections
```

The selector writes two internal discovery artifacts, one diagnostic summary,
and one public artifact:

- `candidates.json`: up to two endpoints per start, including scores below the
  threshold. It is threshold-independent, so score-range reporting and
  re-thresholding are exact without rescanning the transcript.
- `evidence/`: cached audio and visual feature files keyed by source identity
  and time range.
- `selection.json`: discovery statistics, the detected content profile, and the
  score/duration ranges for the CLI and Studio. It is diagnostic, not a
  rendering contract.
- `segments.json`: the public, editable list of clips that passed the threshold.

## Selection engine

### Sentence timeline

`sentenceize()` stays the transcript splitter. It splits on terminal punctuation
or a pause longer than 1.2 seconds and keeps the original word objects.

Per sentence, the selector precomputes:

- start, end, duration, word count, words per second
- terminal punctuation
- filler count and filler ratio
- meta/outro phrases (`subscribe`, `like and comment`, `welcome back`,
  `thanks for watching`, `sponsor`, `intro`)
- question, number, second-person, curiosity, stakes, superlative, negation
- emotion, controversy, humour, and insight cue counts
- gameplay commentary cues (`clutch`, `ace`, `kill`, `win`, `GG`, `no way`,
  `let's go`, `boss`, `speedrun`, `world record`, `one shot`, `comeback`)
- energy marks (exclamation, all-caps, repeated letters, intensity words)
- quotability pattern (short imperative, contrast, rule of three, number+claim)
- anaphoric opening (`this`, `that`, `it`, `he`, `she`, `they` without a name)
- reference to earlier content (`as I said`, `like I mentioned`, `earlier`,
  `last time`, `previous video`)
- repeated bigrams and false-start markers

Window features are computed from cumulative sums plus the first two and last two
sentences. Range maximum quotability uses a sparse table over sentence scores, so
window scoring stays O(1) per window.

### Seeds

Every sentence start is a seed. This is the core change: no sentence is marked
"taken" before all windows are considered.

Two optional seed families are added in the creator-evidence phase:

- **Audio peaks and events.** Local maxima in the per-second RMS timeline above
  the source's 90th percentile or the local 60-second rolling baseline,
  separated by at least 10 seconds. This catches screams, gunshots, explosions,
  impacts, victory stingers, and crowd reactions. A peak or event seed starts
  2 seconds before the peak and snaps to a sentence start when one is within
  3 seconds.
- **Visual action.** Scene-change, motion-spike, and UI-change peaks from source
  sampling, used when speech is sparse or absent. This catches kills, clutches,
  boss phases, stunts, item drops, round transitions, and victory/defeat screens.
  A visual seed snaps to a nearby sentence start when possible.

Visual seeds need a local video or a short-source download. Long URL sources use
audio peaks plus commentary until the evidence pass downloads low-resolution
sections for the surviving candidates (see "Visual analysis").

Seeds within 2 seconds of each other collapse to one, preferring the sentence
start. Seeds are de-duplicated by time before window evaluation.

When a source has no sentences, or a region has no sentence starts, the duration
ladder is evaluated on time targets from the audio/visual seed. Endpoints snap to
the nearest loudness trough, motion trough, scene/UI cut, or event resolution
instead of a sentence end. Sentence-completeness penalties do not apply to a
window with no words. This is the path that makes no-commentary gameplay work.

### Duration ladder and endpoint choice

The ladder is:

```text
15s, 30s, 45s, 60s, 90s, 120s, 180s, 300s
```

The user's preferred length is added as an extra ladder value. Values outside
`[min_len, max_len]` are clipped with a printed warning. Defaults become
`min_len=15` and `max_len=300`.

For each seed and target duration:

1. For a sentence seed, find the first sentence end at or after `start + target`
   with binary search. For a media-only seed, use `start + target` directly.
2. Consider that endpoint and its two neighbours on each side.
3. Keep endpoints inside `[min_len, max_len]` and inside the source.
4. Refine by up to two candidate boundaries in each direction and keep the best
   endpoint for that target. For sentence seeds those are sentence ends; for
   media-only seeds they are sampled times snapped to audio/visual boundaries.
5. Keep up to two endpoints for the seed:
   - `quality_best`: the highest-quality endpoint.
   - `preferred_best`: when it differs, the endpoint closest to the preferred
     length, or to the source-appropriate band when no preference is given,
     among endpoints within `0.15` of `quality_best`.
   - Both go into `candidates.json`. The final segment list keeps the better
     passing one after dedupe, so a passing endpoint is never lost to a failing
     near-tie.

This is the soft duration rule. A preferred length breaks near-ties; a clearly
stronger clip at another length wins.

The source-appropriate band is a tie-breaker only:

- gameplay/action source: 15-60 seconds
- mixed source: 30-120 seconds
- talk-heavy source: 45-180 seconds

The band is derived from the content profile described below. The profile is
computed from a cheap audio/visual baseline before window selection. Phase 1,
before the evidence pass exists, uses the mixed band; phase 2 replaces it with
the measured profile. The band never hard-rejects a length.

Length is a soft prior, not a weighted rubric criterion. It influences endpoint
choice and dedupe ties; it never adds points to the score. A preferred length
therefore nudges the result without turning every clip into the same duration.

### Text scoring rubric

Each criterion is scored 0-1 for the whole window. Weights sum to 1.0.

| Criterion | Weight | What raises the score |
| --- | --- | --- |
| Hook | 0.18 | Question, number, stake, second-person address, curiosity phrase, superlative, negation, unusual claim; dense speech in the first 3 seconds; no greeting or meta opening |
| Payoff | 0.16 | Resolution or reveal in the last 40%, strong terminal sentence, hook/payoff word overlap, no trailing filler |
| Self-containment | 0.12 | Clean sentence boundaries, no anaphoric opening, no reference to earlier content, complete setup |
| Arc | 0.12 | At least two sentences, tension before resolution, terminal ending |
| Energy | 0.11 | Words per second relative to the source median, exclamation and caps, intensity words, laughter, fast onset, gameplay commentary cues |
| Quotability | 0.10 | Short punchy sentence, imperative, contrast, rule of three, number plus claim, memorable closing line |
| Emotion | 0.08 | Emotion lexicon, first-person stakes, vulnerability, anger, fear, excitement, gratitude, reaction phrases (`no way`, `let's go`) |
| Controversy | 0.07 | Disagreement, absolutes, `everyone`/`nobody`, taboo, challenge, mild insult |
| Humour | 0.06 | Laughter markers, joke structure, absurdity, punchline |

Penalties are subtracted from the weighted sum before calibration:

| Penalty | Size |
| --- | --- |
| Filler density | up to 0.08 |
| Meta/outro phrase in first or last sentence | 0.15 |
| Repeated bigrams or false starts | up to 0.05 |
| Long internal silence (over 2.5 seconds) | up to 0.08 |
| Mid-sentence boundary | 0.20 |
| Long, low-density window | up to 0.10 |
| Empty or non-speech window with no evidence | 0.20 |

`text_raw` is the weighted sum minus penalties, clamped to 0-1.

### Calibration

The absolute calibration is:

```text
text_score = 10 * clamp(1.35 * text_raw - 0.08, 0, 1)
```

Anchors:

| `text_raw` | `text_score` |
| --- | --- |
| 0.30 | 3.3 |
| 0.40 | 4.6 |
| 0.50 | 6.0 |
| 0.58 | 7.0 |
| 0.65 | 8.0 |
| 0.70 | 8.7 |
| 0.75 | 9.3 |
| 0.80 | 10.0 |

A strong clip with an 0.8 hook, 0.8 payoff, 0.9 self-containment, 0.9 arc, 0.7
energy, 0.7 quotability, and modest emotion/controversy/humour lands near 8.7.
Flat narration stays below 6.0. This replaces the current 4.9 ceiling without
making every clip an 8.

### Dedupe and ranking

Candidates are sorted by `overall` descending, then start ascending.

For each candidate, compare against already-kept candidates:

```text
overlap = max(0, min(end1, end2) - max(start1, start2))
ratio   = overlap / min(duration1, duration2)
```

- Drop the new candidate when `ratio > 0.55` and the kept candidate has a higher
  score.
- If scores are within `0.25` and the new candidate is the shorter, self-contained
  one inside the preferred band (or the source-appropriate band when no
  preference is given), replace the kept longer candidate with the shorter one.
  This prevents a loose three-minute window from swallowing a tight 30-second
  moment.
- Drop a candidate whose start is within 1.5 seconds of a kept candidate when the
  overlap ratio is also above 0.30.
- Otherwise keep both. Clips may share up to 55% of the shorter clip.

After dedupe:

1. Apply `overall >= min_score`.
2. Apply `max_clips` only when the user supplied it. Take the top N by score.
3. Write `segments.json` sorted by start time, each with a `rank` field where 1 is
   the highest score. Existing start-sorted table and `--clips 1,3,5` behaviour
   stay intact.

### Threshold, count, and reporting

- Default `min_score` is `8.0`.
- Comparisons are inclusive (`overall >= min_score`); a clip at exactly `8.0`
  passes and is shown as `8.0+`.
- Default `max_clips` is `None`, meaning every passing clip.
- If nothing passes, `segments.json` is written as an empty list. The CLI and
  Studio show the real score range and a threshold suggestion.
- `--action prepare` completes in `review` with zero candidates.
- `--action all` or `--action render` with zero candidates raises a clear error.
- The CLI prints:

```text
Scanned 147,456 windows across 48m 12s.
Candidate scores 4.1-9.2. 12 distinct clips at 8.0 or above.
```

- A `selection.json` file records the same numbers for Studio.

### Search modes and Ollama

- **Keyword search** (`--search`) seeds the shared window builder at every
  matching sentence. Results are deduped and scored but are not filtered by
  `min_score`; an explicit keyword query is the user's threshold.
- **Semantic search** (`--semantic`) keeps its current local-embedding ranking and
  uses the shared window builder and dedupe. Results keep the `similarity` field.
- **Ollama** (`--llm`) no longer reads the whole transcript. After deterministic
  dedupe, it reranks the top 50 candidates before thresholding. It returns a
  score adjustment from -1.0 to +1.0 and a short reason per candidate. It cannot
  change timestamps. `candidates.json` keeps the deterministic score plus the
  optional adjustment and model, so re-thresholding with the same model stays
  exact; `score_parts` keeps the deterministic text score. Invalid responses are
  ignored. If Ollama is unreachable, the deterministic scores stand.
- `--llm` with `--search` or `--semantic` prints a warning and is ignored, because
  those modes already define what to find.

## Creator evidence

Transcript-only scoring misses clips carried by a scream, a reaction, a stunt, a
gameplay spike, or a fast cut. The evidence pass analyzes only candidate ranges.
It never decodes the whole video for visual features.

### Audio timeline

The full 1 Hz audio timeline is built once per source, before window selection,
from the local audio or the downloaded audio track. A short source costs a few
seconds of decode; a long source already has the audio file. Building it once
also gives the content profile and source-energy baseline without a second pass.

The timeline stores, per second:

- RMS in dBFS
- peak in dBFS
- spectral flux from a 50 ms frame FFT
- zero-crossing rate
- high-band ratio (energy above 4 kHz divided by total energy)
- burst flag (RMS above the source median + 8 dB, or above the local
  60-second rolling baseline + 8 dB)

`numpy` computes the features from mono 16 kHz PCM decoded by ffmpeg. The timeline
is cached as `audio_features.npz`, keyed by source identity and feature version.
A 48-hour source is about 173k frames and a few megabytes on disk.

Source baseline: median, 10th percentile, and 90th percentile of RMS, flux, and
peak. `audio_variability = clamp((p90_rms - p50_rms) / 12, 0, 1)`.

The local 60-second rolling baseline is what makes gameplay audio usable.
Continuous loud music or constant game sound raises the global median, but a
gunshot, explosion, hit, or victory stinger still stands out against the last
minute. Sustained music has high RMS but low flux and a low high-band ratio;
transient events have a short burst, high flux, and a high high-band ratio. This
is a heuristic separation, not a trained classifier.

Per-candidate audio signals, each mapped to 0-1 with `smoothstep`:

| Signal | Weight | Meaning |
| --- | --- | --- |
| Peak rise | 0.25 | Loudest second versus source and rolling baseline |
| Burst density | 0.20 | Fraction of seconds above baseline + 8 dB |
| Spectral flux | 0.20 | Sudden timbre change: gunshot, explosion, hit, scream, laughter |
| Event arc | 0.25 | Rise, peak, and decay or post-peak reaction inside the window |
| Boundary quiet | 0.10 | Start and end near local troughs |

The event arc is:

```text
event_arc = 0.35 * rise + 0.45 * peak + 0.20 * decay_or_reaction
```

`rise` compares the first 30% of the window with the middle, `peak` compares the
loudest second with the rolling baseline, and `decay_or_reaction` measures the
post-peak hold, reaction, or drop back toward baseline. A clip that ends exactly
on the peak still scores through `peak`, but a clip that includes the reaction
scores higher.

`audio_score = 10 * weighted_sum`.

### Visual analysis

Gameplay and PC-game VODs are the main reason this pass exists. Continuous motion
is normal in a game, so absolute motion is useless. The engine measures spikes
against a local rolling baseline and asks whether the window contains a complete
action arc.

Visual features are computed only for merged candidate intervals, plus up to 12
sparse five-second baseline samples across the source. For long URL sources,
those baseline samples are downloaded as short 360p sections; if the downloads
fail, `visual_baseline` is unavailable and `source_energy` uses audio only. Each
candidate interval is extracted with a 2-second pad on both sides, inside the
source, so boundary refinement can move the clip without another decode. Inside
each interval, a 30-second rolling baseline is used for motion and scene-change
spikes.

For each interval, ffmpeg extracts frames at 2 fps at 160x90 and OpenCV computes:

- mean absolute frame difference (motion)
- scene-cut count using a histogram-difference threshold
- full-frame and top/center histogram change (UI transitions, scoreboards, kill
  feeds, victory/defeat screens)
- face presence and face motion from Haar detection, including a corner-face
  check for facecam layouts
- action arc: motion rise, peak, and decay or post-peak reaction
- boundary motion troughs

Per-candidate visual signals:

| Signal | Weight | Meaning |
| --- | --- | --- |
| Motion spike | 0.25 | Local frame difference against the 30-second rolling baseline; fights, stunts, fast movement |
| Scene/UI change | 0.20 | Cuts, round transitions, scoreboards, kill feeds, item drops, victory/defeat screens |
| Facecam reaction | 0.20 | Face presence and movement, especially in a consistent corner region; screams, laughs, disbelief |
| Event arc | 0.25 | Motion rise, peak, and decay or reaction inside the window |
| Boundary quiet | 0.10 | Start and end near local motion troughs |

`visual_score = 10 * weighted_sum`.

The event arc uses the same shape as audio:

```text
event_arc = 0.35 * rise + 0.45 * peak + 0.20 * decay_or_reaction
```

A facecam signal is neutral at 0.5 when no consistent face region exists, so
games without a facecam are not penalized. The scene/UI signal is a visual change
detector, not OCR: it notices that a scoreboard, kill feed, or victory screen
appeared, not what the text says. That keeps the feature local and dependency-free
while still catching the moments game viewers clip.

For long URL sources there is no full video. After the audio shortlist, the
evidence pass downloads 360p sections for the surviving candidate ranges, analyzes
them, caches them separately, and deletes them after evidence. These sections are
not reused for final rendering. If a visual-only discovery video exists, ranges
are analyzed from it instead of downloading separate evidence sections. If the
download fails, visual evidence is skipped for those candidates and the audio
result stands.

Visual extraction failure is non-fatal. The candidate falls back to audio evidence
and records a warning in its reason.

### Adaptive weighting

The sparse visual baseline is:

```text
visual_baseline = clamp(0.5 * motion_norm + 0.3 * cut_norm + 0.2 * facecam_norm,
                        0, 1)
```

`motion_norm` is the mean frame difference percentile against the source's sparse
samples, `cut_norm` is `clamp(cut_rate / 30, 0, 1)`, and `facecam_norm` is the
fraction of sampled frames with a face.

The source energy profile is:

```text
source_energy = clamp(0.6 * audio_variability + 0.4 * visual_baseline, 0, 1)
```

If one baseline is unavailable, the other is used. If both are unavailable,
`source_energy` is 0.5.

The engine also assigns an internal content profile. It is diagnostic, not a
user setting:

| Profile | Signal pattern | Evidence floor | Duration band |
| --- | --- | --- | --- |
| Talk | High speech density, low motion, low cut rate | 0.20 | 45-180 seconds |
| Mixed | Neither talk nor action dominates | 0.35 | 30-120 seconds |
| Gameplay/action | Low speech density and high motion, cuts, audio bursts, or facecam activity | 0.60 | 15-60 seconds |

Speech density is total transcript words divided by probed source duration. The
exact thresholds are:

```text
talk:            speech >= 2.0 wps and visual_baseline < 0.35
gameplay/action: speech < 1.5 wps and (visual_baseline >= 0.45
                 or cut_rate >= 12/min or audio_burst_rate >= 0.15/s)
mixed:           everything else
```

The evidence weight is:

```text
w_e = max(0.2 + 0.45 * source_energy, profile_floor)   # 0.20 to 0.75
```

Evidence adjusts the text score without replacing it:

```text
final = clamp(text_score + (evidence_score - 5.0) * 0.6 * w_e, 0, 10)
```

Neutral evidence (5.0) leaves the text score unchanged. Strong evidence adds up
to about 2.25 points; weak evidence subtracts up to the same amount. A gameplay
or high-energy source therefore punishes dull stretches more than a podcast does,
while a podcast's text quality still dominates.

This is how the same threshold works across content types:

- A talk clip passes on a strong hook, a complete arc, and a clean payoff.
- A gameplay clip passes on a complete action arc: setup or tension, an event
  peak, and a reaction or resolution. Commentary can strengthen it but is not
  required.
- A movie or narrative clip passes on a self-contained scene with a clear turn
  and payoff, even when the audio is not loud.

When audio and visual are both available:

```text
evidence_score = 0.6 * audio_score + 0.4 * visual_score
```

When only one is available, its score shrinks 15% toward 5.0 to reflect lower
confidence. When neither is available, evidence is neutral at 5.0.

### Event arc and boundary refinement

Text-led and action-led clips share one final boundary pass. The pass runs after
the audio timeline and padded visual ranges are extracted, and before the final
evidence score and dedupe. The final score is computed on the refined range using
the cached per-second audio features and sampled visual frames, so moving a
boundary does not leave a stale score behind.

For speech-led windows (0.8 words per second or more):

- Keep sentence boundaries.
- Extend the start up to 0.4 seconds earlier to catch the first word's onset, and
  the end up to 0.4 seconds later for the final word's decay.
- Never cross an adjacent sentence boundary and never cut a spoken word.

For action-led or gameplay windows (under 0.8 words per second):

- Move the start to the nearest loudness rise, motion spike, or scene/UI cut
  within 1.5 seconds.
- Move the end to the nearest trough, post-peak reaction, event resolution, or
  scene cut within 1.5 seconds.
- Do not end on the wind-up. Prefer the point where the event resolves or the
  reaction begins.
- Keep the duration inside `[min_len, max_len]`. If a snap would cut a spoken
  word, the word boundary wins.
- If no reliable audio or visual boundary exists, keep the sentence or source
  window unchanged.

Boundary refinement cannot move a clip across the start or end of the source.
The adjusted duration is written to `segments.json` as `duration`.

### Silent and action-only sources

If a candidate range has fewer than 0.8 words per second:

- `text_score` is floored at `7.0` instead of pretending the words are strong.
- `w_e` is raised to at least `0.75`.
- The candidate can pass with exceptional evidence:

```text
7.0 + (evidence_score - 5.0) * 0.6 * 0.75
```

At the default threshold of 8.0, that needs an evidence score of about 7.2 or
higher.

This covers gameplay and PC-game VODs with no commentary, music-only montages,
and silent streams. A candidate with high average motion but no rise or peak is
capped at evidence 6.0, so flat action cannot pass 8.0 on motion alone. The clip
must contain an event arc: setup or tension, a peak, and a reaction or resolution.

If a source has no words at all, audio and visual event peaks are the seeds.
Windows are built from the duration ladder with event-boundary endpoints, as
described under "Seeds". If no event peaks exist but the media timeline still
varies, a coarse time grid (every `max(30, max_len / 2)` seconds) supplies seeds
so the project can report a real score range.

On a long URL, if the downloaded audio is silent and there are no words, download
the lowest-resolution video for visual discovery before giving up. If the
timeline is flat and there are no words, the project fails with:

```text
No speech and no media evidence; this source cannot be scored.
```

### Cost and caching

The content profile and source energy are computed from a sparse audio/visual
baseline before window selection, so the duration band is available without a
full decode.

The evidence pass is two-stage so a 48-hour source does not decode hours of video
for weak candidates:

1. Text score every start-best window.
2. Compute audio score for all candidates from the timeline. The audio
   contribution to the final score is
   `(audio_score - 5.0) * 0.6 * w_e * 0.6`.
3. Keep candidates where
   `text_score + audio_adjustment + max_visual_bonus >= analysis_floor`, merge
   their ranges, and analyze only those intervals. `analysis_floor` is
   `min(8.0, min_score at discovery time)`.
4. Compute final evidence and final score.

`max_visual_bonus` is the largest possible visual contribution:

```text
with audio:    5.0 * 0.6 * w_e * 0.4
without audio: 5.0 * 0.6 * w_e
```

Stage 2 deliberately uses full-modality evidence as an upper bound. The final
pass applies the 15% single-modality shrink described above, so a shortlist never
drops a candidate that the final score would have passed.

If a later selection lowers `min_score` below `analysis_floor`, only the
additional ranges that now qualify are analyzed; the transcript and all existing
evidence stay cached. Raising the threshold never needs new analysis.

If visual analysis is unavailable, the audio-only result is final. If audio is
unavailable but visual is present, visual-only is final. If neither is available,
evidence is neutral at 5.0.

Feature files are cached by source identity, feature version, and rounded range.
A failed extraction marks that range text/audio-only and does not fail the
project.

### Discovery and selection caches

Discovery and selection are separate steps:

- **Discovery** covers the sentence scan, endpoint choice, evidence extraction,
  and dedupe. Its cache key is transcript identity, `min_len`, `max_len`,
  `preferred_len`, evidence version, and content profile. It writes
  `candidates.json`.
- **Selection** applies `min_score`, the optional `max_clips` cap, and `rank` to
  the cached candidates. It writes `segments.json` and `selection.json`.
  Lowering `min_score` below the saved `analysis_floor` analyzes only the newly
  relevant ranges; it never rescans the transcript. Candidates outside the floor
  keep an audio-only score and a `visual_pending` flag until a lower threshold
  makes them relevant.

`score_clips` exposes `discover_candidates()` and `select_candidates()` for this
split. `run()` remains the compatibility wrapper that calls both. `run_project`
uses the split, so changing only `min_score` or `max_clips` re-filters
`candidates.json` without rescanning the transcript.

## Long sources

### Path selection

After probing duration and estimated size:

- **Short URL path:** under 2 hours and estimated full download under 8 GB. Keep
  the current behaviour: download video, transcribe in one pass, render from the
  local file.
- **Long URL path:** 2 hours or more, or estimated full download 8 GB or more.
  This includes multi-hour game streams and PC-game VODs. Download audio only for
  discovery, transcribe in chunks, and download only the selected video sections
  for rendering.
- **Visual-only long URL path:** when the long URL has no audio stream, download
  the lowest-resolution video for discovery instead of audio, skip
  transcription, and use visual seeds and evidence. The download still obeys the
  disk guard. If neither audio nor video can be fetched, fail with the no-media
  message.
- **Short local path:** under 2 hours. Never re-download; transcribe in one pass
  and render by seeking directly in the file.
- **Long local path:** 2 hours or more. Never re-download; transcribe in chunks
  and render by seeking directly in the file.

There is no toggle. Duration and size decide. If duration is unknown, use the long
path.

### Probe

- Local: `ffprobe` format duration and size, plus the first video stream's width
  and height.
- URL: `yt-dlp --dump-single-json --skip-download --no-playlist` for duration,
  title, and formats. If metadata fails, download audio and probe it.
- Cache the result in `project.json` as `source_meta`.
- Estimate full download size from the best video format's `filesize` or
  `filesize_approx` when yt-dlp provides it. Otherwise estimate from duration and
  resolution.

### Audio-first ingest

For long URLs:

- Download the best audio stream to `audio.<ext>` with yt-dlp.
- Keep the audio file for retries and re-transcription.
- Use it directly for chunked transcription and for the audio feature timeline.
- If there is no audio stream, download the lowest-resolution video to
  `discovery.<ext>` instead, use it for visual discovery and the visual baseline,
  skip transcription, and set `render_from_sections`.
- Store the original URL in project state for later section downloads.

### Chunked, resumable transcription

- Chunk length: 30 minutes. Overlap: 30 seconds.
- For each chunk, ffmpeg extracts mono 16 kHz audio to
  `audio_parts/partNNN.wav`.
- faster-whisper transcribes each chunk with word timestamps and VAD.
- All word and segment times are offset by the chunk's global start.
- Each completed chunk is written atomically to `transcript.partNNN.json` with
  `index`, `start`, `end`, `language`, `words`, `segments`, and a checksum of the
  source identity.
- Resume skips a valid part file. A corrupt, empty, or non-monotonic part is
  re-transcribed.
- Merge rule, applied to every word's midpoint:
  - For chunk `i > 0`, drop words before `chunk_start + overlap / 2`.
  - For chunk `i < last`, drop words at or after `chunk_end - overlap / 2`.
  - This assigns the first half of each overlap to the earlier chunk and the
    second half to the later chunk, so every word appears exactly once.
- Exact duplicate `(rounded start, text)` pairs are removed after the boundary
  split.
- Merge segments with the same boundary rule, then remove duplicates that fall
  entirely inside an overlap.
- Set `duration` from the probe, not from the last word.
- Write `transcript.json` with `chunked: true`, `chunks: N`, `media_kind`,
  `render_from_sections`, and the merged words.
- A legacy one-pass transcript is accepted if it is valid and covers at least 90%
  of the probed duration. Otherwise it is re-transcribed.

A 48-hour source is about 96 chunks, not one fragile multi-hour call. A 1,000-hour
source is about 2,000 chunks; there is no fixed ceiling.

### Block scanning

The transcript is scanned in 30-minute ownership blocks. Each block reads up to
`max_len` seconds of transcript context beyond its end, so windows starting near
the boundary are complete. The extra context is read-only and does not re-own any
starts.

- A block owns every sentence start in `[block_start, block_end)`.
- Its scan may extend `max_len` seconds beyond the block end.
- Each block writes `candidates.partNNN.json`.
- Completed blocks are resumable.
- After all blocks, merge candidates, run evidence, dedupe globally, and apply the
  threshold once.

Candidate scores stay comparable across blocks because the text rubric is
absolute and the audio/visual baselines are computed from the whole source.

### Section rendering

For long URL sources, rendering downloads only the chosen clip ranges.

- Sort selected clips by start.
- Group clips whose ranges overlap or are within 30 seconds of each other, and
  split a group when its padded section would exceed the cap.
- Pad each section by 10 seconds on both sides, clamp to the source, and cap a
  section at 10 minutes. When a selected clip itself is longer than 10 minutes,
  it gets its own section sized to the clip plus padding instead of being
  truncated.
- Download each section with:

```text
yt-dlp -f "bestvideo[height<=1080]+bestaudio/best[height<=1080]" \
  --merge-output-format mp4 --download-sections "*START-END" \
  --force-keyframes-at-cuts -o sections/sectionNNN.mp4 URL
```

`--force-keyframes-at-cuts` makes section boundaries accurate. It re-encodes the
section and is slower than a raw download; correctness matters more for captions
and synchronization.

- If a section fails, retry at 720p, then at the lowest available quality. If it
  still fails, record a per-clip error and continue with the remaining clips.
- Cache sections in `sections/` and reuse them across preview and final renders.
- Delete sections after a successful final (non-preview) render action. Preview
  sections stay cached so the final render does not download them again. A later
  re-render downloads them again; this keeps disk use bounded.

For local files and short URL sources, render directly from the full source with
no section step.

`render_clips.render()` gains a `source_offset` parameter:

- Clip `start` and `end` remain global source times.
- ffmpeg seeks to `start - source_offset` inside the section file.
- Captions, SRT timestamps, metadata, and poster generation keep using global
  times.
- Face tracking and tightening receive the local seek time and the same global
  word list. `tighten.apply_cuts()` gains a matching `source_offset`.
- The render cache key includes `source_offset`.

### Progress, disk, and estimates

Studio and CLI report chunk-level progress:

```text
Transcribing 34/96
Scanning 12/96
Analyzing evidence 18/37
Rendering 5/37
```

`project.json` gains a `progress` object with `stage`, `done`, `total`, and
`detail`. CLI callbacks print the same values.

Before a long job, print an approximate transcription estimate from a model and
device table, labelled approximate. Check free disk space against the estimated
audio, chunk, section, and render sizes. Warn below 1.5x the estimate; stop only
when free space is under 500 MB.

A failed chunk keeps every completed chunk. Retry resumes at the first missing or
invalid chunk. A failed clip keeps every successful render and does not stop the
remaining clips.

## CLI

New or changed flags:

| Flag | Behaviour |
| --- | --- |
| `--min-score N` | Minimum score, 0-10. Default `8.0`. Skips the score prompt. |
| `--preferred-len D` | Soft preferred length. Accepts `45`, `45s`, `2m`, `1.5m`, `1m30s`, `1h`. Range 5 seconds to 1 hour, clipped to `min_len`/`max_len`. |
| `--max-clips N` | Optional safety cap. Default none, meaning every passing clip. |
| `--min-len N` | Default `15` seconds. |
| `--max-len N` | Default `300` seconds. |
| `--no-input` | Never prompt. Defaults apply. |
| `--explain` | Print score parts and the reason for each candidate. |

Prompt rules:

- Prompt only for interactive single-source or project runs, not batch, not
  `--action render`, not `--search`/`--semantic`, and not `--no-input`.
- A non-interactive stdin skips prompts automatically.
- Existing projects show their saved values as prompt defaults.
- Flags skip their corresponding prompt.
- Batch mode never prompts; global flags apply to every source.

Prompt text:

```text
Minimum clip score (0-10, Enter = 8.0):
Preferred clip length (e.g. 45s, 2m; Enter = AI chooses):
```

After discovery:

```text
Selection: minimum score 8.0, preferred length AI chooses.
Scanned 147,456 windows across 48m 12s.
Candidate scores 4.1-9.2. 12 distinct clips at 8.0 or above.
```

If more than 10 clips are about to render in an interactive `--action all` run,
ask once:

```text
12 clips at 8.0 or above. Render all? [Y/n]
```

Answering no leaves the project in `review` and prints the render command. The
default is yes. `--no-input` renders all.

To ask after discovery rather than before it, the interactive CLI runs
`prepare` first, prints the summary, asks the confirmation, and then runs
`render`. Non-interactive `--action all` uses the single-call path. This keeps
the project status accurate and lets the user stop after reviewing candidates
without throwing away the scan.

If nothing passes:

```text
Candidate scores 4.1-6.8. 0 distinct clips at 8.0 or above.
No clips reached 8.0. Lower --min-score (for example 7.5) or run
--action prepare to review the range.
```

`--action prepare` exits 0 with an empty candidate list. `--action all` and
`--action render` exit nonzero.

The numbered table keeps its shape and gains duration and rank:

```text
  #  rank  start ->      end  score  duration  hook
  1     3  1234.50 -> 1278.20   8.7     43.7s  he lost everything
```

`--clips 1,3,5` still indexes the start-sorted table. `--explain` adds the score
parts and reason below the table.

## Studio and API

### Find-clips form

- **Minimum score**: number input, default `8.0`, 0-10.
- **Preferred length**: text input, placeholder `AI chooses`, accepts the same
  formats as the CLI.
- **Maximum clips**: optional number, blank means all.
- The existing minimum/maximum length fields remain, with defaults 15 and 300.

On save, the server parses and validates the preferred length. Invalid input
returns HTTP 400 with an example format.

### Candidate list

- Paginate 25 cards per page with previous/next controls. Pagination is
  client-side; the API returns the full candidate list.
- Show `#rank`, score, duration, editable hook, start/end fields, and an
  expandable reason with score parts and evidence.
- All passing candidates start selected on a fresh discovery.
- Add **Select all** and **Select none** beside the existing selected count.
- Show a summary above the list:

```text
12 clips at 8.0+ · passing scores 8.0-9.2 · 18s-2m 22s
Footage: gameplay/action
```

The content profile is shown for context. It is not editable and does not change
the score threshold.

- If zero clips pass, show the score range and a prompt to lower the minimum.

### Long-source preview

`GET /api/projects/<id>` gains:

- `source_meta`
- `selection` (from `selection.json`)
- `progress`
- `playable_source` (true when a local full video exists and the project does not
  render from sections)
- `render_errors`

When `playable_source` is true, **Watch** seeks the source player as today. When
it is false (long URL project), **Watch** saves the current candidate edits and
queues a 480p preview render for that one candidate through the existing run
route (`action: "render"`, `preview: true`, `pick: "<index>"`). Studio plays the
resulting MP4 and explains that the preview was rendered from a downloaded
section.

### Settings and run routes

`POST /api/projects/<id>/settings` accepts the new options:

- `min_score`: number 0-10 or null
- `preferred_len`: integer seconds, duration string (`45s`, `2m`, `1m30s`), or
  null; parsed and clipped to `min_len`/`max_len`
- `max_clips`: integer 1-3600 or null
- `min_len`, `max_len`: integers 1-3600, `min_len <= max_len`

The existing run route keeps its actions and `pick` semantics. Progress is visible
through `project.json`. No new mutation route is required.

## Data and compatibility

### `segments.json`

Existing keys stay first and keep their meaning. New keys are optional.

```json
{
  "start": 1234.5,
  "end": 1278.2,
  "hook": "he lost everything",
  "overall": 8.7,
  "duration": 43.7,
  "rank": 3,
  "raw_score": 7.4,
  "score_parts": {
    "hook": 0.8,
    "payoff": 0.7,
    "self_containment": 0.8,
    "arc": 0.8,
    "energy": 0.7,
    "quotability": 0.5,
    "emotion": 0.5,
    "controversy": 0.1,
    "humour": 0.1,
    "penalties": 0.02,
    "text_score": 7.4
  },
  "evidence": {
    "audio": 8.9,
    "visual": 7.8,
    "action_arc": 0.86,
    "source_energy": 0.78,
    "profile": "gameplay/action",
    "weight": 0.60,
    "score": 8.5
  },
  "reason": "Strong hook and payoff; reaction peak; clean boundaries"
}
```

`raw_score` is the text score before evidence adjustment. `overall` is the final
calibrated score used for the threshold. `reason` is at most 160 characters.

`projects.validate_segments()` preserves these optional fields, validates finite
times and scores, and still accepts legacy four-key segments.

### `selection.json`

Diagnostic output for the CLI and Studio:

```json
{
  "mode": "heuristic",
  "source_duration": 2892.0,
  "seeds": 18432,
  "windows_scanned": 147456,
  "candidates": 24118,
  "passing": 12,
  "score_min": 4.1,
  "score_max": 9.2,
  "passing_score_min": 8.0,
  "passing_score_max": 9.2,
  "duration_min": 18.0,
  "duration_max": 142.0,
  "min_score": 8.0,
  "preferred_len": null,
  "analysis_floor": 8.0,
  "content_profile": "gameplay/action",
  "source_energy": 0.78,
  "evidence_available": {"audio": true, "visual": true}
}
```

`content_profile` is one of `talk`, `mixed`, or `gameplay/action`. It is shown
in Studio and printed by `--explain`, never used as a user-facing mode. `seeds`
counts sentence starts, `windows_scanned` counts duration-ladder evaluations, and
`candidates` counts deduped endpoint records written to `candidates.json`.

### `transcript.json`

Adds:

- `source`: original URL or local path
- `source_duration`: probed duration
- `media_kind`: `video` or `audio`
- `render_from_sections`: boolean
- `chunked`: boolean
- `chunks`: number of chunks
- `transcript_version`: integer

The existing `video` key remains the path of the discovery media. For long URL
sources it points to the audio file, or to the lowest-resolution discovery video
when the source has no audio stream. `render_from_sections: true` tells the
renderer to download sections instead of rendering from that file.

### `project.json`

Adds:

- `source_meta`: duration, title, URL flag, long flag, width, height, estimated
  size, discovery path, and `render_from_sections`
- `selection`: summary counters and score/duration ranges
- `progress`: stage, done, total, detail
- `render_errors`: list of `{clip, error}` entries
- cache keys for discovery, evidence, sections, and transcription version

New default options:

```python
DEFAULT_OPTIONS = {
    ...,
    "min_len": 15,
    "max_len": 300,
    "max_clips": None,
    "min_score": 8.0,
    "preferred_len": None,
}
```

`validate_options()` accepts `None` for `max_clips` and `preferred_len`, accepts
the new defaults, and still rejects unknown keys and out-of-range values.

### Compatibility rules

- Existing brand JSON and caption style JSON remain valid.
- Existing caption presets and default 16:9 output are unchanged.
- Existing `segments.json` files render as before.
- Existing projects with `max_clips: 10` keep that cap until the user changes it.
  Existing `min_score: null` now means the new default `8.0`.
- Existing CLI flags keep their names and meanings. Defaults for `--max-clips`,
  `--min-len`, `--max-len`, and `--min-score` change deliberately; the changelog
  calls this out.
- `score_clips.build_candidates()` keeps its positional signature and gains
  optional `min_score` and `preferred_len`. It returns start-sorted, deduped
  candidates, including candidates below the threshold; when `max_clips` is not
  `None` it caps the returned list after dedupe.
- `score_clips.run()` keeps returning `(segments, mode)` and also writes
  `candidates.json` and `selection.json` next to `segments.json`.
- `pick_segments()`, `search_hits()`, and the semantic search cache format keep
  their names, call signatures, and behaviour. `print_table()` keeps its name and
  call signature and adds duration and rank columns.

## Error handling

- **Invalid preferred length:** fail before any project work with an example
  format and the accepted range.
- **Probe failure:** fall back to audio download and probe the audio. If that also
  fails, stop with the URL and the underlying yt-dlp/ffprobe message.
- **No audio stream on a long URL:** use the visual-only path and download the
  lowest-resolution video. If that download also fails, stop with the no-media
  message.
- **Visual-only source:** skip transcription, write an empty word list with the
  probed duration, and use visual seeds and evidence.
- **Download failure:** no partial project is marked complete. Retry leaves
  completed chunks and sections in place.
- **Chunk failure:** completed parts remain. Retry resumes at the first missing or
  invalid part. The project status is `failed` with the chunk number in the error.
- **Corrupt part file:** delete and re-transcribe that part only.
- **No speech:** use audio/visual seeds. If no media evidence exists either, fail
  with the message above.
- **Evidence extraction failure:** warn, fall back to the remaining evidence
  (text, audio, or visual), and continue. The candidate reason records the
  fallback.
- **Section download failure:** retry at lower quality, then skip that clip and
  continue. `render_errors` records the clip and message. Project status becomes
  `partial`.
- **Render failure:** the same per-clip handling. Successful clips stay cached.
- **Zero candidates:** `prepare` succeeds with an empty list; render actions fail
  with the score range and a threshold suggestion.
- **Disk space:** warn below 1.5x the estimate; stop below 500 MB free.
- **Cancellation:** KeyboardInterrupt leaves all completed parts and renders.
  Retry resumes.

New project status `partial` is handled by Studio as "complete with errors" and
shows the failed clips with a retry action.

## Testing

### Unit tests

- Strong synthetic window reaches at least 8.0; flat narration stays below 6.0.
- Filler and meta/outro penalties lower a window by at least 0.5.
- A five-minute low-density window loses to a tight 45-second window from the
  same start.
- Every sentence is considered as a start; a better overlapping window is found
  even when an earlier seed covered its sentences. This is the regression test for
  the current `taken` bug.
- Duration ladder includes the preferred length and clips values outside
  `min_len`/`max_len`.
- Endpoint refinement checks two sentences in each direction.
- A passing endpoint is never discarded in favour of a failing endpoint within the
  preference tolerance.
- Dedupe drops overlaps above 55% of the shorter clip, keeps the higher score,
  drops near-identical starts, and prefers a tight shorter clip inside the
  preferred band when scores are within 0.25.
- Zero passing clips writes `[]` and does not create a fallback clip.
- `max_clips=None` returns all passing clips; an explicit cap returns the top N.
- Preferred length parser accepts `45`, `45s`, `2m`, `1.5m`, `1m30s`, `1h` and
  rejects `0`, negative, `abc`, and values over one hour.
- `validate_options` accepts `None` for `max_clips` and `preferred_len`, rejects
  booleans, non-finite numbers, and out-of-range values.
- `validate_segments` preserves rank, duration, raw score, score parts, evidence,
  and reason.
- Keyword and semantic search still return start-sorted, deduped results.

### Evidence tests

- Audio normalization on a synthetic quiet/loud source shifts weights toward
  evidence.
- A loud reaction near the end raises the audio score; a flat track does not.
- Silent or speech-sparse windows use the 7.0 text floor and the 0.75 evidence
  weight.
- A no-speech source builds windows from event boundaries instead of sentence
  ends.
- Visual extraction failure falls back to audio without failing the project.
- No audio and no video returns neutral evidence and does not crash.

### Long-source tests

- Chunk merge removes overlap duplicates and keeps monotonic global times.
- Resume skips valid parts and re-transcribes corrupt parts.
- A failed chunk leaves earlier parts and reports the chunk number.
- Block scanning assigns starts to one block and includes windows that extend
  into the next block.
- Section grouping merges nearby clips, pads by 10 seconds, clamps to the source,
  caps at 10 minutes, and splits a group that would exceed the cap.
- `render_clips.render(source_offset=...)` seeks locally while captions, SRT, and
  metadata keep global times.
- `tighten.apply_cuts(source_offset=...)` cuts from the section without shifting
  captions.
- A failed section download records a per-clip error and continues.
- Long URL path downloads audio only; short path still downloads full video.
- A long URL without an audio stream uses the low-resolution visual-only path and
  still renders from sections.
- A long URL with a silent audio track and no words falls back to the
  low-resolution visual discovery video.
- Legacy one-pass transcripts are accepted when they cover the duration.

### Integration and HTTP tests

- `score_clips.run` on the 12-minute calibration transcript returns at least three
  clips at 8.0, with no fallback and no single-clip cap.
- `projects.run_project` `prepare` writes candidates and selection stats; changing
  only `min_score` re-filters without re-transcribing, and lowering it below
  `analysis_floor` analyzes only the newly relevant ranges.
- Studio settings round-trip the new fields; invalid preferred length returns 400.
- A project with zero candidates is `review` with an empty list; render fails
  clearly.
- Candidate save preserves optional fields.
- `GET /api/projects/<id>` exposes `selection`, `progress`, `source_meta`,
  `playable_source`, and `render_errors`.
- Real ffmpeg: a source-offset render produces H.264/AAC output of the expected
  duration and an SRT whose first cue starts near zero.

### Manual checks

- Run the 12-minute real transcript at 8.0, then at 7.5, and confirm the second
  run adds candidates without rescanning the transcript.
- Run a 3-hour local file and a long URL (or a mocked section server) through
  chunked transcription, block scanning, and section rendering.
- In Studio, paginate a large candidate list, select/deselect across pages, edit a
  hook and times, preview a long-URL candidate, and export.
- Confirm existing projects, brand presets, caption styles, and 16:9 output are
  unchanged.

## Rollout

The work ships in four phases. Each phase is usable on its own.

1. **Selection core.** New rubric, calibration, every-sentence scan, duration
   ladder, soft preference, dedupe, threshold behaviour, CLI prompts, Studio
   fields, pagination, tests, docs. Short sources only.
2. **Creator evidence.** Audio timeline, visual candidate analysis, adaptive
   weights, action seeds, silent-source handling, evidence caching and tests.
3. **Long-source ingest.** Probe, audio-first URL path, chunked/resumable
   transcription, block scanning, progress, disk checks, tests.
4. **Section rendering.** Source offset, section grouping/download/cache/cleanup,
   per-clip errors, long-source Studio preview, tests.

Phase 1 changes the default score to 8.0 and the default clip-length range to
15-300 seconds. This is intentional and documented in the changelog. Users can
pass the old values to keep the old boundaries.

No feature flag is added. Phase 3 chooses the long path from duration and size,
not from a user setting. Phase 4 activates only when `render_from_sections` is
true, including visual-only long URL projects whose discovery video is low
resolution.

## Files touched

- `score_clips.py`: rubric, calibration, exhaustive scan, duration ladder,
  endpoint choice, dedupe, `discover_candidates()` / `select_candidates()`
  split, threshold handling, selection stats, Ollama rerank, search-mode
  integration.
- `evidence.py` (new): audio timeline, visual sampling, normalization, adaptive
  weights, feature cache.
- `download_and_transcribe.py`: probe, audio-first download, chunked/resumable
  transcription, merge, legacy transcript acceptance.
- `projects.py`: new options, source metadata, discovery/evidence/section caches,
  long-path orchestration, progress, per-clip errors, `partial` status.
- `chopify.py`: prompts, `--preferred-len`, `--no-input`, `--explain`, new
  defaults, render confirmation, threshold messages.
- `render_clips.py`: `source_offset`, section-aware face tracking and tightening,
  cache-key inputs.
- `tighten.py`: `source_offset` for section cuts.
- `sections.py` (new): section grouping, downloads, retries, cleanup.
- `dashboard.py`: config defaults, settings validation, project response fields,
  progress, `partial` status.
- `assets/studio/index.html`, `app.js`, `style.css`: score/duration fields,
  candidate pagination, selection controls, score summary, reason details,
  long-source preview.
- `tests/test_chopify.py`, `tests/test_studio.py`, new evidence and long-source
  tests, optional calibration fixture.
- `README.md`, `docs/WORKFLOWS.md`, `docs/AGENT-API.md`, `CHANGELOG.md`,
  `docs/product.json`, `llms.txt`, and `docs/index.html` where feature claims are
  listed.
