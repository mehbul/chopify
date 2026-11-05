"""Chopify test suite - covers all pure (non-ffmpeg) logic."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np            # noqa: E402
import score_clips          # noqa: E402
import tighten              # noqa: E402
import render_clips         # noqa: E402
import chopify              # noqa: E402


# ----------------------------------------------------------------- fixtures

def make_word(w, start, dur=0.35):
    return {"word": w, "start": round(start, 3), "end": round(start + dur, 3)}


def sentence(text, start):
    words = [make_word(w, start + i * 0.4) for i, w in enumerate(text.split())]
    return words


@pytest.fixture
def sample_words():
    words = []
    words += sentence("Here is the thing nobody tells you about growing an audience.", 0.0)
    words += sentence("The secret is consistency not talent, and that is the truth.", 8.0)
    words += sentence("Um, most creators quit after three months of posting.", 16.5)
    words += sentence("The biggest mistake is copying viral videos instead of building a system.", 25.0)
    words += sentence("So here is how you fix it, first pick one format and publish weekly.", 34.0)
    words += sentence("Anyway, thanks for watching the whole video today.", 43.0)
    t = 0.0
    return words


@pytest.fixture
def workdir(tmp_path, sample_words):
    wd = tmp_path / "work"
    wd.mkdir()
    data = {"video": "fake.mp4", "language": "en", "duration": 50.0,
            "model": "test", "segments": [], "words": sample_words}
    (wd / "transcript.json").write_text(
        json.dumps(data), encoding="utf-8")
    return wd


# ------------------------------------------------------------- score_clips

class TestSentenceize:
    def test_splits_on_punctuation(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        assert len(sents) >= 4
        assert sents[0]["start"] == pytest.approx(sample_words[0]["start"])
        assert sents[0]["text"].endswith("audience.")

    def test_words_preserved(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        total = sum(len(s["words"]) for s in sents)
        assert total == len(sample_words)


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


class TestScoring:
    def test_hook_sentence_scores_higher(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        hook = next(s for s in sents if "nobody tells" in s["text"])
        flat = next(s for s in sents if "thanks for watching" in s["text"])
        _, h_score = score_clips.score_sentence(hook)
        _, f_score = score_clips.score_sentence(flat)
        assert h_score > f_score

    def test_score_in_range(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        for s in sents:
            _, sc = score_clips.score_sentence(s)
            assert 0.0 <= sc <= 10.0

    def test_complete_arc_bonus(self):
        done = score_clips.score_sentence(
            {"text": "That is the whole story.", "start": 0, "end": 3, "words": []})
        mid = score_clips.score_sentence(
            {"text": "and then we were walking to the", "start": 0, "end": 3, "words": []})
        assert done[1] > mid[1]


class TestHooks:
    def test_make_hook_strips_fillers(self):
        assert "um" not in score_clips.make_hook("um basically the secret sauce")

    def test_make_hook_length(self):
        assert len(score_clips.make_hook("a b c d e f g h i j").split()) <= 7

    def test_make_hook_never_empty(self):
        assert score_clips.make_hook("!!!") == "clip"


class TestCandidates:
    def test_build_candidates_shape(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        segs = score_clips.build_candidates(sents, min_len=5, max_len=30, max_clips=3)
        assert 0 < len(segs) <= 3
        for s in segs:
            assert s["end"] > s["start"]
            assert 0 <= s["start"] and s["end"] <= 50.0
            assert s["hook"]
            assert 0 <= s["overall"] <= 10

    def test_candidates_overlap_no_more_than_55_percent(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        segs = score_clips.build_candidates(sents, min_len=5, max_len=30, max_clips=10)
        for i, a in enumerate(segs):
            for b in segs[i + 1:]:
                assert score_clips.overlap_ratio(a, b) <= 0.55


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


class TestNearest:
    def test_exact_and_between(self):
        assert score_clips._nearest([1, 5, 9], 5) == 1
        assert score_clips._nearest([1, 5, 9], 6) == 1
        assert score_clips._nearest([1, 5, 9], 8) == 2
        assert score_clips._nearest([4], 100) == 0


class TestRun:
    def test_run_writes_segments(self, workdir):
        segs, mode = score_clips.run(workdir, min_score=0)
        assert mode == "heuristic"
        assert (workdir / "segments.json").exists()
        on_disk = json.loads((workdir / "segments.json").read_text())
        assert len(on_disk) == len(segs) >= 1

    def test_ollama_fallback_on_dead_server(self, workdir):
        segs, mode = score_clips.run(workdir, llm="qwen2.5:7b", min_score=0,
                                     host="http://127.0.0.1:1", max_clips=3)
        assert mode == "heuristic"       # graceful fallback, no crash
        assert len(segs) >= 1

    def test_missing_transcript_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            score_clips.run(tmp_path)

    def test_bom_transcript_accepted(self, workdir):
        raw = (workdir / "transcript.json").read_bytes()
        (workdir / "transcript.json").write_bytes(b"\xef\xbb\xbf" + raw)
        segs, _ = score_clips.run(workdir, min_score=0)
        assert len(segs) >= 1


# ----------------------------------------------------------------- tighten

class TestPlanCuts:
    def test_no_words_keeps_everything(self):
        keeps = tighten.plan_cuts([], 10.0, 40.0)
        assert keeps == [(0.0, 30.0)]

    def test_filler_words_dropped(self, sample_words):
        keeps = tighten.plan_cuts(sample_words, 16.5, 25.0, fillers=True, silence=False)
        total = sum(b - a for a, b in keeps)
        assert total < 25.0 - 16.5          # something was cut
        # "um" sits at clip-relative ~0-0.35s and is guarded only by GUARD=0.15
        assert all(a >= 0.0 for a, b in keeps)

    def test_silence_cut_only_when_asked(self, sample_words):
        loose = tighten.plan_cuts(sample_words, 0.0, 48.0, gap=0.6, silence=True)
        strict = tighten.plan_cuts(sample_words, 0.0, 48.0, gap=5.0, silence=True)
        assert sum(b - a for a, b in strict) > sum(b - a for a, b in loose)

    def test_guard_band_never_cut(self, sample_words):
        keeps = tighten.plan_cuts(sample_words, 0.0, 48.0)
        assert keeps[0][0] == 0.0
        assert keeps[-1][1] == pytest.approx(48.0, abs=0.05)

    def test_minimum_keep_respected(self, sample_words):
        keeps = tighten.plan_cuts(sample_words, 0.0, 48.0)
        for a, b in keeps:
            assert b - a >= tighten.MIN_KEEP - 0.01

    def test_filler_seq_you_know(self):
        w = sentence("you know this thing is huge", 0.0)
        keeps = tighten.plan_cuts(w, 0.0, 12.0, fillers=True, silence=False)
        total = sum(b - a for a, b in keeps)
        assert total < 12.0                  # "you know" got dropped


class TestRemap:
    def test_remap_shifts_words_left(self, sample_words):
        keeps = [(0.0, 2.0), (5.0, 8.0)]     # cut 3s in the middle
        out, dur = tighten.remap_words(sample_words, 0.0, keeps)
        assert dur == pytest.approx(5.0)
        assert all(w["end"] <= dur + 0.01 for w in out)

    def test_remap_preserves_word_order(self, sample_words):
        keeps = [(0.0, 2.0), (5.0, 8.0)]     # (5,8) is silence -> no words there
        out, _ = tighten.remap_words(sample_words, 0.0, keeps)
        text = " ".join(w["word"] for w in out)
        assert text.startswith("Here is the")

    def test_remap_dur_matches_keeps_sum(self, sample_words):
        keeps = [(0.0, 3.0), (4.0, 9.0)]
        _, dur = tighten.remap_words(sample_words, 0.0, keeps)
        assert dur == pytest.approx(sum(b - a for a, b in keeps))


# ------------------------------------------------------------- render_clips

class TestRenderHelpers:
    def test_sanitize_filename(self):
        assert render_clips.sanitize("Hello, World! 123") == "hello-world-123"
        assert render_clips.sanitize("") == "clip"
        assert len(render_clips.sanitize("x" * 200)) <= 70

    def test_crop_for_16x9_source(self):
        assert render_clips.crop_for(1920, 1080, 1920, 1080) == (1920, 1080, 0, 0)

    def test_crop_for_vertical(self):
        cw, ch, x0, y0 = render_clips.crop_for(1920, 1080, 1080, 1920)
        assert cw == 608                     # round(1080 * 1080/1920) = round(607.5)
        assert ch == 1080 and x0 == 656 and y0 == 0

    def test_default_out_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHOPIFY_OUT", str(tmp_path / "myclips"))
        assert render_clips.default_out() == tmp_path / "myclips"

    def test_default_out_fallback(self, monkeypatch):
        monkeypatch.delenv("CHOPIFY_OUT", raising=False)
        assert render_clips.default_out() == Path("clips")

    def test_get_style_valid_and_invalid(self):
        assert render_clips.get_style("hormozi")["font"] == "Arial Black"
        with pytest.raises(SystemExit):
            render_clips.get_style("nope")


class TestWriteMeta:
    def test_meta_file_written(self, tmp_path):
        seg = {"start": 12.5, "end": 44.0, "hook": "the-biggest-mistake", "overall": 8.2}
        clip = tmp_path / "the-biggest-mistake.mp4"
        p = render_clips.write_meta(seg, clip)
        assert p.exists() and p.name.endswith(".meta.json")
        meta = json.loads(p.read_text(encoding="utf-8"))
        assert meta["score"] == 8.2
        assert "The biggest mistake" in meta["title"]
        assert meta["hashtags"][0] == "#shorts"


class TestASS:
    def test_ass_contains_events(self, tmp_path, sample_words):
        out = tmp_path / "c.ass"
        render_clips.build_ass(sample_words, 0.0, 48.0, out, 1080, 1920, 96, 300)
        content = out.read_text(encoding="utf-8")
        assert "Dialogue: 0," in content
        assert "PlayResY: 1920" in content

    def test_ass_style_preset_colours(self, tmp_path, sample_words):
        out_y = tmp_path / "y.ass"
        out_r = tmp_path / "r.ass"
        render_clips.build_ass(sample_words, 0.0, 48.0, out_y, 1920, 1080, 72, 95,
                               style_name="default")
        render_clips.build_ass(sample_words, 0.0, 48.0, out_r, 1920, 1080, 72, 95,
                               style_name="hormozi")
        assert "&H0000FFFF&" in out_y.read_text(encoding="utf-8")
        assert "&H000000FF&" in out_r.read_text(encoding="utf-8")   # red highlight

    def test_ass_escapes_braces(self, tmp_path):
        w = [{"word": "{weird}", "start": 0.0, "end": 0.3}]
        out = tmp_path / "e.ass"
        render_clips.build_ass(w, 0.0, 1.0, out, 1920, 1080, 72, 95)
        assert "{" not in render_clips.ass_escape("{weird}")


# ------------------------------------------------------- v1.3: complete-thought

class TestArc:
    def test_is_arc_complete(self):
        assert score_clips.is_arc_complete("That is the whole story.")
        assert score_clips.is_arc_complete('He said "no!"')
        assert not score_clips.is_arc_complete("walking to the")
        assert not score_clips.is_arc_complete("")

    def test_candidates_end_on_complete_thought(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        segs = score_clips.build_candidates(sents, min_len=5, max_len=30, max_clips=5)
        assert segs
        for seg_ in segs:
            last = [s for s in sents
                    if s["end"] <= seg_["end"] + 0.01 and s["end"] > seg_["start"] - 30]
            assert score_clips.is_arc_complete(last[-1]["text"]), seg_

    def test_grow_window_backs_off_mid_sentence_end(self):
        # one complete sentence followed by an incomplete tail with no punctuation
        words = (sentence("Here is the thing nobody tells you about this.", 0.0)
                 + sentence("and then we were walking to the", 8.0))
        sents = score_clips.sentenceize(words)
        taken = [False] * len(sents)
        w = score_clips._grow_window(sents, 0, taken, min_len=5, max_len=30)
        assert w is not None
        a, b = w
        assert score_clips.is_arc_complete(sents[b]["text"])


# ------------------------------------------------------------ v1.3: --search

class TestSearch:
    def test_search_hits_all_tokens(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        hits = score_clips.search_hits(sents, "biggest mistake")
        assert len(hits) == 1
        assert "biggest mistake" in sents[hits[0]]["text"].lower()

    def test_search_hits_all_tokens_required(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        # "secret" and "quitting" never co-occur in one sentence -> no hits
        assert score_clips.search_hits(sents, "secret quitting") == []

    def test_search_hits_empty_query(self, sample_words):
        assert score_clips.search_hits(score_clips.sentenceize(sample_words), "") == []
        assert score_clips.search_hits(score_clips.sentenceize(sample_words), "!!!") == []

    def test_search_candidates_shape(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        segs = score_clips.search_candidates(sents, "biggest mistake",
                                             min_len=5, max_len=30, max_clips=3)
        assert len(segs) == 1
        assert segs[0]["start"] <= sents[3]["start"]

    def test_run_search_writes_segments(self, workdir):
        segs, mode = score_clips.run(workdir, search="biggest mistake",
                                     min_len=4, max_len=20)
        assert mode == "heuristic"
        assert len(segs) == 1
        on_disk = json.loads((workdir / "segments.json").read_text())
        assert len(on_disk) == 1

    def test_run_search_no_match_exits(self, workdir):
        with pytest.raises(SystemExit):
            score_clips.run(workdir, search="zzzqqqxyzzy")



# ------------------------------------------------------------- v1.3: --clips

def seg(start, end, hook, overall=5.0):
    return {"start": start, "end": end, "hook": hook, "overall": overall}


class TestPick:
    def test_pick_segments_subset(self):
        segs = [seg(0, 5, "a"), seg(6, 9, "b"), seg(10, 15, "c")]
        got = score_clips.pick_segments(segs, "1,3")
        assert [s["hook"] for s in got] == ["a", "c"]

    def test_pick_none_returns_all(self):
        segs = [seg(0, 5, "a")]
        assert score_clips.pick_segments(segs, None) is segs

    def test_pick_tolerates_spaces(self):
        segs = [seg(0, 5, "a"), seg(6, 9, "b")]
        got = score_clips.pick_segments(segs, "2, 1")
        assert [s["hook"] for s in got] == ["a", "b"]

    def test_pick_out_of_range_exits(self):
        with pytest.raises(SystemExit):
            score_clips.pick_segments([seg(0, 5, "a")], "5")

    def test_pick_non_numeric_exits(self):
        with pytest.raises(SystemExit):
            score_clips.pick_segments([seg(0, 5, "a")], "x")

    def test_run_pick_selects_one(self, workdir):
        segs, _ = score_clips.run(workdir, min_score=0, min_len=4, max_len=20, pick="1")
        assert len(segs) == 1

    def test_run_pick_out_of_range_exits(self, workdir):
        with pytest.raises(SystemExit):
            score_clips.run(workdir, min_score=0, min_len=4, max_len=20, pick="99")


class TestPrintTable:
    def test_table_lists_indexes(self, capsys):
        score_clips.print_table([seg(0, 5, "alpha hook", 7.2),
                                 seg(6, 9, "beta hook", 6.1)])
        out = capsys.readouterr().out
        assert "alpha hook" in out and "7.2/10" in out



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


class TestSelectionSummary:
    def stats(self, **overrides):
        base = {"windows_scanned": 147456, "source_duration": 2892,
                "score_min": 4.1, "score_max": 9.2, "passing": 12,
                "min_score": 8.0, "preferred_len": None}
        base.update(overrides)
        return base

    def test_three_line_summary(self):
        text = score_clips.format_selection_summary(self.stats())
        lines = text.splitlines()
        assert lines[0] == ("Selection: minimum score 8.0, preferred length "
                            "AI chooses.")
        assert lines[1] == "Scanned 147,456 windows across 48m 12s."
        assert lines[2] == ("Candidate scores 4.1-9.2. 12 distinct clips at "
                            "8.0 or above.")

    def test_preferred_length_and_zero_clips(self):
        text = score_clips.format_selection_summary(self.stats(
            preferred_len=45, passing=0, score_max=6.8))
        assert "preferred length 45s" in text
        assert "0 distinct clips at 8.0 or above" in text

    def test_duration_formats(self):
        assert score_clips.format_duration(43.7, True) == "43.7s"
        assert score_clips.format_duration(142) == "2m 22s"
        assert score_clips.format_duration(3720) == "1h 02m"


# --------------------------------------------------------- v1.3: --preview

class TestPreview:
    def test_preview_dims(self):
        assert render_clips.PREVIEW["16:9"] == (854, 480)
        assert render_clips.PREVIEW["9:16"] == (480, 854)
        assert render_clips.PREVIEW["1:1"] == (480, 480)

    def test_preview_dims_match_aspect(self):
        for a, (tw, th) in render_clips.PREVIEW.items():
            fw, fh = render_clips.ASPECTS[a][:2]
            assert abs(tw / th - fw / fh) < 0.02


# ------------------------------------------------------- v1.3: preflight

class TestPreflight:
    def test_missing_ffmpeg_flagged(self, monkeypatch):
        monkeypatch.setattr(chopify.shutil, "which", lambda t: None)
        monkeypatch.setattr(chopify, "_missing_modules", lambda: [])
        problems = chopify.check_environment()
        assert any("ffmpeg" in p for p in problems)
        assert any("ffprobe" in p for p in problems)

    def test_missing_libx264_flagged(self, monkeypatch):
        monkeypatch.setattr(chopify.shutil, "which",
                            lambda t: "C:/bin/ffmpeg.exe" if t == "ffmpeg"
                            else "C:/bin/ffprobe.exe")
        monkeypatch.setattr(chopify, "_missing_modules", lambda: [])
        fake = subprocess.CompletedProcess([], 0, stdout=" V mp4v ... A pcm_s16le")
        problems = chopify.check_environment(runner=lambda cmd, **k: fake)
        assert any("libx264" in p for p in problems)
        assert any("AAC" in p for p in problems)

    def test_clean_environment_passes(self, monkeypatch):
        monkeypatch.setattr(chopify.shutil, "which", lambda t: "C:/bin/" + t)
        monkeypatch.setattr(chopify, "_missing_modules", lambda: [])
        fake = subprocess.CompletedProcess(
            [], 0, stdout=" V libx264 x264 ... A aac AAC (audio)")
        assert chopify.check_environment(runner=lambda cmd, **k: fake) == []

    def test_missing_module_flagged(self, monkeypatch):
        monkeypatch.setattr(chopify.shutil, "which", lambda t: "C:/bin/" + t)
        monkeypatch.setattr(chopify, "_missing_modules",
                            lambda: [("faster_whisper",
                                      "pip install -r requirements.txt")])
        fake = subprocess.CompletedProcess([], 0, stdout=" V libx264 ... A aac AAC")
        problems = chopify.check_environment(runner=lambda cmd, **k: fake)
        assert len(problems) == 1
        assert "faster_whisper" in problems[0]
        assert "pip install" in problems[0]



# ---------------------------------------------------------- creator evidence

def sparse_workdir(tmp_path, words, duration=40.0):
    wd = tmp_path / "sparse"
    wd.mkdir()
    data = {"video": "fake.mp4", "language": "en", "duration": duration,
            "model": "test", "segments": [], "words": words}
    (wd / "transcript.json").write_text(json.dumps(data), encoding="utf-8")
    return wd


class TestCreatorEvidence:
    def test_discovery_attaches_audio_evidence(self, workdir, monkeypatch):
        import evidence
        timeline = evidence.features_from_samples(
            np.concatenate([np.full(16000 * 4, 0.001, dtype=np.float32),
                            np.full(16000, 0.8, dtype=np.float32)]))
        monkeypatch.setattr(evidence, "load_audio_timeline",
                            lambda *a, **k: timeline)
        segs, _ = score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
        candidates = json.loads(
            (workdir / "candidates.json").read_text())["candidates"]
        assert all("evidence" in item for item in candidates)
        assert any(item["evidence"]["audio"] is not None for item in candidates)
        selection = json.loads((workdir / "selection.json").read_text())
        assert selection["evidence_available"]["audio"] is True
        assert selection["content_profile"] in {"mixed", "gameplay/action"}

    def test_missing_source_keeps_neutral_evidence(self, workdir):
        segs, _ = score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
        selection = json.loads((workdir / "selection.json").read_text())
        assert selection["evidence_available"] == {"audio": False, "visual": False}
        assert segs
        candidates = json.loads(
            (workdir / "candidates.json").read_text())["candidates"]
        assert all(item["evidence"]["score"] == 5.0 for item in candidates)
        assert all(item["overall"] == item["raw_score"] for item in candidates)

    def test_ollama_rerank_uses_final_score(self, monkeypatch):
        monkeypatch.setattr(
            score_clips, "_ollama_chat",
            lambda *a, **k: [{"index": 0, "adjustment": 0.3}])
        records = [{"start": 0.0, "end": 10.0, "duration": 10.0,
                    "overall": 7.0, "raw_score": 8.0, "text": "x"}]
        updated = score_clips.ollama_rerank(records, "test")
        assert updated[0]["overall"] == pytest.approx(7.3, abs=0.05)
        assert updated[0]["raw_score"] == pytest.approx(8.0)

    def test_ollama_rerank_does_not_compound(self, monkeypatch):
        monkeypatch.setattr(
            score_clips, "_ollama_chat",
            lambda *a, **k: [{"index": 0, "adjustment": 0.3}])
        records = [{"start": 0.0, "end": 10.0, "duration": 10.0,
                    "overall": 7.3, "raw_score": 8.0, "text": "x",
                    "llm_adjustment": 0.3}]
        updated = score_clips.ollama_rerank(records, "test")
        assert updated[0]["overall"] == pytest.approx(7.3, abs=0.05)

    def test_sparse_windows_use_text_floor(self, tmp_path, monkeypatch):
        import evidence
        words = (sentence("That was insane.", 0.0)
                 + sentence("No way, let's go.", 30.0))
        workdir = sparse_workdir(tmp_path, words)
        timeline = evidence.features_from_samples(
            np.concatenate([np.full(16000 * 2, 0.01, dtype=np.float32),
                            np.full(16000, 0.9, dtype=np.float32),
                            np.full(16000 * 2, 0.01, dtype=np.float32)]))
        monkeypatch.setattr(evidence, "load_audio_timeline",
                            lambda *a, **k: timeline)
        segs, _ = score_clips.run(workdir, min_score=0, min_len=1, max_len=40)
        sparse = [s for s in segs if s.get("evidence", {}).get("text_floor")]
        assert sparse
        assert all(s["evidence"]["weight"] >= 0.75 for s in sparse)


# ------------------------------------------------------ action-only sources

def wordless_workdir(tmp_path, source, duration=30.0):
    wd = tmp_path / "wordless"
    wd.mkdir()
    data = {"video": str(source), "language": "en", "duration": duration,
            "model": "test", "segments": [], "words": []}
    (wd / "transcript.json").write_text(json.dumps(data), encoding="utf-8")
    return wd


def test_wordless_source_uses_audio_event_seeds(tmp_path, monkeypatch):
    import evidence
    samples = np.concatenate([
        np.full(evidence.SAMPLE_RATE * 5, 0.001, dtype=np.float32),
        np.full(evidence.SAMPLE_RATE, 0.8, dtype=np.float32),
        np.full(evidence.SAMPLE_RATE * 4, 0.001, dtype=np.float32),
    ])
    timeline = evidence.features_from_samples(samples)
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 10.0)
    monkeypatch.setattr(evidence, "load_audio_timeline", lambda *a, **k: timeline)
    segs, _ = score_clips.run(wd, min_score=0, min_len=4, max_len=10)
    assert segs
    assert all(s["evidence"]["audio"] is not None for s in segs)
    assert any("event" in s["hook"] or "action" in s["hook"] for s in segs)


def test_wordless_flat_source_fails_clearly(tmp_path):
    workdir = wordless_workdir(tmp_path, tmp_path / "flat.mp4", 30.0)
    with pytest.raises(ValueError, match="No speech and no media evidence"):
        score_clips.run(workdir, min_score=8.0)


def test_silent_audio_uses_visual_baseline_energy(workdir, monkeypatch):
    import evidence
    import visual
    flat = evidence.features_from_samples(
        np.full(evidence.SAMPLE_RATE * 5, 0.001, dtype=np.float32))
    monkeypatch.setattr(evidence, "load_audio_timeline", lambda *a, **k: flat)
    monkeypatch.setattr(
        visual, "sample_baseline",
        lambda *a, **k: visual.VisualBaseline(0.8, 0.9, 20.0, 0.1, 12))
    data = json.loads((workdir / "transcript.json").read_text())
    (timeline, profile, energy, density, audio_usable,
     baseline) = score_clips.source_evidence(workdir, data)
    assert timeline is flat
    assert audio_usable is False
    assert baseline is not None
    assert baseline.value == pytest.approx(0.8)
    assert energy > 0.5
    assert profile == "gameplay/action"


def test_silent_audio_discovery_marks_audio_unavailable(workdir, monkeypatch):
    import evidence
    import visual
    flat = evidence.features_from_samples(
        np.full(evidence.SAMPLE_RATE * 5, 0.001, dtype=np.float32))
    monkeypatch.setattr(evidence, "load_audio_timeline", lambda *a, **k: flat)
    monkeypatch.setattr(
        visual, "sample_baseline",
        lambda *a, **k: visual.VisualBaseline(0.8, 0.9, 20.0, 0.1, 12))
    score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
    selection = json.loads((workdir / "selection.json").read_text())
    assert selection["evidence_available"] == {"audio": False, "visual": True}
    candidates = json.loads(
        (workdir / "candidates.json").read_text())["candidates"]
    assert candidates
    assert all(item["evidence"]["audio"] is None for item in candidates)


def synthetic_action_features(start=0.0, duration=20.0):
    import visual
    times = np.arange(start, start + duration, 1.0 / visual.SAMPLE_FPS,
                      dtype=np.float64)
    relative = times - start
    motion = np.full(len(times), 0.03, dtype=np.float32)
    ramp = (relative >= 4.0) & (relative < 8.0)
    motion[ramp] = np.linspace(0.10, 0.80, int(ramp.sum()))
    peak = (relative >= 8.0) & (relative < 11.0)
    motion[peak] = 0.80
    decay = (relative >= 11.0) & (relative < 15.0)
    motion[decay] = np.linspace(0.80, 0.05, int(decay.sum()))
    change = np.full(len(times), 0.03, dtype=np.float32)
    event = (relative >= 8.0) & (relative < 13.0)
    change[event] = 0.50
    cut = np.zeros(len(times), dtype=bool)
    cut[np.flatnonzero(np.abs(relative - 8.0) < 1e-9)] = True
    return visual.VisualFeatures(
        start=start, end=start + duration, times=times, motion=motion,
        cut=cut, change=change, center_change=change.copy(),
        face=np.zeros(len(times), dtype=bool),
        face_motion=np.zeros(len(times), dtype=np.float32))


def flat_action_features(start=0.0, duration=20.0):
    import visual
    times = np.arange(start, start + duration, 1.0 / visual.SAMPLE_FPS,
                      dtype=np.float64)
    count = len(times)
    return visual.VisualFeatures(
        start=start, end=start + duration, times=times,
        motion=np.full(count, 0.03, dtype=np.float32),
        cut=np.zeros(count, dtype=bool),
        change=np.full(count, 0.03, dtype=np.float32),
        center_change=np.full(count, 0.03, dtype=np.float32),
        face=np.zeros(count, dtype=bool),
        face_motion=np.zeros(count, dtype=np.float32))


def test_wordless_silent_source_uses_visual_evidence(tmp_path, monkeypatch):
    import visual
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 20.0)
    monkeypatch.setattr(
        visual, "sample_baseline",
        lambda *a, **k: visual.VisualBaseline(0.7, 0.8, 0.0, 0.0, 4))
    monkeypatch.setattr(
        visual, "load_or_analyze",
        lambda *a, **k: [synthetic_action_features(0.0, 20.0)])
    segs, _ = score_clips.run(
        wd, min_score=8.0, min_len=5, max_len=20, preferred_len=15)
    assert segs
    assert all(item["evidence"]["audio"] is None for item in segs)
    assert any(item["evidence"]["visual"] is not None for item in segs)
    selection = json.loads((wd / "selection.json").read_text())
    assert selection["evidence_available"] == {"audio": False, "visual": True}
    assert selection["visual_ranges"] >= 1
    assert selection["visual_sections"] == 1
    assert selection["visual_baseline"]["value"] == pytest.approx(0.7)
    candidates = json.loads((wd / "candidates.json").read_text())
    assert candidates["visual_baseline"]["value"] == pytest.approx(0.7)


def test_silent_flat_source_fails_without_visual_evidence(tmp_path, monkeypatch):
    import visual
    source = tmp_path / "flat.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 20.0)
    monkeypatch.setattr(visual, "sample_baseline", lambda *a, **k: None)
    with pytest.raises(ValueError, match="No speech and no media evidence"):
        score_clips.run(wd, min_score=8.0)


def test_flat_visual_action_cannot_pass_eight(tmp_path, monkeypatch):
    import visual
    source = tmp_path / "flat.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 20.0)
    monkeypatch.setattr(
        visual, "sample_baseline",
        lambda *a, **k: visual.VisualBaseline(0.7, 0.8, 0.0, 0.0, 4))
    monkeypatch.setattr(
        visual, "load_or_analyze",
        lambda *a, **k: [flat_action_features(0.0, 20.0)])
    segs, _ = score_clips.run(
        wd, min_score=8.0, min_len=5, max_len=20, preferred_len=15)
    assert segs == []


# ------------------------------------------------------ section source offsets

def test_render_seeks_inside_section_with_global_caption_times(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(render_clips.subprocess, "run", fake_run)
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    out = render_clips.render(
        {"id": "001", "start": 55, "end": 60, "hook": "clip"},
        [{"word": "hello", "start": 55, "end": 56}],
        source, 640, 360, tmp_path, "16:9", out_dir=tmp_path, preview=True,
        source_offset=50)
    assert commands[0][commands[0].index("-ss") + 1] == "5.000"
    assert "00:00:00,000" in out.with_suffix(".srt").read_text()


def test_render_rejects_clip_before_source_offset(tmp_path):
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    with pytest.raises(ValueError, match="precedes"):
        render_clips.render(
            {"id": "001", "start": 40, "end": 50, "hook": "clip"},
            [], source, 640, 360, tmp_path, "16:9", out_dir=tmp_path,
            preview=True, source_offset=50)
    with pytest.raises(ValueError, match="nonnegative"):
        render_clips.render(
            {"id": "002", "start": 55, "end": 60, "hook": "clip"},
            [], source, 640, 360, tmp_path, "16:9", out_dir=tmp_path,
            preview=True, source_offset=-1)


def test_apply_cuts_seeks_from_section_offset(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(
        tighten.subprocess, "run",
        lambda cmd, **kw: commands.append([str(c) for c in cmd]))
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    tighten.apply_cuts(source, [(0.0, 1.0)], 55, tmp_path / "tight.mp4",
                       source_offset=50)
    assert commands[0][commands[0].index("-ss") + 1] == "5.000"
    assert commands[0][commands[0].index("-t") + 1] == "1.000"


# ------------------------------------------------- partial long render failures

def test_cli_copies_partial_long_outputs_then_exits_nonzero(
        tmp_path, monkeypatch):
    import projects

    project = tmp_path / "long project"
    projects.init_project(project, "https://example.com/long", {})
    rendered = tmp_path / "clips" / "001-clip.mp4"
    rendered.parent.mkdir()
    for ext in (".mp4", ".srt", ".png", ".meta.json"):
        rendered.with_suffix(ext).write_text("rendered", encoding="utf-8")
    failed_state = {
        "id": "long", "selection": {}, "status": "failed",
        "render_errors": [{"clip": "002", "error": "404"}],
        "last_outputs": [str(rendered)],
    }
    monkeypatch.setattr(projects, "run_project",
                        lambda *args, **kwargs: failed_state)
    export = tmp_path / "export"
    monkeypatch.setattr(sys, "argv", [
        "chopify", "--project", str(project), "--action", "render",
        "--no-preflight", "--out", str(export)])
    with pytest.raises(SystemExit) as exc:
        chopify.main()
    assert exc.value.code == 1
    assert (export / "001-clip.mp4").read_text(encoding="utf-8") == "rendered"
    assert (export / "001-clip.meta.json").is_file()


def test_version_flag():
    """--version prints the version and exits 0 without requiring a source or environment."""
    result = subprocess.run(
        [sys.executable, str(REPO / "chopify.py"), "--version"],
        capture_output=True, text=True, cwd=REPO)
    assert result.returncode == 0
    assert "Chopify 1.4.0" in result.stdout
    assert result.stderr == ""
