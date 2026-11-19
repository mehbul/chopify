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
