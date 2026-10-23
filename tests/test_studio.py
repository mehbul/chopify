"""Behavior contracts for local projects and editing workflows."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def transcript():
    return {"video": "source.mp4", "duration": 10.0, "language": "en",
            "words": [{"word": "Hello", "start": 1.0, "end": 1.4},
                      {"word": "world.", "start": 1.5, "end": 2.0}]}


def test_local_input_bypasses_download(tmp_path, monkeypatch):
    import download_and_transcribe as dt
    source = tmp_path / "my recording.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(dt, "download", lambda *a: pytest.fail("downloaded local file"))
    assert dt.resolve_source(str(source), tmp_path / "work") == source.resolve()


def test_srt_uses_clip_relative_times(tmp_path):
    import captions
    out = tmp_path / "clip.srt"
    captions.write_srt(transcript()["words"], 1.2, 1.8, out)
    assert out.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:00,600\nHello world.\n")


def test_caption_edits_rebuild_text_and_reject_bad_times():
    import captions
    data = transcript()
    edited = captions.update_transcript(data, [
        {"word": "Hi", "start": 1.0, "end": 1.2},
        {"word": "Chopify!", "start": 1.5, "end": 2.0}])
    assert edited["segments"][0]["text"] == "Hi Chopify!"
    assert data["words"][0]["word"] == "Hello"
    for invalid in [float("nan"), -1, 11]:
        with pytest.raises(ValueError):
            captions.update_transcript(data, [{"word": "bad", "start": invalid, "end": 2}])
    with pytest.raises(ValueError):
        captions.update_transcript(data, list(reversed(data["words"])))


def test_brand_rejects_filter_injection_and_resolves_logo(tmp_path):
    import branding
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    preset = tmp_path / "brand.json"
    preset.write_text(json.dumps({"font": "Arial", "highlight": "#12AB34", "logo": "logo.png"}))
    brand = branding.load_brand(preset)
    assert brand["logo"] == str(logo.resolve())
    assert brand["highlight"] == "&H0034AB12&"
    preset.write_text(json.dumps({"font": "Arial,evil\nStyle: injected"}))
    with pytest.raises(ValueError):
        branding.load_brand(preset)


def test_semantic_search_ranks_meaning_and_caches_documents(tmp_path):
    import semantic
    sentences = [
        {"text": "A banana is yellow.", "start": 0, "end": 3},
        {"text": "Keep trying after people say no.", "start": 4, "end": 8}]
    calls = []
    def embed(texts, model):
        calls.append(list(texts))
        return [[0, 1], [1, 0]] if len(texts) == 2 else [[1, 0]]
    got = semantic.search(sentences, "handling rejection", "test", tmp_path, embed=embed)
    assert got[0]["index"] == 1
    semantic.search(sentences, "persistence", "test", tmp_path, embed=embed)
    assert len([c for c in calls if len(c) == 2]) == 1
    sentences[1]["text"] = "A new sentence."
    semantic.search(sentences, "persistence", "test", tmp_path, embed=embed)
    assert len([c for c in calls if len(c) == 2]) == 2


def test_project_ids_isolate_sources_and_reject_traversal(tmp_path):
    import projects
    a = projects.create_project(tmp_path, "https://example.com/a", {})
    b = projects.create_project(tmp_path, "https://example.com/b", {})
    assert a["id"] != b["id"]
    assert projects.project_path(tmp_path, a["id"]).parent == tmp_path
    with pytest.raises(ValueError):
        projects.project_path(tmp_path, "../outside")


def test_resume_reuses_stages_and_invalidates_changed_settings(tmp_path, monkeypatch):
    import projects
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    project = projects.create_project(tmp_path / "projects", str(source), {"min_len": 1})
    path = projects.project_path(tmp_path / "projects", project["id"])
    calls = []
    def transcribe(video, workdir, *args):
        calls.append("transcribe")
        data = transcript()
        data["video"] = str(video)
        (workdir / "transcript.json").write_text(json.dumps(data))
    def score(workdir, **kwargs):
        calls.append("score")
        segments = [{"start": 1, "end": 2, "hook": "same", "overall": 5}]
        (workdir / "segments.json").write_text(json.dumps(segments))
        return segments, "heuristic"
    monkeypatch.setattr(projects.dt, "transcribe", transcribe)
    monkeypatch.setattr(projects.score_clips, "run", score)
    projects.run_project(path, action="prepare")
    projects.run_project(path, action="prepare")
    assert calls == ["transcribe", "score"]
    state = projects.read_project(path)
    state["options"]["min_len"] = 2
    projects.save_project(path, state)
    projects.run_project(path, action="prepare")
    assert calls == ["transcribe", "score", "score"]
    source.write_bytes(b"changed video")
    projects.run_project(path, action="prepare")
    assert calls[-2:] == ["transcribe", "score"]


def test_batch_continues_after_failure_and_uses_separate_projects(tmp_path):
    import projects
    seen = []
    def runner(path, **kwargs):
        seen.append(path)
        if len(seen) == 1:
            raise RuntimeError("broken input")
        return {"status": "complete"}
    results = projects.run_batch(["first.mp4", "second.mp4"], tmp_path, {}, runner=runner)
    assert [r["status"] for r in results] == ["failed", "complete"]
    assert seen[0] != seen[1]


def test_saved_local_source_is_absolute_for_later_resume(tmp_path, monkeypatch):
    import projects
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"video")
    monkeypatch.chdir(tmp_path)
    state = projects.create_project(tmp_path / "projects", "recording.mp4", {})
    assert state["source"] == str(source.resolve())


def test_invalid_embedding_shapes_and_nonfinite_scores_are_rejected():
    import projects
    import semantic
    for vectors in [[None], [42], [[0, 0]], [[float("nan"), 1]]]:
        with pytest.raises(ValueError):
            semantic.validate_vectors(vectors, 1)
    with pytest.raises(ValueError):
        projects.validate_segments([{"start": 0, "end": 1, "overall": float("nan")}], 2)
