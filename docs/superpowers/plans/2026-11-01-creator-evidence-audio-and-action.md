# Creator Evidence Phase 2A: Audio Timeline and Action Sources

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully local audio-evidence pass so speech-sparse, action-only, and gameplay sources can produce clips, while keeping transcript-only projects and the Phase 1 selection contract unchanged.

**Architecture:** A new `evidence.py` builds one cached 1 Hz audio timeline per source and scores candidate ranges against global and rolling baselines. `score_clips.discover()` measures the source profile and energy before discovery, applies audio evidence to every candidate, and builds media-only candidates from audio event peaks when a source has no words. Selection still dedupes, thresholds, ranks, and caps the same way.

**Tech Stack:** Python 3.11+, stdlib subprocess/json/math, `numpy` for the audio FFT/RMS timeline, existing ffmpeg, pytest.

## Global Constraints

- Fully local: no paid APIs, cloud uploads, hosted services, or API keys.
- Preserve existing CLI flag names, brand JSON, caption presets, and default 16:9 render output.
- `candidates.json` remains the threshold-independent endpoint list; `segments.json` remains the render contract.
- A project whose source is missing or unreadable must fall back to transcript-only scoring, not fail.
- Existing tests use a fake source path; evidence must be optional and neutral when unavailable.
- Default minimum score stays `8.0`, inclusive, displayed as `8.0+`.
- Do not change Phase 1 scoring calibration, duration ladder, dedupe rules, or CLI prompting.
- Evidence adds to text quality; it never replaces it.
- No OCR, game classifier, cloud model, or network call is introduced.

---

## File Map

| File | Responsibility |
| --- | --- |
| `evidence.py` (new) | Audio decode, 1 Hz features, cache, baselines, evidence scoring, profile/weight math. |
| `score_clips.py` | Discovery integration, media-only candidates, candidate evidence fields, selection stats. |
| `projects.py` | Evidence version in the discovery cache key; no-speech source handling. |
| `requirements.txt` | Declare `numpy`. |
| `tests/test_evidence.py` (new) | Pure feature and evidence tests. |
| `tests/test_chopify.py` | Discovery/selection/no-speech integration tests. |
| `tests/test_studio.py` | Cache-key and project-state tests. |
| `README.md`, `docs/WORKFLOWS.md`, `CHANGELOG.md` | Document evidence, profiles, and gameplay support. |

## Shared Interfaces

```python
# evidence.py
EVIDENCE_VERSION = 1
SAMPLE_RATE = 16000

@dataclass
class AudioTimeline:
    rms: np.ndarray          # dBFS, one value per second
    peak: np.ndarray         # dBFS, one value per second
    flux: np.ndarray         # 0-1 spectral flux
    zcr: np.ndarray          # 0-1 zero-crossing rate
    high_band: np.ndarray    # 0-1 energy above 4 kHz
    burst: np.ndarray        # bool, above global or rolling baseline + 8 dB
    duration: float

    @property
    def variability(self) -> float: ...
    @property
    def burst_rate(self) -> float: ...
    def baseline_at(self, second: int) -> float: ...
    def peak_times(self, min_gap=10.0) -> list[float]: ...
    def trough_near(self, second: float, radius=1.5) -> float: ...
    def score(self, start: float, end: float) -> dict: ...

def features_from_samples(samples) -> AudioTimeline
def build_audio_timeline(source) -> AudioTimeline | None
def load_audio_timeline(source, workdir, identity=None) -> AudioTimeline | None
def save_timeline(timeline, path, identity="") -> None
def load_timeline(path, identity=None) -> AudioTimeline | None

PROFILE_FLOORS = {"talk": 0.20, "mixed": 0.35, "gameplay/action": 0.60}
PROFILE_BANDS = {"talk": (45, 180), "mixed": (30, 120), "gameplay/action": (15, 60)}

def content_profile(speech_density, visual_baseline, cut_rate_per_min,
                    burst_rate) -> str
def profile_band(profile) -> tuple[int, int]
def source_energy(audio_variability, visual_baseline=None) -> float
def evidence_weight(source_energy, profile, sparse=False) -> float
def audio_evidence(timeline, start, end) -> dict
def combine_evidence(audio=None, visual=None) -> float
def apply_evidence(candidate, audio=None, visual=None, profile="mixed",
                   source_energy=0.5, words_per_second=None) -> dict
```

---

### Task 1: Audio Timeline and Cache

**Files:**
- Create: `evidence.py`
- Create: `tests/test_evidence.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `features_from_samples`, `AudioTimeline`, `build_audio_timeline`, `save_timeline`, `load_timeline`, `load_audio_timeline`.

- [ ] **Step 1: Write the failing feature tests**

```python
import numpy as np
import pytest

import evidence


def tone(seconds, amplitude=0.001):
    t = np.arange(int(evidence.SAMPLE_RATE * seconds)) / evidence.SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def noise(seconds, amplitude, seed=7):
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(int(evidence.SAMPLE_RATE * seconds))
            ).astype(np.float32)


def test_timeline_is_one_value_per_second():
    samples = np.concatenate([tone(2), noise(1, 0.8), tone(2)])
    timeline = evidence.features_from_samples(samples)
    assert timeline.duration == pytest.approx(5.0)
    assert len(timeline.rms) == 5
    assert len(timeline.burst) == 5


def test_loud_burst_stands_out_from_quiet_source():
    samples = np.concatenate([tone(3), noise(1, 0.8), tone(2)])
    timeline = evidence.features_from_samples(samples)
    assert timeline.rms[3] > timeline.rms[0] + 20
    assert timeline.burst[3]
    assert not timeline.burst[0]


def test_spectral_flux_and_high_band_capture_transients():
    samples = np.concatenate([tone(2), noise(1, 0.8), tone(2)])
    timeline = evidence.features_from_samples(samples)
    assert timeline.flux[3] > timeline.flux[0]
    assert timeline.high_band[3] > timeline.high_band[0]


def test_timeline_cache_round_trip(tmp_path):
    timeline = evidence.features_from_samples(np.concatenate([tone(2), noise(1, 0.5)]))
    path = tmp_path / "audio_features.npz"
    evidence.save_timeline(timeline, path, identity="abc")
    loaded = evidence.load_timeline(path, identity="abc")
    assert loaded is not None
    assert loaded.duration == pytest.approx(timeline.duration)
    assert np.allclose(loaded.rms, timeline.rms)
    assert evidence.load_timeline(path, identity="other") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`

Expected: `ModuleNotFoundError: No module named 'evidence'`.

- [ ] **Step 3: Implement `evidence.py`**

Implementation requirements:

- `FEATURE_VERSION = 1`, `SAMPLE_RATE = 16000`, `FRAME_SAMPLES = 800` (50 ms).
- `_db(value)` returns `20 * log10(value + 1e-9)` clamped at `-80.0`.
- `features_from_samples(samples)`:
  - Convert to mono `np.float32`; return an empty timeline when there are no samples.
  - Split into 800-sample frames; pad the last frame with zeros only for FFT stability, but derive `duration` from the real sample count.
  - Per frame compute RMS dBFS, peak dBFS, spectral flux against the previous magnitude spectrum, zero-crossing rate, and high-band ratio above 4 kHz.
  - Aggregate frames into one row per second with mean for RMS/flux/ZCR/high-band and max for peak. Pad the final partial second with the available frame means.
  - Compute `burst`: RMS above `max(global_p50 + 8, rolling_baseline + 8)`. The rolling baseline is a centered median over at most 60 seconds.
- `build_audio_timeline(source)`:
  - Return `None` when `source` is missing, ffmpeg fails, or there are no samples.
  - Run `ffmpeg -nostdin -v error -i <source> -vn -ac 1 -ar 16000 -f f32le -` and read stdout in 1 MiB blocks so multi-hour sources never load fully into memory.
  - Feed decoded blocks into `features_from_samples`; do not decode video.
- `save_timeline` writes `np.savez_compressed` with `version`, `identity`, `duration`, and every array; use a `.tmp` file and `replace`.
- `load_timeline` returns `None` when the file is missing, corrupt, has another version, or an identity mismatch.
- `load_audio_timeline(source, workdir, identity=None)`:
  - Cache at `Path(workdir) / "audio_features.npz"`.
  - Reuse a valid cache; otherwise build once and save.
  - Never raise for missing source or failed decode; return `None`.

- [ ] **Step 4: Add numpy to requirements**

```text
numpy
```

Place it with the other runtime dependencies and keep the existing comment style.

- [ ] **Step 5: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`

Expected: all pass.

- [ ] **Step 6: Add a real-decode test**

Append to `tests/test_evidence.py`:

```python
import shutil
import subprocess


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_build_audio_timeline_decodes_a_wav(tmp_path):
    wav = tmp_path / "input.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "sine=frequency=220:duration=2", str(wav),
    ], check=True)
    timeline = evidence.build_audio_timeline(wav)
    assert timeline is not None
    assert timeline.duration == pytest.approx(2.0, abs=0.1)
    assert len(timeline.rms) == 2
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`

- [ ] **Step 7: Commit**

```powershell
git add evidence.py tests/test_evidence.py requirements.txt
git commit -m "feat: build cached audio evidence timelines"
```

---

### Task 2: Audio Evidence, Profiles, and Weights

**Files:**
- Modify: `evidence.py`
- Modify: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `AudioTimeline` from Task 1.
- Produces: `audio_evidence`, `content_profile`, `profile_band`, `source_energy`, `evidence_weight`, `combine_evidence`, `apply_evidence`.

- [ ] **Step 1: Write failing evidence tests**

Append to `tests/test_evidence.py`:

```python
def candidate(start, end, score=7.5):
    return {"start": start, "end": end, "duration": end - start,
            "overall": score, "raw_score": score, "reason": "text"}


def test_audio_evidence_prefers_a_loud_reaction():
    samples = np.concatenate([tone(4), noise(1, 0.8), tone(4)])
    timeline = evidence.features_from_samples(samples)
    quiet = evidence.audio_evidence(timeline, 0, 4)
    loud = evidence.audio_evidence(timeline, 3, 5)
    assert loud["score"] > quiet["score"] + 1.0
    assert 0 <= loud["score"] <= 10


def test_audio_evidence_is_neutral_for_missing_timeline():
    got = evidence.audio_evidence(None, 0, 10)
    assert got["score"] == 5.0


def test_profile_thresholds():
    assert evidence.content_profile(2.5, 0.1, 2, 0.01) == "talk"
    assert evidence.content_profile(0.4, 0.7, 20, 0.2) == "gameplay/action"
    assert evidence.content_profile(1.7, 0.4, 5, 0.05) == "mixed"


def test_profile_bands_and_weight_floor():
    assert evidence.profile_band("talk") == (45, 180)
    assert evidence.profile_band("mixed") == (30, 120)
    assert evidence.profile_band("gameplay/action") == (15, 60)
    assert evidence.evidence_weight(0.0, "talk") == pytest.approx(0.20)
    assert evidence.evidence_weight(1.0, "gameplay/action") == pytest.approx(0.65)
    assert evidence.evidence_weight(0.0, "mixed", sparse=True) == pytest.approx(0.75)


def test_sparse_text_uses_floor_and_raised_weight():
    got = evidence.apply_evidence(
        candidate(0, 10, 4.2), audio=8.0, profile="mixed",
        source_energy=0.5, words_per_second=0.4)
    assert got["raw_score"] == pytest.approx(4.2)
    assert got["overall"] > 7.0
    assert got["evidence"]["weight"] == pytest.approx(0.75)
    assert got["evidence"]["text_floor"] == 7.0


def test_combine_evidence_shrinks_single_modality():
    assert evidence.combine_evidence(8.0, 8.0) == pytest.approx(8.0)
    assert evidence.combine_evidence(8.0, None) == pytest.approx(7.55)
    assert evidence.combine_evidence(None, None) == pytest.approx(5.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`

Expected: `AttributeError` for the new functions.

- [ ] **Step 3: Implement the audio scoring math**

Requirements:

- `smoothstep(value)` clamps to 0..1 and returns `x*x*(3-2x)`.
- `audio_evidence(timeline, start, end)` returns:
  - `score`, `peak_rise`, `burst_density`, `spectral_flux`, `event_arc`,
    `boundary_quiet`, `peak_second`.
  - `score = 10 * (0.25*peak_rise + 0.20*burst_density + 0.20*spectral_flux
    + 0.25*event_arc + 0.10*boundary_quiet)`.
  - `peak_rise` compares the loudest second with `max(global p50, rolling baseline)`.
  - `burst_density = smoothstep(min(1, burst_fraction / 0.25))`.
  - `spectral_flux` compares the candidate's 90th-percentile flux with the
    source p50/p90 spread.
  - `event_arc = 0.35*rise + 0.45*peak + 0.20*closure`; `rise` compares the
    first 30% with the middle 40%; `closure` is the larger of a return toward
    baseline and a post-peak reaction/hold.
  - `boundary_quiet` is high when the first and last seconds sit near a local
    trough compared with the candidate peak.
  - A `None` timeline returns `{"score": 5.0, ...}` with zero components.
- `content_profile` implements the spec thresholds verbatim. Treat an
  unavailable `visual_baseline` as `0.0` for the talk test.
- `profile_band` and `PROFILE_FLOORS` use the exact spec values.
- `source_energy(audio_variability, visual_baseline=None)`:
  - both: `0.6*a + 0.4*v`; one: that value; neither: `0.5`.
  - Clamp to 0..1.
- `evidence_weight(source_energy, profile, sparse=False)`:
  - `max(0.2 + 0.45*source_energy, PROFILE_FLOORS[profile])`, and at least
    `0.75` when `sparse`.
- `combine_evidence`:
  - both: `0.6*a + 0.4*v`.
  - one: `5.0 + 0.85*(score - 5.0)`.
  - neither: `5.0`.
- `apply_evidence`:
  - Start from `raw_score = candidate.get("raw_score", candidate["overall"])`.
  - Use `text_score = max(raw_score, 7.0)` when `words_per_second < 0.8`,
    otherwise `raw_score`.
  - `final = clamp(text_score + (evidence_score - 5.0) * 0.6 * weight, 0, 10)`.
  - Store `overall`, keep `raw_score`, and add:
    ```json
    {"audio": 8.9, "visual": null, "action_arc": 0.86,
     "source_energy": 0.78, "profile": "gameplay/action",
     "weight": 0.60, "score": 8.5, "text_floor": 7.0}
    ```
  - Append at most one short evidence phrase to `reason`, keeping it <= 160
    characters.
  - When both modality scores are `None`, return the candidate with
    `overall == raw_score` and a neutral evidence object.

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add evidence.py tests/test_evidence.py
git commit -m "feat: score clip candidates with local audio evidence"
```

---

### Task 3: Integrate Audio Evidence into Discovery

**Files:**
- Modify: `score_clips.py`
- Modify: `projects.py`
- Modify: `tests/test_chopify.py`
- Modify: `tests/test_studio.py`

**Interfaces:**
- Consumes: `evidence.load_audio_timeline`, `evidence.content_profile`,
  `evidence.source_energy`, `evidence.profile_band`, `evidence.audio_evidence`,
  `evidence.apply_evidence`.
- Produces: evidence fields on every candidate; profile and energy in
  `candidates.json`/`selection.json`; evidence version in the discovery cache key.

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_chopify.py`:

```python
def test_discovery_attaches_audio_evidence(workdir, monkeypatch):
    import evidence
    timeline = evidence.features_from_samples(
        np.concatenate([np.full(16000 * 4, 0.001, dtype=np.float32),
                        np.full(16000, 0.8, dtype=np.float32)]))
    monkeypatch.setattr(evidence, "load_audio_timeline",
                        lambda *a, **k: timeline)
    segs, _ = score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
    candidates = json.loads((workdir / "candidates.json").read_text())["candidates"]
    assert all("evidence" in item for item in candidates)
    assert any(item["evidence"]["audio"] is not None for item in candidates)
    selection = json.loads((workdir / "selection.json").read_text())
    assert selection["evidence_available"]["audio"] is True


def test_missing_source_keeps_neutral_evidence(workdir):
    segs, _ = score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
    selection = json.loads((workdir / "selection.json").read_text())
    assert selection["evidence_available"] == {"audio": False, "visual": False}
    assert segs


def test_ollama_rerank_adjusts_final_not_raw_score(workdir, monkeypatch):
    import evidence
    timeline = evidence.features_from_samples(
        np.full(16000 * 10, 0.2, dtype=np.float32))
    monkeypatch.setattr(evidence, "load_audio_timeline", lambda *a, **k: timeline)
    monkeypatch.setattr(score_clips, "_ollama_chat",
                        lambda *a, **k: [{"index": 0, "adjustment": 0.3}])
    segs, _ = score_clips.run(workdir, llm="test", min_score=0,
                              min_len=4, max_len=20)
    top = max(segs, key=lambda s: s["overall"])
    assert top["overall"] == pytest.approx(top["raw_score"] + 0.3, abs=0.05)
```

`tests/test_chopify.py` already imports `json`, `pytest`, and `score_clips`;
add `import numpy as np` at the top.

Add to `tests/test_studio.py`:

```python
def test_evidence_version_invalidates_discovery_cache():
    import evidence
    import projects
    options = projects.validate_options({})
    base = projects.score_keys("transcript", {"words": []}, options)
    monkeypatched = {**options}
    assert projects.score_keys("transcript", {"words": []}, monkeypatched) == base
    assert evidence.EVIDENCE_VERSION >= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chopify.py::test_discovery_attaches_audio_evidence tests/test_chopify.py::test_missing_source_keeps_neutral_evidence tests/test_studio.py::test_evidence_version_invalidates_discovery_cache -q`

Expected: failures because candidates have no evidence and selection has no
`evidence_available` booleans.

- [ ] **Step 3: Wire evidence into `score_clips.discover()`**

- Import `evidence` at module top.
- Add a helper:
  ```python
  def source_evidence(workdir, data):
      timeline = evidence.load_audio_timeline(data.get("video"), workdir)
      words = data.get("words") or []
      duration = float(data.get("duration") or getattr(timeline, "duration", 0) or 0)
      speech_density = len(words) / duration if duration else 0.0
      variability = timeline.variability if timeline else 0.0
      burst_rate = timeline.burst_rate if timeline else 0.0
      profile = evidence.content_profile(speech_density, 0.0, 0.0, burst_rate)
      energy = evidence.source_energy(variability, None) if timeline else 0.5
      return timeline, profile, energy, speech_density
  ```
- In `discover()`, call `source_evidence()` before discovery and pass
  `band=evidence.profile_band(profile)` to `discover_candidates`.
- After discovery, apply audio evidence to every candidate:
  - `words_per_second = len(candidate text words) / duration` if words exist.
  - Use `candidate["seed_index"]` and the sentence list to count words in the
    window; media candidates report `None`.
  - `evidence.apply_evidence(candidate, audio=..., profile=profile,
    source_energy=energy, words_per_second=...)`.
- Update stats with `content_profile`, `source_energy`, and
  `evidence_available={"audio": timeline is not None, "visual": False}`.
- Preserve `discover_candidates`'s public signature: add only keyword-only
  optional arguments at the end; default behavior stays `band=(30, 120)` and no
  evidence.
- Do not change `build_candidates`.

- [ ] **Step 4: Update selection stats and reranking**

- `select_candidates(..., evidence_available=None)` merges the availability
  dict into stats; default to inferring from candidate records:
  - audio true if any candidate evidence has a non-null `audio`.
  - visual true if any has a non-null `visual`.
- `select()` passes `discovery.get("evidence_available")` through.
- `ollama_rerank` must base its adjustment on `record.get("overall")`, not
  `raw_score`, so evidence is not discarded. Keep `raw_score` unchanged.
- Candidate records keep `raw_score` as the text score.

- [ ] **Step 5: Add the evidence version to the cache key**

In `projects.py`:

```python
import evidence
...
discovery_key = digest([transcript_key, data, discovery_options,
                        evidence.EVIDENCE_VERSION])
```

Keep the selection key derived from the discovery key plus min score, cap, and
LLM. Existing Phase 1 projects will rerun discovery once because the key
changes; transcript reuse still applies.

- [ ] **Step 6: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_evidence.py tests/test_chopify.py tests/test_studio.py -q`

Expected: all pass. If the new evidence changes the fixture top scores, keep
the assertions on structure and ordering, not exact values.

- [ ] **Step 7: Commit**

```powershell
git add score_clips.py projects.py tests/test_chopify.py tests/test_studio.py
git commit -m "feat: attach audio evidence to clip discovery"
```

---

### Task 4: Action-Only and Speech-Sparse Sources

**Files:**
- Modify: `score_clips.py`
- Modify: `tests/test_chopify.py`

**Interfaces:**
- Produces: `discover_media_candidates`, no-speech source discovery, event/grid
  seeds, the specific flat-source error, and speech-sparse scoring.

- [ ] **Step 1: Write failing no-speech tests**

Append to `tests/test_chopify.py`:

```python
def wordless_workdir(tmp_path, source, duration=30.0):
    wd = tmp_path / "wordless"
    wd.mkdir()
    data = {"video": str(source), "language": "en", "duration": duration,
            "model": "test", "segments": [], "words": []}
    (wd / "transcript.json").write_text(json.dumps(data), encoding="utf-8")
    return wd


def test_wordless_source_uses_audio_event_seeds(tmp_path, monkeypatch):
    import evidence
    samples = np.concatenate([
        np.full(evidence.SAMPLE_RATE * 5, 0.001, dtype=np.float32),
        np.full(evidence.SAMPLE_RATE, 0.8, dtype=np.float32),
        np.full(evidence.SAMPLE_RATE * 4, 0.001, dtype=np.float32),
    ])
    timeline = evidence.features_from_samples(samples)
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"x")
    wd = wordless_workdir(tmp_path, source, 10.0)
    monkeypatch.setattr(evidence, "load_audio_timeline", lambda *a, **k: timeline)
    segs, _ = score_clips.run(wd, min_score=0, min_len=4, max_len=10)
    assert segs
    assert all(s["evidence"]["audio"] is not None for s in segs)
    assert any("event" in s["hook"] or "action" in s["hook"] for s in segs)


def test_wordless_flat_source_fails_clearly(tmp_path):
    workdir = wordless_workdir(tmp_path, tmp_path / "flat.mp4", 30.0)
    with pytest.raises(ValueError, match="No speech and no media evidence"):
        score_clips.run(workdir, min_score=8.0)


def test_speech_sparse_windows_use_text_floor(workdir, monkeypatch):
    import evidence
    timeline = evidence.features_from_samples(
        np.concatenate([np.full(16000 * 2, 0.01, dtype=np.float32),
                        np.full(16000, 0.9, dtype=np.float32),
                        np.full(16000 * 2, 0.01, dtype=np.float32)]))
    monkeypatch.setattr(evidence, "load_audio_timeline", lambda *a, **k: timeline)
    segs, _ = score_clips.run(workdir, min_score=0, min_len=4, max_len=20)
    sparse = [s for s in segs if s.get("evidence", {}).get("text_floor")]
    assert sparse
    assert all(s["evidence"]["weight"] >= 0.75 for s in sparse)
```

- [ ] **Step 2: Run to verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chopify.py::test_wordless_source_uses_audio_event_seeds tests/test_chopify.py::test_wordless_flat_source_fails_clearly tests/test_chopify.py::test_speech_sparse_windows_use_text_floor -q`

Expected: `SystemExit` from `load_transcript` or no media candidates.

- [ ] **Step 3: Allow a transcript with no words when media is present**

In `score_clips.load_transcript`:

- Require `words` to be a list.
- If empty, still accept the transcript when `video` and a positive `duration` are
  present.
- Keep exiting with `transcript.json has no words - was the video silent?` only
  when there is no usable media metadata.

- [ ] **Step 4: Add media candidates**

Implement in `score_clips.py`:

```python
MEDIA_TEXT_FLOOR = 7.0

def media_candidate_record(start, end, seed_kind="event"):
    """Build a wordless candidate; evidence decides whether it passes."""
    start, end = round(float(start), 2), round(float(end), 2)
    return {
        "start": start, "end": end, "duration": round(end - start, 2),
        "hook": f"{seed_kind} at {int(start)}s", "text": "",
        "raw_score": MEDIA_TEXT_FLOOR, "overall": MEDIA_TEXT_FLOOR,
        "score_parts": {key: 0.0 for key in TEXT_WEIGHTS},
        "profile": "gameplay/action", "seed_index": None,
        "seed_kind": seed_kind, "endpoint_kind": "quality_best",
        "reason": "No words; scored on media evidence",
    }


def discover_media_candidates(timeline, min_len, max_len, preferred_len=None,
                              band=(15, 60), profile="gameplay/action",
                              source_energy=0.5, duration=None):
    """Build event-seeded windows for a source with no transcript words."""
```

Requirements:

- Get event seeds from `timeline.peak_times(min_gap=10)`. A seed starts 2
  seconds before the peak and never before 0.
- If there are no peaks and `timeline.variability < 0.02`, raise
  `ValueError("No speech and no media evidence; this source cannot be scored.")`.
- If there are no peaks but the timeline varies, use a grid every
  `max(30, max_len / 2)` seconds.
- For every seed and ladder target, build a window `[seed, seed + target]`,
  clamp to source duration, snap both edges to `timeline.trough_near(...,
  radius=1.5)`, and keep durations inside `[min_len, max_len]`.
- Score each candidate with `evidence.audio_evidence` and
  `evidence.apply_evidence`, then keep up to two endpoints per seed with
  `choose_seed_endpoints`.
- Return start-sorted candidates and stats with `seeds` (event/grid count) and
  `windows_scanned`.
- Hook text uses `"event at 83s"` or `"action at 83s"`; never claim a semantic
  event that was not detected.

- [ ] **Step 5: Route no-word sources through media discovery**

In `discover()`:

```python
sentences = sentenceize(data.get("words") or [])
if not sentences:
    if timeline is None:
        raise ValueError("No speech and no media evidence; this source cannot be scored.")
    candidates, stats = discover_media_candidates(...)
else:
    candidates, stats = discover_candidates(...)
```

Update stats with the profile, energy, duration, and availability. Save through
the existing `save_candidates()` path.

- [ ] **Step 6: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chopify.py tests/test_evidence.py -q`

Expected: all pass. Existing fake-source tests still get neutral evidence and
their text clips.

- [ ] **Step 7: Commit**

```powershell
git add score_clips.py tests/test_chopify.py
git commit -m "feat: clip speech-sparse and action-only sources"
```

---

### Task 5: Docs, Cache Review, and Phase 2A Exit

**Files:**
- Modify: `README.md`
- Modify: `docs/WORKFLOWS.md`
- Modify: `CHANGELOG.md`
- Test: full suite + a real transcript run.

- [ ] **Step 1: Document the behavior**

README and WORKFLOWS must state:

- Audio evidence runs locally and only analyzes candidate ranges.
- The audio timeline is cached in `audio_features.npz` and reused.
- Content profiles are internal; `talk`, `mixed`, and `gameplay/action` change
  tie-break bands and evidence floors, never the user-facing threshold.
- Silent/action-only sources use event seeds and the 7.0 text floor.
- A flat source with no words and no media evidence fails with the exact message.
- Visual evidence arrives in Phase 2B; audio-only sources are supported now.

CHANGELOG adds:

- Audio evidence timeline, cached features, adaptive weights.
- Action-only and speech-sparse clip discovery.
- Evidence fields in `candidates.json`/`segments.json`.

- [ ] **Step 2: Run the full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run the real 12-minute transcript**

```powershell
$work = "$env:TEMP\opencode\chopify-youtube-test\work"
.\.venv\Scripts\python.exe score_clips.py $work --min-score 8 --preferred-len 45s
```

Expected: at least one 8.0+ clip, `evidence_available.audio` true for that
source, and no crash. The source is a real 12-minute video; this also exercises
the streaming decoder.

- [ ] **Step 4: Confirm the project cache behavior**

Run prepare twice and confirm the second run skips decoding:

```powershell
.\.venv\Scripts\python.exe chopify.py --project $work --action prepare --no-input
.\.venv\Scripts\python.exe chopify.py --project $work --action prepare --no-input
```

Expected: second run reuses `audio_features.npz`, prints the same selection, and
does not re-transcribe.

- [ ] **Step 5: Review the diff**

Check:
- No network calls beyond local Ollama.
- Missing source still scores from text.
- `candidates.json` remains an endpoint list.
- `segments.json` preserves the Phase 1 keys and adds `evidence`.
- Existing `max_clips` values still win.
- Lowering `min_score` still re-filters without rescanning text.

- [ ] **Step 6: Commit docs and merge**

```powershell
git add README.md docs/WORKFLOWS.md CHANGELOG.md
git commit -m "docs: document local audio evidence"
git checkout main
git merge --no-ff feat/creator-evidence
.\.venv\Scripts\python.exe -m pytest -q
```

Do not push until asked.

---

## Acceptance

- `evidence.py` builds a 1 Hz timeline from a real source and caches it.
- Audio evidence raises a loud reaction over a flat track.
- A source with no words and a real audio peak produces passing media candidates
  at `min_score=0` and reports `evidence.audio`.
- A flat, wordless source fails with the exact spec message.
- Sparse windows use the 7.0 text floor and 0.75 evidence weight.
- Missing or unreadable sources fall back to text-only scoring.
- All existing 219 tests plus the new tests pass.
- The real 12-minute transcript still produces at least one 8.0+ clip.

## Deferred to Phase 2B

- Visual sparse baseline, frame sampling, scene/UI/facecam features.
- Visual evidence blending, the flat-action cap, and visual shortlist analysis.
- Boundary refinement using motion/loudness troughs.
- Long URL evidence-section downloads.
