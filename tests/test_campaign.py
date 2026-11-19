import json

import campaign


def test_parse_json_response_strips_fences():
    text = 'Sure!\n```json\n{"goal": "test", "must_mention": ["x"]}\n```'
    assert campaign.parse_json_response(text)["goal"] == "test"


def test_detect_provider_override(monkeypatch):
    name, descriptor = campaign.detect_provider(explicit="claude")
    assert name == "claude" and "{prompt}" in descriptor["command"]


def test_detect_provider_custom_command():
    name, descriptor = campaign.detect_provider(command="mytool --ask {prompt}")
    assert name == "custom" and descriptor["command"].endswith("{prompt}")


def test_heuristic_fallback(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no llm")
    monkeypatch.setattr(campaign, "ask_llm", boom)
    rules = campaign.parse_brief(
        "Goal: show camera. Must include: night mode. Avoid: spoilers. 15-30s, 9:16. CTA: link in bio")
    assert rules["source"] == "heuristic"
    assert "night mode" in rules["must_mention"]
    assert rules["aspect"] == "9:16"
    assert rules["min_len"] == 15 and rules["max_len"] == 30
    assert rules["cta"] == "link in bio"


def test_parse_brief_uses_llm(monkeypatch):
    payload = {"goal": "g", "must_mention": ["a"], "banned": [], "min_len": 10,
               "max_len": 40, "aspect": "9:16", "cta": "c", "tone": "t"}
    monkeypatch.setattr(campaign, "detect_provider",
                        lambda *a, **k: ("fake", {"command": "fake {prompt}"}))
    monkeypatch.setattr(campaign, "ask_llm", lambda *a, **k: json.dumps(payload))
    rules = campaign.parse_brief("brief text")
    assert rules["source"] == "llm" and rules["provider"] == "fake"
    assert rules["must_mention"] == ["a"]


def test_save_load_rules(tmp_path):
    rules = campaign.parse_brief("must include: x")
    campaign.save_rules(tmp_path, rules)
    assert campaign.load_rules(tmp_path)["must_mention"] == ["x"]


def test_score_clips_applies_brief_rules(tmp_path, monkeypatch):
    import score_clips
    data = {"video": "x.mp4", "duration": 10,
            "words": [{"word": "night", "start": 0, "end": 1},
                      {"word": "mode", "start": 1, "end": 2}]}
    (tmp_path / "transcript.json").write_text(json.dumps(data))
    rules = campaign._normalize({"banned": ["night"]}, "llm", "fake", "brief")
    campaign.save_rules(tmp_path, rules)
    monkeypatch.setattr(score_clips, "discover", lambda *a, **k: None)
    monkeypatch.setattr(score_clips, "select",
                        lambda *a, **k: ([{"start": 0, "end": 2, "hook": "a", "overall": 8.0}], "heuristic"))
    monkeypatch.setattr(campaign, "detect_provider",
                        lambda *a, **k: ("fake", {"command": "fake {prompt}"}))
    monkeypatch.setattr(campaign, "ask_llm",
                        lambda prompt, *a, **k: '{"scores":[{"index":0,"score":9}]}')
    segments, mode = score_clips.run(tmp_path, min_score=0)
    assert segments == []


def test_brief_provider_none():
    rules = campaign.parse_brief("must include: camera", provider="none")
    assert rules["source"] == "heuristic"
    assert "keyword" in rules["warning"]


def test_brief_failure_reason(monkeypatch):
    monkeypatch.setattr(campaign, "detect_provider", lambda *a, **k: (
        "claude", {"command": "claude -p {prompt}",
                   "unavailable_reason": "claude found but not authenticated or not runnable"}))
    rules = campaign.parse_brief("must include: camera")
    assert rules["source"] == "heuristic"
    assert "claude" in rules["warning"]


def test_detect_provider_reports_unavailable(monkeypatch):
    def no_ollama(*args, **kwargs):
        raise OSError("ollama unavailable")

    class FailedProbe:
        returncode = 1

    monkeypatch.setattr(campaign.urllib.request, "urlopen", no_ollama)
    monkeypatch.setattr(campaign.shutil, "which",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *a, **k: FailedProbe())
    name, descriptor = campaign.detect_provider()
    assert name == "claude"
    assert "not authenticated" in descriptor["unavailable_reason"]


def test_apply_rules_filters_and_scores(tmp_path, monkeypatch):
    words = [
        {"word": "night", "start": 0, "end": 0.5},
        {"word": "mode", "start": 0.5, "end": 1},
        {"word": "spoiler", "start": 1, "end": 1.5},
    ]
    (tmp_path / "transcript.json").write_text(
        json.dumps({"video": "x.mp4", "duration": 2, "words": words}))
    rules = campaign._normalize(
        {"goal": "g", "must_mention": ["night mode"], "banned": ["spoiler"], "cta": ""},
        "llm", "fake", "brief")
    monkeypatch.setattr(campaign, "detect_provider",
                        lambda *a, **k: ("fake", {"command": "fake {prompt}"}))
    monkeypatch.setattr(campaign, "ask_llm",
                        lambda prompt, *a, **k: '{"scores":[{"index":0,"score":9,"reason":"ok"}]}')
    segments = [{"start": 0, "end": 1, "hook": "a"},
                {"start": 1, "end": 2, "hook": "b"}]
    kept, scores = campaign.apply_rules(segments, rules, tmp_path)
    assert len(kept) == 1 and kept[0]["hook"] == "a"
    assert scores[0]["score"] == 9
