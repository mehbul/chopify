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
