"""Handlers for agent operations. Heavy modules are imported lazily."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agent_contract import TERMINAL_STATUSES, AgentError, emit


def _path(value):
    return Path(value).expanduser().resolve()


def _read_json(path):
    from projects import read_json
    if not Path(path).exists():
        return None
    return read_json(path)


def _options(raw):
    import projects
    if raw is None:
        return dict(projects.DEFAULT_OPTIONS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(
            "INVALID_ARGUMENT", f"--options-json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError("INVALID_ARGUMENT", "--options-json must be a JSON object")
    try:
        return projects.validate_options(data)
    except (ValueError, TypeError) as exc:
        raise AgentError("INVALID_OPTIONS", str(exc),
                         details={"provided": data}) from exc


def _project_dir(value):
    path = _path(value)
    if not (path / "project.json").exists():
        raise AgentError("NOT_FOUND", f"No project at {path}",
                         details={"path": str(path)})
    return path


def describe(args, ctx):
    from agent_contract import REGISTRY, VERSION
    return {"version": VERSION, "operations": [op.as_dict() for op in REGISTRY]}


def preflight(args, ctx):
    from chopify import check_environment
    problems = check_environment()
    if problems:
        raise AgentError("PREFLIGHT_FAILED", "; ".join(problems),
                         retryable=True, details={"problems": problems})
    return {"ok": True, "problems": []}


def styles(args, ctx):
    from render_clips import CAPTION_STYLES
    return {"styles": sorted(CAPTION_STYLES)}


def project_create(args, ctx):
    import projects
    root = _path(args.workdir)
    options = _options(args.options_json)
    state = projects.create_project(root, args.source, options)
    path = projects.project_path(root, state["id"])
    return {"id": state["id"], "path": str(path),
            "status": state["status"], "options": state["options"]}


def _state_summary(path, state):
    selections = _read_json(path / "selection.json") or {}
    segments = _read_json(path / "segments.json") or []
    files = []
    for folder in (path / "clips", path / "previews"):
        if folder.exists():
            files.extend(str(p.relative_to(path))
                         for p in sorted(folder.rglob("*")) if p.is_file())
    return {
        "id": state.get("id"), "source": state.get("source"),
        "status": state.get("status"), "stage": state.get("stage"),
        "progress": state.get("progress") or {},
        "options": state.get("options") or {},
        "error": state.get("error"),
        "candidates": segments if isinstance(segments, list) else [],
        "selection": selections,
        "render_errors": state.get("render_errors") or [],
        "last_outputs": state.get("last_outputs") or [],
        "files": files,
    }


def project_get(args, ctx):
    import projects
    path = _project_dir(args.project)
    return _state_summary(path, projects.read_project(path))


def _copy_exports(result, out):
    if not out:
        return []
    export = _path(out)
    export.mkdir(parents=True, exist_ok=True)
    copied = []
    for video in result.get("last_outputs", []):
        for suffix in (".mp4", ".srt", ".png", ".meta.json"):
            src = Path(video).with_suffix(suffix)
            target = export / src.name
            if src.exists() and src.resolve() != target.resolve():
                shutil.copy2(src, target)
                copied.append(str(target))
    return copied


def _run_project(args, ctx, action, pick=None, preview=False, out=None):
    import projects
    path = _project_dir(args.project)
    state = projects.read_project(path)
    if state.get("status") == "running":
        raise AgentError("BUSY", "Project is already running.", retryable=True)

    def progress(stage):
        emit("progress", ctx.op, stream=ctx.stdout, stage=stage)

    result = projects.run_project(
        path, action=action, pick=pick, preview=preview, progress=progress)
    data = _state_summary(path, result)
    data["exports"] = _copy_exports(result, out)
    if result.get("status") == "failed":
        raise AgentError(
            "RENDER_FAILED", "One or more clips failed to render.",
            details={"render_errors": result.get("render_errors") or [],
                     "result": data})
    return data


def project_prepare(args, ctx):
    return _run_project(args, ctx, "prepare")


def project_render(args, ctx):
    return _run_project(args, ctx, "render", pick=args.pick,
                        preview=args.preview, out=args.out)


def project_wait(args, ctx):
    import projects
    path = _project_dir(args.project)
    deadline = time.monotonic() + args.timeout
    while True:
        state = projects.read_project(path)
        summary = _state_summary(path, state)
        if state.get("status") in TERMINAL_STATUSES:
            return summary
        if time.monotonic() >= deadline:
            raise AgentError(
                "TIMEOUT", f"Project did not settle within {args.timeout}s.",
                retryable=True, exit_code=4, details={"last": summary})
        time.sleep(0.5)


def batch_create(args, ctx):
    import projects
    source_file = Path(args.file).expanduser().resolve()
    try:
        sources = json.loads(source_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AgentError("INVALID_ARGUMENT", f"Batch file is not valid JSON: {exc}") from exc
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(s, str) for s in sources)):
        raise AgentError("INVALID_ARGUMENT",
                         "Batch file must contain a non-empty JSON list of sources.")
    options = _options(args.options_json)
    root = _path(args.projects_dir)
    results = []
    for source in sources:
        state = projects.create_project(root, source, options)
        path = projects.project_path(root, state["id"])

        def progress(stage, project_id=state["id"]):
            emit("progress", ctx.op, stream=ctx.stdout,
                 project=project_id, stage=stage)

        try:
            result = projects.run_project(path, action=args.action,
                                          progress=progress)
            results.append({"id": state["id"], "path": str(path),
                            "source": source, "status": result["status"]})
        except Exception as exc:  # one bad source must not hide the others
            results.append({"id": state["id"], "path": str(path),
                            "source": source, "status": "failed",
                            "error": str(exc)})
    data = {"root": str(root), "results": results}
    if any(r["status"] == "failed" for r in results):
        raise AgentError("PROCESS_FAILED", "One or more batch projects failed.",
                         details=data)
    return data


def batch_wait(args, ctx):
    import projects
    root = _path(args.root)
    deadline = time.monotonic() + args.timeout
    while True:
        dirs = (sorted(p for p in root.iterdir()
                       if p.is_dir() and (p / "project.json").exists())
                if root.exists() else [])
        states = []
        for directory in dirs:
            state = projects.read_project(directory)
            states.append({"id": state.get("id"), "status": state.get("status"),
                           "path": str(directory)})
        if states and all(s["status"] in TERMINAL_STATUSES for s in states):
            return {"root": str(root), "projects": states}
        if time.monotonic() >= deadline:
            raise AgentError("TIMEOUT", f"Batch did not settle within {args.timeout}s.",
                             retryable=True, exit_code=4,
                             details={"root": str(root), "projects": states})
        time.sleep(0.5)


def live_start(args, ctx):
    import live_capture
    options = _options(args.options_json)
    root = _path(args.root)
    session_id = uuid.uuid4().hex[:12]
    session_dir = root / "live" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "agent.log"

    # Resolve in this process first so a bad URL fails before spawning anything.
    live_capture.resolve_live_source(args.source, options)

    command = [sys.executable, str(Path(__file__).resolve()), "_live-worker",
               "--source", args.source, "--root", str(root),
               "--id", session_id, "--options-json", json.dumps(options)]
    with open(log_path, "w", encoding="utf-8") as log:
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": log,
                  "stderr": subprocess.STDOUT,
                  "cwd": str(Path(__file__).resolve().parent)}
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)

    state_path = session_dir / "live_session.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not state_path.exists():
        time.sleep(0.2)
    if not state_path.exists():
        raise AgentError("LIVE_FAILED",
                         "Live worker did not write live_session.json.",
                         details={"id": session_id, "log": str(log_path)})
    return {"id": session_id, "path": str(session_dir), "status": "starting"}


def live_worker(args, ctx):
    import live
    import live_capture
    options = _options(args.options_json)
    source = live_capture.resolve_live_source(args.source, options)
    session = live.LiveSession(source, options, _path(args.root),
                               session_id=args.id)
    session.start()
    return {"id": session.id, "status": session.status}


def live_status(args, ctx):
    path = _path(args.root) / "live" / args.id / "live_session.json"
    data = _read_json(path)
    if data is None:
        raise AgentError("NOT_FOUND", f"No live session {args.id}.",
                         details={"id": args.id})
    return data


def live_stop(args, ctx):
    session_dir = _path(args.root) / "live" / args.id
    state_path = session_dir / "live_session.json"
    data = _read_json(state_path)
    if data is None:
        raise AgentError("NOT_FOUND", f"No live session {args.id}.",
                         details={"id": args.id})
    (session_dir / "stop").write_text("stop\n", encoding="utf-8")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = _read_json(state_path) or {}
        if current.get("status") == "stopped":
            return {"id": args.id, "status": "stopped"}
        time.sleep(0.5)
    raise AgentError("TIMEOUT", "Live session did not stop within 30s.",
                     retryable=True, exit_code=4, details={"id": args.id})
