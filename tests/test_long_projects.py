"""Long projects probe once, ingest discovery media, and transcribe chunks."""
import json

import pytest

import projects


def long_meta(source="https://example.com/long", **overrides):
    meta = {"source": source, "url": True, "duration": 9000, "width": 1920,
            "height": 1080, "has_audio": True, "has_video": True,
            "estimated_size": 12 * 1024**3}
    meta.update(overrides)
    return meta


def stub_scoring(monkeypatch, path):
    monkeypatch.setattr(projects.score_clips, "discover",
                        lambda *args, **kwargs: None)

    def select(*args, **kwargs):
        projects.atomic_json(path / "segments.json", [])
        projects.atomic_json(path / "selection.json",
                             {"score_min": None, "score_max": None,
                              "passing": 0})
        return [], "heuristic"

    monkeypatch.setattr(projects.score_clips, "select", select)


def make_project(tmp_path, source, options=None):
    state = projects.create_project(
        tmp_path / "projects", source, options or {"min_len": 15})
    return projects.project_path(tmp_path / "projects", state["id"])


def write_transcript(workdir, video, source, duration, **extra):
    data = {"video": str(video), "source": source, "duration": duration,
            "words": [{"word": "Hello.", "start": 0, "end": 1}],
            "segments": []}
    data.update(extra)
    projects.atomic_json(workdir / "transcript.json", data)


def test_long_url_prepare_downloads_audio_and_transcribes_in_chunks(
        tmp_path, monkeypatch):
    source = "https://example.com/long"
    path = make_project(tmp_path, source)
    calls = []
    monkeypatch.setattr(projects.ingest, "probe_source",
                        lambda value: long_meta())
    monkeypatch.setattr(projects.ingest, "is_long_source", lambda meta: True)
    monkeypatch.setattr(projects.ingest, "check_disk", lambda *args: None)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(
        projects.ingest, "download_discovery",
        lambda url, workdir, meta=None: (audio, "audio"))

    def transcribe(video, workdir, *args, **kwargs):
        calls.append(kwargs)
        write_transcript(workdir, video, source, 9000,
                         render_from_sections=True, chunked=True, chunks=5)

    monkeypatch.setattr(projects.dt, "transcribe", transcribe)
    stub_scoring(monkeypatch, path)
    projects.run_project(path, action="prepare")

    assert calls and calls[0]["chunked"] is True
    assert calls[0]["render_from_sections"] is True
    assert calls[0]["duration"] == 9000
    assert calls[0]["media_kind"] == "audio"
    state = projects.read_project(path)
    assert state["source_meta"]["media_kind"] == "audio"
    assert state["source_meta"]["long"] is True
    assert state["source_meta"]["render_from_sections"] is True
    assert state["cache"]["source_media"]


def test_long_local_file_transcribes_chunks_without_downloading(
        tmp_path, monkeypatch):
    source = tmp_path / "recording.mp4"
    source.write_bytes(b"video")
    path = make_project(tmp_path, str(source))
    calls = []
    monkeypatch.setattr(projects.ingest, "probe_source", lambda value: {
        "source": str(value), "url": False, "duration": 9000, "size": 5,
        "estimated_size": 5, "width": 640, "height": 360,
        "has_audio": True, "has_video": True})
    monkeypatch.setattr(projects.dt, "download",
                        lambda *args, **kwargs: pytest.fail("downloaded local file"))
    monkeypatch.setattr(projects.ingest, "download_discovery",
                        lambda *args, **kwargs: pytest.fail("downloaded local file"))

    def transcribe(video, workdir, *args, **kwargs):
        calls.append(kwargs)
        write_transcript(workdir, video, str(source), 9000,
                         render_from_sections=False, chunked=True, chunks=5)

    monkeypatch.setattr(projects.dt, "transcribe", transcribe)
    stub_scoring(monkeypatch, path)
    projects.run_project(path, action="prepare")

    assert calls and calls[0]["chunked"] is True
    assert calls[0]["render_from_sections"] is False
    state = projects.read_project(path)
    assert state["source_meta"]["render_from_sections"] is False
    assert state["video"] == str(source.resolve())


def test_long_url_without_audio_skips_transcription(tmp_path, monkeypatch):
    source = "https://example.com/muted"
    path = make_project(tmp_path, source)
    written = []
    monkeypatch.setattr(projects.ingest, "probe_source", lambda value: long_meta(
        has_audio=False, duration=5400))
    monkeypatch.setattr(projects.ingest, "is_long_source", lambda meta: True)
    monkeypatch.setattr(projects.ingest, "check_disk", lambda *args: None)
    discovery = tmp_path / "discovery.mp4"
    discovery.write_bytes(b"video")
    monkeypatch.setattr(
        projects.ingest, "download_discovery",
        lambda url, workdir, meta=None: (discovery, "video"))
    monkeypatch.setattr(projects.dt, "transcribe",
                        lambda *args, **kwargs: pytest.fail("transcribed silent URL"))

    def write_media(video, workdir, duration, **kwargs):
        written.append((duration, kwargs))
        data = {"video": str(video), "source": source, "duration": duration,
                "words": [], "segments": []}
        data.update(kwargs)
        projects.atomic_json(workdir / "transcript.json", data)

    monkeypatch.setattr(projects.dt, "write_media_transcript", write_media)
    stub_scoring(monkeypatch, path)
    projects.run_project(path, action="prepare")

    assert written and written[0][0] == 5400
    assert written[0][1]["media_kind"] == "video"
    assert written[0][1]["render_from_sections"] is True
    state = projects.read_project(path)
    assert state["source_meta"]["media_kind"] == "video"


def test_short_url_keeps_full_download_and_single_pass(tmp_path, monkeypatch):
    source = "https://example.com/short"
    path = make_project(tmp_path, source)
    calls = []
    meta = long_meta(duration=60, estimated_size=100 * 1024**2)
    monkeypatch.setattr(projects.ingest, "probe_source", lambda value: meta)
    monkeypatch.setattr(projects.ingest, "is_long_source", lambda value: False)
    monkeypatch.setattr(projects.ingest, "check_disk", lambda *args: None)
    monkeypatch.setattr(projects.ingest, "download_discovery",
                        lambda *args, **kwargs: pytest.fail("used discovery path"))
    video = tmp_path / "short.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(projects.dt, "resolve_source",
                        lambda value, workdir: video)

    def transcribe(video, workdir, *args, **kwargs):
        calls.append(kwargs)
        write_transcript(workdir, video, source, 60, chunked=False, chunks=0)

    monkeypatch.setattr(projects.dt, "transcribe", transcribe)
    stub_scoring(monkeypatch, path)
    projects.run_project(path, action="prepare")

    assert calls and calls[0]["chunked"] is False
    assert calls[0]["render_from_sections"] is False
    assert calls[0]["duration"] is None
    assert projects.read_project(path)["source_meta"]["render_from_sections"] is False


def test_probe_is_cached_per_source_identity(tmp_path, monkeypatch):
    source = "https://example.com/long"
    path = make_project(tmp_path, source)
    probes = []
    meta = long_meta()
    monkeypatch.setattr(projects.ingest, "probe_source",
                        lambda value: probes.append(value) or meta)
    monkeypatch.setattr(projects.ingest, "is_long_source", lambda value: True)
    monkeypatch.setattr(projects.ingest, "check_disk", lambda *args: None)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(
        projects.ingest, "download_discovery",
        lambda url, workdir, meta=None: (audio, "audio"))

    def transcribe(video, workdir, *args, **kwargs):
        write_transcript(workdir, video, source, 9000,
                         render_from_sections=True, chunked=True, chunks=5)

    monkeypatch.setattr(projects.dt, "transcribe", transcribe)
    stub_scoring(monkeypatch, path)
    projects.run_project(path, action="prepare")
    projects.run_project(path, action="prepare")
    assert probes == [source]
