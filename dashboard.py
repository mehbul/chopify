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
from urllib.parse import quote, unquote, urlsplit
import webbrowser

import branding
import projects

HERE = Path(__file__).resolve().parent
STATIC = HERE / "assets" / "studio"


class Studio:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "brands").mkdir(exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chopify")
        self.lock = threading.RLock()
        self.pdf_lock = threading.Lock()
        self.futures = {}
        self.processes = set()
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

    def close(self):
        with self.lock:
            self.closing = True
            for child in list(self.processes):
                child.terminate()
            self.executor.shutdown(wait=False, cancel_futures=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ChopifyStudio/1.4"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; media-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'")
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
            if parts[:2] == ["api", "pdf"]:
                return self.get_pdf(parts)
            if parts == ["api", "config"]:
                brands = sorted(p.stem for p in (self.app.root / "brands").glob("*.json"))
                return self.send_json({"token": self.app.token, "defaults": projects.DEFAULT_OPTIONS, "brands": brands})
            if parts == ["api", "projects"]:
                return self.send_json({"projects": self.app.list_projects()})
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                path = projects.project_path(self.app.root, parts[2])
                state = projects.read_project(path)
                data = {"project": state, "busy": self.app.busy(parts[2]), "candidates": [], "transcript": None,
                        "files": [], "log": ""}
                for filename, key in [("segments.json", "candidates"), ("transcript.json", "transcript")]:
                    if (path / filename).exists():
                        data[key] = projects.read_json(path / filename)
                for directory in ("previews", "clips"):
                    for file in sorted((path / directory).glob("*")):
                        if file.suffix in {".mp4", ".srt", ".png", ".json"}:
                            data["files"].append({"name": file.name, "kind": directory,
                                "url": f"/media/{parts[2]}/{directory}/{file.name}"})
                if (path / "job.log").exists():
                    with (path / "job.log").open("rb") as log:
                        log.seek(max(0, (path / "job.log").stat().st_size - 12000))
                        data["log"] = log.read().decode("utf-8", errors="replace")
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
            static = {"": "index.html", "app.js": "app.js", "style.css": "style.css",
                      "pdf.js": "pdf.js", "pdf.css": "pdf.css", "images-to-pdf": "pdf.html"}
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
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            if parts == ["api", "pdf", "assets"]:
                import pdf_images
                if not 0 < length <= pdf_images.MAX_UPLOAD:
                    raise ValueError("Each uploaded image must be 20 MB or smaller.")
                result = pdf_images.upload(self.app.root, unquote(self.headers.get("X-File-Name", "image")), self.rfile.read(length))
                return self.send_json(result, 201)
            if not 0 < length <= 10_000_000:
                raise ValueError("Request must be JSON smaller than 10 MB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            if parts[:2] == ["api", "pdf"]:
                return self.post_pdf(parts, body)
            with self.app.lock:
                if parts == ["api", "projects"]:
                    return self.send_json(projects.create_project(self.app.root, body["source"], body.get("options", {})), 201)
                if parts == ["api", "batch"]:
                    sources = body["sources"]
                    if not isinstance(sources, list) or not 1 <= len(sources) <= 100 or any(not isinstance(s, str) or not s.strip() for s in sources):
                        raise ValueError("Provide 1 to 100 sources.")
                    options = projects.validate_options(body.get("options", {}))
                    result = []
                    for source in sources:
                        state = projects.create_project(self.app.root, source, options)
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
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError:
            self.send_json({"error": "Project or transcript not found. Prepare the project first."}, 404)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def get_pdf(self, parts):
        import pdf_images as pdf
        if parts == ["api", "pdf", "config"]:
            return self.send_json({"image_defaults": pdf.IMAGE_DEFAULTS, "pdf_defaults": pdf.PDF_DEFAULTS,
                                   "assets": pdf.assets(self.app.root), "history": pdf.history(self.app.root)})
        if len(parts) == 5 and parts[:3] == ["api", "pdf", "assets"] and parts[4] in {"image", "thumbnail"}:
            path = pdf.asset_path(self.app.root, parts[3])
            return self.send_file(path if parts[4] == "image" else path.with_suffix(".thumb.png"))
        if len(parts) in (4, 5) and parts[:3] == ["api", "pdf", "documents"]:
            saved = pdf.document(self.app.root, parts[3])
            if len(parts) == 4:
                return self.send_json(saved)
            if parts[4] == "download":
                return self.send_file(pdf.export_path(self.app.root, parts[3]), download=saved["filename"])
        return self.send_json({"error": "PDF route not found."}, 404)

    def post_pdf(self, parts, body):
        import pdf_images as pdf
        with self.app.pdf_lock:
            if parts == ["api", "pdf", "preview"]:
                index, total = body.get("index", 1), body.get("total", 1)
                if not isinstance(index, int) or not isinstance(total, int) or not 1 <= index <= total <= pdf.MAX_PAGES:
                    raise ValueError("Invalid page number.")
                data = pdf.preview(self.app.root, body["page"], body.get("options", {}), index, total)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if parts == ["api", "pdf", "export"]:
                result = pdf.export(self.app.root, body["pages"], body.get("options", {}))
                result["url"] = f"/api/pdf/documents/{result['id']}/download"
                return self.send_json(result, 201)
        return self.send_json({"error": "PDF route not found."}, 404)

    def send_file(self, path, download=None):
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
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download))
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
