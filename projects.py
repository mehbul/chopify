"""Shared, resumable local project pipeline for CLI and dashboard."""
import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path

import download_and_transcribe as dt
import render_clips
import score_clips
import branding
from captions import update_transcript

DEFAULT_OPTIONS = {"aspect": "16:9", "style": "default", "tighten": False,
                   "loudnorm": False, "whisper_model": "medium", "device": "cpu",
                   "min_len": 20, "max_len": 75, "max_clips": 10, "min_score": None,
                   "llm": None, "search": None, "semantic": None,
                   "embed_model": "nomic-embed-text", "brand": None,
                   "layout": "single", "centers": [0.25, 0.75]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def validate_options(options):
    unknown = set(options) - set(DEFAULT_OPTIONS)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    result = {**DEFAULT_OPTIONS, **options}
    if result["aspect"] not in render_clips.ASPECTS or result["style"] not in render_clips.CAPTION_STYLES:
        raise ValueError("Unknown aspect ratio or caption style.")
    if result["layout"] not in {"single", "split"}:
        raise ValueError("Layout must be single or split.")
    if result["layout"] == "split" and result["aspect"] != "9:16":
        raise ValueError("Podcast split-screen requires aspect 9:16.")
    for key in ("min_len", "max_len", "max_clips"):
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not 1 <= result[key] <= 3600:
            raise ValueError(f"{key} must be an integer from 1 to 3600.")
    if result["min_len"] > result["max_len"]:
        raise ValueError("Minimum clip length cannot exceed maximum clip length.")
    if result["min_score"] is not None and (not isinstance(result["min_score"], (int, float))
            or not math.isfinite(result["min_score"]) or not 0 <= result["min_score"] <= 10):
        raise ValueError("Minimum score must be between 0 and 10.")
    if result["device"] not in {"cpu", "cuda", "auto"}:
        raise ValueError("Device must be cpu, cuda or auto.")
    if result["search"] and result["semantic"]:
        raise ValueError("Choose keyword search or semantic search, not both.")
    centers = result["centers"]
    if not isinstance(centers, (list, tuple)) or len(centers) != 2 or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in centers):
        raise ValueError("Provide two speaker centers between 0 and 1.")
    for key in ("whisper_model", "embed_model", "llm", "search", "semantic", "brand"):
        if result[key] is not None and (not isinstance(result[key], str) or len(result[key]) > 2000):
            raise ValueError(f"{key} must be text.")
    for key in ("tighten", "loudnorm"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false.")
    return result


def project_path(root, project_id):
    if not re.fullmatch(r"[a-f0-9]{12}", project_id):
        raise ValueError("Invalid project ID.")
    return Path(root) / project_id


def create_project(root, source, options):
    project_id = uuid.uuid4().hex[:12]
    return init_project(project_path(root, project_id), source, options, project_id)


def init_project(path, source, options, project_id=None):
    if not isinstance(source, str) or not source.strip() or len(source) > 8000:
        raise ValueError("Provide a video path or URL.")
    options = validate_options(options)
    source = source.strip()
    if Path(source).expanduser().is_file():
        source = str(Path(source).expanduser().resolve())
    project_id = project_id or uuid.uuid4().hex[:12]
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    state = {"id": project_id, "source": source.strip(), "options": options,
             "status": "new", "stage": "waiting", "created": time.time(),
             "updated": time.time(), "cache": {}, "renders": {}, "error": None}
    save_project(path, state)
    return state


def read_project(path):
    return read_json(Path(path) / "project.json")


def save_project(path, state):
    state["updated"] = time.time()
    atomic_json(Path(path) / "project.json", state)


def edit_transcript(path, words):
    path = Path(path)
    data = update_transcript(read_json(path / "transcript.json"), words)
    atomic_json(path / "transcript.json", data)
    state = read_project(path)
    # Preserve candidate time ranges for manual editing; changed words invalidate renders.
    state["status"] = "review"
    save_project(path, state)
    return data


def validate_segments(segments, duration):
    if not isinstance(segments, list) or not segments:
        raise ValueError("Select at least one clip.")
    result = []
    for item in segments:
        start, end = float(item["start"]), float(item["end"])
        score = float(item.get("overall", 0))
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError("Clip times must be inside the source video.")
        if not math.isfinite(score) or not 0 <= score <= 10:
            raise ValueError("Clip score must be between 0 and 10.")
        result.append({"start": start, "end": end, "hook": str(item.get("hook", "clip"))[:200],
                       "overall": score})
    return result


def run_project(path, action="all", preview=False, pick=None, resume=True, progress=None):
    """Prepare candidates or render saved candidates; checkpoint every successful clip."""
    path = Path(path).resolve()
    state = read_project(path)
    options = validate_options(state["options"])
    if action not in {"all", "prepare", "render"}:
        raise ValueError("Unknown project action.")
    def stage(name):
        state.update(status="running", stage=name, error=None)
        save_project(path, state)
        print(f"[{state['id']}] {name}", flush=True)
        if progress:
            progress(name)
    try:
        if action in {"all", "prepare"}:
            stage("source")
            source_input = state["source"]
            local = Path(source_input).expanduser()
            identity = file_identity(local) if local.is_file() else source_input
            source_key = digest(identity)
            source = Path(state.get("video", "__missing__"))
            if not resume or state["cache"].get("source") != source_key or not source.is_file():
                source = dt.resolve_source(source_input, path)
                state["video"] = str(source)
                state["cache"]["source"] = source_key
                save_project(path, state)
            stage("transcribe")
            transcript_key = digest([file_identity(source), options["whisper_model"], options["device"]])
            transcript_path = path / "transcript.json"
            if not resume or state["cache"].get("transcript") != transcript_key or not transcript_path.exists():
                dt.transcribe(source, path, options["whisper_model"], options["device"],
                              "int8" if options["device"] == "cpu" else "default")
                state["cache"]["transcript"] = transcript_key
                state["source_identity"] = file_identity(source)
                save_project(path, state)
            stage("score")
            data = read_json(transcript_path)
            selection = {k: options[k] for k in ("llm", "search", "semantic", "embed_model",
                                                "min_score", "min_len", "max_len", "max_clips")}
            score_key = digest([transcript_key, data, selection])
            if not resume or state["cache"].get("score") != score_key or not (path / "segments.json").exists():
                score_clips.run(path, **selection)
                state["cache"]["score"] = score_key
                save_project(path, state)
        if action == "prepare":
            state.update(status="review", stage="ready for review")
        else:
            stage("render preview" if preview else "render")
            data = read_json(path / "transcript.json")
            source = Path(data["video"]).resolve()
            if state.get("source_identity") and state["source_identity"] != file_identity(source):
                raise ValueError("Source video changed since transcription. Find clips again before rendering.")
            segments = validate_segments(read_json(path / "segments.json"), data["duration"])
            # Assign stable IDs before subset selection so names do not shift.
            segments = [{**s, "id": f"{i:03}"} for i, s in enumerate(segments, 1)]
            segments = score_clips.pick_segments(segments, pick)
            out = path / ("previews" if preview else "clips")
            out.mkdir(exist_ok=True)
            brand = branding.load_brand(options["brand"]) if options["brand"] else None
            logo_identity = file_identity(brand["logo"]) if brand and brand["logo"] else None
            W, H = render_clips.ffprobe_dims(source)
            outputs = []
            for i, segment in enumerate(segments, 1):
                stage(f"{'preview' if preview else 'render'} {i}/{len(segments)}")
                key = digest([segment, data["words"], file_identity(source), options, brand, logo_identity, preview])
                cached = state["renders"].get(key)
                expected_suffixes = [".mp4", ".srt"] if preview else [".mp4", ".srt", ".png", ".meta.json"]
                if resume and cached and all(Path(cached).with_suffix(ext).is_file() for ext in expected_suffixes):
                    outputs.append(cached)
                    continue
                output = render_clips.render({**segment, "id": segment["id"] + "-" + key[:8]}, data["words"], source, W, H, path,
                    options["aspect"], out_dir=out, style=options["style"],
                    do_tighten=options["tighten"], loudnorm=options["loudnorm"], preview=preview,
                    brand=brand, layout=options["layout"], centers=options["centers"])
                state["renders"][key] = str(output)
                outputs.append(str(output))
                save_project(path, state)
            state["last_outputs"] = outputs
            state.update(status="review" if preview else "complete", stage="preview ready" if preview else "complete")
        save_project(path, state)
        return state
    except (Exception, SystemExit) as exc:
        state.update(status="failed", error=str(exc), stage=state.get("stage", "failed"))
        save_project(path, state)
        raise


def run_batch(sources, root, options, runner=None, **kwargs):
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
        raise ValueError("Batch input must be a non-empty JSON list of video paths or URLs.")
    results = []
    for source in sources:
        state = create_project(root, source, options)
        path = project_path(root, state["id"])
        try:
            result = (runner or run_project)(path, **kwargs)
            results.append({"id": state["id"], "source": source, "status": result["status"]})
        except (Exception, SystemExit) as exc:
            results.append({"id": state["id"], "source": source, "status": "failed", "error": str(exc)})
    return results
