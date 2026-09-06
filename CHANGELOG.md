# Changelog

All notable changes to Chopify will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.0] - 2026-09-06

### Added
- **Complete-thought clips**: every clip is grown or trimmed to end on a sentence
  with terminal punctuation, so clips never start or end mid-thought.
- **Keyword search** (`--search "pricing"`): bypass virality scoring and build
  clips around transcript sentences containing all your keywords.
- **Review workflow** (`--clips 1,3,5`): scoring prints a numbered candidate
  table; render only the rows you pick.
- **Preview drafts** (`--preview`): render fast 480p cuts (no poster or
  metadata) to check your picks before the full-quality render.
- **Dependency pre-flight check**: before any stage, verifies ffmpeg/ffprobe are
  on PATH, the ffmpeg build can encode H.264 (libx264) and AAC, and required
  Python packages are installed — each problem reported with a fix hint. Bypass
  with `--no-preflight`.

### Changed
- Candidate selection now shares a single window-growth routine
  (`_grow_window`) between virality scoring and keyword search, both enforcing
  the complete-thought rule.

[Unreleased]: https://github.com/mehbul/chopify/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/mehbul/chopify/releases/tag/v1.3.0
