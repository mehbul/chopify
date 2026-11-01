"""Unit tests for the local audio evidence timeline."""
import shutil
import subprocess

import numpy as np
import pytest

import evidence


def tone(seconds, amplitude=0.001):
    t = np.arange(int(evidence.SAMPLE_RATE * seconds)) / evidence.SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def noise(seconds, amplitude, seed=7):
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(int(evidence.SAMPLE_RATE * seconds))
            ).astype(np.float32)


def test_timeline_is_one_value_per_second():
    samples = np.concatenate([tone(2), noise(1, 0.8), tone(2)])
    timeline = evidence.features_from_samples(samples)
    assert timeline.duration == pytest.approx(5.0)
    assert len(timeline.rms) == 5
    assert len(timeline.burst) == 5


def test_loud_burst_stands_out_from_quiet_source():
    samples = np.concatenate([tone(3), noise(1, 0.8), tone(2)])
    timeline = evidence.features_from_samples(samples)
    assert timeline.rms[3] > timeline.rms[0] + 20
    assert timeline.burst[3]
    assert not timeline.burst[0]


def test_spectral_flux_and_high_band_capture_transients():
    samples = np.concatenate([tone(3), noise(1, 0.8), tone(2)])
    timeline = evidence.features_from_samples(samples)
    assert timeline.flux[3] > timeline.flux[0]
    assert timeline.high_band[3] > timeline.high_band[0]


def test_timeline_cache_round_trip(tmp_path):
    timeline = evidence.features_from_samples(np.concatenate([tone(2), noise(1, 0.5)]))
    path = tmp_path / "audio_features.npz"
    evidence.save_timeline(timeline, path, identity="abc")
    loaded = evidence.load_timeline(path, identity="abc")
    assert loaded is not None
    assert loaded.duration == pytest.approx(timeline.duration)
    assert np.allclose(loaded.rms, timeline.rms)
    assert evidence.load_timeline(path, identity="other") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_build_audio_timeline_decodes_a_wav(tmp_path):
    wav = tmp_path / "input.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "sine=frequency=220:duration=2", str(wav),
    ], check=True)
    timeline = evidence.build_audio_timeline(wav)
    assert timeline is not None
    assert timeline.duration == pytest.approx(2.0, abs=0.1)
    assert len(timeline.rms) == 2
