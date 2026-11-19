"""Tests for video-type CLI flags and the interactive format builder."""
import chopify
import video_types


def test_callback_silent_when_confident(capsys):
    detection = {"type": "podcast", "confidence": 0.91,
                 "needs_confirmation": False, "signals": {}, "reason": "x"}
    resolved = {"format": video_types.FORMATS["podcast"],
                "source": "detected", "needs_confirmation": False}
    assert chopify.detection_callback(detection, resolved) is None
    out = capsys.readouterr().out
    assert "Detected: podcast" in out
    assert "91%" in out


def test_build_new_format_interactive_with_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(video_types, "FORMATS_ROOT", tmp_path)
    answers = iter(["my_cast", "45", "150", "podcast", "single",
                    "cover", "y", "n"])

    def fake_input(prompt):
        return next(answers)

    recipe = chopify.build_new_format_interactive(
        video_types.FORMATS["podcast"], input_fn=fake_input)
    assert recipe["name"] == "my_cast"
    assert recipe["min_len"] == 45
    assert recipe["max_len"] == 150
    assert recipe["caption_style"] == "podcast"


def test_build_new_format_interactive_invalid_name_returns_none():
    answers = iter(["Bad Name"])

    def fake_input(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    assert chopify.build_new_format_interactive(
        video_types.FORMATS["podcast"], input_fn=fake_input) is None
