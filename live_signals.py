"""Rolling signal bus and hype scoring for live sessions."""
from __future__ import annotations

import math
import re
import socket
import statistics
import subprocess
import threading
import time
from collections import defaultdict, deque

import numpy as np

import download_and_transcribe as dt
import evidence
import score_clips
import visual

DEFAULT_WEIGHTS = {"audio": 0.30, "visual": 0.25,
                   "transcript": 0.25, "chat": 0.20}
SIGNALS = tuple(DEFAULT_WEIGHTS)
# Minimum dynamic range used as the denominator when a signal is steady.
SCALES = {"audio": 0.05, "visual": 0.05, "transcript": 1.0, "chat": 2.0}

# Best-effort ad-slate detection: flat fields with strong magenta/cyan tint.
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
            while queue and queue[0][0] <= cutoff:
                queue.popleft()

    def prune(self, now, retention=None):
        cutoff = float(now) - float(retention if retention is not None else self.retention)
        with self._lock:
            for queue in self._samples.values():
                while queue and queue[0][0] <= cutoff:
                    queue.popleft()

    def values(self, name, t, window):
        with self._lock:
            return [value for stamp, value in self._samples.get(name, ())
                    if t - float(window) < stamp <= t + 1e-9]

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
        ready = getattr(self.buffer, "ready_segments", None)
        segments = ready() if callable(ready) else self.buffer.segments()
        return [p for p in segments
                if self.buffer.index_of(p) not in self._seen]

    def _decode(self, path):
        raise NotImplementedError

    def _publish(self, index, payload):
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

    def __init__(self, buffer, bus):
        super().__init__(buffer, bus)
        self.ad_like = False

    def _mark_ad(self, frame):
        if looks_like_ad_slate(frame):
            self.ad_like = True

    def _decode(self, path):
        cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
               "fps=2,scale=160:90", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "pipe:1"]
        result = subprocess.run(cmd, capture_output=True, check=True)
        raw = result.stdout
        frame_size = 160 * 90 * 3
        count = len(raw) // frame_size
        return [np.frombuffer(raw[i * frame_size:(i + 1) * frame_size],
                              dtype=np.uint8).reshape(90, 160, 3)
                for i in range(count)]

    def _publish(self, index, frames):
        if not frames:
            return
        for frame in frames:
            self._mark_ad(frame)
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
