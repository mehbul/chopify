"""Tests that format profile/band and detection reach discovery stats."""
import score_clips


def test_discover_records_detection_and_honours_profile(monkeypatch, tmp_path):
    seen = {}

    def fake_source_evidence(workdir, data):
        class Timeline:
            duration = 10.0
            variability = 0.5
            burst_rate = 0.0
        return Timeline(), "talk", 0.5, 0.5, True, None

    def fake_save(workdir, candidates, stats, mode=None):
        seen["stats"] = stats

    monkeypatch.setattr(score_clips, "source_evidence", fake_source_evidence)
    monkeypatch.setattr(score_clips, "load_transcript", lambda w: {
        "video": "x.mp4", "words": [{"text": "hello world", "start": 0, "end": 1}],
        "duration": 10})
    monkeypatch.setattr(score_clips, "sentenceize", lambda words: [
        {"text": "hello world", "start": 0, "end": 1, "words": words}])
    monkeypatch.setattr(score_clips, "attach_audio_evidence", lambda c, t, p, e: c)
    monkeypatch.setattr(score_clips, "visual_shortlist", lambda *a: [])
    monkeypatch.setattr(score_clips.visual, "merge_ranges", lambda *a: [])
    monkeypatch.setattr(score_clips.visual, "load_or_analyze", lambda *a: [])
    monkeypatch.setattr(score_clips, "attach_media_evidence",
                        lambda c, s, t, p, e, au, mn, mx: c)
    monkeypatch.setattr(score_clips, "save_candidates", fake_save)

    detection = {"type": "gaming", "confidence": 0.9,
                 "needs_confirmation": False, "signals": {}, "reason": "test"}
    format_info = {"name": "gaming", "source": "detected"}
    score_clips.discover(tmp_path, band=(15, 60), profile="gameplay/action",
                         detection=detection, format_info=format_info)
    stats = seen["stats"]
    assert stats["content_profile"] == "gameplay/action"
    assert stats["video_type"] == "gaming"
    assert stats["format"] == "gaming"
    assert stats["format_source"] == "detected"
    assert stats["detection"]["type"] == "gaming"
