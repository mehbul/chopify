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
import evidence
import visual
import ingest
import branding
from captions import update_transcript

DEFAULT_OPTIONS = {"aspect": "16:9", "style": "default", "tighten": False,
                   "loudnorm": False, "whisper_model": "medium", "device": "cpu",
                   "min_len": 15, "max_len": 300, "max_clips": None,
                   "min_score": 8.0, "preferred_len": None,
                   "llm": None, "search": None, "semantic": None,
                   "embed_model": "nomic-embed-text", "brand": None,
                   "layout": "single", "centers": [0.25, 0.75], "caption": None}


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
    for key in ("min_len", "max_len"):
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not 1 <= result[key] <= 3600:
            raise ValueError(f"{key} must be an integer from 1 to 3600.")
    if result["min_len"] > result["max_len"]:
        raise ValueError("Minimum clip length cannot exceed maximum clip length.")
    if result["max_clips"] is not None and (
            isinstance(result["max_clips"], bool) or not isinstance(result["max_clips"], int)
            or not 1 <= result["max_clips"] <= 3600):
        raise ValueError("max_clips must be an integer from 1 to 3600 or null.")
    if result["min_score"] is not None and (not isinstance(result["min_score"], (int, float))
            or not math.isfinite(result["min_score"]) or not 0 <= result["min_score"] <= 10):
        raise ValueError("Minimum score must be between 0 and 10.")
    if result["preferred_len"] is not None:
        try:
            preferred = score_clips.parse_duration(result["preferred_len"])
        except ValueError as exc:
            raise ValueError(f"preferred_len is invalid. {exc}") from exc
        preferred = float(max(result["min_len"], min(result["max_len"], preferred)))
        result["preferred_len"] = int(preferred) if preferred.is_integer() else preferred
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
    if result["caption"] is not None:
        result["caption"] = branding.validate_caption(result["caption"])
    return result


def score_options(options):
    keys = ("llm", "search", "semantic", "embed_model", "min_score",
            "max_clips", "min_len", "max_len", "preferred_len")
    return {key: options[key] for key in keys}


def score_keys(transcript_key, data, options):
    """Return (discovery_key, selection_key) for split stage caching."""
    discovery_options = {key: options[key] for key in (
        "search", "semantic", "embed_model", "min_len", "max_len",
        "preferred_len")}
    discovery_key = digest([transcript_key, data, discovery_options,
                            evidence.EVIDENCE_VERSION,
                            visual.FEATURE_VERSION])
    selection_options = {key: options[key] for key in (
        "min_score", "max_clips", "llm")}
    selection_key = digest([discovery_key, selection_options])
    return discovery_key, selection_key


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
    if not isinstance(segments, list):
        raise ValueError("Candidates must be a list.")
    if not segments:
        return []
    result = []
    for item in segments:
        if not isinstance(item, dict):
            raise ValueError("Each clip must be an object.")
        start, end = float(item["start"]), float(item["end"])
        score = float(item.get("overall", 0))
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError("Clip times must be inside the source video.")
        if not math.isfinite(score) or not 0 <= score <= 10:
            raise ValueError("Clip score must be between 0 and 10.")
        record = dict(item)
        record.update(start=start, end=end,
                      hook=str(item.get("hook", "clip"))[:200], overall=score)
        result.append(record)
    return result


def run_project(path, action="all", preview=False, pick=None, resume=True, progress=None):
    """Prepare candidates or render saved candidates; checkpoint every successful clip."""
    path = Path(path).resolve()
    state = read_project(path)
    options = validate_options(state["options"])
    if action not in {"all", "prepare", "render"}:
        raise ValueError("Unknown project action.")
    def stage(name):
        state.update(status="running", stage=name, error=None,
                     progress={"stage": name, "done": 0, "total": 0,
                               "detail": ""})
        save_project(path, state)
        print(f"[{state['id']}] {name}", flush=True)
        if progress:
            progress(name)

    def transcribe_progress(done, total):
        state["progress"] = {"stage": "transcribe", "done": int(done),
                             "total": int(total),
                             "detail": f"chunk {done}/{total}"}
        save_project(path, state)

    try:
        if action in {"all", "prepare"}:
            stage("source")
            source_input = state["source"]
            local = Path(source_input).expanduser()
            is_url = ingest.is_url(source_input)
            if not is_url and not local.is_file():
                raise ValueError(
                    f"Video file does not exist or URL is invalid: {source_input}")
            if not is_url and local.suffix.lower() not in ingest.VIDEO_EXTS:
                raise ValueError(
                    "Use a video file: MP4, MKV, WebM, MOV, M4V or AVI.")
            identity = file_identity(local) if local.is_file() else source_input
            source_key = digest(identity)
            cached_meta = (resume
                           and state["cache"].get("source_meta") == source_key
                           and isinstance(state.get("source_meta"), dict))
            if cached_meta:
                meta = state["source_meta"]
            else:
                meta = ingest.probe_source(source_input)
                meta.pop("formats", None)
                meta = {**meta, "long": ingest.is_long_source(meta)}
                state["source_meta"] = meta
                state["cache"]["source_meta"] = source_key
                save_project(path, state)
            long = bool(meta.get("long", ingest.is_long_source(meta)))

            source = Path(state.get("video", "__missing__"))
            media_kind = str(meta.get("media_kind") or "")
            render_from_sections = bool(meta.get("render_from_sections", False))
            if (not resume or state["cache"].get("source") != source_key
                    or not source.is_file()):
                if is_url and long:
                    ingest.check_disk(path, None)
                    source, media_kind = ingest.download_discovery(
                        source_input, path, meta)
                    render_from_sections = True
                elif is_url:
                    ingest.check_disk(path, meta.get("estimated_size"))
                    source = dt.resolve_source(source_input, path)
                    media_kind = "video"
                    render_from_sections = False
                else:
                    source = dt.resolve_source(source_input, path)
                    media_kind = "video"
                    render_from_sections = False
                if not media_kind:
                    media_kind = ("audio"
                                  if source.suffix.lower() in ingest.AUDIO_EXTS
                                  else "video")
                state["video"] = str(source)
                state["cache"]["source"] = source_key
                state["cache"]["source_media"] = digest(file_identity(source))
                meta = {**meta, "long": long, "media_kind": media_kind,
                        "render_from_sections": render_from_sections}
                state["source_meta"] = meta
                save_project(path, state)
            else:
                if not media_kind:
                    media_kind = ("audio"
                                  if source.suffix.lower() in ingest.AUDIO_EXTS
                                  else "video")
                render_from_sections = bool(
                    render_from_sections and is_url and long)
                meta = {**meta, "long": long, "media_kind": media_kind,
                        "render_from_sections": render_from_sections}
                state["source_meta"] = meta

            stage("transcribe")
            transcript_key = digest([
                dt.TRANSCRIPT_VERSION, file_identity(source),
                options["whisper_model"], options["device"], bool(long),
                media_kind, bool(render_from_sections), source_input])
            transcript_path = path / "transcript.json"
            if (not resume or state["cache"].get("transcript") != transcript_key
                    or not transcript_path.exists()):
                duration = meta.get("duration")
                if duration is None:
                    try:
                        duration = ingest.probe_source(
                            str(source)).get("duration")
                    except (OSError, ValueError):
                        duration = None
                media_only = (is_url and long and media_kind == "video"
                              and meta.get("has_audio") is False)
                if media_only and not duration:
                    raise ValueError(
                        "Cannot determine the duration of the silent source.")
                source_identity = file_identity(source)
                if media_only:
                    dt.write_media_transcript(
                        source, path, duration, source=source_input,
                        media_kind=media_kind,
                        render_from_sections=render_from_sections,
                        identity=source_identity)
                else:
                    dt.transcribe(
                        source, path, options["whisper_model"],
                        options["device"],
                        "int8" if options["device"] == "cpu" else "default",
                        duration=duration if long else None,
                        chunked=bool(long), source=source_input,
                        media_kind=media_kind,
                        render_from_sections=render_from_sections,
                        identity=source_identity,
                        progress=transcribe_progress)
                state["cache"]["transcript"] = transcript_key
                state["source_identity"] = source_identity
                state["progress"] = {"stage": "transcribe", "done": 1,
                                     "total": 1, "detail": "complete"}
                save_project(path, state)
            stage("discover")
            data = read_json(transcript_path)
            discovery_key, selection_key = score_keys(transcript_key, data, options)
            candidates_path = path / "candidates.json"
            selection_path = path / "selection.json"
            segments_path = path / "segments.json"
            if options["search"] or options["semantic"]:
                combined_key = digest([discovery_key, selection_key])
                if (not resume or state["cache"].get("score") != combined_key
                        or not segments_path.exists()):
                    score_clips.run(path, **score_options(options))
                    state["cache"]["score"] = combined_key
                    save_project(path, state)
            else:
                if (not resume or state["cache"].get("discovery") != discovery_key
                        or not candidates_path.exists()):
                    score_clips.discover(
                        path, min_len=options["min_len"], max_len=options["max_len"],
                        preferred_len=options["preferred_len"])
                    state["cache"]["discovery"] = discovery_key
                    save_project(path, state)

                stage("select")
                if (not resume or state["cache"].get("selection") != selection_key
                        or not segments_path.exists() or not selection_path.exists()):
                    score_clips.select(
                        path,
                        min_score=options["min_score"] if options["min_score"] is not None else 8.0,
                        max_clips=options["max_clips"],
                        preferred_len=options["preferred_len"], llm=options["llm"])
                    state["cache"]["selection"] = selection_key
                    save_project(path, state)

            state["selection"] = read_json(selection_path)
        if action == "prepare":
            state.update(status="review", stage="ready for review")
        else:
            stage("render preview" if preview else "render")
            data = read_json(path / "transcript.json")
            source = Path(data["video"]).resolve()
            if state.get("source_identity") and state["source_identity"] != file_identity(source):
                raise ValueError("Source video changed since transcription. Find clips again before rendering.")
            segments = validate_segments(read_json(path / "segments.json"), data["duration"])
            if not segments:
                stats = state.get("selection") or {}
                low = stats.get("score_min")
                high = stats.get("score_max")
                score = stats.get("min_score", options["min_score"] or 8.0)
                range_text = (f"Candidate scores {low:.1f}-{high:.1f}. "
                              if low is not None else "No candidates were found. ")
                raise ValueError(f"{range_text}No clips reached {score:.1f}. "
                                 f"Lower the minimum score or run this project with "
                                 f"--action prepare to review the range.")
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
                    brand=brand, layout=options["layout"], centers=options["centers"],
                    caption=options["caption"])
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
