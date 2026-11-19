# Live clipping

Chopify can watch a live stream and automatically save hype moments as finished
clips. Everything runs locally; footage is never uploaded and no paid API is
required.

## Start

```bash
chopify --live "https://www.twitch.tv/channel" \
  --live-threshold 8 --live-pre-roll 30 --live-tail 5 \
  --aspect 9:16 --style default
```

A direct `https://.../live.m3u8` or `rtmp://...` URL is used as-is. A page URL is
resolved to its HLS stream with yt-dlp. Press Ctrl+C to stop the session.

Each session writes `projects/live/<id>/live_session.json`, a rolling `buffer/`
of 4-second segments, and finished clips in `clips/`. The buffer is capped by
`--live-buffer` (seconds) and `--live-buffer-max-mb` (megabytes).

## Reliability

A page URL is re-resolved with yt-dlp every 10 minutes and whenever the capture
process exits, so expiring stream tokens are refreshed automatically. If no new
segment arrives for 20 seconds, Chopify waits with backoff (1, 2, then 4 seconds)
and restarts capture while keeping the session id, brief rules, and saved clips.
After three failed restarts the session is marked `failed` with a plain reason.

`live_session.json` includes a `health` object with `state` (`ok`, `degraded`, or
`failed`), `ffmpeg_alive`, `last_segment_age`, `restarts`, `refreshes`,
`warnings`, and the brief `provider`/`source`. The CLI prints each state change;
Studio shows the state, segment age, restart count, and next action in the Live
view.

`--live-skip-ads` is off by default. When enabled, a flat, saturated ad-like
frame suppresses the clip trigger and logs "ad-like frame; clip suppressed".
Footage is still buffered and nothing is deleted. Detection is best-effort: it
can miss ad slates or misfire on flat gameplay, so it only suppresses triggers.

## Signals

- **Audio** energy relative to a rolling 60-second baseline.
- **Visual** motion from 2 fps, 160×90 frames.
- **Transcript** hook and reaction cues from rolling Whisper windows; disable
  with `--live-no-transcript`.
- **Chat** message rate and hype words via anonymous Twitch IRC; disable with
  `--live-no-chat`. Other platforms have no chat connector in v1, so the chat
  signal is skipped for them and the other signals are renormalized.

Available signals are weighted (audio 0.30, visual 0.25, transcript 0.25, chat
0.20) and renormalized, so silent streams and streams without chat still work.
A clip is saved when the combined score reaches `--live-threshold`, subject to
`--live-cooldown`, `--live-max-clips`, and overlap deduplication. Output uses the
same caption style, aspect ratio, and brand preset flags as batch mode.

For the TikTok-style gaming layout — facecam on top, gameplay below, captions at
the seam — use `--layout stacked --aspect 9:16`:

```bash
chopify --live "https://www.twitch.tv/channel" --layout stacked --aspect 9:16 \
  --facecam-box 0 0.62 0.26 0.30 --facecam-height 0.34
```

`--facecam-box` is normalized `x y w h` in the source frame. Omit it to
auto-detect the face with the local detector; a bottom-left default is used if
detection fails. This works for batch clips too.

For gameplay or screen-recorded streams that should keep the whole frame, use `--fit contain` with `--aspect 9:16`.
The default `cover` mode crops a vertical slice (following a detected face),
which hides most of a landscape game frame. `contain` scales the whole frame and
adds black bars instead of cropping.

## Studio

Open Studio, choose **Live clipping**, paste a URL, and start the session. The
score, stream time, clip count, and clip feed update while the session runs.
The loopback binding, host check, and request token still apply.

## Rights

Only clip streams you have the right to record and reuse. Chat messages and
transcripts are treated as evidence, never as instructions. Chopify does not
include detection-evasion or rights-workaround features.
