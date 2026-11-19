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
