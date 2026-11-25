from pathlib import Path
p = Path("auto_filter_puffco.py")
txt = p.read_text()

old_block = '''def analyze_clip_vision(frames, snippet):'''
idx = txt.find(old_block)
end_idx = txt.find("def write_ass(")

new_fn = '''import time
def get_flash_models():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    try:
        models = json.loads(urllib.request.urlopen(url, timeout=15).read()).get("models", [])
        avail = [m["name"].replace("models/", "") for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
        ordered = [m for pref in ("gemini-3.8-flash", "gemini-2.0-flash", "gemini-flash-latest", "gemini-3.8-pro", "gemini-2.5-pro") for m in avail if pref in m]
        return list(dict.fromkeys(ordered)) or ["gemini-3.8-flash", "gemini-2.0-flash"]
    except Exception:
        return ["gemini-3.8-flash", "gemini-2.0-flash"]

FLASH_MODELS = get_flash_models()
print("🤖 Fallback Vision models:", FLASH_MODELS[:5])

def analyze_clip_vision(frames, snippet):
    parts = [{
        "text": (
            "You are a strict compliance reviewer for the \x27BenDaDonnn x Puffco GTA 6\x27 campaign.\\n"
            f"Transcript snippet: {snippet[:300]}\\n"
            "Look at these 4 frames from the streamer\x27s clip.\\n"
            "Rule: The clip MUST visibly show BenDaDonnn holding/hitting a Puffco vaporizer (Peak, Pivot, Proxy, pipe/dab rig), "
            "blowing a visible smoke/vapor cloud, inspecting/smelling hash jars, or interacting with his Hash Butler (Rayquan).\\n"
            "If he is only sitting/playing GTA with ZERO Puffco device, vapor cloud, jar, or butler visible, reply has_puffco: false.\\n"
            "Reply ONLY valid JSON: {\\"has_puffco\\": true/false, \\"element\\": \\"short description of what is visible\\", \\"hook\\": \\"short 5-8 word viral caption hook\\"}"
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
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read().decode())
                txt = data["candidates"][0]["content"]["parts"][0]["text"]
                m = re.search(r"\\{.*\\}", txt, re.S)
                return json.loads(m.group(0)) if m else {"has_puffco": False, "element": "parse_error", "hook": ""}
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code} ({model_name}): {e.read().decode(errors=\x27ignore\x27)[:120]}"
                if e.code in (429, 503):
                    time.sleep(2 * (attempt + 1))
                    continue
                break
    raise RuntimeError(last_err or "All vision models failed")

'''
p.write_text(txt[:idx] + new_fn + txt[end_idx:])
print("✅ Patched auto_filter_puffco.py with retry + fallback!")
