"""Tests for Studio video-type and format API surface."""
import dashboard


def test_config_payload_exposes_types_and_formats():
    payload = dashboard.config_payload(None)
    assert "podcast" in payload["video_types"]
    assert "gaming" in payload["formats"]
    assert payload["formats"]["gaming"]["profile"] == "gameplay/action"


def test_save_format_route(tmp_path):
    saved = dashboard.save_format({
        "name": "talk_short", "video_type": "podcast", "min_len": 30,
        "max_len": 90, "profile": "talk", "caption_style": "podcast",
        "layout": "single", "fit": "cover", "tighten": True,
        "loudnorm": True, "hook_style": "insight"}, root=tmp_path)
    assert saved["name"] == "talk_short"
    assert (tmp_path / "talk_short.json").exists()
