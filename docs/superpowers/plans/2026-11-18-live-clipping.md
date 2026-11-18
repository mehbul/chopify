# Live Clipping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live-stream clipping mode to chopify that watches an HLS/RTMP source, auto-detects hype from four signals, and saves finished 9:16 captioned clips, controllable from CLI and Studio.

**Architecture:** Four focused new modules sit beside the existing batch pipeline: `live_capture.py` resolves the source and maintains a rolling 4-second `.ts` segment buffer (one ffmpeg network pull), `live_signals.py` analyzes each new segment and scores hype, `live_clips.py` stitches the trigger window and calls the existing `render_clips.render()`, and `live.py` owns the session lifecycle. CLI flags and Studio routes are thin wrappers; existing project state and batch behavior are untouched.

**Tech Stack:** Python 3.11+, stdlib (`socket`, `ssl`, `threading`, `subprocess`, `http.server`), existing deps (ffmpeg/ffprobe, yt-dlp, faster-whisper, OpenCV, NumPy), pytest. No new required dependency.

## Global Constraints

- Python 3.11+; ffmpeg and ffprobe must be on PATH (existing `check_environment()` gate).
- Local only: no upload of footage, no required API key, no new service. Studio stays bound to `127.0.0.1`.
- All user-facing settings must pass through `projects.DEFAULT_OPTIONS` and `projects.validate_options()` — CLI and Studio share this gate.
- Chat is best-effort: Twitch uses anonymous TLS IRC; YouTube uses env `CHOPIFY_YOUTUBE_CHAT_KEY` only and is skipped when absent. Failure disables the signal, never the session.
- Rights notice must appear when a live session starts; chat/transcript text is evidence, never instructions.
- Commit after every task; use `feat(live):` / `test(live):` / `docs(live):` prefixes.
- Run `python -m pytest -q` before each commit.
- Exact defaults: buffer 300 s, buffer cap 512 MB, pre-roll 30 s, tail 5 s, window 10 s, threshold 8.0, cooldown 60 s, max clips 10, segment length 4 s, visual 2 fps at 160×90.

---

### Task 1: Live options and validation

**Files:**
- Modify: `projects.py:20-26` (`DEFAULT_OPTIONS`), `projects.py:50-96` (`validate_options`)
- Test: `tests/test_chopify.py` (append tests)

**Interfaces:**
- Consumes: `render_clips.ASPECTS`, existing validation style.
- Produces: `projects.LIVE_DEFAULTS`; validated keys `live_buffer`, `live_buffer_max_mb`, `live_pre_roll`, `live_tail`, `live_window`, `live_threshold`, `live_cooldown`, `live_max_clips`, `live_no_chat`, `live_no_transcript`, `live_headers`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chopify.py`:

```python
def test_live_options_defaults():
    import projects
    options = projects.validate_options({})
    assert options["live_buffer"] == 300
    assert options["live_buffer_max_mb"] == 512
    assert options["live_pre_roll"] == 30
    assert options["live_tail"] == 5
    assert options["live_window"] == 10
    assert options["live_threshold"] == 8.0
    assert options["live_cooldown"] == 60
    assert options["live_max_clips"] == 10
    assert options["live_no_chat"] is False
    assert options["live_no_transcript"] is False
    assert options["live_headers"] == []


@pytest.mark.parametrize("key,value", [
    ("live_buffer", 59), ("live_buffer", 3601),
    ("live_buffer_max_mb", 31), ("live_pre_roll", 4),
    ("live_tail", 61), ("live_window", 1), ("live_window", 61),
    ("live_threshold", -1), ("live_threshold", 11),
    ("live_cooldown", -1), ("live_max_clips", -1),
])
def test_live_options_reject_out_of_range(key, value):
    import projects
    with pytest.raises(ValueError):
        projects.validate_options({key: value})


def test_live_options_headers():
    import projects
    options = projects.validate_options({"live_headers": ["X-Key: abc", "Referer: https://x/"]})
    assert options["live_headers"] == ["X-Key: abc", "Referer: https://x/"]
    with pytest.raises(ValueError):
        projects.validate_options({"live_headers": ["not-a-header"]})
    with pytest.raises(ValueError):
        projects.validate_options({"live_headers": "X-Key: abc"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_chopify.py -k live_options`
Expected: FAIL with `KeyError: 'live_buffer'` (or unknown-settings error).

- [ ] **Step 3: Add defaults**

In `projects.py`, replace the `DEFAULT_OPTIONS` block with:

```python
LIVE_DEFAULTS = {
    "live_buffer": 300, "live_buffer_max_mb": 512, "live_pre_roll": 30,
    "live_tail": 5, "live_window": 10, "live_threshold": 8.0,
    "live_cooldown": 60, "live_max_clips": 10, "live_no_chat": False,
    "live_no_transcript": False, "live_headers": [],
}

DEFAULT_OPTIONS = {"aspect": "16:9", "style": "default", "tighten": False,
                   "loudnorm": False, "whisper_model": "medium", "device": "cpu",
                   "min_len": 15, "max_len": 300, "max_clips": None,
                   "min_score": 8.0, "preferred_len": None,
                   "llm": None, "search": None, "semantic": None,
                   "embed_model": "nomic-embed-text", "brand": None,
                   "layout": "single", "centers": [0.25, 0.75], "caption": None,
                   **LIVE_DEFAULTS}
```

- [ ] **Step 4: Add validation**

Insert before `if result["caption"] is not None:` in `validate_options()`:

```python
    live_int_ranges = {
        "live_buffer": (60, 3600), "live_buffer_max_mb": (32, 8192),
        "live_pre_roll": (5, 300), "live_tail": (0, 60),
        "live_window": (2, 60), "live_cooldown": (0, 3600),
        "live_max_clips": (0, 1000),
    }
    for key, (low, high) in live_int_ranges.items():
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not low <= result[key] <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}.")
    if (isinstance(result["live_threshold"], bool)
            or not isinstance(result["live_threshold"], (int, float))
            or not math.isfinite(result["live_threshold"])
            or not 0 <= result["live_threshold"] <= 10):
        raise ValueError("live_threshold must be between 0 and 10.")
    for key in ("live_no_chat", "live_no_transcript"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false.")
    headers = result["live_headers"]
    if not isinstance(headers, (list, tuple)) or len(headers) > 20:
        raise ValueError("live_headers must be a list of at most 20 headers.")
    for header in headers:
        if (not isinstance(header, str) or ":" not in header
                or not header.split(":", 1)[0].strip()
                or any(c in header for c in "\r\n")):
            raise ValueError("Each live header must be 'Name: Value' without line breaks.")
    result["live_headers"] = list(headers)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_chopify.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add projects.py tests/test_chopify.py
git commit -m "feat(live): add live options and validation"
```

---

### Task 2: Live source resolution and rolling segment buffer

**Files:**
- Create: `live_capture.py`
- Test: `tests/test_live_capture.py`

**Interfaces:**
- Consumes: `ingest.is_url()`, `ingest._run`, `ingest._probe_local`, `ingest._kill_tree`, `projects.atomic_json` (not used here).
- Produces: `LiveSource`, `resolve_live_source(url, options, runner=None)`, `SegmentBuffer(directory, url, options, runner=None)` with `.start()`, `.stop()`, `.segments()`, `.window(start, end)`, `.cleanup()`, `.probe()`, `.alive`, `.index_to_time(index)`, `.time_to_index(t)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_capture.py`:

```python
import json
import time
from pathlib import Path

import pytest

import live_capture


def test_resolve_direct_hls():
    source = live_capture.resolve_live_source(" https://x/live.m3u8 ", {})
    assert source.kind == "hls"
    assert source.url == "https://x/live.m3u8"
    assert source.resolved_by == "passthrough"


def test_resolve_direct_rtmp():
    source = live_capture.resolve_live_source("rtmp://x/live", {})
    assert source.kind == "rtmp"
    assert source.platform == "generic"


def test_resolve_page_uses_ytdlp(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class Result:
            stdout = json.dumps({"webpage_url": "https://twitch.tv/foo",
                                 "title": "Foo", "protocol": "hls",
                                 "url": "https://video/live.m3u8"})
        return Result()

    monkeypatch.setattr(live_capture.ingest, "_run", fake_run)
    source = live_capture.resolve_live_source("https://www.twitch.tv/foo", {})
    assert source.kind == "hls"
    assert source.platform == "twitch"
    assert source.resolved_by == "yt-dlp"
    assert source.url == "https://video/live.m3u8"
    assert any("yt-dlp" in part for part in calls[0])


def test_resolve_rejects_non_url():
    with pytest.raises(ValueError, match="http"):
        live_capture.resolve_live_source("not a url", {})


def test_segment_window_and_retention(tmp_path):
    buffer = live_capture.SegmentBuffer(
        tmp_path, "https://x/live.m3u8",
        {"live_buffer": 300, "live_buffer_max_mb": 512, "live_headers": []},
        runner=lambda cmd, **kw: None)
    for i in range(5):
        (tmp_path / f"segment{i:08d}.ts").write_bytes(b"x" * 100)
    assert [p.name for p in buffer.segments()] == [
        f"segment{i:08d}.ts" for i in range(5)]
    assert buffer.time_to_index(9.9) == 2
    assert buffer.index_to_time(2) == 8.0
    selected = buffer.window(4.0, 12.0)
    assert [p.name for p in selected] == [
        "segment00000001.ts", "segment00000002.ts", "segment00000003.ts"]
    old = tmp_path / "segment00000000.ts"
    old.touch()
    import os
    os.utime(old, (time.time() - 400, time.time() - 400))
    buffer.cleanup()
    assert not old.exists()


def test_segment_buffer_cap(tmp_path):
    buffer = live_capture.SegmentBuffer(
        tmp_path, "https://x/live.m3u8",
        {"live_buffer": 300, "live_buffer_max_mb": 1, "live_headers": []},
        runner=lambda cmd, **kw: None)
    for i in range(12):
        (tmp_path / f"segment{i:08d}.ts").write_bytes(b"x" * 100_000)
    buffer.cleanup()
    assert len(buffer.segments()) < 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_live_capture.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'live_capture'`.

- [ ] **Step 3: Implement `live_capture.py`**

Create `live_capture.py`:

```python
"""Resolve live URLs and keep a bounded rolling buffer of HLS segments."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import ingest
import projects

SEGMENT_SECONDS = 4
DIRECT_SUFFIXES = (".m3u8", ".ts")
DIRECT_SCHEMES = {"rtmp", "rtmps", "rtmp+https"}
PLATFORMS = (
    ("twitch.tv", "twitch"), ("youtube.com", "youtube"), ("youtu.be", "youtube"),
    ("kick.com", "kick"),
)


@dataclass
class LiveSource:
    url: str
    kind: str
    platform: str
    resolved_by: str
    headers: list[str]


def _platform(url):
    host = (urlparse(url).hostname or "").lower()
    for needle, name in PLATFORMS:
        if needle in host:
            return name
    return "generic"


def resolve_live_source(url, options, runner=None):
    """Return a LiveSource; direct manifests pass through, pages use yt-dlp."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        raise ValueError("Provide a live stream URL of at most 2000 characters.")
    url = url.strip()
    headers = list(options.get("live_headers") or [])
    parsed = urlparse(url)
    if (parsed.scheme in DIRECT_SCHEMES
            or (parsed.scheme in {"http", "https"} and url.lower().split("?")[0].endswith(DIRECT_SUFFIXES))):
        return LiveSource(url, "rtmp" if parsed.scheme in DIRECT_SCHEMES else "hls",
                          _platform(url), "passthrough", headers)
    if not ingest.is_url(url):
        raise ValueError("Live source must be an http(s), HLS or RTMP URL.")
    runner = runner or ingest._run
    cmd = ["yt-dlp", "--dump-single-json", "--no-playlist", "--no-warnings", url]
    result = runner(cmd)
    data = json.loads(result.stdout or "{}")
    resolved = str(data.get("url") or "")
    if not resolved:
        raise ValueError("yt-dlp could not resolve a live stream URL from that page.")
    protocol = str(data.get("protocol") or "").lower()
    kind = "hls" if "m3u8" in resolved or "hls" in protocol else "http"
    return LiveSource(resolved, kind, _platform(url), "yt-dlp", headers)


class SegmentBuffer:
    """ffmpeg pulls once; analyzers read the resulting 4-second segments."""

    def __init__(self, directory, url, options, runner=None):
        self.directory = Path(directory)
        self.url = url
        self.options = options
        self.runner = runner or ingest._run
        self.retention = float(options.get("live_buffer", 300))
        self.max_bytes = int(options.get("live_buffer_max_mb", 512)) * 1024 * 1024
        self.headers = list(options.get("live_headers") or [])
        self.process = None
        self.started_at = 0.0
        self._stop = threading.Event()
        self._cleaner = None

    def ffmpeg_command(self):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-reconnect", "1", "-reconnect_streamed", "1",
               "-reconnect_delay_max", "5", "-rw_timeout", "15000000"]
        if self.headers:
            cmd += ["-headers", "".join(h + "\r\n" for h in self.headers)]
        cmd += ["-i", self.url, "-map", "0", "-c", "copy",
                "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
                "-reset_timestamps", "1", str(self.directory / "segment%08d.ts")]
        return cmd

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        self.process = subprocess.Popen(
            self.ffmpeg_command(), stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True)
        self._cleaner = threading.Thread(target=self._clean_loop, daemon=True)
        self._cleaner.start()
        return self

    def _clean_loop(self):
        while not self._stop.wait(5.0):
            try:
                self.cleanup()
            except OSError:
                pass

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        self._stop.set()
        if self.process is not None and self.alive:
            ingest._kill_tree(self.process)
        if self._cleaner is not None:
            self._cleaner.join(timeout=2.0)

    def segments(self):
        if not self.directory.exists():
            return []
        return sorted(p for p in self.directory.glob("segment[0-9]*.ts")
                      if re.fullmatch(r"segment\d{8}\.ts", p.name))

    @staticmethod
    def index_of(path):
        return int(Path(path).stem.split("segment")[1])

    @staticmethod
    def index_to_time(index):
        return float(index) * SEGMENT_SECONDS

    @staticmethod
    def time_to_index(t):
        return max(0, int(float(t) // SEGMENT_SECONDS))

    def window(self, start, end):
        """Segments covering [start, end], plus one older segment for GOP safety."""
        first = max(0, self.time_to_index(start) - 1)
        last = self.time_to_index(end)
        return [p for p in self.segments() if first <= self.index_of(p) <= last]

    def cleanup(self):
        now = time.time()
        paths = self.segments()
        keep = []
        for path in paths:
            if now - path.stat().st_mtime > self.retention:
                path.unlink(missing_ok=True)
            else:
                keep.append(path)
        total = sum(p.stat().st_size for p in keep)
        for path in keep:
            if total <= self.max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size

    def probe(self):
        paths = self.segments()
        if not paths:
            raise FileNotFoundError("No segments yet; wait for the buffer to fill.")
        return ingest._probe_local(paths[-1], run=self.runner)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_live_capture.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add live_capture.py tests/test_live_capture.py
git commit -m "feat(live): add source resolution and rolling segment buffer"
```

---

### Task 3: Signal bus and hype detector (pure logic)

**Files:**
- Create: `live_signals.py`
- Test: `tests/test_live_signals.py`

**Interfaces:**
- Consumes: nothing external (pure math).
- Produces: `SignalBus` with `.add(name, t, value)`, `.prune(now, retention)`, `.values(name, t, window)`, `.available(now, window)`; `normalize(value, baseline, scale)`, `HypeDetector` with `.weights`, `.evaluate(bus, t)` returning `{"score": float, "signals": dict}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_signals.py`:

```python
import pytest

import live_signals


def test_signal_bus_window_and_prune():
    bus = live_signals.SignalBus()
    for i in range(10):
        bus.add("audio", float(i), float(i))
    assert bus.values("audio", 9.0, 3.0) == [7.0, 8.0, 9.0]
    bus.prune(9.0, 5.0)
    assert bus.values("audio", 9.0, 10.0) == [5.0, 6.0, 7.0, 8.0, 9.0]
    assert bus.available(9.0, 3.0) == {"audio"}


def test_normalize():
    assert live_signals.normalize(12.0, 0.0, 12.0) == 10.0
    assert live_signals.normalize(-100.0, 0.0, 12.0) == 0.0
    assert live_signals.normalize(0.0, 0.0, 0.0) == 0.0


def _spike(bus, name, t=9.0, value=10.0):
    for i in range(9):
        bus.add(name, float(i), 0.0)
    bus.add(name, t, value)


def _steady(bus, name, value, count=10):
    for i in range(count):
        bus.add(name, float(i), value)


def test_detector_renormalizes_missing_signals():
    bus = live_signals.SignalBus()
    _spike(bus, "audio")
    _spike(bus, "visual")
    result = live_signals.HypeDetector().evaluate(bus, 9.0)
    assert result["score"] == pytest.approx(10.0)
    assert set(result["signals"]) == {"audio", "visual"}


def test_detector_weighted_score():
    bus = live_signals.SignalBus()
    _spike(bus, "audio")
    for name in ("visual", "transcript", "chat"):
        _steady(bus, name, 0.0)
    result = live_signals.HypeDetector().evaluate(bus, 9.0)
    assert result["score"] == pytest.approx(3.0)


def test_detector_ignores_steady_signal():
    bus = live_signals.SignalBus()
    _steady(bus, "audio", 10.0)
    assert live_signals.HypeDetector().evaluate(bus, 9.0)["score"] == 0.0


def test_detector_uses_latest_sample():
    bus = live_signals.SignalBus()
    _spike(bus, "audio", t=2.0, value=10.0)
    assert live_signals.HypeDetector().evaluate(bus, 2.0)["score"] == pytest.approx(10.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_live_signals.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the bus and detector**

Create `live_signals.py` (first half; analyzers added in Task 4):

```python
"""Rolling signal bus and hype scoring for live sessions."""
from __future__ import annotations

import math
import statistics
import threading
from collections import defaultdict, deque

DEFAULT_WEIGHTS = {"audio": 0.30, "visual": 0.25,
                   "transcript": 0.25, "chat": 0.20}
SIGNALS = tuple(DEFAULT_WEIGHTS)
# Minimum dynamic range used as the denominator when a signal is steady.
SCALES = {"audio": 0.05, "visual": 0.05, "transcript": 1.0, "chat": 2.0}


class SignalBus:
    """Thread-safe, time-tagged samples with bounded retention."""

    def __init__(self, retention=300.0):
        self._lock = threading.Lock()
        self._samples = defaultdict(deque)
        self.retention = float(retention)

    def add(self, name, t, value):
        if name not in SIGNALS:
            raise ValueError(f"Unknown signal: {name}")
        if not math.isfinite(float(t)) or not math.isfinite(float(value)):
            return
        with self._lock:
            self._samples[name].append((float(t), float(value)))
            cutoff = float(t) - self.retention
            queue = self._samples[name]
            while queue and queue[0][0] < cutoff:
                queue.popleft()

    def prune(self, now, retention=None):
        cutoff = float(now) - float(retention if retention is not None else self.retention)
        with self._lock:
            for queue in self._samples.values():
                while queue and queue[0][0] < cutoff:
                    queue.popleft()

    def values(self, name, t, window):
        with self._lock:
            return [value for stamp, value in self._samples.get(name, ())
                    if t - float(window) <= stamp <= t + 1e-9]

    def available(self, t, window):
        return {name for name in SIGNALS if self.values(name, t, window)}


def normalize(value, baseline, scale):
    """Map a raw signal onto 0-10 relative to its rolling baseline."""
    if not math.isfinite(scale) or scale <= 0:
        return 0.0
    return max(0.0, min(10.0, 10.0 * (float(value) - float(baseline)) / float(scale)))


class HypeDetector:
    """Combine available signals into a single 0-10 score."""

    def __init__(self, weights=None):
        self.weights = dict(weights or DEFAULT_WEIGHTS)

    def evaluate(self, bus, t):
        signals = {}
        for name in SIGNALS:
            history = bus.values(name, t, 60.0)
            if not history:
                continue
            baseline = statistics.median(history) if len(history) >= 5 else 0.0
            scale = max(SCALES[name], max(history) - baseline)
            signals[name] = round(normalize(history[-1], baseline, scale), 4)
        if not signals:
            return {"score": 0.0, "signals": {}}
        total_weight = sum(self.weights[name] for name in signals)
        score = sum(self.weights[name] * value
                    for name, value in signals.items()) / total_weight
        return {"score": round(score, 4), "signals": signals}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_live_signals.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add live_signals.py tests/test_live_signals.py
git commit -m "feat(live): add signal bus and hype detector"
```

---

### Task 4: Signal analyzers (audio, visual, transcript, chat)

**Files:**
- Modify: `live_signals.py` (append analyzer classes)
- Test: `tests/test_live_signals.py` (append analyzer tests)

**Interfaces:**
- Consumes: `evidence.features_from_samples`, `visual.features_from_frames`, `score_clips.sentenceize`, `score_clips.HOOK_CUES`/`SHOCK_CUES`/`REACTION_PHRASES`/`GAMEPLAY_CUES`, `download_and_transcribe._load_model`/`_transcribe_media`, `live_capture.SegmentBuffer`.
- Produces: `AudioAnalyzer(buffer, bus)`, `VisualAnalyzer(buffer, bus)`, `TranscriptAnalyzer(buffer, bus, model, device, enabled)`, `ChatAnalyzer(buffer, bus, platform, channel, enabled)`, each with `.run(stop_event)` and `.error` / `.available` attributes. Plus `start_analyzers(buffer, bus, options, platform, channel)` returning a list of `(thread, analyzer)` pairs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live_signals.py`:

```python
from pathlib import Path

import numpy as np

import live_signals


class FakeSegment:
    def __init__(self, index):
        self.name = f"segment{index:08d}.ts"
        self.index = index


class FakeBuffer:
    def __init__(self, segments=()):
        self.segments = list(segments)
        self.started_at = 0.0
        self.directory = Path(".")

    def index_of(self, path):
        return path.index

    def index_to_time(self, index):
        return float(index) * 4.0

    def window(self, start, end):
        return self.segments


class FakeStop:
    """Allows exactly one loop iteration, so no real sleep occurs."""
    def __init__(self):
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > 1

    def wait(self, seconds):
        return False


def test_audio_analyzer_publishes_energy(monkeypatch):
    bus = live_signals.SignalBus()
    fake = FakeBuffer([FakeSegment(0)])
    analyzer = live_signals.AudioAnalyzer(fake, bus)
    monkeypatch.setattr(analyzer, "_decode", lambda path: np.full(16000, 0.5, dtype=np.float32))
    analyzer.run(FakeStop())
    assert bus.values("audio", 4.0, 10.0)


def test_visual_analyzer_publishes_motion(monkeypatch):
    bus = live_signals.SignalBus()
    fake = FakeBuffer([FakeSegment(0)])
    analyzer = live_signals.VisualAnalyzer(fake, bus)
    frames = [np.zeros((90, 160, 3), dtype=np.uint8) for _ in range(8)]
    monkeypatch.setattr(analyzer, "_decode", lambda path: frames)
    analyzer.run(FakeStop())
    assert bus.values("visual", 4.0, 10.0)


def test_transcript_score_from_cues():
    words = [{"word": "Here's", "start": 0.0, "end": 0.2},
             {"word": "the", "start": 0.2, "end": 0.3},
             {"word": "secret", "start": 0.3, "end": 0.6, }]
    words[-1]["word"] = "secret."
    assert live_signals.transcript_score(words) > 0.0
    assert live_signals.transcript_score([
        {"word": "the", "start": 0.0, "end": 0.2},
        {"word": "a", "start": 0.2, "end": 0.4}]) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_live_signals.py -k analyzer`
Expected: FAIL with `AttributeError: module 'live_signals' has no attribute 'AudioAnalyzer'`.

- [ ] **Step 3: Implement analyzers**

Append to `live_signals.py`:

```python
import re
import socket
import subprocess
import time

import evidence
import visual
import score_clips
import download_and_transcribe as dt

CHAT_WORDS = ("let's go", "no way", "insane", "clutch", "hype", "w", "gg", "crazy")


def transcript_score(words):
    """Return 0-10 from hook/reaction cues in a rolling word window."""
    if not words:
        return 0.0
    text = " ".join(str(w.get("word", "")) for w in words).lower()
    hits = 0
    for cues in (score_clips.HOOK_CUES, score_clips.SHOCK_CUES,
                 score_clips.REACTION_PHRASES, score_clips.GAMEPLAY_CUES):
        hits += sum(1 for cue in cues if cue in text)
    return normalize(min(3.0, float(hits)), 0.0, 3.0)


class BaseAnalyzer:
    name = "base"
    delay = 1.0

    def __init__(self, buffer, bus):
        self.buffer = buffer
        self.bus = bus
        self.error = None
        self.available = True
        self._seen = set()

    def _new_segments(self):
        return [p for p in self.buffer.segments()
                if self.buffer.index_of(p) not in self._seen]

    def _decode(self, path):
        raise NotImplementedError

    def _publish(self, path, payload):
        raise NotImplementedError

    def run(self, stop_event):
        while not stop_event.is_set():
            try:
                for path in self._new_segments():
                    index = self.buffer.index_of(path)
                    payload = self._decode(path)
                    self._publish(index, payload)
                    self._seen.add(index)
            except Exception as exc:  # one bad segment must not kill the session
                self.error = f"{type(exc).__name__}: {exc}"
                self.available = False
                return
            stop_event.wait(self.delay)


class AudioAnalyzer(BaseAnalyzer):
    name = "audio"
    delay = 0.5

    def _decode(self, path):
        cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn",
               "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"]
        result = subprocess.run(cmd, capture_output=True, check=True)
        return np.frombuffer(result.stdout, dtype=np.float32).copy()

    def _publish(self, index, samples):
        rows = evidence.features_from_samples(samples)
        if rows is None:
            return
        values = getattr(rows, "rms", None)
        if values is None:
            values = np.asarray(rows).reshape(-1)
        for i, value in enumerate(values):
            t = self.buffer.index_to_time(index) + i
            self.bus.add("audio", t, float(value))


class VisualAnalyzer(BaseAnalyzer):
    name = "visual"
    delay = 0.5

    def _decode(self, path):
        cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
               "fps=2,scale=160:90", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "pipe:1"]
        result = subprocess.run(cmd, capture_output=True, check=True)
        raw = result.stdout
        frame_size = 160 * 90 * 3
        count = len(raw) // frame_size
        return [raw[i * frame_size:(i + 1) * frame_size] for i in range(count)]

    def _publish(self, index, frames):
        if not frames:
            return
        features = visual.features_from_frames(frames)
        for i, value in enumerate(features.motion):
            t = self.buffer.index_to_time(index) + i / 2.0
            self.bus.add("visual", t, float(value))


class TranscriptAnalyzer(BaseAnalyzer):
    name = "transcript"
    delay = 1.0

    def __init__(self, buffer, bus, model=None, device="cpu", enabled=True):
        super().__init__(buffer, bus)
        self.model = model
        self.device = device
        self.enabled = enabled
        if not enabled:
            self.available = False
        self.words = []
        self._next = 0.0

    def _decode(self, path):
        index = self.buffer.index_of(path)
        start = self.buffer.index_to_time(index)
        if start < self._next:
            return [], start
        self._next = start + 30.0
        segments = self.buffer.window(start, start + 30.0)
        if not segments:
            return [], start
        listing = self.buffer.directory / "transcript_list.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments),
                           encoding="utf-8")
        wav = self.buffer.directory / "transcript.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", str(listing), "-ac", "1",
                        "-ar", "16000", str(wav)], check=True, capture_output=True)
        if self.model is None:
            self.model = dt._load_model("small", self.device, "int8")
        _, words, _, _ = dt._transcribe_media(self.model, wav, offset=start)
        return words, start

    def _publish(self, index, payload):
        words, start = payload
        self.words.extend(words)
        self.words = [w for w in self.words if w["end"] >= start - 60.0]
        for i in range(0, 30):
            t = start + i
            window = [w for w in self.words if t - 10.0 <= w["start"] <= t]
            self.bus.add("transcript", t, transcript_score(window))

    def run(self, stop_event):
        if not self.enabled:
            return
        super().run(stop_event)


class ChatAnalyzer(BaseAnalyzer):
    name = "chat"
    delay = 1.0

    def __init__(self, buffer, bus, platform="generic", channel="", enabled=True):
        super().__init__(buffer, bus)
        self.platform = platform
        self.channel = channel
        self.enabled = enabled
        self.counts = []
        if not enabled or platform != "twitch" or not channel:
            self.available = False

    def _decode(self, path):
        return self._read_twitch() if self.platform == "twitch" else []

    def _read_twitch(self):
        import ssl
        context = ssl.create_default_context()
        with context.wrap_socket(socket.create_connection(
                ("irc.chat.twitch.tv", 6697), timeout=10), server_hostname="irc.chat.twitch.tv") as sock:
            sock.settimeout(10)
            sock.sendall(b"NICK justinfan12345\r\nUSER justinfan12345 0 * :justinfan12345\r\n")
            sock.sendall(f"JOIN #{self.channel}\r\n".encode())
            sock.settimeout(2)
            messages = 0
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    line = sock.recv(4096).decode("utf-8", "replace")
                except (socket.timeout, OSError):
                    break
                messages += len(re.findall(r"PRIVMSG", line))
                for word in CHAT_WORDS:
                    messages += line.lower().count(word)
            return [float(messages)]

    def _publish(self, index, payload):
        t = self.buffer.index_to_time(index)
        for value in payload:
            self.bus.add("chat", t, value)

    def run(self, stop_event):
        if not self.available:
            return
        try:
            super().run(stop_event)
        except OSError as exc:
            self.error = f"Chat unavailable: {exc}"
            self.available = False


def start_analyzers(buffer, bus, options, platform, channel, model=None):
    """Start analyzer threads; return (thread, analyzer) pairs."""
    pairs = []
    specs = [
        (AudioAnalyzer(buffer, bus), {}),
        (VisualAnalyzer(buffer, bus), {}),
        (TranscriptAnalyzer(buffer, bus, model, options.get("device", "cpu"),
                            not options.get("live_no_transcript", False)), {}),
        (ChatAnalyzer(buffer, bus, platform, channel,
                      not options.get("live_no_chat", False)), {}),
    ]
    for analyzer, kwargs in specs:
        thread = threading.Thread(target=analyzer.run,
                                  args=(buffer._stop,), kwargs=kwargs,
                                  name=f"live-{analyzer.name}", daemon=True)
        thread.start()
        pairs.append((thread, analyzer))
    return pairs
```

`threading` is already imported at the top of the module from Task 3; the appended block above adds `socket`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_live_signals.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add live_signals.py tests/test_live_signals.py
git commit -m "feat(live): add audio, visual, transcript and chat analyzers"
```

---

### Task 5: Clip writer — window stitching and rendering

**Files:**
- Create: `live_clips.py`
- Test: `tests/test_live_clips.py`

**Interfaces:**
- Consumes: `render_clips.render`, `live_capture.SegmentBuffer`, `projects` path helpers.
- Produces: `ClipRecord`, `plan_window(trigger_time, buffer_seconds, pre_roll, tail, min_len, max_len, now)` returning `(start, end)`; `remap_words(words, start, end)`; `stitch(buffer, start, end, destination, runner=None)`; `LiveClipWriter.write(trigger, session_dir, options, probe, words, renderer=None)` returning `ClipRecord`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_clips.py`:

```python
import pytest

import live_clips


def test_plan_window_defaults():
    start, end = live_clips.plan_window(
        100.0, 300, 30, 5, 15, 300, 110.0)
    assert start == 70.0
    assert end == 105.0


def test_plan_window_respects_buffer():
    start, end = live_clips.plan_window(1000.0, 60, 120, 5, 15, 300, 1010.0)
    assert start == 945.0
    assert end == 1005.0


def test_plan_window_min_length():
    start, end = live_clips.plan_window(10.0, 300, 5, 0, 15, 300, 12.0)
    assert start == 0.0
    assert end == 10.0


def test_plan_window_max_length():
    start, end = live_clips.plan_window(500.0, 600, 300, 60, 15, 120, 560.0)
    assert end - start == 120.0


def test_remap_words():
    words = [{"word": "hi", "start": 12.0, "end": 12.5, "score": 1.0},
             {"word": "bye", "start": 13.0, "end": 13.5},
             {"word": "late", "start": 20.0, "end": 20.2}]
    result = live_clips.remap_words(words, 10.0, 14.0)
    assert result == [
        {"word": "hi", "start": 2.0, "end": 2.5, "score": 1.0},
        {"word": "bye", "start": 3.0, "end": 3.5}]


def test_stitch_builds_concat_list(tmp_path):
    paths = [tmp_path / f"segment{i:08d}.ts" for i in range(2, 5)]
    for p in paths:
        p.write_bytes(b"x")
    calls = []
    live_clips.stitch(paths, paths[-1].parent / "window.mp4",
                      runner=lambda cmd, **kw: calls.append(cmd))
    assert calls and "concat" in calls[0]
    listing = (paths[0].parent / "window_list.txt").read_text()
    assert "segment00000002.ts" in listing
    assert "segment00000004.ts" in listing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_live_clips.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `live_clips.py`**

Create `live_clips.py`:

```python
"""Turn a hype trigger into a finished chopify clip artifact set."""
from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import ingest
import render_clips


@dataclass
class ClipRecord:
    id: str
    start: float
    end: float
    score: float
    signals: dict
    files: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Trigger:
    time: float
    score: float
    signals: dict


def plan_window(trigger_time, buffer_seconds, pre_roll, tail,
                min_len, max_len, now):
    """Return (start, end) in stream seconds for a trigger."""
    now = min(float(now), float(trigger_time) + float(tail))
    end = min(float(trigger_time) + float(tail), now)
    start = max(0.0, float(trigger_time) - float(pre_roll))
    start = max(start, end - float(buffer_seconds))
    length = end - start
    if length < min_len:
        start = max(0.0, end - min_len)
    if length > max_len:
        start = end - max_len
    return round(start, 3), round(end, 3)


def remap_words(words, start, end):
    """Shift word timestamps into a window-relative timeline."""
    result = []
    for word in words or []:
        if not (start <= float(word.get("start", -1)) <= end):
            continue
        item = dict(word)
        item["start"] = float(word["start"]) - start
        item["end"] = float(word["end"]) - start
        result.append(item)
    return result


def stitch(paths, destination, runner=None):
    """Concatenate selected segments into one seekable local MP4."""
    runner = runner or ingest._run
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("No segments available for this clip window.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    listing = destination.parent / "window_list.txt"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in paths),
                       encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
           "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
           str(destination)]
    runner(cmd)
    return destination


class LiveClipWriter:
    def __init__(self, session_dir):
        self.session_dir = Path(session_dir)
        self.clips_dir = self.session_dir / "clips"
        self.windows_dir = self.session_dir / "windows"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.windows_dir.mkdir(parents=True, exist_ok=True)

    def write(self, trigger, options, probe, words, segments, renderer=None):
        """Render one clip; return a ClipRecord (with .error on failure)."""
        render_fn = renderer or render_clips.render
        start, end = plan_window(
            trigger.time, options["live_buffer"], options["live_pre_roll"],
            options["live_tail"], options["min_len"], options["max_len"],
            trigger.time + options["live_tail"])
        record = ClipRecord(uuid.uuid4().hex[:12], start, end,
                            trigger.score, trigger.signals)
        try:
            window = stitch(segments, self.windows_dir / f"{record.id}.mp4")
            seg = {"start": 0.0, "end": round(end - start, 3)}
            render_fn(seg, remap_words(words, start, end), window,
                      probe["width"], probe["height"], str(self.session_dir),
                      options["aspect"], out_dir=str(self.clips_dir),
                      style=options["style"], tighten=options["tighten"],
                      loudnorm=options["loudnorm"], brand=options["brand"],
                      layout=options["layout"], centers=options["centers"],
                      caption=options["caption"])
            record.files = {"window": str(window)}
            for path in sorted(self.clips_dir.glob(f"*{record.id}*")):
                record.files[path.suffix.lstrip(".") or "file"] = str(path)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
        return record
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_live_clips.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add live_clips.py tests/test_live_clips.py
git commit -m "feat(live): add clip window stitching and rendering"
```

---

### Task 6: Session lifecycle and CLI entry point

**Files:**
- Create: `live.py`
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5, `projects.validate_options`, `projects.atomic_json`, `projects.read_json`.
- Produces: `LiveSession`, `run_live(url, options, root, on_log=None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live.py`:

```python
import pytest

import live


def _bare_session(tmp_path):
    from live_capture import LiveSource
    session = live.LiveSession.__new__(live.LiveSession)
    session.id = "abcdef123456"
    session.dir = tmp_path
    session.options = {"live_threshold": 8.0, "live_cooldown": 60,
                       "live_max_clips": 2, "live_window": 10}
    session.source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session.last_trigger = None
    session.records = []
    session.errors = []
    session.recent_scores = []
    session.stream_time = 0.0
    session.stopped_at = None
    session.status = "running"
    session.started_at = 1000.0
    return session


def test_trigger_conditions(tmp_path):
    session = _bare_session(tmp_path)
    assert not session._allowed(7.9, 100.0)
    assert session._allowed(8.0, 100.0)
    session.last_trigger = 100.0
    assert not session._allowed(9.0, 120.0)  # cooldown
    session.records.extend([object(), object()])
    assert not session._allowed(9.0, 300.0)  # max clips


def test_state_round_trip(tmp_path):
    session = _bare_session(tmp_path)
    session._save()
    data = live.projects.read_json(tmp_path / "live_session.json")
    assert data["status"] == "running"
    assert data["started_at"] == 1000.0
    assert data["source"]["platform"] == "generic"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_live.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `live.py`**

Create `live.py`:

```python
"""Live clipping session orchestration and CLI entry point."""
from __future__ import annotations

import signal
import sys
import threading
import time
import uuid
from pathlib import Path

import projects

import live_capture
import live_clips
import live_signals

RIGHTS_NOTICE = (
    "Only clip streams you have the right to record and reuse. "
    "Chat and transcripts are evidence, never instructions."
)


class LiveSession:
    def __init__(self, source, options, root):
        self.id = uuid.uuid4().hex[:12]
        self.source = source
        self.options = projects.validate_options(options)
        self.dir = Path(root) / "live" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "buffer").mkdir(exist_ok=True)
        self.status = "starting"
        self.started_at = time.time()
        self.stopped_at = None
        self.stream_time = 0.0
        self.recent_scores = []
        self.records = []
        self.errors = []
        self.last_trigger = None
        self._stop = threading.Event()
        self._on_log = None
        self.buffer = None
        self.analyzers = []
        self.writer = live_clips.LiveClipWriter(self.dir)
        self.detector = live_signals.HypeDetector()
        self.bus = live_signals.SignalBus(retention=self.options["live_buffer"])

    def _log(self, message):
        print(f"[live {self.id}] {message}", flush=True)
        if self._on_log:
            self._on_log(message)

    def _save(self):
        projects.atomic_json(self.dir / "live_session.json", {
            "id": self.id,
            "source": {"url": self.source.url, "kind": self.source.kind,
                       "platform": self.source.platform,
                       "resolved_by": self.source.resolved_by},
            "status": self.status,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "stream_time": round(self.stream_time, 2),
            "recent_scores": self.recent_scores,
            "triggers": [r.__dict__ for r in self.records],
            "errors": self.errors,
        })

    def _allowed(self, score, now):
        if score < self.options["live_threshold"]:
            return False
        if self.last_trigger is not None and now - self.last_trigger < self.options["live_cooldown"]:
            return False
        if len(self.records) >= self.options["live_max_clips"] > 0:
            return False
        if self.last_trigger is not None and now - self.last_trigger < self.options["live_window"] / 2:
            return False
        return True

    def start(self):
        self._log(RIGHTS_NOTICE)
        self._log(f"Watching {self.source.url} via {self.source.resolved_by}")
        self.buffer = live_capture.SegmentBuffer(
            self.dir / "buffer", self.source.url, self.options).start()
        self.status = "running"
        self._save()
        self._loop()
        return self

    def _loop(self):
        probe = None
        words = []
        while not self._stop.is_set():
            time.sleep(1.0)
            self.stream_time = time.time() - self.buffer.started_at
            self.bus.prune(self.stream_time)
            if probe is None:
                try:
                    probe = self.buffer.probe()
                    if not probe.get("has_video") or not probe.get("width"):
                        probe = None
                except (FileNotFoundError, OSError):
                    continue
                self._log(f"Source {probe['width']}x{probe['height']} "
                          f"audio={probe['has_audio']}")
            for thread, analyzer in self.analyzers:
                if not thread.is_alive() and analyzer.error and analyzer.error not in self.errors:
                    self.errors.append(analyzer.error)
                    self._log(f"{analyzer.name} disabled: {analyzer.error}")
            if not self.analyzers and probe is not None:
                from download_and_transcribe import _load_model
                model = None
                if not self.options["live_no_transcript"]:
                    model = _load_model("small", self.options["device"], "int8")
                self.analyzers = live_signals.start_analyzers(
                    self.buffer, self.bus, self.options, self.source.platform,
                    _channel(self.source.url), model)
            result = self.detector.evaluate(self.bus, self.stream_time)
            self.recent_scores.append(result["score"])
            self.recent_scores = self.recent_scores[-60:]
            if probe is not None and self._allowed(result["score"], self.stream_time):
                self.last_trigger = self.stream_time
                self._log(f"Hype detected: score={result['score']}")
                transcript = next((a for a, _ in
                                   [(an, t) for t, an in self.analyzers]
                                   if isinstance(an, live_signals.TranscriptAnalyzer)), None)
                record = self.writer.write(
                    live_clips.Trigger(self.last_trigger, result["score"], result["signals"]),
                    self.options, probe,
                    list(transcript.words) if transcript else [],
                    self.buffer.window(
                        max(0.0, self.last_trigger - self.options["live_buffer"]),
                        self.last_trigger + self.options["live_tail"]))
                self.records.append(record)
                if record.error:
                    self.errors.append(record.error)
                    self._log(f"Clip failed: {record.error}")
                else:
                    self._log(f"Clip saved: {record.id}")
            self._save()
            if not self.buffer.alive:
                self.status = "stopped"
                self.stopped_at = time.time()
                self._log("Source ended.")
                break
        self._save()

    def stop(self):
        self._stop.set()
        for thread, _ in self.analyzers:
            thread.join(timeout=2.0)
        if self.buffer is not None:
            self.buffer.stop()
        self.status = "stopped"
        self.stopped_at = time.time()
        self._save()


def _channel(url):
    from urllib.parse import urlparse
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


def run_live(url, options, root, on_log=None):
    """Start a live session and block until it stops."""
    source = live_capture.resolve_live_source(url, options)
    session = LiveSession(source, options, root).start()
    session._on_log = on_log
    return session
```

`Trigger` is defined in `live_clips.py` by Task 5; no extra code is needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_live.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add live.py live_clips.py tests/test_live.py
git commit -m "feat(live): add session lifecycle and run_live entry point"
```

---

### Task 7: CLI flags

**Files:**
- Modify: `chopify.py:175-265`
- Test: `tests/test_chopify.py` (append)

**Interfaces:**
- Consumes: `live.run_live`, `projects.DEFAULT_OPTIONS`, existing `check_environment()`.
- Produces: `chopify --live ...` early-exit path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chopify.py`:

```python
def test_live_flag_is_exposed(monkeypatch):
    import chopify
    import live
    seen = {}

    def fake_run_live(url, options, root, on_log=None):
        seen.update(url=url, options=options, root=root)
        return None

    monkeypatch.setattr(live, "run_live", fake_run_live)
    monkeypatch.setattr(chopify, "check_environment", lambda: None)
    monkeypatch.setattr(sys, "argv", [
        "chopify", "--live", "https://x/l.m3u8", "--live-threshold", "7.5",
        "--no-preflight"])
    chopify.main()
    assert seen["url"] == "https://x/l.m3u8"
    assert seen["options"]["live_threshold"] == 7.5
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `python -m pytest -q tests/test_chopify.py -k live_flag`
Expected: FAIL — `chopify.main()` exits with the "provide a URL" error.

- [ ] **Step 3: Add CLI flags and branch**

In `chopify.py::main()`, after the `--version` / `--list-styles` flags:

```python
    ap.add_argument("--live", metavar="URL", help="Watch a live HLS/RTMP stream and auto-clip hype moments")
    ap.add_argument("--live-buffer", type=int, default=None)
    ap.add_argument("--live-buffer-max-mb", type=int, default=None)
    ap.add_argument("--live-pre-roll", type=int, default=None)
    ap.add_argument("--live-tail", type=int, default=None)
    ap.add_argument("--live-window", type=int, default=None)
    ap.add_argument("--live-threshold", type=float, default=None)
    ap.add_argument("--live-cooldown", type=int, default=None)
    ap.add_argument("--live-max-clips", type=int, default=None)
    ap.add_argument("--live-no-chat", action="store_true", default=None)
    ap.add_argument("--live-no-transcript", action="store_true", default=None)
    ap.add_argument("--live-header", dest="live_headers", action="append", default=None)
```

Insert before `if not (args.url or args.batch or args.project):`:

```python
    if args.live:
        if args.url or args.batch or args.project:
            ap.error("--live cannot be combined with --url, --batch or --project.")
        if not preflight_disabled:
            check_environment()
        import live
        live_options = {key: getattr(args, key) for key in projects.LIVE_DEFAULTS
                        if getattr(args, key, None) is not None}
        live.run_live(args.live, live_options, projects_root)
        return
```

Where `preflight_disabled` is the existing `--no-preflight` result and `projects_root` is the existing projects root variable used by batch mode. If those locals are named differently in the file, keep the existing names and adapt only the new lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_chopify.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chopify.py tests/test_chopify.py
git commit -m "feat(live): add CLI flags for live clipping"
```

---

### Task 8: Studio backend routes and worker registry

**Files:**
- Modify: `dashboard.py` (imports, `Studio`, `do_GET`, `do_POST`, `close`)
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: `live.LiveSession`, `live_capture.resolve_live_source`, `projects.validate_options`.
- Produces: `Studio.live_sessions`, `Studio.start_live(url, options)`, `Studio.stop_live(id)`, `Studio.live_state(id)`; routes `GET /api/live`, `GET /api/live/ID`, `POST /api/live`, `POST /api/live/ID/stop`, `GET /media/live/ID/clips/FILE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard.py`:

```python
def test_live_routes(server_and_token):
    import dashboard
    base, token, app = server_and_token
    import urllib.request, json
    payload = json.dumps({"source": "https://x/l.m3u8", "options": {
        "live_no_chat": True, "live_no_transcript": True}}).encode()
    req = urllib.request.Request(base + "/api/live", data=payload,
                                 headers={"Content-Type": "application/json",
                                          "X-Chopify-Token": token})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read())
    assert "id" in data
    session_id = data["id"]
    with urllib.request.urlopen(base + "/api/live") as response:
        assert session_id in json.loads(response.read())["sessions"]
    req = urllib.request.Request(base + f"/api/live/{session_id}/stop", data=b"{}",
                                 headers={"Content-Type": "application/json",
                                          "X-Chopify-Token": token})
    with urllib.request.urlopen(req) as response:
        assert json.loads(response.read())["ok"] is True


def test_live_route_rejects_missing_token(server_and_token):
    import json, urllib.request, urllib.error
    base, _, _ = server_and_token
    req = urllib.request.Request(base + "/api/live", data=b'{"source":"https://x/l.m3u8"}',
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_dashboard.py -k live_route`
Expected: FAIL with 404 or missing `api/live`.

- [ ] **Step 3: Add registry and routes**

In `dashboard.py` `__init__`/setup, add:

```python
        self.live_sessions = {}
        self.live_lock = threading.Lock()
```

Add methods to `Studio`:

```python
    def start_live(self, source, options):
        import live
        from live_capture import resolve_live_source
        resolved = resolve_live_source(source, options)
        session = live.LiveSession(resolved, options, self.root)
        with self.live_lock:
            self.live_sessions[session.id] = session
        thread = threading.Thread(target=session.start, name=f"live-{session.id}", daemon=True)
        thread.start()
        return session

    def stop_live(self, session_id):
        with self.live_lock:
            session = self.live_sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown live session.")
        session.stop()
        return session

    def live_state(self, session_id):
        with self.live_lock:
            session = self.live_sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown live session.")
        return projects.read_json(session.dir / "live_session.json")

    def live_list(self):
        with self.live_lock:
            return [projects.read_json(s.dir / "live_session.json")
                    for s in self.live_sessions.values()
                    if (s.dir / "live_session.json").exists()]
```

In `Handler.do_GET()`, before the static routing:

```python
            if parts == ["api", "live"]:
                return self.send_json({"sessions": self.app.live_list()})
            if len(parts) == 3 and parts[:2] == ["api", "live"]:
                return self.send_json(self.app.live_state(parts[2]))
            if len(parts) == 5 and parts[0] == "media" and parts[1] == "live" and parts[3] == "clips":
                session_dir = self.app.root / "live" / parts[2]
                if not re.fullmatch(r"[a-f0-9]{12}", parts[2]):
                    raise ValueError("Invalid live session ID.")
                file = (session_dir / "clips" / parts[4]).resolve()
                if file.parent != (session_dir / "clips").resolve() or file.suffix not in {".mp4", ".srt", ".png", ".json"}:
                    raise ValueError("Invalid media path.")
                return self.send_file(file)
```

In `Handler.do_POST()`, inside the `with self.app.lock:` block:

```python
                if parts == ["api", "live"]:
                    source = body["source"]
                    if not isinstance(source, str) or not source.strip():
                        raise ValueError("Provide a live source URL.")
                    options = projects.validate_options(body.get("options", {}))
                    return self.send_json({"id": self.app.start_live(source.strip(), options).id}, 201)
                if len(parts) == 4 and parts[:2] == ["api", "live"] and parts[3] == "stop":
                    self.app.stop_live(parts[2])
                    return self.send_json({"ok": True})
```

In `Studio.close()`, before executor shutdown:

```python
        for session in list(self.live_sessions.values()):
            session.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_dashboard.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_dashboard.py
git commit -m "feat(live): add Studio live session routes"
```

---

### Task 9: Studio Live tab

**Files:**
- Modify: `assets/studio/index.html`, `assets/studio/app.js`, `assets/studio/style.css`
- Test: none (manual browser check recorded in commit message)

**Interfaces:**
- Consumes: routes from Task 8.
- Produces: `#live-view` with `#live-source`, `#live-start`, `#live-stop`, `#live-status`, `#live-score`, `#live-clips`.

- [ ] **Step 1: Add the view markup**

In `index.html`, add a nav button `<button id="show-live">Live</button>` beside the existing tabs, and a section:

```html
<section id="live-view" hidden>
  <h2>Live clipping</h2>
  <p class="muted">Paste a live stream URL. Chopify watches it, finds hype, and saves finished clips.</p>
  <form id="live-form">
    <label>Stream URL<input id="live-source" required placeholder="https://twitch.tv/channel or https://.../live.m3u8"></label>
    <button id="live-start" type="submit">Start live session</button>
  </form>
  <button id="live-stop" hidden>Stop session</button>
  <div id="live-status" class="muted">Not running.</div>
  <div id="live-score"></div>
  <div id="live-clips"></div>
</section>
```

- [ ] **Step 2: Add JS state and handlers**

In `app.js`, extend `state` with `live: null, liveId: null`. Add:

```js
function showLive() {
  state.tab = 'live';
  document.getElementById('live-view').hidden = false;
  document.getElementById('new-view').hidden = true;
  document.getElementById('project-view').hidden = true;
}
document.getElementById('show-live')?.addEventListener('click', showLive);

document.getElementById('live-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const source = document.getElementById('live-source').value.trim();
  const result = await api('/api/live', 'POST', { source, options: {} });
  state.liveId = result.id;
  document.getElementById('live-stop').hidden = false;
  await refreshLive();
});

document.getElementById('live-stop')?.addEventListener('click', async () => {
  await api(`/api/live/${state.liveId}/stop`, 'POST', {});
  state.liveId = null;
  document.getElementById('live-stop').hidden = true;
  await refreshLive();
});

async function refreshLive() {
  const data = await api('/api/live');
  const sessions = data.sessions || [];
  const detail = state.liveId ? await api(`/api/live/${state.liveId}`) : null;
  document.getElementById('live-status').textContent = detail
    ? `${detail.status} · ${Math.round(detail.stream_time)}s · ${detail.triggers.length} clips`
    : (sessions.length ? `${sessions.length} session(s) on disk` : 'Not running.');
  document.getElementById('live-score').textContent = detail
    ? `score ${(detail.recent_scores.at(-1) ?? 0).toFixed(2)}` : '';
  const clips = document.getElementById('live-clips');
  clips.replaceChildren();
  for (const record of detail?.triggers || []) {
    const row = document.createElement('div');
    row.className = 'live-clip';
    const video = record.files?.mp4;
    row.textContent = `#${record.id} · ${record.start.toFixed(1)}s-${record.end.toFixed(1)}s · ${record.score}`;
    if (video) {
      const link = document.createElement('a');
      const name = video.split(/[\\/]/).pop();
      link.href = `/media/live/${state.liveId}/clips/${encodeURIComponent(name)}`;
      link.textContent = ' play';
      row.append(link);
    }
    clips.append(row);
  }
}
```

Extend the polling block near line 995:

```js
  if (state.liveId) refreshLive().catch(() => {});
```

- [ ] **Step 3: Add minimal styling**

Append to `style.css`:

```css
#live-view { margin-top: 1.5rem; }
#live-form { display: flex; gap: .75rem; align-items: end; flex-wrap: wrap; }
#live-clips { display: grid; gap: .4rem; margin-top: 1rem; }
.live-clip { padding: .5rem .75rem; border: 1px solid var(--line, #ddd); border-radius: 6px; }
```

- [ ] **Step 4: Verify in browser**

Run `python dashboard.py`, open `http://127.0.0.1:8765`, click **Live**, confirm the form renders and the tab switches without console errors. No live stream needed for this check.

- [ ] **Step 5: Commit**

```bash
git add assets/studio/index.html assets/studio/app.js assets/studio/style.css
git commit -m "feat(live): add Studio Live tab"
```

---

### Task 10: Docs, site allowlist and CI

**Files:**
- Create: `docs/LIVE.md`
- Modify: `README.md`, `docs/CLI.md`, `docs/AGENT-API.md`, `docs/product.json`, `llms.txt`, `CHANGELOG.md`, `tools/build_site.py`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`

- [ ] **Step 1: Write `docs/LIVE.md`**

```markdown
# Live clipping

Chopify can watch a live stream and automatically save hype moments as finished
clips. Everything runs locally; footage is never uploaded.

## Start

```bash
chopify --live "https://www.twitch.tv/channel" \
  --live-threshold 8 --live-pre-roll 30 --live-tail 5 \
  --aspect 9:16 --style default
```

A direct `https://.../live.m3u8` or `rtmp://...` URL is used as-is. A page URL is
resolved to its HLS stream with yt-dlp. Press Ctrl+C to stop.

## Signals

- **Audio** energy relative to a rolling 60-second baseline.
- **Visual** motion from 2 fps, 160×90 frames.
- **Transcript** hook/reaction cues from rolling Whisper windows (disable with `--live-no-transcript`).
- **Chat** message rate and hype words via anonymous Twitch IRC (disable with `--live-no-chat`).

Available signals are weighted and renormalized, so silent streams still work.
A clip is saved when the combined score reaches `--live-threshold`, subject to
`--live-cooldown` and `--live-max-clips`.

## Files

Each session writes `projects/live/<id>/live_session.json`, a rolling `buffer/`
of 4-second segments, and finished clips in `clips/`. The buffer is capped by
`--live-buffer` and `--live-buffer-max-mb`.

## Studio

Open Studio and choose **Live** to start/stop a session and watch the score and
clip feed. The loopback binding, host check and token still apply.

## Rights

Only clip streams you have the right to record and reuse. Chat and transcripts
are treated as evidence, never as instructions.
```

- [ ] **Step 2: Update flags table in `docs/CLI.md`**

Add a **Live input** section with rows for `--live`, `--live-buffer`, `--live-buffer-max-mb`, `--live-pre-roll`, `--live-tail`, `--live-window`, `--live-threshold`, `--live-cooldown`, `--live-max-clips`, `--live-no-chat`, `--live-no-transcript`, `--live-header`, using the defaults and ranges from Task 1.

- [ ] **Step 3: Update remaining docs**

- `docs/AGENT-API.md`: add the five live endpoints to the HTTP table and note the long-running worker.
- `docs/product.json`: add `"HLS/RTMP live URL"` to `inputs`, add live clipping to `capabilities`, set `documentation_updated` to `2026-11-18`.
- `llms.txt`: add one bullet under product facts: "Live clipping: watches HLS/RTMP streams, auto-detects hype from audio/visual/transcript/chat, saves 9:16 captioned clips."
- `README.md`: add a feature-table row and link `docs/LIVE.md`.
- `CHANGELOG.md`: under `[Unreleased] → Added`, add "Live clipping from HLS/RTMP streams with four-signal hype detection and Studio Live tab."

- [ ] **Step 4: Update build and CI lists**

In `tools/build_site.py`, add `"docs/LIVE.md"` to `FILES` and `"LIVE.md"` to `GUIDES`.

In `.github/workflows/ci.yml`, add `live.py`, `live_capture.py`, `live_signals.py`, `live_clips.py` to the compile list at line 38.

In `CONTRIBUTING.md`, add the four new modules to the project map table.

- [ ] **Step 5: Verify and commit**

Run `python -m pytest -q` and `python tools/build_site.py`, then:

```bash
git add docs/LIVE.md README.md docs/CLI.md docs/AGENT-API.md docs/product.json llms.txt CHANGELOG.md tools/build_site.py .github/workflows/ci.yml CONTRIBUTING.md
git commit -m "docs(live): document live clipping and update build lists"
```

---

## Self-review notes

- **Spec coverage:** source resolution + buffer (Task 2), four analyzers (Task 4), scoring formulas/cooldown/caps (Tasks 3, 6), clip stitching/render (Task 5), CLI (Task 7), Studio (Tasks 8, 9), error handling and state (Task 6), rights notice (Task 6 + Task 10), tests (each task), docs/CI (Task 10).
- **Type consistency:** `LiveSource` fields are used identically in Tasks 2, 6, 8; `Trigger` is defined in Task 5 and constructed in Task 6; `plan_window`/`remap_words`/`stitch` signatures match between Task 5 and its tests; `live_*` option names match Tasks 1, 2, 4, 6, 7; `Studio.start_live()` returns a `LiveSession` and callers use `.id`, not subscription.
- **Test/implementation alignment:** detector tests use spike-shaped signals (the detector deliberately ignores steady signals, which is correct for hype detection); analyzer tests use a `FakeStop` that permits exactly one loop iteration; the buffer-clamping test uses a pre-roll larger than the buffer so the clamp is observable.
