"""Unit tests for the local visual evidence timeline."""
import numpy as np
import pytest

import visual


def flat_frames(count=6):
    return [np.zeros((visual.FRAME_HEIGHT, visual.FRAME_WIDTH, 3),
                     dtype=np.uint8) for _ in range(count)]


def test_flat_frames_have_no_motion_or_cuts():
    features = visual.features_from_frames(flat_frames(), start=10.0)
    assert features.start == pytest.approx(10.0)
    assert features.end == pytest.approx(13.0)
    assert np.allclose(features.motion, 0.0)
    assert not features.cut.any()
    assert not features.face.any()


def test_a_hard_cut_is_detected():
    frames = []
    for index in range(6):
        frame = np.zeros((visual.FRAME_HEIGHT, visual.FRAME_WIDTH, 3),
                         dtype=np.uint8)
        if index >= 3:
            frame[:] = 255
        frames.append(frame)
    features = visual.features_from_frames(frames, start=0.0)
    assert features.cut[3]
    assert features.change[3] > features.change[0]
    assert features.motion[3] > features.motion[0]


def test_cache_round_trip(tmp_path):
    features = visual.features_from_frames(flat_frames(4), start=5.0)
    path = tmp_path / "section.npz"
    visual.save_features(features, path, identity="abc")
    loaded = visual.load_features(path, identity="abc")
    assert loaded is not None
    assert loaded.start == pytest.approx(features.start)
    assert loaded.end == pytest.approx(features.end)
    assert np.allclose(loaded.motion, features.motion)
    assert visual.load_features(path, identity="other") is None


def test_extract_features_decodes_a_generated_video(tmp_path):
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    video = tmp_path / "cut.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=160x90:d=2",
        "-f", "lavfi", "-i", "color=c=white:s=160x90:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
        "-map", "[v]", "-r", "10", "-pix_fmt", "yuv420p", str(video),
    ], check=True)
    features = visual.extract_features(video, 0.0, 4.0)
    assert features is not None
    assert len(features.motion) >= 4
    assert features.change.max() > 0.4


def feature_arrays(motion, change=None, center=None, face=None,
                   face_motion=None, start=0.0):
    motion = np.asarray(motion, dtype=np.float32)
    count = len(motion)
    return visual.VisualFeatures(
        start=start,
        end=start + count / visual.SAMPLE_FPS,
        times=start + np.arange(count) / visual.SAMPLE_FPS,
        motion=motion,
        cut=np.zeros(count, dtype=bool),
        change=(np.asarray(change, dtype=np.float32)
                if change is not None else np.full(count, 0.05, np.float32)),
        center_change=(np.asarray(center, dtype=np.float32)
                       if center is not None
                       else np.full(count, 0.05, np.float32)),
        face=(np.asarray(face, dtype=bool) if face is not None
              else np.zeros(count, dtype=bool)),
        face_motion=(np.asarray(face_motion, dtype=np.float32)
                     if face_motion is not None
                     else np.zeros(count, dtype=np.float32)),
    )


def test_flat_motion_is_capped_below_action_evidence():
    features = feature_arrays([0.05] * 12)
    got = visual.visual_evidence(features, 0.0, features.end)
    assert got["score"] <= 6.0
    assert got["event_arc"] < 0.30


def test_action_arc_beats_flat_motion():
    rising = feature_arrays([0.02] * 4 + [0.3, 0.5, 0.4] + [0.05] * 5)
    flat = feature_arrays([0.05] * 12)
    assert visual.visual_evidence(rising, 0.0, rising.end)["score"] > \
        visual.visual_evidence(flat, 0.0, flat.end)["score"] + 2.0


def test_missing_facecam_is_neutral():
    features = feature_arrays([0.05] * 12)
    got = visual.visual_evidence(features, 0.0, features.end)
    assert got["facecam"] == pytest.approx(0.5)


def test_single_visual_modality_shrinks_toward_neutral():
    assert visual.combine_visual(8.0, None) == pytest.approx(7.55)
    assert visual.combine_visual(8.0, 8.0) == pytest.approx(8.0)


def test_baseline_ranges_are_even_and_cover_the_source():
    ranges = visual.baseline_ranges(60.0)
    assert len(ranges) == 12
    assert ranges[0][0] == pytest.approx(0.0)
    assert ranges[-1][1] == pytest.approx(60.0)
    assert all(end - start == pytest.approx(5.0) for start, end in ranges)


def test_short_source_baseline_range_is_clamped():
    assert visual.baseline_ranges(3.0) == [(0.0, 3.0)]
    assert visual.baseline_ranges(0.0) == []


def test_sample_baseline_caches_and_blends_signals(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")
    calls = []

    def fake_extract(path, start, end):
        calls.append((start, end))
        return feature_arrays([0.1, 0.2, 0.15, 0.18],
                              face=[True, True, False, True], start=start)

    monkeypatch.setattr(visual, "extract_features", fake_extract)
    first = visual.sample_baseline(source, tmp_path, 60.0)
    assert first is not None
    assert first.samples == 12
    assert first.motion_norm > 0
    assert first.facecam_norm == pytest.approx(0.75)
    assert first.value > 0
    before = len(calls)
    second = visual.sample_baseline(source, tmp_path, 60.0)
    assert second.samples == first.samples
    assert second.value == pytest.approx(first.value, abs=1e-3)
    assert second.motion_norm == pytest.approx(first.motion_norm, abs=1e-3)
    assert len(calls) == before
    assert (tmp_path / visual.BASELINE_CACHE).exists()


def test_flat_audio_is_unavailable_not_zero():
    import evidence
    flat = evidence.features_from_samples(
        np.full(evidence.SAMPLE_RATE * 5, 0.001, dtype=np.float32))
    assert not evidence.timeline_has_signal(flat)
    varying = evidence.features_from_samples(np.concatenate([
        np.full(evidence.SAMPLE_RATE * 5, 0.001, dtype=np.float32),
        np.full(evidence.SAMPLE_RATE, 0.8, dtype=np.float32),
    ]))
    assert evidence.timeline_has_signal(varying)


def test_visual_baseline_raises_source_energy_and_profile():
    import evidence
    assert evidence.source_energy(0.2, 0.9) > evidence.source_energy(0.2, 0.1)
    assert evidence.content_profile(0.4, 0.8, 0, 0) == "gameplay/action"


def test_merge_ranges_pads_and_merges_overlapping_candidates():
    merged = visual.merge_ranges([
        {"start": 10.0, "end": 20.0},
        {"start": 21.0, "end": 30.0},
        {"start": 40.0, "end": 50.0},
    ], duration=60.0, pad=2.0)
    assert merged == [(8.0, 32.0), (38.0, 52.0)]
    assert visual.merge_ranges([], duration=60.0) == []


def test_merge_ranges_clamps_to_source_bounds():
    merged = visual.merge_ranges([{"start": 0.0, "end": 14.0}],
                                 duration=15.0, pad=2.0)
    assert merged == [(0.0, 15.0)]


def test_refine_media_boundary_snaps_to_motion_onset():
    times = np.arange(0.0, 20.0, 1.0 / visual.SAMPLE_FPS)
    motion = np.full(len(times), 0.02, dtype=np.float32)
    spike = (times >= 10.0) & (times < 10.5)
    motion[spike] = 0.6
    features = visual.VisualFeatures(
        start=0.0, end=20.0, times=times, motion=motion,
        cut=np.abs(times - 10.0) < 1e-9,
        change=np.full(len(times), 0.03, dtype=np.float32),
        center_change=np.full(len(times), 0.03, dtype=np.float32),
        face=np.zeros(len(times), dtype=bool),
        face_motion=np.zeros(len(times), dtype=np.float32))
    start, end = visual.refine_media_boundary(
        features, None, 9.0, 18.0, 5.0, 20.0)
    assert start == pytest.approx(9.9, abs=0.15)
    assert 5.0 <= end - start <= 20.0


def test_refine_media_boundary_keeps_range_without_evidence():
    assert visual.refine_media_boundary(
        None, None, 5.0, 15.0, 5.0, 20.0) == (5.0, 15.0)
