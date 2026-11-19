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
    checkers = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "null": lambda v: v is None,
    }
    for key, value in values.items():
        prop = properties[key]
        kinds = prop["type"]
        if isinstance(kinds, str):
            kinds = [kinds]
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
