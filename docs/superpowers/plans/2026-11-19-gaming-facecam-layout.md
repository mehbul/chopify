# Gaming Facecam Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 9:16 `stacked` layout that places the streamer's facecam above gameplay with captions at the seam, available in batch and live modes from CLI and Studio.

**Architecture:** A new `layout=stacked` branch in `render_clips.render()` reuses the existing `split`/`vstack` filter-graph pattern. `facecam_box` is resolved either from explicit normalized coords or from the existing YuNet/Haar detector, with a bottom-left fallback. No new module, dependency, or encode pass.

**Tech Stack:** Python 3.11+, ffmpeg filter_complex (`split`, `crop`, `scale`, `vstack`, `subtitles`), existing OpenCV detector, argparse, stdlib HTTP Studio, pytest.

## Global Constraints

- Python 3.11+; ffmpeg/ffprobe with libx264, AAC, libass on PATH.
- Local only; no new dependency, no API key, no new service. Studio stays on `127.0.0.1`.
- Settings pass through `projects.DEFAULT_OPTIONS` and `projects.validate_options()`.
- `stacked` requires `aspect == "9:16"`. `fit=contain` is ignored for `stacked`.
- Defaults: `facecam_box = null`, `facecam_height = 0.34` (range 0.20–0.60), seam offset 12px, fallback box `(0.0, 0.62, 0.26, 0.30)`, facecam expansion 25%.
- Caption bottom edge lands 12px below the seam: `margin_v = th - facecam_h - 12`.
- Full suite must pass before each commit.

---

### Task 1: Options and validation

**Files:** `projects.py`, `tests/test_chopify.py`

- [ ] **Step 1: Add failing tests**

```python
def test_facecam_options_defaults_and_validation():
    import projects
    o = projects.validate_options({})
    assert o["facecam_box"] is None
    assert o["facecam_height"] == 0.34
    assert projects.validate_options({"facecam_box": [0.1, 0.6, 0.2, 0.3]})["facecam_box"] == [0.1, 0.6, 0.2, 0.3]
    assert projects.validate_options({"layout": "stacked", "aspect": "9:16"})["layout"] == "stacked"
    for bad in (0.19, 0.61, -1, 2):
        try:
            projects.validate_options({"facecam_height": bad})
        except ValueError:
            pass
        else:
            raise AssertionError(f"facecam_height {bad} should fail")
    for bad in ([0.1, 0.2, 0.3], [0.1, 0.2, 0.3, 4], "0 0 1 1"):
        try:
            projects.validate_options({"facecam_box": bad})
        except ValueError:
            pass
        else:
            raise AssertionError(f"facecam_box {bad} should fail")
```

- [ ] **Step 2: Run to verify failure**

`python -m pytest -q tests/test_chopify.py -k facecam_options` → FAIL (`KeyError: 'facecam_height'`).

- [ ] **Step 3: Implement**

In `projects.py`, `DEFAULT_OPTIONS` add `"facecam_box": None, "facecam_height": 0.34,` and change `"layout": "single"` stay. In `validate_options()` replace the layout check with:

```python
    if result["layout"] not in {"single", "split", "stacked"}:
        raise ValueError("Layout must be single, split or stacked.")
    if result["layout"] in {"split", "stacked"} and result["aspect"] != "9:16":
        raise ValueError("Split-screen and gaming stacked layouts require aspect 9:16.")
    height = result["facecam_height"]
    if (isinstance(height, bool) or not isinstance(height, (int, float))
            or not math.isfinite(height) or not 0.20 <= height <= 0.60):
        raise ValueError("facecam_height must be a number from 0.20 to 0.60.")
    box = result["facecam_box"]
    if box is not None:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or not 0 <= v <= 1 for v in box)):
            raise ValueError("facecam_box must be four numbers from 0 to 1: x y w h.")
        result["facecam_box"] = list(box)
```

- [ ] **Step 4: Run to verify pass** → `python -m pytest -q tests/test_chopify.py`
- [ ] **Step 5: Commit** → `git commit -m "feat(facecam): add stacked layout options"`

---

### Task 2: Facecam box resolution

**Files:** `render_clips.py`, `tests/test_render_integration.py`

**Interfaces:** produces `resolve_facecam_box(source, W, H, workdir, box, detector=None)` → `((x, y, w, h), warning_or_none)`, normalized floats.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_render_integration.py`:

```python
def test_clamp_facecam_box():
    import render_clips
    assert render_clips.clamp_facecam_box([0.1, 0.6, 0.2, 0.3]) == (0.1, 0.6, 0.2, 0.3)
    assert render_clips.clamp_facecam_box([0.95, 0.95, 0.5, 0.5]) == (0.95, 0.95, 0.05, 0.05)
    assert render_clips.clamp_facecam_box([-2, 2, 0.01, 9]) == (0.0, 0.95, 0.05, 0.05)


def test_resolve_facecam_box_manual_and_fallback(tmp_path):
    import render_clips
    box, warning = render_clips.resolve_facecam_box(
        None, 1920, 1080, tmp_path, [0.05, 0.65, 0.25, 0.3])
    assert box == (0.05, 0.65, 0.25, 0.3) and warning is None
    box, warning = render_clips.resolve_facecam_box(
        None, 1920, 1080, tmp_path, None, detector=object())
    assert box == (0.0, 0.62, 0.26, 0.30) and warning
```

- [ ] **Step 2: Run to verify failure** → FAIL (`AttributeError`).

- [ ] **Step 3: Implement** (append to `render_clips.py`):

```python
DEFAULT_FACECAM_BOX = (0.0, 0.62, 0.26, 0.30)


def clamp_facecam_box(box):
    x, y, w, h = (max(0.0, min(1.0, float(v))) for v in box)
    x, y = min(x, 0.95), min(y, 0.95)
    return round(x, 6), round(y, 6), round(max(0.05, min(w, 1 - x)), 6), round(max(0.05, min(h, 1 - y)), 6)


def resolve_facecam_box(source, W, H, workdir, box, detector=None):
    """Return (normalized box, warning). Explicit box wins; detection is best-effort."""
    if box is not None:
        return clamp_facecam_box(box), None
    if source is None:
        return DEFAULT_FACECAM_BOX, "No source for facecam detection; using default box."
    try:
        frame_path = Path(workdir) / "_facecam.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5",
                        "-i", str(source), "-frames:v", "1", str(frame_path)],
                       check=True, capture_output=True)
        return _detect_facecam_box(frame_path, W, H, workdir, detector)
    except Exception as exc:
        return DEFAULT_FACECAM_BOX, f"Facecam detection failed ({exc}); using default box."


def _detect_facecam_box(frame_path, W, H, workdir, detector=None):
    import cv2
    image = cv2.imread(str(frame_path))
    if image is None:
        raise ValueError("Could not read sampled frame")
    kind, det = detector or _make_detector(workdir)
    ih, iw = image.shape[:2]
    if kind == "yunet":
        det.setInputSize((iw, ih))
        _, faces = det.detect(image)
        if faces is None or not len(faces):
            raise ValueError("No face found")
        best = max(faces, key=lambda f: f[2] * f[3])
        x, y, w, h = float(best[0]), float(best[1]), float(best[2]), float(best[3])
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = det.detectMultiScale(gray, 1.1, 5, minSize=(36, 36))
        if not len(faces):
            raise ValueError("No face found")
        x, y, w, h = (float(v) for v in max(faces, key=lambda f: f[2] * f[3]))
    pad = 0.25
    x -= w * pad; y -= h * pad; w *= 1 + 2 * pad; h *= 1 + 2 * pad
    box = (x / iw, y / ih, w / iw, h / ih)
    return clamp_facecam_box(box), None
```

The test's `detector=object()` makes `_make_detector` return the object as a non-yunet detector, the sampled source is `None` so the early branch returns the fallback — no ffmpeg runs.

- [ ] **Step 4: Run to verify pass** → `python -m pytest -q tests/test_render_integration.py -k facecam`
- [ ] **Step 5: Commit** → `git commit -m "feat(facecam): add box resolution with auto-detect fallback"`

---

### Task 3: Stacked render graph

**Files:** `render_clips.py`, `tests/test_render_integration.py`

**Interfaces:** `render(..., layout="stacked", facecam_box=None, facecam_height=0.34)` produces the standard artifacts.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_render_integration.py`:

```python
def test_stacked_layout_graph_and_seam_caption(tmp_path):
    import render_clips
    box, margin = render_clips.stacked_graph(1920, 1080, 1080, 1920, 0.34, (0.0, 0.62, 0.26, 0.30))
    assert "vstack=inputs=2" in box
    assert "split=2" in box
    assert "crop=498:324" in box        # facecam box
    assert "crop=920:1080" in box       # gameplay cover crop
    assert "scale=1080:652" in box
    assert margin == 1256


def test_stacked_layout_renders_real_video(source, tmp_path):
    out = render_clips.render(
        {"start": 0, "end": 2, "hook": "stacked"}, [{"word": "Hi", "start": 0.2, "end": 0.8}],
        source, 640, 360, tmp_path, "9:16", out_dir=tmp_path / "stacked",
        layout="stacked", facecam_box=[0.0, 0.6, 0.3, 0.35], preview=True)
    meta = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json",
                                      str(out)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == render_clips.PREVIEW["9:16"]
```

- [ ] **Step 2: Run to verify failure** → FAIL (`AttributeError: module 'render_clips' has no attribute 'stacked_graph'`).

- [ ] **Step 3: Implement** — add helper and wire into `render()`:

```python
def stacked_graph(W, H, tw, th, facecam_height, box, source=None, workdir=None,
                  facecam_box=None):
    """Return (filter graph fragment, ASS margin_v)."""
    if box is None:
        box, _ = resolve_facecam_box(source, W, H, workdir, facecam_box)
    fh = int(round(th * facecam_height) // 2 * 2)
    gh = th - fh
    fcw = max(2, int(round(box[2] * W)) // 2 * 2)
    fch = max(2, int(round(box[3] * H)) // 2 * 2)
    fcx = min(W - fcw, int(box[0] * W) // 2 * 2)
    fcy = min(H - fch, int(box[1] * H) // 2 * 2)
    gcw, gch, gcx, gcy = crop_for(W, H, tw, gh)
    graph = (f"[0:v]split=2[cam][game];"
             f"[cam]crop={fcw}:{fch}:{fcx}:{fcy},scale={tw}:{fh}[top];"
             f"[game]crop={gcw}:{gch}:{gcx}:{gcy},scale={tw}:{gh}[bottom];"
             f"[top][bottom]vstack=inputs=2[stack];"
             f"[stack]subtitles=_caption.ass[v]")
    return graph, th - fh - 12
```

In `render()` signature add `facecam_box=None, facecam_height=0.34`. Add validation `if layout == "stacked" and fit == "contain": fit = "cover"` silently. Replace the unconditional `build_ass(...)` call at `render_clips.py:645-646` with this guarded version:

```python
    if layout == "stacked":
        box, warning = resolve_facecam_box(source, W, H, workdir, facecam_box)
        if warning:
            print(f"  {warning}", flush=True)
        vf, seam_margin = stacked_graph(W, H, tw, th, facecam_height, box)
        caption_override = dict(caption or {})
        caption_override["margin"] = None
        build_ass(cap_words, start, start + dur, out_dir / "_caption.ass",
                  tw, th, font_size, seam_margin, style, brand, caption_override)
    else:
        build_ass(cap_words, start, start + dur, out_dir / "_caption.ass",
                  tw, th, font_size, margin_v, style, brand, caption)
```

Then make the framing branch skip stacked's already-built graph:

Then add `stacked` as the first branch of the framing chain (before
`if fit == "contain":`) — the graph `vf` was already built above, so only set
the mode here:

```python
    if layout == "stacked":
        mode = "gaming facecam + gameplay (stacked)"
    elif fit == "contain":
```

In the brand-logo block, add a stacked case before the existing `layout == "single"` prefixing, because the stacked graph already ends in `[v]`:

```python
        if layout == "stacked":
            graph = (vf + f";[1:v]scale={width}:-1[logo];"
                          "[v][logo]overlay=W-w-24:24:eof_action=repeat[out]")
            cmd[cmd.index("-c:v"):cmd.index("-c:v")] = [
                "-filter_complex", graph, "-map", "[out]", "-map", "0:a?"]
        elif layout == "single":
            vf = "[0:v]" + vf
```

Finally add a dedicated output branch immediately after the existing
`elif layout == "split":` branch. Leave the split branch untouched:

```python
    elif layout == "stacked":
        cmd[cmd.index("-c:v"):cmd.index("-c:v")] = [
            "-filter_complex", vf, "-map", "[v]", "-map", "0:a?"]
```

The stacked graph already ends in `[v]`, so it needs no concatenation suffix.

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(facecam): stacked render graph with seam captions"`

---

### Task 4: Live, CLI, and Studio wiring

**Files:** `live_clips.py`, `chopify.py`, `assets/studio/index.html`, `assets/studio/app.js`, tests

- [ ] **Step 1: Failing tests**

```python
def test_live_writer_forwards_facecam_options(tmp_path, monkeypatch):
    import projects
    from live_clips import LiveClipWriter, Trigger
    options = projects.validate_options({"layout": "stacked", "facecam_box": [0, 0.6, 0.3, 0.3], "facecam_height": 0.4})
    segment = tmp_path / "segment00000000.ts"
    segment.write_bytes(b"x")
    writer = LiveClipWriter(tmp_path / "s")
    monkeypatch.setattr(live_clips, "stitch", lambda p, d, runner=None: d)
    seen = {}
    def fake_render(seg, words, source, W, H, workdir, aspect, **kw):
        seen.update(kw)
    writer.write(Trigger(20.0, 9.0, {}), options, {"width": 640, "height": 360},
                 [], [segment], renderer=fake_render)
    assert seen["layout"] == "stacked"
    assert seen["facecam_box"] == [0, 0.6, 0.3, 0.3]
    assert seen["facecam_height"] == 0.4
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement**

In `live_clips.LiveClipWriter.write()`, add to the `render_fn(...)` kwargs:
`layout=options["layout"], centers=options["centers"], caption=options["caption"], fit=options["fit"], facecam_box=options["facecam_box"], facecam_height=options["facecam_height"]`.

In `chopify.py` add flags after `--layout`:

```python
    ap.add_argument("--facecam-box", type=float, nargs=4, default=None,
                    metavar=("X", "Y", "W", "H"),
                    help="normalized facecam rectangle x y w h (0-1) for --layout stacked")
    ap.add_argument("--facecam-height", type=float, default=None,
                    help="facecam panel fraction of output height, 0.20-0.60 (default 0.34)")
```

In Studio `index.html`, add `<option value="stacked">Gaming (facecam + gameplay)</option>` to the layout select, and add to the settings form:

```html
<label for="facecam-box">Facecam box (x y w h, blank = auto)</label>
<input id="facecam-box" placeholder="0 0.62 0.26 0.30">
<label for="facecam-height">Facecam height</label>
<input id="facecam-height" type="number" min="0.2" max="0.6" step="0.01" value="0.34">
```

In `app.js`, when collecting settings for `/settings`, add:

```js
  const boxText = ($("#facecam-box")?.value || "").trim();
  if (boxText) {
    const parts = boxText.split(/[\s,]+/).map(Number);
    if (parts.length === 4) settings.facecam_box = parts;
  }
  const height = Number($("#facecam-height")?.value);
  if (Number.isFinite(height)) settings.facecam_height = height;
```

- [ ] **Step 4: Run full suite** → `python -m pytest -q`
- [ ] **Step 5: Commit** → `git commit -m "feat(facecam): wire stacked layout into live, CLI and Studio"`

---

### Task 5: Docs and final verification

**Files:** `docs/CLI.md`, `docs/LIVE.md`, `README.md`, `CHANGELOG.md`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`

- [ ] **Step 1: CLI.md** — add `--layout stacked` row, `--facecam-box`, `--facecam-height` with defaults/ranges.
- [ ] **Step 2: LIVE.md / README.md** — add example:

```bash
chopify --live URL --layout stacked --aspect 9:16 \
  --facecam-box 0 0.62 0.26 0.30 --facecam-height 0.34
```

and note auto-detect when the box is omitted.
- [ ] **Step 3: CHANGELOG.md** — under `[Unreleased] → Added`: "Gaming `stacked` layout: facecam above gameplay with captions at the seam, for batch and live clips."
- [ ] **Step 4: CI** — no new compile target (no new module); confirm `python -m compileall -q ...` list unchanged.
- [ ] **Step 5: Verify** — `python -m pytest -q`, `python tools/build_site.py --out _site`, `node --check assets/studio/app.js`.
- [ ] **Step 6: Commit** → `git commit -m "docs(facecam): document gaming stacked layout"`

---

## Self-review notes

- Spec coverage: options (Task 1), box resolution + fallback (Task 2), graph + seam captions (Task 3), live/CLI/Studio (Task 4), docs (Task 5).
- Type consistency: `resolve_facecam_box` returns normalized tuple `(x,y,w,h)`; `stacked_graph` consumes it and returns `(graph, margin_v)`; `facecam_box` stays a list in options and a tuple after clamping.
- Known ambiguity resolved: `stacked` forces `fit=cover` behavior; the renderer silently normalizes `fit` rather than rejecting the combination.
