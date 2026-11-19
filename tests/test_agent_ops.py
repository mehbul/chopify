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


def test_project_wait_returns_on_settled_and_times_out(tmp_path, monkeypatch):
    import projects

    workdir = tmp_path / "work"
    state = projects.create_project(workdir, "C:/video.mp4", {})
    path = projects.project_path(workdir, state["id"])
    state["status"] = "review"
    projects.save_project(path, state)
    args = SimpleNamespace(project=str(path), timeout=1)
    assert agent_ops.project_wait(args, ctx("project.wait"))["status"] == "review"

    state["status"] = "new"
    projects.save_project(path, state)
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
    assert spawned["cmd"][7] == "--id"
    assert len(spawned["cmd"][8]) == 12
