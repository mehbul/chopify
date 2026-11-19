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
