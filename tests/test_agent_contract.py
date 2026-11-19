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
