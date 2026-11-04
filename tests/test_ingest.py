"""Probe and download selection for short and long sources."""
import json
import subprocess
from pathlib import Path

import pytest

import ingest


class FakeResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def write_output(cmd):
    """Create the file named by yt-dlp's -o template."""
    template = Path(cmd[cmd.index("-o") + 1])
    output = Path(str(template).replace("%(ext)s", "m4a"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"media")
    return output


def test_long_source_uses_duration_size_or_unknown():
    assert ingest.is_long_source({"duration": 7200, "estimated_size": 0})
    assert ingest.is_long_source({"duration": 7200, "estimated_size": None})
    assert ingest.is_long_source({"duration": 60, "estimated_size": 8 * 1024**3})
    assert not ingest.is_long_source({"duration": 7199, "estimated_size": 1})
    assert ingest.is_long_source({"duration": None, "estimated_size": None})


def test_probe_local_reads_duration_size_and_streams(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    payload = {
        "format": {"duration": "3600.5", "size": "1234"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080},
            {"codec_type": "audio"},
        ],
    }
    commands = []
    monkeypatch.setattr(
        ingest, "_run",
        lambda cmd, **kw: commands.append(cmd) or FakeResult(json.dumps(payload)))
    meta = ingest.probe_source(str(source))
    assert meta["duration"] == pytest.approx(3600.5)
    assert meta["size"] == 1234
    assert meta["width"] == 1920 and meta["height"] == 1080
    assert meta["has_audio"] is True and meta["has_video"] is True
    assert meta["url"] is False
    assert commands[0][0] == "ffprobe"


def test_probe_url_estimates_best_video_plus_audio(monkeypatch):
    payload = {
        "duration": 7200,
        "title": "Long stream",
        "formats": [
            {"vcodec": "h264", "height": 1080, "filesize": 6 * 1024**3},
            {"vcodec": "h264", "height": 720, "filesize": 3 * 1024**3},
            {"acodec": "aac", "vcodec": "none", "filesize": 200 * 1024**2},
        ],
    }
    captured = []
    monkeypatch.setattr(
        ingest, "_run",
        lambda cmd, **kw: captured.append(cmd) or FakeResult(json.dumps(payload)))
    meta = ingest.probe_source("https://example.com/v")
    assert meta["url"] is True
    assert meta["duration"] == 7200
    assert meta["estimated_size"] >= 6 * 1024**3
    assert ingest.is_long_source(meta)
    assert captured[0][2:5] == ["yt_dlp", "--dump-single-json", "--skip-download"]


def test_probe_url_failure_stays_unknown(monkeypatch):
    def fail(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(ingest, "_run", fail)
    meta = ingest.probe_source("https://example.com/missing")
    assert meta["duration"] is None
    assert meta["estimated_size"] is None
    assert ingest.is_long_source(meta)


def test_download_discovery_prefers_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ingest, "_run",
        lambda cmd, **kw: (write_output(cmd), FakeResult())[1])
    path, kind = ingest.download_discovery(
        "https://example.com/v", tmp_path,
        {"has_audio": True, "duration": 9000})
    assert kind == "audio"
    assert path.name == "audio.m4a"
    assert path.is_file()


def test_download_discovery_falls_back_to_video(monkeypatch, tmp_path):
    def run(cmd, **kwargs):
        if "audio.%(ext)s" in " ".join(cmd):
            raise subprocess.CalledProcessError(1, cmd)
        write_output(cmd)
        return FakeResult()

    monkeypatch.setattr(ingest, "_run", run)
    path, kind = ingest.download_discovery(
        "https://example.com/muted", tmp_path,
        {"has_audio": False, "duration": 9000})
    assert kind == "video"
    assert path.name == "discovery.m4a"


def test_download_visual_discovery_requests_lowest_video(monkeypatch, tmp_path):
    captured = []

    def run(cmd, **kwargs):
        captured.append(cmd)
        write_output(cmd)
        return FakeResult()

    monkeypatch.setattr(ingest, "_run", run)
    path = ingest.download_visual_discovery(
        "https://example.com/gameplay", tmp_path)
    assert path.is_file()
    assert captured[0][captured[0].index("-f") + 1] == "worst[height<=360]/worst"
    assert captured[0][-1] == "https://example.com/gameplay"


def test_download_visual_discovery_raises_when_no_video(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "_run", lambda cmd, **kw: FakeResult())
    with pytest.raises(RuntimeError, match="no video file"):
        ingest.download_visual_discovery("https://example.com/gameplay", tmp_path)


def test_download_section_uses_accurate_cut_flags(monkeypatch, tmp_path):
    captured = []

    def run(cmd, **kwargs):
        captured.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"section")
        return FakeResult()

    monkeypatch.setattr(ingest, "_run", run)
    out = tmp_path / "sections" / "section001.mp4"
    assert ingest.download_section(
        "https://example.com/v", 100, 160, out, quality=720) == out
    assert "--download-sections" in captured[0]
    assert "*100.000-160.000" in captured[0]
    assert "--force-keyframes-at-cuts" in captured[0]
    assert "bestvideo[height<=720]+bestaudio/best[height<=720]" in captured[0]


def test_download_section_timeout_is_bounded(monkeypatch, tmp_path):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(ingest, "_run", run)
    out = tmp_path / "sections" / "section001.mp4"
    with pytest.raises(ingest.SectionDownloadTimeout, match="stalled"):
        ingest.download_section("https://example.com/v", 100, 160, out)


def test_download_full_avoids_hls_and_ranged_sections(monkeypatch, tmp_path):
    captured = []

    def run(cmd, **kwargs):
        captured.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"full")
        return FakeResult()

    monkeypatch.setattr(ingest, "_run", run)
    out = tmp_path / "source_full.mp4"
    assert ingest.download_full(
        "https://example.com/v", out, quality=1080) == out
    fmt = captured[0][captured[0].index("-f") + 1]
    assert "protocol=https" in fmt
    assert "[vcodec^=avc1]" in fmt
    assert "--download-sections" not in captured[0]
