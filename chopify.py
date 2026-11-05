"""
chopify - one command, YouTube link to finished viral clips.

Chains the three stages so nobody has to remember the sequence:

    python chopify.py "https://youtube.com/watch?v=..."
    python chopify.py URL --aspect 9:16 --style hormozi --tighten --llm qwen2.5:7b

Everything runs locally: yt-dlp download -> faster-whisper transcription ->
virality scoring (heuristic by default, local Ollama with --llm) -> ffmpeg
render with captions, poster and .meta.json. No paid APIs, no API keys.

A preflight check runs before any stage and reports setup problems (missing
ffmpeg, no libx264 encoder, missing Python packages) with human-readable fix
hints - because installability is where users bail.
"""
import re
import sys
import math
import shutil
import argparse
import subprocess
import json
from pathlib import Path

CHOPIFY_VERSION = "1.4.0"

import render_clips
import score_clips

# Python packages the pipeline needs, with a fix hint per missing module.
IMPORT_HINTS = {
    "yt_dlp": "pip install -r requirements.txt",
    "faster_whisper": "pip install -r requirements.txt",
    "cv2": "pip install -r requirements.txt (the opencv-python package)",
}

FFMPEG_HINT = ("install a full ffmpeg build and put it on PATH "
               "(winget install Gyan.FFmpeg, or choco install ffmpeg)")


def _missing_modules():
    """Modules from IMPORT_HINTS that cannot be imported, as [(module, hint)]."""
    missing = []
    for mod, hint in IMPORT_HINTS.items():
        try:
            __import__(mod)
        except Exception:                                    # noqa: BLE001
            missing.append((mod, hint))
    return missing


def check_environment(runner=subprocess.run):
    """Return a list of human-readable setup problems (empty list = good to go).

    Checks: Python version, ffmpeg/ffprobe on PATH, an ffmpeg build that can
    actually encode H.264/AAC (clips from mp4v-only builds are unplayable on
    most platforms), and the required Python packages.
    """
    problems = []
    if sys.version_info < (3, 11):
        problems.append(f"Python 3.11+ required (you are running "
                        f"{sys.version_info.major}.{sys.version_info.minor}).")

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            problems.append(f"'{tool}' not found on PATH - {FFMPEG_HINT}")

    if shutil.which("ffmpeg"):
        try:
            enc = runner(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
            if "libx264" not in enc:
                problems.append("ffmpeg build lacks the libx264 (H.264) encoder - "
                                f"clips would be unplayable. {FFMPEG_HINT}")
            if not re.search(r"\baac\b", enc):
                problems.append("ffmpeg build lacks the AAC audio encoder - "
                                f"clips would have no sound. {FFMPEG_HINT}")
        except OSError as e:
            problems.append(f"ffmpeg exists but could not be executed: {e}")

    for mod, hint in _missing_modules():
        problems.append(f"Python package '{mod}' is missing -> {hint}")
    return problems


def flag_given(name):
    """True when --name or --name=value appears in sys.argv."""
    flag = name if name.startswith("--") else "--" + name
    return any(token == flag or token.startswith(flag + "=")
               for token in sys.argv[1:])


def prompt_scoring(options, defaults=None, interactive=True,
                   ask_score=True, ask_length=True):
    """Prompt for the two scoring controls and return a new options dict."""
    result = dict(options)
    if not interactive:
        return result
    defaults = defaults or {}
    score_default = defaults.get("min_score")
    if score_default is None:
        score_default = 8.0
    length_default = defaults.get("preferred_len")
    if ask_score:
        raw = input(f"Minimum clip score 0-10 [{score_default}]: ").strip()
        try:
            result["min_score"] = float(raw) if raw else score_default
        except ValueError:
            result["min_score"] = score_default
    if ask_length:
        raw = input(f"Preferred length in seconds (blank = auto) [{length_default or ''}]: ").strip()
        if raw:
            try:
                result["preferred_len"] = float(raw)
            except ValueError:
                pass
    return result


def _project_selection(path, state):
    """Return selection stats if the project has them, else None."""
    try:
        sel = json.loads((Path(path) / "selection.json").read_text(encoding="utf-8-sig"))
        return sel.get("stats")
    except (OSError, ValueError, KeyError):
        return None


def _print_explanations(path, explain):
    if not explain:
        return
    segments_path = Path(path) / "segments.json"
    if not segments_path.exists():
        return
    try:
        segments = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        segments = []
    if segments:
        score_clips.print_explanations(segments)


def main():
    ap = argparse.ArgumentParser(
        prog="chopify",
        description="YouTube link -> scored, captioned clips. 100% local, free.")
    ap.add_argument("--version", action="version", version=f"Chopify {CHOPIFY_VERSION}")
    ap.add_argument("url", nargs="?", help="local video path or yt-dlp-supported URL")
    # ... (rest of the file truncated for this call; will fix properly)
    args = ap.parse_args()
    print("truncated")

if __name__ == "__main__":
    main()
