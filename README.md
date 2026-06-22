# Viral Clip Pipeline

Turn a long YouTube video into vertical (9:16) short-form clips, fully on-device — **no paid APIs, no cloud**.

Downloads a video, transcribes it locally, scores the transcript for "virality",
cuts the winning segments, reframes them to 9:16 while **tracking the speaker''s
face**, and burns in animated **word-by-word (CapCut-style) captions**.

## Stack
- **yt-dlp** — download (highest quality)
- **faster-whisper** — local transcription, word-level timestamps (CPU `medium` default)
- **ffmpeg** — cut, crop, caption burn-in
- **OpenCV (YuNet)** — face detection for a dynamic, speaker-tracking 9:16 crop
- Captions via the ffmpeg **ASS** engine (bold, white + yellow active word, black outline)

## Requirements
- Windows, Python 3.11+
- `ffmpeg` + `ffprobe` on PATH — `winget install Gyan.FFmpeg`
- `deno` on PATH (yt-dlp JS-challenge solving) — `winget install DenoLand.Deno`

## Install
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt

## Usage
**1. Download + transcribe** -> writes `work/transcript.json`:

    .venv\Scripts\python download_and_transcribe.py "<YOUTUBE_URL>"

**2. Score** -> write `work/segments.json` with the segments to clip. Each entry:

    { "start": 134.2, "end": 187.6, "hook": "short title for the filename", "overall": 8.4 }

Scoring reads `transcript.json` and rates segments on hook / shock / humour /
controversy / insight / emotion / energy / complete-arc. In the reference setup
an LLM agent does this in-loop (zero API cost); you can score it however you like.

**3. Render** -> cut + 9:16 speaker-tracking crop + burnt captions -> `C:\clips`:

    .venv\Scripts\python render_clips.py work

## Notes
- **GPU:** faster-whisper (CTranslate2) is **CUDA/NVIDIA-only**; on AMD it runs on CPU.
  (whisper.cpp + Vulkan was attempted for AMD but is unstable on RDNA3 — crashes or
  returns corrupted output.)
- **Captions:** built-in ffmpeg ASS renderer (the PyPI `pycaps` is an empty stub).
- Output folder is `C:\clips` (change `OUT_DIR` in `render_clips.py`).

Personal / educational use — you are responsible for the rights to any video you process.