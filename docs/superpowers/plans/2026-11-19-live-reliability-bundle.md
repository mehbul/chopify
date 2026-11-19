# Live Reliability Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep live sessions alive through token expiry and stalls, report honest health and LLM state, and prove the pipeline with a local HLS fixture in CI.

**Architecture:** `LiveSource` gets a `refresh()` path; `LiveSession` owns capture restarts and a `health` dict saved to `live_session.json`; `live_signals` adds an off-by-default ad-slate heuristic that suppresses triggers; `campaign` reports provider failure precisely; an ffmpeg-generated local HLS fixture gives CI a network-free integration test.

**Tech Stack:** Python 3.11+, ffmpeg, OpenCV, stdlib `http.server`, pytest.

## Global Constraints

- No new dependency, service, account, or network requirement in CI.
- Ad suppression is off by default and never deletes footage.
- Restart backoff: 1, 2, 4 seconds capped; fail after 3 restarts.
- Stall threshold: 20 seconds without a new segment; refresh interval: 600 seconds.
- Health values: `ok | degraded | failed`; every degraded state names the next action.
- CLI flags: `--live-skip-ads`, `--brief-provider none`.
- Full suite must pass before each commit.

---

### Task 1: Source refresh

**Files:** `live_capture.py`, `tests/test_live_capture.py`

- [ ] **Step 1: Write failing test**

```python
def test_live_source_refresh(monkeypatch):
    import live_capture
    calls = []

    class Result:
        stdout = __import__("json").dumps({
            "url": "https://cdn/new.m3u8", "protocol": "hls"})

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(live_capture.ingest, "_run", fake_run)
    source = live_capture.resolve_live_source(
        "https://twitch.tv/channel", {}, runner=fake_run)
    assert source.page_url == "https://twitch.tv/channel"
    refreshed = source.refresh({}, runner=fake_run)
    assert refreshed.url == "https://cdn/new.m3u8"
    assert refreshed.page_url == "https://twitch.tv/channel"
    assert calls


def test_direct_source_refresh_is_noop():
    import live_capture
    source = live_capture.resolve_live_source(
        "https://cdn/live.m3u8", {})
    assert source.refresh({}) is source
```

- [ ] **Step 2: Run to verify failure** → FAIL (`AttributeError: 'LiveSource' object has no attribute 'page_url'`).

- [ ] **Step 3: Implement**

In `live_capture.py`, replace the dataclass with:

```python
@dataclass
class LiveSource:
    url: str
    kind: str
    platform: str
    resolved_by: str
    headers: list[str]
    page_url: str | None = None

    def refresh(self, options, runner=None):
        """Re-resolve a page URL; a direct manifest cannot be refreshed."""
        if not self.page_url:
            return self
        return resolve_live_source(self.page_url, options, runner)
```

In `resolve_live_source()`, change the two return statements to include `page_url`:
- passthrough: `LiveSource(..., headers, None)`
- yt-dlp: `LiveSource(resolved, kind, _platform(url), "yt-dlp", headers, url)`

- [ ] **Step 4: Run to verify pass** → `python -m pytest -q tests/test_live_capture.py`
- [ ] **Step 5: Commit** → `git commit -m "feat(live): add source refresh for expiring stream tokens"`

---

### Task 2: Ad-slate heuristic

**Files:** `live_signals.py`, `tests/test_live_signals.py`

- [ ] **Step 1: Failing tests**

```python
import numpy as np


def test_ad_slate_detection():
    import live_signals
    flat = np.zeros((90, 160, 3), dtype=np.uint8)
    flat[:, :, 0] = 200
    flat[:, :, 2] = 220
    assert live_signals.looks_like_ad_slate(flat)
    noisy = np.random.RandomState(1).randint(0, 255, (90, 160, 3)).astype(np.uint8)
    assert not live_signals.looks_like_ad_slate(noisy)


def test_analyzer_tracks_ad_like():
    import live_signals
    analyzer = live_signals.VisualAnalyzer.__new__(live_signals.VisualAnalyzer)
    analyzer.ad_like = False
    analyzer._mark_ad(np.zeros((90, 160, 3), dtype=np.uint8))
    assert analyzer.ad_like
```

- [ ] **Step 2: Run to verify failure** → FAIL (`AttributeError`).

- [ ] **Step 3: Implement** (append to `live_signals.py`):

```python
AD_MIN_SATURATION = 0.35
AD_MAX_TEXTURE = 12.0


def looks_like_ad_slate(frame):
    """Best-effort check for a flat, saturated Twitch ad slate."""
    import cv2
    image = np.asarray(frame, dtype=np.uint8)
    if image.ndim != 3 or image.shape[2] != 3:
        return False
    small = cv2.resize(image, (80, 45), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    texture = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    saturation = float(np.mean(hsv[:, :, 1])) / 255.0
    return texture < AD_MAX_TEXTURE and saturation > AD_MIN_SATURATION
```

Add to `VisualAnalyzer` (after `self._seen = set()` in `__init__`): `self.ad_like = False`. Add method:

```python
    def _mark_ad(self, frame):
        if looks_like_ad_slate(frame):
            self.ad_like = True
```

In `VisualAnalyzer._publish()`, call `self._mark_ad(frame)` for each frame before adding visual signals.

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(live): add best-effort ad slate detection"`

---

### Task 3: Health state and capture restart

**Files:** `live.py`, `chopify.py`, `tests/test_live.py`

- [ ] **Step 1: Failing tests**

```python
def test_health_state_saved(tmp_path):
    import live
    from live_capture import LiveSource
    source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, {}, tmp_path)
    session.health["state"] = "degraded"
    session._save()
    data = live.projects.read_json(session.dir / "live_session.json")
    assert data["health"]["state"] == "degraded"
    assert "last_segment_age" in data["health"]


def test_next_action():
    import live
    assert "segments" in live.next_action({"state": "degraded"}).lower()
    assert live.next_action({"state": "ok"}) == "Watching"
```

- [ ] **Step 2: Run to verify failure** → FAIL (`AttributeError: 'LiveSession' object has no attribute 'health'`).

- [ ] **Step 3: Implement**

In `LiveSession.__init__`, add before `_analyzers_started`:

```python
        self.health = {
            "state": "ok", "ffmpeg_alive": True, "last_segment_age": 0.0,
            "restarts": 0, "refreshes": 0, "warnings": [],
            "brief_provider": (self.rules or {}).get("provider", "none"),
            "brief_source": (self.rules or {}).get("source", "none"),
        }
        self._last_refresh = time.time()
        self._restart_delays = (1, 2, 4)
```

In `_save()`, add `"health": self.health,` to the JSON dict.

Add module-level helper:

```python
def next_action(health):
    return {"ok": "Watching",
            "degraded": "Waiting for segments",
            "failed": "Check the URL or restart Studio"}.get(
                health.get("state"), "Watching")
```

Add method to `LiveSession`:

```python
    def _capture_alive(self):
        segments = sorted((self.dir / "buffer").glob("segment*.ts"))
        if not segments:
            self.health["last_segment_age"] = 999.0
            return False
        self.health["last_segment_age"] = round(
            time.time() - max(p.stat().st_mtime for p in segments), 1)
        alive = self.buffer is not None and self.buffer.alive
        self.health["ffmpeg_alive"] = alive
        return alive and self.health["last_segment_age"] < 20
```

Add method:

```python
    def _restart_capture(self):
        delay = self._restart_delays[min(self.health["restarts"], 2)]
        self.health["restarts"] += 1
        self.health["state"] = "degraded"
        self._log(f"Capture stalled; restarting in {delay}s ({next_action(self.health)}).")
        if self.buffer is not None:
            self.buffer.stop()
        time.sleep(delay)
        if self.source.page_url and self.health["restarts"] >= 1:
            try:
                self.source = self.source.refresh(self.options)
                self.health["refreshes"] += 1
                self._log("Stream token refreshed.")
            except Exception as exc:
                self.health["warnings"].append(f"Refresh failed: {exc}")
        try:
            self.buffer = live_capture.SegmentBuffer(
                self.dir / "buffer", self.source.url, self.options).start()
        except Exception as exc:
            self.health["warnings"].append(f"Restart failed: {exc}")
```

In `_loop()`, replace the final `if not self.buffer.alive:` block with:

```python
            if self.buffer is not None and self.source.page_url and (
                    time.time() - self._last_refresh > 600):
                self._last_refresh = time.time()
                try:
                    refreshed = self.source.refresh(self.options)
                    self.source = refreshed
                    self.health["refreshes"] += 1
                    self._log("Stream token refreshed on schedule.")
                except Exception as exc:
                    self.health["warnings"].append(f"Refresh failed: {exc}")
            if not self._capture_alive():
                if self.health["restarts"] >= 3:
                    self.health["state"] = "failed"
                    self.status = "failed"
                    self.stopped_at = time.time()
                    self._log(f"Capture failed: {next_action(self.health)}")
                    break
                self._restart_capture()
            else:
                self.health["state"] = "ok"
```

Add ad suppression in the trigger branch: before rendering, if any analyzer has `ad_like` true and `self.options.get("live_skip_ads")`, log "ad-like frame; clip suppressed" and skip.

Add CLI flag in `chopify.py` after `--no-transcript`:

```python
    ap.add_argument("--live-skip-ads", action="store_true", default=None,
                    help="suppress clip triggers while a flat ad slate is detected")
```

Add `"live_skip_ads": False` to `projects.LIVE_DEFAULTS`.

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(live): add health state and capture restarts"`

---

### Task 4: Honest LLM fallback reporting

**Files:** `campaign.py`, `assets/studio/app.js`, `tests/test_campaign.py`

- [ ] **Step 1: Failing test**

```python
def test_brief_provider_none_and_failure_reason(monkeypatch):
    import campaign
    monkeypatch.setattr(campaign, "detect_provider",
                        lambda *a, **k: ("none", {"unavailable_reason": "claude not authenticated"}))
    rules = campaign.parse_brief("must include: camera", provider="none")
    assert rules["source"] == "heuristic"
    assert "claude" in rules["warning"]
```

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement**

In `campaign.detect_provider()`, when a CLI is found but `--version` probe fails, return `(name, {"command": ..., "unavailable_reason": f"{name} found but not authenticated or not runnable"})`. In `parse_brief()`, call `ask_llm` only if the descriptor has no `unavailable_reason`; when it does, raise that reason so the existing fallback records it in `warning`.

Add provider value `none` handling: if `provider == "none"`, skip `detect_provider` and go straight to `_heuristic_parse` with `warning = "Brief parsed with keyword rules (provider disabled)."`.

In Studio `app.js`, after a live session starts, set the health text (from Task 3) and show `brief_rules.json` source/provider in the live status area if available. Use the existing `refreshLive()` result; no new API.

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(campaign): report provider failures and allow forced keyword rules"`

---

### Task 5: Offline HLS fixture and integration test

**Files:** `tests/fixtures/make_hls_fixture.py`, `tests/test_live_integration.py`

- [ ] **Step 1: Write fixture generator**

```python
"""Generate a short local HLS fixture for live pipeline tests."""
import subprocess
from pathlib import Path


def make_fixture(directory, seconds=8):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "raw.ts"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=" + str(seconds),
        "-f", "lavfi", "-i", "sine=frequency=440:duration=" + str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", "-f", "mpegts", str(raw)], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(raw), "-c", "copy",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
        str(directory / "index.m3u8")], check=True)
    return directory / "index.m3u8"
```

- [ ] **Step 2: Write integration test**

```python
import shutil
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_live_pipeline_against_local_hls(tmp_path):
    import live
    import projects
    from fixtures.make_hls_fixture import make_fixture
    from live_capture import LiveSource

    served = make_fixture(tmp_path / "stream", seconds=8)
    handler = partial(SimpleHTTPRequestHandler, directory=str(served.parent))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}/index.m3u8"

    options = projects.validate_options({
        "live_buffer": 30, "live_threshold": 0.5, "live_pre_roll": 5,
        "live_tail": 2, "live_max_clips": 1, "live_no_chat": True,
        "live_no_transcript": True})
    source = LiveSource(url, "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, options, tmp_path)
    thread = threading.Thread(target=session.start, daemon=True)
    thread.start()
    deadline = time.time() + 25
    while time.time() < deadline and not session.records:
        time.sleep(0.5)
    session.stop()
    thread.join(timeout=5)
    httpd.shutdown()
    httpd.server_close()

    data = projects.read_json(session.dir / "live_session.json")
    assert data["health"]["state"] in {"ok", "degraded", "failed"}
    assert set(data["health"]) >= {"state", "ffmpeg_alive", "last_segment_age",
                                   "restarts", "refreshes", "warnings"}
    assert (session.dir / "clips").exists()
```

- [ ] **Step 3: Run the test** → `python -m pytest -q tests/test_live_integration.py`
Expected: PASS with ffmpeg; skip without it.

- [ ] **Step 4: Full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "test(live): add offline HLS fixture integration test"`

---

### Task 6: Docs and final verification

**Files:** `docs/LIVE.md`, `docs/CLI.md`, `CHANGELOG.md`

- [ ] **Step 1: LIVE.md** — document token refresh, health states, `--live-skip-ads`, and that ad detection is off by default and never discards footage.
- [ ] **Step 2: CLI.md** — add `--live-skip-ads` and `--brief-provider none` rows.
- [ ] **Step 3: CHANGELOG.md** — under `[Unreleased]`, add `Added` (token refresh, health state, offline fixture test) and `Fixed` (provider failure messages).
- [ ] **Step 4: Verify** — `python -m pytest -q`, `python tools/build_site.py --out _site`.
- [ ] **Step 5: Commit** → `git commit -m "docs(live): document reliability bundle"`

---

## Self-review notes

- Spec coverage: refresh (Task 1), ad heuristic (Task 2), health/restarts (Task 3), LLM honesty (Task 4), offline fixture (Task 5), docs (Task 6).
- Type consistency: `LiveSource.refresh(options, runner=None)` returns `LiveSource`; `health` is a plain dict with the same keys in Task 3 and the integration test; `looks_like_ad_slate(frame)` returns bool and is called from `VisualAnalyzer`.
- Risk acknowledged: the ad heuristic can misfire on flat dark gameplay; it is off by default and only suppresses triggers, never deletes segments.
