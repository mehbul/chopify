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


# ------------------------------------------------------------ evidence scoring

def candidate(start, end, score=7.5):
    return {"start": start, "end": end, "duration": end - start,
            "overall": score, "raw_score": score, "reason": "text"}


def test_audio_evidence_prefers_a_loud_reaction():
    samples = np.concatenate([tone(4), noise(1, 0.8), tone(4)])
    timeline = evidence.features_from_samples(samples)
    quiet = evidence.audio_evidence(timeline, 0, 4)
    loud = evidence.audio_evidence(timeline, 3, 5)
    assert loud["score"] > quiet["score"] + 1.0
    assert 0 <= loud["score"] <= 10


def test_audio_evidence_is_neutral_for_missing_timeline():
    got = evidence.audio_evidence(None, 0, 10)
    assert got["score"] == 5.0


def test_profile_thresholds():
    assert evidence.content_profile(2.5, 0.1, 2, 0.01) == "talk"
    assert evidence.content_profile(0.4, 0.7, 20, 0.2) == "gameplay/action"
    assert evidence.content_profile(1.7, 0.4, 5, 0.05) == "mixed"


def test_profile_bands_and_weight_floor():
    assert evidence.profile_band("talk") == (45, 180)
    assert evidence.profile_band("mixed") == (30, 120)
    assert evidence.profile_band("gameplay/action") == (15, 60)
    assert evidence.evidence_weight(0.0, "talk") == pytest.approx(0.20)
    assert evidence.evidence_weight(1.0, "gameplay/action") == pytest.approx(0.65)
    assert evidence.evidence_weight(0.0, "mixed", sparse=True) == pytest.approx(0.75)


def test_sparse_text_uses_floor_and_raised_weight():
    got = evidence.apply_evidence(
        candidate(0, 10, 4.2), audio=8.0, profile="mixed",
        source_energy=0.5, words_per_second=0.4)
    assert got["raw_score"] == pytest.approx(4.2)
    assert got["overall"] > 7.0
    assert got["evidence"]["weight"] == pytest.approx(0.75)
    assert got["evidence"]["text_floor"] == 7.0


def test_combine_evidence_shrinks_single_modality():
    assert evidence.combine_evidence(8.0, 8.0) == pytest.approx(8.0)
    assert evidence.combine_evidence(8.0, None) == pytest.approx(7.55)
    assert evidence.combine_evidence(None, None) == pytest.approx(5.0)

