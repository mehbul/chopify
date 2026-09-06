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
import shutil
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

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


def sh(cmd):
    print("\n" + "=" * 70, flush=True)
    print(">", " ".join(str(c) for c in cmd), flush=True)
    print("=" * 70, flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    ap = argparse.ArgumentParser(
        prog="chopify",
        description="YouTube link -> scored, captioned clips. 100% local, free.")
    ap.add_argument("url", help="YouTube URL (or any yt-dlp-supported URL)")
    ap.add_argument("--workdir", default="work", help="intermediate files dir")
    ap.add_argument("--aspect", default="16:9", choices=["16:9", "9:16", "1:1"],
                    help="output aspect ratio (default 16:9)")
    ap.add_argument("--out", default=None,
                    help="output directory (default ./clips, or $CHOPIFY_OUT)")
    ap.add_argument("--style", default="default",
                    help="caption preset: default, hormozi, mrbeast, podcast")
    ap.add_argument("--tighten", action="store_true",
                    help="remove filler words (um, uh...) and long silences")
    ap.add_argument("--loudnorm", action="store_true",
                    help="normalize audio to -14 LUFS (platform standard)")
    ap.add_argument("--llm", nargs="?", const="qwen2.5:7b", default=None,
                    metavar="MODEL",
                    help="pick clips with a local Ollama model (e.g. qwen2.5:7b)")
    ap.add_argument("--search", default=None, metavar="KEYWORDS",
                    help="skip virality scoring: clip around transcript sentences "
                         "containing ALL keywords (e.g. --search \"pricing\")")
    ap.add_argument("--clips", default=None, metavar="1,3,5",
                    help="render only these numbered candidates from the score "
                         "table (review workflow)")
    ap.add_argument("--preview", action="store_true",
                    help="render fast 480p drafts (no poster/meta) to review the "
                         "picks before the full render")
    ap.add_argument("--min-score", type=float, default=None,
                    help="keep clips at/above this virality score")
    ap.add_argument("--max-clips", type=int, default=10)
    ap.add_argument("--min-len", type=int, default=20, help="min clip seconds")
    ap.add_argument("--max-len", type=int, default=75, help="max clip seconds")
    ap.add_argument("--whisper-model", default="medium",
                    help="faster-whisper size: tiny/base/small/medium/large-v3")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip the environment check (ffmpeg, packages, ...)")
    args = ap.parse_args()

    if not args.no_preflight:
        problems = check_environment()
        if problems:
            print("PREFLIGHT FAILED - fix these and re-run "
                  "(or bypass with --no-preflight):", flush=True)
            for p in problems:
                print("  - " + p, flush=True)
            sys.exit(1)
        print("PREFLIGHT OK (ffmpeg + libx264/aac, ffprobe, yt-dlp, "
              "faster-whisper, opencv)", flush=True)

    workdir = Path(args.workdir)

    # Stage 1: download + transcribe
    sh([PY, HERE / "download_and_transcribe.py", args.url,
        "--workdir", workdir, "--model", args.whisper_model])

    # Stage 2: score
    cmd = [PY, HERE / "score_clips.py", workdir,
           "--max-clips", args.max_clips,
           "--min-len", args.min_len, "--max-len", args.max_len]
    if args.llm:
        cmd += ["--llm", args.llm]
    if args.min_score is not None:
        cmd += ["--min-score", args.min_score]
    if args.search:
        cmd += ["--search", args.search]
    if args.clips:
        cmd += ["--clips", args.clips]
    sh(cmd)

    # Stage 3: render
    cmd = [PY, HERE / "render_clips.py", workdir,
           "--aspect", args.aspect, "--style", args.style]
    if args.out:
        cmd += ["--out", args.out]
    if args.tighten:
        cmd.append("--tighten")
    if args.loudnorm:
        cmd.append("--loudnorm")
    if args.preview:
        cmd.append("--preview")
    sh(cmd)
    print("\nCHOPIFY COMPLETE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
