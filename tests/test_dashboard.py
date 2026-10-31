"""Exercise actual HTTP routes, mutation protection and saved project editing."""
import json
import threading
import urllib.error
import urllib.request

import pytest


@pytest.fixture
def server(tmp_path):
    import dashboard
    httpd = dashboard.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()
    httpd.app.close()
    thread.join()


def request(base, path, data=None, token=None, origin=None):
    headers = {}
    if token:
        headers["X-Chopify-Token"] = token
    if origin:
        headers["Origin"] = origin
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path,
        data=json.dumps(data).encode() if data is not None else None, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def test_http_rejects_cross_origin_and_unauthenticated_mutations(server):
    httpd, base = server
    for token, origin in [(None, base), (httpd.app.token, "https://evil.example")]:
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(base, "/api/projects", {"source": "test.mp4", "options": {}}, token, origin)
        assert exc.value.code == 403


def test_create_edit_and_read_project_over_http(server):
    httpd, base = server
    project = request(base, "/api/projects", {"source": "recording.mp4", "options": {}}, httpd.app.token)
    assert len(request(base, "/api/projects")["projects"]) == 1
    project_id = project["id"]
    request(base, f"/api/projects/{project_id}/settings", {"aspect": "9:16", "layout": "split"}, httpd.app.token)
    detail = request(base, f"/api/projects/{project_id}")
    assert detail["project"]["options"]["layout"] == "split"
    assert detail["candidates"] == []
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, f"/api/projects/{project_id}/settings", {"min_len": -1}, httpd.app.token)
    assert exc.value.code == 400


def test_config_exposes_caption_presets_and_defaults(server):
    httpd, base = server
    config = request(base, "/api/config")
    assert set(config["caption_presets"]) == {
        "default", "clean", "minimal", "bold", "boxed", "karaoke", "pop",
        "neon", "typewriter", "retro", "hormozi", "mrbeast", "podcast"}
    assert config["caption_presets"]["bold"]["animation"] == "pop"
    assert config["caption_defaults"]["words_per_cue"] == 3
    assert config["caption_aspects"]["9:16"]["font_size"] == 96
    assert "money" in config["caption_power_words"] or "secret" in config["caption_power_words"]
    assert any(rule["emoji"] == "💰" for rule in config["caption_emoji_rules"])


def test_caption_style_saves_to_project_and_rejects_invalid(server):
    httpd, base = server
    project = request(base, "/api/projects", {"source": "recording.mp4", "options": {}}, httpd.app.token)
    prefix = f"/api/projects/{project['id']}"
    caption = {"words_per_cue": 2, "uppercase": True, "position": "top",
               "animation": "bounce", "background": "box",
               "background_color": "#101010", "background_opacity": 60,
               "highlight_mode": "karaoke", "emoji": True, "emphasis": True}
    request(base, prefix + "/settings", {"caption": caption}, httpd.app.token)
    detail = request(base, prefix)
    assert detail["project"]["options"]["caption"] == caption
    for bad in ({"words_per_cue": 99}, {"color": "red"}, {"animation": "spin"}):
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(base, prefix + "/settings", {"caption": bad}, httpd.app.token)
        assert exc.value.code == 400


def test_media_cannot_escape_project_and_supports_ranges(server):
    import projects
    httpd, base = server
    state = projects.create_project(httpd.app.root, "test.mp4", {})
    path = projects.project_path(httpd.app.root, state["id"])
    (path / "clips").mkdir()
    (path / "clips" / "test.mp4").write_bytes(b"0123456789")
    req = urllib.request.Request(base + f"/media/{state['id']}/clips/test.mp4", headers={"Range": "bytes=2-5"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206
        assert r.read() == b"2345"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + f"/media/{state['id']}/clips/%2e%2e/project.json")
    assert exc.value.code in (400, 403, 404)


def test_queued_projects_reject_duplicate_jobs_and_edits(server, monkeypatch):
    httpd, base = server
    gate = threading.Event()
    monkeypatch.setattr(httpd.app, "_run", lambda *args: gate.wait(10))
    project = request(base, "/api/projects", {"source": "test.mp4", "options": {}}, httpd.app.token)
    prefix = f"/api/projects/{project['id']}"
    try:
        request(base, prefix + "/run", {"action": "prepare"}, httpd.app.token)
        assert request(base, prefix)["busy"]
        for action, body in [("run", {"action": "prepare"}),
                             ("settings", {"style": "podcast"}),
                             ("settings", {"caption": {"words_per_cue": 2}})]:
            with pytest.raises(urllib.error.HTTPError) as exc:
                request(base, prefix + "/" + action, body, httpd.app.token)
            assert exc.value.code == 400
    finally:
        gate.set()
