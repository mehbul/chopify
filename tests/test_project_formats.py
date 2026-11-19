"""Tests for video-type option plumbing in the project pipeline."""
import pytest

import projects


def test_new_options_defaults():
    options = projects.validate_options({})
    assert options["video_type"] == "auto"
    assert options["format"] is None
    assert options["video_type_model"] is None


def test_validate_options_rejects_bad_type_and_format():
    with pytest.raises(ValueError, match="video type"):
        projects.validate_options({"video_type": "cartoon"})
    with pytest.raises(ValueError, match="format"):
        projects.validate_options({"format": 7})


def test_score_keys_change_with_type_and_format():
    base = projects.validate_options({})
    data = {"words": [{"text": "hi"}], "duration": 10}
    first, _ = projects.score_keys("transcript", data, base)
    second, _ = projects.score_keys("transcript", data, {**base, "video_type": "gaming"})
    third, _ = projects.score_keys("transcript", data, {**base, "format": "movie"})
    assert len({first, second, third}) == 3


def test_apply_detection_persists_choice_and_applies_recipe():
    state = {"cache": {}, "options": {}}
    options = projects.validate_options({})
    detection = {"type": "gaming", "confidence": 0.9,
                 "needs_confirmation": False, "signals": {}, "reason": "test"}
    updated_options, updated_state = projects.apply_detection(
        "work", state, options, detection, explicit_keys={"min_len"})
    assert updated_state["detection"] == detection
    assert updated_state["format"]["name"] == "gaming"
    assert updated_state["format"]["source"] == "detected"
    assert updated_state["format_recipe"]["profile"] == "gameplay/action"
    assert updated_options["style"] == "bold"
    assert updated_options["min_len"] == options["min_len"]  # explicit wins
    assert updated_options["max_len"] == 60


def test_apply_detection_honours_user_choice_from_callback():
    state = {"cache": {}, "options": {}}
    options = projects.validate_options({})
    detection = {"type": "unknown", "confidence": 0.1,
                 "needs_confirmation": True, "signals": {}, "reason": "test"}
    updated_options, updated_state = projects.apply_detection(
        "work", state, options, detection,
        on_detection=lambda d, r: {"format": "podcast"})
    assert updated_state["format"]["name"] == "podcast"
    assert updated_state["format"]["source"] == "override"
    assert updated_options["style"] == "podcast"
