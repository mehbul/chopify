# Agent-Native Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AI agents a self-describing, machine-readable CLI interface to Chopify (NDJSON events, typed errors, deterministic workflows) plus a shipped agent skill, without an MCP server or any change to the human app.

**Architecture:** Three additive modules: `agent_contract.py` holds the import-light operation registry, JSON Schemas, event emitter and typed errors; `agent_ops.py` holds thin handlers that call existing `projects` / `live` / `chopify` functions; `agent.py` builds argparse from the registry, dispatches, and owns stdout purity and exit codes. Two small additive hooks in `live.py` (optional `session_id`, stop-file check) make detached live sessions possible. Nothing else in the app changes.

**Tech Stack:** Python 3.11+, stdlib only (`argparse`, `json`, `contextlib`/stdout swizzle, `subprocess`, `uuid`, `pathlib`), pytest.

## Global Constraints

- No MCP, A2A, AG-UI, daemon, hosted service, network requirement, or new third-party dependency.
- stdout of `agent.py` is **only** NDJSON — one JSON object per line. Every line has `event`, `op`, `ts`; `start`, `result`, `error` also have `request_id`. The final line is `result` or `error`.
- Exit codes: `0` ok, `2` invalid input, `3` operation failed, `4` timeout.
- Error codes: `INVALID_ARGUMENT`, `INVALID_OPTIONS`, `NOT_FOUND`, `PRECONDITION_FAILED`, `BUSY`, `TIMEOUT`, `PREFLIGHT_FAILED`, `PROCESS_FAILED`, `RENDER_FAILED`, `LIVE_FAILED`. Every error has `code`, `message`, `retryable`, `details`.
- Core modules are imported **lazily inside handlers**, so `agent.py describe` works before ffmpeg/models are installed.
- `chopify.py`, `dashboard.py`, the HTTP API, scoring and rendering behavior are unchanged.
- Settled project statuses: `complete`, `failed`, `interrupted`, `review`.
- Windows is primary; POSIX must also work. Use `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows and `start_new_session=True` elsewhere.
- Full test suite passes before each commit.

---

### Task 1: Contract module and tests

**Files:**
- Create: `agent_contract.py`
- Test: `tests/test_agent_contract.py`

**Interfaces:**
- Consumes: nothing (pure stdlib, no repository imports).
- Produces: `VERSION`, `ERROR_CODES`, `EXIT_OK/EXIT_INVALID/EXIT_FAILED/EXIT_TIMEOUT`, `TERMINAL_STATUSES`, `AgentError(code, message, retryable=False, details=None, exit_code=None)`, `Operation` dataclass with `as_dict()`, `REGISTRY`, `get_operation(name)`, `validate_input(operation, values)`, `emit(event, op, stream=None, **payload)`, `emit_error(op, request_id, error, stream=None)`.

- [ ] **Step 1: Write the failing contract tests**

Create `tests/test_agent_contract.py`:

```python
import io
import json

import agent_contract as contract


def test_registry_has_unique_complete_operations():
    names = [op.name for op in contract.REGISTRY]
    assert len(names) == len(set(names))
    assert set(names) == {
        "describe", "preflight", "styles",
        "project.create", "project.get", "project.prepare",
        "project.render", "project.wait",
        "batch.create", "batch.wait",
        "live.start", "live.status", "live.stop",
    }
    for op in contract.REGISTRY:
        data = op.as_dict()
        assert set(data) == {"name", "summary", "input_schema",
                             "output_schema", "errors"}
        assert data["input_schema"]["type"] == "object"
        assert data["output_schema"]["type"] == "object"
        assert data["errors"] == [] or all(
            e in contract.ERROR_CODES for e in data["errors"])


def test_validate_input_accepts_valid_values():
    op = contract.get_operation("project.render")
    contract.validate_input(op, {"project": "work", "pick": "1,3",
                                 "preview": True, "out": None})


def test_validate_input_rejects_missing_unknown_and_wrong_types():
    op = contract.get_operation("project.create")
    for values, message in [
        ({}, "Missing required argument"),
        ({"source": "a.mp4", "bogus": 1}, "Unknown argument"),
        ({"source": 123}, "source must be string"),
    ]:
        try:
            contract.validate_input(op, values)
        except contract.AgentError as exc:
            assert exc.code == "INVALID_ARGUMENT"
            assert message in exc.message
        else:
            raise AssertionError(f"expected failure for {values}")


def test_validate_input_checks_enum_and_range():
    op = contract.get_operation("batch.create")
    try:
        contract.validate_input(op, {"file": "b.json", "action": "nope"})
    except contract.AgentError as exc:
        assert "must be one of" in exc.message
    else:
        raise AssertionError("enum not enforced")

    wait = contract.get_operation("project.wait")
    try:
        contract.validate_input(wait, {"project": "work", "timeout": 0})
    except contract.AgentError as exc:
        assert "at least 1" in exc.message
    else:
        raise AssertionError("minimum not enforced")


def test_emit_writes_one_json_object_per_line_to_given_stream():
    stream = io.StringIO()
    contract.emit("start", "project.get", stream=stream,
                  request_id="abc123")
    contract.emit("result", "project.get", stream=stream,
                  request_id="abc123", data={"status": "review"})
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["event"] for line in lines] == ["start", "result"]
    assert all(line["op"] == "project.get" for line in lines)
    assert all(line["ts"].endswith("Z") for line in lines)
    assert lines[0]["request_id"] == "abc123"


def test_emit_error_carries_typed_fields():
    stream = io.StringIO()
    error = contract.AgentError("INVALID_OPTIONS", "bad option",
                                retryable=False, details={"key": "aspect"})
    contract.emit_error("project.create", "r1", error, stream=stream)
    data = json.loads(stream.getvalue())
    assert data["event"] == "error"
    assert data["request_id"] == "r1"
    assert data["code"] == "INVALID_OPTIONS"
    assert data["message"] == "bad option"
    assert data["retryable"] is False
    assert data["details"] == {"key": "aspect"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_agent_contract.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_contract'`.

- [ ] **Step 3: Write `agent_contract.py`**

```python
"""Stable, import-light contract for Chopify's agent interface.

This module deliberately imports no repository modules so that
`agent.py describe` works before ffmpeg, models, or pip dependencies exist.
"""
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

VERSION = 1

ERROR_CODES = (
    "INVALID_ARGUMENT", "INVALID_OPTIONS", "NOT_FOUND", "PRECONDITION_FAILED",
    "BUSY", "TIMEOUT", "PREFLIGHT_FAILED", "PROCESS_FAILED", "RENDER_FAILED",
    "LIVE_FAILED",
)

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_FAILED = 3
EXIT_TIMEOUT = 4

TERMINAL_STATUSES = ("complete", "failed", "interrupted", "review")


class AgentError(Exception):
    """A typed, machine-readable failure."""

    def __init__(self, code, message, retryable=False, details=None,
                 exit_code=None):
        super().__init__(message)
        self.code = code
        self.message = str(message)
        self.retryable = retryable
        self.details = details or {}
        self.exit_code = exit_code


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    input_schema: dict
    output_schema: dict
    errors: tuple[str, ...]

    def as_dict(self):
        return {
            "name": self.name,
            "summary": self.summary,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "errors": list(self.errors),
        }


def _prop(kind, description, **extra):
    result = {"type": kind, "description": description}
    result.update(extra)
    return result


def _obj(properties, required=()):
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


SOURCE = _prop("string", "Local video path or yt-dlp-supported URL.")
PROJECT = _prop("string", "Path to a project directory.")
OPTIONS_JSON = _prop(["string", "null"], "JSON object of Chopify options.")
ROOT = _prop("string", "Projects root directory.", default="projects")
WORKDIR = _prop("string", "Intermediate files directory.", default="work")
TIMEOUT = _prop("integer", "Maximum seconds to wait.", minimum=1, default=600)
SESSION_ID = _prop("string", "Live session id.")
ID = _prop("string", "Project or session id.")
STATUS = _prop("string", "Current status.")
PROGRESS = {"type": "object", "description": "Stage progress."}
BLOB = {"type": "object", "description": "Arbitrary JSON object."}
STRINGS = {"type": "array", "items": {"type": "string"}}
OBJECTS = {"type": "array", "items": {"type": "object"}}

PROJECT_STATE = _obj({
    "id": ID, "source": _prop("string", "Original source."),
    "status": STATUS, "stage": _prop("string", "Current stage."),
    "progress": PROGRESS, "options": BLOB,
    "error": _prop(["string", "null"], "Last error."),
    "candidates": OBJECTS, "selection": BLOB,
    "render_errors": OBJECTS, "last_outputs": STRINGS, "files": STRINGS,
})

REGISTRY = (
    Operation(
        "describe", "Return this catalog with JSON Schemas.",
        _obj({}),
        _obj({"version": _prop("integer", "Catalog version."),
              "operations": OBJECTS}), ()),
    Operation(
        "preflight", "Check ffmpeg, encoders and Python packages.",
        _obj({}),
        _obj({"ok": _prop("boolean", "True when no problems were found."),
              "problems": STRINGS}), ("PREFLIGHT_FAILED",)),
    Operation(
        "styles", "List caption preset names.",
        _obj({}), _obj({"styles": STRINGS}), ()),
    Operation(
        "project.create", "Create a project from a source.",
        _obj({"source": SOURCE, "workdir": WORKDIR,
              "options_json": OPTIONS_JSON}, ["source"]),
        _obj({"id": ID, "path": _prop("string", "Project directory."),
              "status": STATUS, "options": BLOB}),
        ("INVALID_ARGUMENT", "INVALID_OPTIONS")),
    Operation(
        "project.get", "Return project state, candidates and exports.",
        _obj({"project": PROJECT}, ["project"]), PROJECT_STATE,
        ("NOT_FOUND",)),
    Operation(
        "project.prepare", "Download, transcribe and score a project.",
        _obj({"project": PROJECT}, ["project"]), PROJECT_STATE,
        ("NOT_FOUND", "BUSY", "PROCESS_FAILED")),
    Operation(
        "project.render", "Render selected candidates.",
        _obj({"project": PROJECT,
              "pick": _prop(["string", "null"], "One-based clip numbers, e.g. 1,3."),
              "preview": _prop("boolean", "Render draft previews."),
              "out": _prop(["string", "null"], "Export directory.")}, ["project"]),
        _obj(dict(PROJECT_STATE["properties"], exports=STRINGS)),
        ("NOT_FOUND", "BUSY", "RENDER_FAILED")),
    Operation(
        "project.wait", "Wait until a project is settled.",
        _obj({"project": PROJECT, "timeout": TIMEOUT}, ["project"]),
        PROJECT_STATE, ("NOT_FOUND", "TIMEOUT")),
    Operation(
        "batch.create", "Create and run a batch from a JSON list.",
        _obj({"file": _prop("string", "JSON file with a list of sources."),
              "projects_dir": _prop("string", "Batch root.", default="projects"),
              "options_json": OPTIONS_JSON,
              "action": _prop("string", "Stage to run.",
                              enum=["prepare", "render"], default="prepare")},
             ["file"]),
        _obj({"root": _prop("string", "Batch root."), "results": OBJECTS}),
        ("INVALID_ARGUMENT", "INVALID_OPTIONS", "PROCESS_FAILED")),
    Operation(
        "batch.wait", "Wait until all projects under a root are settled.",
        _obj({"root": ROOT, "timeout": TIMEOUT}),
        _obj({"root": _prop("string", "Batch root."),
              "projects": OBJECTS}), ("TIMEOUT",)),
    Operation(
        "live.start", "Start a detached live worker.",
        _obj({"source": _prop("string", "Live HLS/RTMP URL."),
              "root": ROOT, "options_json": OPTIONS_JSON}, ["source"]),
        _obj({"id": SESSION_ID, "path": _prop("string", "Session directory."),
              "status": STATUS}),
        ("INVALID_ARGUMENT", "INVALID_OPTIONS", "LIVE_FAILED")),
    Operation(
        "live.status", "Read a live session state file.",
        _obj({"id": SESSION_ID, "root": ROOT}, ["id"]),
        BLOB, ("NOT_FOUND",)),
    Operation(
        "live.stop", "Request a live stop and wait for it.",
        _obj({"id": SESSION_ID, "root": ROOT}, ["id"]),
        _obj({"id": SESSION_ID, "status": STATUS}),
        ("NOT_FOUND", "TIMEOUT")),
)

BY_NAME = {op.name: op for op in REGISTRY}


def get_operation(name):
    return BY_NAME.get(name)


def validate_input(operation, values):
    """Minimal stdlib check for the JSON-Schema shapes used in REGISTRY."""
    schema = operation.input_schema
    properties = schema["properties"]
    for key in schema.get("required", []):
        if key not in values:
            raise AgentError("INVALID_ARGUMENT",
                             f"Missing required argument: {key}")
    extra = set(values) - set(properties)
    if extra:
        raise AgentError("INVALID_ARGUMENT",
                         f"Unknown argument: {', '.join(sorted(extra))}")
    for key, value in values.items():
        prop = properties[key]
        kinds = prop["type"]
        if isinstance(kinds, str):
            kinds = [kinds]
        checkers = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "null": lambda v: v is None,
        }
        if not any(checkers[kind](value) for kind in kinds):
            raise AgentError("INVALID_ARGUMENT",
                             f"{key} must be {' or '.join(kinds)}")
        if "enum" in prop and value not in prop["enum"]:
            raise AgentError("INVALID_ARGUMENT",
                             f"{key} must be one of {prop['enum']}")
        if "minimum" in prop and value < prop["minimum"]:
            raise AgentError("INVALID_ARGUMENT",
                             f"{key} must be at least {prop['minimum']}")
        if "maximum" in prop and value > prop["maximum"]:
            raise AgentError("INVALID_ARGUMENT",
                             f"{key} must be at most {prop['maximum']}")


def emit(event, op, stream=None, **payload):
    """Write exactly one NDJSON line; `stream` defaults to current stdout."""
    line = {"event": event, "op": op,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    line.update(payload)
    out = stream if stream is not None else sys.stdout
    out.write(json.dumps(line, ensure_ascii=False, allow_nan=False) + "\n")
    out.flush()
    return line


def emit_error(op, request_id, error, stream=None):
    return emit("error", op, request_id=request_id, code=error.code,
                message=error.message, retryable=error.retryable,
                details=error.details, stream=stream)


def new_request_id():
    return uuid.uuid4().hex[:12]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_agent_contract.py`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add agent_contract.py tests/test_agent_contract.py
git commit -m "feat(agent): add machine-readable operation contract"
```

---

### Task 2: Live session hooks for detached control

**Files:**
- Modify: `live.py:31-37`, `live.py:156-160`
- Test: `tests/test_live.py` (append)

**Interfaces:**
- Consumes: existing `LiveSession` with `__init__(source, options, root, brief_text=None)` and `_loop()`.
- Produces: `LiveSession(..., session_id=None)`; setting `<session.dir>/stop` causes the loop to call `self.stop()` and return. Existing callers (`run_live`, dashboard) are unaffected.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live.py`:

```python
def test_live_session_accepts_explicit_id(tmp_path):
    import live
    from live_capture import LiveSource
    source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, {}, tmp_path, session_id="abc123def456")
    assert session.id == "abc123def456"
    assert session.dir == tmp_path / "live" / "abc123def456"


def test_stop_file_ends_loop(tmp_path, monkeypatch):
    import live
    from live_capture import LiveSource
    source = LiveSource("https://x/l.m3u8", "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, {}, tmp_path)
    session._save()
    stopped = []
    monkeypatch.setattr(live.LiveSession, "stop",
                        lambda self: (stopped.append(True),
                                      setattr(self, "status", "stopped")))
    (session.dir / "stop").write_text("stop\n", encoding="utf-8")
    session._loop()
    assert stopped == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_live.py -k "explicit_id or stop_file"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'session_id'`, then an infinite/irrelevant loop result.

- [ ] **Step 3: Implement the two additive hooks**

In `live.py`, change the constructor signature and first line:

```python
    def __init__(self, source, options, root, brief_text=None, session_id=None):
        self.id = session_id or uuid.uuid4().hex[:12]
```

Then change the start of `_loop()` from:

```python
    def _loop(self):
        probe = None
        while not self._stop.is_set():
            time.sleep(1.0)
```

to:

```python
    def _loop(self):
        probe = None
        while not self._stop.is_set():
            if (self.dir / "stop").exists():
                self._log("Stop requested by agent; shutting down.")
                self.stop()
                return
            time.sleep(1.0)
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. The stop file is never present for existing callers, so behavior is unchanged.

- [ ] **Step 5: Commit**

```bash
git add live.py tests/test_live.py
git commit -m "feat(live): allow agent-assigned session ids and stop files"
```

---

### Task 3: Operation handlers

**Files:**
- Create: `agent_ops.py`
- Test: `tests/test_agent_ops.py`

**Interfaces:**
- Consumes: `agent_contract.AgentError`, `agent_contract.TERMINAL_STATUSES`, `agent_contract.emit`; `projects`, `live`, `live_capture`, `chopify`, `render_clips`.
- Produces: handler functions all with signature `(args, ctx)`, where `ctx` has `.op`, `.request_id`, `.stdout`; each returns a JSON-serializable dict and raises `AgentError` on typed failures. The CLI imports these by name.

- [ ] **Step 1: Write the failing handler tests**

Create `tests/test_agent_ops.py`:

```python
import json
from types import SimpleNamespace

import pytest

import agent_ops
from agent_contract import AgentError


def ctx(name):
    return SimpleNamespace(op=name, request_id="r1", stdout=None)


def test_options_json_validation(tmp_path):
    args = SimpleNamespace(options_json='{"aspect": "9:16"}')
    assert agent_ops._options(args.options_json)["aspect"] == "9:16"
    with pytest.raises(AgentError) as bad:
        agent_ops._options("{not json")
    assert bad.value.code == "INVALID_ARGUMENT"
    with pytest.raises(AgentError) as bad:
        agent_ops._options('{"aspect": "nope"}')
    assert bad.value.code == "INVALID_OPTIONS"


def test_project_create_and_get(tmp_path):
    args = SimpleNamespace(source="C:/video.mp4", workdir=str(tmp_path / "work"),
                           options_json=None)
    created = agent_ops.project_create(args, ctx("project.create"))
    assert created["id"] and created["status"] == "new"
    get_args = SimpleNamespace(project=created["path"])
    data = agent_ops.project_get(get_args, ctx("project.get"))
    assert data["id"] == created["id"]
    assert data["status"] == "new"
    assert data["candidates"] == []


def test_project_get_missing_raises_not_found(tmp_path):
    args = SimpleNamespace(project=str(tmp_path / "missing"))
    with pytest.raises(AgentError) as bad:
        agent_ops.project_get(args, ctx("project.get"))
    assert bad.value.code == "NOT_FOUND"


def test_prepare_emits_progress_and_returns_state(tmp_path, monkeypatch):
    import projects

    workdir = tmp_path / "work"
    state = projects.create_project(workdir, "C:/video.mp4", {})
    path = projects.project_path(workdir, state["id"])
    monkeypatch.setattr(
        projects, "run_project",
        lambda p, **kw: (kw["progress"]("transcribe"),
                         projects.read_project(p))[1])

    args = SimpleNamespace(project=str(path))
    captured = []
    monkeypatch.setattr(agent_ops, "emit",
                        lambda *a, **k: captured.append((a, k)))
    data = agent_ops.project_prepare(args, ctx("project.prepare"))
    assert data["id"] == state["id"]
    assert captured[0][0] == ("progress", "project.prepare")
    assert captured[0][1]["stage"] == "transcribe"


def test_project_wait_returns_on_review_and_times_out(tmp_path, monkeypatch):
    import projects

    workdir = tmp_path / "work"
    state = projects.create_project(workdir, "C:/video.mp4", {})
    path = projects.project_path(workdir, state["id"])
    args = SimpleNamespace(project=str(path), timeout=1)
    assert agent_ops.project_wait(args, ctx("project.wait"))["status"] == "new"
    # "new" is not settled: with no status change it must time out.
    monkeypatch.setattr(agent_ops.time, "monotonic",
                        iter([0.0, 2.0]).__next__)
    with pytest.raises(AgentError) as bad:
        agent_ops.project_wait(args, ctx("project.wait"))
    assert bad.value.code == "TIMEOUT"
    assert bad.value.exit_code == 4


def test_live_start_spawns_detached_worker_and_reports_failure(tmp_path, monkeypatch):
    import live_capture

    monkeypatch.setattr(
        live_capture, "resolve_live_source",
        lambda url, options: live_capture.LiveSource(
            url, "hls", "generic", "passthrough", []))
    spawned = {}
    monkeypatch.setattr(agent_ops.subprocess, "Popen",
                        lambda cmd, **kw: spawned.update(cmd=cmd, kw=kw))
    monkeypatch.setattr(agent_ops.time, "monotonic",
                        iter([0.0, 11.0]).__next__)

    source_file = tmp_path / "live.m3u8"
    source_file.write_text("#EXTM3U\n", encoding="utf-8")
    args = SimpleNamespace(
        source=str(source_file), root=str(tmp_path / "projects"), options_json=None)
    with pytest.raises(AgentError) as bad:
        agent_ops.live_start(args, ctx("live.start"))
    assert bad.value.code == "LIVE_FAILED"
    assert spawned["cmd"][2] == "_live-worker"
    assert spawned["cmd"][6] == "--id"
    assert len(spawned["cmd"][7]) == 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_agent_ops.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_ops'`.

- [ ] **Step 3: Write `agent_ops.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_agent_ops.py tests/test_live.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_ops.py tests/test_agent_ops.py
git commit -m "feat(agent): add project, batch and live operation handlers"
```

---

### Task 4: CLI entry point, dispatch and exit codes

**Files:**
- Create: `agent.py`
- Test: `tests/test_agent_cli.py`

**Interfaces:**
- Consumes: `agent_contract` registry/errors/emit; `agent_ops` handlers.
- Produces: `python agent.py <command> ...` with NDJSON stdout and stable exit codes; `build_parser()`, `main(argv=None)`.

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_agent_cli.py`:

```python
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent
import agent_ops
from agent_contract import AgentError

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, "agent.py", *args],
                          cwd=ROOT, capture_output=True, text=True)


def lines(proc):
    return [json.loads(line) for line in proc.stdout.splitlines()]


def test_describe_subprocess_is_pure_ndjson():
    proc = run("describe")
    assert proc.returncode == 0
    events = lines(proc)
    assert [e["event"] for e in events] == ["start", "result"]
    catalog = events[-1]["data"]
    assert catalog["version"] == 1
    assert any(op["name"] == "project.create" for op in catalog["operations"])
    assert proc.stderr == ""


def test_styles_and_bad_command_exit_codes():
    assert run("styles").returncode == 0
    bad = run("no-such-command")
    assert bad.returncode == 2
    assert lines(bad)[-1]["code"] == "INVALID_ARGUMENT"


def test_project_create_then_get_subprocess(tmp_path):
    work = tmp_path / "work"
    created = run("project", "create", "--source", "C:/video.mp4",
                  "--workdir", str(work))
    assert created.returncode == 0, created.stderr
    data = lines(created)[-1]["data"]
    got = run("project", "get", "--project", data["path"])
    assert lines(got)[-1]["data"]["id"] == data["id"]


def test_invalid_options_emits_error_and_creates_nothing(tmp_path):
    work = tmp_path / "work"
    proc = run("project", "create", "--source", "C:/video.mp4",
               "--workdir", str(work),
               "--options-json", '{"aspect": "nope"}')
    assert proc.returncode == 2
    event = lines(proc)[-1]
    assert event["event"] == "error"
    assert event["code"] == "INVALID_OPTIONS"
    assert not work.exists()


def test_wait_timeout_exit_code(tmp_path, monkeypatch):
    import projects

    workdir = tmp_path / "work"
    state = projects.create_project(workdir, "C:/video.mp4", {})
    path = projects.project_path(workdir, state["id"])
    monkeypatch.setattr(agent_ops.time, "monotonic",
                        iter([0.0, 2.0, 3.0]).__next__)
    code = agent.main(["project", "wait", "--project", str(path),
                       "--timeout", "1"])
    assert code == 4


def test_core_prints_never_pollute_stdout(tmp_path, capsys, monkeypatch):
    import projects

    workdir = tmp_path / "work"
    state = projects.create_project(workdir, "C:/video.mp4", {})
    path = projects.project_path(workdir, state["id"])

    def fake_run(p, **kw):
        print("HUMAN LOG FROM CORE")
        kw["progress"]("transcribe")
        return projects.read_project(p)

    monkeypatch.setattr(projects, "run_project", fake_run)
    code = agent.main(["project", "prepare", "--project", str(path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "HUMAN LOG FROM CORE" not in captured.out
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert [e["event"] for e in events] == ["start", "progress", "result"]


def test_unexpected_exception_maps_to_process_failed(monkeypatch, capsys):
    def boom(args, ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(agent.HANDLERS, "styles", boom)
    code = agent.main(["styles"])
    captured = capsys.readouterr()
    assert code == 3
    event = json.loads(captured.out.strip().splitlines()[-1])
    assert event["code"] == "PROCESS_FAILED"
    assert "kaboom" in event["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_agent_cli.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent'`.

- [ ] **Step 3: Write `agent.py`**

```python
"""Chopify's machine-readable interface for AI agents.

stdout is newline-delimited JSON only. Human logs are sent to stderr with
--verbose or discarded. See docs/AGENT-API.md.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from types import SimpleNamespace

import agent_contract as contract
import agent_ops
from agent_contract import (
    EXIT_FAILED, EXIT_INVALID, EXIT_OK, EXIT_TIMEOUT, AgentError,
    emit, emit_error,
)

HANDLERS = {
    "describe": agent_ops.describe,
    "preflight": agent_ops.preflight,
    "styles": agent_ops.styles,
    "project.create": agent_ops.project_create,
    "project.get": agent_ops.project_get,
    "project.prepare": agent_ops.project_prepare,
    "project.render": agent_ops.project_render,
    "project.wait": agent_ops.project_wait,
    "batch.create": agent_ops.batch_create,
    "batch.wait": agent_ops.batch_wait,
    "live.start": agent_ops.live_start,
    "live.status": agent_ops.live_status,
    "live.stop": agent_ops.live_stop,
    "_live-worker": agent_ops.live_worker,
}


class AgentParser(argparse.ArgumentParser):
    """Raise a typed error instead of printing usage and exiting."""

    def error(self, message):
        raise AgentError("INVALID_ARGUMENT", message)


def _json_object(raw):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return json.dumps(value)


def _add_arguments(sub, operation):
    schema = operation.input_schema
    for name, prop in schema["properties"].items():
        flags = ["--" + name.replace("_", "-")]
        kinds = prop["type"]
        kind = kinds[0] if isinstance(kinds, list) else kinds
        if kind == "boolean":
            sub.add_argument(*flags, dest=name, action="store_true")
        elif name == "options_json":
            sub.add_argument(*flags, dest=name, type=_json_object,
                             default=prop.get("default"))
        else:
            converter = {"integer": int, "number": float, "string": str}[kind]
            sub.add_argument(*flags, dest=name, type=converter,
                             default=prop.get("default"),
                             required=name in schema.get("required", []))


def build_parser():
    common = AgentParser(add_help=False)
    common.add_argument("--verbose", action="store_true",
                        help="forward underlying logs to stderr")
    parser = AgentParser(prog="agent.py",
                         description="Chopify agent interface (NDJSON stdout).")
    top = parser.add_subparsers(dest="command", required=True)
    groups = {}
    for operation in contract.REGISTRY:
        if "." not in operation.name:
            sub = top.add_parser(operation.name, parents=[common],
                                 help=operation.summary)
            _add_arguments(sub, operation)
            continue
        group, action = operation.name.split(".", 1)
        if group not in groups:
            parent = top.add_parser(group, parents=[common])
            groups[group] = parent.add_subparsers(dest="action", required=True)
        sub = groups[group].add_parser(action, parents=[common],
                                       help=operation.summary)
        _add_arguments(sub, operation)

    worker = top.add_parser("_live-worker", parents=[common])
    worker.add_argument("--source", required=True)
    worker.add_argument("--root", default="projects")
    worker.add_argument("--id", required=True)
    worker.add_argument("--options-json", type=_json_object)
    return parser


def _operation_name(args):
    action = getattr(args, "action", None)
    return f"{args.command}.{action}" if action else args.command


def main(argv=None):
    parser = build_parser()
    request_id = contract.new_request_id()
    try:
        args = parser.parse_args(argv)
    except AgentError as exc:
        emit_error("parse", request_id, exc)
        return EXIT_INVALID

    op_name = _operation_name(args)
    operation = contract.get_operation(op_name)
    handler = HANDLERS.get(op_name)
    if handler is None or (operation is None and op_name != "_live-worker"):
        emit_error(op_name, request_id,
                   AgentError("INVALID_ARGUMENT", f"Unknown operation: {op_name}"))
        return EXIT_INVALID

    real_stdout = sys.stdout
    values = ({name: getattr(args, name, None)
               for name in operation.input_schema["properties"]}
              if operation else {})
    try:
        if operation:
            contract.validate_input(operation, values)
        emit("start", op_name, request_id=request_id, stream=real_stdout)
        sink = (sys.stderr if (args.verbose or op_name == "_live-worker")
                else io.StringIO())
        context = SimpleNamespace(op=op_name, request_id=request_id,
                                  stdout=real_stdout)
        sys.stdout = sink
        try:
            data = handler(args, context)
        finally:
            sys.stdout = real_stdout
        emit("result", op_name, request_id=request_id, data=data,
             stream=real_stdout)
        return EXIT_OK
    except AgentError as exc:
        emit_error(op_name, request_id, exc, stream=real_stdout)
        if exc.exit_code is not None:
            return exc.exit_code
        if exc.code in ("INVALID_ARGUMENT", "INVALID_OPTIONS"):
            return EXIT_INVALID
        return EXIT_TIMEOUT if exc.code == "TIMEOUT" else EXIT_FAILED
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        emit_error(op_name, request_id,
                   AgentError("INVALID_ARGUMENT", str(exc)), stream=real_stdout)
        return EXIT_INVALID
    except SystemExit as exc:
        emit_error(op_name, request_id,
                   AgentError("PROCESS_FAILED", f"exited with {exc.code}"),
                   stream=real_stdout)
        return EXIT_FAILED
    except Exception as exc:
        emit_error(op_name, request_id,
                   AgentError("PROCESS_FAILED", f"{type(exc).__name__}: {exc}"),
                   stream=real_stdout)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_agent_cli.py tests/test_agent_ops.py tests/test_agent_contract.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent_cli.py
git commit -m "feat(agent): add NDJSON CLI with typed errors and exit codes"
```

---

### Task 5: Distributable agent skill and consistency tests

**Files:**
- Create: `SKILL.md`
- Create: `skills/chopify/SKILL.md` (byte-identical copy)
- Test: `tests/test_agent_skill.py`

**Interfaces:**
- Consumes: `agent.py describe` output and `docs/AGENT-API.md`.
- Produces: two identical installable skill files whose operation names all exist in the catalog.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_skill.py`:

```python
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_files_exist_and_are_identical():
    root_skill = ROOT / "SKILL.md"
    copy = ROOT / "skills" / "chopify" / "SKILL.md"
    assert root_skill.exists() and copy.exists()
    assert root_skill.read_text(encoding="utf-8") == copy.read_text(encoding="utf-8")


def test_skill_has_frontmatter_and_workflow():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: chopify" in text
    assert "description:" in text
    assert "project.create" in text
    assert "project.prepare" in text
    assert "project.get" in text
    assert "project.render" in text
    assert "project.wait" in text


def test_skill_operation_names_are_in_catalog():
    proc = subprocess.run([sys.executable, "agent.py", "describe"],
                          cwd=ROOT, capture_output=True, text=True)
    catalog = json.loads(proc.stdout.strip().splitlines()[-1])["data"]
    names = {op["name"] for op in catalog["operations"]}
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(r"\b(?:project|batch|live|preflight|styles|describe)\.[a-z.]+|\b(?:describe|preflight|styles)\b", text))
    assert mentioned <= names | {"describe", "preflight", "styles"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_agent_skill.py`
Expected: FAIL — skill files do not exist.

- [ ] **Step 3: Write `SKILL.md`**

```markdown
---
name: chopify
description: Use when the user wants to turn a local recording or video URL into scored, captioned shorts with Chopify, or wants to operate an existing Chopify project, batch or live session. This skill covers the agent CLI only; use the Chopify Studio docs for browser work.
---

# Chopify agent interface

Chopify turns a long video into reviewed, captioned 9:16 clips on the user's
computer. Use it for local files or yt-dlp URLs. It makes no uploads and needs
no API key. Scores are editing suggestions, not predictions of virality.

## Install

The skill is shipped in the Chopify repository as `SKILL.md`. Copy that single
file to either location:

- `~/.claude/skills/chopify/SKILL.md`
- `.agents/skills/chopify/SKILL.md`

Then run commands from the Chopify checkout with the project's Python:
`python agent.py <command> ...`.

## Output contract

`agent.py` prints newline-delimited JSON to stdout. Every line has `event`,
`op`, `ts`; `start`, `result` and `error` also have `request_id`. Parse the last
line: it is always `result` (with `data`) or `error` (with `code`, `message`,
`retryable`, `details`). Human logs go to stderr with `--verbose`; otherwise
they are discarded. Never parse stdout as plain text.

Exit codes: `0` success, `2` bad input, `3` operation failed, `4` timeout.

## Commands

`describe`, `preflight`, `styles`, `project.create`, `project.get`,
`project.prepare`, `project.render`, `project.wait`, `batch.create`,
`batch.wait`, `live.start`, `live.status`, `live.stop`. Each maps to a CLI
subcommand such as `python agent.py project create --help`.

## Workflow

1. `python agent.py describe` — read the operation catalog and JSON Schemas.
2. `python agent.py preflight` — if `PREFLIGHT_FAILED`, fix the environment or
   report `details.problems`; do not start a job.
3. `python agent.py project create --source SOURCE [--options-json '{...}']`
   — save the returned `id` and `path`.
4. `python agent.py project prepare --project PATH` — may stream `progress`
   events. When it returns, status is usually `review`.
5. `python agent.py project get --project PATH` — review `candidates`,
   `selection` and `files`. Do not treat `hook` or `overall` as guaranteed.
6. `python agent.py project render --project PATH --pick 1,3 [--out DIR]`
   — render the chosen one-based clip numbers.
7. `python agent.py project wait --project PATH --timeout 600` — returns when
   the project is settled. `review` is settled; `interrupted` needs an explicit
   retry.
8. Verify the exports listed in `last_outputs` / `files` before telling the user
   the job is done.

For many sources, write a JSON array to a file and use
`python agent.py batch create --file FILE [--action prepare|render]`, then
`batch wait --root projects`. For live streams use `live start`, `live status`
and `live stop` with the returned `id`.

## Error recovery

- `INVALID_ARGUMENT` / `INVALID_OPTIONS`: fix the command or options object and
  retry. No project is created on validation failure.
- `NOT_FOUND`: check the project path or live id.
- `BUSY`: wait and call `get` / `status` again.
- `TIMEOUT`: the work may still be running; poll `get` rather than restarting.
- `PREFLIGHT_FAILED`: install the missing tool or package listed in `details`.
- `RENDER_FAILED`: successful clips are kept; read `details.render_errors` and
  retry only the failed ones.
- `PROCESS_FAILED` / `LIVE_FAILED`: inspect the message and `details`; use
  `--verbose` on a retry to capture human logs.

## Constraints

- Everything runs locally. Keep project files, transcripts and recordings
  private; never publish a running Studio token.
- Do not publish, schedule or upload clips. The user reviews and publishes.
- Live streams carry rights obligations; read `docs/LIVE.md` before clipping.
- Prefer `describe` over memorizing flags, and re-read it after upgrading.
- Full file formats are in `docs/AGENT-API.md`.
```

- [ ] **Step 4: Copy it to the install path**

Run (from the repository root):

```powershell
New-Item -ItemType Directory -Force skills/chopify | Out-Null
Copy-Item SKILL.md skills/chopify/SKILL.md
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest -q tests/test_agent_skill.py`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add SKILL.md skills/chopify/SKILL.md tests/test_agent_skill.py
git commit -m "feat(agent): ship installable agent skill"
```

---

### Task 6: Documentation, site allowlist and final verification

**Files:**
- Modify: `docs/AGENT-API.md`
- Modify: `llms.txt`
- Modify: `docs/product.json`
- Modify: `CHANGELOG.md`
- Modify: `tools/build_site.py:7-13`

**Interfaces:**
- Consumes: the finished `agent.py` contract.
- Produces: public docs that match the runtime; `skill.md` included in `_site`.

- [ ] **Step 1: Add an Agent interface section to `docs/AGENT-API.md`**

Append this section before the final privacy paragraph:

```markdown
## Agent interface (no MCP)

`python agent.py` is a machine-readable interface for coding agents. stdout is
newline-delimited JSON only; human logs go to stderr with `--verbose` and are
discarded otherwise. Run `python agent.py describe` for the operation catalog
with JSON Schemas — it is the source of truth for commands, inputs and errors.

Commands: `describe`, `preflight`, `styles`, `project create|get|prepare|render|wait`,
`batch create|wait`, `live start|status|stop`. Use `--help` on any subcommand for
flags; `--options-json` accepts a JSON object of the options documented above.

Each command emits `start`, zero or more `progress`, then `result` or `error`.

| Event | Extra fields |
| --- | --- |
| `start` | `request_id` |
| `progress` | `stage`, optionally `project` |
| `result` | `request_id`, `data` |
| `error` | `request_id`, `code`, `message`, `retryable`, `details` |

Exit codes: `0` success, `2` invalid input, `3` failed, `4` timeout. Error codes:
`INVALID_ARGUMENT`, `INVALID_OPTIONS`, `NOT_FOUND`, `PRECONDITION_FAILED`,
`BUSY`, `TIMEOUT`, `PREFLIGHT_FAILED`, `PROCESS_FAILED`, `RENDER_FAILED`,
`LIVE_FAILED`. A project is settled at `complete`, `failed`, `interrupted` or
`review`. `TIMEOUT` does not cancel work; poll `project get` again.

A copyable skill that teaches the full workflow ships as `SKILL.md` (and
`skills/chopify/SKILL.md`). Copy it to `~/.claude/skills/chopify/SKILL.md` or
`.agents/skills/chopify/SKILL.md`. The public build publishes it as `skill.md`.
```

- [ ] **Step 2: Update `llms.txt`**

Insert after the Agent and API reference bullet:

```markdown
- [Agent interface and skill](https://github.com/mehbul/chopify/blob/main/docs/AGENT-API.md#agent-interface-no-mcp): `python agent.py describe` lists every operation with JSON Schemas; stdout is NDJSON, errors are typed, and `SKILL.md` is an installable agent skill.
```

Change `Updated: 2026-11-19.` to `Updated: 2026-11-20.`

- [ ] **Step 3: Update `docs/product.json`**

Change the `interfaces` line to:

```json
  "interfaces": ["Python CLI", "local browser dashboard", "authenticated loopback HTTP API", "agent CLI (NDJSON)"],
```

Insert this object after the `interfaces` line:

```json
  "agent_interface": {
    "entry_point": "python agent.py",
    "discovery": "python agent.py describe",
    "output": "newline-delimited JSON with typed error events",
    "skill": "SKILL.md",
    "mcp": false
  },
```

Change `"documentation_updated": "2026-11-19"` to `"2026-11-20"`.

- [ ] **Step 4: Update `CHANGELOG.md`**

Under `## [Unreleased]` → `### Added`, insert:

```markdown
- Agent-native interface: `python agent.py describe` exposes a self-describing
  operation catalog with JSON Schemas; all commands emit NDJSON with typed
  errors and stable exit codes. An installable `SKILL.md` teaches agents the
  full create → prepare → review → render → verify workflow. No MCP server or
  new dependency is required.
```

- [ ] **Step 5: Add the skill to the public site allowlist**

In `tools/build_site.py`, change:

```python
FILES = {"docs/index.html": "index.html", "docs/site.css": "site.css",
```

to:

```python
FILES = {"SKILL.md": "skill.md",
         "docs/index.html": "index.html", "docs/site.css": "site.css",
```

- [ ] **Step 6: Verify everything**

Run:

```bash
python -m pytest -q
python tools/build_site.py --out _site
python agent.py describe
git status --short
```

Expected: full suite PASS; `_site/skill.md` exists; `describe` prints two NDJSON lines; `git status` clean.

- [ ] **Step 7: Commit**

```bash
git add docs/AGENT-API.md llms.txt docs/product.json CHANGELOG.md tools/build_site.py
git commit -m "docs(agent): document agent interface and ship skill"
```

---

## Self-review notes

- Spec coverage: contract registry/describe (Task 1), NDJSON/typed errors/exit codes (Tasks 1, 4), project/batch/live operations (Task 3), detached live control (Task 2), skill (Task 5), docs/site (Task 6).
- Type consistency: handlers are `(args, ctx)`; `ctx` always has `op`, `request_id`, `stdout`; `emit` accepts `stream=`; progress emits pass `ctx.stdout`; `AgentError.exit_code` is honored by `main`.
- No placeholders: every code step contains complete code; the only non-code step is the literal file copy in Task 5.
- Risk acknowledged: `live start` depends on the detached worker writing `live_session.json` within 10 s; on failure it emits `LIVE_FAILED` with the log path so the agent can diagnose rather than hang.
