# Campaign Brief Clipping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a pasted campaign brief into structured rules via a locally installed LLM agent, then cut clips that satisfy those rules in batch and live modes.

**Architecture:** A new `campaign.py` owns provider auto-detection, the local-LLM bridge, the `brief_rules.json` contract, and rule enforcement. `score_clips.py` and `live.py` call into it; CLI and Studio both feed the brief into the same parser. A deterministic heuristic fallback keeps the feature working when no agent is installed.

**Tech Stack:** Python 3.11+, stdlib (`subprocess`, `shlex`, `shutil`, `urllib`, `re`), local CLI agents (claude/codex/hermes) or Ollama HTTP, pytest.

## Global Constraints

- No API keys stored by chopify; no cloud inference. Local CLI tools or Ollama at `127.0.0.1:11434` only.
- Auto-detect order: Ollama HTTP, then `claude`, `codex`, `hermes` on PATH; `--brief-provider` / `--brief-command` override.
- LLM timeout 120 s; brief truncated to 8000 chars; `brief_excerpt` keeps first 800 chars.
- Rules JSON always has: `goal`, `must_mention`, `banned`, `min_len`, `max_len`, `aspect`, `cta`, `tone`, `source`, `provider`, `brief_excerpt`.
- Heuristic fallback on any LLM failure; never abort a job because of the LLM.
- `brief_rules.json` lives in the project workdir (batch) or live session dir.
- Full suite must pass before each commit.

---

### Task 1: Campaign module — provider bridge and brief parsing

**Files:** Create `campaign.py`, `tests/test_campaign.py`

**Interfaces produced:** `PROVIDERS`, `detect_provider(explicit=None, command=None) -> (name, descriptor)`, `ask_llm(prompt, provider=None, command=None, timeout=120) -> str`, `parse_json_response(text) -> dict`, `parse_brief(text, provider=None, command=None) -> dict`, `save_rules(workdir, rules)`, `load_rules(workdir)`.

- [ ] **Step 1: Write failing tests**

```python
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
    monkeypatch.setattr(campaign, "ask_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    rules = campaign.parse_brief(
        "Goal: show camera. Must include: night mode. Avoid: spoilers. 15-30s, 9:16. CTA: link in bio")
    assert rules["source"] == "heuristic"
    assert "night mode" in rules["must_mention"]
    assert rules["aspect"] == "9:16"
    assert rules["min_len"] == 15 and rules["max_len"] == 30
    assert rules["cta"] == "link in bio"


def test_parse_brief_uses_llm(monkeypatch, tmp_path):
    payload = {"goal": "g", "must_mention": ["a"], "banned": [], "min_len": 10,
               "max_len": 40, "aspect": "9:16", "cta": "c", "tone": "t"}
    monkeypatch.setattr(campaign, "detect_provider", lambda *a, **k: ("fake", {"command": "fake {prompt}"}))
    monkeypatch.setattr(campaign, "ask_llm", lambda *a, **k: json.dumps(payload))
    rules = campaign.parse_brief("brief text")
    assert rules["source"] == "llm" and rules["provider"] == "fake"
    assert rules["must_mention"] == ["a"]


def test_save_load_rules(tmp_path):
    rules = campaign.parse_brief("must include: x")
    campaign.save_rules(tmp_path, rules)
    assert campaign.load_rules(tmp_path)["must_mention"] == ["x"]
```

- [ ] **Step 2: Run to verify failure** → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement `campaign.py`**

```python
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
        if shutil.which(name):
            return name, PROVIDERS[name]
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


def _heuristic_parse(text):
    lower = text.lower()
    must = []
    for match in re.finditer(r"(?:must|include|mention|show)\s*:?\s*([^.\n]+)", lower):
        must += [p.strip() for p in re.split(r"[,/]", match.group(1)) if p.strip()]
    banned = []
    for match in re.finditer(r"(?:avoid|no|don't|exclude)\s*:?\s*([^.\n]+)", lower):
        banned += [p.strip() for p in re.split(r"[,/]", match.group(1)) if p.strip()]
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
    name, _ = detect_provider(provider, command)
    prompt = (
        "Read this campaign brief and reply with ONLY one JSON object with "
        "fields: goal (string), must_mention (array of short phrases), "
        "banned (array of short phrases), min_len (int seconds), max_len "
        "(int seconds), aspect (one of 16:9, 9:16, 1:1), cta (string), "
        "tone (string). Infer sensible values when absent.\n\nBRIEF:\n" + text)
    try:
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
```

- [ ] **Step 4: Run to verify pass** → `python -m pytest -q tests/test_campaign.py`
- [ ] **Step 5: Commit** → `git commit -m "feat(campaign): add local-LLM brief parser"`

---

### Task 2: Rule enforcement

**Files:** `campaign.py`, `tests/test_campaign.py`

**Interfaces:** `segment_texts(segments, workdir) -> list[str]`, `apply_rules(segments, rules, workdir, provider=None, command=None) -> (segments, scores)`.

- [ ] **Step 1: Failing tests**

```python
def test_apply_rules_filters_and_scores(tmp_path, monkeypatch):
    import campaign
    words = [
        {"word": "night", "start": 0, "end": 0.5},
        {"word": "mode", "start": 0.5, "end": 1},
        {"word": "spoiler", "start": 1, "end": 1.5},
    ]
    (tmp_path / "transcript.json").write_text(
        '{"video": "x.mp4", "duration": 2, "words": ' + __import__("json").dumps(words) + '}')
    rules = campaign._normalize({"goal": "g", "must_mention": ["night mode"],
                                 "banned": ["spoiler"], "cta": ""}, "llm", "fake", "brief")
    monkeypatch.setattr(campaign, "ask_llm",
                        lambda prompt, *a, **k: '{"scores":[{"index":0,"score":9,"reason":"ok"}]}')
    monkeypatch.setattr(campaign, "detect_provider",
                        lambda *a, **k: ("fake", {"command": "fake {prompt}"}))
    segments = [{"start": 0, "end": 1, "hook": "a"}, {"start": 1, "end": 2, "hook": "b"}]
    kept, scores = campaign.apply_rules(segments, rules, tmp_path)
    assert len(kept) == 1 and kept[0]["hook"] == "a"
    assert scores[0]["score"] == 9
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** (append to `campaign.py`):

```python
def segment_texts(segments, workdir):
    """Build one lowercase transcript string per segment for rule checks."""
    data = json.loads((Path(workdir) / "transcript.json").read_text(encoding="utf-8-sig"))
    words = data.get("words") or []
    texts = []
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        texts.append(" ".join(str(w.get("word", "")).lower()
                              for w in words if start <= w.get("start", 0) <= end))
    return texts


def _matches(text, phrase):
    tokens = [t for t in phrase.lower().split() if len(t) > 2]
    return bool(tokens) and all(t in text for t in tokens)


def apply_rules(segments, rules, workdir, provider=None, command=None):
    if not segments:
        return [], []
    texts = segment_texts(segments, workdir)
    kept, kept_texts, indices = [], [], []
    for index, (seg, text) in enumerate(zip(segments, texts)):
        if any(phrase in text for phrase in rules.get("banned", [])):
            continue
        if not all(_matches(text, phrase) for phrase in rules.get("must_mention", [])):
            continue
        kept.append(seg)
        kept_texts.append(text)
        indices.append(index)
    if not kept:
        return [], []
    listing = "\n".join(f'{i}. {t[:600]}' for i, t in enumerate(kept_texts))
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
```

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(campaign): enforce brief rules with batched LLM scoring"`

---

### Task 3: Batch integration and CLI flags

**Files:** `score_clips.py`, `chopify.py`, `tests/test_campaign.py`

- [ ] **Step 1: Failing test**

```python
def test_score_clips_applies_brief_rules(tmp_path, monkeypatch):
    import score_clips, campaign
    data = {"video": "x.mp4", "duration": 10,
            "words": [{"word": "night", "start": 0, "end": 1},
                      {"word": "mode", "start": 1, "end": 2}]}
    (tmp_path / "transcript.json").write_text(__import__("json").dumps(data))
    (tmp_path / "candidates.json").write_text(__import__("json").dumps([]))
    (tmp_path / "segments.json").write_text(__import__("json").dumps([]))
    rules = campaign._normalize({"must_mention": ["night mode"]}, "llm", "fake", "brief")
    campaign.save_rules(tmp_path, rules)
    monkeypatch.setattr(score_clips, "discover", lambda *a, **k: None)
    monkeypatch.setattr(score_clips, "select",
                        lambda *a, **k: ([{"start": 0, "end": 2, "hook": "a"}], "heuristic"))
    monkeypatch.setattr(campaign, "ask_llm",
                        lambda prompt, *a, **k: '{"scores":[{"index":0,"score":9}]}')
    monkeypatch.setattr(campaign, "detect_provider",
                        lambda *a, **k: ("fake", {"command": "fake {prompt}"}))
    segments, mode = score_clips.run(tmp_path, min_score=0)
    assert len(segments) == 1
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement**

At the top of `score_clips.py` add `import campaign`. In `run()`, immediately before `if not segments:` (around line 1702), insert:

```python
    rules = campaign.load_rules(workdir)
    if rules and segments:
        segments, scores = campaign.apply_rules(
            segments, rules, workdir, command=None)
        if scores:
            print(f"Campaign brief: {len(segments)} clip(s) matched "
                  f"(provider={rules.get('provider')}).", flush=True)
        else:
            print(f"Campaign brief: {len(segments)} clip(s) matched by rules "
                  "(LLM scoring unavailable).", flush=True)
```

In `chopify.py::main()`, add flags after `--live-header`:

```python
    ap.add_argument("--brief", default=None, help="campaign requirements text (conflicts with --brief-file)")
    ap.add_argument("--brief-file", default=None, help="path to a .txt/.md campaign brief")
    ap.add_argument("--brief-provider", default=None,
                    choices=["ollama", "claude", "codex", "hermes"],
                    help="local LLM agent for brief parsing (auto-detected by default)")
    ap.add_argument("--brief-command", default=None,
                    help="custom command template for the local LLM, with {prompt}")
```

Insert after `args = ap.parse_args()` and before the `--live` branch:

```python
    brief_text = args.brief
    if args.brief_file:
        if args.brief:
            ap.error("--brief and --brief-file are mutually exclusive.")
        brief_text = Path(args.brief_file).read_text(encoding="utf-8-sig")
    if brief_text:
        import campaign
        rules = campaign.parse_brief(brief_text, args.brief_provider, args.brief_command)
        if rules.get("warning"):
            print(f"Brief warning: {rules['warning']}", flush=True)
```

For single-source mode, after the project path is known and before `run_project`, write the rules:

```python
        if brief_text:
            campaign.save_rules(path, rules)
```

For batch mode, inside the loop over created projects (after `results = projects.run_batch(...)`), write rules into each project dir by deriving it from `Path(args.projects_dir).resolve() / result["id"]` before queuing — but since `run_batch` already queues, simpler: write rules to `Path(args.projects_dir).resolve() / "<id>"` for each returned result **before** `projects.run_batch` starts jobs is not possible; instead add `--brief` support only to non-batch runs and print a clear message for batch:

```python
    if brief_text and args.batch:
        ap.error("--brief is not supported with --batch; run sources individually or use Studio.")
```

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(campaign): wire brief parsing into scoring and CLI"`

---

### Task 4: Live session integration

**Files:** `live.py`, `live_signals.py`, `tests/test_live.py`

- [ ] **Step 1: Failing test**

```python
def test_live_session_parses_brief(tmp_path, monkeypatch):
    import live, campaign
    monkeypatch.setattr(campaign, "parse_brief",
                        lambda text, *a, **k: {"must_mention": ["night mode"], "banned": []})
    from live_capture import LiveSource
    source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, {}, tmp_path, brief_text="show night mode")
    assert session.rules["must_mention"] == ["night mode"]
    assert (session.dir / "brief_rules.json").exists()
```

- [ ] **Step 2: Run to verify failure** → FAIL (`unexpected keyword argument 'brief_text'`).
- [ ] **Step 3: Implement**

In `live.py`:
- `LiveSession.__init__(self, source, options, root, brief_text=None)` — set `self._on_log = None` before the brief block, then after creating dirs parse and save rules:

```python
        self.rules = None
        if brief_text:
            import campaign
            self.rules = campaign.parse_brief(brief_text)
            campaign.save_rules(self.dir, self.rules)
            if self.rules.get("warning"):
                self._log(f"Brief warning: {self.rules['warning']}")
```

- In `_loop()`, when a transcript analyzer exists and a hype trigger fires, gate using rules before rendering:

```python
            if probe is not None and self._allowed(result["score"], self.stream_time):
                if self.rules and not self._brief_allows(transcript, self.stream_time):
                    self._log("Brief rules not met; skipping clip.")
                else:
                    ... existing render block ...
```

- Add method:

```python
    def _brief_allows(self, transcript, now):
        if not self.rules:
            return True
        from campaign import _matches
        words = list(transcript.words) if transcript else []
        text = " ".join(str(w.get("word", "")).lower() for w in words
                        if now - 10 <= w.get("start", 0) <= now)
        if any(b in text for b in self.rules.get("banned", [])):
            return False
        return all(_matches(text, phrase) for phrase in self.rules.get("must_mention", []))
```

- `run_live(url, options, root, on_log=None, brief_text=None)` forwards `brief_text`.
- CLI `--live` branch passes `brief_text=brief_text` to `live.run_live`.
- CLI live branch: remove the batch incompatibility error so brief works with `--live`.

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(campaign): gate live clips with brief rules"`

---

### Task 5: Studio and docs

**Files:** `assets/studio/index.html`, `assets/studio/app.js`, `dashboard.py`, `README.md`, `docs/CLI.md`, `docs/WORKFLOWS.md`, `CHANGELOG.md`

- [ ] **Step 1: Studio markup** — add to the new-project form and the Live form:

```html
<label for="brief-text">Campaign brief (paste requirements)</label>
<textarea id="brief-text" rows="5" placeholder="Paste the campaign requirements here..."></textarea>
```

- [ ] **Step 2: JS** — `api("/api/projects", {... , brief: $("#brief-text").value})` and `api("/api/live", {source, options, brief: $("#brief-text").value})`.
- [ ] **Step 3: Backend** — in `dashboard.py` POST `/api/projects` and `/api/live`, after `projects.create_project` / before `start_live`, if `body.get("brief")` then parse and save rules into the project/session dir via `campaign.parse_brief` / `campaign.save_rules`.
- [ ] **Step 4: Docs** — `docs/CLI.md` flags, `docs/WORKFLOWS.md` campaign workflow + `brief_rules.json`, README feature row, CHANGELOG entry.
- [ ] **Step 5: Verify** — `python -m pytest -q`, `node --check assets/studio/app.js`, `python tools/build_site.py --out _site`.
- [ ] **Step 6: Commit** → `git commit -m "docs(campaign): document brief-driven clipping"`

---

## Self-review notes

- Spec coverage: bridge/parsing (Task 1), enforcement (Task 2), batch + CLI (Task 3), live (Task 4), Studio + docs (Task 5).
- Type consistency: `parse_brief()` always returns a normalized dict with all 11 keys; `save_rules()`/`load_rules()` use `brief_rules.json`; `apply_rules()` consumes the same dict and returns `(segments, scores)`.
- Ambiguity resolved: `--brief` works for single-source and live runs; `--batch` is rejected with a clear message because queued jobs cannot receive the parsed rules before starting.
