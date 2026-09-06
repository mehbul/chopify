"""
Stage 2 of the chopify pipeline - the built-in "brain".

Reads work/transcript.json, picks the most viral self-contained moments and
writes work/segments.json (a bare JSON array render_clips.py consumes):

    [{"start": 134.2, "end": 187.6, "hook": "short title", "overall": 8.4}]

Two scoring modes, both free:

  1. heuristic (default) - pure-Python scoring over the transcript: words per
     second (energy), lexical cue detection (hook / shock / humour /
     controversy / insight / emotion) and arc completion. Deterministic,
     zero dependency.
  2. --llm MODEL - asks a LOCAL Ollama server (http://localhost:11434) in JSON
     mode to pick the moments. Falls back to the heuristic automatically if
     Ollama is unreachable or returns junk.

No paid APIs, no API keys. Usage:
    python score_clips.py work
    python score_clips.py work --llm qwen2.5:7b
    python score_clips.py work --min-score 6.0 --max-clips 12
"""
import re
import json
import argparse
import urllib.request
from pathlib import Path

OLLAMA_HOST = "http://localhost:11434"

FILLER_TOKENS = {"um", "uh", "uhh", "umm", "erm", "er", "hmm", "mmm", "mm", "ah", "eh"}
FILLER_SEQS = {("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of")}

HOOK_CUES = ("the secret", "nobody tells you", "here's the thing", "here is the thing",
             "what most people", "the truth is", "here's why", "here is why", "imagine",
             "the problem is", "the biggest mistake", "let me tell you", "you need to",
             "here's how", "here is how", "the trick", "the key", "listen", "story")
SHOCK_CUES = ("insane", "crazy", "shocking", "unbelievable", "never", "nobody", "everyone",
              "banned", "illegal", "mistake", "wrong", "destroyed", "worst", "best",
              "huge", "million", "billion", "dead", "killed")
HUMOUR_CUES = ("haha", "lol", "laugh", "joke", "funny", "hilarious", "kidding", "ridiculous")
CONTROVERSY_CUES = ("i disagree", "unpopular opinion", "hot take", "controversial",
                    "stop doing", "don't do", "do not do", "overrated", "underrated",
                    "everyone is wrong", "myth", "lie", "scam")
INSIGHT_CUES = ("because", "the reason", "how it works", "step one", "first", "second",
                "third", "the way", "what happens", "if you", "the difference",
                "framework", "system", "lesson", "learned", "the point")
EMOTION_CUES = ("love", "hate", "afraid", "scared", "amazing", "terrible", "proud",
                "embarrassed", "angry", "excited", "grateful", "hurts", "cried",
                "dream", "hope", "believe")


# ---------------------------------------------------------------- transcript

def load_transcript(workdir):
    path = Path(workdir) / "transcript.json"
    if not path.exists():
        raise SystemExit(f"No transcript at {path} - run download_and_transcribe.py first")
    # utf-8-sig transparently handles BOM'd files from Windows editors too
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"transcript.json is not valid JSON: {e}")
    if not data.get("words"):
        raise SystemExit("transcript.json has no words - was the video silent?")
    return data


def sentenceize(words, gap=1.2):
    """Split a word list into sentences on terminal punctuation or >gap-second pauses."""
    sentences, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"]) > gap:
            sentences.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?\u2026][\"')\]]?$", w["word"].strip()):
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    out = []
    for s in sentences:
        out.append({
            "start": s[0]["start"],
            "end": s[-1]["end"],
            "text": " ".join(w["word"] for w in s).strip(),
            "words": s,
        })
    return out


# ------------------------------------------------------------- heuristic core

# criterion -> weight (hook/energy/arc dominate; matches the README's 8 criteria)
WEIGHTS = {"hook": 0.20, "shock": 0.10, "humour": 0.05, "controversy": 0.10,
           "insight": 0.15, "emotion": 0.10, "energy": 0.15, "arc": 0.15}

def _cues(low, group):
    return min(1.0, sum(c in low for c in group) / 2.0)


def score_sentence(sent):
    text = sent["text"]
    low = " " + text.lower() + " "
    dur = max(0.1, sent["end"] - sent["start"])
    wps = len(sent["words"]) / dur
    arc = 1.0 if re.search(r"[.!?\u2026][\"')\]]?$", text) else 0.55
    scores = {
        "hook": _cues(low, HOOK_CUES),
        "shock": _cues(low, SHOCK_CUES),
        "humour": _cues(low, HUMOUR_CUES),
        "controversy": _cues(low, CONTROVERSY_CUES),
        "insight": _sm_cues(low),
        "emotion": _cues(low, EMOTION_CUES),
        "energy": min(1.0, wps / 3.0),
        "arc": arc,
    }
    overall = sum(WEIGHTS[k] * v for k, v in scores.items()) * 10.0
    return scores, round(overall, 2)


def _sm_cues(low):
    return _cues(low, INSIGHT_CUES)


def make_hook(text, max_words=7):
    words = [_norm_token(w) for w in text.split()]
    words = [w for w in words if w and w not in FILLER_TOKENS]
    return " ".join(words[:max_words]) or "clip"


def _norm_token(w):
    return re.sub(r"[^\w']", "", w.lower())


def build_candidates(sentences, min_len, max_len, max_clips):
    """Seed windows at the highest-scoring sentences, grow to min_len, dedup, sort."""
    scored = {i: score_sentence(s)[1] for i, s in enumerate(sentences)}
    order = sorted(range(len(sentences)), key=lambda i: scored[i], reverse=True)
    taken = [False] * len(sentences)
    accepted = []

    def span(a, b):
        return sentences[b]["end"] - sentences[a]["start"]

    for seed in order:
        if taken[seed] or len(accepted) >= max_clips:
            continue
        j = seed
        while span(seed, j) < min_len and j + 1 < len(sentences) \
                and not taken[j + 1]:
            j += 1
        while span(seed, j) > max_len and j > seed:
            j -= 1
        if span(seed, j) < min_len * 0.6:
            continue
        win = sentences[seed:j + 1]
        words = [w for s in win for w in s["words"]]
        text = " ".join(w["word"] for w in words)
        scores, _ = score_sentence({"start": win[0]["start"], "end": win[-1]["end"],
                                    "text": text, "words": words})
        accepted.append({
            "start": round(win[0]["start"], 2),
            "end": round(win[-1]["end"], 2),
            "hook": make_hook(win[0]["text"]),
            "overall": round(sum(WEIGHTS[k] * v for k, v in scores.items()) * 10.0, 1),
        })
        for k in range(seed, j + 1):
            taken[k] = True
    accepted.sort(key=lambda s: s["start"])
    return accepted

# ------------------------------------------------------------------- ollama

def ollama_select(data, model, host, min_score, max_clips, min_len, max_len, timeout=1800):
    """Ask local Ollama for clips. Returns list of dicts, or None on any failure."""
    sentences = sentenceize(data["words"])
    if not sentences:
        return None

    def call(batch):
        lines = "\n".join(f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in batch)
        sys_msg = LLM_SYSTEM.format(min_len=min_len, max_len=max_len,
                                    max_clips=max_clips, min_score=min_score)
        payload = {
            "model": model, "stream": False, "format": "json",
            "options": {"temperature": 0.2},
            "messages": [{"role": "system", "content": sys_msg},
                         {"role": "user", "content":
                          f"Video duration: {data['duration']:.0f}s\n\nTranscript:\n{lines}"}],
        }
        req = urllib.request.Request(
            host.rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        content = resp.get("message", {}).get("content", "")
        m = re.search(r"\[.*\]", content, re.S)
        return json.loads(m.group(0)) if m else []

    try:
        chunks = [sentences[i:i + 400] for i in range(0, len(sentences), 400)] or [[]]
        raw = []
        for batch in chunks:
            raw.extend(call(batch) or [])
    except Exception as e:                                   # noqa: BLE001
        print(f"  Ollama unavailable ({e}) -> heuristic scoring", flush=True)
        return None

    valid = []
    for it in raw:
        try:
            start, end = float(it["start"]), float(it["end"])
            overall = float(it.get("overall", 0))
            hook = str(it.get("hook") or "clip")
        except (KeyError, TypeError, ValueError):
            continue
        start = max(0.0, min(start, data["duration"] - min_len))
        end = max(start + min_len, min(end, data["duration"]))
        if end - start > max_len + 15:
            end = start + max_len
        valid.append({"start": round(start, 2), "end": round(end, 2),
                      "hook": make_hook(hook, 8), "overall": round(overall, 1)})

    # snap to sentence boundaries (nearest within 3s) so clips never cut mid-word
    starts = [s["start"] for s in sentences]
    ends = [s["end"] for s in sentences]
    for seg in valid:
        si = _nearest(starts, seg["start"])
        ei = _nearest(ends, seg["end"])
        if 2.0 < ends[ei] - starts[si] < max_len + 15:
            seg["start"], seg["end"] = round(starts[si], 2), round(ends[ei], 2)

    valid.sort(key=lambda s: s["start"])
    dedup = []
    for seg in valid:
        if dedup and seg["start"] < dedup[-1]["end"] - 2:
            continue
        dedup.append(seg)
    return dedup[:max_clips]


def _nearest(values, t):
    lo, hi = 0, len(values) - 1
    best = 0
    best_d = float("inf")
    while lo <= hi:
        mid = (lo + hi) // 2
        d = abs(values[mid] - t)
        if d < best_d:
            best_d, best = d, mid
        if values[mid] < t:
            lo = mid + 1
        else:
            hi = mid - 1
    return best

# ----------------------------------------------------------------- run / cli

def run(workdir, llm=None, host=OLLAMA_HOST, min_score=None, max_clips=10,
        min_len=20, max_len=75):
    """Score the transcript in workdir and write workdir/segments.json.

    Returns (segments, mode) where mode is 'ollama' or 'heuristic'.
    """
    workdir = Path(workdir)
    data = load_transcript(workdir)
    sentences = sentenceize(data["words"])
    print(f"Transcript: {len(sentences)} sentences over {data['duration']:.0f}s", flush=True)

    mode = "heuristic"
    segments = None
    if llm:
        ms = min_score if min_score is not None else 8.0
        segments = ollama_select(data, llm, host, ms, max_clips, min_len, max_len)
        if segments is not None:
            mode = "ollama"
    if segments is None:
        ms = min_score if min_score is not None else 6.5
        segments = build_candidates(sentences, min_len, max_len, max_clips)
        segments = [s for s in segments if s["overall"] >= ms]

    if not segments:                       # never ship zero clips silently
        print("No clip reached the threshold - keeping the single best candidate.",
              flush=True)
        segments = build_candidates(sentences, min_len, max_len, 1)

    out = workdir / "segments.json"
    out.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}  (mode={mode}, {len(segments)} clips)", flush=True)
    for s in segments:
        print(f"  [{s['start']:8.2f} -> {s['end']:8.2f}] {s['overall']:4.1f}/10  {s['hook']}",
              flush=True)
    return segments, mode


def main():
    ap = argparse.ArgumentParser(description="Score a chopify transcript for virality.")
    ap.add_argument("workdir", nargs="?", default="work")
    ap.add_argument("--llm", nargs="?", const="qwen2.5:7b", default=None, metavar="MODEL",
                    help="score with a local Ollama model (e.g. qwen2.5:7b, llama3.1:8b)")
    ap.add_argument("--host", default=OLLAMA_HOST,
                    help="Ollama host (default http://localhost:11434)")
    ap.add_argument("--min-score", type=float, default=None,
                    help="keep clips at/above this score (default 6.5 heuristic, 8.0 llm)")
    ap.add_argument("--max-clips", type=int, default=10)
    ap.add_argument("--min-len", type=int, default=20, help="min clip seconds")
    ap.add_argument("--max-len", type=int, default=75, help="max clip seconds")
    args = ap.parse_args()
    run(args.workdir, args.llm, args.host, args.min_score, args.max_clips,
        args.min_len, args.max_len)
    print("SCORING COMPLETE", flush=True)


if __name__ == "__main__":
    main()


# criterion -> weight (hook/energy/arc dominate; matches the README's 8 criteria)
WEIGHTS = {"hook": 0.20, "shock": 0.10, "humour": 0.05, "controversy": 0.10,
           "insight": 0.15, "emotion": 0.10, "energy": 0.15, "arc": 0.15}

LLM_SYSTEM = (
    "You are a short-form video editor. You receive a timestamped transcript. "
    "Select the moments most likely to go viral as self-contained clips. Rules:\n"
    "- Each clip MUST be a complete thought: hook, build, payoff. Never end mid-sentence.\n"
    "- Clip length between {min_len} and {max_len} seconds.\n"
    "- Return AT MOST {max_clips} clips, taken from DIFFERENT parts of the video.\n"
    "- Rate each clip 0-10 on hook, shock, humour, controversy, insight, emotion, "
    "energy and complete arc; put the mean in 'overall'.\n"
    "- Only include clips with overall >= {min_score}.\n"
    'Respond with JSON only, an array: [{{"start": <sec>, "end": <sec>, '
    '"hook": "<3-7 word title>", "overall": <0-10>}}]'
)

# --- transcript helpers below ---
