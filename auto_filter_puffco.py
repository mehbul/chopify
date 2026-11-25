import os, re, json, base64, subprocess, urllib.request, urllib.error
from pathlib import Path

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("ERROR: Run `export GEMINI_API_KEY='your_key'` first!")

PROJ = Path("projects/5095c962e610")
VOD_URL = "https://www.twitch.tv/videos/2933247628"
SECTIONS = PROJ / "verified_sections"
FRAMES = PROJ / "vision_frames"
OUT = PROJ / "verified_puffco_clips"
for d in (SECTIONS, FRAMES, OUT):
    d.mkdir(exist_ok=True)

MODEL = "gemini-3.8-flash"

# 1. Load candidates & transcript
raw_c = json.loads((PROJ / "candidates.json").read_text(encoding="utf-8", errors="ignore"))
cands = raw_c if isinstance(raw_c, list) else (raw_c.get("candidates") or raw_c.get("clips") or [])
raw_t = json.loads((PROJ / "transcript.json").read_text(encoding="utf-8", errors="ignore"))
t_segs = raw_t if isinstance(raw_t, list) else (raw_t.get("segments") or [])

PAT = re.compile(r"\b(puff\s*co|puffy|puff\s*car|puff\s*coast|peak|pivot|proxy|hash|butler|rayquan|rek\s*kwan|dab|cloud|vapor|jar|terp|sponsor)\b", re.I)

# Deduplicate non-overlapping moments (at least 45s apart), prioritize strong hits
unique_hits = []
last_st = -999.0
for c in cands:
    st, en = float(c["start"]), float(c["end"])
    txt = c.get("text") or ""
    matches = list({m.group(1).lower() for m in PAT.finditer(txt)})
    if not matches:
        continue
    if st - last_st < 45.0:
        continue
    # Clamp duration to 18-45s for tight Reels/Shorts
    dur = min(45.0, max(18.0, en - st))
    en_clamped = st + dur
    unique_hits.append({
        "start": st, "end": en_clamped, "dur": dur,
        "score": float(c.get("overall") or 0),
        "matches": matches, "text": txt
    })
    last_st = st

# Sort by number of distinct keyword matches + score, cap at top 30 to keep fast
unique_hits.sort(key=lambda x: (len(x["matches"]), x["score"]), reverse=True)
unique_hits = unique_hits[:30]
unique_hits.sort(key=lambda x: x["start"])
print(f"🔎 Found {len(unique_hits)} unique Puffco/Hash/Butler candidate moments across the VOD.")

import time
def get_flash_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        models = json.loads(urllib.request.urlopen(url, timeout=15).read()).get("models", [])
        avail = [m["name"].replace("models/", "") for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
        ordered = [m for pref in ("gemini-flash-latest", "gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-2.5-pro") for m in avail if pref in m and "tts" not in m and "image" not in m]
        return list(dict.fromkeys(ordered)) or ["gemini-3.8-flash", "gemini-2.0-flash"]
    except Exception:
        return ["gemini-3.8-flash", "gemini-2.0-flash"]

FLASH_MODELS = get_flash_models()
print("🤖 Fallback Vision models:", FLASH_MODELS[:5])

def analyze_clip_vision(frames, snippet):
    parts = [{
        "text": (
            "You are a strict compliance reviewer for the 'BenDaDonnn x Puffco GTA 6' campaign.\n"
            f"Transcript snippet: {snippet[:300]}\n"
            "Look at these 4 frames from the streamer's clip.\n"
            "Rule: The clip MUST visibly show BenDaDonnn holding/hitting a Puffco vaporizer (Peak, Pivot, Proxy, pipe/dab rig), "
            "blowing a visible smoke/vapor cloud, inspecting/smelling hash jars, or interacting with his Hash Butler (Rayquan).\n"
            "If he is only sitting/playing GTA with ZERO Puffco device, vapor cloud, jar, or butler visible, reply has_puffco: false.\n"
            "Reply ONLY valid JSON: {\"has_puffco\": true/false, \"element\": \"short description of what is visible\", \"hook\": \"short 5-8 word viral caption hook\"}"
        )
    }]
    for f in frames:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f.read_bytes()).decode()}})
    body = json.dumps({"contents": [{"parts": parts}]}).encode()
    last_err = None
    for model_name in FLASH_MODELS[:4]:
        for attempt in range(2):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={API_KEY}"
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode())
                txt = data["candidates"][0]["content"]["parts"][0]["text"]
                m = re.search(r"\{.*\}", txt, re.S)
                return json.loads(m.group(0)) if m else {"has_puffco": False, "element": "parse_error", "hook": ""}
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code} ({model_name}): {e.read().decode(errors='ignore')[:120]}"
                if e.code in (429, 503):
                    time.sleep(2 * (attempt + 1))
                    continue
                break
    raise RuntimeError(last_err or "All vision models failed")

def write_ass(ass_path, st, en):
    # Pull word-level or segment-level captions from transcript.json
    lines = []
    for seg in t_segs:
        s0, s1 = float(seg.get("start", 0)), float(seg.get("end", 0))
        if s1 < st or s0 > en:
            continue
        words = seg.get("words") or []
        if words:
            chunk = []
            for w in words:
                ws, we = float(w.get("start", s0)), float(w.get("end", s1))
                if we < st or ws > en:
                    continue
                chunk.append((max(0.0, ws - st), max(0.05, we - st), w.get("word", "").strip()))
                if len(chunk) == 4:
                    lines.append((chunk[0][0], chunk[-1][1], " ".join(x[2] for x in chunk).upper()))
                    chunk = []
            if chunk:
                lines.append((chunk[0][0], chunk[-1][1], " ".join(x[2] for x in chunk).upper()))
        else:
            txt = (seg.get("text") or "").strip().upper()
            if txt:
                lines.append((max(0.0, s0 - st), min(en - st, s1 - st), txt))

    def fmt(t):
        return f"0:{int(t//60):02d}:{t%60:05.2f}"

    hdr = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: SeamCap,Arial Black,56,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,5,60,60,360,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    ev = [f"Dialogue: 0,{fmt(a)},{fmt(b)},SeamCap,,0,0,0,,{txt}" for a, b, txt in lines if b > a]
    ass_path.write_text(hdr + "\n".join(ev) + "\n", encoding="utf-8")

report = []
verified_files = []

for idx, h in enumerate(unique_hits, 1):
    st, en = h["start"], h["end"]
    ts_label = f"{int(st//60)}m{int(st%60):02d}s-{int(en//60)}m{int(en%60):02d}s"
    raw_seg = SECTIONS / f"seg_{idx:02d}.mp4"

    if not raw_seg.exists() or raw_seg.stat().st_size < 10000:
        print(f"[{idx}/{len(unique_hits)}] Downloading {ts_label} ({h['matches']})...")
        subprocess.run([
            "python", "-m", "yt_dlp", "--no-playlist",
            "--download-sections", f"*{st:.1f}-{en:.1f}",
            "--force-keyframes-at-cuts",
            "-f", "bv[height<=1080]+ba/b",
            "--merge-output-format", "mp4",
            "-o", str(raw_seg), VOD_URL
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not raw_seg.exists() or raw_seg.stat().st_size < 10000:
        print(f"  ✗ Download failed for {ts_label}")
        continue

    for old in FRAMES.glob(f"s{idx}_*.jpg"):
        old.unlink()
    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw_seg),
        "-vf", f"fps={4.0/h['dur']:.3f},crop=iw*0.30:ih*0.35:0:ih*0.58,scale=384:-1", "-frames:v", "4",
        str(FRAMES / f"s{idx}_%d.jpg")
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    frames = sorted(FRAMES.glob(f"s{idx}_*.jpg"))
    if not frames:
        continue

    try:
        res = analyze_clip_vision(frames, h["text"])
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode(errors='ignore')[:180]}")
        continue
    except Exception as e:
        print(f"  Vision error: {e}")
        continue

    ok = bool(res.get("has_puffco"))
    elem = res.get("element", "")
    hook = res.get("hook", "")
    print(f"  {'✅ KEEP' if ok else '❌ REJECT'} | {ts_label} | {elem}")
    report.append({"idx": idx, "timestamps": ts_label, "start": st, "end": en, "has_puffco": ok, "element": elem, "hook": hook, "matches": h["matches"]})

    if ok:
        ass_p = SECTIONS / f"seg_{idx:02d}.ass"
        write_ass(ass_p, st, en)
        out_mp4 = OUT / f"puffco_{idx:02d}_{ts_label}.mp4"
        # Stacked 9:16 layout: Facecam (bottom-left 0, 0.62, 0.26, 0.30) on top (1080x650), gameplay below (1080x1270)
        fc = (
            "[0:v]crop=iw*0.26:ih*0.30:0:ih*0.62,scale=1080:650:force_original_aspect_ratio=increase,crop=1080:650[cam];"
            "[0:v]scale=1080:1270:force_original_aspect_ratio=increase,crop=1080:1270[game];"
            "[cam][game]vstack=inputs=2[stacked];"
            f"[stacked]subtitles={ass_p}[vout]"
        )
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_seg),
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(out_mp4)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out_mp4.exists() and out_mp4.stat().st_size > 10000:
            print(f"     🎬 Rendered: {out_mp4.name} ({out_mp4.stat().st_size//1024} KB)")
            verified_files.append(str(out_mp4))

(PROJ / "VERIFIED_PUFFCO_REPORT.json").write_text(json.dumps(report, indent=2))
if verified_files:
    subprocess.run(["zip", "-j", str(PROJ / "VERIFIED_PUFFCO_CLIPS.zip"), *verified_files], check=True)
    print(f"\n🎉 DONE! {len(verified_files)} verified Puffco clips zipped -> {PROJ / 'VERIFIED_PUFFCO_CLIPS.zip'}")
else:
    print("\n⚠️ 0 clips passed visual verification. Check VERIFIED_PUFFCO_REPORT.json.")
