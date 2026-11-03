# Long-Source Ingest and Section Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every-length local files and URL sources produce 8.0+ clips by adding audio-first long URL ingest, chunked/resumable transcription, block-scanned discovery, and section-only rendering.

**Architecture:** A new `ingest.py` probes sources and downloads audio/discovery media and video sections. `download_and_transcribe.py` gains 30-minute chunked transcription with overlap merging. `score_clips.py` scans 30-minute ownership blocks and merges endpoint candidates before global evidence and thresholding. `sections.py` plans, downloads, caches, and cleans padded sections. `render_clips.render()` and `tighten.apply_cuts()` gain `source_offset` so global clip times remain the contract while ffmpeg seeks inside a downloaded section. `projects.py` chooses the short or long path from probe duration/size, with no user toggle.

**Tech Stack:** Python 3.11+, ffmpeg/ffprobe, yt-dlp, faster-whisper, OpenCV, NumPy, pytest. Everything remains local; URL input downloads media, and no paid API or hosted processing is introduced.

## Global Constraints

- Preserve existing CLI flags, brand JSON, caption presets, and default 16:9 appearance.
- No paid API, hosted processing, cloud upload, or API key.
- Short sources keep the current one-pass download/transcribe/render behavior.
- Long URL sources are 2 hours or more, estimated full download 8 GB or more, or unknown duration.
- Long local sources never re-download; transcribe in chunks and render by seeking directly in the file.
- Long URL sources download audio only for discovery when audio exists; otherwise download the lowest-resolution video and use visual seeds.
- Chunk length 30 minutes, overlap 30 seconds.
- Discovery block size 30 minutes; each block reads up to `max_len` seconds of context beyond its block end.
- Section padding 10 seconds, merge gap 30 seconds, section cap 10 minutes. A single longer clip gets its own padded section.
- Clip `start`/`end` stay global source times. `source_offset` is the section's global start.
- Sections are cached under `sections/` and deleted only after a successful final (non-preview) render.
- `segments.json` remains a bare start-sorted list; optional keys stay optional.
- Minimum score stays 8.0 inclusive and every passing clip is returned unless `--max-clips` is set.
- No push.

---

### Task 1: Source probe, long-path selection, and long URL media download

**Files:**
- Create: `ingest.py`
- Create: `tests/test_ingest.py`

**Interfaces:**
- Consumes: local paths, HTTP(S) URLs, local `ffprobe`, local `yt-dlp`.
- Produces:
  - `is_url(source: str) -> bool`
  - `probe_source(source: str, runner=None) -> dict` with keys `source`, `url`, `duration`, `title`, `size`, `estimated_size`, `width`, `height`, `has_audio`, `has_video`
  - `is_long_source(meta: dict, seconds=7200, size_bytes=8 * 1024**3) -> bool`
  - `download_discovery(url, workdir, meta=None, runner=None) -> tuple[Path, str]` where the second value is `"audio"` or `"video"`
  - `download_section(url, start, end, out_path, quality=1080, runner=None) -> Path`
  - `check_disk(path, required_bytes) -> None`

- [ ] **Step 1: Write failing probe and long-path tests**

```python
def test_long_source_uses_duration_size_or_unknown():
    assert ingest.is_long_source({"duration": 7200, "estimated_size": 0})
    assert ingest.is_long_source({"duration": 60, "estimated_size": 8 * 1024**3})
    assert not ingest.is_long_source({"duration": 7199, "estimated_size": 1})
    assert ingest.is_long_source({"duration": None, "estimated_size": None})

def test_probe_local_reads_duration_size_and_streams(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    payload = {
        "format": {"duration": "3600.5", "size": "1234"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080},
            {"codec_type": "audio"},
        ],
    }
    monkeypatch.setattr(ingest, "_run", lambda cmd, **kw: FakeResult(json.dumps(payload)))
    meta = ingest.probe_source(str(source))
    assert meta["duration"] == pytest.approx(3600.5)
    assert meta["size"] == 1234
    assert meta["width"] == 1920 and meta["height"] == 1080
    assert meta["has_audio"] is True and meta["has_video"] is True
    assert meta["url"] is False

def test_probe_url_estimates_best_video_plus_audio(monkeypatch):
    payload = {
        "duration": 7200,
        "title": "Long stream",
        "formats": [
            {"vcodec": "h264", "height": 1080, "filesize": 6 * 1024**3},
            {"vcodec": "h264", "height": 720, "filesize": 3 * 1024**3},
            {"acodec": "aac", "vcodec": "none", "filesize": 200 * 1024**2},
        ],
    }
    monkeypatch.setattr(ingest, "_run", lambda cmd, **kw: FakeResult(json.dumps(payload)))
    meta = ingest.probe_source("https://example.com/v")
    assert meta["url"] is True
    assert meta["duration"] == 7200
    assert meta["estimated_size"] >= 6 * 1024**3
    assert ingest.is_long_source(meta)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ingest.py -q`
Expected: FAIL with `ModuleNotFoundError: ingest`.

- [ ] **Step 3: Implement `ingest.py`**

Use a module-level `_run(cmd, **kwargs)` that calls `subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)`. Local probing runs:

```text
ffprobe -v error -show_entries format=duration,size:stream=codec_type,width,height -of json PATH
```

URL probing runs:

```text
python -m yt_dlp --dump-single-json --skip-download --no-playlist URL
```

Parse `formats`; `has_audio` is any `acodec` other than `none`; `has_video` is any `vcodec` other than `none`. Estimate the best video format by `(height or 0, tbr or 0)` and add the best audio format size. Fall back to `duration * bitrate / 8` using 8 Mbps for 1080p, 5 Mbps for 720p, 2.5 Mbps for 480p, otherwise 4 Mbps. On URL probe failure return a metadata dict with `duration=None`, `estimated_size=None`, empty formats, and `has_audio=None`.

`download_discovery` removes stale `audio.*` / `discovery.*` files, then:

```text
python -m yt_dlp -f bestaudio/best --no-playlist -o audio.%(ext)s URL
```

If no audio file appears or `meta["has_audio"] is False`, run:

```text
python -m yt_dlp -f worst[height<=360]/worst --no-playlist -o discovery.%(ext)s URL
```

Return the first existing media file and its kind. Raise `RuntimeError("Download failed: no audio or video file produced.")` when neither exists.

`download_section` runs:

```text
python -m yt_dlp -f bestvideo[height<=Q]+bestaudio/best[height<=Q]
  --merge-output-format mp4 --no-playlist --force-overwrites
  --download-sections *START-END --force-keyframes-at-cuts
  -o OUT URL
```

For `quality=None`, use `worst`. Remove a partial output before retrying and verify the file exists.

`check_disk` raises below 500 MB free and prints a warning below 1.5x required.

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_ingest.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "feat: probe and ingest long sources"
```

---

### Task 2: Chunked, resumable transcription

**Files:**
- Modify: `download_and_transcribe.py`
- Create: `tests/test_chunked_transcription.py`

**Interfaces:**
- Consumes: discovery media, probe duration, faster-whisper model.
- Produces:
  - `TRANSCRIPT_VERSION = 2`
  - `CHUNK_SECONDS = 1800`, `OVERLAP_SECONDS = 30`
  - `transcribe(video, workdir, model_size, device, compute_type, duration=None, chunked=False, source=None, media_kind="video", render_from_sections=False, identity=None, progress=None, resume=True)`
  - `transcribe_chunked(...)` with the same keyword fields
  - `merge_transcript_parts(parts, duration, overlap=OVERLAP_SECONDS) -> dict`
  - `write_media_transcript(video, workdir, duration, source=None, media_kind="video", render_from_sections=True, identity=None, language="")`

- [ ] **Step 1: Write failing merge and resume tests**

```python
def test_merge_assigns_each_overlap_word_once():
    parts = [
        {"index": 0, "start": 0, "end": 1830, "language": "en",
         "segments": [{"start": 1790, "end": 1802, "text": "late"}],
         "words": [{"word": "late", "start": 1790, "end": 1800},
                   {"word": "edge", "start": 1805, "end": 1810}]},
        {"index": 1, "start": 1800, "end": 3600, "language": "en",
         "segments": [{"start": 1805, "end": 1810, "text": "edge"}],
         "words": [{"word": "edge", "start": 1805, "end": 1815},
                   {"word": "next", "start": 1830, "end": 1835}]},
    ]
    merged = download_and_transcribe.merge_transcript_parts(parts, 3600)
    words = [w["word"] for w in merged["words"]]
    assert words == ["late", "edge", "next"]
    assert merged["chunks"] == 2

def test_chunked_transcription_resumes_completed_parts(tmp_path, monkeypatch):
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")
    calls = []
    monkeypatch.setattr(download_and_transcribe, "CHUNK_SECONDS", 1800)
    monkeypatch.setattr(download_and_transcribe, "OVERLAP_SECONDS", 30)
    monkeypatch.setattr(download_and_transcribe, "_probe_duration", lambda p: 3600.0)
    monkeypatch.setattr(download_and_transcribe, "_extract_chunk",
                        lambda source, start, end, path: path.write_bytes(b"wav"))
    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(path)
            return iter([FakeSegment(0, 1, "hello", start=0)]), FakeInfo("en", 1.0)
    monkeypatch.setattr(download_and_transcribe, "_load_model", lambda *a: Model())
    download_and_transcribe.transcribe_chunked(source, tmp_path, "tiny", "cpu", "int8", 3600.0)
    first = len(calls)
    download_and_transcribe.transcribe_chunked(source, tmp_path, "tiny", "cpu", "int8", 3600.0)
    assert len(calls) == first
    data = json.loads((tmp_path / "transcript.json").read_text())
    assert data["chunked"] is True and data["chunks"] == 2
    assert data["duration"] == 3600.0
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chunked_transcription.py -q`
Expected: FAIL because `merge_transcript_parts` and `transcribe_chunked` do not exist.

- [ ] **Step 3: Implement chunking**

Add `TRANSCRIPT_VERSION`, `CHUNK_SECONDS`, `OVERLAP_SECONDS`. Refactor the existing model call into `_load_model(model_size, device, compute_type)` and `_transcribe_media(model, media, offset=0.0)` returning `(segments, words, language, info_duration)`.

`transcribe_chunked`:

- Determine duration from the argument or `_probe_duration`.
- Create `audio_parts/`.
- For each block `i`, `start=i*1800`, `owned_end=min(duration, start+1800)`, and `end=min(duration, owned_end+30)`. The part stores `start` and `end` as the transcribed range.
- Reuse `transcript.partNNN.json` only when version, index, start/end, identity, and word monotonicity match.
- Otherwise extract `[start, end]` to `audio_parts/partNNN.wav` with mono 16 kHz ffmpeg, transcribe it, offset all times by `start`, and atomically write the part.
- Delete the WAV after a successful part.
- Call `progress(done, total)` after each part.
- Merge all parts with `merge_transcript_parts`.
- Write `transcript.json` with `chunked=True`, `chunks=N`, `media_kind`, `render_from_sections`, `transcript_version`, `source`, `source_duration`, `source_identity`, and merged words/segments.
- Set `duration` from the probe, not the last word.

`merge_transcript_parts` applies the midpoint rule:

- For `index > 0`, drop words whose midpoint is before `start + overlap / 2`.
- For `index < last`, drop words whose midpoint is at or after `end - overlap / 2`.
- Apply the same rule to segments.
- Remove exact `(round(start, 3), text)` duplicates.
- Sort by start and return `{"words": ..., "segments": ..., "language": ..., "chunks": len(parts)}`.

`write_media_transcript` writes empty words/segments plus the metadata for the silent long URL visual-only path.

- [ ] **Step 4: Run the tests and the existing transcription-adjacent suite**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chunked_transcription.py tests/test_studio.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add download_and_transcribe.py tests/test_chunked_transcription.py
git commit -m "feat: transcribe long sources in resumable chunks"
```

---

### Task 3: Block-scanned discovery for long transcripts

**Files:**
- Modify: `score_clips.py`
- Create: `tests/test_block_scan.py`

**Interfaces:**
- Consumes: sentence timeline, duration, discovery options, workdir.
- Produces:
  - `LONG_SOURCE_SECONDS = 7200`
  - `BLOCK_SECONDS = 1800`
  - `discover_blocks(sentences, min_len, max_len, preferred_len=None, band=(30, 120), duration=None, workdir=None, progress=None) -> (candidates, stats)`

- [ ] **Step 1: Write failing block tests**

```python
def test_block_scan_merges_candidates_and_writes_parts(tmp_path):
    words = []
    for i in range(0, 7200, 30):
        words.extend([{"word": f"w{i}.", "start": i, "end": i + 20}])
    sentences = score_clips.sentenceize(words)
    candidates, stats = score_clips.discover_blocks(
        sentences, 15, 60, duration=7200, workdir=tmp_path)
    assert stats["seeds"] == len(candidates)
    assert len(list(tmp_path.glob("candidates.part*.json"))) == 4
    again, _ = score_clips.discover_blocks(
        sentences, 15, 60, duration=7200, workdir=tmp_path)
    assert [(c["start"], c["end"]) for c in again] == [
        (c["start"], c["end"]) for c in candidates]

def test_long_discover_uses_block_scan(monkeypatch, tmp_path):
    data = {"video": "fake.mp4", "duration": 7200, "words": [
        {"word": "Hello.", "start": 0, "end": 1}]}
    calls = []
    monkeypatch.setattr(score_clips, "load_transcript", lambda p: data)
    monkeypatch.setattr(score_clips, "source_evidence",
                        lambda *a: (None, "talk", 0.5, 1.0, False, None))
    monkeypatch.setattr(score_clips, "discover_blocks",
                        lambda *a, **k: (calls.append("blocks") or [], {"seeds": 0}))
    monkeypatch.setattr(score_clips, "discover_candidates",
                        lambda *a, **k: (calls.append("single") or [], {"seeds": 0}))
    score_clips.discover(tmp_path, min_len=15, max_len=60)
    assert calls == ["blocks"]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_block_scan.py -q`
Expected: FAIL because `discover_blocks` does not exist.

- [ ] **Step 3: Implement block scanning**

Add `import hashlib`. Compute a scan key from the transcript file identity and `[min_len, max_len, preferred_len, band, evidence.EVIDENCE_VERSION, visual.FEATURE_VERSION]`. For each 30-minute block:

- Find owned sentences with `bisect` on start times in `[block_start, block_end)`.
- Extend context to `block_end + max_len`.
- Call `discover_candidates` on the local slice with `seed_indices=range(owned_count)`.
- Write `candidates.partNNN.json` with `version`, `scan_key`, `index`, `start`, `end`, `candidates`, `stats`.
- Reuse a part only when its scan key and bounds match.
- Sum `seeds` and `windows_scanned`, merge candidates, and return stats with `source_duration=duration`.

In `discover`, after `sentenceize` and duration calculation, call `discover_blocks` when `duration >= LONG_SOURCE_SECONDS`, otherwise keep `discover_candidates`. Do not block-scan wordless media discovery.

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_block_scan.py tests/test_chopify.py -q`
Expected: all pass, 260 existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add score_clips.py tests/test_block_scan.py
git commit -m "feat: scan long transcripts in resumable blocks"
```

---

### Task 4: Project long-source orchestration

**Files:**
- Modify: `projects.py`
- Modify: `tests/test_studio.py`
- Create: `tests/test_long_projects.py`

**Interfaces:**
- Consumes: `ingest.probe_source`, `ingest.is_long_source`, `ingest.download_discovery`, `download_and_transcribe.transcribe(..., chunked=...)`.
- Produces project state keys:
  - `source_meta`: probe metadata plus `long`, `render_from_sections`, `media_kind`
  - `progress`: `{"stage": str, "done": int, "total": int, "detail": str}`
  - `render_errors`: list of `{"clip": str, "error": str}`
  - `cache["source_media"]`

- [ ] **Step 1: Write failing long-project tests**

```python
def test_long_url_prepare_downloads_audio_and_transcribes_in_chunks(tmp_path, monkeypatch):
    import projects
    source = "https://example.com/long"
    state = projects.create_project(tmp_path / "projects", source, {"min_len": 15})
    path = projects.project_path(tmp_path / "projects", state["id"])
    meta = {"source": source, "url": True, "duration": 9000, "width": 1920,
            "height": 1080, "has_audio": True, "has_video": True,
            "estimated_size": 12 * 1024**3}
    calls = []
    monkeypatch.setattr(projects.ingest, "probe_source", lambda s: meta)
    monkeypatch.setattr(projects.ingest, "is_long_source", lambda m: True)
    monkeypatch.setattr(projects.ingest, "download_discovery",
                        lambda url, workdir, m=None: (tmp_path / "audio.m4a", "audio"))
    (tmp_path / "audio.m4a").write_bytes(b"audio")
    def transcribe(video, workdir, *args, **kwargs):
        calls.append(kwargs)
        projects.atomic_json(workdir / "transcript.json", {
            "video": str(video), "source": source, "duration": 9000,
            "render_from_sections": True, "chunked": True, "chunks": 5,
            "words": [{"word": "Hello.", "start": 0, "end": 1}],
            "segments": []})
    monkeypatch.setattr(projects.dt, "transcribe", transcribe)
    monkeypatch.setattr(projects.score_clips, "discover", lambda *a, **k: None)
    monkeypatch.setattr(projects.score_clips, "select", lambda *a, **k: (
        projects.atomic_json(path / "segments.json", []), None))
    projects.run_project(path, action="prepare")
    assert calls and calls[0]["chunked"] is True
    assert calls[0]["render_from_sections"] is True
    assert calls[0]["duration"] == 9000
    assert projects.read_project(path)["source_meta"]["media_kind"] == "audio"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_long_projects.py -q`
Expected: FAIL because the source stage does not probe or download audio-first.

- [ ] **Step 3: Implement the source stage**

- Probe once per source identity and cache `source_meta`.
- Local: discovery path is the local file, `long = is_long_source(meta)`, `render_from_sections=False`.
- Short URL: keep `dt.resolve_source` and full download.
- Long URL: call `ingest.download_discovery`; keep the audio/discovery file; set `render_from_sections=True`.
- If long URL has no audio, write a media-only transcript with `dt.write_media_transcript`; never call faster-whisper.
- Pass `duration`, `chunked=long`, `source=source_input`, `media_kind`, `render_from_sections`, and `identity` to `dt.transcribe`.
- Add `dt.TRANSCRIPT_VERSION` to the transcript cache key.
- Store `state["source_identity"] = file_identity(discovery)`.
- Update `state["progress"]` on stage changes.

- [ ] **Step 4: Run long-project and existing project tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_long_projects.py tests/test_studio.py tests/test_project_render.py -q`
Expected: all pass. Update the `transcribe` monkeypatch in `test_studio.py` to accept `**kwargs`.

- [ ] **Step 5: Commit**

```bash
git add projects.py tests/test_studio.py tests/test_long_projects.py
git commit -m "feat: route long projects through audio-first ingest"
```

---

### Task 5: Section planning, download caching, and cleanup

**Files:**
- Create: `sections.py`
- Create: `tests/test_sections.py`

**Interfaces:**
- Consumes: selected clip ranges, source duration, source URL, `ingest.download_section`.
- Produces:
  - `SECTION_PAD = 10.0`, `SECTION_GAP = 30.0`, `SECTION_MAX = 600.0`
  - `plan_sections(segments, duration, pad=SECTION_PAD, gap=SECTION_GAP, max_section=SECTION_MAX) -> list[dict]` with `start`, `end`, `clips` (0-based clip indexes)
  - `ensure_section(url, workdir, index, start, end, runner=None) -> Path`
  - `cleanup_sections(workdir) -> None`

- [ ] **Step 1: Write failing section tests**

```python
def test_nearby_clips_merge_and_far_clips_split():
    segments = [{"start": 100, "end": 130}, {"start": 150, "end": 180},
                {"start": 300, "end": 330}]
    groups = sections.plan_sections(segments, 600)
    assert [(g["start"], g["end"], g["clips"]) for g in groups] == [
        (90.0, 190.0, [0, 1]),
        (290.0, 340.0, [2])]

def test_section_cap_splits_and_single_long_clip_is_not_truncated():
    segments = [{"start": i * 60, "end": i * 60 + 30} for i in range(12)]
    groups = sections.plan_sections(segments, 1000)
    assert all(g["end"] - g["start"] <= 600 for g in groups)
    long = sections.plan_sections([{"start": 100, "end": 800}], 1000)
    assert long == [{"start": 90.0, "end": 810.0, "clips": [0]}]

def test_ensure_section_reuses_matching_metadata(tmp_path, monkeypatch):
    calls = []
    def download(url, start, end, out, quality=1080, runner=None):
        calls.append(quality)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"section")
        return out
    monkeypatch.setattr(sections.ingest, "download_section", download)
    first = sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    second = sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    assert first == second
    assert calls == [1080]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sections.py -q`
Expected: FAIL because `sections` does not exist.

- [ ] **Step 3: Implement sections**

`plan_sections` sorts clips by start, merges clips whose ranges overlap or are within 30 seconds, pads each group by 10 seconds, clamps to `[0, duration]`, and splits a group before adding a clip that would push the padded section over 600 seconds. A group with one longer clip keeps its full padded range.

`ensure_section` stores `sections/sectionNNN.mp4` plus `sectionNNN.json` metadata with `version`, source hash, start, end, and quality. Reuse only when the metadata and file match. On a miss, try qualities `1080`, `720`, then `None`, removing partial files between attempts.

`cleanup_sections` removes only files under `<workdir>/sections/`.

- [ ] **Step 4: Run the tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sections.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sections.py tests/test_sections.py
git commit -m "feat: plan and cache long-source sections"
```

---

### Task 6: Render and tighten with `source_offset`

**Files:**
- Modify: `render_clips.py`
- Modify: `tighten.py`
- Modify: `tests/test_chopify.py`
- Modify: `tests/test_render_integration.py`

**Interfaces:**
- Consumes: a section file whose local time 0 equals global `source_offset`.
- Produces:
  - `render_clips.render(..., source_offset=0.0)`
  - `tighten.apply_cuts(source, keeps, clip_start, out_path, crf=18, source_offset=0.0)`

- [ ] **Step 1: Write failing seek tests**

```python
def test_render_seeks_inside_section_with_global_caption_times(tmp_path, monkeypatch):
    commands = []
    def fake_run(cmd, **kwargs):
        commands.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(render_clips.subprocess, "run", fake_run)
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    out = render_clips.render(
        {"id": "001", "start": 55, "end": 60, "hook": "clip"},
        [{"word": "hello", "start": 55, "end": 56}],
        source, 640, 360, tmp_path, "16:9", out_dir=tmp_path, preview=True,
        source_offset=50)
    assert commands[0][commands[0].index("-ss") + 1] == "5.000"
    assert "00:00:00,000" in out.with_suffix(".srt").read_text()

def test_apply_cuts_seeks_from_section_offset(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(tighten.subprocess, "run",
                        lambda cmd, **kw: commands.append([str(c) for c in cmd]))
    source = tmp_path / "section.mp4"
    source.write_bytes(b"section")
    tighten.apply_cuts(source, [(0.0, 1.0)], 55, tmp_path / "tight.mp4",
                       source_offset=50)
    assert commands[0][commands[0].index("-ss") + 1] == "5.000"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chopify.py -q -k "source_offset or section_offset"`
Expected: FAIL with unexpected keyword argument `source_offset`.

- [ ] **Step 3: Implement source offsets**

- Validate `source_offset` is finite and nonnegative, and `start >= source_offset`.
- Compute `local_start = start - source_offset`.
- Use `local_start` for the main ffmpeg seek and `detect_track`.
- Use global `start`/`end` for `build_ass`, `write_srt`, metadata, and poster.
- Pass `source_offset` to `tighten.apply_cuts`.
- In `apply_cuts`, seek to `clip_start + a - source_offset`, clamped to 0.
- Add `source_offset` to the project render cache key.

- [ ] **Step 4: Run render tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_chopify.py tests/test_render_integration.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add render_clips.py tighten.py tests/test_chopify.py tests/test_render_integration.py
git commit -m "feat: render clips from offset sections"
```

---

### Task 7: Render long projects from sections with per-clip recovery

**Files:**
- Modify: `projects.py`
- Modify: `tests/test_project_render.py`

**Interfaces:**
- Consumes: `sections.plan_sections`, `sections.ensure_section`, `sections.cleanup_sections`, `render_clips.render(source_offset=...)`.
- Produces: section-aware render loop and `render_errors` state.

- [ ] **Step 1: Write failing section-render tests**

```python
def test_long_project_renders_from_section_offsets(tmp_path, monkeypatch):
    path = ready_long_project(tmp_path)
    section = path / "sections" / "section001.mp4"
    section.parent.mkdir()
    section.write_bytes(b"section")
    monkeypatch.setattr(projects.sections, "ensure_section",
                        lambda *a, **k: section)
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims", lambda p: (640, 360))
    seen = []
    def render(segment, words, source, W, H, workdir, *args, **kwargs):
        seen.append((Path(source), kwargs["source_offset"]))
        out = kwargs["out_dir"] / f"{segment['id']}.mp4"
        for ext in (".mp4", ".srt"):
            out.with_suffix(ext).write_text("x")
        return out
    monkeypatch.setattr(projects.render_clips, "render", render)
    state = projects.run_project(path, action="render")
    assert seen == [(section, 90.0)]
    assert state["render_errors"] == []
    assert not section.exists()

def test_section_failure_records_error_and_keeps_successes(tmp_path, monkeypatch):
    path = ready_long_project(tmp_path, two_clips=True)
    monkeypatch.setattr(projects.sections, "ensure_section",
                        lambda url, workdir, index, start, end: (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(projects.render_clips, "ffprobe_dims", lambda p: (640, 360))
    with pytest.raises(ValueError, match="failed"):
        projects.run_project(path, action="render")
    assert len(projects.read_project(path)["render_errors"]) == 2
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_project_render.py -q -k long`
Expected: FAIL because the render loop still probes the audio file and has no sections.

- [ ] **Step 3: Implement the section render loop**

- Read `render_from_sections` from `source_meta` or transcript.
- For section projects, plan groups from the picked segments and map each clip index to its group.
- Lazily call `ensure_section` for the group, then probe that section's dimensions.
- Render with `source_offset=group["start"]`.
- On section or render failure, append `{"clip": segment["id"], "error": str(exc)}`, keep going, and do not abort other groups.
- Record `state["render_errors"]`. If no clip rendered, raise `ValueError` mentioning failed clips. If some rendered, keep successful outputs and leave sections cached for retry.
- After a successful final (not preview) render with no errors, call `sections.cleanup_sections`.
- Include `source_offset` and section file identity in the render cache key.

- [ ] **Step 4: Run project render tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_project_render.py tests/test_long_projects.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add projects.py tests/test_project_render.py
git commit -m "feat: render long projects from cached sections"
```

---

### Task 8: Documentation, full verification, and merge

**Files:**
- Modify: `README.md`
- Modify: `docs/WORKFLOWS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document the long path**

README and WORKFLOWS must state:

- No toggle: 2 hours, 8 GB estimated, or unknown duration selects the long path.
- Long URLs download audio only; visual-only URLs download the lowest-resolution video.
- Transcription is 30-minute chunks with 30-second overlap and resumable parts.
- Discovery scans 30-minute ownership blocks with `max_len` context.
- Rendering downloads only selected sections, pads 10 seconds, merges within 30 seconds, caps at 10 minutes, and keeps long clips whole.
- Local long files render directly.
- Sections cache under `sections/`, previews keep them, successful final renders delete them.
- Per-clip section errors keep successful renders and retry later.
- Existing short-source behavior, CLI flags, brand JSON, captions, and 16:9 output are unchanged.

CHANGELOG adds long-source probe/long path, chunked transcription, block scanning, section rendering, `source_offset`, and per-clip recovery.

- [ ] **Step 2: Run the full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass.

- [ ] **Step 3: Run a real long-path smoke test**

Use a local file with a forced long probe in a test harness, or a URL if the environment is online. Verify:

- `transcript.json` has `chunked: true`, correct `chunks`, and global word times.
- `candidates.partNNN.json` exists for every 30-minute block.
- Long URL render calls section download only for selected ranges.
- `render_errors` is empty and sections are removed after a successful final render.

- [ ] **Step 4: Review the diff**

Check no paid API/network service was added, local files never re-download, short sources still use the old path, `segments.json` stays a bare list, and cache files stay inside the project work directory.

- [ ] **Step 5: Commit docs and merge**

```bash
git add README.md docs/WORKFLOWS.md CHANGELOG.md
git commit -m "docs: document long-source ingest and sections"
git checkout main
git merge --no-ff feat/long-source-ingest -m "merge: long-source ingest and section rendering"
.\.venv\Scripts\python.exe -m pytest -q
```

Do not push.
