import json
import subprocess
import sys
from pathlib import Path

import agent
import agent_ops

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
