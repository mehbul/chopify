<p align="center">
  <img src="assets/banner.svg" alt="Chopify" width="880">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/100%25-Local-08d9d6" alt="Local">
  <img src="https://img.shields.io/badge/Paid_APIs-zero-ff2e63" alt="No paid APIs">
  <img src="https://img.shields.io/badge/ffmpeg-powered-007808?logo=ffmpeg&logoColor=white" alt="ffmpeg">
  <img src="https://img.shields.io/badge/OpenCV-YuNet-5C3EE8?logo=opencv&logoColor=white" alt="OpenCV">
  <img src="https://img.shields.io/badge/Windows-11-0078D6?logo=windows&logoColor=white" alt="Windows">
</p>

<p align="center"><b>Drop in a YouTube link &#8594; get back scored, captioned clips (16:9 or 9:16) &#8212; 100% on your own machine.</b></p>

---

**Chopify is a free, open-source, fully local alternative to Opus Clip, Vizard, Klap, 2Short and Submagic.** It automatically turns long videos &#8212; YouTube videos, podcasts, interviews and webinars &#8212; into short, ready-to-post viral clips in **16:9, 9:16 (vertical) or 1:1**, with AI virality scoring, automatic face-tracking reframe, and burnt-in word-by-word captions. Everything runs **100% on your own machine**: no subscription, no cloud upload, and no API keys.

> [!TIP]
> **New in v1.1** &#8212; output is now **16:9 full-frame by default** (the whole screen, no crop). Add `--aspect 9:16` for vertical or `--aspect 1:1` for square, and **every clip auto-saves a PNG poster** next to the `.mp4`.

## Demo

<p align="center"><img src="assets/demo.gif" alt="16:9 clip with burnt word-by-word captions" width="640"></p>

## How it works

<p align="center">
  <img src="assets/pipeline.svg" alt="Pipeline" width="980">
</p>

## Features

- **AI virality scoring** &#8212; every segment is rated on hook, shock, humour, controversy, insight, emotion, energy and complete-arc; only **8+/10** clips are kept.
- **Local transcription** &#8212; faster-whisper with word-level timestamps. Nothing leaves your PC.
- **Multi-format output** &#8212; 16:9 (default, full frame), 9:16 (speaker-tracking auto-reframe via OpenCV YuNet, snap-on-cut + edge guard), or 1:1.
- **PNG poster per clip** &#8212; a ready thumbnail saved next to every clip.
- **Word-by-word captions** &#8212; bold CapCut style, burnt in with ffmpeg.
- **Zero paid APIs, zero cloud** &#8212; yt-dlp + faster-whisper + ffmpeg + OpenCV.
- **Content decides the count** &#8212; a 1-hour video might yield 2 clips or 20.

## Chopify vs. the paid tools

Chopify does the core of what these subscription products do &#8212; **AI-scored clips, auto-reframe to 9:16, and burnt word-by-word captions** &#8212; except it runs **free, on your own machine, and fully open-source**.

| Product | Pricing | Runs | Open source |
| --- | --- | --- | --- |
| **Chopify** | **Free** | **Local (your PC)** | **MIT** |
| [Opus Clip](https://www.opus.pro) | Paid subscription | Cloud | No |
| [Vizard.ai](https://vizard.ai) | Paid subscription | Cloud | No |
| [Klap](https://klap.app) | Paid subscription | Cloud | No |
| [2Short.ai](https://2short.ai) | Freemium + paid | Cloud | No |
| [Munch](https://www.getmunch.com) | Paid subscription | Cloud | No |
| [Spikes Studio](https://spikes.studio) | Freemium + paid | Cloud | No |
| [Submagic](https://www.submagic.co) | Paid subscription | Cloud | No |
| [SendShort](https://sendshort.ai) | Paid subscription | Cloud | No |

<sub>Independent products and trademarks of their respective owners; pricing and features change over time. Comparison covers the long-video to short-clip workflow only.</sub>

## Requirements

- Windows, Python 3.11+
- `ffmpeg` + `ffprobe` on PATH &#8212; `winget install Gyan.FFmpeg`
- `deno` on PATH (yt-dlp JS-challenge solving) &#8212; `winget install DenoLand.Deno`

## Install

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Usage

**1. Download + transcribe** &#8594; writes `work/transcript.json`:

```bash
.venv\Scripts\python download_and_transcribe.py "<YOUTUBE_URL>"
```

**2. Score** &#8594; write `work/segments.json` with the segments to clip:

```json
{ "start": 134.2, "end": 187.6, "hook": "short title for the filename", "overall": 8.4 }
```

Scoring reads `transcript.json` and rates segments on the eight criteria above. In the
reference setup an LLM agent does this in-loop (zero API cost); score it however you like.

**3. Render** &#8594; cut + reframe (16:9 default; `--aspect 9:16` / `1:1`) + burnt captions + a `.png` poster &#8594; `C:\clips`:

```bash
.venv\Scripts\python render_clips.py work
```

## FAQ

**Is there a free alternative to Opus Clip?**
Yes &#8212; Chopify is a free, open-source, self-hosted alternative to Opus Clip, Vizard and Klap. It runs entirely on your own computer, with no subscription and no API keys.

**Can I turn long videos into short clips without paying?**
Yes. Chopify downloads a video, transcribes it locally, AI-scores every segment for virality, and exports the best moments as ready-to-post clips &#8212; for free.

**Does it run locally, offline and privately?**
Yes. Download, transcription, scoring and rendering all happen on your machine. Nothing is uploaded to the cloud.

**What formats / aspect ratios does it export?**
16:9 (landscape, default), 9:16 (vertical for TikTok, Reels and YouTube Shorts) and 1:1 (square). Each clip also gets a PNG poster.

**Do I need an OpenAI, Gemini or other paid API key?**
No. Chopify uses only open-source tools: yt-dlp, faster-whisper, ffmpeg and OpenCV.

**What is it good for?**
Repurposing podcasts, interviews, webinars, lectures and long YouTube videos into short-form clips for TikTok, Instagram Reels and YouTube Shorts.

## Notes

- **GPU:** faster-whisper (CTranslate2) is **CUDA / NVIDIA-only**; on AMD it runs on CPU
  (whisper.cpp + Vulkan was attempted for AMD but is unstable on RDNA3 &#8212; crashes or
  returns corrupted output).
- **Captions** use a built-in ffmpeg ASS renderer (the PyPI `pycaps` is an empty stub).
- Output folder is `C:\clips` &#8212; change `OUT_DIR` in `render_clips.py`.

<sub>Personal / educational use &#8212; you are responsible for the rights to any video you process.</sub>