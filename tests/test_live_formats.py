"""Tests for live sessions using detected clipping formats."""
import live
import projects


def test_run_live_applies_format_to_options(monkeypatch, tmp_path):
    captured = {}

    class FakeSession:
        def __init__(self, source, options, root, brief_text=None, session_id=None,
                     detection=None, format_info=None):
            captured["options"] = options
            captured["detection"] = detection
            captured["format"] = format_info
            captured["root"] = root

        def start(self):
            pass

    monkeypatch.setattr(live.live_capture, "resolve_live_source",
                        lambda url, options: type("Source", (), {
                            "url": url, "kind": "hls", "platform": "twitch",
                            "resolved_by": "test"})())
    monkeypatch.setattr(live, "LiveSession", FakeSession)
    options = projects.validate_options({"live_threshold": 9.5})
    live.run_live("https://twitch.tv/example", options, tmp_path,
                  explicit_keys={"live_threshold"})
    assert captured["options"]["live_threshold"] == 9.5  # explicit wins
    assert captured["options"]["style"] == "bold"
    assert captured["format"]["name"] == "livestream"
    assert captured["detection"]["type"] == "livestream"
