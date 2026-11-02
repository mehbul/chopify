"""Plan, cache, and clean padded source sections for long URL rendering."""
import hashlib
import json
import os
import shutil
from pathlib import Path

import ingest

SECTION_DIR = "sections"
SECTION_VERSION = 1
SECTION_PAD = 10.0
SECTION_GAP = 30.0
SECTION_MAX = 600.0
QUALITIES = (1080, 720, None)


def _padded(start, end, duration, pad):
    padded_start = max(0.0, float(start) - pad)
    padded_end = float(end) + pad
    if duration > 0:
        padded_end = min(float(duration), padded_end)
    return round(padded_start, 3), round(padded_end, 3)


def _group(start, end, clips, duration, pad):
    padded_start, padded_end = _padded(start, end, duration, pad)
    return {"start": padded_start, "end": padded_end,
            "clips": sorted(int(index) for index in clips)}


def plan_sections(segments, duration, pad=SECTION_PAD, gap=SECTION_GAP,
                  max_section=SECTION_MAX):
    """Merge nearby clips into padded sections capped at ``max_section``.

    Clips stay global source times. A single clip longer than the cap keeps its
    full padded range instead of being truncated.
    """
    source_duration = max(0.0, float(duration or 0.0))
    items = []
    for index, segment in enumerate(segments or []):
        start = float(segment["start"])
        end = float(segment["end"])
        if end <= start:
            continue
        items.append((start, end, index))
    items.sort(key=lambda item: (item[0], item[1], item[2]))

    groups = []
    raw_start = raw_end = None
    clips = []
    for start, end, index in items:
        if raw_start is None:
            raw_start, raw_end, clips = start, end, [index]
            continue
        if start <= raw_end + gap:
            candidate_start = min(raw_start, start)
            candidate_end = max(raw_end, end)
            padded_start, padded_end = _padded(
                candidate_start, candidate_end, source_duration, pad)
            if padded_end - padded_start > max_section:
                groups.append(_group(raw_start, raw_end, clips,
                                     source_duration, pad))
                raw_start, raw_end, clips = start, end, [index]
            else:
                raw_start, raw_end = candidate_start, candidate_end
                clips.append(index)
        else:
            groups.append(_group(raw_start, raw_end, clips,
                                 source_duration, pad))
            raw_start, raw_end, clips = start, end, [index]
    if raw_start is not None:
        groups.append(_group(raw_start, raw_end, clips,
                             source_duration, pad))
    return groups


def _source_key(url):
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()


def _paths(workdir, index):
    directory = Path(workdir) / SECTION_DIR
    return (directory / f"section{index:03d}.mp4",
            directory / f"section{index:03d}.json")


def _matches(meta, source_key, index, start, end):
    if not isinstance(meta, dict):
        return False
    if meta.get("version") != SECTION_VERSION:
        return False
    if meta.get("source") != source_key or meta.get("index") != index:
        return False
    try:
        return (abs(float(meta.get("start")) - float(start)) <= 1e-3
                and abs(float(meta.get("end")) - float(end)) <= 1e-3)
    except (TypeError, ValueError):
        return False


def _write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.replace(temp, path)


def ensure_section(url, workdir, index, start, end, runner=None):
    """Return a cached section, downloading it on a version/range miss."""
    workdir = Path(workdir)
    section, meta_path = _paths(workdir, index)
    source_key = _source_key(url)
    start, end = round(float(start), 3), round(float(end), 3)
    if section.is_file() and section.stat().st_size > 0 and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = None
        if _matches(meta, source_key, index, start, end):
            return section

    section.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for quality in QUALITIES:
        for stale in (section, section.with_suffix(section.suffix + ".part"),
                      meta_path):
            try:
                stale.unlink()
            except OSError:
                pass
        try:
            ingest.download_section(url, start, end, section,
                                    quality=quality, runner=runner)
            if not section.is_file() or section.stat().st_size <= 0:
                raise RuntimeError(
                    f"Section download produced no file: {section.name}")
            _write_json(meta_path, {
                "version": SECTION_VERSION, "source": source_key,
                "index": index, "start": start, "end": end,
                "quality": quality, "file": section.name,
            })
            return section
        except Exception as exc:  # try the next quality, then surface it
            last_error = exc
            continue
    _write_json(meta_path, {
        "version": SECTION_VERSION, "source": source_key, "index": index,
        "start": start, "end": end, "quality": None, "failed": True,
    })
    raise RuntimeError(
        f"Section download failed for {start:.0f}-{end:.0f}s: {last_error}")


def cleanup_sections(workdir):
    """Remove only the project's cached section files."""
    directory = Path(workdir) / SECTION_DIR
    if not directory.exists():
        return
    for child in directory.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            continue
    try:
        directory.rmdir()
    except OSError:
        pass
