"""Block-scanned discovery keeps long transcripts bounded and resumable."""
import json

import score_clips


def long_sentences(duration=7200):
    words = []
    for i in range(0, duration, 30):
        words.extend([{"word": f"w{i}.", "start": i, "end": i + 20}])
    return score_clips.sentenceize(words)


def test_block_scan_merges_candidates_and_writes_parts(tmp_path):
    sentences = long_sentences()
    candidates, stats = score_clips.discover_blocks(
        sentences, 15, 60, duration=7200, workdir=tmp_path)
    assert stats["seeds"] == 240
    assert stats["blocks"] == 4
    assert stats["block_scan"] is True
    assert len(candidates) >= stats["seeds"]
    assert len(list(tmp_path.glob("candidates.part*.json"))) == 4

    again, _ = score_clips.discover_blocks(
        sentences, 15, 60, duration=7200, workdir=tmp_path)
    assert [(item["start"], item["end"]) for item in again] == [
        (item["start"], item["end"]) for item in candidates]


def test_block_scan_reuses_parts_until_transcript_changes(tmp_path, monkeypatch):
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"words": []}), encoding="utf-8")
    sentences = long_sentences()
    calls = []
    original = score_clips.discover_candidates

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(score_clips, "discover_candidates", counting)
    score_clips.discover_blocks(sentences, 15, 60, duration=7200,
                                workdir=tmp_path)
    assert len(calls) == 4
    score_clips.discover_blocks(sentences, 15, 60, duration=7200,
                                workdir=tmp_path)
    assert len(calls) == 4
    transcript.write_text(json.dumps({"words": [{"word": "changed"}]}),
                          encoding="utf-8")
    score_clips.discover_blocks(sentences, 15, 60, duration=7200,
                                workdir=tmp_path)
    assert len(calls) == 8


def test_block_scan_caps_windows_at_max_len_across_boundaries(tmp_path):
    sentences = long_sentences()
    candidates, _ = score_clips.discover_blocks(
        sentences, 15, 60, duration=7200, workdir=tmp_path)
    assert all(item["duration"] <= 60 + 1e-6 for item in candidates)
    assert candidates[0]["start"] < 1800
    assert candidates[-1]["end"] >= 7190


def test_long_discover_uses_block_scan(monkeypatch, tmp_path):
    data = {"video": "fake.mp4", "duration": 7200, "words": [
        {"word": "Hello.", "start": 0, "end": 1}]}
    calls = []
    monkeypatch.setattr(score_clips, "load_transcript", lambda path: data)
    monkeypatch.setattr(score_clips, "source_evidence",
                        lambda *args: (None, "talk", 0.5, 1.0, False, None))
    monkeypatch.setattr(
        score_clips, "discover_blocks",
        lambda *args, **kwargs: (calls.append("blocks") or [],
                                 {"seeds": 0, "blocks": 4}))
    monkeypatch.setattr(
        score_clips, "discover_candidates",
        lambda *args, **kwargs: (calls.append("single") or [],
                                 {"seeds": 0}))
    score_clips.discover(tmp_path, min_len=15, max_len=60)
    assert calls == ["blocks"]


def test_short_discover_keeps_single_scan(monkeypatch, tmp_path):
    data = {"video": "fake.mp4", "duration": 1800, "words": [
        {"word": "Hello.", "start": 0, "end": 1}]}
    calls = []
    monkeypatch.setattr(score_clips, "load_transcript", lambda path: data)
    monkeypatch.setattr(score_clips, "source_evidence",
                        lambda *args: (None, "talk", 0.5, 1.0, False, None))
    monkeypatch.setattr(
        score_clips, "discover_blocks",
        lambda *args, **kwargs: (calls.append("blocks") or [], {"seeds": 0}))
    monkeypatch.setattr(
        score_clips, "discover_candidates",
        lambda *args, **kwargs: (calls.append("single") or [], {"seeds": 0}))
    score_clips.discover(tmp_path, min_len=15, max_len=60)
    assert calls == ["single"]
