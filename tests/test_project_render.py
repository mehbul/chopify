"""Exercise retry and cache behavior at the real project/render boundary."""
import json
import subprocess
from pathlib import Path

import pytest
import projects


def ready_project(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    state = projects.create_project(tmp_path / "projects", str(source), {})
    path = projects.project_path(tmp_path / "projects", state["id"])
    projects.atomic_json(path / "transcript.json", {"video": str(source), "duration": 20,
        "words": [{"word": "Hello.", "start": 0, "end": 1}]})
    projects.atomic_json(path / "segments.json", [
        {"start": 0, "end": 5, "hook": "same", "overall": 7},
        {"start": 6, "end": 10, "hook": "same", "overall": 8}])
    return path


def fake_render(segment, *args, **kwargs):
    out = kwargs["out_dir"] / f"{segment['id']}.mp4"
    for ext in (".mp4", ".srt", ".png", ".meta.json"):
        out.with_suffix(ext).write_text("rendered")
    return out


def test_failed_job_retains_successful_clips_for_retry(tmp_path, monkeypatch):
    path = ready_project(tmp_path)
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims", lambda p: (640,360))
    calls = []
    def fail_second(segment, *args, **kwargs):
        calls.append(segment["start"])
        if segment["start"] == 6:
            raise subprocess.CalledProcessError(1, "ffmpeg")
        return fake_render(segment, *args, **kwargs)
    monkeypatch.setattr(projects.render_clips, "render", fail_second)
    with pytest.raises(subprocess.CalledProcessError):
        projects.run_project(path, action="render")
    assert projects.read_project(path)["status"] == "failed"
    assert len(projects.read_project(path)["renders"]) == 1
    def succeed(segment, *args, **kwargs):
        calls.append(segment["start"])
        return fake_render(segment, *args, **kwargs)
    monkeypatch.setattr(projects.render_clips, "render", succeed)
    state = projects.run_project(path, action="render")
    assert calls == [0, 6, 6]
    assert state["status"] == "complete"
    assert len(set(state["last_outputs"])) == 2


def test_style_variants_never_overwrite_cached_outputs(tmp_path, monkeypatch):
    path = ready_project(tmp_path)
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims", lambda p: (640,360))
    monkeypatch.setattr(projects.render_clips, "render", fake_render)
    state = projects.run_project(path, action="render")
    original = state["last_outputs"]
    state["options"]["style"] = "podcast"
    projects.save_project(path, state)
    changed = projects.run_project(path, action="render")["last_outputs"]
    assert not set(original) & set(changed)
    state = projects.read_project(path)
    state["options"]["style"] = "default"
    projects.save_project(path, state)
    assert projects.run_project(path, action="render")["last_outputs"] == original


def test_render_rejects_source_changed_since_transcription(tmp_path, monkeypatch):
    path = ready_project(tmp_path)
    state = projects.read_project(path)
    source = Path(projects.read_json(path / "transcript.json")["video"])
    state["source_identity"] = projects.file_identity(source)
    projects.save_project(path, state)
    source.write_bytes(b"changed video")
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims", lambda p: (640,360))
    monkeypatch.setattr(projects.render_clips, "render", fake_render)
    with pytest.raises(ValueError, match="changed"):
        projects.run_project(path, action="render")


def ready_long_project(tmp_path, segments=None, duration=600):
    source_url = "https://example.com/long"
    audio = tmp_path / "discovery.m4a"
    audio.write_bytes(b"audio")
    state = projects.create_project(tmp_path / "projects", source_url, {})
    path = projects.project_path(tmp_path / "projects", state["id"])
    projects.atomic_json(path / "transcript.json", {
        "video": str(audio), "duration": duration, "render_from_sections": True,
        "words": [{"word": "Hello.", "start": 0, "end": 1}]})
    projects.atomic_json(path / "segments.json", segments or [
        {"start": 100, "end": 140, "hook": "sectioned", "overall": 9}])
    return path


def fake_section_render(segment, *args, **kwargs):
    out = kwargs["out_dir"] / f"{segment['id']}.mp4"
    for ext in (".mp4", ".srt", ".png", ".meta.json"):
        out.with_suffix(ext).write_text("rendered")
    return out


def test_long_project_renders_from_section_offsets(tmp_path, monkeypatch):
    path = ready_long_project(tmp_path)
    section = path / "sections" / "section001.mp4"
    section.parent.mkdir()
    section.write_bytes(b"section")
    calls = []
    monkeypatch.setattr(
        projects.sections, "ensure_section",
        lambda url, workdir, index, start, end: (
            calls.append((url, index, start, end)) or section))
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims",
                        lambda p: (640, 360))
    seen = []

    def render(segment, words, source, W, H, workdir, *args, **kwargs):
        seen.append((Path(source), kwargs["source_offset"]))
        return fake_section_render(segment, *args, **kwargs)

    monkeypatch.setattr(projects.render_clips, "render", render)
    state = projects.run_project(path, action="render")
    assert calls == [("https://example.com/long", 1, 90.0, 150.0)]
    assert seen == [(section, 90.0)]
    assert state["render_errors"] == []
    assert state["status"] == "complete"
    assert not section.exists()


def test_section_stall_falls_back_to_full_source(tmp_path, monkeypatch):
    path = ready_long_project(tmp_path)
    full = tmp_path / "full.mp4"
    full.write_bytes(b"full source")
    monkeypatch.setattr(
        projects.sections, "ensure_section",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            projects.ingest.SectionDownloadTimeout("stalled")))
    monkeypatch.setattr(projects.sections, "ensure_full_source",
                        lambda *args, **kwargs: full)
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims",
                        lambda p: (640, 360))
    seen = []

    def render(segment, words, source, W, H, workdir, *args, **kwargs):
        seen.append((Path(source), kwargs["source_offset"]))
        return fake_section_render(segment, *args, **kwargs)

    monkeypatch.setattr(projects.render_clips, "render", render)
    state = projects.run_project(path, action="render")
    assert seen == [(full, 0.0)]
    assert state["status"] == "complete"
    assert state["render_errors"] == []


def test_section_failure_records_error_and_keeps_successes(tmp_path, monkeypatch):
    path = ready_long_project(tmp_path, segments=[
        {"start": 100, "end": 140, "hook": "one", "overall": 9},
        {"start": 400, "end": 440, "hook": "two", "overall": 9}])

    def fail(*args, **kwargs):
        raise RuntimeError("404")

    monkeypatch.setattr(projects.sections, "ensure_section", fail)
    with pytest.raises(ValueError, match="failed"):
        projects.run_project(path, action="render")
    state = projects.read_project(path)
    assert state["status"] == "failed"
    assert [item["clip"] for item in state["render_errors"]] == ["001", "002"]


def test_partial_section_failure_keeps_outputs_for_retry(tmp_path, monkeypatch):
    path = ready_long_project(tmp_path, segments=[
        {"start": 100, "end": 140, "hook": "one", "overall": 9},
        {"start": 400, "end": 440, "hook": "two", "overall": 9}])
    section = path / "sections" / "section001.mp4"
    section.parent.mkdir()
    section.write_bytes(b"section")

    def ensure(url, workdir, index, start, end):
        if index == 1:
            return section
        raise RuntimeError("404")

    monkeypatch.setattr(projects.sections, "ensure_section", ensure)
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims",
                        lambda p: (640, 360))
    monkeypatch.setattr(projects.render_clips, "render", fake_section_render)
    state = projects.run_project(path, action="render")
    assert state["status"] == "failed"
    assert len(state["last_outputs"]) == 1
    assert len(state["render_errors"]) == 1
    assert section.exists()

    seen = []
    monkeypatch.setattr(
        projects.sections, "ensure_section",
        lambda url, workdir, index, start, end: (
            seen.append(index) or section))
    state = projects.run_project(path, action="render")
    assert seen == [2]
    assert sorted(state["last_outputs"]) == sorted(set(state["last_outputs"]))
    assert len(set(state["last_outputs"])) == 2
    assert state["status"] == "complete"
    assert state["render_errors"] == []
    assert not section.exists()
