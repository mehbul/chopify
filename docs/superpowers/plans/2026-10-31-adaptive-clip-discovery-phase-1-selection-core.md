# Adaptive Clip Discovery Phase 1: Selection Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the seed-and-take selector with an exhaustive, calibrated selection engine that considers every sentence, chooses clip length from a duration ladder, returns every clip at `overall >= 8.0`, and exposes the new score/length controls in the CLI and Studio.

**Architecture:** `score_clips.py` becomes a two-stage engine: `discover_candidates()` builds threshold-independent candidates from every sentence seed, and `select_candidates()` dedupes, applies the threshold, optional cap, and rank. Discovery writes `candidates.json`; selection writes `selection.json` and `segments.json`. `projects.py` caches discovery and selection separately so changing only the score re-filters without rescanning the transcript. CLI and Studio read the same artifacts.

**Tech Stack:** Python 3.11+, existing local stack (faster-whisper, ffmpeg, OpenCV), pytest, vanilla JS/HTML/CSS for Studio. No new packages.

**Spec:** `docs/superpowers/specs/2026-10-31-chopify-adaptive-clip-discovery-design.md`

---

## Global Constraints

- Local only: no paid APIs, cloud uploads, hosted services, or API keys.
- Default minimum score is `8.0` for every source type. Comparisons are inclusive (`overall >= min_score`) and displayed as `8.0+`.
- Default `max_clips` is `None`, meaning every passing clip. `--max-clips` remains an optional cap.
- Default `min_len` is `15`, default `max_len` is `300`.
- Length is a soft prior, never a weighted rubric term.
- Zero passing clips writes `segments.json` as `[]`. There is no fallback clip.
- Existing brand JSON, caption presets, caption styling, and default 16:9 output stay unchanged.
- Existing `segments.json` consumers keep working. New fields are additive and optional.
- No evidence extraction in this phase. Text-only scores are the baseline; Phase 2 adds audio/visual evidence behind the same candidate contract.
- Content profile is `"mixed"` and the source-appropriate band is `(30, 120)` in Phase 1. Phase 2 replaces both with measured values.
- All new public functions get type-light docstrings and deterministic output. No `print()` inside pure functions except existing table/reporting helpers.

## Execution Setup

- [ ] Create a feature branch before the first code change:

```powershell
git switch -c feat/adaptive-discovery
```

- [ ] Confirm the baseline is green before editing:

```powershell
python -m pytest -q
```

Expected: `139 passed` (or the current count if later work landed).

- [ ] Commit the plan itself first:

```powershell
git add docs/superpowers/plans/2026-10-31-adaptive-clip-discovery-phase-1-selection-core.md
git commit -m "docs: plan adaptive clip discovery phase 1"
```

## Deliverables

1. Calibrated 0-10 text rubric with score parts and penalties.
2. Duration parser and ladder.
3. `discover_candidates()`: every sentence start, endpoint refinement, two endpoints per start.
4. `dedupe_candidates()`: 55% overlap rule, near-identical start rule, tighter-clip replacement.
5. `select_candidates()`: threshold, optional cap, rank, stats.
6. `candidates.json`, `selection.json`, and updated `segments.json`.
7. Split discovery/selection caching in `projects.py`.
8. CLI defaults, prompts, prepare-then-render flow, `--explain`.
9. Studio score/length fields, candidate pagination, selection summary.
10. Docs and changelog.

## File Map

| File | Responsibility in this phase |
| --- | --- |
| `score_clips.py` | Rubric, duration parsing, discovery, dedupe, selection, artifacts, Ollama rerank, `run()`. |
| `semantic.py` | Route semantic results through the shared discovery/dedupe builder while preserving `similarity`. |
| `projects.py` | New defaults, option validation, split cache keys, zero-candidate handling, selection summary. |
| `chopify.py` | New flags, guided prompts, prepare-then-render flow, `--explain` pass-through. |
| `dashboard.py` | Settings validation, `selection` in project detail, candidate fields, run responses. |
| `assets/studio/index.html` | Minimum score, preferred length, optional maximum clips, selection summary, select-all UI. |
| `assets/studio/app.js` | Candidate pagination, select all/none, summary rendering, new settings payload. |
| `tests/test_chopify.py` | Rubric, ladder, discovery, dedupe, selection, CLI, search, artifact tests. |
| `tests/test_studio.py` | Option validation, empty selection, optional segment fields, API detail tests. |
| `README.md`, `docs/WORKFLOWS.md`, `CHANGELOG.md` | User-facing behaviour and deliberate default changes. |

## Shared Contracts

### Duration parsing

`score_clips.parse_duration(value)` accepts:

- an `int`/`float` number of seconds,
- `"45"`, `"45s"`, `"1.5m"`, `"1m30s"`, `"2m"`, `"1h"`, `"1h30m"`,
- optional surrounding whitespace.

It returns a finite positive number of seconds, rounded to 3 decimals. It rejects booleans, `0`, negatives, `"abc"`, empty strings, and values over `3600` seconds with `ValueError`. CLI and Studio show one example in the error: `Use seconds or a duration like 45s, 2m, or 1m30s (5s to 1h).`

`score_clips.duration_ladder(min_len, max_len, preferred_len=None)` returns a sorted unique list of second targets. Base ladder:

```python
BASE_LADDER = (15, 30, 45, 60, 90, 120, 180, 300)
```

The result always includes `min_len` and `max_len`, includes base values inside the range, and includes the preferred value clipped into the range.

### Candidate record

Every record produced by discovery is a plain dict:

```json
{
  "start": 1234.5,
  "end": 1278.2,
  "duration": 43.7,
  "hook": "he lost everything",
  "text": "full transcript text of the window",
  "raw_score": 7.4,
  "overall": 8.7,
  "score_parts": {
    "hook": 0.8,
    "payoff": 0.7,
    "self_containment": 0.8,
    "arc": 0.8,
    "energy": 0.7,
    "quotability": 0.5,
    "emotion": 0.5,
    "controversy": 0.1,
    "humour": 0.1,
    "penalties": 0.02,
    "text_score": 8.7
  },
  "profile": "mixed",
  "seed_index": 42,
  "seed_kind": "sentence",
  "endpoint_kind": "quality_best",
  "reason": "Strong hook and payoff; clean boundaries"
}
```

- `raw_score` is the calibrated text score before any evidence or LLM adjustment (identical to `overall` in Phase 1).
- `overall` is the final threshold score. Phase 1 sets it equal to `raw_score`.
- `endpoint_kind` is `"quality_best"` or `"preferred_best"`.
- `reason` is at most 160 characters and is generated deterministically from the strongest and weakest rubric parts.
- Phase 2 may add `evidence`; Phase 1 does not write that key.

### `candidates.json`

Threshold-independent discovery artifact:

```json
{
  "version": 1,
  "content_profile": "mixed",
  "source_energy": 0.5,
  "source_duration": 2892.0,
  "seeds": 18432,
  "windows_scanned": 147456,
  "candidates": [
    {"start": 0.0, "end": 43.7, "...": "..."}
  ]
}
```

`candidates` is start-sorted. Multiple records may share a start (up to two endpoints). Records below any threshold stay in the file.

### `selection.json`

Diagnostic summary written by selection:

```json
{
  "mode": "heuristic",
  "source_duration": 2892.0,
  "seeds": 18432,
  "windows_scanned": 147456,
  "candidates": 24118,
  "passing": 12,
  "score_min": 4.1,
  "score_max": 9.2,
  "passing_score_min": 8.0,
  "passing_score_max": 9.2,
  "duration_min": 18.0,
  "duration_max": 142.0,
  "min_score": 8.0,
  "preferred_len": null,
  "analysis_floor": 8.0,
  "content_profile": "mixed",
  "source_energy": 0.5,
  "evidence_available": {"audio": false, "visual": false}
}
```

`candidates` is the count after dedupe. `score_min`/`score_max` describe deduped candidates. `passing_*` describe clips at or above the threshold. Empty lists produce `null` for the passing ranges and `0` for `passing`.

### `segments.json`

Public render contract. Existing keys stay first and keep their meaning; new keys are additive:

```json
{
  "start": 1234.5,
  "end": 1278.2,
  "hook": "he lost everything",
  "overall": 8.7,
  "duration": 43.7,
  "rank": 3,
  "raw_score": 7.4,
  "score_parts": {"...": "..."},
  "reason": "Strong hook and payoff; clean boundaries"
}
```

`rank` is 1 for the highest score. The list is sorted by start; `pick_segments()` indexes that start-sorted list.

## Task Map

| Task | Outcome | Files |
| --- | --- | --- |
| 1 | Calibrated rubric with parts and penalties | `score_clips.py`, tests |
| 2 | Duration parser, ladder, sentence features | `score_clips.py`, tests |
| 3 | Exhaustive discovery and two endpoints per start | `score_clips.py`, tests |
| 4 | Dedupe and selection math | `score_clips.py`, tests |
| 5 | Artifacts, search/semantic integration, Ollama rerank | `score_clips.py`, `semantic.py`, tests |
| 6 | Split discovery/selection caching and defaults | `projects.py`, tests |
| 7 | CLI prompts and prepare-then-render flow | `chopify.py`, tests |
| 8 | Studio fields, pagination, summary | `dashboard.py`, `assets/studio/*`, tests |
| 9 | Docs, changelog, real-transcript calibration check | `README.md`, `docs/*`, `CHANGELOG.md` |

---

## Task 1: Calibrated Text Rubric

**Files:**
- Modify: `score_clips.py`
- Test: `tests/test_chopify.py`

**Why:** The current weighted mean tops out near 4.9. The new rubric scores nine criteria 0-1, subtracts explicit penalties, then maps the result onto an absolute 0-10 scale with a practical strong-clip range of 8-10.

- [ ] **Step 1: Write failing calibration tests**

Add this class after `TestSentenceize` in `tests/test_chopify.py`:

```python
class TestCalibration:
    def test_calibration_anchors(self):
        anchors = {0.30: 3.3, 0.40: 4.6, 0.50: 6.0, 0.58: 7.0,
                   0.65: 8.0, 0.70: 8.7, 0.75: 9.3, 0.80: 10.0}
        for raw, expected in anchors.items():
            assert score_clips.calibrate_text(raw) == pytest.approx(expected, abs=0.06)

    def test_calibration_clamps(self):
        assert score_clips.calibrate_text(-1) == 0.0
        assert score_clips.calibrate_text(2) == 10.0

    def test_strong_window_reaches_eight(self):
        words = (sentence("Here is the thing nobody tells you about losing everything.", 0.0)
                 + sentence("I was broke and terrified, then I found one insane secret.", 8.0)
                 + sentence("It changed my life forever, and it will change yours.", 16.0))
        sents = score_clips.sentenceize(words)
        parts, text_score, raw_score = score_clips.score_window(sents, 0, len(sents) - 1)
        assert text_score >= 8.0
        assert raw_score >= 0.64
        assert set(score_clips.TEXT_WEIGHTS) <= set(parts)

    def test_flat_narration_stays_below_six(self):
        words = (sentence("Today we are going to talk about the general topic.", 0.0)
                 + sentence("There are many things that people consider in this area.", 8.0)
                 + sentence("That is all for this section of the discussion.", 16.0))
        sents = score_clips.sentenceize(words)
        _, text_score, _ = score_clips.score_window(sents, 0, len(sents) - 1)
        assert text_score < 6.0

    def test_meta_outro_and_filler_lower_the_score(self):
        words = (sentence("The biggest mistake is waiting until you feel ready.", 0.0)
                 + sentence("You have to start scared and learn from the wreckage.", 8.0))
        sents = score_clips.sentenceize(words)
        _, clean, _ = score_clips.score_window(sents, 0, len(sents) - 1)
        polluted_words = sents[0]["words"] + sents[1]["words"] + sentence(
            "Um, thanks for watching and subscribe to the channel.", 16.0)
        polluted = score_clips.sentenceize(polluted_words)
        _, noisy, _ = score_clips.score_window(polluted, 0, len(polluted) - 1)
        assert clean - noisy >= 0.5

    def test_score_sentence_keeps_old_contract(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        for s in sents:
            scores, overall = score_clips.score_sentence(s)
            assert set(scores) == set(score_clips.TEXT_WEIGHTS)
            assert 0.0 <= overall <= 10.0
```

- [ ] **Step 2: Run the new tests and confirm they fail**

```powershell
python -m pytest tests/test_chopify.py::TestCalibration -q
```

Expected: failures for missing `calibrate_text`, `score_window`, and `TEXT_WEIGHTS`.

- [ ] **Step 3: Implement the rubric**

In `score_clips.py`, replace `WEIGHTS`, `score_sentence`, and `_sm_cues` with the following surface. Keep the existing cue tuples; add the extra lexicons below them.

```python
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
```

Add these feature helpers near the cue lists:

```python
META_OPENERS = ("welcome back", "welcome to", "in this video", "today we",
                "before we start", "quick intro")
META_OUTROS = ("thanks for watching", "subscribe", "like and comment",
               "hit the bell", "see you next time", "sponsor")
ANAPHORIC_OPENERS = ("this", "that", "it", "he", "she", "they", "those", "these")
EARLIER_REFERENCES = ("as i said", "like i mentioned", "earlier", "last time",
                      "previous video", "as you know")
INTENSITY_WORDS = ("insane", "crazy", "unbelievable", "massive", "huge",
                   "terrified", "furious", "incredible", "disgusting", "wild")
REACTION_PHRASES = ("no way", "let's go", "what the", "oh my god", "are you kidding")
PAYOFF_WORDS = ("so", "therefore", "that is why", "the result", "it turned out",
                "in the end", "changed", "finally", "which means", "the lesson")
```

The implementation must expose:

```python
def score_window(sentences, a, b, source_wps=None):
    """Score sentences[a..b]. Return (parts, text_score, raw_score)."""
```

Rules for `score_window`:

- `parts` contains every key in `TEXT_WEIGHTS` plus `"penalties"` and `"text_score"`.
- `raw_score = clamp01(sum(TEXT_WEIGHTS[k] * parts[k]) - penalties)`.
- `text_score = calibrate_text(raw_score)`.
- `source_wps` defaults to the median words-per-second across all sentences passed in. Guard against zero.
- `hook`: weighted mix of cue density, question, number, second-person, stakes, curiosity, superlative/negation, and first-3-second speech density; subtract `0.25` when the first sentence matches `META_OPENERS`.
- `payoff`: terminal punctuation, resolution/reveal language in the last 40%, hook/payoff token overlap, and absence of trailing filler.
- `self_containment`: complete start, no anaphoric opening, no earlier reference, complete ending.
- `arc`: at least two sentences, terminal ending, and tension language before resolution language.
- `energy`: words per second relative to `source_wps`, exclamation/caps/repeated letters, intensity words, laughter, gameplay commentary cues, fast onset.
- `quotability`: maximum sentence-level score from short imperative, contrast, rule of three, number plus claim, and a memorable closing line.
- `emotion`, `controversy`, `humour`: cue density plus reaction phrases for emotion.
- `penalties`: filler density up to `0.08`, meta/outro `0.15`, repeated bigrams/false starts up to `0.05`, internal silence over 2.5 seconds up to `0.08`, mid-sentence boundary `0.20`, long low-density window up to `0.10`, empty/non-speech window with no evidence `0.20`.
- Keep all values rounded to 3 decimals in `parts`; return `raw_score` and `text_score` rounded to 3 and 2 decimals respectively.

Keep the old one-sentence entry point:

```python
def score_sentence(sent, source_wps=None):
    parts, text_score, _ = score_window([sent], 0, 0, source_wps)
    return {key: parts[key] for key in TEXT_WEIGHTS}, text_score
```

- [ ] **Step 4: Add deterministic reasons**

Add:

```python
def score_reason(parts):
    """One short, deterministic explanation from the strongest/weakest parts."""
```

Pick the highest and lowest non-penalty criterion. Return strings such as
`"Strong hook and payoff; weak humour"`, capped at 160 characters. Use it from
discovery, not from `score_window`.

- [ ] **Step 5: Run the rubric tests**

```powershell
python -m pytest tests/test_chopify.py::TestCalibration tests/test_chopify.py::TestScoring -q
```

Expected: all pass. Tune only the component formulas, never the calibration anchors.

- [ ] **Step 6: Commit**

```powershell
git add score_clips.py tests/test_chopify.py
git commit -m "feat: recalibrate clip scoring to an absolute 10-point scale"
```

---

## Task 2: Duration Parsing, Ladder, and Seed Plumbing

**Files:**
- Modify: `score_clips.py`
- Test: `tests/test_chopify.py`

**Why:** Clip length becomes an AI-chosen soft prior. Every consumer must parse the same duration formats, and discovery must accept an explicit set of seed indices for keyword and semantic search.

- [ ] **Step 1: Write failing parser and ladder tests**

Add:

```python
class TestDurationParser:
    @pytest.mark.parametrize("value,expected", [
        (45, 45.0), ("45", 45.0), ("45s", 45.0), ("2m", 120.0),
        ("1.5m", 90.0), ("1m30s", 90.0), ("1h", 3600.0),
    ])
    def test_accepts_supported_formats(self, value, expected):
        assert score_clips.parse_duration(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", [True, False, 0, -5, "0", "-2s", "",
                                        "abc", "1x", "4000s", None])
    def test_rejects_invalid_formats(self, value):
        with pytest.raises(ValueError):
            score_clips.parse_duration(value)

    def test_error_mentions_an_example(self):
        with pytest.raises(ValueError, match="45s"):
            score_clips.parse_duration("nope")


class TestDurationLadder:
    def test_default_ladder(self):
        assert score_clips.duration_ladder(15, 300) == [
            15, 30, 45, 60, 90, 120, 180, 300]

    def test_preferred_value_is_inserted_and_clipped(self):
        assert 52 in score_clips.duration_ladder(15, 300, 52)
        assert max(score_clips.duration_ladder(15, 300, 900)) == 300
        assert min(score_clips.duration_ladder(15, 300, 1)) == 15

    def test_narrow_range_still_has_endpoints(self):
        assert score_clips.duration_ladder(40, 100) == [40, 45, 60, 90, 100]
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_chopify.py::TestDurationParser tests/test_chopify.py::TestDurationLadder -q
```

- [ ] **Step 3: Implement `parse_duration` and `duration_ladder`**

Place them near the top of `score_clips.py`:

```python
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
```

Add `import math` to `score_clips.py`.

- [ ] **Step 4: Add seed-index plumbing**

`discover_candidates()` will accept `seed_indices=None`. When `None`, use every sentence index. When a list, validate indices, sort them, and evaluate only those starts. This is the shared path for keyword and semantic search. No search-specific window logic is allowed.

Add a small helper:

```python
def normalize_seed_indices(sentences, seed_indices=None):
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
```

- [ ] **Step 5: Run the tests**

```powershell
python -m pytest tests/test_chopify.py::TestDurationParser tests/test_chopify.py::TestDurationLadder -q
```

- [ ] **Step 6: Commit**

```powershell
git add score_clips.py tests/test_chopify.py
git commit -m "feat: add duration parsing and seed selection plumbing"
```

---

## Task 3: Exhaustive Candidate Discovery

**Files:**
- Modify: `score_clips.py`
- Test: `tests/test_chopify.py`

**Why:** The current selector marks every sentence in a chosen window as taken, so a better overlapping window can never be found. Discovery now evaluates every sentence as a start and keeps the best endpoint for every target length, plus a preferred-length near-tie.

- [ ] **Step 1: Write failing discovery tests**

Add fixtures and tests:

```python
def numbered_sentences(count=8, step=5.0):
    words = []
    for i in range(count):
        text = (f"Sentence number {i} is a complete thought about the topic."
                if i % 2 == 0 else
                f"Here is the insane secret number {i}, and it changed everything.")
        words += sentence(text, i * step)
    return score_clips.sentenceize(words)


class TestDiscovery:
    def test_every_sentence_is_a_seed(self):
        sents = numbered_sentences()
        candidates, stats = score_clips.discover_candidates(
            sents, min_len=2, max_len=30)
        assert {c["seed_index"] for c in candidates} == set(range(len(sents)))
        assert stats["seeds"] == len(sents)
        assert stats["windows_scanned"] == len(sents) * len(
            score_clips.duration_ladder(2, 30))

    def test_overlapping_start_is_not_marked_taken(self):
        sents = numbered_sentences()
        candidates, _ = score_clips.discover_candidates(
            sents, min_len=5, max_len=30)
        starts = {c["start"] for c in candidates}
        assert sents[2]["start"] in starts
        assert any(c["start"] == sents[2]["start"] and c["end"] >= sents[4]["end"]
                   for c in candidates)

    def test_seed_indices_restrict_starts(self):
        sents = numbered_sentences()
        candidates, _ = score_clips.discover_candidates(
            sents, min_len=5, max_len=30, seed_indices=[1, 3])
        assert {c["seed_index"] for c in candidates} == {1, 3}

    def test_records_end_on_complete_sentences(self):
        sents = numbered_sentences()
        candidates, _ = score_clips.discover_candidates(
            sents, min_len=5, max_len=30)
        ends = {s["end"] for s in sents}
        assert all(c["end"] in ends for c in candidates)
        assert all(score_clips.is_arc_complete(s["text"])
                   for s in sents if s["end"] in {c["end"] for c in candidates})

    def test_candidate_shape_and_limits(self):
        sents = numbered_sentences()
        candidates, _ = score_clips.discover_candidates(
            sents, min_len=5, max_len=30)
        for c in candidates:
            assert 5 <= c["duration"] <= 30
            assert c["overall"] == c["raw_score"]
            assert c["endpoint_kind"] in {"quality_best", "preferred_best"}
            assert c["score_parts"]["text_score"] == c["overall"]
            assert c["reason"]
```

Add pure endpoint-choice tests:

```python
def choice(end, score, start=0.0):
    return {"start": start, "end": end, "duration": end - start,
            "overall": score, "raw_score": score}


class TestEndpointChoice:
    def test_quality_and_preferred_endpoints_are_kept(self):
        choices = [choice(20, 7.00), choice(45, 7.00), choice(50, 7.05)]
        kept = score_clips.choose_seed_endpoints(choices, preferred_len=45,
                                                 band=(30, 120))
        assert [c["endpoint_kind"] for c in kept] == [
            "quality_best", "preferred_best"]
        assert kept[0]["end"] == 50
        assert kept[1]["end"] == 45

    def test_preferred_endpoint_must_be_within_tolerance(self):
        choices = [choice(20, 6.80), choice(50, 7.05)]
        kept = score_clips.choose_seed_endpoints(choices, preferred_len=20,
                                                 band=(30, 120))
        assert len(kept) == 1
        assert kept[0]["end"] == 50

    def test_band_is_used_when_no_preference(self):
        choices = [choice(20, 7.00), choice(60, 7.00)]
        kept = score_clips.choose_seed_endpoints(choices, preferred_len=None,
                                                 band=(30, 120))
        assert len(kept) == 2
        assert kept[1]["end"] == 60

    def test_duplicate_endpoint_is_not_kept_twice(self):
        choices = [choice(45, 7.00)]
        kept = score_clips.choose_seed_endpoints(choices, preferred_len=45,
                                                 band=(30, 120))
        assert len(kept) == 1
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_chopify.py::TestDiscovery tests/test_chopify.py::TestEndpointChoice -q
```

- [ ] **Step 3: Add a reusable window index**

Refactor the internals from Task 1 behind a `WindowIndex` so discovery does not rescore every word for every window. Keep `score_window(sentences, a, b, source_wps=None, index=None)` working for existing tests.

```python
class WindowIndex:
    """Per-sentence features plus cumulative sums for O(1)-ish window scoring."""

    def __init__(self, sentences, source_wps=None):
        self.sentences = sentences
        self.source_wps = source_wps or median_wps(sentences)
        self.features = [sentence_features(s) for s in sentences]
        self.prefix = build_prefix_sums(self.features)

    def score(self, a, b):
        return _score_indexed(self, a, b)
```

`sentence_features(sent)` returns a dict containing at least:

```python
{
    "start", "end", "duration", "words", "text", "wps",
    "terminal", "filler_count", "meta_open", "meta_outro",
    "anaphoric", "earlier_ref", "question", "number", "second_person",
    "curiosity", "stakes", "superlative", "negation",
    "emotion", "controversy", "humour", "insight",
    "game_cue", "intensity", "laughter", "reaction",
    "quotability", "bigram_repeat", "false_start",
}
```

`build_prefix_sums` creates cumulative lists for every numeric or boolean feature used by the rubric. `_score_indexed` combines the prefix sums with the first two and last two sentence features so hook, payoff, self-containment, arc, energy, and penalties stay cheap.

- [ ] **Step 4: Implement endpoint helpers**

```python
def first_sentence_at_or_after(ends, target):
    """Bisect the sorted sentence end list for the first end >= target."""


def choose_seed_endpoints(choices, preferred_len=None, band=(30, 120)):
    """Return up to two candidate records: quality_best then preferred_best."""
```

Rules:

- `quality_best` is the highest `overall`; tie-break by shorter duration, then earlier end.
- `preferred_target = preferred_len if preferred_len is not None else sum(band) / 2`.
- `preferred_best` is the endpoint closest to `preferred_target` among choices whose score is within `0.15` of `quality_best`.
- If `preferred_best` has the same `end` as `quality_best`, return only `quality_best`.
- Copy records before setting `endpoint_kind`; never mutate the caller's list.

- [ ] **Step 5: Implement `discover_candidates`**

```python
def discover_candidates(sentences, min_len=15, max_len=300,
                        preferred_len=None, band=(30, 120),
                        seed_indices=None, progress=None):
    """Evaluate every seed against the duration ladder.

    Returns (start-sorted candidates, stats). Stats keys: seeds,
    windows_scanned, source_duration, content_profile, source_energy.
    """
```

Algorithm:

1. Validate `min_len <= max_len`; build `ladder = duration_ladder(...)`.
2. Build `index = WindowIndex(sentences)`.
3. Get `seeds = normalize_seed_indices(sentences, seed_indices)`.
4. For each seed index `i`:
   - Skip if `sentences[i]["end"] - sentences[i]["start"] > max_len`.
   - For each target in `ladder`, increment `windows_scanned` once.
   - `target_end = sentences[i]["start"] + target`.
   - `j0 = first_sentence_at_or_after(ends, target_end)`.
   - Scan `j0 - 2 .. j0 + 2`, clamped to valid complete sentences and `min_len <= duration <= max_len`. Score each endpoint and collect it in a per-seed dict keyed by `end`.
   - If there are choices, find the best endpoint among them, then scan `best_j - 2 .. best_j + 2` once more. This is the second refinement pass. Add any new endpoints to the same dict.
   - Keep only choices whose terminal sentence is `is_arc_complete`.
5. Call `choose_seed_endpoints` once per seed and extend the output.
6. Report progress every 500 seeds when `progress` is callable: `progress("scanning", done, total)`.
7. Sort by `start`, then `end`. Set `stats["candidates"]` only in the selection stage; discovery stats do not claim a deduped count.
8. `content_profile = "mixed"`, `source_energy = 0.5`, `source_duration = sentences[-1]["end"]` if any.

Helper for record construction:

```python
def candidate_record(index, seed, end_index, endpoint_kind="quality_best"):
    a, b = seed, end_index
    parts, text_score, _ = index.score(a, b)
    text = " ".join(w["word"] for s in index.sentences[a:b + 1]
                    for w in s["words"])
    return {
        "start": round(index.sentences[a]["start"], 2),
        "end": round(index.sentences[b]["end"], 2),
        "duration": round(index.sentences[b]["end"] - index.sentences[a]["start"], 2),
        "hook": make_hook(index.sentences[a]["text"]),
        "text": text,
        "raw_score": text_score,
        "overall": text_score,
        "score_parts": parts,
        "profile": "mixed",
        "seed_index": a,
        "seed_kind": "sentence",
        "endpoint_kind": endpoint_kind,
        "reason": score_reason(parts),
    }
```

- [ ] **Step 6: Run the discovery tests**

```powershell
python -m pytest tests/test_chopify.py::TestDiscovery tests/test_chopify.py::TestEndpointChoice tests/test_chopify.py::TestCalibration -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add score_clips.py tests/test_chopify.py
git commit -m "feat: discover overlapping clip candidates from every sentence"
```

---

## Task 4: Dedupe and Selection

**Files:**
- Modify: `score_clips.py`
- Test: `tests/test_chopify.py`

**Why:** Discovery intentionally produces many overlapping windows. Selection must keep one strong representative, allow up to 55% shared footage, prefer a tighter clip when scores are nearly tied, and never lose a passing clip to a failing near-tie.

- [ ] **Step 1: Write failing dedupe and selection tests**

Add:

```python
def candidate(start, end, score, kind="quality_best"):
    return {"start": start, "end": end, "duration": end - start,
            "overall": score, "raw_score": score,
            "hook": f"clip {start}", "score_parts": {"text_score": score},
            "endpoint_kind": kind, "reason": "test"}


class TestOverlap:
    def test_overlap_ratio_uses_shorter_clip(self):
        assert score_clips.overlap_ratio(
            candidate(0, 30, 8.0), candidate(15, 60, 8.0)) == pytest.approx(0.5)
        assert score_clips.overlap_ratio(
            candidate(0, 10, 8.0), candidate(5, 15, 8.0)) == pytest.approx(0.5)
        assert score_clips.overlap_ratio(
            candidate(0, 10, 8.0), candidate(10, 20, 8.0)) == 0.0


class TestDedupe:
    def test_drops_overlap_above_55_percent(self):
        kept = score_clips.dedupe_candidates(
            [candidate(0, 40, 8.5), candidate(10, 50, 8.0)])
        assert len(kept) == 1
        assert kept[0]["overall"] == 8.5

    def test_keeps_overlap_at_or_below_55_percent(self):
        kept = score_clips.dedupe_candidates(
            [candidate(0, 40, 8.5), candidate(22, 62, 8.0)])
        assert len(kept) == 2

    def test_tighter_clip_replaces_longer_within_quarter_point(self):
        kept = score_clips.dedupe_candidates(
            [candidate(0, 90, 8.4), candidate(0, 45, 8.2)],
            preferred_len=45, band=(30, 120))
        assert len(kept) == 1
        assert kept[0]["duration"] == 45

    def test_failing_tighter_clip_does_not_replace_passing_longer_clip(self):
        kept = score_clips.dedupe_candidates(
            [candidate(0, 90, 8.4), candidate(0, 45, 8.2)],
            preferred_len=45, band=(30, 120), min_score=8.3)
        assert len(kept) == 1
        assert kept[0]["duration"] == 90

    def test_near_identical_start_dropped_when_overlap_above_30_percent(self):
        kept = score_clips.dedupe_candidates(
            [candidate(0, 60, 8.6), candidate(1, 61, 8.1)])
        assert len(kept) == 1
        assert kept[0]["overall"] == 8.6


class TestSelection:
    def test_threshold_is_inclusive(self):
        segments, stats = score_clips.select_candidates(
            [candidate(0, 40, 8.0), candidate(50, 90, 7.99)], min_score=8.0)
        assert [s["overall"] for s in segments] == [8.0]
        assert stats["passing"] == 1

    def test_none_cap_returns_everything(self):
        segments, _ = score_clips.select_candidates(
            [candidate(i * 60, i * 60 + 30, 8.0 + i / 10) for i in range(4)],
            min_score=8.0)
        assert len(segments) == 4

    def test_explicit_cap_takes_top_scores(self):
        segments, _ = score_clips.select_candidates(
            [candidate(i * 60, i * 60 + 30, 8.0 + i / 10) for i in range(4)],
            min_score=8.0, max_clips=2)
        assert len(segments) == 2
        assert {s["overall"] for s in segments} == {8.2, 8.3}

    def test_segments_are_start_sorted_with_rank(self):
        segments, _ = score_clips.select_candidates(
            [candidate(120, 160, 8.1), candidate(0, 40, 8.7),
             candidate(60, 100, 8.3)], min_score=8.0)
        assert [s["start"] for s in segments] == [0, 60, 120]
        assert [s["rank"] for s in segments] == [1, 2, 3]

    def test_zero_passing_writes_empty_stats(self):
        segments, stats = score_clips.select_candidates(
            [candidate(0, 40, 7.2)], min_score=8.0)
        assert segments == []
        assert stats["passing"] == 0
        assert stats["passing_score_min"] is None
        assert stats["score_min"] == 7.2


class TestBuildCandidatesCompat:
    def test_returns_deduped_start_sorted_candidates_below_threshold(self):
        sents = numbered_sentences()
        segs = score_clips.build_candidates(
            sents, min_len=5, max_len=30, max_clips=None)
        starts = [s["start"] for s in segs]
        assert starts == sorted(starts)
        assert any(s["overall"] < 8.0 for s in segs)

    def test_optional_cap_is_applied_after_dedupe(self):
        sents = numbered_sentences()
        segs = score_clips.build_candidates(
            sents, min_len=5, max_len=30, max_clips=2)
        assert len(segs) == 2
```

Update the old overlap assertion at `TestCandidates.test_candidates_non_overlapping`:

```python
def test_candidates_overlap_no_more_than_55_percent(self, sample_words):
    sents = score_clips.sentenceize(sample_words)
    segs = score_clips.build_candidates(sents, min_len=5, max_len=30, max_clips=10)
    for i, a in enumerate(segs):
        for b in segs[i + 1:]:
            assert score_clips.overlap_ratio(a, b) <= 0.55
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_chopify.py::TestOverlap tests/test_chopify.py::TestDedupe tests/test_chopify.py::TestSelection tests/test_chopify.py::TestBuildCandidatesCompat -q
```

- [ ] **Step 3: Implement overlap and dedupe**

```python
def overlap_ratio(first, second):
    """Overlap divided by the shorter clip duration; 0 when they do not touch."""
    overlap = max(0.0, min(first["end"], second["end"])
                  - max(first["start"], second["start"]))
    shorter = min(first["end"] - first["start"], second["end"] - second["start"])
    return overlap / shorter if shorter > 0 else 0.0


def inside_band(item, preferred_len, band):
    low, high = band
    if preferred_len is not None:
        low = high = preferred_len
        tolerance = 0.25 * preferred_len
        low, high = low - tolerance, high + tolerance
    return low <= item["duration"] <= high
```

`dedupe_candidates(candidates, preferred_len=None, band=(30, 120), min_score=None)`:

1. Copy records. Sort by `overall` descending, then `start` ascending, then `duration` ascending.
2. For each candidate, compare with every currently kept record.
3. If `overlap_ratio(new, kept) > 0.55`:
   - Replacement is allowed only when all are true: scores within `0.25`, new is shorter, new is `inside_band`, and (`min_score is None` or new score `>= min_score`).
   - When allowed, replace the kept record with the new one and stop comparing.
   - Otherwise drop the new record when the kept score is greater or equal. If the new score is higher, remove the kept record and continue comparing.
4. Independently of the 0.55 rule, drop the new record when `abs(new.start - kept.start) <= 1.5` and `overlap_ratio > 0.30`. Because the list is score-sorted, the kept record is never lower scoring here.
5. Return the kept list sorted by start, then end.

Guard all duration and score fields as finite numbers. Records must never be mutated in place.

- [ ] **Step 4: Implement selection**

```python
PUBLIC_SEGMENT_KEYS = ("start", "end", "hook", "overall", "duration", "rank",
                       "raw_score", "score_parts", "reason")


def select_candidates(candidates, min_score=8.0, max_clips=None,
                      preferred_len=None, band=(30, 120), dedupe=True,
                      discovery=None, mode="heuristic"):
    """Dedupe, threshold, cap, rank, and return (segments, selection_stats)."""
```

Rules:

- Validate `min_score` in `0..10`; `max_clips` is `None` or an integer `>= 1`.
- When `dedupe`, call `dedupe_candidates(..., min_score=min_score)`.
- Filter with `overall >= min_score` (inclusive).
- If `max_clips is not None`, take the top N by `overall` descending before sorting.
- Sort the passing records by `start`, then `end`.
- Build the rank map from a score-descending copy: 1 is highest, ties broken by start then duration.
- Return only `PUBLIC_SEGMENT_KEYS`; fill `duration` and `reason` when absent.
- Build `selection_stats` exactly as documented in the shared contract. Use `None` for empty score/duration ranges. `analysis_floor = min(8.0, min_score)`.
- `discovery` is the stats dict from `discover_candidates`; default `{}`.

- [ ] **Step 5: Implement the compatibility wrapper**

```python
def build_candidates(sentences, min_len, max_len, max_clips,
                     min_score=None, preferred_len=None):
    """Start-sorted deduped candidates, including those below the threshold."""
```

- Call `discover_candidates(sentences, min_len, max_len, preferred_len=preferred_len)`.
- Dedupe with `preferred_len` and `min_score`.
- Apply `max_clips` after dedupe only when it is not `None`. Take the top N by score, then sort by start.
- Return candidate records, not public segments, so `endpoint_kind` and other diagnostics remain available.

- [ ] **Step 6: Run the tests**

```powershell
python -m pytest tests/test_chopify.py::TestOverlap tests/test_chopify.py::TestDedupe tests/test_chopify.py::TestSelection tests/test_chopify.py::TestBuildCandidatesCompat tests/test_chopify.py::TestCandidates -q
```

Expected: all pass. If the 55% boundary test is flaky because of floating point, compare with `pytest.approx(0.55, abs=1e-6)`.

- [ ] **Step 7: Commit**

```powershell
git add score_clips.py tests/test_chopify.py
git commit -m "feat: dedupe and select all passing clip candidates"
```

---

## Task 5: Artifacts, Search, Semantic, Ollama, and `run()`

**Files:**
- Modify: `score_clips.py`, `semantic.py`
- Test: `tests/test_chopify.py`

**Why:** CLI and Studio need one stable artifact contract, keyword and semantic search must use the shared window builder, and Ollama must rerank only the top candidates without being able to invent timestamps.

- [ ] **Step 1: Write failing artifact and rerank tests**

Add:

```python
class TestArtifacts:
    def test_run_writes_candidates_and_selection(self, workdir):
        segs, mode = score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
        assert mode == "heuristic"
        candidates = json.loads((workdir / "candidates.json").read_text())
        selection = json.loads((workdir / "selection.json").read_text())
        assert candidates["version"] == 1
        assert candidates["candidates"]
        assert selection["candidates"] >= len(segs)
        assert selection["passing"] == len(segs)
        assert selection["min_score"] == 0

    def test_zero_passing_writes_empty_segments(self, workdir, monkeypatch):
        low = candidate(0, 20, 7.0)
        monkeypatch.setattr(score_clips, "discover_candidates",
                            lambda *a, **k: ([low], {"seeds": 1,
                            "windows_scanned": 1, "source_duration": 20.0,
                            "content_profile": "mixed", "source_energy": 0.5}))
        segs, _ = score_clips.run(workdir, min_score=8.0, min_len=4, max_len=20)
        assert segs == []
        assert json.loads((workdir / "segments.json").read_text()) == []

    def test_llm_adjustment_changes_score_not_times(self, workdir, monkeypatch):
        def fake_chat(model, host, messages, timeout):
            return [{"index": 0, "adjustment": 0.4, "reason": "strong reaction"}]
        monkeypatch.setattr(score_clips, "_ollama_chat", fake_chat)
        segs, mode = score_clips.run(workdir, min_score=0, min_len=4, max_len=20,
                                     llm="test-model")
        assert mode == "ollama"
        assert segs
        assert any(s.get("overall") != s.get("raw_score") for s in segs)

    def test_invalid_llm_response_is_ignored(self, workdir, monkeypatch):
        monkeypatch.setattr(score_clips, "_ollama_chat",
                            lambda *a, **k: [{"index": 99, "adjustment": 9}])
        segs, mode = score_clips.run(workdir, min_score=0, min_len=4, max_len=20,
                                     llm="test-model")
        assert mode == "heuristic"
        assert segs


class TestSearchSharesBuilder:
    def test_keyword_results_are_deduped_and_start_sorted(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        segs = score_clips.search_candidates(
            sents, "biggest mistake", min_len=5, max_len=30, max_clips=None)
        assert segs
        assert [s["start"] for s in segs] == sorted(s["start"] for s in segs)
        for i, a in enumerate(segs):
            for b in segs[i + 1:]:
                assert score_clips.overlap_ratio(a, b) <= 0.55
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_chopify.py::TestArtifacts tests/test_chopify.py::TestSearchSharesBuilder -q
```

- [ ] **Step 3: Add artifact read/write helpers**

In `score_clips.py`:

```python
CANDIDATES_VERSION = 1


def _write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               allow_nan=False), encoding="utf-8")
    temp.replace(path)


def save_candidates(workdir, candidates, stats, mode="heuristic"):
    """Write the threshold-independent candidates.json envelope."""


def load_candidates(workdir):
    """Return (candidates, stats) from candidates.json.

    Raise SystemExit with a clear message when the file is missing or invalid.
    """
```

Envelope shape is the one in the shared contract. Include `"mode": mode` and
`"version": CANDIDATES_VERSION`.

- [ ] **Step 4: Add `discover()` and `select()`**

```python
def discover(workdir, min_len=15, max_len=300, preferred_len=None,
             band=(30, 120), progress=None):
    """Load the transcript, discover candidates, write candidates.json."""
    workdir = Path(workdir)
    data = load_transcript(workdir)
    sentences = sentenceize(data["words"])
    print(f"Transcript: {len(sentences)} sentences over "
          f"{data['duration']:.0f}s", flush=True)
    candidates, stats = discover_candidates(
        sentences, min_len, max_len, preferred_len=preferred_len,
        band=band, progress=progress)
    save_candidates(workdir, candidates, stats)
    return candidates, stats


def select(workdir, min_score=8.0, max_clips=None, preferred_len=None,
           band=(30, 120), llm=None, host=OLLAMA_HOST, mode="heuristic"):
    """Read candidates.json, write segments.json and selection.json."""
```

`select()` does:

1. `candidates, discovery = load_candidates(workdir)`.
2. `deduped = dedupe_candidates(candidates, preferred_len=preferred_len,
   band=band, min_score=min_score)`.
3. If `llm`, call `ollama_rerank(deduped[:50], llm, host)`.
   - On success, build an adjustment map keyed by
     `(round(start, 2), round(end, 2), seed_index)`.
   - Merge those fields into the matching records in both `deduped` and the
     full `candidates` list. Do not change the order produced by deterministic
     dedupe, and do not change timestamps.
   - Set `mode = "ollama"` and rewrite `candidates.json` with the **full**
     endpoint list (not the deduped subset) plus the merged adjustments, so
     re-thresholding remains exact.
4. `segments, stats = select_candidates(deduped, min_score, max_clips,
   preferred_len=preferred_len, band=band, dedupe=False,
   discovery=discovery, mode=mode)`.
5. `_write_json(workdir / "segments.json", segments)` and
   `_write_json(workdir / "selection.json", stats)`.
6. Return `(segments, mode)`.

- [ ] **Step 5: Replace keyword and semantic window logic**

`search_candidates(sentences, query, min_len, max_len, max_clips)`:

```python
hits = search_hits(sentences, query)
if not hits:
    return []
found, _ = discover_candidates(sentences, min_len, max_len, seed_indices=hits)
kept = dedupe_candidates(found)
if max_clips is not None:
    kept = top_by_score(kept, max_clips)
return sorted(kept, key=lambda item: (item["start"], item["end"]))
```

Search results are never thresholded by `min_score`.

Update `semantic.candidates()`:

- Keep `search()` and the embedding cache exactly as they are.
- Use only the top 200 similarity matches as seeds.
- Call `score_clips.discover_candidates(..., seed_indices=seeds)`.
- Attach `similarity` to each candidate from its `seed_index`.
- Dedupe with `score_clips.dedupe_candidates`.
- If `max_clips` is not `None`, take the top N by similarity; otherwise keep all.
- Return start-sorted records.

Delete `_grow_window` usage from `semantic.py`. Do not delete `_grow_window` or
`_make_segment` from `score_clips.py` yet; existing tests still cover them until
the cleanup task.

- [ ] **Step 6: Implement Ollama reranking**

Replace `ollama_select` with:

```python
def _ollama_chat(model, host, messages, timeout=120):
    """POST to the local Ollama chat endpoint and return parsed JSON content."""


def ollama_rerank(candidates, model, host=OLLAMA_HOST, timeout=120):
    """Rerank the top 50 deduped candidates by -1.0..+1.0.

    Return an updated list, or None when Ollama is unavailable or returns junk.
    Timestamps are never changed.
    """
```

Rules:

- Send at most 50 candidates, already score-sorted. Include `index`, `start`,
  `end`, and a 400-character text snippet.
- Request JSON only, with objects `{"index": n, "adjustment": -1..1,
  "reason": "..."}`.
- Ignore unknown indices, non-finite adjustments, booleans, and non-object
  entries. Clamp valid adjustments to `[-1, 1]`.
- For adjusted records set `llm_adjustment`, `llm_model`, and
  `overall = round(clamp(overall + adjustment, 0, 10), 2)`. Append the reason to
  the existing reason, capped at 160 characters.
- Leave all timestamps, hooks, score parts, and `raw_score` unchanged.
- Return `None` when no valid adjustments were parsed, when the candidate list
  is empty, or on any exception. Print
  `Ollama unavailable (<error>) -> deterministic scores` only for exceptions.
- A `None` result leaves `mode = "heuristic"` and the deterministic list intact.

- [ ] **Step 7: Rewrite `run()`**

Keep the existing positional prefix and add the new keywords at the end:

```python
def run(workdir, llm=None, host=OLLAMA_HOST, min_score=None, max_clips=None,
        min_len=15, max_len=300, search=None, pick=None, semantic=None,
        embed_model="nomic-embed-text", preferred_len=None, explain=False):
```

Flow:

1. `effective_score = 8.0 if min_score is None else min_score`.
2. If `semantic`: call `semantic.candidates(...)`; `mode = "semantic"`;
   `effective_score = 0.0` because semantic search is its own threshold.
3. Else if `search`: call `search_candidates(...)`; `mode = "heuristic"`;
   `effective_score = 0.0`; raise `SystemExit` with the existing message when
   there are no matches.
4. Else: call `discover(workdir, ...)`, then call `select(workdir,
   min_score=effective_score, max_clips=..., preferred_len=..., llm=llm,
   host=host)`. `select()` performs dedupe, optional rerank, thresholding, and
   writes all three artifacts. Return its `(segments, mode)`.
5. For search/semantic only: save the supplied records with `save_candidates()`,
   dedupe them, call `select_candidates(..., dedupe=False, min_score=0.0)`, and
   write `segments.json` plus `selection.json`.
6. When no clips pass, print the score range and:
   `No clips reached 8.0. Lower --min-score (for example 7.5) or run --action prepare to review the range.`
   Do not create a fallback clip.
7. Apply `pick_segments()` after writing the full selection. If `pick` removes
   everything, keep the existing `SystemExit` message.
8. `print_table(segments)`; when `explain`, call `print_explanations(segments)`.
9. Return `(segments, mode)`.

- [ ] **Step 8: Update `print_table` and add `print_explanations`**

Keep the function signature `print_table(segments)`. New columns:

```text
  #  rank  start ->      end  score  duration  hook
  1     3  1234.50 -> 1278.20   8.7     43.7s  he lost everything
```

`print_explanations(segments)` prints each start time, `score_parts`, and
`reason` for `--explain`.

- [ ] **Step 9: Update `score_clips.py` argparse defaults**

- `--min-score` default `None` with help text `default 8.0`.
- `--max-clips` default `None`.
- `--min-len` default `15`.
- `--max-len` default `300`.
- Add `--preferred-len`.
- Add `--explain`.
- Keep `--search`, `--semantic`, `--clips`, `--llm`, `--embed-model`.

Standalone `score_clips.py` does not prompt. It uses `8.0` when `--min-score` is
omitted.

- [ ] **Step 10: Update existing run tests**

- `test_run_writes_segments`: pass `min_score=0`.
- `test_ollama_fallback_on_dead_server`: pass `min_score=0`.
- `test_bom_transcript_accepted`: pass `min_score=0`.
- `test_run_pick_selects_one` and `test_run_pick_out_of_range_exits`: pass
  `min_score=0` so the fixture always has candidates.
- `test_run_search_writes_segments`: unchanged, because search ignores the
  threshold.

- [ ] **Step 11: Run the full suite**

```powershell
python -m pytest -q
```

Expected: all tests pass except tests deliberately changed in later tasks. Fix
any regression before committing.

- [ ] **Step 12: Commit**

```powershell
git add score_clips.py semantic.py tests/test_chopify.py
git commit -m "feat: write discovery artifacts and rerank local candidates"
```

---

## Task 6: Project Defaults and Split Caching

**Files:**
- Modify: `projects.py`
- Test: `tests/test_studio.py`, `tests/test_chopify.py`

**Why:** Re-running a project after changing only the score must not rescan the transcript. Discovery (transcript, length ladder) and selection (threshold, cap, LLM) need separate cache keys.

- [ ] **Step 1: Write failing option and cache-key tests**

Add to `tests/test_studio.py`:

```python
class TestProjectOptions:
    def test_new_defaults(self):
        import projects
        options = projects.validate_options({})
        assert options["min_len"] == 15
        assert options["max_len"] == 300
        assert options["max_clips"] is None
        assert options["min_score"] == 8.0
        assert options["preferred_len"] is None

    def test_none_cap_and_null_score_are_accepted(self):
        import projects
        options = projects.validate_options({"max_clips": None, "min_score": None})
        assert options["max_clips"] is None
        assert options["min_score"] is None

    def test_preferred_length_is_parsed_and_clipped(self):
        import projects
        assert projects.validate_options(
            {"min_len": 15, "max_len": 100, "preferred_len": "2m"}
        )["preferred_len"] == 100
        assert projects.validate_options(
            {"min_len": 15, "max_len": 100, "preferred_len": 10}
        )["preferred_len"] == 15

    @pytest.mark.parametrize("value", [0, -1, "abc", "4000s", True])
    def test_invalid_preferred_length_rejected(self, value):
        import projects
        with pytest.raises(ValueError):
            projects.validate_options({"preferred_len": value})

    @pytest.mark.parametrize("value", [0, 3601, 1.5, True])
    def test_invalid_cap_rejected(self, value):
        import projects
        with pytest.raises(ValueError):
            projects.validate_options({"max_clips": value})

    def test_changing_score_keeps_discovery_key(self):
        import projects
        base = {"min_len": 15, "max_len": 300, "preferred_len": None,
                "search": None, "semantic": None, "embed_model": "x",
                "min_score": 8.0, "max_clips": None, "llm": None}
        d1, s1 = projects.score_keys("transcript", {"words": []}, base)
        d2, s2 = projects.score_keys(
            "transcript", {"words": []}, {**base, "min_score": 7.5})
        assert d1 == d2
        assert s1 != s2
```

Add to `tests/test_studio.py`:

```python
def test_validate_segments_allows_empty_and_preserves_fields():
    import projects
    assert projects.validate_segments([], 100) == []
    rich = {
        "start": 1.0, "end": 5.0, "hook": "x", "overall": 8.4,
        "duration": 4.0, "rank": 2, "raw_score": 7.9,
        "score_parts": {"hook": 0.8}, "evidence": {"audio": 8.0},
        "reason": "strong",
    }
    assert projects.validate_segments([rich], 10)[0] == rich
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_studio.py::TestProjectOptions -q
python -m pytest tests/test_studio.py::test_validate_segments_allows_empty_and_preserves_fields -q
```

- [ ] **Step 3: Update `DEFAULT_OPTIONS`**

```python
DEFAULT_OPTIONS = {
    ...,
    "min_len": 15, "max_len": 300, "max_clips": None, "min_score": 8.0,
    "preferred_len": None, ...
}
```

Do not rename or remove any existing key.

- [ ] **Step 4: Update `validate_options`**

- Validate `min_len` and `max_len` as integers `1..3600`; keep `min_len <= max_len`.
- Allow `max_clips is None`; otherwise require an integer `1..3600`.
- Allow `min_score is None`; otherwise require a finite number `0..10`.
- For `preferred_len is not None`, call `score_clips.parse_duration`, then clip to `[min_len, max_len]`. Preserve the numeric value (int when whole, float otherwise).
- Catch `ValueError` from `parse_duration` and re-raise with the project setting context; the example format must remain visible.
- Keep unknown-key rejection and all existing validations unchanged.

- [ ] **Step 5: Add `score_keys`**

```python
def score_keys(transcript_key, data, options):
    """Return (discovery_key, selection_key) for split stage caching."""
    discovery_options = {key: options[key] for key in (
        "search", "semantic", "embed_model", "min_len", "max_len",
        "preferred_len")}
    discovery_key = digest([transcript_key, data, discovery_options])
    selection_options = {key: options[key] for key in (
        "min_score", "max_clips", "llm")}
    selection_key = digest([discovery_key, selection_options])
    return discovery_key, selection_key
```

- [ ] **Step 6: Split the score stage in `run_project`**

Replace the single `selection`/`score_key` block with:

```python
stage("discover")
data = read_json(transcript_path)
discovery_key, selection_key = score_keys(transcript_key, data, options)
candidates_path = path / "candidates.json"
selection_path = path / "selection.json"
segments_path = path / "segments.json"

if options["search"] or options["semantic"]:
    combined_key = digest([discovery_key, selection_key])
    if (not resume or state["cache"].get("score") != combined_key
            or not segments_path.exists()):
        score_clips.run(path, **score_options(options))
        state["cache"]["score"] = combined_key
        save_project(path, state)
else:
    if (not resume or state["cache"].get("discovery") != discovery_key
            or not candidates_path.exists()):
        score_clips.discover(
            path, min_len=options["min_len"], max_len=options["max_len"],
            preferred_len=options["preferred_len"])
        state["cache"]["discovery"] = discovery_key
        save_project(path, state)

    stage("select")
    if (not resume or state["cache"].get("selection") != selection_key
            or not segments_path.exists() or not selection_path.exists()):
        score_clips.select(
            path, min_score=options["min_score"] if options["min_score"] is not None else 8.0,
            max_clips=options["max_clips"],
            preferred_len=options["preferred_len"], llm=options["llm"])
        state["cache"]["selection"] = selection_key
        save_project(path, state)

state["selection"] = read_json(selection_path)
```

Add a small `score_options(options)` helper:

```python
def score_options(options):
    keys = ("llm", "search", "semantic", "embed_model", "min_score",
            "max_clips", "min_len", "max_len", "preferred_len")
    return {key: options[key] for key in keys}
```

Do not pass `min_score=None` to `select`; use `8.0`.

- [ ] **Step 7: Handle zero candidates in render**

- Change `validate_segments()` to return `[]` for an empty list and to preserve
  optional fields by copying each item after validation.
- In the render branch of `run_project`, after validation:

```python
if not segments:
    stats = state.get("selection") or {}
    low = stats.get("score_min")
    high = stats.get("score_max")
    score = stats.get("min_score", options["min_score"] or 8.0)
    range_text = (f"Candidate scores {low:.1f}-{high:.1f}. " if low is not None
                  else "No candidates were found. ")
    raise ValueError(f"{range_text}No clips reached {score:.1f}. "
                     f"Lower the minimum score or run this project with "
                     f"--action prepare to review the range.")
```

`prepare` still succeeds and leaves status `review` with `segments.json == []`.

- [ ] **Step 8: Run the project tests**

```powershell
python -m pytest tests/test_studio.py -q
python -m pytest tests/test_chopify.py -q
```

Expected: all pass. Existing tests that assert `max_clips` or the old defaults
must be updated to the new deliberate defaults.

- [ ] **Step 9: Commit**

```powershell
git add projects.py tests/test_studio.py tests/test_chopify.py
git commit -m "feat: cache clip discovery separately from selection"
```

---

## Task 7: Guided CLI and Prepare-Then-Render

**Files:**
- Modify: `chopify.py`
- Test: `tests/test_chopify.py`

**Why:** The user should answer two questions once, see the real candidate range, and decide whether to render more than ten clips without throwing away discovery.

- [ ] **Step 1: Write failing prompt tests**

Add:

```python
class TestCliPrompts:
    def test_prompts_score_and_length(self, monkeypatch):
        answers = iter(["7.5", "90s"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
        got = chopify.prompt_scoring(
            {"min_score": None, "preferred_len": None}, interactive=True)
        assert got["min_score"] == 7.5
        assert got["preferred_len"] == 90

    def test_enter_keeps_existing_defaults(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        got = chopify.prompt_scoring(
            {"min_score": None, "preferred_len": None},
            defaults={"min_score": 7.5, "preferred_len": 45},
            interactive=True)
        assert got["min_score"] == 7.5
        assert got["preferred_len"] == 45

    def test_non_interactive_skips_prompts(self, monkeypatch):
        monkeypatch.setattr("builtins.input",
                            lambda prompt: pytest.fail("prompted"))
        got = chopify.prompt_scoring(
            {"min_score": None, "preferred_len": None}, interactive=False)
        assert got == {"min_score": None, "preferred_len": None}

    def test_flags_skip_their_prompt(self, monkeypatch):
        answers = iter(["90s"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
        got = chopify.prompt_scoring(
            {"min_score": 7.0, "preferred_len": None},
            interactive=True, ask_score=False)
        assert got["min_score"] == 7.0
        assert got["preferred_len"] == 90

    def test_invalid_score_reprompts(self, monkeypatch):
        answers = iter(["11", "8.2", ""])
        monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
        got = chopify.prompt_scoring(
            {"min_score": None, "preferred_len": None},
            interactive=True, ask_length=False)
        assert got["min_score"] == 8.2
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_chopify.py::TestCliPrompts -q
```

- [ ] **Step 3: Add prompt helper to `chopify.py`**

```python
def prompt_scoring(options, defaults=None, interactive=True,
                   ask_score=True, ask_length=True):
    """Prompt for the two scoring controls and return a new options dict."""
```

Rules:

- Return a copy unchanged when `interactive` is false.
- `defaults` supplies Enter defaults; fall back to `8.0` and `None`.
- Print exactly:
  - `Minimum clip score (0-10, Enter = 8.0): `
  - `Preferred clip length (e.g. 45s, 2m; Enter = AI chooses): `
- Re-prompt on an invalid score or duration, printing the validation message.
- Empty input keeps the default.
- Use `score_clips.parse_duration` for the preferred length.
- Catch `EOFError` and keep the current value.

- [ ] **Step 4: Add summary formatting**

In `score_clips.py`:

```python
def format_duration(seconds, compact=False):
    """Format seconds as 43.7s, 2m 22s, or 1h 02m."""
```

- `compact=True` is for the table and keeps one decimal below 60 seconds.
- `compact=False` is for summaries and omits decimals.
- Add `format_selection_summary(stats)` returning the three-line summary from
  the spec. Use `"AI chooses"` when `preferred_len` is null.

- [ ] **Step 5: Add CLI flags and defaults**

In `chopify.py`:

```python
ap.add_argument("--min-score", type=float, default=None,
                help="minimum clip score 0-10 (default 8.0)")
ap.add_argument("--preferred-len", default=None, metavar="D",
                help="soft preferred length, e.g. 45s, 2m, 1m30s")
ap.add_argument("--max-clips", type=int, default=None,
                help="optional safety cap; default all passing clips")
ap.add_argument("--min-len", type=int, default=15)
ap.add_argument("--max-len", type=int, default=300)
ap.add_argument("--no-input", action="store_true",
                help="never prompt; use flags and defaults")
ap.add_argument("--explain", action="store_true",
                help="print score parts and reasons")
```

Keep flag names and meanings for existing options.

- [ ] **Step 6: Detect explicitly supplied flags**

Add:

```python
def flag_given(name):
    """True when --name or --name=value appears in sys.argv."""
```

Use it for `--min-score` and `--preferred-len`. Do not rely on `None`, because
`None` is also the prompt sentinel.

- [ ] **Step 7: Wire prompts into `main()`**

- Determine `interactive`:
  - `not args.no_input`
  - `not args.batch`
  - `args.action != "render"`
  - `not args.search and not args.semantic`
  - `sys.stdin.isatty()`
- For a new project: `options = prompt_scoring(options, interactive=interactive,
  ask_score=not flag_given("--min-score"),
  ask_length=not flag_given("--preferred-len"))`.
- For an existing project: read `state["options"]` first, use them as prompt
  defaults, then apply prompted values before the explicit-flag merge.
- Batch mode never prompts.

- [ ] **Step 8: Implement the interactive `--action all` flow**

For interactive single-source/project runs with `action == "all"`:

1. Run `projects.run_project(path, action="prepare", ...)`.
2. Read the updated state and `selection.json`.
3. Print `score_clips.format_selection_summary(state["selection"])`.
4. If zero clips: print the existing no-clips hint and `raise SystemExit(1)`.
   The project remains in `review`.
5. If more than 10 clips, ask:
   `12 clips at 8.0 or above. Render all? [Y/n] `
   - Enter or `y`/`yes` continues.
   - `n`/`no` prints `Run: python chopify.py --project <path> --action render`
     and returns with status 0.
6. Run `projects.run_project(path, action="render", ...)`.

All other cases keep the existing single-call path. `--no-input` renders all
without asking.

- [ ] **Step 9: Print summary and explanations**

- After prepare or render, print the selection summary from `selection.json`.
- When `args.explain`, call `score_clips.print_explanations(segments)`.
- Keep the final `CHOPIFY COMPLETE` line.

- [ ] **Step 10: Run CLI tests and a dry local run**

```powershell
python -m pytest tests/test_chopify.py::TestCliPrompts -q
python -m pytest -q
python chopify.py --help
```

Expected: help shows the new flags and no argument-parsing regression. If a
small local sample is available, run `--action prepare --no-input` and confirm
`candidates.json`, `selection.json`, and `segments.json` are written.

- [ ] **Step 11: Commit**

```powershell
git add chopify.py score_clips.py tests/test_chopify.py
git commit -m "feat: guide score and clip length from the CLI"
```

---

## Task 8: Studio Fields, Pagination, and Selection Summary

**Files:**
- Modify: `dashboard.py`, `assets/studio/index.html`, `assets/studio/app.js`,
  `assets/studio/style.css`
- Test: `tests/test_studio.py`

**Why:** Hundreds of candidates are unusable in one long list. Studio needs the same score/length controls, a summary of the real score range, and paginated cards with reasons.

- [ ] **Step 1: Write failing API tests**

Add to `tests/test_studio.py`:

```python
def test_project_detail_exposes_selection(tmp_path):
    import dashboard
    import projects
    projects.init_project(tmp_path / "abc123abc123", "source.mp4", {},
                          project_id="abc123abc123")
    (tmp_path / "abc123abc123" / "transcript.json").write_text(
        json.dumps({"video": "source.mp4", "duration": 100.0, "words": []}),
        encoding="utf-8")
    (tmp_path / "abc123abc123" / "segments.json").write_text(
        json.dumps([{"start": 1.0, "end": 40.0, "hook": "x", "overall": 8.4,
                     "rank": 1, "duration": 39.0}]), encoding="utf-8")
    (tmp_path / "abc123abc123" / "selection.json").write_text(
        json.dumps({"passing": 1, "min_score": 8.0, "content_profile": "mixed"}),
        encoding="utf-8")
    detail = dashboard.project_detail(tmp_path, "abc123abc123")
    assert detail["selection"]["passing"] == 1
    assert detail["candidates"][0]["rank"] == 1


def test_settings_accept_null_cap_and_preferred_length():
    import projects
    options = projects.validate_options({
        "max_clips": None, "min_score": 8.0, "preferred_len": "90s"})
    assert options["max_clips"] is None
    assert options["preferred_len"] == 90
```

- [ ] **Step 2: Run and confirm failures**

```powershell
python -m pytest tests/test_studio.py::test_project_detail_exposes_selection -q
```

- [ ] **Step 3: Extract `project_detail()` in `dashboard.py`**

Move the body of the GET detail branch into:

```python
def project_detail(root, project_id):
    """Build the JSON payload for one project, including selection.json."""
```

Add `"selection": {}` to the base dict and load `selection.json` when present.
Keep `"candidates"`, `"transcript"`, `"files"`, and `"log"` unchanged. The GET
route calls this helper.

- [ ] **Step 4: Update the settings form**

In `assets/studio/index.html`, inside the scoring details block:

- Change the minimum score label to `Minimum score` and make the input
  `required`, `value="8"`.
- Add:

```html
<label>Preferred length<input
  name="preferred_len"
  placeholder="AI chooses"
  autocomplete="off" /></label>
```

- Change the maximum clips input to optional:

```html
<label>Maximum clips (optional)<input
  name="max_clips"
  type="number"
  min="1"
  max="3600"
  placeholder="All passing clips" /></label>
```

- Change min/max seconds defaults to 15 and 300 in the input attributes. JS
  still fills saved values.

- [ ] **Step 5: Add summary, pagination, and select-all controls**

Above `#candidates`:

```html
<div id="selection-summary" class="selection-summary" hidden></div>
```

Below the candidate list, before the export bar:

```html
<div class="candidate-pager">
  <button id="candidates-prev" type="button">← Previous</button>
  <span id="candidate-page">0–0 of 0</span>
  <button id="candidates-next" type="button">Next →</button>
</div>
```

In the export bar, beside the selected count:

```html
<button id="select-all" type="button">Select all</button>
<button id="select-none" type="button">Select none</button>
```

- [ ] **Step 6: Update `app.js` state and settings payload**

- Add `candidatePage: 0` to `state`.
- Reset `state.candidatePage = 0` in `selectProject()`.
- In the settings submit handler:

```js
for (const key of ["min_len", "max_len"])
  values[key] = Number(values[key]);
values.max_clips =
  values.max_clips === "" ? null : Number(values.max_clips);
values.min_score =
  values.min_score === "" ? null : Number(values.min_score);
values.preferred_len = values.preferred_len.trim() || null;
```

- Keep `values.centers`, `values.llm`, `values.search`, and `values.semantic`
  handling unchanged.

- [ ] **Step 7: Render paginated candidates with rank and reasons**

Add:

```js
const CANDIDATES_PER_PAGE = 25;
function formatDuration(seconds) { /* 43.7s, 2m 22s, 1h 02m */ }
function renderSelectionSummary(detail) { /* hidden when no selection */ }
```

In `renderDetail()`:

- `const total = detail.candidates.length;`
- Clamp `state.candidatePage` to the last page.
- Slice `[start, start + CANDIDATES_PER_PAGE]`.
- Preserve the global index `i = start + offset` for `data-select`, `data-play`,
  and field edits.
- Card meta line:

```text
#3 · 8.7/10 · 43.7s
```

- Add an expandable block:

```html
<details class="candidate-reason">
  <summary>Why this clip</summary>
  <p>${escapeHTML(c.reason || "")}</p>
  <pre>${escapeHTML(JSON.stringify(c.score_parts || {}, null, 2))}</pre>
  ${c.evidence ? `<pre>${escapeHTML(JSON.stringify(c.evidence, null, 2))}</pre>` : ""}
</details>
```

- Update `#candidate-page` to `1–25 of 120` and disable prev/next at the ends.
- Call `renderSelectionSummary(detail)`.

Summary rules:

- If `detail.selection` is missing or `passing` is `0`, show the candidate score
  range and `No clips at 8.0+. Lower the minimum score to review more.`
- Otherwise show:
  `12 clips at 8.0+ · passing scores 8.0-9.2 · 18s-2m 22s`
  and `Footage: gameplay/action`.
- Use `selection.min_score`, `passing_score_min`, `passing_score_max`,
  `duration_min`, `duration_max`, and `content_profile`.

- [ ] **Step 8: Add select-all/none handlers**

```js
$("#select-all").addEventListener("click", () => {
  state.selected = new Set(state.detail.candidates.map((_, i) => i + 1));
  renderDetail();
});
$("#select-none").addEventListener("click", () => {
  state.selected.clear();
  renderDetail();
});
```

Keep `updateSelection()` as the single place that updates the selected count.
Do not reset selection on every poll; only on project load or fresh discovery.

- [ ] **Step 9: Add minimal CSS**

Add styles for `.selection-summary`, `.candidate-pager`, and
`.candidate-reason` consistent with the existing card spacing and muted text.
Do not change the existing palette or card layout.

- [ ] **Step 10: Run tests and manually inspect Studio**

```powershell
python -m pytest tests/test_studio.py -q
python dashboard.py --projects-dir "$env:TEMP\opencode\chopify-studio-test" --no-browser
```

Open the printed loopback URL, prepare the existing sample project (or create a
small one), and verify:

- minimum score defaults to 8
- preferred length accepts `90s` and rejects `abc` with a visible message
- maximum clips can be blank
- the summary shows the real range
- 25 cards per page, global numbering stable, select all/none works
- Watch still plays the correct candidate

Stop the server after verification.

- [ ] **Step 11: Commit**

```powershell
git add dashboard.py assets/studio/index.html assets/studio/app.js assets/studio/style.css tests/test_studio.py
git commit -m "feat: paginate and summarize candidates in Studio"
```

---

## Task 9: Docs, Calibration Check, and Phase Exit

**Files:**
- Modify: `README.md`, `docs/WORKFLOWS.md`, `CHANGELOG.md`,
  `docs/index.html` (only if it repeats scoring defaults)
- Test: full suite + real transcript

**Why:** The default threshold, default cap, and clip-length behaviour are deliberate breaking changes. They must be documented, and the new calibration must be checked against a real transcript rather than only synthetic fixtures.

- [ ] **Step 1: Update `README.md`**

Add or update a short section:

- Default minimum score is `8.0`; a clip at exactly `8.0` passes and is shown as `8.0+`.
- By default every passing clip is returned; `--max-clips` is optional.
- Clip length is chosen by the engine; `--preferred-len` is a soft preference.
- Default range is 15-300 seconds.
- Interactive runs ask for score and preferred length once.
- `--no-input` skips prompts for scripts and CI.
- Zero passing clips is a real result, not a fallback clip.

Keep the local-only, no-API-key promise prominent.

- [ ] **Step 2: Update `docs/WORKFLOWS.md`**

Document:

- Every sentence is considered as a start; overlapping windows are allowed up to 55%.
- The duration ladder and preferred-length tie-break.
- Score parts and the absolute calibration.
- `candidates.json` and `selection.json` as diagnostics; `segments.json` as the render contract.
- The CLI prepare/review/render flow and the >10-clip confirmation.
- Studio pagination, select all/none, and the summary.
- What to do when nothing passes.

- [ ] **Step 3: Update `CHANGELOG.md`**

Under `## [Unreleased]` add:

```markdown
### Changed
- Default minimum clip score is now 8.0 (was 6.5 heuristic / 8.0 LLM).
- Every clip at or above the threshold is returned by default; the ten-clip cap
  is now opt-in through --max-clips.
- Clip length is selected from a duration ladder; --preferred-len is a soft
  preference rather than a fixed target.
- Default clip range is 15-300 seconds (was 20-75).
- Discovery and selection are cached separately, so changing the score
  re-filters without rescanning the transcript.

### Fixed
- Overlapping candidate windows are no longer blocked by marking covered
  sentences as taken.
- Zero passing clips now writes an empty segment list instead of a fallback clip.
```

- [ ] **Step 4: Update `docs/index.html` if needed**

Search for `6.5`, `20`, `75`, `max-clips`, or "ten clips". Update only claims
that are now false. Do not redesign the page in this task.

- [ ] **Step 5: Run the real-transcript calibration check**

Use the retained 12-minute transcript:

```powershell
$work = "$env:TEMP\opencode\chopify-youtube-test\work"
python score_clips.py $work --min-score 8 --preferred-len 45s
Get-Content "$work\selection.json" -Raw
```

Acceptance:

- `passing >= 1` at the default `8.0`.
- `candidates` is substantially larger than the old single-digit output.
- `passing_score_max >= 8.0`.
- The highest clip is a coherent, complete thought when read in the transcript.
- No clip starts or ends mid-sentence.

If zero clips pass, inspect the top candidates' `score_parts` and fix the
component formulas or lexicons. Do **not** lower the default threshold and do
not special-case this transcript.

- [ ] **Step 6: Run a prepare/render smoke test**

```powershell
python chopify.py --project "$env:TEMP\opencode\chopify-youtube-test\work" --action prepare --no-input
python chopify.py --project "$env:TEMP\opencode\chopify-youtube-test\work" --action render --clips 1 --preview --no-input
```

Expected: prepare writes the three artifacts; preview renders one clip from the
new selection; captions and 16:9 output look unchanged.

- [ ] **Step 7: Run the full suite**

```powershell
python -m pytest -q
python -m compileall -q chopify.py dashboard.py projects.py captions.py branding.py semantic.py download_and_transcribe.py score_clips.py render_clips.py tighten.py
```

Expected: all tests pass, compileall exits 0.

- [ ] **Step 8: Commit docs**

```powershell
git add README.md docs/WORKFLOWS.md CHANGELOG.md docs/index.html
git commit -m "docs: document adaptive clip selection defaults"
```

- [ ] **Step 9: Review the phase diff**

```powershell
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff main...HEAD -- score_clips.py projects.py chopify.py dashboard.py
```

Check specifically:

- no paid API or network call was added except local Ollama/embeddings
- no timestamp is changed by Ollama
- `pick_segments()` indexes the start-sorted list
- existing project `max_clips: 10` still wins over the new default
- empty selection never reaches the renderer

- [ ] **Step 10: Merge to main**

```powershell
git switch main
git merge --no-ff feat/adaptive-discovery -m "feat: adaptive clip discovery phase 1"
python -m pytest -q
```

Do not push unless the user asks.

---

## Phase 1 Exit Criteria

- [ ] Every sentence is evaluated as a start; the old `taken` bug is gone.
- [ ] Default threshold is `8.0`, inclusive.
- [ ] Default cap is `None`; every passing clip is returned.
- [ ] A preferred length is a soft prior and can be overridden by a stronger endpoint.
- [ ] Zero passing clips writes `[]` and gives a real score range.
- [ ] CLI prompts once, prepares first, and confirms before rendering more than ten clips.
- [ ] Studio shows the new fields, paginated candidates, select all/none, and a summary.
- [ ] `candidates.json` and `selection.json` are written and cached separately.
- [ ] Keyword and semantic search use the shared discovery/dedupe path.
- [ ] Ollama reranks only the top 50 and cannot change timestamps.
- [ ] Existing caption presets, brand JSON, and default 16:9 output are unchanged.
- [ ] Full test suite passes and the real 12-minute transcript produces at least one `8.0+` clip.

## Follow-On Phases

Phase 2 adds creator-grade audio/visual evidence, content profiles, speech-sparse
floors, event arcs, boundary refinement, and gameplay seeds. It consumes the
candidate contract from this phase unchanged.

Phase 3 adds probe/chunked ingest, resumable transcription, block scanning, and
long-source section rendering.

Do not start Phase 2 until the Phase 1 exit criteria are met and the branch is
merged.

