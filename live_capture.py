"""Resolve live URLs and keep a bounded rolling buffer of HLS segments."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import ingest

SEGMENT_SECONDS = 4
DIRECT_SUFFIXES = (".m3u8", ".ts")
DIRECT_SCHEMES = {"rtmp", "rtmps", "rtmp+https"}
PLATFORMS = (
    ("twitch.tv", "twitch"), ("youtube.com", "youtube"), ("youtu.be", "youtube"),
    ("kick.com", "kick"),
)


@dataclass
class LiveSource:
    url: str
    kind: str
    platform: str
    resolved_by: str
    headers: list[str]
    page_url: str | None = None

    def refresh(self, options, runner=None):
        """Re-resolve a page URL; a direct manifest cannot be refreshed."""
        if not self.page_url:
            return self
        return resolve_live_source(self.page_url, options, runner)


def _platform(url):
    host = (urlparse(url).hostname or "").lower()
    for needle, name in PLATFORMS:
        if needle in host:
            return name
    return "generic"


def resolve_live_source(url, options, runner=None):
    """Return a LiveSource; direct manifests pass through, pages use yt-dlp."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        raise ValueError("Provide a live stream URL of at most 2000 characters.")
    url = url.strip()
    headers = list(options.get("live_headers") or [])
    parsed = urlparse(url)
    if (parsed.scheme in DIRECT_SCHEMES
            or (parsed.scheme in {"http", "https"} and url.lower().split("?")[0].endswith(DIRECT_SUFFIXES))):
        return LiveSource(url, "rtmp" if parsed.scheme in DIRECT_SCHEMES else "hls",
                          _platform(url), "passthrough", headers, None)
    if not ingest.is_url(url):
        raise ValueError("Live source must be an http(s), HLS or RTMP URL.")
    runner = runner or ingest._run
    cmd = ["yt-dlp", "--dump-single-json", "--no-playlist", "--no-warnings", url]
    result = runner(cmd)
    data = json.loads(result.stdout or "{}")
    resolved = str(data.get("url") or "")
    if not resolved:
        raise ValueError("yt-dlp could not resolve a live stream URL from that page.")
    protocol = str(data.get("protocol") or "").lower()
    kind = "hls" if "m3u8" in resolved or "hls" in protocol else "http"
    return LiveSource(resolved, kind, _platform(url), "yt-dlp", headers, url)


class SegmentBuffer:
    """ffmpeg pulls once; analyzers read the resulting 4-second segments."""

    def __init__(self, directory, url, options, runner=None):
        self.directory = Path(directory)
        self.url = url
        self.options = options
        self.runner = runner or ingest._run
        self.retention = float(options.get("live_buffer", 300))
        self.max_bytes = int(options.get("live_buffer_max_mb", 512)) * 1024 * 1024
        self.headers = list(options.get("live_headers") or [])
        self.process = None
        self.started_at = 0.0
        self._stop = threading.Event()
        self._cleaner = None

    def ffmpeg_command(self):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-reconnect", "1", "-reconnect_streamed", "1",
               "-reconnect_delay_max", "5", "-rw_timeout", "15000000"]
        if self.headers:
            cmd += ["-headers", "".join(h + "\r\n" for h in self.headers)]
        cmd += ["-i", self.url, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
                "-reset_timestamps", "1", str(self.directory / "segment%08d.ts")]
        return cmd

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        self.log_path = self.directory / "ffmpeg.log"
        self._log_handle = self.log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            self.ffmpeg_command(), stdout=subprocess.DEVNULL,
            stderr=self._log_handle)
        self._cleaner = threading.Thread(target=self._clean_loop, daemon=True)
        self._cleaner.start()
        return self

    def _clean_loop(self):
        while not self._stop.wait(5.0):
            try:
                self.cleanup()
            except OSError:
                pass

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        self._stop.set()
        if self.process is not None and self.alive:
            ingest._kill_tree(self.process)
        if self._cleaner is not None:
            self._cleaner.join(timeout=2.0)
        if getattr(self, "_log_handle", None) is not None:
            self._log_handle.close()
            self._log_handle = None

    def segments(self):
        if not self.directory.exists():
            return []
        return sorted(p for p in self.directory.glob("segment[0-9]*.ts")
                      if re.fullmatch(r"segment\d{8}\.ts", p.name))

    def ready_segments(self):
        """Segments safe to decode: the newest one is still being written."""
        return self.segments()[:-1]

    @staticmethod
    def index_of(path):
        return int(Path(path).stem.split("segment")[1])

    @staticmethod
    def index_to_time(index):
        return float(index) * SEGMENT_SECONDS

    @staticmethod
    def time_to_index(t):
        return max(0, int(float(t) // SEGMENT_SECONDS))

    def window(self, start, end):
        """Segments covering [start, end], including the one holding `start`."""
        first = self.time_to_index(start)
        last = self.time_to_index(end)
        return [p for p in self.segments() if first <= self.index_of(p) <= last]

    def cleanup(self):
        now = time.time()
        paths = self.segments()
        keep = []
        for path in paths:
            if now - path.stat().st_mtime > self.retention:
                path.unlink(missing_ok=True)
            else:
                keep.append(path)
        total = sum(p.stat().st_size for p in keep)
        for path in keep:
            if total <= self.max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size

    def probe(self):
        paths = self.segments()
        if not paths:
            raise FileNotFoundError("No segments yet; wait for the buffer to fill.")
        return ingest._probe_local(paths[-1], run=self.runner)
