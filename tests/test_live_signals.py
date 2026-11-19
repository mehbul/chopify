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


from pathlib import Path

import numpy as np

import live_signals


class FakeSegment:
    def __init__(self, index):
        self.name = f"segment{index:08d}.ts"
        self.index = index


class FakeBuffer:
    def __init__(self, segments=()):
        self._segments = list(segments)
        self.started_at = 0.0
        self.directory = Path(".")

    def segments(self):
        return self._segments

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


def test_ad_slate_detection():
    flat = np.zeros((90, 160, 3), dtype=np.uint8)
    flat[:, :, 0] = 200
    flat[:, :, 2] = 220
    assert live_signals.looks_like_ad_slate(flat)
    noisy = np.random.RandomState(1).randint(0, 255, (90, 160, 3)).astype(np.uint8)
    assert not live_signals.looks_like_ad_slate(noisy)


def test_analyzer_tracks_ad_like():
    analyzer = live_signals.VisualAnalyzer.__new__(live_signals.VisualAnalyzer)
    analyzer.ad_like = False
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    frame[:, :, 0] = 200
    frame[:, :, 2] = 220
    analyzer._mark_ad(frame)
    assert analyzer.ad_like
