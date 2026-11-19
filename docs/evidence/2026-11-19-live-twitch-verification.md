# Live clipping — real Twitch stream verification

Date: 2026-11-19
Source used for testing: `https://www.twitch.tv/ishowspeed` (live at test time)
Status: **passed** after fixing four defects found by this test.

> Rights note: `media/` contains the rendered test clip, poster and session log,
> which include footage and audio from a third-party Twitch broadcast. They are
> included only as technical evidence; all rights remain with the streamer and
> licensors. See `media/README.md`. Signed HLS URLs and tokens are redacted.

## Environment

| Item | Value |
| --- | --- |
| OS | Windows (PowerShell) |
| Python | 3.11 |
| ffmpeg | 8.1.1-full_build (libx264, libass, AAC) |
| yt-dlp | 2026.10.26 |
| chopify | `main` at `8a840fd` + live-hardening fix |
| Source resolution | live page URL resolved to HLS by yt-dlp (`platform=twitch`, `is_live=true`) |
| Stream format observed | 1920x1080, 60 fps, AAC audio present |

## Run configuration

The threshold was deliberately lowered so a short run would exercise the full
trigger → stitch → render path. Defaults remain unchanged in the product.

```
live_buffer        180 s
live_buffer_max_mb 512 MB
live_pre_roll      15 s
live_tail          5 s
live_window        10 s
live_threshold     2.0      (default 8.0)
live_cooldown      15 s     (default 60 s)
live_max_clips     3        (default 10)
live_no_chat       false
live_no_transcript true     (avoids a first-run Whisper model download)
aspect             9:16
style              default
run duration       100 s
```

## Result

| Metric | Value |
| --- | --- |
| Session ID | `c8f7e3dbf990` |
| Final status | `stopped` (graceful stop) |
| Stream time reached | 100.15 s |
| Segments buffered | 28 |
| Buffer size | 88,944,272 bytes (~84.8 MiB) |
| Score range | 0.0 – 4.8976 |
| Hype triggers | 3 (scores 5.4545, 2.6667, 2.82) |
| Clips rendered | 3 |
| Session errors | none |
| ffmpeg capture log | empty (no errors) |

Rendered artifacts (copied to `media/` as evidence):

| File | Size |
| --- | --- |
| `live-clip-gameplay.mp4` | 1,657,212 bytes (H.264 1080x1920, AAC) |
| `live-clip-gameplay.png` | 396,491 bytes |
| `live-clip-gameplay.meta.json` | 282 bytes |
| `clip.srt` | 0 bytes (transcript disabled in this run) |

- [Rendered clip](media/live-clip-gameplay.mp4) · [Poster frame](media/live-clip-gameplay.png) ·
  [Clip metadata](media/live-clip-gameplay.meta.json) · [Session state](media/session.json)

### Full-frame follow-up

The original 9:16 clips used the default `cover` mode, which crops a 608px
vertical slice from a 1920x1080 gameplay frame — most of the screen was left
out. A `--fit contain` mode was added and tested (real ffmpeg test in
`tests/test_render_integration.py`). The clip in `media/` is that full-frame
render from the same captured gameplay window (`windows/ebffba8b752b.mp4`):
the complete 16:9 frame is scaled into 9:16 with black bars, and nothing is
cropped. The earlier cropped clip was removed. The source window ran into a
Twitch commercial break near its end, so the published demo uses only the first
9 seconds of gameplay — a frame-by-frame check confirms there is no ad footage
in the final clip.

ffmpeg reported successful 9:16 renders, e.g.:
`SAVED .../clips/clip.mp4 (9:16 1080x1920, speaker-tracked) + poster clip.png + clip.meta.json`

## Defects found and fixed during this test

1. **Analyzers decoded the in-progress segment.** The segment muxer writes a
   file progressively; decoding it produced `CalledProcessError`. Fixed by
   adding `SegmentBuffer.ready_segments()` and making analyzers read only
   completed segments.
2. **Visual frames were raw bytes.** `visual.features_from_frames()` needs RGB
   uint8 arrays; passing `bytes` raised `ValueError`. Fixed by reshaping each
   ffmpeg frame chunk to `(90, 160, 3)`.
3. **Renderer keyword mismatch.** The clip writer passed `tighten=` to
   `render_clips.render()`, whose parameter is `do_tighten`. Fixed.
4. **Extra HLS stream broke the capture.** Twitch exposed a third stream;
   `ffmpeg -map 0 -c copy` failed with non-monotonic DTS and the session died at
   ~34 s. Fixed by mapping only `0:v:0` and `0:a:0?`. Capture stderr is now
   written to `buffer/ffmpeg.log` instead of an undrained pipe.

After these fixes, a clean 100-second run produced three clips with zero errors.

## Reproduce

```bash
# From the checkout, with ffmpeg/yt-dlp installed
python -c "import live, live_capture, projects, threading, time; \
o=projects.validate_options({'live_buffer':180,'live_threshold':2.0,'live_cooldown':15,'live_max_clips':3,'live_no_transcript':True}); \
s=live.LiveSession(live_capture.resolve_live_source('https://www.twitch.tv/<your-own-channel>',o),o,'projects'); \
t=threading.Thread(target=s.start); t.start(); time.sleep(100); s.stop(); t.join()"
```

Use a stream you are permitted to record and reuse.
