# Chopify Studio Implementation Plan

**Goal:** Deliver local input, resumable projects, batch queue, dashboard, caption
editing/SRT, brand presets, semantic search, split-screen, and updated discovery docs.

**Architecture:** Reuse the existing transcription/scoring/rendering modules from
a shared project runner. Serve a dependency-free dashboard with Python's HTTP
server and a serialized background executor. Persist project state as JSON.

**Tech stack:** Python 3.11+, stdlib HTTP/JSON, ffmpeg, existing OpenCV/Whisper,
optional local Ollama embeddings, plain HTML/CSS/JavaScript.

## Constraints

- Keep existing CLI flags and default behavior compatible.
- Bind dashboard to 127.0.0.1 and restrict mutations and file serving.
- No paid services or external browser assets.
- Validate finite times, safe project IDs, brand configuration and output paths.

## Tasks (execute inline)

- [x] 1. Write and run failing tests for source selection, stage invalidation,
  transcript edits, embedding ranking, batch isolation, presets and SRT output.
  Files: tests/test_studio.py; interfaces: projects.run_project/create_project,
  captions.update_transcript/write_srt, semantic.search, branding.load_brand.
- [x] 2. Implement captions.py, branding.py, semantic.py and projects.py; use
  atomic JSON, input fingerprints, per-stage signatures and per-clip render cache.
  Run `.venv/Scripts/python -m pytest tests/test_studio.py -q`.
- [x] 3. Integrate local inputs and new flags in chopify.py and the standalone
  stage commands. Extend render_clips.py for logo, styled captions, SRT and
  two-panel vertical filtergraphs. Test real ffmpeg output and failure exit codes.
- [x] 4. Write HTTP tests, then dashboard.py and assets/studio UI; expose project
  prepare/render/preview, selection and timing edits, persistent queue and brands.
  Verify HTTP behavior, keyboard controls, browser errors and responsive layout.
- [x] 5. Rewrite README/llms.txt and task docs; add static docs/index.html,
  structured data, sitemap/robots, Pages workflow and changelog. Validate commands
  and metadata against the implementation.
- [x] 6. Run full tests/compile/CLI checks and synthetic rendering; review diffs,
  fix issues, commit and push to GitHub; verify remote commit matches local HEAD.
