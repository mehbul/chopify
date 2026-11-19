"""Offline integration test: a local HLS fixture drives the live pipeline."""
import shutil
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def test_live_pipeline_against_local_hls(tmp_path):
    import live
    import projects
    from fixtures.make_hls_fixture import make_fixture
    from live_capture import LiveSource

    served = make_fixture(tmp_path / "stream", seconds=20)
    handler = partial(QuietHandler, directory=str(served.parent))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}/index.m3u8"

    options = projects.validate_options({
        "live_buffer": 60, "live_threshold": 0.5, "live_pre_roll": 5,
        "live_tail": 2, "live_max_clips": 1, "live_no_chat": True,
        "live_no_transcript": True})
    source = LiveSource(url, "hls", "generic", "passthrough", [])
    session = live.LiveSession(source, options, tmp_path)
    thread = threading.Thread(target=session.start, daemon=True)
    thread.start()
    deadline = time.time() + 25
    while time.time() < deadline and not session.records:
        time.sleep(0.5)
    session.stop()
    thread.join(timeout=5)
    httpd.shutdown()
    httpd.server_close()

    data = projects.read_json(session.dir / "live_session.json")
    assert data["health"]["state"] in {"ok", "degraded"}
    assert set(data["health"]) >= {"state", "ffmpeg_alive", "last_segment_age",
                                   "restarts", "refreshes", "warnings"}
    assert (session.dir / "clips").exists()
    assert list((session.dir / "clips").glob("*.mp4")), "no clip was rendered"
