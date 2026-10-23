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
import json
from pathlib import Path

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


def main():
    ap = argparse.ArgumentParser(
        prog="chopify",
        description="YouTube link -> scored, captioned clips. 100% local, free.")
    ap.add_argument("url", nargs="?", help="local video path or yt-dlp-supported URL")
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
    ap.add_argument("--project", help="resume an existing project directory")
    ap.add_argument("--projects-dir", default="projects", help="batch project storage")
    ap.add_argument("--batch", help="JSON file containing a list of video paths or URLs")
    ap.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                    help="reuse successful matching stages (default enabled)")
    ap.add_argument("--action", choices=["all", "prepare", "render"], default="all",
                    help="prepare candidates for review, render saved edits, or run all stages")
    ap.add_argument("--semantic", help="find clips by meaning with local embeddings")
    ap.add_argument("--embed-model", default="nomic-embed-text")
    ap.add_argument("--brand", help="JSON brand preset path")
    ap.add_argument("--layout", choices=["single", "split"], default="single")
    ap.add_argument("--centers", type=float, nargs=2, default=[0.25, 0.75],
                    help="horizontal speaker centers from 0 to 1 for split-screen")
    ap.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    args = ap.parse_args()
    if not (args.url or args.batch or args.project):
        ap.error("provide a local video, URL, --batch file, or --project directory")
    if args.batch and (args.url or args.project):
        ap.error("--batch cannot be combined with a single source or --project")

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

    import projects
    options = {k: getattr(args, k) for k in projects.DEFAULT_OPTIONS}
    if options["brand"]:
        options["brand"] = str(Path(options["brand"]).resolve())
    try:
        if args.batch:
            sources = json.loads(Path(args.batch).read_text(encoding="utf-8-sig"))
            results = projects.run_batch(sources, Path(args.projects_dir).resolve(), options,
                action=args.action, preview=args.preview, pick=args.clips, resume=args.resume)
            print(json.dumps(results, indent=2))
            if any(r["status"] == "failed" for r in results):
                raise SystemExit(1)
            return
        path = Path(args.project or args.workdir).resolve()
        if (path / "project.json").exists():
            state = projects.read_project(path)
            if args.url:
                state["source"] = str(Path(args.url).resolve()) if Path(args.url).is_file() else args.url
            # Existing projects keep their settings unless a flag explicitly changes one.
            for key in options:
                flag = "--" + key.replace("_", "-")
                if any(token == flag or token.startswith(flag + "=") for token in sys.argv[1:]):
                    state["options"][key] = options[key]
            projects.save_project(path, state)
        else:
            if not args.url:
                ap.error("new projects require a local video or URL")
            source = str(Path(args.url).resolve()) if Path(args.url).is_file() else args.url
            projects.init_project(path, source, options)
        result = projects.run_project(path, action=args.action, preview=args.preview,
                                      pick=args.clips, resume=args.resume)
        if args.action != "prepare":
            from render_clips import default_out
            export = (Path(args.out) if args.out else default_out()).resolve()
            export.mkdir(parents=True, exist_ok=True)
            for video in result.get("last_outputs", []):
                for suffix in (".mp4", ".srt", ".png", ".meta.json"):
                    file = Path(video).with_suffix(suffix)
                    target = export / file.name
                    if file.exists() and file.resolve() != target.resolve():
                        shutil.copy2(file, target)
            print(f"Exports: {export}")
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        ap.exit(1, f"Chopify: {exc}\n")
    print("\nCHOPIFY COMPLETE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
