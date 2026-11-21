"""Shared, resumable local project pipeline for CLI and dashboard."""
import hashlib
import json
import math
import os
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
import sections
import branding
import video_types
from captions import update_transcript

LIVE_DEFAULTS = {
    "live_buffer": 300, "live_buffer_max_mb": 512, "live_pre_roll": 30,
    "live_tail": 5, "live_window": 10, "live_threshold": 8.0,
    "live_cooldown": 60, "live_max_clips": 10, "live_no_chat": False,
    "live_no_transcript": False, "live_headers": [], "live_skip_ads": False,
}

DEFAULT_OPTIONS = {"aspect": "16:9", "fit": "cover", "style": "default", "tighten": False,
                   "loudnorm": False, "whisper_model": "medium", "device": "cpu",
                   "asr": "parakeet",
                   "min_len": 15, "max_len": 300, "max_clips": None,
                   "min_score": 8.0, "preferred_len": None,
                   "llm": None, "search": None, "semantic": None,
                   "embed_model": "nomic-embed-text", "brand": None,
                   "layout": "single", "centers": [0.25, 0.75], "caption": None,
                   "facecam_box": None, "facecam_height": 0.34,
                    "video_type": "auto", "format": None, "video_type_model": None,
                   **LIVE_DEFAULTS}


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
    if result["fit"] not in {"cover", "contain"}:
        raise ValueError("Fit must be cover or contain.")
    if result["layout"] not in {"single", "split", "stacked"}:
        raise ValueError("Layout must be single, split or stacked.")
    if result["layout"] in {"split", "stacked"} and result["aspect"] != "9:16":
        raise ValueError("Split-screen and gaming stacked layouts require aspect 9:16.")
    video_type = result["video_type"]
    if video_type != "auto" and video_type not in video_types.VIDEO_TYPES:
        raise ValueError(f"Unknown video type: {video_type}.")
    fmt = result["format"]
    if fmt is not None and (not isinstance(fmt, str) or not fmt.strip()):
        raise ValueError("format must be a format name or a JSON path.")
    model = result["video_type_model"]
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("video_type_model must be a model name or None.")
    height = result["facecam_height"]
    if (isinstance(height, bool) or not isinstance(height, (int, float))
            or not math.isfinite(height) or not 0.20 <= height <= 0.60):
        raise ValueError("facecam_height must be a number from 0.20 to 0.60.")
    box = result["facecam_box"]
    if box is not None:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or not 0 <= v <= 1 for v in box)):
            raise ValueError("facecam_box must be four numbers from 0 to 1: x y w h.")
        result["facecam_box"] = list(box)
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
    live_int_ranges = {
        "live_buffer": (60, 3600), "live_buffer_max_mb": (32, 8192),
        "live_pre_roll": (5, 300), "live_tail": (0, 60),
        "live_window": (2, 60), "live_cooldown": (0, 3600),
        "live_max_clips": (0, 1000),
    }
    for key, (low, high) in live_int_ranges.items():
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not low <= result[key] <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}.")
    if (isinstance(result["live_threshold"], bool)
            or not isinstance(result["live_threshold"], (int, float))
            or not math.isfinite(result["live_threshold"])
            or not 0 <= result["live_threshold"] <= 10):
        raise ValueError("live_threshold must be between 0 and 10.")
    for key in ("live_no_chat", "live_no_transcript"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false.")
    headers = result["live_headers"]
    if not isinstance(headers, (list, tuple)) or len(headers) > 20:
        raise ValueError("live_headers must be a list of at most 20 headers.")
    for header in headers:
        if (not isinstance(header, str) or ":" not in header
                or not header.split(":", 1)[0].strip()
                or any(c in header for c in "\r\n")):
            raise ValueError("Each live header must be 'Name: Value' without line breaks.")
    result["live_headers"] = list(headers)
    if result["caption"] is not None:
        result["caption"] = branding.validate_caption(result["caption"])
    return result


def score_options(options):
    keys = ("llm", "search", "semantic", "embed_model", "min_score",
            "max_clips", "min_len", "max_len", "preferred_len")
    return {key: options[key] for key in keys}


def score_keys(transcript_key, data, options):
    """Return (discovery_key, selection_key) for split stage caching."""
    discovery_options = {key: options.get(key) for key in (
        "search", "semantic", "embed_model", "min_len", "max_len",
        "preferred_len", "video_type", "format", "video_type_model")}
    discovery_key = digest([transcript_key, data, discovery_options,
                            evidence.EVIDENCE_VERSION,
                            visual.FEATURE_VERSION])
    selection_options = {key: options[key] for key in (
        "min_score", "max_clips", "llm")}
    selection_key = digest([discovery_key, selection_options])
    return discovery_key, selection_key


def apply_detection(path, state, options, detection, on_detection=None,
                    explicit_keys=None):
    """Resolve a format for the detection and return updated options and state."""
    if explicit_keys is None:
        explicit_keys = {key for key, default in DEFAULT_OPTIONS.items()
                         if options.get(key) != default}
    resolved = video_types.resolve_format(
        detection, options.get("video_type"), options.get("format"))
    if on_detection is not None:
        choice = on_detection(detection, resolved) or {}
        if choice.get("type"):
            options = {**options, "video_type": choice["type"]}
        if choice.get("format"):
            options = {**options, "format": choice["format"]}
        if choice:
            resolved = video_types.resolve_format(
                detection, options.get("video_type"), options.get("format"))
    options = video_types.apply_format(options, resolved["format"],
                                       explicit_keys or set())
    state = {
        **state,
        "detection": detection,
        "format_recipe": resolved["format"],
        "format": {
            "name": resolved["format"]["name"],
            "source": resolved["source"],
            "needs_confirmation": resolved["needs_confirmation"],
        },
    }
    return options, state


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


def run_project(path, action="all", preview=False, pick=None, resume=True,
                progress=None, on_detection=None, explicit_keys=None):
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
                if is_url:
                    meta["discovery"] = str(source)
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
                if is_url:
                    meta["discovery"] = str(source)
                state["source_meta"] = meta

            stage("transcribe")
            transcript_key = digest([
                dt.TRANSCRIPT_VERSION, file_identity(source),
                options["whisper_model"], options["device"],
                options.get("asr", "parakeet"), bool(long),
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
                        progress=transcribe_progress,
                        asr_backend=options.get("asr", "parakeet"))
                    if (is_url and long and media_kind == "audio"
                            and not read_json(transcript_path).get("words")):
                        visual = ingest.download_visual_discovery(
                            source_input, path)
                        data = read_json(transcript_path)
                        data.update(video=str(visual), media_kind="video")
                        atomic_json(transcript_path, data)
                        source = visual
                        media_kind = "video"
                        source_identity = file_identity(source)
                        state["video"] = str(source)
                        state["source_meta"] = {
                            **state.get("source_meta", {}),
                            "media_kind": "video",
                            "discovery": str(visual)}
                        state["cache"]["source_media"] = digest(source_identity)
                        transcript_key = digest([
                            dt.TRANSCRIPT_VERSION, source_identity,
                            options["whisper_model"], options["device"],
                            options.get("asr", "parakeet"),
                            bool(long), media_kind,
                            bool(render_from_sections), source_input])
                        print("No speech; switching discovery to the "
                              "low-resolution visual video.", flush=True)
                state["cache"]["transcript"] = transcript_key
                state["source_identity"] = source_identity
                state["progress"] = {"stage": "transcribe", "done": 1,
                                     "total": 1, "detail": "complete"}
                save_project(path, state)
            stage("detect")
            data = read_json(transcript_path)
            detection = video_types.detect_from_transcript(
                data, state.get("video"), path, options)
            options, state = apply_detection(
                path, state, options, detection, on_detection, explicit_keys)
            save_project(path, state)
            recipe = state["format_recipe"]
            discovery_key, selection_key = score_keys(transcript_key, data, options)
            candidates_path = path / "candidates.json"
            selection_path = path / "selection.json"
            segments_path = path / "segments.json"
            stage("discover")
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
                        preferred_len=options["preferred_len"],
                        profile=recipe.get("profile"),
                        band=(recipe["min_len"], recipe["max_len"]),
                        detection=state.get("detection"),
                        format_info=state.get("format"))
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
            section_source = str(data.get("source") or state["source"])
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
            render_from_sections = bool(
                (state.get("source_meta") or {}).get("render_from_sections")
                or data.get("render_from_sections"))
            cached_full = (sections.find_full_source(path)
                           if render_from_sections else None)
            if cached_full is not None:
                print(f"Using cached full source: {cached_full.name}", flush=True)
                render_from_sections = False
                source = cached_full
            section_plan = {}
            if render_from_sections:
                groups = sections.plan_sections(
                    segments, data.get("duration") or 0)
                for group_index, group in enumerate(groups, 1):
                    for clip_index in group["clips"]:
                        section_plan[clip_index] = (group_index, group)
                W, H = None, None
            else:
                W, H = render_clips.ffprobe_dims(source)
            loaded_sections = {}
            failed_sections = {}
            render_errors = []
            state["render_errors"] = render_errors
            outputs = [None] * len(segments)
            tasks = []

            def clip_key(segment, src, offset, skey):
                return digest([segment, data["words"], file_identity(src),
                               options, brand, logo_identity, preview,
                               offset, skey])

            planned = []
            for i, segment in enumerate(segments, 1):
                offset, skey = 0.0, None
                if render_from_sections:
                    _, group = section_plan[i - 1]
                    offset = float(group["start"])
                    skey = digest([section_source, group["start"],
                                   group["end"], sections.SECTION_VERSION])
                key = clip_key(segment, source, offset, skey)
                cached = state["renders"].get(key)
                expected_suffixes = ([".mp4", ".srt"] if preview
                                     else [".mp4", ".srt", ".png", ".meta.json"])
                if resume and cached and all(
                        Path(cached).with_suffix(ext).is_file()
                        for ext in expected_suffixes):
                    outputs[i - 1] = cached
                planned.append((i, segment, key, offset, skey))

            if render_from_sections:
                needed = {}
                for i, segment, key, offset, skey in planned:
                    if outputs[i - 1] is None:
                        group_index, group = section_plan[i - 1]
                        needed.setdefault(group_index, (group_index, group))

                def fetch_section(item):
                    group_index, group = item
                    try:
                        src = sections.ensure_section(
                            section_source, path, group_index,
                            group["start"], group["end"])
                        return group_index, src, render_clips.ffprobe_dims(src), None, None
                    except ingest.SectionDownloadTimeout as exc:
                        return group_index, None, None, "timeout", exc
                    except (Exception, SystemExit) as exc:
                        return group_index, None, None, "error", exc

                try:
                    section_workers = max(1, int(
                        os.environ.get("CHOPIFY_SECTION_WORKERS")
                        or min(4, os.cpu_count() or 2)))
                except ValueError:
                    section_workers = 1
                items = list(needed.values())
                if section_workers > 1 and len(items) > 1:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(
                            max_workers=section_workers,
                            thread_name_prefix="chopify-sections") as pool:
                        fetched = list(pool.map(fetch_section, items))
                else:
                    fetched = [fetch_section(item) for item in items]
                stalled = False
                for group_index, src, dims, kind, exc in fetched:
                    if kind is None:
                        loaded_sections[group_index] = (src, dims[0], dims[1])
                    elif kind == "timeout":
                        stalled = True
                    else:
                        failed_sections[group_index] = str(exc)
                if stalled:
                    print("Section download stalled; downloading the full "
                          "source once and rendering from it instead.", flush=True)
                    source = sections.ensure_full_source(section_source, path)
                    render_from_sections = False
                    W, H = render_clips.ffprobe_dims(source)
                    planned = [
                        (i, segment, clip_key(segment, source, 0.0, None),
                         0.0, None)
                        for i, segment, key, offset, skey in planned
                        if outputs[i - 1] is None]

            for i, segment, key, offset, skey in planned:
                if outputs[i - 1] is not None:
                    continue
                stage(f"{'preview' if preview else 'render'} {i}/{len(segments)}")
                render_source = source
                if render_from_sections:
                    group_index, group = section_plan[i - 1]
                    if group_index in failed_sections:
                        render_errors.append(
                            {"clip": segment["id"],
                             "error": failed_sections[group_index]})
                        continue
                    render_source, W, H = loaded_sections[group_index]
                tasks.append((i, segment, render_source, offset, key, W, H))

            def render_task(task):
                index, segment, render_source, source_offset, key, width, height = task
                try:
                    output = render_clips.render(
                        {**segment, "id": segment["id"] + "-" + key[:8]},
                        data["words"], render_source, width, height, path,
                        options["aspect"], out_dir=out, style=options["style"],
                        do_tighten=options["tighten"],
                        loudnorm=options["loudnorm"], preview=preview,
                        brand=brand, layout=options["layout"],
                        centers=options["centers"], caption=options["caption"],
                        source_offset=source_offset)
                    return index, segment, key, str(output), None
                except (Exception, SystemExit) as exc:
                    return index, segment, key, None, exc

            try:
                workers = max(1, int(os.environ.get("CHOPIFY_RENDER_WORKERS")
                                     or min(4, os.cpu_count() or 2)))
            except ValueError:
                workers = 1
            if workers > 1 and len(tasks) > 1:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="chopify-render") as pool:
                    results = list(pool.map(render_task, tasks))
            else:
                results = [render_task(task) for task in tasks]

            fatal = None
            for index, segment, key, output, exc in results:
                if exc is not None:
                    if not render_from_sections:
                        fatal = fatal or exc
                    else:
                        render_errors.append(
                            {"clip": segment["id"], "error": str(exc)})
                else:
                    state["renders"][key] = output
                    outputs[index - 1] = output
            outputs = [output for output in outputs if output]
            save_project(path, state)
            if fatal is not None:
                raise fatal
            state["last_outputs"] = outputs
            if render_errors:
                message = f"{len(render_errors)} clip(s) failed to render."
                if not outputs:
                    raise ValueError(message + " Sections stay cached for retry.")
                state.update(status="failed", stage="render incomplete",
                             error=message + " Successful clips were kept.")
                save_project(path, state)
                return state
            if render_from_sections and not preview:
                sections.cleanup_sections(path)
            state.update(status="review" if preview else "complete", stage="preview ready" if preview else "complete")
        save_project(path, state)
        return state
    except (Exception, SystemExit) as exc:
        state.update(status="failed", error=str(exc), stage=state.get("stage", "failed"))
        save_project(path, state)
        raise


def run_batch(sources, root, options, runner=None, brief_text=None, **kwargs):
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
        raise ValueError("Batch input must be a non-empty JSON list of video paths or URLs.")
    results = []
    for source in sources:
        state = create_project(root, source, options)
        path = project_path(root, state["id"])
        if brief_text:
            import campaign
            campaign.save_rules(path, campaign.parse_brief(brief_text))
        try:
            result = (runner or run_project)(path, **kwargs)
            results.append({"id": state["id"], "source": source, "status": result["status"]})
        except (Exception, SystemExit) as exc:
            results.append({"id": state["id"], "source": source, "status": "failed", "error": str(exc)})
    return results
