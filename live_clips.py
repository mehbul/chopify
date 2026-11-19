"""Turn a hype trigger into a finished chopify clip artifact set."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import ingest
import render_clips


@dataclass
class ClipRecord:
    id: str
    start: float
    end: float
    score: float
    signals: dict
    files: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Trigger:
    time: float
    score: float
    signals: dict


def plan_window(trigger_time, buffer_seconds, pre_roll, tail,
                min_len, max_len, now):
    """Return (start, end) in stream seconds for a trigger."""
    now = min(float(now), float(trigger_time) + float(tail))
    end = min(float(trigger_time) + float(tail), now)
    start = max(0.0, float(trigger_time) - float(pre_roll))
    start = max(start, end - float(buffer_seconds))
    length = end - start
    if length < min_len:
        start = max(0.0, end - min_len)
    if length > max_len:
        start = end - max_len
    return round(start, 3), round(end, 3)


def remap_words(words, start, end):
    """Shift word timestamps into a window-relative timeline."""
    result = []
    for word in words or []:
        if not (start <= float(word.get("start", -1)) <= end):
            continue
        item = dict(word)
        item["start"] = float(word["start"]) - start
        item["end"] = float(word["end"]) - start
        result.append(item)
    return result


def stitch(paths, destination, runner=None):
    """Concatenate selected segments into one seekable local MP4."""
    runner = runner or ingest._run
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError("No segments available for this clip window.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    listing = destination.parent / "window_list.txt"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in paths),
                       encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
           "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
           str(destination)]
    runner(cmd)
    return destination


class LiveClipWriter:
    def __init__(self, session_dir):
        self.session_dir = Path(session_dir)
        self.clips_dir = self.session_dir / "clips"
        self.windows_dir = self.session_dir / "windows"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.windows_dir.mkdir(parents=True, exist_ok=True)

    def write(self, trigger, options, probe, words, segments, renderer=None):
        """Render one clip; return a ClipRecord (with .error on failure)."""
        render_fn = renderer or render_clips.render
        start, end = plan_window(
            trigger.time, options["live_buffer"], options["live_pre_roll"],
            options["live_tail"], options["min_len"], options["max_len"],
            trigger.time + options["live_tail"])
        record = ClipRecord(uuid.uuid4().hex[:12], start, end,
                            trigger.score, trigger.signals)
        try:
            window = stitch(segments, self.windows_dir / f"{record.id}.mp4")
            seg = {"start": 0.0, "end": round(end - start, 3)}
            render_fn(seg, remap_words(words, start, end), window,
                      probe["width"], probe["height"], str(self.session_dir),
                      options["aspect"], out_dir=str(self.clips_dir),
                      style=options["style"], do_tighten=options["tighten"],
                      loudnorm=options["loudnorm"], brand=options["brand"],
                      layout=options["layout"], centers=options["centers"],
                      caption=options["caption"], fit=options["fit"],
                      facecam_box=options["facecam_box"],
                      facecam_height=options["facecam_height"])
            record.files = {"window": str(window)}
            for path in sorted(self.clips_dir.glob(f"*{record.id}*")):
                record.files[path.suffix.lstrip(".") or "file"] = str(path)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
        return record
