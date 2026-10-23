"""Validate editable transcripts and export clip-relative SubRip captions."""
import copy
import math
from pathlib import Path

from score_clips import sentenceize


def update_transcript(data, words):
    if not isinstance(words, list) or not words:
        raise ValueError("Provide at least one timed word.")
    duration = float(data["duration"])
    clean = []
    previous = 0.0
    for item in words:
        text = str(item.get("word", "")).strip()
        start, end = float(item["start"]), float(item["end"])
        if (not text or len(text) > 500 or "\n" in text or "\r" in text
                or not math.isfinite(start) or not math.isfinite(end)
                or start < previous - 0.001 or end <= start or end > duration + 0.001):
            raise ValueError("Words must have text and ordered, non-overlapping times within the video.")
        clean.append({"word": text, "start": start, "end": end})
        previous = end
    result = copy.deepcopy(data)
    result["words"] = clean
    result["segments"] = [{k: s[k] for k in ("start", "end", "text")}
                          for s in sentenceize(clean)]
    return result


def srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def write_srt(words, start, end, path, group_size=8):
    selected = [w for w in words if w["end"] > start and w["start"] < end]
    blocks = []
    for i in range(0, len(selected), group_size):
        group = selected[i:i + group_size]
        a = max(start, group[0]["start"]) - start
        b = min(end, group[-1]["end"]) - start
        text = " ".join(w["word"].strip() for w in group)
        blocks.append(f"{len(blocks) + 1}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n")
    Path(path).write_text("\n".join(blocks), encoding="utf-8")
    return Path(path)
