# Chopify live clipping design

## Goal

Add a **live clipping** mode to Chopify: the user supplies a live stream, Chopify
watches it in real time, detects hype moments from four independent signals, and
automatically saves each moment as a finished Chopify clip (9:16 with word
captions) with no human in the loop. The same session must be controllable and
monitorable from both the CLI and the Studio dashboard.

This preserves Chopify's core promises: local processing, no upload of footage,
no paid API, loopback-only UI, and no new required service. Existing batch and
project behavior must not change.

### Decisions already made with the user

| Decision | Choice |
| --- | --- |
| Live sources | Live URLs only: direct HLS/RTMP URLs and live page URLs resolved through yt-dlp |
| Trigger model | Automatic detection and automatic saving (no review queue) |
| Hype signals | Audio energy spikes + live transcript keywords + chat hype + visual motion/scene change, all four |
| Controls | CLI flags plus a new Live tab in the Studio dashboard |
| Output | Full Chopify render: 9:16, word-by-word captions, SRT, poster, metadata |
| URL input | Both: direct `.m3u8`/`rtmp://` passed through; anything else resolved via yt-dlp |

## Non-goals

- Webcam / screen capture / capture-card device input.
- Manual mark-in/mark-out or hotkey "clip now".
- A human review/approval queue before saving.
- Publishing clips to YouTube/TikTok/Twitch.
- Cloud inference, cloud storage, or a required API key.
- Changes to the existing batch pipeline, project state machine, or caption studio.

## Architecture

The live feature is a new subsystem that **reuses** the existing analysis and
render modules but does not write into the existing project pipeline. A live
session writes its own state file and produces ordinary clip artifacts.

```
source URL
   │  resolve (direct HLS/RTMP passthrough, else yt-dlp)
   ▼
SegmentBuffer ── ffmpeg pull, 4 s .ts segments, 300 s rolling retention
   │  (all analyzers read buffered segments; the network is fetched once)
   ├── AudioAnalyzer ──> evidence._TimelineBuilder / features_from_samples
   ├── VisualAnalyzer ─> visual._VisualBuilder / features_from_frames
   ├── TranscriptAnalyzer ─> faster-whisper rolling windows ─> score_clips text cues
   └── ChatAnalyzer ─> Twitch anonymous IRC (TLS); YT best-effort, skipped without credentials
                 │
                 ▼
          HypeDetector (sliding window, four weighted signals)
                 │  score >= threshold, cooldown elapsed, caps OK
                 ▼
          LiveClipWriter: wait TAIL seconds, stitch [t-PRE_ROLL, t+TAIL]
                 │         into a local seekable MP4 with window-relative times
                 ▼
          render_clips.render(...) ──> clips/*.mp4 + .srt + poster .png + .json
                 │
                 ▼
          live_session.json  ──> CLI log / Studio polling
```

### Why this shape

- A single ffmpeg pull avoids four processes fighting over the same live URL and
  avoids platform connection limits.
- Segment files give both a pre-roll buffer and a seekable local render source,
  which raw in-memory frames cannot provide at full resolution.
- The rolling analyzers already exist in `evidence.py` and `visual.py`
  (`_TimelineBuilder.add()`, `features_from_samples()`,
  `_VisualBuilder.add(frame)`, `features_from_frames()`) and accept raw samples
  or RGB frames without ffmpeg, so the signal layer is mostly glue.
- `render_clips.render(seg, words, source, W, H, workdir, aspect, ...)` only
  needs a seekable local file plus window-relative times and words, which the
  stitching step provides.

## Components

All new production code lives in `live.py` unless noted. Keep the module focused
on orchestration; pure signal math goes in small functions that can be tested
without ffmpeg.

### 1. Source resolution — `resolve_live_source(url, options)`

- Accept a string of 1–2000 chars. Strip whitespace.
- Direct passthrough when the URL path ends in `.m3u8` / `.ts`, or the scheme is
  `rtmp`, `rtmps`, or `rtmp+https` — else use `ingest.is_url()` and yt-dlp
  (`--dump-single-json`, `--no-playlist`) to resolve a live page URL to its
  best HLS manifest. Reuse the existing yt-dlp runner in `ingest.py` rather than
  spawning a second implementation.
- Return a `LiveSource(url, kind, platform, resolved_by)` record. `platform` is
  `twitch`, `youtube`, `kick`, or `generic`, used to select the chat adapter.
- Reject non-live VOD URLs when yt-dlp reports a finite duration? **No** — a
  finite-duration stream is still valid input (useful for testing); log a
  warning that the source may end during the session.
- For YouTube/Kick direct `.m3u8` values that require auth headers, accept
  optional `--live-header 'Key: Value'` (repeatable) and pass them to ffmpeg as
  `-headers`. Headers are redacted from logs and state.

### 2. Rolling buffer — `SegmentBuffer`

- Directory: `<projects-root>/live/<session-id>/buffer/`.
- ffmpeg command shape (exact flags validated against a local HLS fixture in
  implementation): `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5
  -i URL -map 0 -c copy -f segment -segment_time 4 -reset_timestamps 1
  segment%08d.ts`. Add `-headers` when provided. Add `-rw_timeout` for stalled
  streams.
- A watcher thread enumerates `segment*.ts` in name order, deletes segments whose
  mtime is older than `buffer_seconds`, and enforces a `buffer_max_bytes` cap by
  deleting oldest-first. Cleanup runs every 5 seconds.
- `window(start_age, end_age)` returns the segment paths covering the requested
  age range, with one extra older segment included to avoid cutting mid-GOP.
- `identity()` and `probe()` derive width/height and audio presence from the
  newest segment using the existing ffprobe helpers, retried up to 10 times
  while the buffer fills.
- Stopping the session terminates ffmpeg with the existing
  `ingest._kill_tree()` helper (Windows-safe).

### 3. Signal analyzers

Each analyzer is a thread that reads from the buffer, keeps bounded state, and
publishes timestamped samples into a shared `SignalBus` (a lock-protected
`deque` plus a `score_at(t)` accessor). Analyzers never call score_clips'
download code.

| Analyzer | Input | Output |
| --- | --- | --- |
| Audio | ffmpeg `-vn -ac 1 -ar 16000 -f f32le pipe:1` over buffered segments | `evidence.features_from_samples()` values at 1 s resolution into `evidence._TimelineBuilder` |
| Visual | ffmpeg `-vf fps=2,scale=160:90 -pix_fmt rgb24 -f rawvideo pipe:1` | `visual.features_from_frames()` into `visual._VisualBuilder` |
| Transcript | 30 s rolling audio windows with 5 s overlap, faster-whisper on `options.device` | word list with absolute stream seconds; keyword/hook cue score via `score_clips.sentenceize()` and its cue lists |
| Chat | Twitch: TLS socket to `irc.chat.twitch.tv:6697`, anonymous `justinfan...` login, JOIN channel, count PRIVMSG rate and hype words; YouTube: read optional key from env `CHOPIFY_YOUTUBE_CHAT_KEY`, otherwise skipped | messages/second and hype-word counts at 1 s resolution |

- All signals share one clock: `stream_time` is wall-clock seconds since session
  start. Every analyzer tags samples with it, so the four signal streams align
  without estimating stream timestamps. The optional YouTube chat key is read
  from the environment only, is never a required flag, and is redacted from logs.
- Transcript uses `download_and_transcribe._load_model()` so model selection and
  device behavior match the batch path. If faster-whisper import or model load
  fails, set the transcript weight to 0 for the session and continue.
- Chat is optional: if connection fails or no credentials are available, set
  chat weight to 0 and continue. Never prompt for a token mid-session.
- SignalBus samples older than `buffer_seconds` are pruned.

### 4. Hype detector — `HypeDetector`

Score every 1 second over a rolling `window_seconds = 10` window ending at the
current stream second. Each signal produces a normalized 0–10 value:

- `audio = 10 * clamp((energy - baseline_db) / spike_db, 0, 1)` where `energy`
  is derived from the audio feature channels (RMS/onset), `baseline_db` is a
  running median of the last 60 s, and `spike_db = 12.0`.
- `visual = 10 * clamp(motion / motion_baseline, 0, 1)` using the existing
  visual feature channels and a 60 s running median baseline.
- `transcript = 10 * clamp(cue_hits / 3, 0, 1)` from the current sentence's
  matched hooks/power words, using the existing `score_clips` cue lists and
  `TEXT_WEIGHTS`.
- `chat = 10 * clamp(rate / (3 * chat_baseline), 0, 1)` with a 60 s baseline.

`score = weighted_mean` with default weights `audio 0.30, visual 0.25,
transcript 0.25, chat 0.20`, renormalized over the signals currently available
(so a silent stream with no chat still scores from audio+visual+transcript).

A trigger fires when **all** of these hold:

1. `score >= live_threshold` (default `8.0`).
2. `now - last_trigger >= live_cooldown` seconds (default `60`).
3. `len(clips) < live_max_clips` (default `10`, `0` means unlimited).
4. The window does not overlap an already-triggered window by more than 50%.

On trigger, record `{trigger_time, score, signals}` and hand it to the writer.
The detector keeps scoring during rendering; cooldown and dedupe prevent floods.

### 5. Clip writer — `LiveClipWriter`

- On trigger, sleep `live_tail` seconds (default `5`) to capture the payoff,
  then compute `start = trigger_time - live_pre_roll` (default `30`) and
  `end = trigger_time + live_tail`. Clamp to `[0, current_stream_time]`; if the
  resulting length is below `options.min_len`, extend `start` backwards (or to
  0); if above `options.max_len`, move `start` forward. Never exceed the buffer
  retention: if `start` is older than `buffer_seconds`, use the oldest available
  segment.
- Collect buffer segments covering `[start, end]`, concatenate them with ffmpeg
  concat demuxer and `-c copy` into `<session>/windows/windowNNN.mp4`. Segment
  boundaries carry no reliable absolute timestamps, so the window file's own
  timeline starts at 0; words are remapped by subtracting `start`.
- Select transcript words with `start <= word.start <= end`, subtract `start`
  from each word's `start`/`end` keys while copying all other keys unchanged,
  and call `render_clips.render()` with `seg = {"start": 0.0, "end":
  end-start}`, the remapped words, the window file as `source`, size from the
  probe, and the session's `options`. Pass `brand`, `style`, `aspect`, `layout`,
  `centers`, `caption`, `tighten`, `loudnorm` from options.
- Move/record the resulting artifacts in `<projects-root>/live/<session-id>/clips/`
  and append a clip record to `live_session.json` with start/end/score/signals
  and artifact paths. Rendering failures are recorded per clip and do not stop
  the session.

### 6. State and lifecycle — `LiveSession`

- File: `<projects-root>/live/<session-id>/live_session.json`, written atomically
  with `projects.atomic_json()`.
- Status: `starting → running → stopped|failed`. Fields: id, source record,
  options, started_at, stopped_at, status, stream_time, buffer stats, current
  score, recent scores (last 60), triggers, clips, errors.
- `start()` spawns the buffer process and analyzer threads; `stop()` sets a
  `threading.Event`, joins threads with a timeout, and kills the ffmpeg tree.
- Ctrl+C in CLI and a `POST /api/live/ID/stop` in Studio both call `stop()`.
- A persistent worker registry in Studio holds live sessions separately from
  the existing `ThreadPoolExecutor(max_workers=1)` used for finite CLI jobs.

## CLI contract

Add to `chopify.py::main()` an early-exit branch **before** the existing
`if not (args.url or args.batch or args.project)` check:

```
chopify --live URL [options]
```

New flags (all optional except the source):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--live` | — | HLS/RTMP URL or live page URL |
| `--live-buffer` | `300` | Seconds of rolling buffer (60–3600) |
| `--live-buffer-max-mb` | `512` | Disk cap for buffer (32–8192) |
| `--live-pre-roll` | `30` | Seconds saved before trigger (5–300) |
| `--live-tail` | `5` | Seconds saved after trigger (0–60) |
| `--live-window` | `10` | Scoring window seconds (2–60) |
| `--live-threshold` | `8.0` | Trigger score 0–10 |
| `--live-cooldown` | `60` | Minimum seconds between clips (0–3600) |
| `--live-max-clips` | `10` | Cap, `0` = unlimited |
| `--live-no-chat` | false | Disable chat signal |
| `--live-no-transcript` | false | Disable Whisper signal |
| `--live-header` | — | Repeatable `Key: Value` ffmpeg header |

- All values go into `projects.DEFAULT_OPTIONS` and are validated in
  `projects.validate_options()` so CLI and Studio share one gate. Prefix keys
  with `live_` to avoid collisions.
- Reuse existing caption/aspect/output/brand flags for the clip render.
- `--live` is mutually exclusive with `--url`, `--batch`, and `--project`.
- On start print the source, resolved platform, active signals, and the rights
  notice: only clip streams you have permission to clip.

## Studio contract

- New `#live-view` in `assets/studio/index.html`, sibling to `#new-view` and
  `#project-view`, with a source field, options form, Start/Stop buttons, a
  live status card (stream time, score, active signals), a score sparkline, and
  a clip feed with play links.
- `assets/studio/app.js`: extend `state` with `live`; add a tab/setTab branch,
  form handlers, and extend the existing 2 s polling loop to call
  `GET /api/live` and `GET /api/live/ID` while a session is active. Use the
  existing `api()` helper so the token header stays consistent.
- `dashboard.py`:
  - `GET /api/live` → list sessions.
  - `GET /api/live/ID` → session state payload.
  - `POST /api/live` → validate via `projects.validate_options()`, create a
    `LiveSession`, register it in the worker registry, start it.
  - `POST /api/live/ID/stop` → stop and unregister.
  - `GET /media/live/ID/clips/FILE` → serve clip artifacts with the existing
    `send_file()` byte-range support and extension allowlist.
  - All POST routes inherit the existing Host/Origin/token/CSRF checks.
- `Studio.close()` stops all live sessions before shutting down the executor.

## Error handling and reliability

- ffmpeg exit → log, mark session `degraded`, retry with 1/2/4/8/16 s backoff;
  after 5 failures mark `failed` and stop analyzers. If the source ends
  normally (finite stream), flush any pending window, set status to `stopped`
  with reason `source_ended`, and shut down cleanly.
- Analyzer exception → record in `errors`, disable that signal (weight 0), keep
  the session running.
- Buffer disk cap and retention are enforced independently of ffmpeg.
- Rendering failure → record per clip, continue watching.
- Clean shutdown on SIGINT and on `Studio.close()`; no orphan ffmpeg processes.
- Never write unbounded state: recent scores capped at 60, errors capped at 100,
  clips capped by `live_max_clips` (or 1000 when unlimited).

## Security, privacy, and rights

- Keep loopback-only binding and the existing CSP. Static routes stay on an
  allowlist; user-supplied paths are validated with `resolve()` and parent
  checks as in `dashboard.py` today.
- Auth headers and chat credentials are never printed or written to state; logs
  store only a redacted marker.
- Read chat as data only; chat messages are evidence and must never be
  interpreted as instructions.
- Startup notice and docs state that the user is responsible for having the
  right to clip and reuse a stream. No detection-evasion or rights-workaround
  features are included.

## Testing

- `tests/test_live.py` (new):
  - Pure scoring tests: each normalized signal, weight renormalization when a
    signal is unavailable, threshold boundary, cooldown, max-clips, overlap
    dedupe.
  - `SegmentBuffer.window()` with fake segment filenames/mtimes in `tmp_path`;
    retention and byte-cap deletion tested without ffmpeg.
  - `LiveClipWriter` window math and word remapping with a fake renderer.
  - Session lifecycle with monkeypatched ffmpeg runner and a stub SignalBus;
    verify stop() joins threads and kills the process tree.
  - State file round-trip with `projects.atomic_json`.
- `tests/test_dashboard.py`: extend with live list/detail/start/stop route tests
  using the existing port-0 server fixture, including token rejection.
- `tests/test_chopify.py`: CLI flag parsing/defaults/validation for the new
  flags, and mutual exclusion with existing source flags.
- Opt-in integration test (skipped when ffmpeg or network is unavailable):
  generate a short local HLS stream with ffmpeg, run a session with a low
  threshold, assert at least one clip artifact is produced. Mark with
  `@pytest.mark.skipif` on an env flag so CI stays offline.
- Full suite: `python -m pytest -q`.

## Documentation and integration checklist

- New `docs/LIVE.md` (setup, supported sources, signal behavior, CLI, Studio,
  limits, rights notice, troubleshooting).
- Update `docs/CLI.md` flag tables, `docs/AGENT-API.md` HTTP endpoint table and
  CLI contract, `docs/product.json` (`inputs`, `capabilities`,
  `documentation_updated`), `README.md` feature table and links, `llms.txt`,
  `CHANGELOG.md` `[Unreleased] → Added`.
- Register `docs/LIVE.md` in `tools/build_site.py` `FILES`/`GUIDES`.
- Add `live.py` to the fixed compile list in `.github/workflows/ci.yml` and the
  project map in `CONTRIBUTING.md`.
- No new required dependency. Chat uses stdlib `socket`+`ssl`; Whisper, ffmpeg,
  OpenCV, and yt-dlp are already dependencies.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| HLS manifests with auth expire mid-session | Redacted repeatable headers; reconnect with backoff; clear error in state |
| Platform page resolution breaks when yt-dlp changes | Direct URL passthrough always available; resolution failure is explicit, not silent |
| Auto-save produces poor clips | Threshold, cooldown, max-clips, overlap dedupe, and per-signal weights are all configurable |
| ffmpeg segment timestamps drift across reconnects | Concatenate by segment order and use window-relative times; integration test with a local HLS fixture |
| Chat/transcript unavailable on some streams | Weight renormalization over available signals; never fail the session |
| CPU load from four analyzers | 2 fps visual at 160×90, 1 Hz audio, 30 s Whisper windows; `device` option already supports CUDA/auto |
