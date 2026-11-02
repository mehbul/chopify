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
  2. --llm MODEL - after deterministic discovery and dedupe, asks a LOCAL
     Ollama server (http://localhost:11434) to rerank the top candidates by
     +/-1.0. It cannot change timestamps. Falls back automatically if Ollama
     is unreachable or returns junk.

No paid APIs, no API keys. Usage:
    python score_clips.py work
    python score_clips.py work --llm qwen2.5:7b
    python score_clips.py work --min-score 8.0 --preferred-len 45s
"""
import re
import math
import json
import bisect
import argparse
import urllib.request
from pathlib import Path

import evidence
import visual

OLLAMA_HOST = "http://localhost:11434"

BASE_LADDER = (15, 30, 45, 60, 90, 120, 180, 300)
DURATION_HELP = "Use seconds or a duration like 45s, 2m, or 1m30s (5s to 1h)."


def parse_duration(value):
    """Parse seconds or a duration string into a float clamped to 5..3600."""
    if isinstance(value, bool) or value is None:
        raise ValueError(DURATION_HELP)
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().lower().replace(" ", "")
        if not text:
            raise ValueError(DURATION_HELP)
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            seconds = float(text)
        else:
            match = re.fullmatch(
                r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?",
                text)
            if not match or not any(match.groups()):
                raise ValueError(DURATION_HELP)
            hours, minutes, secs = (float(x or 0) for x in match.groups())
            seconds = hours * 3600 + minutes * 60 + secs
    else:
        raise ValueError(DURATION_HELP)
    if not math.isfinite(seconds) or not 5 <= seconds <= 3600:
        raise ValueError(DURATION_HELP)
    return round(seconds, 3)


def duration_ladder(min_len, max_len, preferred_len=None):
    """Sorted duration targets: base ladder, range endpoints, preferred value."""
    if min_len > max_len:
        raise ValueError("Minimum clip length cannot exceed maximum clip length.")
    values = {x for x in BASE_LADDER if min_len <= x <= max_len}
    values.update((min_len, max_len))
    if preferred_len is not None:
        values.add(int(min(max(preferred_len, min_len), max_len)))
    return sorted(values)


def normalize_seed_indices(sentences, seed_indices=None):
    """Validated, de-duplicated, sorted seed indices for discovery."""
    if seed_indices is None:
        return list(range(len(sentences)))
    seen, result = set(), []
    for index in seed_indices:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("Seed indices must be integers.")
        if not 0 <= index < len(sentences):
            raise ValueError("Seed index is outside the transcript.")
        if index not in seen:
            seen.add(index)
            result.append(index)
    return sorted(result)


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
    words = data.get("words")
    if not isinstance(words, list):
        raise SystemExit("transcript.json has no words - was the video silent?")
    if not words:
        duration = data.get("duration")
        usable_media = (bool(data.get("video"))
                        and isinstance(duration, (int, float))
                        and not isinstance(duration, bool)
                        and duration > 0)
        if not usable_media:
            raise SystemExit("transcript.json has no words - was the video silent?")
    return data


ARC_RE = re.compile(r"[.!?\u2026][\"')\]]?$")


def is_arc_complete(text):
    """True when a sentence ends with terminal punctuation (a complete thought)."""
    return bool(ARC_RE.search((text or "").strip()))


def sentenceize(words, gap=1.2):
    """Split a word list into sentences on terminal punctuation or >gap-second pauses."""
    sentences, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"]) > gap:
            sentences.append(cur)
            cur = []
        cur.append(w)
        if is_arc_complete(w["word"]):
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

META_OPENERS = ("welcome back", "welcome to", "in this video", "today we",
                "before we start", "quick intro")
META_OUTROS = ("thanks for watching", "subscribe", "like and comment",
               "hit the bell", "see you next time", "sponsor")
ANAPHORIC_OPENERS = ("this", "that", "it", "he", "she", "they", "those", "these")
EARLIER_REFERENCES = ("as i said", "like i mentioned", "earlier", "last time",
                      "previous video", "as you know")
INTENSITY_WORDS = ("insane", "crazy", "unbelievable", "massive", "huge",
                   "terrified", "furious", "incredible", "disgusting", "wild",
                   "forever", "broke", "destroyed", "brutal")
REACTION_PHRASES = ("no way", "let's go", "what the", "oh my god", "are you kidding")
PAYOFF_WORDS = ("so", "therefore", "that is why", "the result", "it turned out",
                "in the end", "changed", "finally", "which means", "the lesson",
                "turned out", "which is why")
CURIOSITY_CUES = ("secret", "nobody tells", "truth", "reason", "mistake",
                  "hidden", "discovery", "nobody knows", "what happened")
STAKES_WORDS = ("lose", "losing", "lost", "risk", "money", "business", "life",
                "death", "everything", "nothing", "fail", "failure", "broke",
                "fired", "quit", "divorce", "war", "fight", "survive", "broke")
SUPERLATIVE_WORDS = ("best", "worst", "biggest", "most", "least", "never",
                     "always", "everyone", "nobody", "ever")
NEGATION_WORDS = ("not", "don't", "dont", "never", "no", "nothing", "can't",
                  "cant", "won't", "wont", "isn't", "isnt", "wasn't", "wasnt")
GAMEPLAY_CUES = ("clutch", "ace", "kill", "win", "gg", "no way", "let's go",
                 "boss", "speedrun", "world record", "one shot", "comeback",
                 "pentakill", "headshot", "round")
STOPWORDS = {"the", "a", "an", "and", "or", "but", "to", "of", "in", "on",
             "for", "with", "is", "are", "was", "were", "be", "been", "it",
             "this", "that", "you", "your", "i", "my", "we", "our", "me",
             "at", "as", "so", "if", "then", "than", "have", "has", "had",
             "do", "does", "did", "will", "would", "can", "could", "just"}

TEXT_WEIGHTS = {
    "hook": 0.18, "payoff": 0.16, "self_containment": 0.12, "arc": 0.12,
    "energy": 0.11, "quotability": 0.10, "emotion": 0.08,
    "controversy": 0.07, "humour": 0.06,
}
WEIGHTS = TEXT_WEIGHTS  # backward-compatible alias for third-party imports


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def calibrate_text(raw):
    """Map a 0-1 rubric value onto the absolute 0-10 score scale."""
    return round(10.0 * clamp01(1.35 * clamp01(raw) - 0.08), 2)


def _cue_hits(low, group):
    return sum(1 for cue in group if cue in low)


def _tokens(text):
    return [token for token in (_norm_token(word) for word in (text or "").split())
            if token]


def _max_word_gap(words):
    gap = 0.0
    for first, second in zip(words, words[1:]):
        gap = max(gap, float(second.get("start", 0.0)) - float(first.get("end", 0.0)))
    return gap


def _has_repeated_bigram(tokens):
    bigrams = list(zip(tokens, tokens[1:]))
    return len(bigrams) != len(set(bigrams))


def _has_false_start(tokens):
    return any(first == second for first, second in zip(tokens, tokens[1:]))


def _has_filler_sequence(tokens):
    return any((first, second) in FILLER_SEQS
               for first, second in zip(tokens, tokens[1:]))


def _quotability(tokens, low, text):
    score = 0.0
    if 2 <= len(tokens) <= 12:
        score += 0.35
    if any(cue in low for cue in (" but ", " not ", " instead ", " however ",
                                  " yet ", " without ")):
        score += 0.20
    if text.count(",") >= 2 or low.count(" and ") >= 2:
        score += 0.20
    if any(ch.isdigit() for ch in text) and any(
            word in low for word in (" is ", " was ", " will ", " can ")):
        score += 0.20
    if any(word in low for word in (" forever ", " everything ", " nothing ",
                                    " truth ", " life ", " never ")):
        score += 0.25
    return clamp01(score)


def sentence_features(sent):
    """Cheap per-sentence features shared by every window score."""
    text = sent.get("text", "")
    low = " " + text.lower() + " "
    tokens = _tokens(text)
    words = sent.get("words") or []
    duration = max(0.1, float(sent["end"]) - float(sent["start"]))
    filler_count = sum(token in FILLER_TOKENS for token in tokens)
    if _has_filler_sequence(tokens):
        filler_count += 1
    return {
        "start": float(sent["start"]), "end": float(sent["end"]),
        "duration": duration, "words": len(tokens), "text": text,
        "tokens": tokens, "clean_words": max(1, len(tokens) - filler_count),
        "wps": len(tokens) / duration,
        "terminal": is_arc_complete(text),
        "filler_count": filler_count,
        "filler_ratio": filler_count / max(1, len(tokens)),
        "meta_open": any(phrase in low for phrase in META_OPENERS),
        "meta_outro": any(phrase in low for phrase in META_OUTROS),
        "anaphoric": bool(tokens) and tokens[0] in ANAPHORIC_OPENERS,
        "earlier_ref": any(phrase in low for phrase in EARLIER_REFERENCES),
        "question": text.strip().endswith("?") or " what if " in low,
        "number": bool(re.search(
            r"\b\d[\d,.]*\b|\b(one|two|three|ten|hundred|thousand|million|billion)\b",
            low)),
        "second_person": any(token in tokens for token in
                             ("you", "your", "yours", "yourself")),
        "first_person": any(token in tokens for token in
                            ("i", "me", "my", "mine", "we", "our", "us")),
        "hook": _cue_hits(low, HOOK_CUES),
        "payoff_cue": _cue_hits(low, PAYOFF_WORDS),
        "curiosity": _cue_hits(low, CURIOSITY_CUES),
        "stakes": _cue_hits(low, STAKES_WORDS),
        "superlative": _cue_hits(low, SUPERLATIVE_WORDS),
        "negation": _cue_hits(low, NEGATION_WORDS),
        "emotion": _cue_hits(low, EMOTION_CUES),
        "controversy": _cue_hits(low, CONTROVERSY_CUES),
        "humour": _cue_hits(low, HUMOUR_CUES),
        "insight": _cue_hits(low, INSIGHT_CUES),
        "game_cue": _cue_hits(low, GAMEPLAY_CUES),
        "intensity": _cue_hits(low, INTENSITY_WORDS),
        "laughter": _cue_hits(low, HUMOUR_CUES),
        "reaction": _cue_hits(low, REACTION_PHRASES),
        "personal_stakes": int(any(
            token in tokens for token in ("i", "me", "my", "we", "our", "us"))) * _cue_hits(low, STAKES_WORDS),
        "exclamation": text.count("!"),
        "caps": sum(1 for word in text.split()
                    if len(word) >= 3 and word.isupper()),
        "repeated": bool(re.search(r"(.)\1{2,}", text.lower())),
        "quotability": _quotability(tokens, low, text),
        "repeated_bigram": _has_repeated_bigram(tokens),
        "false_start": _has_false_start(tokens),
        "max_gap": _max_word_gap(words),
    }


PREFIX_KEYS = (
    "hook", "payoff_cue", "curiosity", "stakes", "superlative", "negation",
    "earlier_ref", "emotion", "reaction", "personal_stakes", "controversy",
    "humour", "exclamation", "caps", "repeated", "intensity", "laughter",
    "game_cue",
    "filler_count", "repeated_bigram", "false_start", "words",
)


def build_prefix_sums(features):
    """Cumulative totals for the numeric sentence features."""
    prefix = {key: [0] for key in PREFIX_KEYS}
    for feature in features:
        for key in PREFIX_KEYS:
            value = feature.get(key, 0)
            if isinstance(value, bool):
                value = int(value)
            prefix[key].append(prefix[key][-1] + value)
    return prefix


def _span_sum(index, key, a, b):
    """Sum one numeric feature over sentences[a..b] inclusive."""
    if a > b:
        return 0
    return index.prefix[key][b + 1] - index.prefix[key][a]


class WindowIndex:
    """Per-sentence features plus fast window scoring."""

    def __init__(self, sentences, source_wps=None):
        self.sentences = sentences
        self.features = [sentence_features(sent) for sent in sentences]
        self.prefix = build_prefix_sums(self.features)
        rates = sorted(f["wps"] for f in self.features if f["words"])
        if source_wps is not None:
            self.source_wps = max(0.5, float(source_wps))
        elif rates:
            middle = len(rates) // 2
            self.source_wps = max(0.5, (rates[middle] if len(rates) % 2
                                         else (rates[middle - 1] + rates[middle]) / 2))
        else:
            self.source_wps = 2.5

    def score(self, a, b):
        return _score_indexed(self, a, b)


def _score_indexed(index, a, b):
    features = index.features[a:b + 1]
    if not features:
        return ({key: 0.0 for key in TEXT_WEIGHTS} |
                {"penalties": 0.2, "text_score": 0.0}), 0.0, 0.0
    first, last = features[0], features[-1]
    duration = max(0.1, last["end"] - first["start"])
    total_words = _span_sum(index, "words", a, b)
    wps = total_words / duration

    hook_cues = (_span_sum(index, "hook", a, min(b, a + 1))
                 + first["curiosity"] + first["stakes"]
                 + first["superlative"] + first["negation"])
    hook = (0.45 * min(1.0, hook_cues / 4.0)
            + 0.15 * first["question"] + 0.10 * first["number"]
            + 0.10 * first["second_person"]
            + 0.10 * min(1.0, first["curiosity"] / 2.0)
            + 0.10 * min(1.0, first["stakes"] / 2.0))
    if first["meta_open"]:
        hook -= 0.25
    hook = clamp01(hook)

    first_tokens = set(first["tokens"]) - STOPWORDS
    last_tokens = set(last["tokens"]) - STOPWORDS
    overlap = 1.0 if first_tokens & last_tokens else 0.0
    payoff_cues = _span_sum(index, "payoff_cue", max(a, b - 1), b)
    payoff = (0.35 * last["terminal"]
              + 0.25 * min(1.0, payoff_cues / 2.0)
              + 0.20 * overlap
              + 0.20 * (1.0 - min(1.0, last["filler_ratio"] * 3.0)))
    payoff = clamp01(payoff)

    start_clean = not first["anaphoric"] and not first["meta_open"]
    no_earlier = _span_sum(index, "earlier_ref", a, b) == 0
    self_containment = (0.35 * start_clean + 0.25 * no_earlier
                        + 0.20 * last["terminal"] + 0.20)
    self_containment = clamp01(self_containment)

    halfway = a + max(1, (b - a + 1) // 2)
    tension = (_span_sum(index, "stakes", a, halfway - 1)
               + _span_sum(index, "emotion", a, halfway - 1)
               + _span_sum(index, "controversy", a, halfway - 1)) > 0
    resolution = _span_sum(index, "payoff_cue", halfway, b) > 0
    arc = (0.30 * (len(features) >= 2) + 0.30 * last["terminal"]
           + 0.40 * (tension and resolution))
    arc = clamp01(arc)

    rate = clamp01((wps / max(0.5, index.source_wps)) / 1.35)
    marks = min(1.0, (_span_sum(index, "exclamation", a, b)
                      + _span_sum(index, "caps", a, b)
                      + _span_sum(index, "repeated", a, b)
                      + _span_sum(index, "intensity", a, b)
                      + _span_sum(index, "laughter", a, b)
                      + _span_sum(index, "game_cue", a, b)) / 3.0)
    onset = clamp01(first["wps"] / 3.5)
    energy = clamp01(0.50 * rate + 0.30 * marks + 0.20 * onset)

    quotability = max(feature["quotability"] for feature in features)
    emotion_hits = (_span_sum(index, "emotion", a, b)
                    + _span_sum(index, "reaction", a, b)
                    + _span_sum(index, "personal_stakes", a, b))
    emotion = clamp01(emotion_hits / 3.0)
    controversy = clamp01(_span_sum(index, "controversy", a, b) / 2.0)
    humour = clamp01(_span_sum(index, "humour", a, b) / 2.0)

    total_filler = _span_sum(index, "filler_count", a, b)
    filler_penalty = min(0.08, (total_filler / max(1, total_words)) * 0.6)
    meta_penalty = 0.15 if first["meta_open"] or last["meta_outro"] else 0.0
    repeat_penalty = 0.05 if (_span_sum(index, "repeated_bigram", a, b)
                              + _span_sum(index, "false_start", a, b)) else 0.0
    silence_penalty = min(0.08, max(0.0, max(
        (feature["max_gap"] for feature in features), default=0.0) - 2.5) * 0.03)
    mid_penalty = 0.0 if last["terminal"] else 0.20
    low_density = 0.0
    if duration > 120:
        low_density = min(0.10, (duration - 120) / 600 * 0.10
                          + (0.05 if wps < 1.2 else 0.0))
    empty_penalty = 0.20 if total_words == 0 else 0.0
    penalties = clamp01(filler_penalty + meta_penalty + repeat_penalty
                        + silence_penalty + mid_penalty + low_density
                        + empty_penalty)

    parts = {
        "hook": round(hook, 3), "payoff": round(payoff, 3),
        "self_containment": round(self_containment, 3),
        "arc": round(arc, 3), "energy": round(energy, 3),
        "quotability": round(quotability, 3), "emotion": round(emotion, 3),
        "controversy": round(controversy, 3), "humour": round(humour, 3),
    }
    weighted = sum(TEXT_WEIGHTS[key] * value for key, value in parts.items())
    raw_score = clamp01(weighted - penalties)
    text_score = calibrate_text(raw_score)
    parts["penalties"] = round(penalties, 3)
    parts["text_score"] = text_score
    return parts, text_score, round(raw_score, 3)


def score_window(sentences, a, b, source_wps=None, index=None):
    """Score sentences[a..b]. Return (parts, text_score, raw_score)."""
    index = index or WindowIndex(sentences, source_wps)
    return index.score(a, b)


def score_sentence(sent, source_wps=None):
    parts, text_score, _ = score_window([sent], 0, 0, source_wps)
    return {key: parts[key] for key in TEXT_WEIGHTS}, text_score


def score_reason(parts):
    """One short, deterministic explanation from the strongest/weakest parts."""
    criteria = {key: value for key, value in parts.items()
                if key in TEXT_WEIGHTS}
    if not criteria:
        return "No score parts"
    labels = {"hook": "hook", "payoff": "payoff", "self_containment": "setup",
              "arc": "arc", "energy": "energy", "quotability": "quotability",
              "emotion": "emotion", "controversy": "controversy",
              "humour": "humour"}
    strong = sorted(criteria, key=lambda key: (-criteria[key], key))[:2]
    weak = min(criteria, key=lambda key: (criteria[key], key))
    text = f"Strong {labels[strong[0]]} and {labels[strong[1]]}; weak {labels[weak]}"
    if parts.get("penalties", 0) >= 0.15:
        text += "; boundary or meta penalty"
    return text[:160]


def first_sentence_at_or_after(ends, target):
    """Index of the first sentence end at or after target seconds."""
    return bisect.bisect_left(ends, target)


def choose_seed_endpoints(choices, preferred_len=None, band=(30, 120)):
    """Return up to two endpoint records: quality_best then preferred_best."""
    if not choices:
        return []
    target = preferred_len if preferred_len is not None else sum(band) / 2.0

    quality = min(choices, key=lambda item: (
        -item["overall"], item["duration"], item["start"], item["end"]))
    quality = dict(quality, endpoint_kind="quality_best")

    near = [item for item in choices
            if item["overall"] >= quality["overall"] - 0.15 - 1e-9]
    preferred = min(near, key=lambda item: (
        abs(item["duration"] - target), -item["overall"],
        item["duration"], item["end"]))
    if preferred["end"] == quality["end"]:
        return [quality]
    preferred = dict(preferred, endpoint_kind="preferred_best")
    return [quality, preferred]


def candidate_record(index, seed, end_index, endpoint_kind="quality_best"):
    """Build one candidate record from a scored sentence range."""
    parts, text_score, _ = index.score(seed, end_index)
    start = float(index.sentences[seed]["start"])
    end = float(index.sentences[end_index]["end"])
    text = " ".join(word["word"] for sentence_ in index.sentences[seed:end_index + 1]
                    for word in sentence_["words"])
    return {
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": round(end - start, 2),
        "hook": make_hook(index.sentences[seed]["text"]),
        "text": text,
        "raw_score": text_score,
        "overall": text_score,
        "score_parts": parts,
        "profile": "mixed",
        "seed_index": seed,
        "seed_kind": "sentence",
        "endpoint_kind": endpoint_kind,
        "reason": score_reason(parts),
    }


MEDIA_TEXT_FLOOR = 7.0


def media_candidate_record(start, end, seed_kind="event",
                           profile="gameplay/action"):
    """Build a wordless candidate; evidence decides whether it passes."""
    start, end = round(float(start), 2), round(float(end), 2)
    label = "event" if seed_kind == "event" else "action"
    return {
        "start": start, "end": end, "duration": round(end - start, 2),
        "hook": f"{label} at {int(start)}s", "text": "",
        "raw_score": MEDIA_TEXT_FLOOR, "overall": MEDIA_TEXT_FLOOR,
        "score_parts": {key: 0.0 for key in TEXT_WEIGHTS},
        "profile": profile, "seed_index": None,
        "seed_kind": seed_kind, "endpoint_kind": "quality_best",
        "reason": "No words; scored on media evidence",
    }


def discover_media_candidates(timeline, min_len, max_len, preferred_len=None,
                              band=(15, 60), profile="gameplay/action",
                              source_energy=0.5, duration=None,
                              visual_baseline=None, audio_usable=None):
    """Build event or grid seeds for a source with no transcript words.

    Detected audio peaks become ``event`` seeds. A usable but peakless audio
    track or a varying visual baseline falls back to evenly spaced ``grid``
    seeds. Every candidate is scored from media evidence; there is no text.
    """
    source_duration = float(duration or getattr(timeline, "duration", 0) or 0)
    if audio_usable is None:
        audio_usable = evidence.timeline_has_signal(timeline)
    usable_timeline = timeline if audio_usable else None
    peaks = (usable_timeline.peak_times(min_gap=10.0)
             if usable_timeline is not None else [])
    visual_varies = (visual_baseline is not None
                     and (visual_baseline.value > 0.02
                          or visual_baseline.cut_rate > 0.0))
    if peaks:
        seeds = [(max(0.0, float(peak) - 2.0), "event") for peak in peaks]
    elif usable_timeline is not None or visual_varies:
        step = max(30.0, float(max_len) / 2.0)
        seeds = []
        position = 0.0
        while position < source_duration:
            seeds.append((position, "grid"))
            position += step
    else:
        raise ValueError(
            "No speech and no media evidence; this source cannot be scored.")

    ladder = duration_ladder(min_len, max_len, preferred_len)
    candidates = []
    windows_scanned = 0
    for seed, seed_kind in seeds:
        choices = {}
        for target in ladder:
            if usable_timeline is not None:
                start = usable_timeline.trough_near(seed, radius=1.5)
                end_target = min(source_duration, seed + target)
                end = usable_timeline.trough_near(end_target, radius=1.5)
            else:
                start = max(0.0, seed)
                end = min(source_duration, seed + target)
            if end - start < min_len - 1e-9 or end - start > max_len + 1e-9:
                continue
            key = (round(start, 2), round(end, 2))
            if key in choices:
                continue
            windows_scanned += 1
            record = media_candidate_record(
                start, end, seed_kind=seed_kind, profile=profile)
            audio = (evidence.audio_evidence(usable_timeline, record["start"],
                                             record["end"])
                     if usable_timeline is not None else None)
            record = evidence.apply_evidence(
                record,
                audio=None if audio is None else audio["score"],
                profile=profile, source_energy=source_energy,
                words_per_second=0.0,
                action_arc=None if audio is None else audio["event_arc"])
            choices[key] = record
        if choices:
            candidates.extend(choose_seed_endpoints(
                list(choices.values()), preferred_len, band))

    if not candidates:
        raise ValueError(
            "No media windows fit the requested clip length; lower the minimum "
            "length or raise the maximum length.")
    candidates.sort(key=lambda item: (item["start"], item["end"]))
    stats = {
        "seeds": len(seeds),
        "windows_scanned": windows_scanned,
        "source_duration": source_duration,
        "content_profile": profile,
        "source_energy": source_energy,
    }
    return candidates, stats


def discover_candidates(sentences, min_len=15, max_len=300,
                        preferred_len=None, band=(30, 120),
                        seed_indices=None, progress=None):
    """Evaluate every seed against the duration ladder.

    Returns (start-sorted candidates, stats). No threshold is applied here.
    """
    if min_len > max_len:
        raise ValueError("Minimum clip length cannot exceed maximum clip length.")
    empty_stats = {"seeds": 0, "windows_scanned": 0, "source_duration": 0.0,
                   "content_profile": "mixed", "source_energy": 0.5}
    if not sentences:
        return [], empty_stats

    ladder = duration_ladder(min_len, max_len, preferred_len)
    index = WindowIndex(sentences)
    seeds = normalize_seed_indices(sentences, seed_indices)
    ends = [float(sentence_["end"]) for sentence_ in sentences]
    last_index = len(sentences) - 1
    candidates = []
    windows_scanned = 0
    total = len(seeds)

    def evaluate(seed, end_index, choices):
        if end_index < seed or end_index > last_index:
            return
        if not is_arc_complete(sentences[end_index]["text"]):
            return
        duration = ends[end_index] - sentences[seed]["start"]
        if duration < min_len or duration > max_len:
            return
        if end_index not in choices:
            choices[end_index] = candidate_record(index, seed, end_index)

    for done, seed in enumerate(seeds, 1):
        start = sentences[seed]["start"]
        windows_scanned += len(ladder)
        if ends[seed] - start <= max_len:
            choices = {}
            for target in ladder:
                j0 = first_sentence_at_or_after(ends, start + target)
                low = max(seed, j0 - 2)
                high = min(last_index, j0 + 2)
                for end_index in range(low, high + 1):
                    evaluate(seed, end_index, choices)
            if choices:
                best = max(choices, key=lambda end_index: (
                    choices[end_index]["overall"],
                    -choices[end_index]["duration"], -end_index))
                low = max(seed, best - 2)
                high = min(last_index, best + 2)
                for end_index in range(low, high + 1):
                    evaluate(seed, end_index, choices)
                candidates.extend(choose_seed_endpoints(
                    list(choices.values()), preferred_len, band))
        if progress and done % 500 == 0:
            progress("scanning", done, total)

    candidates.sort(key=lambda item: (item["start"], item["end"]))
    stats = {
        "seeds": len(seeds),
        "windows_scanned": windows_scanned,
        "source_duration": float(sentences[-1]["end"]),
        "content_profile": "mixed",
        "source_energy": 0.5,
    }
    return candidates, stats


def overlap_ratio(first, second):
    """Overlap divided by the shorter clip duration; 0 when they do not touch."""
    overlap = max(0.0, min(first["end"], second["end"])
                  - max(first["start"], second["start"]))
    shorter = min(first["end"] - first["start"], second["end"] - second["start"])
    return overlap / shorter if shorter > 0 else 0.0


def inside_band(item, preferred_len, band):
    """True when a duration sits inside the preferred or source-appropriate band."""
    if preferred_len is not None:
        tolerance = 0.25 * preferred_len
        low, high = preferred_len - tolerance, preferred_len + tolerance
    else:
        low, high = band
    return low <= item["duration"] <= high


def _validated_candidate(item):
    record = dict(item)
    try:
        start = float(record["start"])
        end = float(record["end"])
        overall = float(record.get("overall", 0.0))
    except (KeyError, TypeError, ValueError):
        raise ValueError("Candidate times and scores must be finite numbers.")
    if not all(math.isfinite(value) for value in (start, end, overall)) or end <= start:
        raise ValueError("Candidate times and scores must be finite numbers.")
    record["start"] = start
    record["end"] = end
    record["duration"] = end - start
    record["overall"] = overall
    return record


def dedupe_candidates(candidates, preferred_len=None, band=(30, 120),
                      min_score=None):
    """Keep one strong representative per overlapping run of windows."""
    records = sorted((_validated_candidate(item) for item in candidates),
                     key=lambda item: (-item["overall"], item["start"],
                                       item["duration"]))
    kept = []
    for record in records:
        drop = False
        for index, existing in enumerate(kept):
            ratio = overlap_ratio(record, existing)
            if ratio > 0.55:
                replacement = (
                    abs(record["overall"] - existing["overall"]) <= 0.25 + 1e-9
                    and record["duration"] < existing["duration"]
                    and inside_band(record, preferred_len, band)
                    and (min_score is None or record["overall"] >= min_score - 1e-9))
                if replacement:
                    kept[index] = record
                    drop = True
                    break
                if record["overall"] > existing["overall"] + 1e-9:
                    kept.pop(index)
                    break
                drop = True
                break
            if (abs(record["start"] - existing["start"]) <= 1.5
                    and ratio > 0.30):
                drop = True
                break
        if not drop:
            kept.append(record)
    kept.sort(key=lambda item: (item["start"], item["end"]))
    return kept


PUBLIC_SEGMENT_KEYS = ("start", "end", "hook", "overall", "duration", "rank",
                       "raw_score", "score_parts", "reason")


def _public_segment(item):
    segment = {
        "start": item["start"],
        "end": item["end"],
        "hook": item.get("hook", "clip"),
        "overall": item["overall"],
        "duration": round(float(item.get("duration", item["end"] - item["start"])), 2),
        "raw_score": item.get("raw_score", item["overall"]),
        "score_parts": item.get("score_parts", {}),
        "reason": str(item.get("reason", ""))[:160],
    }
    if item.get("evidence") is not None:
        segment["evidence"] = item["evidence"]
    return segment


def select_candidates(candidates, min_score=8.0, max_clips=None,
                      preferred_len=None, band=(30, 120), dedupe=True,
                      discovery=None, mode="heuristic",
                      evidence_available=None):
    """Dedupe, threshold, cap, rank, and return (segments, selection_stats)."""
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) \
            or not math.isfinite(min_score) or not 0 <= min_score <= 10:
        raise ValueError("Minimum score must be between 0 and 10.")
    if max_clips is not None and (isinstance(max_clips, bool)
                                  or not isinstance(max_clips, int)
                                  or max_clips < 1):
        raise ValueError("Maximum clips must be a positive integer or None.")
    discovery = discovery or {}
    records = dedupe_candidates(candidates, preferred_len, band, min_score) \
        if dedupe else [_validated_candidate(item) for item in candidates]
    passing = [item for item in records if item["overall"] >= min_score]
    if max_clips is not None:
        passing = sorted(passing, key=lambda item: (
            -item["overall"], item["start"], item["duration"]))[:max_clips]

    public = [_public_segment(item) for item in passing]
    ranked = sorted(public, key=lambda item: (
        -item["overall"], item["start"], item["duration"]))
    for rank, segment in enumerate(ranked, 1):
        segment["rank"] = rank
    public.sort(key=lambda item: (item["start"], item["end"]))
    segments = []
    for segment in public:
        record = {key: segment[key] for key in PUBLIC_SEGMENT_KEYS}
        if "evidence" in segment:
            record["evidence"] = segment["evidence"]
        segments.append(record)

    if evidence_available is None:
        evidence_available = discovery.get("evidence_available")
    if evidence_available is None:
        evidence_available = {
            "audio": any((item.get("evidence") or {}).get("audio") is not None
                         for item in candidates),
            "visual": any((item.get("evidence") or {}).get("visual") is not None
                          for item in candidates),
        }
    availability = {"audio": bool(evidence_available.get("audio")),
                    "visual": bool(evidence_available.get("visual"))}

    scores = [item["overall"] for item in records]
    durations = [item["duration"] for item in records]
    passing_scores = [item["overall"] for item in passing]
    stats = {
        "mode": mode,
        "source_duration": discovery.get("source_duration"),
        "seeds": discovery.get("seeds", 0),
        "windows_scanned": discovery.get("windows_scanned", 0),
        "candidates": len(records),
        "passing": len(passing),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "passing_score_min": min(passing_scores) if passing_scores else None,
        "passing_score_max": max(passing_scores) if passing_scores else None,
        "duration_min": min(durations) if durations else None,
        "duration_max": max(durations) if durations else None,
        "min_score": min_score,
        "preferred_len": preferred_len,
        "analysis_floor": min(8.0, min_score),
        "content_profile": discovery.get("content_profile", "mixed"),
        "source_energy": discovery.get("source_energy", 0.5),
        "evidence_available": availability,
        "visual_baseline": discovery.get("visual_baseline"),
        "visual_ranges": discovery.get("visual_ranges", 0),
        "visual_sections": discovery.get("visual_sections", 0),
        "warnings": discovery.get("warnings", []),
    }
    return segments, stats


CANDIDATES_VERSION = 1


def _write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               allow_nan=False), encoding="utf-8")
    temp.replace(path)


def save_candidates(workdir, candidates, stats, mode="heuristic"):
    """Write the threshold-independent candidates.json envelope."""
    envelope = {
        "version": CANDIDATES_VERSION,
        "mode": mode,
        "content_profile": stats.get("content_profile", "mixed"),
        "source_energy": stats.get("source_energy", 0.5),
        "source_duration": stats.get("source_duration"),
        "seeds": stats.get("seeds", 0),
        "windows_scanned": stats.get("windows_scanned", 0),
        "evidence_available": stats.get(
            "evidence_available", {"audio": False, "visual": False}),
        "visual_baseline": stats.get("visual_baseline"),
        "visual_ranges": stats.get("visual_ranges", 0),
        "visual_sections": stats.get("visual_sections", 0),
        "warnings": stats.get("warnings", []),
        "candidates": list(candidates),
    }
    _write_json(Path(workdir) / "candidates.json", envelope)


def load_candidates(workdir):
    """Return (candidates, stats) from candidates.json."""
    path = Path(workdir) / "candidates.json"
    if not path.exists():
        raise SystemExit(f"No candidates at {path} - run discovery first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"candidates.json is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise SystemExit("candidates.json is missing a candidate list.")
    stats = {key: data.get(key) for key in
             ("source_duration", "content_profile", "source_energy", "seeds",
              "windows_scanned", "mode", "evidence_available",
              "visual_baseline", "visual_ranges", "visual_sections",
              "warnings")}
    return data["candidates"], stats


def make_hook(text, max_words=7):
    words = [_norm_token(w) for w in text.split()]
    words = [w for w in words if w and w not in FILLER_TOKENS]
    return " ".join(words[:max_words]) or "clip"


def _norm_token(w):
    return re.sub(r"[^\w']", "", w.lower())


def _grow_window(sentences, seed, taken, min_len, max_len):
    """Grow a window from `seed` to >= min_len seconds, then extend or trim it so
    it always ends on a complete sentence (the #1 complaint about AI clippers is
    clips that start or end mid-thought). Returns (seed, end_idx) or None."""
    n = len(sentences)

    def span(a, b):
        return sentences[b]["end"] - sentences[a]["start"]

    j = seed
    while span(seed, j) < min_len and j + 1 < n and not taken[j + 1]:
        j += 1
    while span(seed, j) > max_len and j > seed:
        j -= 1
    if span(seed, j) < min_len * 0.6:
        return None
    # complete-thought rule: keep growing (within max_len) until the last
    # sentence ends with terminal punctuation
    while not is_arc_complete(sentences[j]["text"]) and j + 1 < n \
            and not taken[j + 1] and span(seed, j + 1) <= max_len:
        j += 1
    # still incomplete -> back off to the last complete sentence in the window
    while j > seed and not is_arc_complete(sentences[j]["text"]):
        j -= 1
    if not is_arc_complete(sentences[j]["text"]):
        return None
    if span(seed, j) < min_len * 0.6:
        return None
    return seed, j


def _make_segment(sentences, a, b):
    """Score the sentence window sentences[a..b] and build a segment dict."""
    win = sentences[a:b + 1]
    words = [w for s in win for w in s["words"]]
    text = " ".join(w["word"] for w in words)
    scores, _ = score_sentence({"start": win[0]["start"], "end": win[-1]["end"],
                                "text": text, "words": words})
    return {
        "start": round(win[0]["start"], 2),
        "end": round(win[-1]["end"], 2),
        "hook": make_hook(win[0]["text"]),
        "overall": round(sum(WEIGHTS[k] * v for k, v in scores.items()) * 10.0, 1),
    }


def build_candidates(sentences, min_len, max_len, max_clips=None,
                     min_score=None, preferred_len=None):
    """Start-sorted deduped candidates, including those below the threshold."""
    found, _ = discover_candidates(sentences, min_len, max_len,
                                   preferred_len=preferred_len)
    kept = dedupe_candidates(found, preferred_len=preferred_len,
                             min_score=min_score)
    if max_clips is not None:
        kept = sorted(kept, key=lambda item: (
            -item["overall"], item["start"], item["duration"]))[:max_clips]
    return sorted(kept, key=lambda item: (item["start"], item["end"]))


# ----------------------------------------------------------------- search/pick

def search_hits(sentences, query):
    """Indexes of sentences containing ALL whitespace-separated query tokens
    (case-insensitive). Empty-token queries match nothing."""
    tokens = [_norm_token(t) for t in (query or "").split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    hits = []
    for i, s in enumerate(sentences):
        low = " " + s["text"].lower() + " "
        if all(t in low for t in tokens):
            hits.append(i)
    return hits


def search_candidates(sentences, query, min_len, max_len, max_clips=None):
    """Build scored clips around every sentence matching `query` (keyword search
    over the transcript - the feature users call a dealbreaker when missing)."""
    hits = search_hits(sentences, query)
    if not hits:
        return []
    found, _ = discover_candidates(sentences, min_len, max_len, seed_indices=hits)
    kept = dedupe_candidates(found)
    if max_clips is not None:
        kept = sorted(kept, key=lambda item: (
            -item["overall"], item["start"], item["duration"]))[:max_clips]
    return sorted(kept, key=lambda item: (item["start"], item["end"]))


def pick_segments(segments, spec):
    """Keep only the given 1-based indexes ('1,3,5') of the start-sorted list -
    the review workflow: look at the printed table, render just the winners."""
    if not spec:
        return segments
    want = set()
    for tok in spec.replace(" ", "").split(","):
        if not tok:
            continue
        if not tok.isdigit():
            raise SystemExit(f"--clips: '{tok}' is not a number (use e.g. --clips 1,3,5)")
        want.add(int(tok))
    n = len(segments)
    bad = sorted(i for i in want if i < 1 or i > n)
    if bad:
        raise SystemExit(
            f"--clips: index(es) {bad} out of range 1..{n} "
            f"(see the numbered table above)")
    return [s for i, s in enumerate(segments, 1) if i in want]


def format_duration(seconds, compact=False):
    """Format seconds as 43.7s, 2m 22s, or 1h 02m."""
    if seconds is None:
        return "?"
    seconds = max(0.0, float(seconds))
    if compact and seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def format_selection_summary(stats):
    """Return the three-line selection summary shown by the CLI and Studio."""
    stats = stats or {}
    min_score = stats.get("min_score")
    if min_score is None:
        min_score = 8.0
    preferred = stats.get("preferred_len")
    preferred_text = (format_duration(preferred) if preferred is not None
                      else "AI chooses")
    lines = [f"Selection: minimum score {min_score:.1f}, preferred length "
             f"{preferred_text}."]
    lines.append(f"Scanned {int(stats.get('windows_scanned') or 0):,} windows "
                 f"across {format_duration(stats.get('source_duration'))}.")
    low, high = stats.get("score_min"), stats.get("score_max")
    passing = int(stats.get("passing") or 0)
    if low is None or high is None:
        lines.append(f"Candidate scores unavailable. {passing} distinct clips "
                     f"at {min_score:.1f} or above.")
    else:
        lines.append(f"Candidate scores {low:.1f}-{high:.1f}. {passing} distinct "
                     f"clips at {min_score:.1f} or above.")
    return "\n".join(lines)


def print_table(segments):
    """Numbered candidate table - pairs with --clips for the review workflow."""
    print("\n  #  rank  start ->      end  score  duration  hook", flush=True)
    for i, s in enumerate(segments, 1):
        rank = s.get("rank", i)
        duration = s.get("duration", s["end"] - s["start"])
        print(f"  {i:>2}  {rank:>4}  {s['start']:8.2f} -> {s['end']:8.2f}"
              f"  {s['overall']:4.1f}/10  {format_duration(duration, True):>8}"
              f"  {s['hook']}", flush=True)
    print("  render a subset with --clips 1,3,5", flush=True)


def print_explanations(segments):
    """Print score parts and reasons for --explain."""
    for i, segment in enumerate(segments, 1):
        print(f"\n  [{i}] {segment['start']:.2f}s -> {segment['end']:.2f}s"
              f"  {segment['overall']:.1f}/10", flush=True)
        parts = segment.get("score_parts") or {}
        if parts:
            detail = ", ".join(f"{key} {value:.2f}" if isinstance(value, (int, float))
                               else f"{key} {value}" for key, value in parts.items())
            print(f"      parts: {detail}", flush=True)
        if segment.get("reason"):
            print(f"      reason: {segment['reason']}", flush=True)

# ------------------------------------------------------------------- ollama

RERANK_SYSTEM = (
    "You are a short-form video editor reviewing candidate clips. "
    "For each candidate, return a score adjustment between -1.0 and 1.0. "
    "Positive means stronger viral potential; negative means weaker. "
    "Never change timestamps. Respond with JSON only: "
    '[{"index": <int>, "adjustment": <-1..1>, "reason": "<short reason>"}]'
)


def _ollama_chat(model, host, messages, timeout=120):
    """POST to the local Ollama chat endpoint and return parsed JSON content."""
    payload = {
        "model": model, "stream": False, "format": "json",
        "options": {"temperature": 0.2},
        "messages": messages,
    }
    request = urllib.request.Request(
        host.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = json.loads(response.read().decode("utf-8")).get(
            "message", {}).get("content", "")
    match = re.search(r"\[.*\]", content, re.S)
    return json.loads(match.group(0)) if match else []


def _candidate_key(candidate):
    """Stable identity for merging rerank adjustments into candidate records."""
    return (round(float(candidate["start"]), 2), round(float(candidate["end"]), 2),
            candidate.get("seed_index"))


def ollama_rerank(candidates, model, host=OLLAMA_HOST, timeout=120):
    """Adjust the top 50 deduped candidates by -1.0..+1.0.

    Returns an updated list, or None when Ollama is unavailable or returns junk.
    Timestamps are never changed.
    """
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda item: (
        -item["overall"], item["start"], item["duration"]))[:50]
    sent = [{"index": i, "start": item["start"], "end": item["end"],
             "text": str(item.get("text", ""))[:400]}
            for i, item in enumerate(ranked)]
    messages = [
        {"role": "system", "content": RERANK_SYSTEM},
        {"role": "user", "content": json.dumps(sent, ensure_ascii=False)},
    ]
    try:
        raw = _ollama_chat(model, host, messages, timeout)
    except Exception as exc:                                 # noqa: BLE001
        print(f"Ollama unavailable ({exc}) -> deterministic scores", flush=True)
        return None
    if not isinstance(raw, list):
        return None

    adjustments = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        adjustment = entry.get("adjustment")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(ranked):
            continue
        if isinstance(adjustment, bool) or not isinstance(adjustment, (int, float)):
            continue
        if not math.isfinite(adjustment):
            continue
        adjustments[index] = (max(-1.0, min(1.0, float(adjustment))),
                              str(entry.get("reason", ""))[:120])
    if not adjustments:
        return None

    updated = [dict(item) for item in candidates]
    positions = {_candidate_key(item): i for i, item in enumerate(updated)}
    for index, (adjustment, reason) in adjustments.items():
        position = positions.get(_candidate_key(ranked[index]))
        if position is None:
            continue
        record = updated[position]
        base = float(record.get("overall", 0.0))
        previous = record.get("llm_adjustment")
        if isinstance(previous, (int, float)) and not isinstance(previous, bool):
            base -= float(previous)
        record["overall"] = round(max(0.0, min(10.0, base + adjustment)), 2)
        record["llm_adjustment"] = adjustment
        record["llm_model"] = model
        if reason and reason not in str(record.get("reason", "")):
            base_reason = str(record.get("reason", ""))[:160]
            record["reason"] = (base_reason + " | " + reason)[:160]
    return updated


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

def source_evidence(workdir, data):
    """Measure audio, visual, and speech baselines before window selection."""
    source = data.get("video")
    timeline = evidence.load_audio_timeline(source, workdir)
    words = data.get("words") or []
    duration = float(data.get("duration")
                     or getattr(timeline, "duration", 0) or 0)
    speech_density = len(words) / duration if duration else 0.0
    audio_usable = evidence.timeline_has_signal(timeline)
    baseline = visual.sample_baseline(source, workdir, duration)
    visual_value = baseline.value if baseline is not None else None
    cut_rate = baseline.cut_rate if baseline is not None else 0.0
    burst_rate = timeline.burst_rate if audio_usable else 0.0
    profile = evidence.content_profile(
        speech_density, visual_value, cut_rate, burst_rate)
    variability = timeline.variability if audio_usable else None
    energy = evidence.source_energy(variability, visual_value)
    return (timeline, profile, energy, speech_density, audio_usable, baseline)


def candidate_words_per_second(candidate):
    """Words spoken per second inside this candidate's own range."""
    duration = float(candidate["end"]) - float(candidate["start"])
    if duration <= 0:
        return 0.0
    return len(str(candidate.get("text", "")).split()) / duration


def attach_audio_evidence(candidates, timeline, profile, energy):
    """Apply audio evidence to each candidate without changing its raw text score."""
    updated = []
    for candidate in candidates:
        audio = (evidence.audio_evidence(timeline, candidate["start"],
                                          candidate["end"])
                 if timeline is not None else None)
        updated.append(evidence.apply_evidence(
            candidate,
            audio=None if audio is None else audio["score"],
            profile=profile,
            source_energy=energy,
            words_per_second=candidate_words_per_second(candidate),
            action_arc=None if audio is None else audio["event_arc"]))
    return updated


def visual_potential(candidate, profile, energy, audio_usable):
    """Optimistic score ceiling used to decide which ranges deserve frames."""
    raw_score = float(candidate.get("raw_score", candidate["overall"]))
    sparse = candidate_words_per_second(candidate) < 0.8
    weight = evidence.evidence_weight(energy, profile, sparse=sparse)
    audio = (candidate.get("evidence") or {}).get("audio")
    audio_adjustment = (0.0 if audio is None
                        else (float(audio) - 5.0) * 0.6 * weight)
    text_score = max(raw_score, 7.0) if sparse else raw_score
    visual_share = 0.4 if audio_usable else 1.0
    max_visual_bonus = 5.0 * 0.6 * weight * visual_share
    return text_score + audio_adjustment + max_visual_bonus


def visual_shortlist(candidates, profile, energy, audio_usable):
    """Keep text candidates that could still reach 8.0 with visual evidence."""
    shortlisted = []
    for candidate in candidates:
        if candidate.get("seed_kind") != "sentence":
            shortlisted.append(candidate)
        elif visual_potential(candidate, profile, energy,
                              audio_usable) >= 8.0:
            shortlisted.append(candidate)
    return shortlisted


def attach_media_evidence(candidates, sections, timeline, profile, energy,
                          audio_usable, min_len, max_len):
    """Blend audio and visual evidence and refine wordless action boundaries."""
    updated = []
    for candidate in candidates:
        record = dict(candidate)
        media = record.get("seed_kind") != "sentence"
        section = (visual.visual_features_for(
            sections, record["start"], record["end"])
            if sections else None)
        audio_hit = (evidence.audio_evidence(
            timeline, record["start"], record["end"])
            if audio_usable and timeline is not None else None)
        visual_hit = (visual.visual_evidence(
            section, record["start"], record["end"])
            if section is not None else None)
        if media and section is not None:
            new_start, new_end = visual.refine_media_boundary(
                section, timeline if audio_usable else None,
                record["start"], record["end"], min_len, max_len)
            changed = (abs(new_start - float(record["start"])) > 1e-6
                       or abs(new_end - float(record["end"])) > 1e-6)
            new_section = (visual.visual_features_for(
                sections, new_start, new_end)
                if changed else section)
            if changed and new_section is not None:
                new_audio = (evidence.audio_evidence(
                    timeline, new_start, new_end)
                    if audio_usable and timeline is not None else None)
                new_visual = visual.visual_evidence(
                    new_section, new_start, new_end)
                before = evidence.combine_evidence(
                    None if audio_hit is None else audio_hit["score"],
                    None if visual_hit is None else visual_hit["score"])
                after = evidence.combine_evidence(
                    None if new_audio is None else new_audio["score"],
                    None if new_visual is None else new_visual["score"])
                if after > before + 1e-9:
                    record["start"] = round(float(new_start), 2)
                    record["end"] = round(float(new_end), 2)
                    record["duration"] = round(
                        record["end"] - record["start"], 2)
                    label = ("event" if record.get("seed_kind") == "event"
                             else "action")
                    record["hook"] = f"{label} at {int(record['start'])}s"
                    section = new_section
                    audio_hit, visual_hit = new_audio, new_visual
        arcs = [hit["event_arc"] for hit in (audio_hit, visual_hit)
                if hit is not None]
        record = evidence.apply_evidence(
            record,
            audio=None if audio_hit is None else audio_hit["score"],
            visual=None if visual_hit is None else visual_hit["score"],
            profile=profile,
            source_energy=energy,
            words_per_second=candidate_words_per_second(record),
            action_arc=max(arcs) if arcs else None)
        record["profile"] = profile
        updated.append(record)
    return updated


def discover(workdir, min_len=15, max_len=300, preferred_len=None,
             band=None, progress=None):
    """Load the transcript, measure evidence, discover, write candidates.json."""
    workdir = Path(workdir)
    data = load_transcript(workdir)
    sentences = sentenceize(data.get("words") or [])
    (timeline, profile, energy, _, audio_usable,
     baseline) = source_evidence(workdir, data)
    duration = float(data.get("duration")
                     or getattr(timeline, "duration", 0) or 0)
    effective_band = (evidence.profile_band(profile)
                      if band is None else band)
    usable_timeline = timeline if audio_usable else None
    source_duration = duration or float(getattr(timeline, "duration", 0) or 0)
    source = data.get("video")
    if not sentences:
        if usable_timeline is None and baseline is None:
            raise ValueError(
                "No speech and no media evidence; this source cannot be scored.")
        print(f"No transcript words; scanning media events over "
              f"{source_duration:.0f}s", flush=True)
        candidates, stats = discover_media_candidates(
            usable_timeline, min_len, max_len, preferred_len=preferred_len,
            band=effective_band, profile=profile, source_energy=energy,
            duration=source_duration, visual_baseline=baseline,
            audio_usable=audio_usable)
    else:
        print(f"Transcript: {len(sentences)} sentences over "
              f"{duration or 0:.0f}s", flush=True)
        candidates, stats = discover_candidates(
            sentences, min_len, max_len, preferred_len=preferred_len,
            band=effective_band, progress=progress)
        candidates = attach_audio_evidence(
            candidates, usable_timeline, profile, energy)

    shortlist = visual_shortlist(candidates, profile, energy, audio_usable)
    visual_ranges = visual.merge_ranges(shortlist, source_duration)
    sections = (visual.load_or_analyze(source, workdir, visual_ranges)
                if visual_ranges else [])
    candidates = attach_media_evidence(
        candidates, sections, usable_timeline, profile, energy, audio_usable,
        min_len, max_len)
    warnings = []
    if visual_ranges and source and Path(source).is_file() and not sections:
        warnings.append(
            "Visual evidence was unavailable; scored from audio and text.")
    stats.update({
        "source_duration": source_duration or stats.get("source_duration"),
        "content_profile": profile,
        "source_energy": energy,
        "evidence_available": {
            "audio": audio_usable,
            "visual": baseline is not None or bool(sections),
        },
        "visual_baseline": None if baseline is None else {
            "value": round(baseline.value, 4),
            "motion_norm": round(baseline.motion_norm, 4),
            "cut_rate": round(baseline.cut_rate, 4),
            "facecam_norm": round(baseline.facecam_norm, 4),
            "samples": baseline.samples,
        },
        "visual_ranges": len(visual_ranges),
        "visual_sections": len(sections),
        "warnings": warnings,
    })
    save_candidates(workdir, candidates, stats)
    return candidates, stats


def select(workdir, min_score=8.0, max_clips=None, preferred_len=None,
           band=None, llm=None, host=OLLAMA_HOST, mode="heuristic"):
    """Read candidates.json, write segments.json and selection.json."""
    workdir = Path(workdir)
    candidates, discovery = load_candidates(workdir)
    effective_band = (evidence.profile_band(
        discovery.get("content_profile", "mixed"))
        if band is None else band)
    deduped = dedupe_candidates(candidates, preferred_len=preferred_len,
                                band=effective_band, min_score=min_score)
    if llm:
        adjusted = ollama_rerank(deduped[:50], llm, host)
        if adjusted is not None:
            by_key = {_candidate_key(item): item for item in adjusted}
            candidates = [{**item, **by_key[_candidate_key(item)]}
                          if _candidate_key(item) in by_key else item
                          for item in candidates]
            deduped = [by_key.get(_candidate_key(item), item) for item in deduped]
            mode = "ollama"
            save_candidates(workdir, candidates, discovery, mode=mode)

    segments, stats = select_candidates(
        deduped, min_score=min_score, max_clips=max_clips,
        preferred_len=preferred_len, band=effective_band, dedupe=False,
        discovery=discovery, mode=mode,
        evidence_available=discovery.get("evidence_available"))
    _write_json(workdir / "segments.json", segments)
    _write_json(workdir / "selection.json", stats)
    return segments, mode


def _select_supplied(workdir, records, stats, min_score, max_clips,
                     preferred_len, mode):
    """Write search/semantic records through the shared selection contract."""
    save_candidates(workdir, records, stats, mode=mode)
    deduped = dedupe_candidates(records, preferred_len=preferred_len)
    segments, selection = select_candidates(
        deduped, min_score=min_score, max_clips=max_clips,
        preferred_len=preferred_len, dedupe=False, discovery=stats, mode=mode)
    _write_json(Path(workdir) / "segments.json", segments)
    _write_json(Path(workdir) / "selection.json", selection)
    return segments


def _no_clips_message(workdir, threshold):
    """Print the real score range and a threshold suggestion."""
    try:
        selection = json.loads((Path(workdir) / "selection.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        selection = {}
    low, high = selection.get("score_min"), selection.get("score_max")
    if low is None or high is None:
        print("No candidates were discovered. Try a shorter --min-len.",
              flush=True)
        return
    suggestion = max(0.0, round(threshold - 0.5, 1))
    print(f"No clips reached {threshold:.1f}. Candidate scores "
          f"{low:.2f}-{high:.2f}. Lower --min-score (for example "
          f"{suggestion:.1f}) or run --action prepare to review the range.",
          flush=True)


def run(workdir, llm=None, host=OLLAMA_HOST, min_score=None, max_clips=None,
        min_len=15, max_len=300, search=None, pick=None, semantic=None,
        embed_model="nomic-embed-text", preferred_len=None, explain=False):
    """Score the transcript in workdir and write the discovery artifacts.

    Returns (segments, mode): 'ollama', 'heuristic', or 'semantic'.
    search='keywords' overrides scoring with keyword-search clip finding;
    pick='1,3,5' keeps only the numbered candidates (review workflow).
    """
    workdir = Path(workdir)
    if isinstance(preferred_len, str):
        preferred_len = parse_duration(preferred_len)
    effective_score = 8.0 if min_score is None else min_score
    mode = "heuristic"
    if llm and (search or semantic):
        print("Ollama reranking is ignored for search results.", flush=True)

    if semantic:
        data = load_transcript(workdir)
        sentences = sentenceize(data["words"])
        print(f"Transcript: {len(sentences)} sentences over "
              f"{data['duration']:.0f}s", flush=True)
        from semantic import candidates as semantic_candidates
        records = semantic_candidates(sentences, semantic, embed_model, workdir,
                                      min_len, max_len, max_clips)
        mode = "semantic"
        effective_score = 0.0
        if not records:
            raise ValueError("No complete clip windows found for semantic search.")
        stats = {"source_duration": data["duration"], "seeds": len(sentences),
                 "windows_scanned": 0, "content_profile": "mixed",
                 "source_energy": 0.5}
        segments = _select_supplied(workdir, records, stats, effective_score,
                                    max_clips, preferred_len, mode)
    elif search:
        data = load_transcript(workdir)
        sentences = sentenceize(data["words"])
        print(f"Transcript: {len(sentences)} sentences over "
              f"{data['duration']:.0f}s", flush=True)
        hits = search_hits(sentences, search)
        if not hits:
            raise SystemExit(
                f"--search '{search}': no transcript sentence matched. "
                "Try fewer or different keywords.")
        found, stats = discover_candidates(
            sentences, min_len, max_len, preferred_len=preferred_len,
            seed_indices=hits)
        records = dedupe_candidates(found, preferred_len=preferred_len)
        mode = "heuristic"
        effective_score = 0.0
        print(f"Keyword search '{search}': {len(records)} match(es)", flush=True)
        segments = _select_supplied(workdir, records, stats, effective_score,
                                    max_clips, preferred_len, mode)
    else:
        discover(workdir, min_len=min_len, max_len=max_len,
                 preferred_len=preferred_len)
        segments, mode = select(workdir, min_score=effective_score,
                                max_clips=max_clips,
                                preferred_len=preferred_len,
                                llm=llm, host=host)

    if not segments:
        _no_clips_message(workdir, effective_score)

    segments = pick_segments(segments, pick)
    if not segments and pick:
        raise SystemExit("Nothing to render: --clips removed every candidate.")

    if segments:
        print_table(segments)
    if explain and segments:
        print_explanations(segments)
    if segments:
        print(f"Wrote {workdir / 'segments.json'}  "
              f"(mode={mode}, {len(segments)} clips)", flush=True)
    return segments, mode


def main():
    ap = argparse.ArgumentParser(description="Score a chopify transcript for virality.")
    ap.add_argument("workdir", nargs="?", default="work")
    ap.add_argument("--llm", nargs="?", const="qwen2.5:7b", default=None, metavar="MODEL",
                    help="score with a local Ollama model (e.g. qwen2.5:7b, llama3.1:8b)")
    ap.add_argument("--host", default=OLLAMA_HOST,
                    help="Ollama host (default http://localhost:11434)")
    ap.add_argument("--min-score", type=float, default=None,
                    help="keep clips at/above this score (default 8.0)")
    ap.add_argument("--max-clips", type=int, default=None,
                    help="optional safety cap; default all passing clips")
    ap.add_argument("--min-len", type=int, default=15, help="min clip seconds")
    ap.add_argument("--max-len", type=int, default=300, help="max clip seconds")
    ap.add_argument("--preferred-len", default=None, metavar="D",
                    help="soft preferred length, e.g. 45s, 2m, 1m30s")
    ap.add_argument("--explain", action="store_true",
                    help="print score parts and reasons")
    ap.add_argument("--search", default=None, metavar="KEYWORDS",
                    help="skip virality scoring: build clips around transcript "
                         "sentences containing ALL keywords (e.g. --search \"pricing\")")
    ap.add_argument("--clips", default=None, metavar="1,3,5",
                    help="render only these numbered candidates from the table "
                         "(review workflow)")
    ap.add_argument("--semantic", help="search by meaning using local Ollama embeddings")
    ap.add_argument("--embed-model", default="nomic-embed-text")
    args = ap.parse_args()
    run(args.workdir, args.llm, args.host, args.min_score, args.max_clips,
        args.min_len, args.max_len, search=args.search, pick=args.clips,
        semantic=args.semantic, embed_model=args.embed_model,
        preferred_len=args.preferred_len, explain=args.explain)
    print("SCORING COMPLETE", flush=True)


if __name__ == "__main__":
    main()
