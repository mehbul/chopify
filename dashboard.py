"""Chopify Studio: loopback browser UI with a serial, resumable project queue."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit
import webbrowser

import branding
import projects
import render_clips
import video_types

HERE = Path(__file__).resolve().parent
STATIC = HERE / "assets" / "studio"


def config_payload(root=None):
    """Config fields for video types and available clipping formats."""
    custom, warnings = video_types.load_custom_formats(root)
    return {
        "video_types": list(video_types.VIDEO_TYPES),
        "formats": {**video_types.FORMATS, **custom},
        "format_warnings": warnings,
    }


def save_format(body, root=None):
    """Validate and persist a custom clipping format."""
    return video_types.save_custom_format(body, root=root)


def project_detail(root, project_id):
    """Build the JSON payload for one project, including selection.json."""
    path = projects.project_path(root, project_id)
    state = projects.read_project(path)
    data = {"project": state, "busy": False, "candidates": [],
            "transcript": None, "selection": {}, "files": [], "log": ""}
    for filename, key in [("segments.json", "candidates"),
                          ("transcript.json", "transcript"),
                          ("selection.json", "selection")]:
        if (path / filename).exists():
            data[key] = projects.read_json(path / filename)
    for directory in ("previews", "clips"):
        for file in sorted((path / directory).glob("*")):
            if file.suffix in {".mp4", ".srt", ".png", ".json"}:
                data["files"].append({"name": file.name, "kind": directory,
                                      "url": f"/media/{project_id}/{directory}/{file.name}"})
    if (path / "job.log").exists():
        with (path / "job.log").open("rb") as log:
            log.seek(max(0, (path / "job.log").stat().st_size - 12000))
            data["log"] = log.read().decode("utf-8", errors="replace")
    transcript = data.get("transcript") or {}
    source_meta = state.get("source_meta") or {}
    render_from_sections = bool(
        source_meta.get("render_from_sections")
        or transcript.get("render_from_sections"))
    video = transcript.get("video") or state.get("video")
    data.update({
        "source_meta": source_meta,
        "progress": state.get("progress") or {},
        "render_errors": state.get("render_errors") or [],
        "playable_source": bool(
            not render_from_sections and video
            and Path(video).is_file()),
        "detection": state.get("detection"),
        "format": state.get("format"),
        "format_recipe": state.get("format_recipe"),
    })
    return data


class Studio:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "brands").mkdir(exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chopify")
        self.lock = threading.RLock()
        self.futures = {}
        self.processes = set()
        self.live_sessions = {}
        self.live_lock = threading.Lock()
        self.closing = False
        for path in self.root.glob("*/project.json"):
            try:
                state = projects.read_project(path.parent)
                if state["status"] in {"queued", "running"}:
                    state.update(status="interrupted", error="Studio stopped. Resume this project to continue.")
                    projects.save_project(path.parent, state)
            except (ValueError, OSError, KeyError):
                continue

    def busy(self, project_id):
        future = self.futures.get(project_id)
        return future is not None and not future.done()

    def list_projects(self):
        result = []
        for path in self.root.glob("*/project.json"):
            try:
                result.append(projects.read_project(path.parent))
            except (ValueError, OSError):
                continue
        return sorted(result, key=lambda p: p["created"], reverse=True)

    def enqueue(self, project_id, action, preview=False, pick=None):
        if action not in {"prepare", "render", "all"}:
            raise ValueError("Unknown action.")
        if not isinstance(preview, bool):
            raise ValueError("Preview must be true or false.")
        if pick is not None and (not isinstance(pick, str) or not re.fullmatch(r"\d+(,\d+)*", pick)):
            raise ValueError("Select numbered clips, for example 1,3,5.")
        with self.lock:
            path = projects.project_path(self.root, project_id)
            if self.busy(project_id):
                raise ValueError("This project is already queued or running.")
            state = projects.read_project(path)
            state.update(status="queued", stage=action, error=None)
            projects.save_project(path, state)
            self.futures[project_id] = self.executor.submit(self._run, path, action, preview, pick)

    def _run(self, path, action, preview, pick):
        cmd = [sys.executable, str(HERE / "chopify.py"), "--project", str(path),
               "--action", action, "--out", str(path / ("previews" if preview else "clips"))]
        if preview:
            cmd.append("--preview")
        if pick:
            cmd += ["--clips", pick]
        try:
            with (path / "job.log").open("w", encoding="utf-8") as log:
                with self.lock:
                    if self.closing:
                        return
                    child = subprocess.Popen(cmd, cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
                    self.processes.add(child)
                code = child.wait()
                with self.lock:
                    self.processes.discard(child)
            if code:
                state = projects.read_project(path)
                state.update(status="failed", error=state.get("error") or "Job failed. Open the job log for setup or input details.")
                projects.save_project(path, state)
        except Exception as exc:
            state = projects.read_project(path)
            state.update(status="failed", error=str(exc))
            projects.save_project(path, state)

    def start_live(self, source, options, brief_text=None):
        import live
        from live_capture import resolve_live_source
        resolved = resolve_live_source(source, options)
        session = live.LiveSession(resolved, options, self.root, brief_text=brief_text)
        with self.live_lock:
            self.live_sessions[session.id] = session
        thread = threading.Thread(target=session.start,
                                  name=f"live-{session.id}", daemon=True)
        thread.start()
        return session

    def stop_live(self, session_id):
        with self.live_lock:
            session = self.live_sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown live session.")
        session.stop()
        return session

    def live_state(self, session_id):
        with self.live_lock:
            session = self.live_sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown live session.")
        return projects.read_json(session.dir / "live_session.json")

    def live_list(self):
        with self.live_lock:
            sessions = list(self.live_sessions.values())
        return [projects.read_json(s.dir / "live_session.json")
                for s in sessions if (s.dir / "live_session.json").exists()]

    def close(self):
        with self.lock:
            self.closing = True
            for session in list(self.live_sessions.values()):
                session.stop()
            for child in list(self.processes):
                child.terminate()
            self.executor.shutdown(wait=False, cancel_futures=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ChopifyStudio/1.4.0"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; media-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.valid_host():
            return self.send_json({"error": "Use the 127.0.0.1 Studio address."}, 403)
        try:
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            if parts == ["api", "config"]:
                brands = sorted(p.stem for p in (self.app.root / "brands").glob("*.json"))
                caption_brands = {}
                for brand_path in (self.app.root / "brands").glob("*.json"):
                    try:
                        brand = branding.validate_brand(
                            projects.read_json(brand_path), brand_path.parent)
                    except (ValueError, OSError, KeyError):
                        continue
                    caption_brands[brand_path.stem] = {
                        key: brand[key] for key in branding.CAPTION_FIELDS
                        if brand[key] is not None
                    }
                payload = {
                    "token": self.app.token,
                    "defaults": projects.DEFAULT_OPTIONS,
                    "brands": brands,
                    "caption_brands": caption_brands,
                    "caption_presets": render_clips.CAPTION_STYLES,
                    "caption_defaults": branding.CAPTION_DEFAULTS,
                    "caption_aspects": {
                        name: {"font_size": size, "margin": margin}
                        for name, (_, _, size, margin) in render_clips.ASPECTS.items()
                    },
                    "caption_power_words": sorted(render_clips.POWER_WORDS),
                    "caption_emoji_rules": [
                        {"words": list(stems), "emoji": emoji}
                        for stems, emoji in render_clips.EMOJI_RULES
                    ],
                }
                payload.update(config_payload(self.app.root))
                return self.send_json(payload)
            if parts == ["api", "projects"]:
                return self.send_json({"projects": self.app.list_projects()})
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                data = project_detail(self.app.root, parts[2])
                data["busy"] = self.app.busy(parts[2])
                return self.send_json(data)
            if parts and parts[0] == "media" and len(parts) >= 3:
                path = projects.project_path(self.app.root, parts[1]).resolve()
                if parts[2:] == ["source"]:
                    source = projects.read_json(path / "transcript.json")["video"]
                    return self.send_file(Path(source))
                if len(parts) != 4 or parts[2] not in {"clips", "previews"}:
                    raise ValueError("Unknown media path.")
                file = (path / parts[2] / parts[3]).resolve()
                if file.parent != path / parts[2] or file.suffix not in {".mp4", ".srt", ".png", ".json"}:
                    raise ValueError("Invalid media path.")
                return self.send_file(file)
            if parts == ["api", "live"]:
                return self.send_json({"sessions": self.app.live_list()})
            if len(parts) == 3 and parts[:2] == ["api", "live"]:
                return self.send_json(self.app.live_state(parts[2]))
            if (len(parts) == 5 and parts[0] == "media" and parts[1] == "live"
                    and parts[3] == "clips"):
                if not re.fullmatch(r"[a-f0-9]{12}", parts[2]):
                    raise ValueError("Invalid live session ID.")
                session_dir = self.app.root / "live" / parts[2]
                file = (session_dir / "clips" / parts[4]).resolve()
                clips_dir = (session_dir / "clips").resolve()
                if file.parent != clips_dir or file.suffix not in {".mp4", ".srt", ".png", ".json"}:
                    raise ValueError("Invalid media path.")
                return self.send_file(file)
            static = {"": "index.html", "app.js": "app.js", "style.css": "style.css"}
            filename = "/".join(parts)
            if filename in static:
                return self.send_file(STATIC / static[filename])
            self.send_json({"error": "Not found."}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, KeyError):
            self.send_json({"error": "Project or file not found."}, 404)
        except (ValueError, TypeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if (not self.valid_host() or (origin is not None and origin != expected)
                or not secrets.compare_digest(self.headers.get("X-Chopify-Token", ""), self.app.token)):
            return self.send_json({"error": "Request rejected. Reload Studio and retry."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 10_000_000:
                raise ValueError("Request must be JSON smaller than 10 MB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            with self.app.lock:
                if parts == ["api", "live"]:
                    source = body.get("source")
                    if not isinstance(source, str) or not source.strip():
                        raise ValueError("Provide a live source URL.")
                    options = projects.validate_options(body.get("options", {}))
                    brief_text = body.get("brief")
                    if brief_text is not None and not isinstance(brief_text, str):
                        raise ValueError("Brief must be text.")
                    return self.send_json(
                        {"id": self.app.start_live(
                            source.strip(), options, brief_text=brief_text).id}, 201)
                if len(parts) == 4 and parts[:2] == ["api", "live"] and parts[3] == "stop":
                    self.app.stop_live(parts[2])
                    return self.send_json({"ok": True})
                if parts == ["api", "formats"]:
                    return self.send_json(save_format(body, root=self.app.root), 201)
                if parts == ["api", "projects"]:
                    return self.send_json(projects.create_project(self.app.root, body["source"], body.get("options", {})), 201)
                if parts == ["api", "batch"]:
                    sources = body["sources"]
                    if not isinstance(sources, list) or not 1 <= len(sources) <= 100 or any(not isinstance(s, str) or not s.strip() for s in sources):
                        raise ValueError("Provide 1 to 100 sources.")
                    options = projects.validate_options(body.get("options", {}))
                    brief_text = body.get("brief")
                    if brief_text is not None and not isinstance(brief_text, str):
                        raise ValueError("Brief must be text.")
                    result = []
                    for source in sources:
                        state = projects.create_project(self.app.root, source, options)
                        if brief_text and brief_text.strip():
                            import campaign
                            campaign.save_rules(
                                projects.project_path(self.app.root, state["id"]),
                                campaign.parse_brief(brief_text))
                        self.app.enqueue(state["id"], "prepare")
                        result.append(state)
                    return self.send_json({"projects": result}, 201)
                if parts == ["api", "brands"]:
                    name = body["name"]
                    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", name):
                        raise ValueError("Preset name must use letters, numbers, - or _.")
                    brand = branding.validate_brand(body["preset"])
                    path = self.app.root / "brands" / f"{name}.json"
                    projects.atomic_json(path, brand)
                    return self.send_json({"name": name, "path": str(path)})
                if len(parts) == 4 and parts[:2] == ["api", "projects"]:
                    project_id, action = parts[2:]
                    path = projects.project_path(self.app.root, project_id)
                    state = projects.read_project(path)
                    if self.app.busy(project_id):
                        raise ValueError("Wait for this project's current job to finish before editing.")
                    if action == "settings":
                        settings = dict(body)
                        if "brand_name" in settings:
                            name = settings.pop("brand_name")
                            if name and (not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", name)):
                                raise ValueError("Invalid brand preset.")
                            settings["brand"] = str(self.app.root / "brands" / f"{name}.json") if name else None
                        state["options"] = projects.validate_options({**state["options"], **settings})
                        projects.save_project(path, state)
                    elif action == "transcript":
                        projects.edit_transcript(path, body["words"])
                    elif action == "candidates":
                        duration = projects.read_json(path / "transcript.json")["duration"]
                        segments = projects.validate_segments(body["candidates"], duration)
                        projects.atomic_json(path / "segments.json", segments)
                    elif action == "run":
                        self.app.enqueue(project_id, body.get("action", "prepare"), body.get("preview", False), body.get("pick"))
                    else:
                        return self.send_json({"error": "Not found."}, 404)
                    return self.send_json({"ok": True})
            self.send_json({"error": "Not found."}, 404)
        except FileNotFoundError:
            self.send_json({"error": "Project or transcript not found. Prepare the project first."}, 404)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def send_file(self, path):
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
            if not match:
                return self.send_json({"error": "Unsupported byte range."}, 416)
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, end)
            if start > end or start >= size:
                return self.send_json({"error": "Range outside file."}, 416)
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as file:
                file.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = file.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(root="projects", port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = Studio(root)
    return server


def main():
    parser = argparse.ArgumentParser(description="Open Chopify Studio on your own computer.")
    parser.add_argument("--projects-dir", default="projects")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = make_server(args.projects_dir, args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Chopify Studio: {url}\nProjects: {server.app.root}\nPress Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.close()
        server.server_close()


if __name__ == "__main__":
    main()
