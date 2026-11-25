from pathlib import Path
p = Path("auto_filter_puffco.py")
txt = p.read_text()

txt = txt.replace(
    'ordered = [m for pref in ("gemini-3.8-flash", "gemini-2.0-flash", "gemini-flash-latest", "gemini-3.8-pro", "gemini-2.5-pro") for m in avail if pref in m]',
    'ordered = [m for pref in ("gemini-flash-latest", "gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-2.5-pro") for m in avail if pref in m and "tts" not in m and "image" not in m]'
)
txt = txt.replace('timeout=45', 'timeout=90')
txt = txt.replace('scale=640:-1', 'crop=iw*0.30:ih*0.35:0:ih*0.58,scale=384:-1')
p.write_text(txt)
print("✅ Updated model order + facecam crop + 90s timeout!")
