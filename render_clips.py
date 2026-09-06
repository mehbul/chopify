"""
Stage 3 of the viral-clip pipeline.

Renders scored segments to clips in a chosen aspect ratio:
  - 16:9 (default) -> landscape, full frame kept
  - 9:16           -> vertical, dynamic speaker-tracking crop (YuNet + snap-on-cut)
  - 1:1            -> square
Captions are word-by-word CapCut style, burnt in via the ffmpeg ASS engine,
with selectable style presets. Optional --tighten removes filler words and
long silences (captions are re-timed automatically). Every clip gets a PNG
poster and a .meta.json with a ready-to-post title, description and hashtags.

No external/paid APIs. ffmpeg + OpenCV only. Usage:
    python render_clips.py [workdir] [--aspect 16:9|9:16|1:1]
                           [--out DIR] [--style default|hormozi|mrbeast|podcast]
                           [--tighten] [--loudnorm] [--preview]
Output directory (default ./clips) can also be set with $CHOPIFY_OUT.
"""
import sys
import os
import re
import json
import argparse
import subprocess
from pathlib import Path

import tighten  # local module: filler/silence removal

# aspect -> (target_w, target_h, caption_font_size, caption_margin_v)
ASPECTS = {
    "16:9": (1920, 1080, 72, 95),
    "9:16": (1080, 1920, 96, 300),
    "1:1":  (1080, 1080, 82, 120),
}
DEFAULT_ASPECT = "16:9"

# --preview drafts: small, fast, no poster/meta. Caption font/margins scale down
# proportionally from the full-size ASPECTS values (height is the anchor).
PREVIEW = {
    "16:9": (854, 480),
    "9:16": (480, 854),
    "1:1":  (480, 480),
}

# Caption presets: ASS &HAABBGGRR colours (yellow highlight = &H0000FFFF&).
CAPTION_STYLES = {
    "default":  {"font": "Arial Black", "weight": -1, "highlight": "&H00FFFF&"},
    "hormozi":  {"font": "Arial Black", "weight": -1, "highlight": "&H0000FF&"},
    "mrbeast":  {"font": "Arial Black", "weight": -1, "highlight": "&H00FF00&"},
    "podcast":  {"font": "Arial",       "weight": 0,  "highlight": "&H00FFFF&"},
}
DEFAULT_STYLE = "default"


def default_out():
    """Output dir: --out wins, then $CHOPIFY_OUT, then ./clips (no more hardcoded C:\\clips)."""
    return Path(os.environ.get("CHOPIFY_OUT") or "clips")


def get_style(name):
    if name not in CAPTION_STYLES:
        raise SystemExit(f"Unknown caption style '{name}'. "
                         f"Choose from: {', '.join(CAPTION_STYLES)}")
    return CAPTION_STYLES[name]

WORDS_PER_LINE = 3
DET_FPS = 5
DET_W = 640
EMA_ALPHA = 0.20
JUMP_FRAC = 0.22
MARGIN_FRAC = 0.18
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")


def sanitize(text, maxlen=70):
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:maxlen].strip("-") or "clip"


def ffprobe_dims(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def crop_for(W, H, tw, th):
    """Largest centred crop of the WxH source matching the target aspect."""
    tar = tw / float(th)
    src = W / float(H)
    if abs(src - tar) < 1e-3:
        cw, ch = W, H
    elif src > tar:                      # source wider -> crop width
        ch = H
        cw = int(round(H * tar))
    else:                                # source taller -> crop height
        cw = W
        ch = int(round(W / tar))
    cw -= cw % 2
    ch -= ch % 2
    x0 = max(0, (W - cw) // 2)
    y0 = max(0, (H - ch) // 2)
    return cw, ch, x0, y0


def _make_detector(workdir):
    import cv2
    model = Path(workdir).parent / "yunet.onnx"
    if not model.exists():
        try:
            import urllib.request
            urllib.request.urlretrieve(YUNET_URL, str(model))
        except Exception as e:
            print("  YuNet download failed -> Haar:", e, flush=True)
    if model.exists():
        try:
            return "yunet", cv2.FaceDetectorYN.create(str(model), "", (DET_W, 360), 0.6, 0.3, 5000)
        except Exception as e:
            print("  YuNet init failed -> Haar:", e, flush=True)
    return "haar", cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def detect_track(source, start, dur, W, workdir):
    """Low-res detection pass -> [(t_rel, center_x_source_px or None)]."""
    import cv2
    det_path = Path(workdir) / "_det.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source),
         "-t", f"{dur:.3f}", "-vf", f"fps={DET_FPS},scale={DET_W}:-2",
         "-an", str(det_path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    kind, det = _make_detector(workdir)
    cap = cv2.VideoCapture(str(det_path))
    dw = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or DET_W
    dh = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360
    sx = W / float(dw)
    if kind == "yunet":
        det.setInputSize((int(dw), int(dh)))
    track = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = i / float(DET_FPS)
        cx = None
        if kind == "yunet":
            _, faces = det.detect(frame)
            if faces is not None and len(faces):
                best = max(faces, key=lambda f: f[2] * f[3] * float(f[14]))
                cx = (best[0] + best[2] / 2.0) * sx
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = det.detectMultiScale(gray, 1.1, 5, minSize=(36, 36))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                cx = (x + w / 2.0) * sx
        track.append((t, cx))
        i += 1
    cap.release()
    try:
        det_path.unlink()
    except OSError:
        pass
    return track


def smooth_track(track, W, crop_w):
    half = crop_w / 2.0
    max_off = half - crop_w * MARGIN_FRAC
    jump = W * JUMP_FRAC
    known = [c for _, c in track if c is not None]
    base = sorted(known)[len(known) // 2] if known else W / 2.0
    filled = []
    last = base
    for t, c in track:
        if c is None:
            c = last
        last = c
        filled.append((t, c))
    if not filled:
        return [(0.0, min(max(base, half), W - half))]
    out = []
    c = filled[0][1]
    prev_face = filled[0][1]
    for t, face in filled:
        if abs(face - prev_face) > jump:
            c = face
        else:
            c += EMA_ALPHA * (face - c)
        if face - c > max_off:
            c = face - max_off
        elif c - face > max_off:
            c = face + max_off
        prev_face = face
        out.append((t, min(max(c, half), W - half)))
    return out


def build_sendcmd(track, crop_w, path):
    lines = []
    last = None
    for t, cx in track:
        x = int(round(cx - crop_w / 2.0))
        if last is None or abs(x - last) >= 2:
            lines.append(f"{t:.2f} crop x {x};")
            last = x
    if not lines:
        lines = ["0.0 crop x 0;"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(words, clip_start, clip_end, path, tw, th, font_size, margin_v,
              style_name=DEFAULT_STYLE):
    st = get_style(style_name)
    sub = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {tw}\nPlayResY: {th}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{st['font']},{font_size},&H00FFFFFF,&H000000FF,&H00000000,"
        f"&H64000000,{st['weight']},0,0,0,100,100,0,0,1,5,2,2,80,80,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    groups = [sub[i:i + WORDS_PER_LINE] for i in range(0, len(sub), WORDS_PER_LINE)]
    events = []
    hl = r"{\c" + st["highlight"] + "}"
    white = r"{\c&HFFFFFF&}"
    for g in groups:
        for i, w in enumerate(g):
            s0 = max(w["start"], clip_start) - clip_start
            en = (g[i + 1]["start"] - clip_start) if i + 1 < len(g) \
                else (w["end"] - clip_start)
            if en <= s0:
                en = s0 + 0.12
            parts = []
            for j, ww in enumerate(g):
                token = ass_escape(ww["word"].strip())
                parts.append((hl + token + white) if j == i else (white + token))
            events.append(
                f"Dialogue: 0,{ass_time(s0)},{ass_time(en)},Cap,,0,0,0,,{' '.join(parts)}")
    Path(path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def write_meta(seg, out_path):
    """Deterministic ready-to-post metadata saved as <clip>.meta.json."""
    hook = str(seg.get("hook", "clip")).replace("-", " ").strip()
    title = hook.capitalize()
    meta = {
        "title": f"{title} #shorts",
        "description": (f"{hook.capitalize()} - full video on the channel.\n"
                        "#shorts #reels #fyp #viral #podcast"),
        "hashtags": ["#shorts", "#reels", "#fyp", "#viral"],
        "score": seg.get("overall"),
        "start": seg.get("start"),
        "end": seg.get("end"),
    }
    Path(out_path).with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(out_path).with_suffix(".meta.json")


def render(seg, words, source, W, H, workdir, aspect, out_dir=None, style=DEFAULT_STYLE,
           do_tighten=False, loudnorm=False, preview=False):
    out_dir = Path(out_dir) if out_dir else default_out()
    full_w, full_h, font_size, margin_v = ASPECTS[aspect]
    if preview:
        # fast 480p-class draft: smaller frame, smaller captions, no poster/meta
        tw, th = PREVIEW[aspect]
        scale = th / float(full_h)
        font_size = max(12, round(font_size * scale))
        margin_v = max(10, round(margin_v * scale))
    else:
        tw, th = full_w, full_h
    start = float(seg["start"])
    end = float(seg["end"])
    dur = end - start
    cw, ch, x0, y0 = crop_for(W, H, tw, th)

    cap_words = words
    render_src = source
    if do_tighten:
        keeps = tighten.plan_cuts(words, start, end)
        render_src = out_dir / "_tight_source.mp4"
        tighten.apply_cuts(source, keeps, start, render_src)
        cap_words, dur = tighten.remap_words(words, start, keeps)
        print(f"  tightened: {len(keeps)} keep-ranges, "
              f"{(end - start) - dur:.1f}s removed", flush=True)
        start = 0.0
    build_ass(cap_words, start, start + dur, out_dir / "_caption.ass",
              tw, th, font_size, margin_v, style)

    needs_track = cw < W * 0.95          # real horizontal crop -> track the speaker
    if needs_track:
        track = smooth_track(detect_track(render_src, start, dur, W, workdir), W, cw)
        build_sendcmd(track, cw, out_dir / "_crop.cmd")
        init_x = max(0, min(int(round(track[0][1] - cw / 2.0)), W - cw))
        vf = (f"sendcmd=f=_crop.cmd,crop={cw}:{ch}:{init_x}:{y0},"
              f"scale={tw}:{th},subtitles=_caption.ass")
        mode = "speaker-tracked"
    else:
        vf = f"crop={cw}:{ch}:{x0}:{y0},scale={tw}:{th},subtitles=_caption.ass"
        mode = "full-frame"

    suffix = ".preview" if preview else ""
    out = out_dir / (sanitize(seg.get("hook", "clip")) + suffix + ".mp4")
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(render_src),
           "-t", f"{dur:.3f}", "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    if loudnorm:
        cmd[cmd.index("-c:v"):cmd.index("-c:v")] = [
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=str(out_dir))

    for tmpf in (out_dir / "_caption.ass", out_dir / "_crop.cmd"):
        try:
            tmpf.unlink()
        except OSError:
            pass

    if do_tighten:
        try:
            render_src.unlink()
        except OSError:
            pass

    if preview:
        # drafts skip the poster and metadata - they exist to be eyeballed fast
        print(f"SAVED {out}  ({aspect} {tw}x{th} preview, {mode})", flush=True)
        return out

    # poster: a representative still saved next to the clip (<clip>.png)
    poster = out.with_suffix(".png")
    subprocess.run(["ffmpeg", "-y", "-ss", f"{dur * 0.4:.2f}", "-i", str(out),
                    "-frames:v", "1", "-q:v", "2", str(poster)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    meta_path = write_meta(seg, out)

    print(f"SAVED {out}  ({aspect} {tw}x{th}, {mode})  "
          f"+ poster {poster.name} + {meta_path.name}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", nargs="?", default="work")
    ap.add_argument("--aspect", default=DEFAULT_ASPECT, choices=list(ASPECTS),
                    help="output aspect ratio (default 16:9)")
    ap.add_argument("--out", default=None,
                    help="output directory (default ./clips, or $CHOPIFY_OUT)")
    ap.add_argument("--style", default=DEFAULT_STYLE,
                    help="caption preset: " + ", ".join(CAPTION_STYLES))
    ap.add_argument("--tighten", action="store_true",
                    help="remove filler words (um, uh...) and long silences")
    ap.add_argument("--loudnorm", action="store_true",
                    help="normalize audio to -14 LUFS (platform standard)")
    ap.add_argument("--preview", action="store_true",
                    help="render fast 480p drafts (no poster/meta) to review "
                         "clip selection before the full render")
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    out_dir = Path(args.out) if args.out else default_out()
    out_dir.mkdir(parents=True, exist_ok=True)

    transcript = json.loads((workdir / "transcript.json").read_text(encoding="utf-8-sig"))
    segments = json.loads((workdir / "segments.json").read_text(encoding="utf-8-sig"))
    words = transcript["words"]

    source = Path(transcript["video"])
    if not source.exists():
        cands = sorted(workdir.glob("source.*"))
        source = cands[0] if cands else source
    source = source.resolve()

    W, H = ffprobe_dims(source)
    print(f"Source {source} {W}x{H}; {len(segments)} clips -> {args.aspect} "
          f"(style={args.style}, tighten={args.tighten}) -> {out_dir}", flush=True)

    saved = []
    for i, seg in enumerate(segments, 1):
        print(f"--- Clip {i}/{len(segments)}: "
              f"{seg.get('hook', '')[:60]!r} (overall {seg.get('overall')}) ---",
              flush=True)
        try:
            saved.append(str(render(seg, words, source, W, H, workdir, args.aspect,
                                    out_dir=out_dir, style=args.style,
                                    do_tighten=args.tighten, loudnorm=args.loudnorm,
                                    preview=args.preview)))
        except subprocess.CalledProcessError as e:
            print(f"ERROR rendering clip {i}: {e}", flush=True)

    print("RENDERED", len(saved), "clips:", flush=True)
    for s in saved:
        print("  ", s, flush=True)


if __name__ == "__main__":
    main()
