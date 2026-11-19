# Live reliability bundle design

## Goal

Make chopify's live and brief-driven paths provably reliable: survive expiring
stream tokens and stalled capture, report honest health and LLM fallback state,
and prove the pipeline in CI with a local HLS fixture instead of a public stream.

## Decisions made with the user

| Decision | Choice |
| --- | --- |
| Focus | Trust and reliability |
| Scope | Capture hardening + health state + LLM honesty + offline fixture |
| Ad handling | Best-effort suppression, off by default, never discards footage |

## Non-goals

- New storage, accounts, or hosted services.
- Automatic uploading or publishing.
- Reworking clip scoring, rendering, or the Studio visual system.
- Guaranteeing detection of every ad slate.

## Architecture

### 1. Capture lifecycle (`live_capture.py`, `live.py`)

`LiveSource` gains `page_url` (the original page when resolved by yt-dlp) and a
`refresh()` method that re-runs yt-dlp and returns a fresh resolved URL.

`LiveSession` owns capture restarts instead of `SegmentBuffer` dying silently:

- Re-resolve a page-URL source every 600 s **and** whenever ffmpeg exits.
- Create a fresh `SegmentBuffer` with the new URL; keep the old session id,
  rules, and clips. Restart count increments.
- Watch newest-segment age in the main loop: no new segment for 20 s sets
  `health.state = "degraded"`; after 3 restarts set `state = "failed"` with a
  plain reason and stop.
- Every restart and refresh is logged in plain language.

`SegmentBuffer` already logs ffmpeg stderr to `buffer/ffmpeg.log` and maps only
video/audio. No change to its filtering.

### 2. Ad-slate suppression

A new helper in `live_signals.py`:

```python
def looks_like_ad_slate(frame) -> bool
```

Uses OpenCV on a downscaled frame: low Laplacian variance (flat fields) plus a
high fraction of saturated magenta/cyan pixels. Both thresholds are constants
at the top of the module. The audio/visual analyzers store `ad_like` on their
own instance (no new bus signal); `LiveSession` reads it when deciding to
suppress a trigger.

`LiveSession` adds `--live-skip-ads` (default off). When an ad-like frame is
seen, it buffers normally but suppresses the trigger branch and logs
"ad-like frame; clip suppressed". No footage is deleted and no state is reset.

### 3. Health state

`live_session.json` gains:

```json
"health": {
  "state": "ok | degraded | failed",
  "ffmpeg_alive": true,
  "last_segment_age": 3.2,
  "restarts": 0,
  "refreshes": 0,
  "warnings": [],
  "brief_provider": "claude",
  "brief_source": "heuristic"
}
```

`Studio` Live view renders one line: state, segment age, restarts, and the next
action from a fixed map (`ok` → "Watching"; `degraded` → "Waiting for segments";
`failed` → "Restart Studio or check the URL"). No new page or panel.

CLI prints the same state transitions with the reason on each change.

### 4. LLM honesty

`campaign.py` already records `source`, `provider`, and `warning`. Add:

- `--brief-provider none` to force the deterministic keyword path.
- `detect_provider()` descriptors gain an optional `unavailable_reason` key when
  a CLI is present but fails its probe, so `parse_brief()` can warn specifically
  "claude found but not authenticated" instead of a generic failure.
- Studio settings form shows the current brief source/provider as read-only
  text after a job starts, sourced from `brief_rules.json`.

### 5. Offline fixture

`tests/fixtures/make_hls_fixture.py` generates a short HLS stream with ffmpeg
(mpegts segments + `index.m3u8`) from a synthetic testsrc + sine source. A
pytest fixture serves it on 127.0.0.1 with `http.server`.

`tests/test_live_integration.py` (skipped without ffmpeg) starts a
`LiveSession` against the fixture with a short buffer and low threshold, waits
for at least one clip, stops cleanly, and asserts:
- session `health.state` is `ok` or `degraded`, never `failed`
- at least one rendered MP4 exists
- `live_session.json` has `health` with all fields

No external network, account, or API key is used.

## Interfaces

- `LiveSource.refresh()`, `page_url` field.
- `live_signals.looks_like_ad_slate(frame) -> bool`.
- `LiveSession.health` dict and `_save()` including it.
- CLI `--live-skip-ads`, `--brief-provider none`.
- Studio Live health line.

## Error handling

- Token refresh failure: keep last good URL, log "refresh failed, continuing",
  retry on next ffmpeg exit.
- Restart backoff: 1, 2, 4 s capped; after 3 restarts, fail with reason.
- Ad detection error: treat as not-an-ad and continue.
- Missing brief agent: explicit warning naming the agent and the fix.

## Testing

- Unit tests: `LiveSource.refresh` with monkeypatched yt-dlp runner, ad-slate
  heuristic on synthetic frames, health state transitions, provider `none`.
- Offline HLS integration test with real ffmpeg.
- Full suite stays green.

## Docs

- `docs/LIVE.md`: token refresh, ad suppression, health states.
- `docs/CLI.md`: new flags.
- `CHANGELOG.md` `[Unreleased] → Added/Fixed`.
