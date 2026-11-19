import pytest

import live


def _bare_session(tmp_path):
    from live_capture import LiveSource
    session = live.LiveSession.__new__(live.LiveSession)
    session.id = "abcdef123456"
    session.dir = tmp_path
    session.options = {"live_threshold": 8.0, "live_cooldown": 60,
                       "live_max_clips": 2, "live_window": 10}
    session.source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session.last_trigger = None
    session.records = []
    session.errors = []
    session.recent_scores = []
    session.stream_time = 0.0
    session.stopped_at = None
    session.status = "running"
    session.started_at = 1000.0
    return session


def test_live_session_parses_brief(tmp_path, monkeypatch):
    import campaign
    import live
    from live_capture import LiveSource

    monkeypatch.setattr(campaign, "parse_brief",
                        lambda text, *a, **k: {"must_mention": ["night mode"], "banned": []})
    source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, {}, tmp_path, brief_text="show night mode")
    assert session.rules["must_mention"] == ["night mode"]
    assert (session.dir / "brief_rules.json").exists()


def test_trigger_conditions(tmp_path):
    session = _bare_session(tmp_path)
    assert not session._allowed(7.9, 100.0)
    assert session._allowed(8.0, 100.0)
    session.last_trigger = 100.0
    assert not session._allowed(9.0, 120.0)  # cooldown
    session.records.extend([object(), object()])
    assert not session._allowed(9.0, 300.0)  # max clips


def test_state_round_trip(tmp_path):
    session = _bare_session(tmp_path)
    session._save()
    data = live.projects.read_json(tmp_path / "live_session.json")
    assert data["status"] == "running"
    assert data["started_at"] == 1000.0
    assert data["source"]["platform"] == "generic"
