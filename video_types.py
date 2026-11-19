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
        "movie": (0.50 * stable + 0.30 * (1.0 - speech) + 0.20 * sig.get("long", 0.0)
                  + 0.10 * (1.0 - sig.get("facecam", 0.0)))
                 * (1.0 - 0.8 * sig.get("streamish", 0.0)),
        "music": 0.50 * sig.get("burst", 0.0) + 0.30 * sig.get("audio", 0.0)
                 + 0.20 * (1.0 - speech),
        "livestream": 0.95 * sig.get("streamish", 0.0) + 0.25 * sig.get("long", 0.0)
                      + 0.15 * sig.get("facecam", 0.0),
    }
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    name, top = ordered[0]
    runner, second = ordered[1]
    has_evidence = any(sig.get(key, 0.0) > 0.0 for key in (
        "speech", "audio", "burst", "visual", "cuts", "facecam",
        "long", "streamish"))
    lacks_evidence = not has_evidence or top < 0.15
    if lacks_evidence:
        name = "unknown"
    margin = top - second
    result = {
        "type": name,
        "confidence": round(top, 3),
        "margin": round(margin, 3),
        "signals": sig,
        "reason": f"top={name} ({top:.2f}), runner-up={runner} ({second:.2f})",
        "needs_confirmation": (lacks_evidence or top < CONFIDENCE_MIN
                               or margin < MARGIN_MIN),
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
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        label = str(payload.get("message", {}).get("content", "")).strip().lower()
    except (OSError, ValueError, KeyError, TypeError):
        return None
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
