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
import video_types

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


def detection_callback(detection, resolved):
    """Human-facing detection notice; prompt only when the match is uncertain."""
    fmt = resolved["format"]
    print(f"Detected: {detection.get('type', 'unknown')} "
          f"({(detection.get('confidence') or 0) * 100:.0f}%) "
          f"\u00b7 Format: {fmt['name']}", flush=True)
    if not resolved.get("needs_confirmation"):
        return None
    print("No confident type match. 1) use suggested  2) pick a format  "
          "3) build a new format", flush=True)
    while True:
        try:
            choice = input("Choose [1/2/3]: ").strip()
        except EOFError:
            return None
        if choice == "1":
            return None
        if choice == "2":
            custom, _ = video_types.load_custom_formats()
            names = sorted({**video_types.FORMATS, **custom})
            print("Formats: " + ", ".join(names), flush=True)
            try:
                name = input("Format name: ").strip()
            except EOFError:
                return None
            if name in names:
                return {"format": name}
            print("Unknown format name.", flush=True)
            continue
        if choice == "3":
            return build_new_format_interactive(fmt)
        print("Enter 1, 2 or 3.", flush=True)


def build_new_format_interactive(base, input_fn=input):
    """Guided format builder with defaults from the detected type."""
    while True:
        try:
            name = input_fn("New format name (lowercase): ").strip()
        except EOFError:
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name):
            print("Use lowercase letters, digits, '-' or '_'.", flush=True)
            continue
        if name in video_types.FORMATS:
            print("That name is reserved for a built-in format.", flush=True)
            continue
        break
    data = dict(base)
    data["name"] = name
    for key, label, cast in (
            ("min_len", "Minimum clip seconds", int),
            ("max_len", "Maximum clip seconds", int),
            ("caption_style", "Caption style", str),
            ("layout", "Layout (single/split/stacked)", str),
            ("fit", "Fit (cover/contain)", str)):
        try:
            answer = input_fn(f"{label} [{base.get(key, '')}]: ").strip()
        except EOFError:
            return None
        if answer:
            try:
                data[key] = cast(answer)
            except ValueError:
                print("Invalid value; keeping the default.", flush=True)
    for key in ("tighten", "loudnorm"):
        try:
            answer = input_fn(f"{key} [{'y' if base.get(key) else 'n'}]: ").strip().lower()
        except EOFError:
            return None
        if answer in {"y", "yes", "n", "no"}:
            data[key] = answer in {"y", "yes"}
    try:
        return video_types.save_custom_format(data)
    except ValueError as exc:
        print(f"Could not save format: {exc}", flush=True)
        return None


def main():
    ap = argparse.ArgumentParser(
        prog="chopify",
        description="YouTube link -> scored, captioned clips. 100% local, free.")
    ap.add_argument("--version", action="version", version=f"Chopify {CHOPIFY_VERSION}")
    ap.add_argument("--list-styles", action="store_true",
                    help="print the caption style preset names and exit")
    ap.add_argument("--live", metavar="URL", help="Watch a live HLS/RTMP stream and auto-clip hype moments")
    ap.add_argument("--live-buffer", type=int, default=None)
    ap.add_argument("--live-buffer-max-mb", type=int, default=None)
    ap.add_argument("--live-pre-roll", type=int, default=None)
    ap.add_argument("--live-tail", type=int, default=None)
    ap.add_argument("--live-window", type=int, default=None)
    ap.add_argument("--live-threshold", type=float, default=None)
    ap.add_argument("--live-cooldown", type=int, default=None)
    ap.add_argument("--live-max-clips", type=int, default=None)
    ap.add_argument("--live-no-chat", action="store_true", default=None)
    ap.add_argument("--live-no-transcript", action="store_true", default=None)
    ap.add_argument("--live-skip-ads", action="store_true", default=None,
                    help="suppress clip triggers while a flat ad slate is detected")
    ap.add_argument("--live-header", dest="live_headers", action="append", default=None)
    ap.add_argument("--video-type", dest="video_type", default=None,
                    choices=["auto", *video_types.VIDEO_TYPES],
                    help="video type override, or auto (default) to detect locally")
    ap.add_argument("--format", dest="format", default=None,
                    help="built-in or custom clipping format name, or a JSON path")
    ap.add_argument("--video-type-model", default=None, metavar="MODEL",
                    help="optional local Ollama model to resolve uncertain detections")
    ap.add_argument("--list-formats", action="store_true",
                    help="print built-in and custom format names and exit")
    ap.add_argument("--brief", default=None,
                    help="campaign requirements text (conflicts with --brief-file)")
    ap.add_argument("--brief-file", default=None,
                    help="path to a .txt/.md campaign brief")
    ap.add_argument("--brief-provider", default=None,
                    choices=["ollama", "claude", "codex", "hermes", "none"],
                    help="local LLM agent for brief parsing (auto-detected by default)")
    ap.add_argument("--brief-command", default=None,
                    help="custom command template for the local LLM, with {prompt}")
    ap.add_argument("url", nargs="?", help="local video path or yt-dlp-supported URL")
    ap.add_argument("--workdir", default="work", help="intermediate files dir")
    ap.add_argument("--aspect", default="16:9", choices=["16:9", "9:16", "1:1"],
                    help="output aspect ratio (default 16:9)")
    ap.add_argument("--fit", choices=["cover", "contain"], default=None,
                    help="cover crops to fill (default); contain keeps the whole frame with bars")
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
    ap.add_argument("--asr", choices=["parakeet", "whisper", "auto", "none"],
                    default="parakeet",
                    help="speech engine: fast local Parakeet (default) or faster-whisper")
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
    ap.add_argument("--layout", choices=["single", "split", "stacked"], default="single")
    ap.add_argument("--facecam-box", type=float, nargs=4, default=None,
                    metavar=("X", "Y", "W", "H"),
                    help="normalized facecam rectangle x y w h (0-1) for --layout stacked")
    ap.add_argument("--facecam-height", type=float, default=None,
                    help="facecam panel fraction of output height, 0.20-0.60 (default 0.34)")
    ap.add_argument("--centers", type=float, nargs=2, default=[0.25, 0.75],
                    help="horizontal speaker centers from 0 to 1 for split-screen")
    ap.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    render_clips.add_caption_arguments(ap)
    args = ap.parse_args()
    if args.list_formats:
        custom, _ = video_types.load_custom_formats()
        for name in sorted({**video_types.FORMATS, **custom}):
            print(name)
        return
    brief_text = args.brief
    if args.brief_file:
        if brief_text:
            ap.error("--brief and --brief-file are mutually exclusive.")
        brief_text = Path(args.brief_file).read_text(encoding="utf-8-sig")
    if brief_text:
        import campaign
        rules = campaign.parse_brief(brief_text, args.brief_provider, args.brief_command)
        if rules.get("warning"):
            print(f"Brief warning: {rules['warning']}", flush=True)
    if brief_text and args.batch:
        ap.error("--brief is not supported with --batch; run sources individually or use Studio.")
    if args.list_styles:
        for name in render_clips.CAPTION_STYLES:
            print(name)
        return
    if args.live:
        if args.url or args.batch or args.project:
            ap.error("--live cannot be combined with --url, --batch or --project.")
        if not args.no_preflight:
            problems = check_environment()
            if problems:
                print("PREFLIGHT FAILED - fix these and re-run "
                      "(or bypass with --no-preflight):", flush=True)
                for p in problems:
                    print("  - " + p, flush=True)
                sys.exit(1)
        import projects
        import live
        try:
            caption = render_clips.caption_from_args(args)
        except ValueError as exc:
            ap.exit(1, f"Caption: {exc}\n")
        live_options = {key: getattr(args, key) for key in projects.DEFAULT_OPTIONS
                        if getattr(args, key, None) is not None}
        live_options["caption"] = caption
        if live_options.get("brand"):
            live_options["brand"] = str(Path(live_options["brand"]).resolve())
        live_explicit_keys = {key for key in projects.DEFAULT_OPTIONS
                              if flag_given("--" + key.replace("_", "-"))}
        live_detection = video_types.classify(
            video_types.signals_from_measurements(source=args.live))
        live_resolved = video_types.resolve_format(
            live_detection, live_options.get("video_type"), live_options.get("format"))
        live_options = video_types.apply_format(
            live_options, live_resolved["format"], live_explicit_keys)
        live.run_live(args.live, live_options, Path(args.projects_dir).resolve(),
                      brief_text=brief_text)
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
    if options["video_type"] is None:
        options["video_type"] = "auto"
    if options["brand"]:
        options["brand"] = str(Path(options["brand"]).resolve())
    explicit_keys = {key for key in projects.DEFAULT_OPTIONS
                     if flag_given("--" + key.replace("_", "-"))}
    interactive = (
        not args.no_input and not args.batch and args.action != "render"
        and not args.search and not args.semantic and sys.stdin.isatty()
    )
    detection_cb = detection_callback if interactive else None
    ask_score = not flag_given("--min-score")
    ask_length = not flag_given("--preferred-len")
    try:
        if args.batch:
            sources = json.loads(Path(args.batch).read_text(encoding="utf-8-sig"))
            results = projects.run_batch(sources, Path(args.projects_dir).resolve(), options,
                action=args.action, preview=args.preview, pick=args.clips, resume=args.resume,
                on_detection=detection_cb, explicit_keys=explicit_keys)
            print(json.dumps(results, indent=2))
            if any(r["status"] == "failed" for r in results):
                raise SystemExit(1)
            return
        path = Path(args.project or args.workdir).resolve()
        if brief_text:
            campaign.save_rules(path, rules)
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
                                          resume=args.resume, on_detection=detection_cb,
                                          explicit_keys=explicit_keys)
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
                                          pick=args.clips, resume=args.resume,
                                          on_detection=detection_cb,
                                          explicit_keys=explicit_keys)
        else:
            result = projects.run_project(path, action=args.action, preview=args.preview,
                                          pick=args.clips, resume=args.resume,
                                          on_detection=detection_cb,
                                          explicit_keys=explicit_keys)
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
