# Creator Evidence Phase 2B: Visual Action, Silent Sources, and Boundaries

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make gameplay, PC-game VODs, and muted action sources first-class by adding a local visual-evidence pass, so clips can pass on motion, cuts, reactions, and a complete action arc even when dialogue and game audio are sparse or silent.

**Architecture:** A new `visual.py` extracts 2 fps 160x90 frames with ffmpeg and computes motion, scene/UI change, facecam activity, action arcs, and boundary troughs with OpenCV. A sparse 12-sample baseline sets the visual source energy before discovery. Candidate intervals are analyzed only after the audio shortlist, cached by source identity and range, blended with audio evidence, and re-scored. Wordless sources use audio peaks, visual variation, or a coarse grid as seeds. A final action-only boundary pass snaps media windows to loudness/motion troughs without touching spoken words.

**Tech Stack:** Python 3.11+, stdlib subprocess/json/math, `numpy`, `opencv-python` (already required), existing ffmpeg, pytest.

## Global Constraints

- Fully local. No paid API, cloud upload, hosted service, or API key.
- Preserve the CLI, brand JSON, caption presets, and default 16:9 render.
- Preserve Phase 1/2A behavior: default minimum 8.0 inclusive, every passing clip returned, AI-chosen duration, text-only fallback for missing sources.
- Visual extraction failure is non-fatal: fall back to audio, then text.
- Do not analyze the whole source for every candidate. Sample a sparse baseline, shortlist, merge padded candidate ranges, then analyze those intervals.
- Do not cut a spoken word. Boundary refinement applies only to wordless media candidates in this phase.
- Audio and visual cache files are versioned by source identity.
- No push until asked.

## Acceptance

- A flat visual sequence cannot pass 8.0 on motion alone; it is capped at evidence 6.0.
- A synthetic action arc (rise, peak, decay) scores above a flat sequence.
- A silent, wordless video with visual variation produces candidates and reports `evidence.visual`.
- A silent, wordless, visually flat source fails with `No speech and no media evidence; this source cannot be scored.`
- A silent track with usable visual evidence is treated as audio-unavailable, not as score 0.
- Existing 238 tests still pass, plus the new tests.
- The real 12-minute transcript still produces at least one 8.0+ clip.

---

### Task 1: Visual Feature Timeline and Cache

**Files:**
- Create: `visual.py`
- Create: `tests/test_visual.py`
- Modify: `requirements.txt` if `opencv-python` or `numpy` is missing.

**Interfaces:**
- Produces: `VisualFeatures`, `features_from_frames`, `extract_features`, `load_or_analyze`, `visual_features_for`.

- [ ] **Step 1: Write failing extraction tests**

```python
import numpy as np
import pytest


def test_flat_frames_have_no_motion_or_cuts():
    import visual
    frames = [np.zeros((90, 160, 3), dtype=np.uint8) for _ in range(6)]
    features = visual.features_from_frames(frames, start=10.0)
    assert features.start == 10.0
    assert np.allclose(features.motion, 0.0)
    assert not features.cut.any()


def test_a_hard_cut_and_moving_block_are_detected():
    import visual
    frames = []
    for i in range(6):
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        if i >= 3:
            frame[:] = 255
        frames.append(frame)
    features = visual.features_from_frames(frames, start=0.0)
    assert features.cut[3]
    assert features.change[3] > features.change[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visual.py -q`
Expected: import failure because `visual.py` does not exist.

- [ ] **Step 3: Implement `visual.py` extraction**

Use this data model:

```python
@dataclass
class VisualFeatures:
    start: float
    end: float
    times: np.ndarray       # absolute seconds, one per sampled frame
    motion: np.ndarray      # 0..1 mean absolute grayscale difference / 64
    cut: np.ndarray         # hard histogram cut flag
    change: np.ndarray      # 0..1 full-frame histogram distance
    center_change: np.ndarray
    face: np.ndarray        # bool
    face_motion: np.ndarray # 0..1
    baseline: np.ndarray | None = None
```

Requirements:

- `SAMPLE_FPS = 2`, `FRAME_WIDTH = 160`, `FRAME_HEIGHT = 90`, `FEATURE_VERSION = 1`.
- `features_from_frames(frames, start=0.0)` processes RGB uint8 frames without
  ffmpeg. It is the unit-test path.
- Motion is the mean absolute difference from the previous grayscale frame,
  divided by 64 and clamped to 0..1. The first frame is 0.
- Histogram distance uses a 32-bin normalized grayscale histogram and the L1
  distance divided by 2. A full-frame change above 0.45 is a cut.
- `center_change` uses the top-center crop: top 40% and central 60% width.
- Face presence uses OpenCV's bundled Haar frontal-face cascade at
  `minSize=(12, 12)`. Face motion compares the face center and area with the
  previous frame; no face means 0.
- `baseline = _rolling_median(motion, window=61)` (30 seconds at 2 fps).
- `extract_features(source, start, end)` streams RGB frames from ffmpeg:
  `-ss start -t duration -i source -vf fps=2,scale=160:90 -pix_fmt rgb24 -f rawvideo -`.
  Return `None` on a missing file, ffmpeg failure, or no frames.
- `cache_key(source, start, end)` includes `FEATURE_VERSION`, source identity
  from `evidence.source_identity`, and rounded range.
- `load_or_analyze(source, workdir, ranges)` returns a list of
  `VisualFeatures` for the requested ranges. Cache each as
  `workdir/visual_sections/<sha256>.npz`; include identity, version, start,
  end, and all arrays. Never raise on cache corruption; re-analyze.
- `visual_features_for(sections, start, end)` returns the section containing the
  range, or `None`.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visual.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add visual.py tests/test_visual.py requirements.txt
git commit -m "feat: extract local visual evidence features"
```

---

### Task 2: Visual Evidence, Flat-Action Cap, and Facecam Neutrality

**Files:**
- Modify: `visual.py`
- Modify: `tests/test_visual.py`

**Interfaces:**
- Produces: `visual_evidence(features, start, end)`, `combine_visual`, and the
  flat-action cap.

- [ ] **Step 1: Write failing scoring tests**

```python
def test_flat_motion_is_capped_below_action_evidence():
    import visual
    features = visual.features_from_frames([...] * 6, start=0.0)
    got = visual.visual_evidence(features, 0.0, features.end)
    assert got["score"] <= 6.0


def test_action_arc_beats_flat_motion():
    import visual
    rising = visual.features_from_frames([...], start=0.0)
    flat = visual.features_from_frames([...], start=0.0)
    assert visual.visual_evidence(rising, 0.0, rising.end)["score"] > \
        visual.visual_evidence(flat, 0.0, flat.end)["score"]


def test_missing_facecam_is_neutral():
    import visual
    features = visual.features_from_frames([...], start=0.0)
    got = visual.visual_evidence(features, 0.0, features.end)
    assert got["facecam"] == pytest.approx(0.5)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visual.py -q`
Expected: `visual_evidence` is missing.

- [ ] **Step 3: Implement `visual_evidence`**

```text
visual_score = 10 * (0.25 motion_spike + 0.20 scene_ui + 0.20 facecam
                   + 0.25 event_arc + 0.10 boundary_quiet)
```

- `motion_spike`: candidate 90th-percentile motion against
  `max(section median, rolling baseline at the peak)`, normalized by 0.20.
- `scene_ui`: candidate 90th percentile of `max(change, center_change)`,
  normalized by 0.15..0.50.
- `facecam`: neutral 0.5 when fewer than 15% of section frames contain a face.
  Otherwise `0.5 + 0.5 * clamp(0.6 * face_presence + 0.4 * face_motion)`.
- `event_arc` uses the audio shape:
  `0.35 * rise + 0.45 * peak + 0.20 * max(decay, reaction)`.
  Rise compares the middle third with the first third; peak compares the
  candidate peak with the local baseline; reaction uses face motion and scene
  change after the peak.
- `boundary_quiet`: average of start/end quietness within ±1.5 seconds of each
  boundary, where quietness is 1 at the local minimum and 0 at the local
  maximum.
- The flat-action cap: when `event_arc < 0.30`, cap the visual score at 6.0.
- Return the same shape as `evidence.audio_evidence`: `score`, component keys,
  and `peak_second`. Return neutral 5.0 for a missing section or a range with
  fewer than two frames.
- `combine_visual(audio, visual)` delegates to `evidence.combine_evidence` so
  one modality still shrinks 15% toward 5.0.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visual.py tests/test_evidence.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add visual.py tests/test_visual.py
git commit -m "feat: score visual action arcs and reactions"
```

---

### Task 3: Sparse Visual Baseline, Profile, and Silent Audio

**Files:**
- Modify: `visual.py`
- Modify: `evidence.py`
- Modify: `score_clips.py`
- Modify: `tests/test_visual.py`
- Modify: `tests/test_chopify.py`

**Interfaces:**
- Produces: `sample_baseline(source, workdir, duration)`,
  `VisualBaseline`, `evidence.timeline_has_signal`,
  `score_clips.source_evidence` with visual baseline and usable audio.

- [ ] **Step 1: Write failing tests**

```python
def test_flat_audio_is_unavailable_not_zero():
    import evidence
    timeline = evidence.features_from_samples(
        np.full(16000 * 5, 0.001, dtype=np.float32))
    assert not evidence.timeline_has_signal(timeline)
    assert evidence.timeline_has_signal(
        evidence.features_from_samples(
            np.concatenate([np.full(80000, 0.001, dtype=np.float32),
                            np.full(16000, 0.8, dtype=np.float32)])))


def test_visual_baseline_raises_source_energy():
    import evidence
    assert evidence.source_energy(0.2, 0.9) > evidence.source_energy(0.2, 0.1)
    assert evidence.content_profile(0.4, 0.8, 0, 0) == "gameplay/action"
```

- [ ] **Step 2: Implement baseline sampling**

- `BASELINE_SAMPLES = 12`, `BASELINE_SECONDS = 5.0`.
- Choose up to 12 centered five-second windows evenly across the source.
- Extract each with `extract_features`; combine frame-level values.
- `motion_norm = clamp(mean_motion / 0.25, 0, 1)` for 2 fps 160x90 frames.
- `cut_norm = clamp(cut_rate_per_minute / 30, 0, 1)`.
- `facecam_norm = mean(face)` over sampled frames.
- `visual_baseline = clamp(0.5 * motion_norm + 0.3 * cut_norm
  + 0.2 * facecam_norm, 0, 1)`.
- Cache in `workdir/visual_baseline.json` keyed by source identity and
  `FEATURE_VERSION`; return `None` when the source is missing or no sample
  decodes.
- `VisualBaseline` includes `value`, `motion_norm`, `cut_rate`,
  `facecam_norm`, and `samples`.

- [ ] **Step 3: Integrate into source evidence**

- Add `evidence.timeline_has_signal(timeline)`: false for missing/empty
  timelines, or when `variability < 0.02` and `burst_rate == 0`.
- `score_clips.source_evidence(workdir, data)` also loads the visual baseline
  and returns `(timeline, profile, energy, speech_density, audio_usable,
  visual_baseline)`.
- Pass `visual_baseline.value` as the visual component of
  `content_profile` and `source_energy`.
- Set `evidence_available.audio` from usable audio, not merely a decoded
  timeline. A silent source then records audio unavailable.
- Missing sources keep text-only neutral evidence.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visual.py tests/test_chopify.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add visual.py evidence.py score_clips.py tests/test_visual.py tests/test_chopify.py
git commit -m "feat: measure visual source energy and silent audio"
```

---

### Task 4: Visual Shortlist, Blended Evidence, Grid Seeds, and Boundaries

**Files:**
- Modify: `visual.py`
- Modify: `score_clips.py`
- Modify: `tests/test_visual.py`
- Modify: `tests/test_chopify.py`

**Interfaces:**
- Produces: `merge_ranges`, `refine_media_boundary`,
  `score_clips.attach_media_evidence`, visual shortlist, and silent gameplay
  discovery.

- [ ] **Step 1: Write failing integration tests**

```python
def test_wordless_silent_source_uses_visual_evidence(tmp_path, monkeypatch):
    import visual
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 20.0)
    monkeypatch.setattr(visual, "sample_baseline",
                        lambda *a, **k: visual.VisualBaseline(0.7, 0.8, 0, 0, 4))
    monkeypatch.setattr(visual, "load_or_analyze",
                        lambda *a, **k: [synthetic_action_features(0, 20)])
    segs, _ = score_clips.run(wd, min_score=0, min_len=4, max_len=10)
    assert segs
    assert all(s["evidence"]["audio"] is None for s in segs)
    assert any(s["evidence"]["visual"] is not None for s in segs)


def test_silent_flat_source_fails_without_visual_evidence(tmp_path, monkeypatch):
    import visual
    source = tmp_path / "flat.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 20.0)
    monkeypatch.setattr(visual, "sample_baseline", lambda *a, **k: None)
    with pytest.raises(ValueError, match="No speech and no media evidence"):
        score_clips.run(wd, min_score=8.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chopify.py -q -k "silent_source_uses_visual or silent_flat_source"`
Expected: failures because visual evidence is not wired into discovery.

- [ ] **Step 3: Add range planning and boundary helpers**

- `merge_ranges(candidates, duration, pad=2.0)` returns merged
  `(start, end)` intervals clamped to source bounds. A gap of two seconds or
  less is merged.
- `refine_media_boundary(features, timeline, start, end, min_len, max_len)`:
  - Only called for wordless candidates.
  - Move the start to the nearest loudness rise, motion spike, or cut within
    1.5 seconds; prefer the earliest onset that is still inside the range.
  - Move the end to the nearest trough or post-peak reaction within 1.5
    seconds; prefer resolution over wind-up.
  - Keep the result inside `[min_len, max_len]` and inside the section.
  - Return the original range when no confident boundary exists.

- [ ] **Step 4: Wire visual analysis into `discover()`**

Order:

1. Load transcript, audio timeline, and visual baseline.
2. Compute profile and energy from both baselines.
3. Run text or media discovery.
4. Attach audio evidence using only usable audio.
5. Build the shortlist with
   `potential = text_score + audio_adjustment + max_visual_bonus`.
   `max_visual_bonus` is `5.0 * 0.6 * w_e * 0.4` with audio and
   `5.0 * 0.6 * w_e` without it. Keep candidates with
   `potential >= 8.0`; always keep media candidates.
6. Merge padded ranges, call `visual.load_or_analyze`, and refine wordless
   media boundaries.
7. Reapply evidence with `evidence.apply_evidence(audio=..., visual=...)`,
   using the larger audio/visual action arc.
8. Persist `evidence_available.visual`, warnings, and the visual baseline in
   `stats`. Save through `save_candidates`.

- [ ] **Step 5: Allow silent gameplay and grid seeds**

- `discover_media_candidates` accepts `audio_timeline`, `visual_baseline`, and
  `audio_usable`. Audio peaks seed when available; otherwise a varying visual
  baseline permits grid seeds; only a flat audio and flat visual source raises
  the exact failure.
- A media candidate still floors text at 7.0 and uses `words_per_second=0.0`.
- If audio is unavailable, pass `audio=None`; do not record score 0.
- Keep the event/action hook wording.

- [ ] **Step 6: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visual.py tests/test_chopify.py tests/test_evidence.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add visual.py score_clips.py tests/test_visual.py tests/test_chopify.py
git commit -m "feat: blend visual evidence into clip discovery"
```

---

### Task 5: Docs, Real Run, and Phase 2B Exit

**Files:**
- Modify: `README.md`
- Modify: `docs/WORKFLOWS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document visual evidence**

README and WORKFLOWS must state:

- Visual analysis runs locally at 2 fps and never uploads frames.
- Only sparse baseline samples and shortlisted padded candidate ranges are
  decoded; results are cached by source identity and range.
- Motion, scene/UI changes, facecam reactions, action arcs, and boundary troughs
  contribute; flat motion is capped at 6.0.
- Silent and muted gameplay uses visual seeds and visual-only evidence.
- Visual failure falls back to audio, then text.
- Boundary refinement applies to wordless action windows and never cuts spoken
  words.

CHANGELOG adds visual evidence, silent gameplay, grid seeds, and the flat-action
cap.

- [ ] **Step 2: Run the full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Run the real 12-minute source**

```powershell
$work = "$env:TEMP\opencode\chopify-youtube-test\work"
.\.venv\Scripts\python.exe score_clips.py $work --min-score 8 --preferred-len 45s
```

Expected: at least one 8.0+ clip, `evidence_available` reports audio and visual,
and the run does not crash. The talk profile must not become action-only from
visual sampling.

- [ ] **Step 4: Review the diff**

Check:

- No network calls beyond local Ollama.
- Missing/unreadable sources still score from text.
- `candidates.json` remains an endpoint list.
- `segments.json` preserves Phase 1 keys and adds `evidence`.
- Existing `max_clips` and threshold behavior still win.
- Cache files stay inside the project work directory.

- [ ] **Step 5: Commit docs and merge**

```powershell
git add README.md docs/WORKFLOWS.md CHANGELOG.md
git commit -m "docs: document visual action evidence"
git checkout main
git merge --no-ff feat/creator-evidence-visual
.\.venv\Scripts\python.exe -m pytest -q
```

Do not push until asked.

---

## Deferred to Phase 3

- Long URL audio-first download and low-resolution visual evidence sections.
- Chunked, resumable transcription.
- Block scanning and section rendering.
