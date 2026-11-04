# Contributing to Chopify

Thanks for helping make local video clipping better. Bug reports, docs fixes,
caption presets, and small pull requests are all welcome.

## Before you start

- Search [issues](https://github.com/mehbul/chopify/issues) and
  [discussions](https://github.com/mehbul/chopify/discussions) first.
- For a large feature or a behavior change, open an issue or start a discussion
  before writing code. It saves you from building something that cannot merge.
- Never paste API keys, cookies, tokens, or private footage links into an issue.
  Redact paths and URLs from logs.

## Set up a development checkout

The one-command installer is the fastest path:

```powershell
# Windows
irm https://raw.githubusercontent.com/mehbul/chopify/main/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/mehbul/chopify/main/install.sh | bash
```

For a repository you already cloned:

```powershell
# Windows
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python dashboard.py
```

```bash
# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python dashboard.py
```

You need **Python 3.11+** and **ffmpeg/ffprobe** with libx264, AAC, and libass
support. `python chopify.py --help` and the dashboard preflight both report
missing pieces.

## Run the tests

```bash
python -m pytest -q
```

The suite uses real ffmpeg renders when ffmpeg is available. Whisper models and
Ollama are not required. Run the suite before opening a pull request, and add a
test for any behavior change that can regress.

Useful checks:

```bash
python -m compileall -q chopify.py dashboard.py projects.py captions.py branding.py semantic.py download_and_transcribe.py score_clips.py render_clips.py tighten.py tools
python tools/build_site.py --out _site
```

## Project map

| File | Responsibility |
| --- | --- |
| `chopify.py` | CLI entry point, flags, export copy |
| `dashboard.py` | Local HTTP server and Studio API |
| `projects.py` | Resumable project state, stage caching, render loop |
| `download_and_transcribe.py` | yt-dlp download, Whisper, chunked transcripts |
| `ingest.py` | Source probing and bounded downloads |
| `sections.py` | Long-source section planning and full-source fallback |
| `score_clips.py` | Candidate discovery, evidence, selection |
| `evidence.py` / `visual.py` | Local audio and visual evidence |
| `render_clips.py` | ffmpeg render, captions, posters, metadata |
| `captions.py` | Caption presets, ASS/SRT generation, transcript edits |
| `branding.py` / `semantic.py` | Brand presets and local embeddings |
| `assets/studio/` | Studio HTML, CSS, and JavaScript |
| `docs/` | User, API, troubleshooting, and product documentation |

Keep changes consistent with the surrounding style: standard library first,
small functions, explicit errors, no network calls in tests, and no new required
service. Everything must keep working fully offline for local files.

## Pull requests

1. Fork the repo and branch from `main` (`fix/short-name`, `feat/short-name`).
2. Make the smallest change that solves the problem.
3. Add or update tests and documentation.
4. Run `python -m pytest -q` and the compile check.
5. Open the PR with what changed, why, and how you tested it.

Guidelines:

- Preserve CLI behavior, brand JSON compatibility, caption presets, and the
  default 16:9 render appearance unless the PR intentionally changes them.
- Do not commit videos, transcripts, generated clips, `.venv`, models, or local
  brand presets.
- Do not add a paid API, cloud upload, telemetry, or a required API key.
- Keep new dependencies optional or justify them in the PR.
- Screenshots or a short clip help for caption, layout, and Studio changes.

## Commit messages

Use a short imperative subject, then a body explaining why when it is not
obvious. Examples:

```
fix: keep the full source when a section download stalls
docs: document the one-command installer
feat: add a --no-srt export flag
```

## Good first issues

Issues labeled [`good first issue`](https://github.com/mehbul/chopify/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
are scoped to be finished in one sitting. Comment on the issue to claim it and
ask questions early. If you get stuck, say so in the issue instead of abandoning
the work silently.

## Review and merge

Maintainers aim to answer every issue and PR within a day. Reviews focus on
correctness, regressions, tests, and keeping the local-first contract. A PR may
be asked to split if it mixes unrelated changes.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
