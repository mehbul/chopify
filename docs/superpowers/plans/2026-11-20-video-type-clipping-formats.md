# Video-type detection and clipping formats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect a source's video type (podcast, gaming, movie, livestream, interview, tutorial, music, unknown) and use it to select — or let the user build — a reusable clipping format that drives the whole job across CLI, Studio, live, and agent surfaces.

**Architecture:** A new `video_types.py` sits on top of the existing local evidence signals (`evidence.content_profile`, audio timeline, visual baseline). It classifies, resolves a built-in or custom JSON format, and supplies values for options that already exist. `projects.run_project` runs detection after transcription; `score_clips.discover` accepts the format's profile/band; CLI/Studio/live pass the decision through. Explicit user flags always win over format values. Non-interactive runs never block.

**Tech Stack:** Python 3.11+, stdlib (`json`, `re`, `urllib.request`), existing OpenCV/ffmpeg evidence, optional local Ollama over HTTP. No new dependencies.

## Global Constraints

- Python 3.11+; Windows primary target; Linux CI also runs.
- No new third-party dependency, no cloud service, no API key, no downloaded video-understanding model.
- Named types are inferred from local signals; confidence + explicit override are the contract. Scores stay editing suggestions, not virality predictions.
- Precedence: explicit user flag > custom format > built-in format > default. `--no-input`, batch, and agent runs never prompt.
- Existing HTML structure, element IDs, API routes, and behavior must keep working; this change adds, not replaces.
- All new API routes keep the loopback/token/CSRF guards.
- Full suite baseline is 373 passing tests; keep it green. `node --check assets/studio/app.js` must stay clean.

---

### Task 1: `video_types.py` — classifier, formats, resolution, LLM tie-break

**Files:**
- Create: `video_types.py`
- Test: `tests/test_video_types.py`

**Interfaces:**
- Consumes: `evidence.clamp`, `evidence.PROFILE_FLOORS`, `render_clips.CAPTION_STYLES`.
- Produces: `VIDEO_TYPES`, `FORMATS`, `FORMATS_ROOT`, `signals_from_measurements(...)`, `classify(signals, llm=None, transcript_sample="")`, `llm_classify(signals, transcript_sample, model, host)`, `validate_format(data)`, `load_custom_formats(root=None) -> (dict, warnings)`, `save_custom_format(data, root=None)`, `resolve_format(detection, requested_type=None, requested_format=None, root=None)`, `apply_format(options, recipe, explicit_keys=None)`, `detect_from_transcript(data, source, workdir, options)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_video_types.py
import json

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


def test_llm_tie_break_overrides_uncertain_result(monkeypatch):
    called = []

    def fake_llm(sig, sample):
        called.append(sample)
        return "movie"

    result = vt.classify(signals(), llm=fake_llm, transcript_sample="once upon a time")
    assert result["type"] == "movie"
    assert result["needs_confirmation"] is False
    assert called == ["once upon a time"]


def test_bad_llm_label_keeps_heuristic():
    result = vt.classify(signals(speech=0.9, audio=0.1), llm=lambda *_: "not-a-type")
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_video_types.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'video_types'`.

- [ ] **Step 3: Implement `video_types.py`**

```python
"""Local video-type detection and reusable clipping formats.

No cloud, no API keys, no new dependencies. Named types are inferred from
local audio/visual/transcript/metadata signals; confidence and an explicit
override are the contract when a source is ambiguous.
"""
import json
import re
import urllib.request
from pathlib import Path

import evidence
import render_clips

FORMATS_ROOT = Path(__file__).resolve().parent / "formats"
VIDEO_TYPES = ("podcast", "gaming", "movie", "livestream",
               "interview", "tutorial", "music", "unknown")
CONFIDENCE_MIN = 0.35
MARGIN_MIN = 0.15
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
STREAM_HOSTS = ("twitch.tv", "youtube.com/live", "kick.com", "trovo.live")
STREAM_WORDS = ("stream", "live", "vod", "twitch", "kick")
LIVE_KEYS = {"live_threshold", "live_pre_roll", "live_tail",
             "live_cooldown", "live_window"}

FORMATS = {
    "podcast": {"name": "podcast", "video_type": "podcast", "min_len": 45,
                "max_len": 180, "profile": "talk", "caption_style": "podcast",
                "layout": "single", "fit": "cover", "tighten": True,
                "loudnorm": True, "hook_style": "insight", "live": {}},
    "interview": {"name": "interview", "video_type": "interview", "min_len": 45,
                  "max_len": 180, "profile": "talk", "caption_style": "podcast",
                  "layout": "single", "fit": "cover", "tighten": True,
                  "loudnorm": True, "hook_style": "insight", "live": {}},
    "gaming": {"name": "gaming", "video_type": "gaming", "min_len": 15,
               "max_len": 60, "profile": "gameplay/action", "caption_style": "bold",
               "layout": "stacked", "fit": "cover", "tighten": False,
               "loudnorm": False, "hook_style": "event",
               "live": {"live_threshold": 7.5, "live_pre_roll": 30, "live_tail": 8}},
    "livestream": {"name": "livestream", "video_type": "livestream", "min_len": 15,
                   "max_len": 90, "profile": "gameplay/action", "caption_style": "bold",
                   "layout": "single", "fit": "cover", "tighten": False,
                   "loudnorm": False, "hook_style": "event",
                   "live": {"live_threshold": 7.5, "live_pre_roll": 30,
                            "live_tail": 8, "live_cooldown": 45}},
    "movie": {"name": "movie", "video_type": "movie", "min_len": 60,
              "max_len": 300, "profile": "mixed", "caption_style": "minimal",
              "layout": "single", "fit": "contain", "tighten": False,
              "loudnorm": False, "hook_style": "scene", "live": {}},
    "tutorial": {"name": "tutorial", "video_type": "tutorial", "min_len": 30,
                 "max_len": 180, "profile": "mixed", "caption_style": "clean",
                 "layout": "single", "fit": "cover", "tighten": False,
                 "loudnorm": True, "hook_style": "step", "live": {}},
    "music": {"name": "music", "video_type": "music", "min_len": 15,
              "max_len": 90, "profile": "mixed", "caption_style": "neon",
              "layout": "single", "fit": "cover", "tighten": False,
              "loudnorm": True, "hook_style": "beat", "live": {}},
    "unknown": {"name": "unknown", "video_type": "unknown", "min_len": 30,
                "max_len": 120, "profile": "mixed", "caption_style": "default",
                "layout": "single", "fit": "cover", "tighten": False,
                "loudnorm": False, "hook_style": "moment", "live": {}},
}


def _clamp01(value):
    try:
        return evidence.clamp(float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def signals_from_measurements(speech_density=0.0, audio_variability=0.0,
                              burst_rate=0.0, visual_value=None, cut_rate=0.0,
                              facecam_norm=0.0, duration=0.0, source=""):
    """Turn already-computed local measurements into normalized signals."""
    text = str(source or "").lower()
    streamish = any(host in text for host in STREAM_HOSTS) or any(
        re.search(r"\b" + word + r"\b", text) for word in STREAM_WORDS)
    return {
        "speech": _clamp01(float(speech_density or 0.0) / 3.0),
        "audio": _clamp01(audio_variability),
        "burst": _clamp01(float(burst_rate or 0.0) / 0.3),
        "visual": _clamp01(visual_value),
        "cuts": _clamp01(float(cut_rate or 0.0) / 20.0),
        "facecam": _clamp01(facecam_norm),
        "long": _clamp01(float(duration or 0.0) / 3600.0),
        "streamish": 1.0 if streamish else 0.0,
    }


def classify(signals, llm=None, transcript_sample=""):
    """Return a detection dict. ``llm`` is an optional callable(signals, sample)."""
    sig = {key: _clamp01(value) for key, value in (signals or {}).items()}
    quiet = 1.0 - sig.get("audio", 0.0)
    stable = max(0.0, 1.0 - 1.5 * sig.get("cuts", 0.0))
    speech = sig.get("speech", 0.0)
    scores = {
        "podcast": 0.55 * speech + 0.25 * quiet + 0.20 * stable + 0.10 * sig.get("long", 0.0),
        "interview": 0.45 * speech + 0.30 * sig.get("facecam", 0.0) + 0.20 * stable,
        "tutorial": 0.40 * speech + 0.30 * sig.get("cuts", 0.0)
                    + 0.20 * (1.0 - sig.get("visual", 0.0)) + 0.10 * sig.get("long", 0.0),
        "gaming": 0.35 * sig.get("cuts", 0.0) + 0.30 * sig.get("burst", 0.0)
                  + 0.20 * sig.get("facecam", 0.0) + 0.15 * (1.0 - speech)
                  + 0.25 * sig.get("streamish", 0.0),
        "movie": 0.50 * stable + 0.30 * (1.0 - speech) + 0.20 * sig.get("long", 0.0)
                 + 0.10 * (1.0 - sig.get("facecam", 0.0)),
        "music": 0.50 * sig.get("burst", 0.0) + 0.30 * sig.get("audio", 0.0)
                 + 0.20 * (1.0 - speech),
        "livestream": 0.60 * sig.get("streamish", 0.0) + 0.25 * sig.get("long", 0.0)
                      + 0.15 * sig.get("facecam", 0.0),
    }
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    name, top = ordered[0]
    runner, second = ordered[1]
    if top < 0.15:
        name = "unknown"
    margin = top - second
    result = {
        "type": name,
        "confidence": round(top, 3),
        "margin": round(margin, 3),
        "signals": sig,
        "reason": f"top={name} ({top:.2f}), runner-up={runner} ({second:.2f})",
        "needs_confirmation": top < CONFIDENCE_MIN or margin < MARGIN_MIN,
    }
    if llm is not None and result["needs_confirmation"]:
        try:
            label = str(llm(sig, transcript_sample) or "").strip().lower()
        except Exception as exc:  # noqa: BLE001 - LLM is strictly optional
            result["reason"] += f"; LLM unavailable ({exc})"
        else:
            if label in VIDEO_TYPES and label != "unknown":
                result.update(type=label, confidence=max(top, 0.6),
                              needs_confirmation=False,
                              reason=result["reason"] + f"; LLM selected {label}")
            else:
                result["reason"] += f"; rejected LLM label {label!r}"
    return result


def llm_classify(signals, transcript_sample, model, host="http://localhost:11434"):
    """Ask a local Ollama chat model for a type label. Return None on any failure."""
    prompt = ("Pick exactly one video type from "
              + ", ".join(VIDEO_TYPES) + ". Reply with only that word.\n"
              + "Signals: " + json.dumps(signals, sort_keys=True) + "\n"
              + "Transcript sample: " + str(transcript_sample)[:1200])
    body = json.dumps({"model": model, "stream": False,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(
        host.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    label = str(payload.get("message", {}).get("content", "")).strip().lower()
    return label if label in VIDEO_TYPES else None


def validate_format(data):
    """Validate and normalize a custom format recipe."""
    if not isinstance(data, dict):
        raise ValueError("Format must be a JSON object.")
    name = str(data.get("name") or "").strip()
    if not SLUG.fullmatch(name):
        raise ValueError("Format name must be lowercase letters, digits, '-' or '_' (max 40).")
    if name in FORMATS:
        raise ValueError(f"'{name}' is a built-in format name.")
    result = dict(FORMATS["unknown"])
    for key in ("min_len", "max_len"):
        value = data.get(key, result[key])
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 3600:
            raise ValueError(f"{key} must be an integer from 5 to 3600.")
        result[key] = value
    if result["min_len"] > result["max_len"]:
        raise ValueError("min_len cannot exceed max_len.")
    value = data.get("video_type", result["video_type"])
    if value not in VIDEO_TYPES:
        raise ValueError(f"Unknown video_type: {value!r}.")
    result["video_type"] = value
    value = data.get("profile", result["profile"])
    if value not in evidence.PROFILE_FLOORS:
        raise ValueError(f"Unknown profile: {value!r}.")
    result["profile"] = value
    value = data.get("caption_style", result["caption_style"])
    if value not in render_clips.CAPTION_STYLES:
        raise ValueError(f"Unknown caption_style: {value!r}.")
    result["caption_style"] = value
    for key, allowed in (("layout", {"single", "split", "stacked"}),
                         ("fit", {"cover", "contain"})):
        value = data.get(key, result[key])
        if value not in allowed:
            raise ValueError(f"Unknown {key}: {value!r}.")
        result[key] = value
    for key in ("tighten", "loudnorm"):
        value = data.get(key, result[key])
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false.")
        result[key] = value
    for key in ("hook_style", "description"):
        result[key] = str(data.get(key, result.get(key, "")))[:120]
    live = data.get("live") or {}
    if not isinstance(live, dict):
        raise ValueError("live must be an object.")
    result["live"] = {}
    for key, value in live.items():
        if key not in LIVE_KEYS:
            raise ValueError(f"Unknown live setting: {key}.")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number.")
        result["live"][key] = value
    result["name"] = name
    return result


def load_custom_formats(root=None):
    root = Path(root or FORMATS_ROOT)
    formats, warnings = {}, []
    if not root.is_dir():
        return formats, warnings
    for path in sorted(root.glob("*.json")):
        try:
            recipe = validate_format(json.loads(path.read_text(encoding="utf-8-sig")))
            formats[recipe["name"]] = recipe
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"Skipped {path.name}: {exc}")
    return formats, warnings


def save_custom_format(data, root=None):
    recipe = validate_format(data)
    root = Path(root or FORMATS_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{recipe['name']}.json"
    path.write_text(json.dumps(recipe, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return recipe


def resolve_format(detection, requested_type=None, requested_format=None, root=None):
    custom, warnings = load_custom_formats(root)
    detection = detection or {}
    detected = detection.get("type", "unknown")
    needs = bool(detection.get("needs_confirmation", True))
    if requested_format:
        if requested_format in custom:
            recipe, source = custom[requested_format], "custom"
        elif requested_format in FORMATS:
            recipe, source = FORMATS[requested_format], "override"
        else:
            path = Path(requested_format)
            if not path.is_file():
                raise ValueError(f"Unknown format: {requested_format}.")
            recipe = validate_format(json.loads(path.read_text(encoding="utf-8-sig")))
            source = "custom"
        needs = False
    elif requested_type and requested_type != "auto":
        if requested_type not in VIDEO_TYPES:
            raise ValueError(f"Unknown video type: {requested_type}.")
        recipe, source, needs = FORMATS[requested_type], "override", False
    else:
        key = detected if detected in FORMATS else "unknown"
        recipe, source = FORMATS[key], "detected"
        needs = needs or key == "unknown"
    return {"format": recipe, "source": source,
            "needs_confirmation": needs, "warnings": warnings}


def apply_format(options, recipe, explicit_keys=None):
    """Return options with format values applied; explicit keys are untouched."""
    explicit = set(explicit_keys or ())
    result = dict(options)
    mapping = {"min_len": "min_len", "max_len": "max_len", "style": "caption_style",
               "layout": "layout", "fit": "fit", "tighten": "tighten",
               "loudnorm": "loudnorm"}
    for option_key, recipe_key in mapping.items():
        if option_key in explicit or recipe.get(recipe_key) is None:
            continue
        result[option_key] = recipe[recipe_key]
    if (result.get("aspect") != "9:16" and result.get("layout") in {"split", "stacked"}
            and "layout" not in explicit):
        result["layout"] = "single"
    for key in LIVE_KEYS:
        if key in recipe.get("live", {}) and key not in explicit:
            result[key] = recipe["live"][key]
    return result


def detect_from_transcript(data, source, workdir, options):
    """Classify from cached audio/visual measurements plus transcript metadata."""
    import visual

    words = data.get("words") or []
    timeline = evidence.load_audio_timeline(source, workdir)
    duration = float(data.get("duration")
                     or getattr(timeline, "duration", 0) or 0)
    audio_usable = evidence.timeline_has_signal(timeline)
    baseline = visual.sample_baseline(source, workdir, duration)
    sig = signals_from_measurements(
        speech_density=len(words) / duration if duration else 0.0,
        audio_variability=timeline.variability if audio_usable else 0.0,
        burst_rate=timeline.burst_rate if audio_usable else 0.0,
        visual_value=baseline.value if baseline is not None else None,
        cut_rate=baseline.cut_rate if baseline is not None else 0.0,
        facecam_norm=baseline.facecam_norm if baseline is not None else 0.0,
        duration=duration,
        source=data.get("source") or source or "")
    llm = None
    model = (options or {}).get("video_type_model")
    if model:
        sample = " ".join(str(w.get("text", "")) for w in words[:120])

        def llm_call(signals, _sample, _model=model, _text=sample):
            return llm_classify(signals, _text, _model)

        llm = llm_call
    return classify(sig, llm=llm)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_video_types.py -q`
Expected: all 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add video_types.py tests/test_video_types.py
git commit -m "feat(video-types): local classifier, formats and resolution"
```

---

### Task 2: Project options, detection stage, and cache keys

**Files:**
- Modify: `projects.py` (imports near line 16, `DEFAULT_OPTIONS` line 27, `validate_options` line 59, `score_keys` line 153, `run_project` line 237)
- Test: `tests/test_project_formats.py`

**Interfaces:**
- Consumes: `video_types.VIDEO_TYPES`, `video_types.detect_from_transcript`, `video_types.resolve_format`, `video_types.apply_format`.
- Produces: `projects.apply_detection(path, state, options, detection, on_detection=None, explicit_keys=None) -> (options, state)`; new options `video_type`, `format`, `video_type_model`; `run_project(..., on_detection=None, explicit_keys=None)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_project_formats.py
import pytest

import projects
import video_types


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_project_formats.py -q`
Expected: FAIL — `KeyError: 'video_type'` / `AttributeError: apply_detection`.

- [ ] **Step 3: Add options, cache keys, and the detection helper**

In `projects.py`, add `import video_types` after the existing imports (line 18 area):

```python
import video_types
```

In `DEFAULT_OPTIONS` (line 27), add to the dict:

```python
                    "video_type": "auto", "format": None, "video_type_model": None,
```

In `validate_options`, after the layout/facecam validation block, add:

```python
    video_type = result["video_type"]
    if video_type != "auto" and video_type not in video_types.VIDEO_TYPES:
        raise ValueError(f"Unknown video type: {video_type}.")
    fmt = result["format"]
    if fmt is not None and (not isinstance(fmt, str) or not fmt.strip()):
        raise ValueError("format must be a format name or a JSON path.")
    model = result["video_type_model"]
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("video_type_model must be a model name or None.")
```

In `score_keys`, change `discovery_options` to:

```python
    discovery_options = {key: options[key] for key in (
        "search", "semantic", "embed_model", "min_len", "max_len",
        "preferred_len", "video_type", "format", "video_type_model")}
```

Add this new function immediately after `score_keys`:

```python
def apply_detection(path, state, options, detection, on_detection=None,
                    explicit_keys=None):
    """Resolve a format for the detection and return updated options and state."""
    resolved = video_types.resolve_format(
        detection, options.get("video_type"), options.get("format"))
    if on_detection is not None:
        choice = on_detection(detection, resolved) or {}
        if choice.get("type"):
            options = {**options, "video_type": choice["type"]}
        if choice.get("format"):
            options = {**options, "format": choice["format"]}
        if choice:
            resolved = video_types.resolve_format(
                detection, options.get("video_type"), options.get("format"))
    options = video_types.apply_format(options, resolved["format"],
                                       explicit_keys or set())
    state = {
        **state,
        "detection": detection,
        "format_recipe": resolved["format"],
        "format": {
            "name": resolved["format"]["name"],
            "source": resolved["source"],
            "needs_confirmation": resolved["needs_confirmation"],
        },
    }
    return options, state
```

- [ ] **Step 4: Wire detection into `run_project`**

Change the signature (line 237) to:

```python
def run_project(path, action="all", preview=False, pick=None, resume=True,
                progress=None, on_detection=None, explicit_keys=None):
```

Replace the block between `stage("discover")` and the existing `data = read_json(transcript_path)` / `discovery_key...` lines with:

```python
            stage("detect")
            data = read_json(transcript_path)
            detection = video_types.detect_from_transcript(
                data, state.get("video"), path, options)
            options, state = apply_detection(
                path, state, options, detection, on_detection, explicit_keys)
            save_project(path, state)
            recipe = state["format_recipe"]
            discovery_key, selection_key = score_keys(transcript_key, data, options)
```

(Keep `candidates_path`, `selection_path`, `segments_path` and all following code unchanged.)

Replace the `score_clips.discover(...)` call (around line 414) with:

```python
                    score_clips.discover(
                        path, min_len=options["min_len"], max_len=options["max_len"],
                        preferred_len=options["preferred_len"],
                        profile=recipe.get("profile"),
                        band=(recipe["min_len"], recipe["max_len"]),
                        detection=state.get("detection"),
                        format_info=state.get("format"))
```

`run_batch` already forwards `**kwargs` to `run_project`, so no change is needed there.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_project_formats.py -q`
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add projects.py tests/test_project_formats.py
git commit -m "feat(projects): detect video type and apply format per project"
```

---

### Task 3: `score_clips.discover` accepts format band/profile and records decision

**Files:**
- Modify: `score_clips.py` (`discover` definition line 1502, body lines 1508-1576)
- Test: `tests/test_score_formats.py`

**Interfaces:**
- Consumes: `video_types` via `projects.apply_detection` (no direct import needed here).
- Produces: `score_clips.discover(..., band=None, profile=None, detection=None, format_info=None)`; stats keys `video_type`, `format`, `format_source`, `detection`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_score_formats.py
import json

import pytest

import score_clips


def test_discover_records_detection_and_honours_profile(monkeypatch, tmp_path):
    import evidence

    seen = {}

    def fake_source_evidence(workdir, data):
        class Timeline:
            duration = 10.0
            variability = 0.5
            burst_rate = 0.0
        return Timeline(), "talk", 0.5, 0.5, True, None

    def fake_save(workdir, candidates, stats, mode=None):
        seen["stats"] = stats

    monkeypatch.setattr(score_clips, "source_evidence", fake_source_evidence)
    monkeypatch.setattr(score_clips, "load_transcript", lambda w: {
        "video": "x.mp4", "words": [{"text": "hello world", "start": 0, "end": 1}],
        "duration": 10})
    monkeypatch.setattr(score_clips, "sentenceize", lambda words: [
        {"text": "hello world", "start": 0, "end": 1, "words": words}])
    monkeypatch.setattr(score_clips, "attach_audio_evidence", lambda c, t, p, e: c)
    monkeypatch.setattr(score_clips, "visual_shortlist", lambda *a: [])
    monkeypatch.setattr(score_clips.visual, "merge_ranges", lambda *a: [])
    monkeypatch.setattr(score_clips.visual, "load_or_analyze", lambda *a: [])
    monkeypatch.setattr(score_clips, "attach_media_evidence",
                        lambda c, s, t, p, e, au, mn, mx: c)
    monkeypatch.setattr(score_clips, "save_candidates", fake_save)

    detection = {"type": "gaming", "confidence": 0.9,
                 "needs_confirmation": False, "signals": {}, "reason": "test"}
    format_info = {"name": "gaming", "source": "detected"}
    score_clips.discover(tmp_path, band=(15, 60), profile="gameplay/action",
                         detection=detection, format_info=format_info)
    stats = seen["stats"]
    assert stats["content_profile"] == "gameplay/action"
    assert stats["video_type"] == "gaming"
    assert stats["format"] == "gaming"
    assert stats["format_source"] == "detected"
    assert stats["detection"]["type"] == "gaming"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_score_formats.py -q`
Expected: FAIL — `TypeError: discover() got an unexpected keyword argument 'band'` is not enough because `band` already exists; the failure is `unexpected keyword argument 'profile'`.

- [ ] **Step 3: Modify `discover`**

Change the signature (line 1502):

```python
def discover(workdir, min_len=15, max_len=300, preferred_len=None,
             band=None, progress=None, profile=None, detection=None,
             format_info=None):
```

In the body, right after `source_evidence(...)` returns the tuple, add:

```python
    if profile in evidence.PROFILE_FLOORS:
        detected_profile = profile
    profile = detected_profile
```

Replace `effective_band = (evidence.profile_band(profile) if band is None else band)` with:

```python
    effective_band = band if band is not None else evidence.profile_band(profile)
```

In the `stats.update({...})` block (around line 1556), add these keys:

```python
        "video_type": (detection or {}).get("type", "unknown"),
        "format": (format_info or {}).get("name"),
        "format_source": (format_info or {}).get("source"),
        "detection": detection,
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_score_formats.py -q`
Expected: PASS.

- [ ] **Step 5: Full regression and commit**

Run: `pytest tests/ -q`
Expected: 373+ tests PASS (existing suite unchanged in behavior; the new keys are additive).

```bash
git add score_clips.py tests/test_score_formats.py
git commit -m "feat(score): let formats set profile and band, record detection"
```

---

### Task 4: CLI flags, `--list-formats`, and interactive "build new format"

**Files:**
- Modify: `chopify.py` (argparse near lines 182-266, options near line 330, `run_project`/`run_batch` calls at lines 345, 386, 409, 412)
- Test: `tests/test_cli_formats.py`

**Interfaces:**
- Consumes: `video_types.FORMATS`, `video_types.load_custom_formats`, `video_types.save_custom_format`, `video_types.signals_from_measurements`.
- Produces: `chopify.detection_callback(detection, resolved)`, `chopify.build_new_format_interactive(base, input_fn=input) -> dict | None`, CLI flags `--video-type`, `--format`, `--video-type-model`, `--list-formats`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_formats.py
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


def test_build_new_format_interactive_with_defaults():
    answers = iter(["my_cast", "45", "150", "podcast", "single"])

    def fake_input(prompt):
        return next(answers)

    recipe = chopify.build_new_format_interactive(
        video_types.FORMATS["podcast"], input_fn=fake_input)
    assert recipe["name"] == "my_cast"
    assert recipe["min_len"] == 45
    assert recipe["max_len"] == 150
    assert recipe["caption_style"] == "podcast"


def test_build_new_format_interactive_invalid_name_returns_none():
    def fake_input(prompt):
        return "Bad Name"  # slug validation fails, then EOF path below

    assert chopify.build_new_format_interactive(
        video_types.FORMATS["podcast"], input_fn=fake_input) is None
```

Wait — the last test needs the loop to stop after an invalid name. Use an iterator that raises `EOFError` on the second call:

```python
def test_build_new_format_interactive_invalid_name_returns_none():
    answers = iter(["Bad Name"])

    def fake_input(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    assert chopify.build_new_format_interactive(
        video_types.FORMATS["podcast"], input_fn=fake_input) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli_formats.py -q`
Expected: FAIL — `AttributeError: module 'chopify' has no attribute 'detection_callback'`.

- [ ] **Step 3: Add helpers to `chopify.py`**

Add `import video_types` after the existing imports (line 29 area). Add these functions before `def main()`:

```python
def detection_callback(detection, resolved):
    """Human-facing detection notice; prompt only when the match is uncertain."""
    fmt = resolved["format"]
    print(f"Detected: {detection.get('type', 'unknown')} "
          f"({(detection.get('confidence') or 0) * 100:.0f}%) "
          f"\u00b7 Format: {fmt['name']}", flush=True)
    if not resolved.get("needs_confirmation"):
        return None
    print("No confident type match. 1) use suggested  2) pick a format  "
          "3) build a new format", flush=True)
    while True:
        try:
            choice = input("Choose [1/2/3]: ").strip()
        except EOFError:
            return None
        if choice == "1":
            return None
        if choice == "2":
            custom, _ = video_types.load_custom_formats()
            names = sorted({**video_types.FORMATS, **custom})
            print("Formats: " + ", ".join(names), flush=True)
            try:
                name = input("Format name: ").strip()
            except EOFError:
                return None
            if name in names:
                return {"format": name}
            print("Unknown format name.", flush=True)
            continue
        if choice == "3":
            return build_new_format_interactive(fmt)
        print("Enter 1, 2 or 3.", flush=True)


def build_new_format_interactive(base, input_fn=input):
    """Guided format builder with defaults from the detected type."""
    while True:
        try:
            name = input_fn("New format name (lowercase): ").strip()
        except EOFError:
            return None
        if not __import__("re").fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name):
            print("Use lowercase letters, digits, '-' or '_'.", flush=True)
            continue
        if name in video_types.FORMATS:
            print("That name is reserved for a built-in format.", flush=True)
            continue
        break
    data = dict(base)
    data["name"] = name
    for key, label, cast in (
            ("min_len", "Minimum clip seconds", int),
            ("max_len", "Maximum clip seconds", int),
            ("caption_style", "Caption style", str),
            ("layout", "Layout (single/split/stacked)", str),
            ("fit", "Fit (cover/contain)", str)):
        try:
            answer = input_fn(f"{label} [{base.get(key, '')}]: ").strip()
        except EOFError:
            return None
        if answer:
            try:
                data[key] = cast(answer)
            except ValueError:
                print("Invalid value; keeping the default.", flush=True)
    for key in ("tighten", "loudnorm"):
        try:
            answer = input_fn(f"{key} [{'y' if base.get(key) else 'n'}]: ").strip().lower()
        except EOFError:
            return None
        if answer in {"y", "yes", "n", "no"}:
            data[key] = answer in {"y", "yes"}
    try:
        return video_types.save_custom_format(data)
    except ValueError as exc:
        print(f"Could not save format: {exc}", flush=True)
        return None
```

- [ ] **Step 4: Add CLI flags and wire the callback**

Add to `argparse` setup (near line 196):

```python
    ap.add_argument("--video-type", dest="video_type", default=None,
                    choices=["auto", *video_types.VIDEO_TYPES],
                    help="video type override, or auto (default) to detect locally")
    ap.add_argument("--format", dest="format", default=None,
                    help="built-in or custom clipping format name, or a JSON path")
    ap.add_argument("--video-type-model", default=None, metavar="MODEL",
                    help="optional local Ollama model to resolve uncertain detections")
    ap.add_argument("--list-formats", action="store_true",
                    help="print built-in and custom format names and exit")
```

After `args = ap.parse_args()` (line 267) add:

```python
    if args.list_formats:
        custom, _ = video_types.load_custom_formats()
        for name in sorted({**video_types.FORMATS, **custom}):
            print(name)
        return
```

After `options = {k: getattr(args, k, None) ...}` (line 330) add:

```python
    if options.get("video_type") is None:
        options["video_type"] = "auto"
    explicit_keys = {key for key in projects.DEFAULT_OPTIONS
                     if flag_given("--" + key.replace("_", "-"))}
    detection_cb = detection_callback if interactive else None
```

Note: `interactive` is computed a few lines later (line 336) — move the `detection_cb` assignment to **after** that computation. Then pass `on_detection=detection_cb, explicit_keys=explicit_keys` to every `projects.run_project(...)` call and to `projects.run_batch(...)` (its `**kwargs` forwards them). Four call sites: lines 345, 386, 409, 412.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_cli_formats.py -q`
Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add chopify.py tests/test_cli_formats.py
git commit -m "feat(cli): video type flags and interactive format builder"
```

---

### Task 5: Studio — detection banner, format config, and save route

**Files:**
- Modify: `dashboard.py` (`config_payload` extraction near line 214, `project_detail` line 24, `do_POST` near line 316)
- Modify: `assets/studio/index.html`, `assets/studio/app.js`, `assets/studio/style.css`
- Test: `tests/test_dashboard_formats.py`

**Interfaces:**
- Consumes: `video_types.VIDEO_TYPES`, `video_types.FORMATS`, `video_types.load_custom_formats`, `video_types.save_custom_format`.
- Produces: `dashboard.config_payload(root)`, `dashboard.save_format(body) -> dict`, `/api/formats` POST, `project_detail` keys `detection`, `format`, `format_recipe`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_formats.py
import dashboard


def test_config_payload_exposes_types_and_formats(tmp_path):
    payload = dashboard.config_payload(tmp_path)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_formats.py -q`
Expected: FAIL — `AttributeError: module 'dashboard' has no attribute 'config_payload'`.

- [ ] **Step 3: Add dashboard helpers and routes**

Add `import video_types` after existing imports (line 18 area). Add these top-level functions before `project_detail`:

```python
def config_payload(root):
    """Config response, including video types and available clipping formats."""
    custom, warnings = video_types.load_custom_formats()
    return {
        "video_types": list(video_types.VIDEO_TYPES),
        "formats": {**video_types.FORMATS, **custom},
        "format_warnings": warnings,
    }


def save_format(body, root=None):
    """Validate and persist a custom clipping format."""
    return video_types.save_custom_format(body, root=root)
```

In `project_detail`, extend the final `data.update({...})` dict with:

```python
        "detection": state.get("detection"),
        "format": state.get("format"),
        "format_recipe": state.get("format_recipe"),
```

In `do_GET`, replace the inline `return self.send_json({...})` body for `["api", "config"]` (lines 227-243) with:

```python
                payload = {
                    "token": self.app.token,
                    "defaults": projects.DEFAULT_OPTIONS,
                    "brands": brands,
                    "caption_brands": caption_brands,
                    "caption_presets": render_clips.CAPTION_STYLES,
                    "caption_defaults": branding.CAPTION_DEFAULTS,
                    "caption_aspects": {
                        name: {"font_size": size, "margin": margin}
                        for name, (_, _, size, margin) in render_clips.ASPECTS.items()
                    },
                    "caption_power_words": sorted(render_clips.POWER_WORDS),
                    "caption_emoji_rules": [
                        {"words": list(stems), "emoji": emoji}
                        for stems, emoji in render_clips.EMOJI_RULES
                    ],
                }
                payload.update(config_payload(self.app.root))
                return self.send_json(payload)
```

In `do_POST`, add before the `parts == ["api", "projects"]` branch:

```python
                if parts == ["api", "formats"]:
                    return self.send_json(save_format(body), 201)
```

- [ ] **Step 4: Add the Studio UI**

In `assets/studio/index.html`, inside the project view header immediately after the element with `id="project-source"`, add:

```html
      <div id="detection-banner" class="detection-banner" hidden></div>
```

In `assets/studio/app.js`, add these functions before `renderDetail`:

```js
function renderDetection(detail) {
  const banner = $("#detection-banner");
  if (!banner) return;
  const detection = detail.detection;
  if (!detection) { banner.hidden = true; return; }
  banner.hidden = false;
  const confidence = Math.round((detection.confidence || 0) * 100);
  const formatName = detail.format?.name || "unknown";
  banner.textContent = `Detected: ${detection.type} (${confidence}%) · Using format: ${formatName}`;
  const change = document.createElement("select");
  change.setAttribute("aria-label", "Clipping format");
  const formats = Object.keys((state.config && state.config.formats) || {});
  for (const name of formats) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.selected = name === formatName;
    change.appendChild(option);
  }
  change.addEventListener("change", async () => {
    await api(`/api/projects/${state.id}/settings`, { format: change.value });
    notice("Format saved. Prepare again to apply it.");
  });
  banner.appendChild(change);
  const build = document.createElement("button");
  build.type = "button";
  build.textContent = "Build new format";
  build.addEventListener("click", () => buildFormat(detail));
  banner.appendChild(build);
}

async function buildFormat(detail) {
  const name = window.prompt("New format name (lowercase):");
  if (!name) return;
  const base = detail.format_recipe || {};
  const min = window.prompt("Minimum clip seconds", String(base.min_len || 30));
  const max = window.prompt("Maximum clip seconds", String(base.max_len || 120));
  try {
    await api("/api/formats", {
      ...base, name,
      min_len: Number(min), max_len: Number(max),
    });
    notice("Format saved. Reload Studio to see it in the list.");
    await config();
  } catch (error) {
    notice(error.message, true);
  }
}
```

In `renderDetail`, immediately after `const options = p.options;`, add:

```js
  renderDetection(detail);
```

In `assets/studio/style.css`, append:

```css
.detection-banner {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--hairline, #2b3234);
  color: var(--ink, #f2f4ee);
}
.detection-banner[hidden] { display: none; }
```

- [ ] **Step 5: Run tests and JS syntax check**

Run: `pytest tests/test_dashboard_formats.py -q && node --check assets/studio/app.js`
Expected: 2 tests PASS; JS syntax clean.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py assets/studio/index.html assets/studio/app.js assets/studio/style.css tests/test_dashboard_formats.py
git commit -m "feat(studio): detection banner and custom format saving"
```

---

### Task 6: Live sessions consume formats

**Files:**
- Modify: `live.py` (`LiveSession.__init__` line 31, `_save` line 76, `run_live` line 256)
- Modify: `chopify.py` (live branch call at line 306)
- Test: `tests/test_live_formats.py`

**Interfaces:**
- Consumes: `video_types.signals_from_measurements`, `video_types.classify`, `video_types.resolve_format`, `video_types.apply_format`.
- Produces: `live.run_live(url, options, root, ..., explicit_keys=None)`; `LiveSession(..., detection=None, format_info=None)`; `live_session.json` keys `detection`, `format`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_formats.py
import live
import projects
import video_types


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_live_formats.py -q`
Expected: FAIL — `unexpected keyword argument 'explicit_keys'`.

- [ ] **Step 3: Implement live format handling**

In `live.py`, add `import video_types` after existing imports. Change `LiveSession.__init__` signature to:

```python
    def __init__(self, source, options, root, brief_text=None, session_id=None,
                 detection=None, format_info=None):
```

Inside, right after `self.options = projects.validate_options(options)`, add:

```python
        self.detection = detection
        self.format = format_info
```

In `_save`, add to the JSON dict:

```python
            "detection": getattr(self, "detection", None),
            "format": getattr(self, "format", None),
```

Change `run_live` (line 256) to:

```python
def run_live(url, options, root, on_log=None, brief_text=None, explicit_keys=None):
    options = projects.validate_options(options)
    sig = video_types.signals_from_measurements(source=url)
    detection = video_types.classify(sig)
    resolved = video_types.resolve_format(
        detection, options.get("video_type"), options.get("format"))
    options = video_types.apply_format(options, resolved["format"],
                                       explicit_keys or set())
    source = live_capture.resolve_live_source(url, options)
    session = LiveSession(source, options, root, brief_text=brief_text,
                          detection=detection, format_info={
                              "name": resolved["format"]["name"],
                              "source": resolved["source"],
                              "needs_confirmation": resolved["needs_confirmation"]})
    if on_log:
        session._on_log = on_log
    session.start()
    return session
```

In `chopify.py` live branch, replace the `live.run_live(...)` call with:

```python
        live.run_live(args.live, live_options, Path(args.projects_dir).resolve(),
                      brief_text=brief_text, explicit_keys=explicit_keys)
```

`explicit_keys` is defined in the non-live branch; add the same computation just before the live call:

```python
        explicit_keys = {key for key in projects.DEFAULT_OPTIONS
                         if flag_given("--" + key.replace("_", "-"))}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_live_formats.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add live.py chopify.py tests/test_live_formats.py
git commit -m "feat(live): apply detected format and live overrides"
```

---

### Task 7: Agent interface — options pass through and self-description

**Files:**
- Modify: `docs/AGENT-API.md` (documentation only; no schema change needed because options are a BLOB)
- Test: `tests/test_agent_formats.py`

**Interfaces:**
- Consumes: `projects.validate_options`, `agent_ops._options`.
- Produces: documented `video_type`, `format`, `video_type_model` option fields; detection and format fields in project state.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_formats.py
import agent_ops
import projects


def test_agent_options_accept_video_type_fields():
    options = agent_ops._options(
        '{"video_type": "gaming", "format": "gaming", "video_type_model": null}')
    assert options["video_type"] == "gaming"
    assert options["format"] == "gaming"


def test_project_state_exposes_detection_fields(tmp_path):
    state = projects.init_project(tmp_path, "video.mp4", {
        "video_type": "gaming", "format": "gaming"})
    assert state["options"]["video_type"] == "gaming"
    assert state["options"]["format"] == "gaming"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_formats.py -q`
Expected: FAIL — `KeyError: 'video_type'` in `projects.validate_options` (before Task 2 this key was rejected); after Tasks 1-2 it already passes. If it passes, proceed — the test is a regression guard.

- [ ] **Step 3: Document the agent contract**

Add to `docs/AGENT-API.md` under the options section:

```markdown
`--options-json` also accepts `video_type` (`auto` or one of `podcast`,
`gaming`, `movie`, `livestream`, `interview`, `tutorial`, `music`, `unknown`),
`format` (a built-in or custom format name, or a JSON path), and
`video_type_model` (optional local Ollama model for uncertain detections).
Auto mode is silent: agents are never prompted. Read back `detection`,
`format`, and `format_recipe` from project state and from
`candidates.json` / `selection.json` stats.
```

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_agent_formats.py -q`
Expected: PASS.

```bash
git add docs/AGENT-API.md tests/test_agent_formats.py
git commit -m "docs(agent): document video type and format options"
```

---

### Task 8: User-facing documentation

**Files:**
- Modify: `README.md`, `docs/CLI.md`, `docs/WORKFLOWS.md`, `CHANGELOG.md`, `docs/product.json`

- [ ] **Step 1: Add the README feature row and example**

In the "What can Chopify do?" table, add:

```markdown
| **Video-type formats** | Detects podcast, gaming, movie, livestream, interview, tutorial, or music from local signals, then applies a matching clipping format. If none fits, Studio/CLI lets you build a new one. Explicit flags always win. |
```

In the CLI examples block, add:

```powershell
# Let Chopify detect the type and use its clipping format
python chopify.py "C:\Videos\stream.mp4" --aspect 9:16

# Force a type or a saved custom format
python chopify.py "recording.mp4" --video-type podcast
python chopify.py "recording.mp4" --format my_cast
python chopify.py --list-formats
```

In the FAQ, add:

```markdown
### How reliable is video-type detection?

It infers a type from local speech, audio, visual, and source metadata signals —
it is not a video-understanding model. A dialogue-heavy movie can look like a
podcast. Chopify reports a confidence and the chosen format, lets you override
the type or format at any time, and can ask you to build a new format when it is
uncertain. No footage or metadata leaves your machine unless you enable an
optional local Ollama model.
```

- [ ] **Step 2: Add CLI and workflow docs**

In `docs/CLI.md`, add a "Video types and formats" section listing the flags, the eight types, the built-in formats table from the spec, and a custom-format JSON example. In `docs/WORKFLOWS.md`, add a "Build a clipping format" section describing the interactive CLI builder, Studio's **Build new format** button, and that changing type/format invalidates discovery caches so the next prepare re-clips.

- [ ] **Step 3: Update changelog and product facts**

Add to `CHANGELOG.md`:

```markdown
## Unreleased

- Added local video-type detection (podcast, gaming, movie, livestream,
  interview, tutorial, music, unknown) with reusable clipping formats.
- Added `--video-type`, `--format`, `--video-type-model`, and `--list-formats`.
- Studio now shows the detected type and can save custom formats.
- Live sessions and the agent interface accept type/format options.
```

Add a matching capability entry to `docs/product.json` (keep the existing JSON structure and keys).

- [ ] **Step 4: Verify docs and commit**

Run: `pytest tests/ -q && python -m compileall -q chopify.py dashboard.py projects.py score_clips.py video_types.py live.py agent_ops.py`
Expected: full suite PASS; compile clean.

```bash
git add README.md docs/CLI.md docs/WORKFLOWS.md docs/AGENT-API.md CHANGELOG.md docs/product.json
git commit -m "docs: document video types and clipping formats"
```

---

## Self-review

**Spec coverage:** classification (T1), formats/custom builder (T1, T4, T5), resolution/override (T1, T2), CLI (T4), Studio (T5), live (T6), agent (T7), scoring integration (T3), error handling (T1 validation, T4 builder, T5 route), tests (T1-T7), docs (T8). Every spec section maps to a task.

**Placeholder scan:** no TBD/TODO; every code step contains runnable code and exact test commands.

**Type consistency:** `resolve_format` returns `{format, source, needs_confirmation, warnings}` everywhere; `apply_detection` returns `(options, state)`; `detect_from_transcript(data, source, workdir, options)` is called with the same four arguments in T2 only; `discover(..., band=, profile=, detection=, format_info=)` matches between T2 and T3; `run_project(..., on_detection=, explicit_keys=)` matches between T2, T4 and T6; `run_live(..., explicit_keys=)` matches between T6 and `chopify.py`.
