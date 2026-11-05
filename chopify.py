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
        while True:
            try:
                answer = input("Minimum clip score (0-10, Enter = 8.0): ")
            except EOFError:
                result["min_score"] = score_default
                break
            text = answer.strip()
            if not text:
                result["min_score"] = score_default
                break
            try:
                value = float(text)
            except ValueError:
                print("Minimum score must be a number from 0 to 10.")
                continue
            if not math.isfinite(value) or not 0 <= value <= 10:
                print("Minimum score must be a number from 0 to 10.")
                continue
            result["min_score"] = value
            break

    if ask_length:
        while True:
            try:
                answer = input("Preferred clip length (e.g. 45s, 2m; "
                               "Enter = AI chooses): ")
            except EOFError:
                result["preferred_len"] = length_default
                break
            text = answer.strip()
            if not text:
                result["preferred_len"] = length_default
                break
            try:
                result["preferred_len"] = score_clips.parse_duration(text)
                break
            except ValueError as exc:
                print(f"Invalid preferred length: {exc}")
    return result


def _project_selection(path, state):
    """Selection stats from state, falling back to selection.json."""
    if state.get("selection"):
        return state["selection"]
    selection_path = Path(path) / "selection.json"
    if selection_path.exists():
        try:
            return json.loads(selection_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass
    return {}


def _print_selection(path, state, explain=False):
    stats = _project_selection(path, state)
    if stats:
        print(score_clips.format_selection_summary(stats), flush=True)
    segments_path = Path(path) / "segments.json"
    if explain and segments_path.exists():
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
    ap.add_argument("--list-styles", action="store_true",
                    help="print the caption style preset names and exit")
    ap.add_argument("url", nargs="?", help="local video path or yt-dlp-supported URL")
    ap.add_argument("--workdir", default="work", help="intermediate files dir")
    ap.add_argument("--aspect", default="16:9", choices=["16:9", "9:16", "1:1"],
                    help="output aspect ratio (default 16:9)")
    ap.add_argument("--out", default=None,
                    help="output directory (default ./clips, or $CHOPIFY_OUT)")
    ap.add_argument("--style", default="default",
                    help="caption preset: " + ", ".join(render_clips.CAPTION_STYLES))
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
                    help="minimum clip score 0-10 (default 8.0)")
    ap.add_argument("--preferred-len", default=None, metavar="D",
                    help="soft preferred length, e.g. 45s, 2m, 1m30s")
    ap.add_argument("--max-clips", type=int, default=None,
                    help="optional safety cap; default all passing clips")
    ap.add_argument("--min-len", type=int, default=15, help="min clip seconds")
    ap.add_argument("--max-len", type=int, default=300, help="max clip seconds")
    ap.add_argument("--no-input", action="store_true",
                    help="never prompt; use flags and defaults")
    ap.add_argument("--explain", action="store_true",
                    help="print score parts and reasons")
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
    render_clips.add_caption_arguments(ap)
    args = ap.parse_args()
    if args.list_styles:
        for name in render_clips.CAPTION_STYLES:
            print(name)
        return
    if not (args.url or args.batch or args.project):
        ap.error("provide a local video, URL, --batch file, or --project directory")
    if args.batch and (args.url or args.project):
        ap.error("--batch cannot be combined with a single source or --project")
    try:
        caption = render_clips.caption_from_args(args)
    except ValueError as exc:
        ap.exit(1, f"Caption: {exc}\n")

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
    options = {k: getattr(args, k, None) for k in projects.DEFAULT_OPTIONS if k != "caption"}
    options["caption"] = caption
    if options["min_score"] is None:
        options["min_score"] = 8.0
    if options["brand"]:
        options["brand"] = str(Path(options["brand"]).resolve())
    interactive = (
        not args.no_input and not args.batch and args.action != "render"
        and not args.search and not args.semantic and sys.stdin.isatty()
    )
    ask_score = not flag_given("--min-score")
    ask_length = not flag_given("--preferred-len")
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
        prompted = None
        if (path / "project.json").exists():
            state = projects.read_project(path)
            if args.url:
                state["source"] = str(Path(args.url).resolve()) if Path(args.url).is_file() else args.url
            prompted = prompt_scoring(
                options, defaults=state["options"], interactive=interactive,
                ask_score=ask_score, ask_length=ask_length)
            # Existing projects keep their settings unless a flag explicitly changes one.
            for key in options:
                if key == "caption":
                    continue
                if flag_given("--" + key.replace("_", "-")):
                    state["options"][key] = options[key]
            if interactive:
                if ask_score:
                    state["options"]["min_score"] = prompted["min_score"]
                if ask_length:
                    state["options"]["preferred_len"] = prompted["preferred_len"]
            if caption is not None:
                state["options"]["caption"] = caption
            projects.save_project(path, state)
        else:
            if not args.url:
                ap.error("new projects require a local video or URL")
            source = str(Path(args.url).resolve()) if Path(args.url).is_file() else args.url
            options = prompt_scoring(options, interactive=interactive,
                                     ask_score=ask_score, ask_length=ask_length)
            projects.init_project(path, source, options)

        printed_summary = False
        if interactive and args.action == "all":
            result = projects.run_project(path, action="prepare", preview=args.preview,
                                          resume=args.resume)
            state = projects.read_project(path)
            _print_selection(path, state, args.explain)
            printed_summary = True
            stats = _project_selection(path, state)
            passing = int(stats.get("passing") or 0)
            score = stats.get("min_score", 8.0) or 8.0
            if passing == 0:
                print(f"No clips reached {score:.1f}. Lower --min-score "
                      f"(for example {max(0.0, score - 0.5):.1f}) or run "
                      f"--action prepare to review the range.", flush=True)
                raise SystemExit(1)
            if passing > 10:
                try:
                    answer = input(f"{passing} clips at {score:.1f} or above. "
                                   f"Render all? [Y/n] ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() in {"n", "no"}:
                    print(f"Run: python chopify.py --project {path} "
                          f"--action render")
                    return
            result = projects.run_project(path, action="render", preview=args.preview,
                                          pick=args.clips, resume=args.resume)
        else:
            result = projects.run_project(path, action=args.action, preview=args.preview,
                                          pick=args.clips, resume=args.resume)
        if not printed_summary:
            _print_selection(path, result, args.explain)
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
            if result.get("status") == "failed":
                failed = len(result.get("render_errors") or [])
                ap.exit(1, f"Chopify: {failed} clip(s) failed to render; "
                           "successful exports were kept.\n")
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        ap.exit(1, f"Chopify: {exc}\n")
    print("\nCHOPIFY COMPLETE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
