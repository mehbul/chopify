"""Chunked, resumable transcription for long sources."""
import json
from pathlib import Path

import download_and_transcribe


class FakeResult:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""


class FakeWord:
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end


class FakeSegment:
    def __init__(self, start, end, text, words=None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words or []


class FakeInfo:
    def __init__(self, language="en", duration=1.0, probability=0.99):
        self.language = language
        self.duration = duration
        self.language_probability = probability


def test_merge_assigns_each_overlap_word_once():
    parts = [
        {"index": 0, "start": 0, "end": 1830, "language": "en",
         "segments": [{"start": 1790, "end": 1802, "text": "late"}],
         "words": [{"word": "late", "start": 1790, "end": 1800},
                   {"word": "edge", "start": 1805, "end": 1810}]},
        {"index": 1, "start": 1800, "end": 3600, "language": "en",
         "segments": [{"start": 1805, "end": 1810, "text": "edge"}],
         "words": [{"word": "edge", "start": 1805, "end": 1815},
                   {"word": "next", "start": 1830, "end": 1835}]},
    ]
    merged = download_and_transcribe.merge_transcript_parts(parts, 3600)
    assert [w["word"] for w in merged["words"]] == ["late", "edge", "next"]
    assert [s["text"] for s in merged["segments"]] == ["late"]
    assert merged["chunks"] == 2
    assert merged["duration"] == 3600


def test_chunked_transcription_resumes_completed_parts(tmp_path, monkeypatch):
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")
    calls = []
    monkeypatch.setattr(download_and_transcribe, "CHUNK_SECONDS", 1800)
    monkeypatch.setattr(download_and_transcribe, "OVERLAP_SECONDS", 30)
    monkeypatch.setattr(download_and_transcribe, "_probe_duration",
                        lambda path: 3600.0)
    monkeypatch.setattr(
        download_and_transcribe, "_extract_chunk",
        lambda source, start, end, path: Path(path).write_bytes(b"wav"))

    class Model:
        def transcribe(self, path, **kwargs):
            calls.append(path)
            return iter([FakeSegment(
                0, 1, "hello",
                [FakeWord("hello", 0.0, 1.0)])]), FakeInfo("en", 1.0)

    monkeypatch.setattr(download_and_transcribe, "_load_model",
                        lambda *args: Model())

    download_and_transcribe.transcribe_chunked(
        source, tmp_path, "tiny", "cpu", "int8", 3600.0)
    first = len(calls)
    download_and_transcribe.transcribe_chunked(
        source, tmp_path, "tiny", "cpu", "int8", 3600.0)
    assert len(calls) == first

    data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert data["chunked"] is True
    assert data["chunks"] == 2
    assert data["duration"] == 3600.0
    assert data["transcript_version"] == download_and_transcribe.TRANSCRIPT_VERSION
    assert data["words"][0]["word"] == "hello"
    assert data["words"][0]["start"] == 0.0
    assert [p.name for p in tmp_path.glob("transcript.part*.json")] == [
        "transcript.part000.json", "transcript.part001.json"]


def test_chunked_transcription_offsets_later_parts(tmp_path, monkeypatch):
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")
    monkeypatch.setattr(download_and_transcribe, "CHUNK_SECONDS", 1800)
    monkeypatch.setattr(download_and_transcribe, "_probe_duration",
                        lambda path: 3600.0)
    monkeypatch.setattr(
        download_and_transcribe, "_extract_chunk",
        lambda source, start, end, path: Path(path).write_bytes(b"wav"))

    class Model:
        def transcribe(self, path, **kwargs):
            return iter([FakeSegment(
                20, 21, "hello",
                [FakeWord("hello", 20.0, 21.0)])]), FakeInfo("en", 1.0)

    monkeypatch.setattr(download_and_transcribe, "_load_model",
                        lambda *args: Model())
    download_and_transcribe.transcribe_chunked(
        source, tmp_path, "tiny", "cpu", "int8", 3600.0)
    data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert [w["start"] for w in data["words"]] == [20.0, 1820.0]


def test_transcribe_dispatches_to_chunked(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        download_and_transcribe, "transcribe_chunked",
        lambda *args, **kwargs: seen.update(kwargs) or tmp_path / "transcript.json")
    download_and_transcribe.transcribe(
        tmp_path / "audio.m4a", tmp_path, "tiny", "cpu", "int8", 9000,
        chunked=True, source="https://example.com/v", media_kind="audio",
        render_from_sections=True, identity="abc", progress=lambda *a: None)
    assert seen["source"] == "https://example.com/v"
    assert seen["media_kind"] == "audio"
    assert seen["render_from_sections"] is True
    assert seen["identity"] == "abc"


def test_media_transcript_has_no_words(tmp_path):
    out = download_and_transcribe.write_media_transcript(
        tmp_path / "discovery.mp4", tmp_path, 5400,
        source="https://example.com/muted", identity="abc")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["words"] == [] and data["segments"] == []
    assert data["render_from_sections"] is True
    assert data["media_kind"] == "video"
    assert data["duration"] == 5400
    assert data["source"] == "https://example.com/muted"
