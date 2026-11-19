# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary user: a solo video creator or small-team editor who records long-form video/streams and needs short, captioned clips for Shorts, Reels, and TikTok. They work locally, often for long sessions, and review every pick before export. [Confirmed from README and the user's Studio brief: desktop and phone both matter.]

## Product Purpose

Chopify turns long recordings or live streams into reviewed, styled, captioned MP4 clips with SRT subtitles, running entirely on the user's machine. Success: a creator gets publishable vertical clips without upload, subscription, API key, or watermark. [Confirmed from README.]

## Positioning

Local, zero-paid-API clipper whose scoring uses text, local audio energy, and local visual action evidence, so speech-sparse gameplay and silent VODs still produce clips. It adds live HLS/RTMP clipping, gaming facecam layout, and campaign-brief rule extraction through local LLM agents. [Confirmed from README and shipped features.]

## Operating Context

Workflows: import local file or URL, transcribe, discover candidates, review/edit, render with captions and brand presets; batch queue; live sessions; campaign briefs. Tools: ffmpeg/ffprobe, yt-dlp, faster-whisper, OpenCV, optional local Ollama or installed CLI agents. Studio is a loopback-only local web UI. [Confirmed from docs and code.]

## Capabilities and Constraints

- Python CLI and local browser dashboard; MIT licensed; no cloud processing. [Confirmed.]
- Studio is bound to 127.0.0.1 with token/CSRF checks. [Confirmed.]
- Existing HTML structure, element IDs, API routes, and behavior must keep working; this redesign changes presentation, not contracts. [Confirmed by the user's request to build a good Studio UI without breaking the app.]
- Both desktop and phone layouts required. [Confirmed by the user.]
- Specific visual taste, palette, and typography are deliberately undecided and will be set by the seeded direction. [Explicit decision.]

## Brand Commitments

Name: Chopify. Existing wordmark "c/ chopify" and lime accent (#d4ef83) are recognizable traits but not declared immutable; the user asked for a full visual-world replacement. Voice: plain, concrete, no hype. [Name and voice confirmed from README; accent observed in incumbent code.]

## Evidence on Hand

Real product UI: `assets/studio/index.html`, `assets/studio/app.js`, `assets/studio/style.css`; product docs in `README.md`, `docs/`. No customer quotes, benchmarks, or testimonials exist; none may be invented. [Confirmed.]

## Product Principles

1. Keep footage and inference on the user's machine.
2. Show the real state of a job and let the user review before export.
3. Prefer explicit labels and recovery paths over hidden automation.
4. The tool must stay usable on a phone as well as a desk.

## Accessibility & Inclusion

Keyboard focus, visible focus rings, readable contrast, reduced-motion support, and touch targets on small screens are required for the Studio surface. [Required by user's target surface and the UI/UX rules applied.]
