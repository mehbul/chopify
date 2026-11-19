"""Campaign brief parsing and rule enforcement using local LLM agents."""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import urllib.request
from pathlib import Path

PROVIDERS = {
    "ollama": {"kind": "http"},
    "claude": {"command": "claude -p {prompt}"},
    "codex": {"command": "codex exec {prompt}"},
    "hermes": {"command": "hermes --prompt {prompt}"},
}
RULES_FILE = "brief_rules.json"
FIELDS = ("goal", "must_mention", "banned", "min_len", "max_len",
          "aspect", "cta", "tone", "source", "provider", "brief_excerpt")


def detect_provider(explicit=None, command=None):
    if command:
        return "custom", {"command": command}
    if explicit:
        if explicit not in PROVIDERS:
            raise ValueError(f"Unknown brief provider: {explicit}")
        return explicit, PROVIDERS[explicit]
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1)
        return "ollama", PROVIDERS["ollama"]
    except Exception:
        pass
    for name in ("claude", "codex", "hermes"):
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            probe = subprocess.run([executable, "--version"], capture_output=True,
                                   timeout=5, check=False)
            if probe.returncode == 0:
                return name, PROVIDERS[name]
            return name, dict(PROVIDERS[name],
                              unavailable_reason=f"{name} found but not authenticated or not runnable")
        except Exception as exc:
            return name, dict(PROVIDERS[name],
                              unavailable_reason=f"{name} found but not runnable: {exc}")
    return None, None


def ask_llm(prompt, provider=None, command=None, timeout=120):
    name, descriptor = detect_provider(provider, command)
    if name == "ollama":
        body = json.dumps({"model": "qwen2.5:7b", "stream": False,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
        return data["message"]["content"]
    if name is None:
        raise RuntimeError("No local LLM agent found")
    argv = shlex.split(descriptor["command"])
    argv = [prompt if part == "{prompt}" else part for part in argv]
    if name in PROVIDERS:
        executable = shutil.which(name)
        if executable:
            argv[0] = executable
    result = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout, check=True)
    return result.stdout


def parse_json_response(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("LLM returned no JSON object")
    depth, end = 0, None
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("LLM returned an unterminated JSON object")
    return json.loads(cleaned[start:end])


def _normalize(data, source, provider, text):
    def strings(key):
        value = data.get(key) or []
        if isinstance(value, str):
            value = [value]
        return [str(v).lower().strip() for v in value if str(v).strip()]

    def number(key, default, low, high):
        try:
            value = int(data.get(key))
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    rules = {
        "goal": str(data.get("goal") or "").strip()[:500],
        "must_mention": strings("must_mention"),
        "banned": strings("banned"),
        "min_len": number("min_len", 15, 1, 3600),
        "max_len": number("max_len", 300, 1, 3600),
        "aspect": str(data.get("aspect") or "9:16"),
        "cta": str(data.get("cta") or "").strip()[:300],
        "tone": str(data.get("tone") or "").strip()[:300],
        "source": source,
        "provider": provider or "none",
        "brief_excerpt": text[:800],
    }
    if rules["aspect"] not in {"16:9", "9:16", "1:1"}:
        rules["aspect"] = "9:16"
    if rules["min_len"] > rules["max_len"]:
        rules["min_len"], rules["max_len"] = rules["max_len"], rules["min_len"]
    return rules


def _clean_phrase(value):
    return re.sub(r"^(?:must|include|mention|show|avoid|no|don't|exclude)\s*:\s*",
                  "", value.strip(), flags=re.IGNORECASE).strip()


def _heuristic_parse(text):
    lower = text.lower()
    must = []
    for match in re.finditer(r"(?:must|include|mention|show)\s*:?\s*([^.\n]+)", lower):
        must += [p for p in (_clean_phrase(v) for v in re.split(r"[,/]", match.group(1))) if p]
    banned = []
    for match in re.finditer(r"(?:avoid|no|don't|exclude)\s*:?\s*([^.\n]+)", lower):
        banned += [p for p in (_clean_phrase(v) for v in re.split(r"[,/]", match.group(1))) if p]
    span = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*s", lower)
    cta = re.search(r"(?:cta|call to action)\s*:?\s*([^\n]+)", lower)
    aspect = re.search(r"\b(9:16|16:9|1:1)\b", text)
    return _normalize({
        "goal": text.splitlines()[0] if text.splitlines() else "",
        "must_mention": must[:10], "banned": banned[:10],
        "min_len": span.group(1) if span else 15,
        "max_len": span.group(2) if span else 300,
        "aspect": aspect.group(1) if aspect else "9:16",
        "cta": cta.group(1) if cta else "",
        "tone": "",
    }, "heuristic", "none", text)


def parse_brief(text, provider=None, command=None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Provide a campaign brief.")
    text = text.strip()[:8000]
    if provider == "none":
        rules = _heuristic_parse(text)
        rules["warning"] = "Brief parsed with keyword rules (provider disabled)."
        return rules
    name, descriptor = detect_provider(provider, command)
    prompt = (
        "Read this campaign brief and reply with ONLY one JSON object with "
        "fields: goal (string), must_mention (array of short phrases), "
        "banned (array of short phrases), min_len (int seconds), max_len "
        "(int seconds), aspect (one of 16:9, 9:16, 1:1), cta (string), "
        "tone (string). Infer sensible values when absent.\n\nBRIEF:\n" + text)
    try:
        if descriptor and descriptor.get("unavailable_reason"):
            raise RuntimeError(descriptor["unavailable_reason"])
        raw = ask_llm(prompt, provider, command)
        data = parse_json_response(raw)
        return _normalize(data, "llm", name or "custom", text)
    except Exception as exc:
        rules = _heuristic_parse(text)
        rules["warning"] = f"LLM brief parsing failed ({exc}); using keyword rules."
        return rules


def save_rules(workdir, rules):
    path = Path(workdir) / RULES_FILE
    path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_rules(workdir):
    path = Path(workdir) / RULES_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def segment_texts(segments, workdir):
    """Build one lowercase transcript string per segment for rule checks."""
    data = json.loads((Path(workdir) / "transcript.json").read_text(encoding="utf-8-sig"))
    words = data.get("words") or []
    texts = []
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        texts.append(" ".join(str(w.get("word", "")).lower()
                              for w in words if start <= w.get("start", 0) < end))
    return texts


def _matches(text, phrase):
    tokens = [t for t in phrase.lower().split() if len(t) > 2]
    return bool(tokens) and all(t in text for t in tokens)


def apply_rules(segments, rules, workdir, provider=None, command=None):
    if not segments:
        return [], []
    texts = segment_texts(segments, workdir)
    kept, kept_texts = [], []
    for seg, text in zip(segments, texts):
        if any(phrase in text for phrase in rules.get("banned", [])):
            continue
        if not all(_matches(text, phrase) for phrase in rules.get("must_mention", [])):
            continue
        kept.append(seg)
        kept_texts.append(text)
    if not kept:
        return [], []
    listing = "\n".join(f"{i}. {t[:600]}" for i, t in enumerate(kept_texts))
    prompt = (
        "Score each numbered text from 0 to 10 for how well it satisfies this "
        f"campaign goal: {rules.get('goal','')}. Tone: {rules.get('tone','')}. "
        f"CTA: {rules.get('cta','')}. Reply with ONLY JSON: "
        '{"scores":[{"index":0,"score":8.5,"reason":"short"}]}.\n\n' + listing)
    try:
        data = parse_json_response(ask_llm(prompt, provider, command))
        scores = sorted(data.get("scores", []),
                        key=lambda s: float(s.get("score", 0)), reverse=True)
        order = [int(s["index"]) for s in scores
                 if 0 <= int(s.get("index", -1)) < len(kept)]
        unique = list(dict.fromkeys(order))
        kept = [kept[i] for i in unique] + [s for i, s in enumerate(kept) if i not in unique]
        return kept, scores
    except Exception:
        return kept, []
