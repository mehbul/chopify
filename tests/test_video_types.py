"""Unit tests for local video-type detection and clipping formats."""
import pytest

import video_types as vt


def signals(**overrides):
    base = dict(speech=0.0, audio=0.0, burst=0.0, visual=0.0,
                cuts=0.0, facecam=0.0, long=0.0, streamish=0.0)
    base.update(overrides)
    return base


def test_classify_podcast_from_dense_speech_and_quiet_static_frame():
    result = vt.classify(signals(speech=0.9, audio=0.1, cuts=0.1, visual=0.1))
    assert result["type"] == "podcast"
    assert 0.0 <= result["confidence"] <= 1.0
    assert "signals" in result and "reason" in result


def test_classify_gaming_from_cuts_bursts_and_facecam():
    result = vt.classify(signals(cuts=0.9, burst=0.9, facecam=0.7, speech=0.1))
    assert result["type"] == "gaming"


def test_classify_uses_stream_metadata_for_livestream():
    result = vt.classify(signals(streamish=1.0, long=0.8, cuts=0.6, facecam=0.5))
    assert result["type"] == "livestream"


def test_no_signals_is_unknown_and_needs_confirmation():
    result = vt.classify(signals())
    assert result["type"] == "unknown"
    assert result["needs_confirmation"] is True


def test_llm_tie_break_overrides_uncertain_result():
    called = []

    def fake_llm(sig, sample):
        called.append(sample)
        return "movie"

    result = vt.classify(signals(), llm=fake_llm, transcript_sample="once upon a time")
    assert result["type"] == "movie"
    assert result["needs_confirmation"] is False
    assert called == ["once upon a time"]


def test_bad_llm_label_keeps_heuristic():
    # cuts=0.5 narrows the margin so the optional LLM is consulted.
    result = vt.classify(signals(speech=0.9, audio=0.1, cuts=0.5),
                         llm=lambda *_: "not-a-type")
    assert result["type"] == "podcast"
    assert "LLM" in result["reason"]


def test_llm_classify_parses_ollama_reply(monkeypatch):
    import urllib.request

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"message":{"content":"gaming"}}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response())
    assert vt.llm_classify(signals(), "hello", "qwen2.5:7b") == "gaming"


def test_llm_classify_returns_none_when_ollama_unreachable(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert vt.llm_classify(signals(), "hello", "qwen2.5:7b") is None


def test_validate_format_rejects_bad_values():
    with pytest.raises(ValueError, match="min_len"):
        vt.validate_format({"name": "mine", "min_len": 2, "max_len": 60})
    with pytest.raises(ValueError, match="built-in"):
        vt.validate_format({"name": "gaming"})


def test_custom_format_round_trip_and_collision(tmp_path):
    custom = {"name": "my_cast", "video_type": "podcast", "min_len": 40,
              "max_len": 150, "caption_style": "podcast", "layout": "single",
              "fit": "cover", "tighten": True, "loudnorm": True,
              "hook_style": "insight", "profile": "talk"}
    saved = vt.save_custom_format(custom, root=tmp_path)
    formats, warnings = vt.load_custom_formats(tmp_path)
    assert warnings == []
    assert formats["my_cast"]["min_len"] == 40
    assert saved["live"] == {}


def test_resolve_prefers_explicit_format_over_detection(tmp_path):
    custom = {"name": "my_cast", "video_type": "podcast", "min_len": 40,
              "max_len": 150, "caption_style": "podcast", "layout": "single",
              "fit": "cover", "tighten": True, "loudnorm": True,
              "hook_style": "insight", "profile": "talk"}
    vt.save_custom_format(custom, root=tmp_path)
    detection = {"type": "gaming", "needs_confirmation": False}
    resolved = vt.resolve_format(detection, "auto", "my_cast", root=tmp_path)
    assert resolved["format"]["name"] == "my_cast"
    assert resolved["source"] == "custom"
    assert resolved["needs_confirmation"] is False


def test_resolve_uncertain_detection_uses_unknown_format():
    detection = {"type": "unknown", "needs_confirmation": True}
    resolved = vt.resolve_format(detection)
    assert resolved["format"]["name"] == "unknown"
    assert resolved["needs_confirmation"] is True


def test_apply_format_respects_explicit_keys_and_aspect():
    options = {"min_len": 15, "max_len": 300, "style": "default",
               "layout": "single", "fit": "cover", "tighten": False,
               "loudnorm": False, "aspect": "16:9"}
    recipe = vt.FORMATS["gaming"]
    applied = vt.apply_format(options, recipe, explicit_keys={"min_len"})
    assert applied["min_len"] == 15
    assert applied["max_len"] == recipe["max_len"]
    assert applied["style"] == "bold"
    assert applied["layout"] == "single"  # stacked needs 9:16
    wide = vt.apply_format({**options, "aspect": "9:16"}, recipe)
    assert wide["layout"] == "stacked"
