"""Chopify test suite - covers all pure (non-ffmpeg) logic."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

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

    def test_candidates_non_overlapping(self, sample_words):
        sents = score_clips.sentenceize(sample_words)
        segs = score_clips.build_candidates(sents, min_len=5, max_len=30, max_clips=10)
        for a, b in zip(segs, segs[1:]):
            assert a["end"] <= b["start"] + 0.01


class TestNearest:
    def test_exact_and_between(self):
        assert score_clips._nearest([1, 5, 9], 5) == 1
        assert score_clips._nearest([1, 5, 9], 6) == 1
        assert score_clips._nearest([1, 5, 9], 8) == 2
        assert score_clips._nearest([4], 100) == 0


class TestRun:
    def test_run_writes_segments(self, workdir):
        segs, mode = score_clips.run(workdir)
        assert mode == "heuristic"
        assert (workdir / "segments.json").exists()
        on_disk = json.loads((workdir / "segments.json").read_text())
        assert len(on_disk) == len(segs) >= 1

    def test_ollama_fallback_on_dead_server(self, workdir):
        segs, mode = score_clips.run(workdir, llm="qwen2.5:7b",
                                     host="http://127.0.0.1:1", max_clips=3)
        assert mode == "heuristic"       # graceful fallback, no crash
        assert len(segs) >= 1

    def test_missing_transcript_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            score_clips.run(tmp_path)

    def test_bom_transcript_accepted(self, workdir):
        raw = (workdir / "transcript.json").read_bytes()
        (workdir / "transcript.json").write_bytes(b"\xef\xbb\xbf" + raw)
        segs, _ = score_clips.run(workdir)
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
        assert "&H00FFFF&" in out_y.read_text(encoding="utf-8")
        assert "&H0000FF&" in out_r.read_text(encoding="utf-8")   # red highlight

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
        segs, _ = score_clips.run(workdir, min_len=4, max_len=20, pick="1")
        assert len(segs) == 1

    def test_run_pick_out_of_range_exits(self, workdir):
        with pytest.raises(SystemExit):
            score_clips.run(workdir, min_len=4, max_len=20, pick="99")


class TestPrintTable:
    def test_table_lists_indexes(self, capsys):
        score_clips.print_table([seg(0, 5, "alpha hook", 7.2),
                                 seg(6, 9, "beta hook", 6.1)])
        out = capsys.readouterr().out
        assert "alpha hook" in out and "7.2/10" in out



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

