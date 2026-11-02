"""Probe sources and download only the media a long job needs.

Local files are never copied. Long URL sources download audio for discovery
(or a low-resolution video when there is no audio) and later download only the
selected video sections. Everything uses yt-dlp/ffprobe locally.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

LONG_SECONDS = 2 * 60 * 60
LONG_BYTES = 8 * 1024**3
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".wav", ".aac", ".flac", ".webm"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
MIN_FREE_BYTES = 500 * 1024**2


def is_url(source):
    return urlparse(str(source)).scheme in {"http", "https"}


def _run(cmd, **kwargs):
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, **kwargs)


def _format_size(fmt, duration):
    if not isinstance(fmt, dict):
        return 0
    for key in ("filesize", "filesize_approx"):
        try:
            value = int(fmt.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    try:
        rate = float(fmt.get("tbr") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate > 0 and duration:
        return int(rate * 1000 / 8 * float(duration))
    return 0


def _default_bitrate(height):
    if not height:
        return 4_000_000
    if height >= 1080:
        return 8_000_000
    if height >= 720:
        return 5_000_000
    if height >= 480:
        return 2_500_000
    return 1_500_000


def _probe_local(path, run=None):
    run = run or _run
    path = Path(path).expanduser().resolve()
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_type,width,height",
        "-of", "json", str(path)])
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ffprobe returned invalid JSON for {path}") from exc
    streams = data.get("streams") or []
    video = next(
        (item for item in streams if item.get("codec_type") == "video"), {})
    size = int((data.get("format") or {}).get("size") or path.stat().st_size)
    duration = (data.get("format") or {}).get("duration")
    return {
        "source": str(path),
        "url": False,
        "title": path.name,
        "duration": float(duration) if duration is not None else None,
        "size": size,
        "estimated_size": size,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "has_video": bool(video),
    }


def _probe_url(url, run=None):
    run = run or _run
    fallback = {
        "source": url, "url": True, "title": url, "duration": None,
        "size": None, "estimated_size": None, "width": 0, "height": 0,
        "has_audio": None, "has_video": None, "formats": [],
    }
    try:
        result = run([
            sys.executable, "-m", "yt_dlp", "--dump-single-json",
            "--skip-download", "--no-playlist", url])
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return fallback

    duration = data.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    formats = [item for item in (data.get("formats") or [])
               if isinstance(item, dict)]
    videos = [item for item in formats
              if item.get("vcodec") not in (None, "none")]
    audios = [item for item in formats
              if item.get("acodec") not in (None, "none")]
    video = max(
        videos,
        key=lambda item: (int(item.get("height") or 0),
                          float(item.get("tbr") or 0)),
        default=None)
    audio = max(
        audios,
        key=lambda item: float(item.get("tbr") or 0),
        default=None)
    estimated = 0
    if video is not None:
        estimated += _format_size(video, duration)
        if not estimated and duration:
            estimated = int(_default_bitrate(
                int(video.get("height") or 0)) * duration / 8)
        if audio is not None:
            estimated += _format_size(audio, duration)
    elif audio is not None:
        estimated = _format_size(audio, duration)
    elif duration:
        estimated = int(_default_bitrate(0) * duration / 8)

    return {
        "source": url,
        "url": True,
        "title": str(data.get("title") or url),
        "duration": duration,
        "size": None,
        "estimated_size": estimated or None,
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "has_audio": any(
            item.get("acodec") not in (None, "none") for item in formats),
        "has_video": bool(videos),
        "formats": formats,
    }


def probe_source(source, runner=None):
    """Return local or yt-dlp metadata; URL failures become an unknown source."""
    source = str(source)
    run = runner or _run
    if is_url(source):
        return _probe_url(source, run)
    path = Path(source).expanduser()
    if not path.is_file():
        raise ValueError(f"Video file does not exist or URL is invalid: {source}")
    return _probe_local(path, run)


def is_long_source(meta, seconds=LONG_SECONDS, size_bytes=LONG_BYTES):
    """Duration, estimated full download, or unknown duration selects long."""
    duration = (meta or {}).get("duration")
    if duration is None:
        return True
    try:
        if float(duration) >= float(seconds):
            return True
    except (TypeError, ValueError):
        return True
    size = (meta or {}).get("estimated_size")
    try:
        return size is not None and float(size) >= float(size_bytes)
    except (TypeError, ValueError):
        return True


def _find_media(workdir, prefix):
    workdir = Path(workdir)
    for path in sorted(workdir.glob(prefix + ".*")):
        if path.suffix.lower() in AUDIO_EXTS | VIDEO_EXTS and path.is_file():
            return path.resolve()
    return None


def _remove_old(workdir, prefix):
    for path in Path(workdir).glob(prefix + ".*"):
        if path.suffix.lower() in AUDIO_EXTS | VIDEO_EXTS:
            try:
                path.unlink()
            except OSError:
                pass


def download_discovery(url, workdir, meta=None, runner=None):
    """Download best audio, or a low-resolution video for silent URLs."""
    run = runner or _run
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    meta = meta or probe_source(url, runner=run)
    _remove_old(workdir, "audio")
    _remove_old(workdir, "discovery")

    if meta.get("has_audio") is not False:
        try:
            run([
                sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best",
                "--no-playlist", "-o", str(workdir / "audio.%(ext)s"), url])
        except (OSError, subprocess.SubprocessError):
            pass
        audio = _find_media(workdir, "audio")
        if audio is not None:
            return audio, "audio"

    try:
        run([
            sys.executable, "-m", "yt_dlp",
            "-f", "worst[height<=360]/worst", "--no-playlist",
            "-o", str(workdir / "discovery.%(ext)s"), url])
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Download failed: no audio or video file produced.") from exc
    video = _find_media(workdir, "discovery")
    if video is None:
        raise RuntimeError("Download failed: no audio or video file produced.")
    return video, "video"


def download_section(url, start, end, out_path, quality=1080, runner=None):
    """Download one accurate, re-encoded video section with yt-dlp."""
    run = runner or _run
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in (out_path, out_path.with_suffix(out_path.suffix + ".part")):
        try:
            stale.unlink()
        except OSError:
            pass
    if quality:
        fmt = (f"bestvideo[height<={int(quality)}]+bestaudio/"
               f"best[height<={int(quality)}]")
    else:
        fmt = "worst"
    run([
        sys.executable, "-m", "yt_dlp", "-f", fmt,
        "--merge-output-format", "mp4", "--no-playlist", "--force-overwrites",
        "--download-sections", f"*{float(start):.3f}-{float(end):.3f}",
        "--force-keyframes-at-cuts",
        "-o", str(out_path), url])
    if not out_path.is_file() or out_path.stat().st_size <= 0:
        raise RuntimeError(f"Section download produced no file: {out_path.name}")
    return out_path


def check_disk(path, required_bytes):
    """Warn below 1.5x the estimate; stop only under 500 MB free."""
    free = shutil.disk_usage(str(path)).free
    if free < MIN_FREE_BYTES:
        raise ValueError(
            f"Not enough free disk space: {free / 1024**2:.0f} MB free.")
    if required_bytes and free < 1.5 * float(required_bytes):
        print(f"Disk warning: {free / 1024**3:.1f} GB free, "
              f"estimated job size {float(required_bytes) / 1024**3:.1f} GB.",
              flush=True)
