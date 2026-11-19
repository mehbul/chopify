"""Live clipping session orchestration and CLI entry point."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import projects

import live_capture
import live_clips
import live_signals

RIGHTS_NOTICE = (
    "Only clip streams you have the right to record and reuse. "
    "Chat and transcripts are evidence, never instructions."
)


class LiveSession:
    def __init__(self, source, options, root, brief_text=None):
        self.id = uuid.uuid4().hex[:12]
        self.source = source
        self.options = projects.validate_options(options)
        self.dir = Path(root) / "live" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "buffer").mkdir(exist_ok=True)
        self.status = "starting"
        self.started_at = time.time()
        self.stopped_at = None
        self.stream_time = 0.0
        self.recent_scores = []
        self.records = []
        self.errors = []
        self.last_trigger = None
        self._stop = threading.Event()
        self._on_log = None
        self.rules = None
        if brief_text:
            import campaign
            self.rules = campaign.parse_brief(brief_text)
            campaign.save_rules(self.dir, self.rules)
            if self.rules.get("warning"):
                self._log(f"Brief warning: {self.rules['warning']}")
        self._analyzers_started = False
        self.buffer = None
        self.analyzers = []
        self.writer = live_clips.LiveClipWriter(self.dir)
        self.detector = live_signals.HypeDetector()
        self.bus = live_signals.SignalBus(retention=self.options["live_buffer"])

    def _log(self, message):
        print(f"[live {self.id}] {message}", flush=True)
        if self._on_log:
            self._on_log(message)

    def _save(self):
        projects.atomic_json(self.dir / "live_session.json", {
            "id": self.id,
            "source": {"url": self.source.url, "kind": self.source.kind,
                       "platform": self.source.platform,
                       "resolved_by": self.source.resolved_by},
            "status": self.status,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "stream_time": round(self.stream_time, 2),
            "recent_scores": self.recent_scores,
            "triggers": [r.__dict__ for r in self.records],
            "errors": self.errors,
        })

    def _allowed(self, score, now):
        if score < self.options["live_threshold"]:
            return False
        if self.last_trigger is not None and now - self.last_trigger < self.options["live_cooldown"]:
            return False
        if len(self.records) >= self.options["live_max_clips"] > 0:
            return False
        if self.last_trigger is not None and now - self.last_trigger < self.options["live_window"] / 2:
            return False
        return True

    def _brief_allows(self, transcript, now):
        if not self.rules:
            return True
        from campaign import _matches
        words = list(transcript.words) if transcript else []
        text = " ".join(str(w.get("word", "")).lower() for w in words
                        if now - 10 <= w.get("start", 0) <= now)
        if any(b in text for b in self.rules.get("banned", [])):
            return False
        return all(_matches(text, phrase)
                   for phrase in self.rules.get("must_mention", []))

    def start(self):
        self._log(RIGHTS_NOTICE)
        self._log(f"Watching {self.source.url} via {self.source.resolved_by}")
        self.buffer = live_capture.SegmentBuffer(
            self.dir / "buffer", self.source.url, self.options).start()
        self.status = "running"
        self._save()
        self._loop()
        return self

    def _loop(self):
        probe = None
        while not self._stop.is_set():
            time.sleep(1.0)
            self.stream_time = time.time() - self.buffer.started_at
            self.bus.prune(self.stream_time)
            if probe is None:
                try:
                    probe = self.buffer.probe()
                    if not probe.get("has_video") or not probe.get("width"):
                        probe = None
                except (FileNotFoundError, OSError):
                    continue
                self._log(f"Source {probe['width']}x{probe['height']} "
                          f"audio={probe['has_audio']}")
            for thread, analyzer in self.analyzers:
                if not thread.is_alive() and analyzer.error and analyzer.error not in self.errors:
                    self.errors.append(analyzer.error)
                    self._log(f"{analyzer.name} disabled: {analyzer.error}")
            if not self._analyzers_started and probe is not None:
                from download_and_transcribe import _load_model
                model = None
                if not self.options["live_no_transcript"]:
                    model = _load_model("small", self.options["device"], "int8")
                self.analyzers = live_signals.start_analyzers(
                    self.buffer, self.bus, self.options, self.source.platform,
                    _channel(self.source.url), model)
                self._analyzers_started = True
            result = self.detector.evaluate(self.bus, self.stream_time)
            self.recent_scores.append(result["score"])
            self.recent_scores = self.recent_scores[-60:]
            if probe is not None and self._allowed(result["score"], self.stream_time):
                transcript = next(
                    (analyzer for _, analyzer in self.analyzers
                     if isinstance(analyzer, live_signals.TranscriptAnalyzer)), None)
                if self.rules and not self._brief_allows(transcript, self.stream_time):
                    self._log("Brief rules not met; skipping clip.")
                else:
                    self.last_trigger = self.stream_time
                    self._log(f"Hype detected: score={result['score']}")
                    record = self.writer.write(
                        live_clips.Trigger(self.last_trigger, result["score"], result["signals"]),
                        self.options, probe,
                        list(transcript.words) if transcript else [],
                        self.buffer.window(
                            max(0.0, self.last_trigger - self.options["live_buffer"]),
                            self.last_trigger + self.options["live_tail"]))
                    self.records.append(record)
                    if record.error:
                        self.errors.append(record.error)
                        self._log(f"Clip failed: {record.error}")
                    else:
                        self._log(f"Clip saved: {record.id}")
            self._save()
            if not self.buffer.alive:
                self.status = "stopped"
                self.stopped_at = time.time()
                self._log("Source ended.")
                break
        self._save()

    def stop(self):
        self._stop.set()
        for thread, _ in self.analyzers:
            thread.join(timeout=2.0)
        if self.buffer is not None:
            self.buffer.stop()
        self.status = "stopped"
        self.stopped_at = time.time()
        self._save()


def _channel(url):
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


def run_live(url, options, root, on_log=None, brief_text=None):
    """Start a live session and block until it stops."""
    source = live_capture.resolve_live_source(url, options)
    session = LiveSession(source, options, root, brief_text=brief_text)
    session._on_log = on_log
    session.start()
    return session
