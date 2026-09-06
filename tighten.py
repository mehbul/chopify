"""
Tighten cuts for chopify: remove filler words and long silences from a clip.

Pure planning + a small ffmpeg runner. The plan (list of keep-ranges) is
computed from the word-level timestamps chopify already produces, so the
burnt captions can be re-timed to the shortened timeline exactly.

No paid APIs, no extra dependencies. Used by render_clips.py --tighten.
"""
import re
import subprocess
from pathlib import Path

FILLER_TOKENS = {"um", "uh", "uhh", "umm", "erm", "er", "hmm", "mmm", "mm", "ah", "eh"}
FILLER_SEQS = {("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of")}
DEFAULT_GAP = 0.6          # silence longer than this (s) gets cut
GUARD = 0.15               # always keep this much at the very start/end (s)
MIN_KEEP = 0.30            # never create a keep-range shorter than this (s)


def plan_cuts(words, clip_start, clip_end, gap=DEFAULT_GAP,
              fillers=True, silence=True):
    """Return keep-ranges [(a, b)] in *clip-relative* seconds.

    words: the transcript word list (absolute video time).
    fillers: cut filler words.  silence: cut pauses longer than `gap`.
    """
    sub = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    if not sub:
        return [(0.0, clip_end - clip_start)]
    dur = clip_end - clip_start

    drop = []                                    # absolute-time ranges to cut
    if fillers:
        toks = [re.sub(r"[^\w']", "", w["word"].lower()) for w in sub]
        for i, w in enumerate(sub):
            if toks[i] in FILLER_TOKENS:
                drop.append((w["start"], w["end"]))
            elif i + 1 < len(sub) and (toks[i], toks[i + 1]) in FILLER_SEQS:
                drop.append((w["start"], sub[i + 1]["end"]))
    if silence:
        for a, b in zip(sub, sub[1:]):
            if b["start"] - a["end"] > gap:
                drop.append((a["end"], b["start"]))

    drop = [(max(a, clip_start + GUARD), min(b, clip_end - GUARD))
            for a, b in sorted(drop)]
    drop = [(a, b) for a, b in drop if b - a > 0.08]

    keeps, cur = [], clip_start                  # subtract drops -> keep ranges
    for a, b in drop:
        if a > cur:
            keeps.append((cur, a))
        cur = max(cur, b)
    if cur < clip_end:
        keeps.append((cur, clip_end))

    keeps = [(round(a - clip_start, 3), round(b - clip_start, 3))
             for a, b in keeps if b - a >= MIN_KEEP]
    return keeps or [(0.0, dur)]


def remap_words(words, clip_start, keeps):
    """Re-time words into the tightened timeline; returns (words, new_duration)."""
    out = []
    for w in words:
        shifted = _shift(w, clip_start, keeps)
        if shifted:
            out.append(shifted)
    total = sum(b - a for a, b in keeps)
    return out, round(total, 3)


def _shift(w, clip_start, keeps):
    """Map a word's absolute times onto the concatenated (tightened) timeline."""
    offset = 0.0
    s = t = None
    for a, b in keeps:
        a_abs, b_abs = clip_start + a, clip_start + b
        ws, we = max(w["start"], a_abs), min(w["end"], b_abs)
        if we > ws:                              # word overlaps this keep range
            if s is None:
                s = offset + (ws - a_abs)
            t = offset + (we - a_abs)
        elif s is not None:
            break                                # word fully past
        offset += b - a
    if s is None:
        return None
    # words that straddle a cut keep their kept portion; pad tiny words
    e = t if t and t > s else s + 0.12
    return {"word": w["word"], "start": round(s, 3), "end": round(e, 3)}


def apply_cuts(source, keeps, clip_start, out_path, crf=18):
    """Render the keep-ranges to one concatenated file via ffmpeg."""
    out_path = Path(out_path)
    tmp = out_path.parent / "_tight_parts"
    tmp.mkdir(parents=True, exist_ok=True)
    parts = []
    try:
        for i, (a, b) in enumerate(keeps):
            part = tmp / f"part_{i:03d}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{clip_start + a:.3f}", "-i", str(source),
                 "-t", f"{b - a:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", str(crf), "-c:a", "aac", "-b:a", "160k", str(part)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            parts.append(part)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts),
                       encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", str(out_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        for p in parts:
            try:
                p.unlink()
            except OSError:
                pass
        try:
            (tmp / "list.txt").unlink()
            tmp.rmdir()
        except OSError:
            pass
    return out_path
