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
