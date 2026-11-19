# Chopify campaign-brief clipping design

## Goal

Let a user paste a reward-platform / campaign requirements document (messy
prose, bullets, rules, hashtags — anything) and have chopify use a **local CLI
LLM agent** to turn it into structured rules, then cut only the moments that
satisfy those rules. Works for batch projects and live sessions, from CLI and
Studio. No cloud service, no API key stored by chopify.

## Decisions made with the user

| Decision | Choice |
| --- | --- |
| Brief content | Mixed free-form text, bullets, and platform noise |
| Behavior | Extract structured rules first, then apply them |
| Rule fields | must-mention, banned, length/aspect, goal/angle + CTA, tone |
| Input | CLI and Studio |
| LLM access | Local CLI tools (Claude, Codex, Hermes, Ollama, etc.) |
| Provider setup | Auto-detect installed tools, with manual override |
| Output | Extracted rules JSON; no dedicated per-clip report or Studio panel |
| Enforcement | Hard filters + LLM fit score |
| Scope | Batch and live |

## Non-goals

- Storing API keys or calling hosted LLM APIs directly.
- A separate Studio page or per-clip match report UI.
- Guaranteeing campaign acceptance — scores and rules are editing aids.
- Changing existing selection behavior when no brief is supplied.

## Architecture

A new `campaign.py` module owns brief parsing, the local-LLM bridge, and rule
enforcement. `score_clips.py` and `live.py` call into it. The rules JSON is the
contract between parsing and enforcement, so users can inspect/edit it.

### 1. Local LLM bridge

`campaign.py` exposes:

```python
PROVIDERS = {
    "ollama": {"kind": "http"},
    "claude": {"command": "claude -p {prompt}"},
    "codex": {"command": "codex exec {prompt}"},
    "hermes": {"command": "hermes --prompt {prompt}"},
}

def detect_provider(explicit=None):  # -> (name, descriptor) or (None, None)
def ask_llm(prompt, provider=None, command=None, timeout=120):  # -> str
def parse_json_response(text):  # -> dict, raises ValueError if none
```

- `detect_provider()` probes Ollama at `127.0.0.1:11434/api/tags` first, then
  uses `shutil.which()` for `claude`, `codex`, `hermes`. `explicit` selects a
  named preset; `--brief-command 'tool --flag {prompt}'` overrides any preset.
- `ask_llm()` splits the command template with `shlex.split()`, replaces the
  `{prompt}` token with a single argument, runs it with `subprocess.run` (never
  `shell=True`), captures stdout with a 120-second timeout, and raises a clear
  error on failure.
- `parse_json_response()` strips ```json fences and extracts the first
  balanced `{...}` object so chatty agent output still works.
- If no provider is available, `parse_brief()` falls back to a deterministic
  keyword parser and sets `"source": "heuristic"` plus a warning.

### 2. Rules contract

Saved as `brief_rules.json` in the project directory (batch) or live session
directory:

```json
{
  "goal": "short honest reaction to the new phone camera",
  "must_mention": ["camera", "night mode"],
  "banned": ["spoiler", "another brand"],
  "min_len": 15, "max_len": 60, "aspect": "9:16",
  "cta": "link in bio", "tone": "energetic",
  "source": "llm", "provider": "claude",
  "brief_excerpt": "first 800 chars of the pasted brief"
}
```

All fields are always present. Missing values use defaults; `must_mention` and
`banned` are lists of lowercased strings.

### 3. Parsing prompt

A single prompt asks for strict JSON with exactly the fields above. It includes
the full brief (up to 8000 chars). The LLM is told to infer missing values from
context and to keep phrases short and literal. The heuristic fallback scans for
`must/include`, `avoid/no/don't`, duration patterns like `15-30s`, `9:16`, and
`CTA/call to action`.

### 4. Enforcement

`apply_rules(candidates, rules, workdir, provider)`:

1. Drop candidates whose text contains any `banned` phrase.
2. Require every `must_mention` phrase (token-set match, case-insensitive).
3. Clamp candidate durations to `min_len`/`max_len` and set `aspect` from rules.
4. Rank surviving candidates by a **single batched LLM call**: send all candidate
   texts, ask for `{"scores":[{"index":N,"score":0-10,"reason":"..."}]}`.
   If the LLM call fails, fall back to the existing heuristic score.
5. Return `(selected_candidates, scores_by_index)`.

For **live**, the LLM is used only to parse the brief; gating is deterministic
against the rolling transcript window (banned/must-mention + existing hype
score) so no subprocess runs in the real-time path.

### 5. Interfaces

- CLI: `--brief TEXT`, `--brief-file PATH`, `--brief-provider NAME`,
  `--brief-command TEMPLATE`. Mutually exclusive brief text/file; brief mode is
  additive to normal scoring.
- Studio: a "Campaign brief" textarea on the new-project form and the Live
  form; the selected provider is shown after parsing. `brief_rules.json` is the
  visible artifact.
- Batch: `score_clips` loads `brief_rules.json` from the workdir before
  selection and applies the rules.
- Live: `LiveSession` accepts a brief; it parses once at start and stores
  `brief_rules.json`; `HypeDetector` receives the rules.

### 6. Error handling

- Missing provider → heuristic rules + warning; processing continues.
- LLM timeout/malformed JSON → heuristic rules + warning; never aborts a job.
- Empty brief → error asking for text.
- Oversized brief → truncated to 8000 chars with a note in `brief_excerpt`.

## Testing

- Unit tests: provider detection (monkeypatched `which`/HTTP), JSON fence
  stripping, heuristic fallback, rule validation/clamping, banned filter,
  must-mention matching, batched score parsing.
- Integration test with a fake CLI agent script that prints JSON, verifying
  `parse_brief()` uses it and writes the expected rules.
- Existing suite must stay green; brief mode is opt-in.

## Docs

- `docs/CLI.md`: new flags.
- `docs/WORKFLOWS.md`: campaign-brief workflow and rules file.
- `README.md` feature table.
- `CHANGELOG.md` `[Unreleased] → Added`.
