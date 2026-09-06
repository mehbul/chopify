"""
chopify - one command, YouTube link to finished viral clips.

Chains the three stages so nobody has to remember the sequence:

    python chopify.py "https://youtube.com/watch?v=..." 
    python chopify.py URL --aspect 9:16 --style hormozi --tighten --llm qwen2.5:7b

Everything runs locally: yt-dlp download -> faster-whisper transcription ->
virality scoring (heuristic by default, local Ollama with --llm) -> ffmpeg
render with captions, poster and .meta.json. No paid APIs, no API keys.
"""
import sys
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


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
    ap.add_argument("--min-score", type=float, default=None,
                    help="keep clips at/above this virality score")
    ap.add_argument("--max-clips", type=int, default=10)
    ap.add_argument("--min-len", type=int, default=20, help="min clip seconds")
    ap.add_argument("--max-len", type=int, default=75, help="max clip seconds")
    ap.add_argument("--whisper-model", default="medium",
                    help="faster-whisper size: tiny/base/small/medium/large-v3")
    args = ap.parse_args()

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
    sh(cmd)
    print("\nCHOPIFY COMPLETE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
