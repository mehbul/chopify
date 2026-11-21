"""Fast Parakeet ASR backend and its integration with the transcription stage.

All external parts (sherpa-onnx, the model, ffmpeg) are faked, so these run
without a download or an optional dependency.
"""
import json
import threading
import time

import numpy as np

import download_and_transcribe as dt
import fast_asr


def test_words_from_tokens_merges_subwords_and_punctuation():
    tokens = ["\u2581Hello", "\u2581world", ".", "\u2581again"]
    stamps = [0.0, 0.5, 0.6, 1.0]
    words = fast_asr.words_from_tokens(tokens, stamps, 0, 2 * 16000)
    assert [w["word"] for w in words] == ["Hello", "world.", "again"]
    assert words[1]["start"] == 0.5
    assert words[1]["end"] == 0.72


def test_chunks_cut_near_the_quietest_point():
    audio = np.full(30 * 16000, 0.1, dtype=np.float32)
    audio[14 * 16000:15 * 16000] = 0.0
    bounds = list(fast_asr.chunks(audio))
    assert bounds[0][0] == 0
    assert 13 * 16000 <= bounds[0][1] <= 16 * 16000
    assert bounds[-1][1] == len(audio)


def test_transcribe_skips_silence_and_builds_segments(tmp_path, monkeypatch):
    audio = np.full(2 * 16000, 0.1, dtype=np.float32)
    monkeypatch.setattr(fast_asr, "read_wav", lambda path: audio)
    monkeypatch.setattr(fast_asr, "_take", lambda: object())
    monkeypatch.setattr(
        fast_asr, "decode_pieces",
        lambda pieces, rec: [(["\u2581hello", "\u2581world", "."],
                              [0.0, 0.5, 0.6])])
    result = fast_asr.transcribe(tmp_path / "audio.wav")
    assert [w["word"] for w in result["words"]] == ["hello", "world."]
    assert result["segments"][0]["text"] == "hello world."
    assert result["language"] == "en"


def test_transcribe_returns_empty_for_pure_silence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fast_asr, "read_wav",
        lambda path: np.zeros(2 * 16000, dtype=np.float32))
    result = fast_asr.transcribe(tmp_path / "silent.wav")
    assert result == {"words": [], "segments": [], "language": "en"}


def test_transcribe_uses_parakeet_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(fast_asr, "available", lambda: True)
    seen = []
    monkeypatch.setattr(
        dt, "_fast_transcribe_media",
        lambda video: (seen.append("fast") or
                       ([{"start": 0, "end": 1, "text": "hi"}],
                        [{"word": "hi", "start": 0.0, "end": 1.0}], "en", 1.0)))
    monkeypatch.setattr(
        dt, "_load_model", lambda *args: seen.append("whisper"))
    out = dt.transcribe(tmp_path / "video.mp4", tmp_path, "tiny", "cpu",
                        "int8", asr_backend="parakeet")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert seen == ["fast"]
    assert data["asr"] == "parakeet"
    assert data["model"] == "parakeet"
    assert data["words"][0]["word"] == "hi"


def test_transcribe_falls_back_to_whisper(tmp_path, monkeypatch):
    monkeypatch.setattr(fast_asr, "available", lambda: False)
    monkeypatch.setattr(
        dt, "_transcribe_media",
        lambda model, media: ([{"start": 0, "end": 1, "text": "hi"}],
                              [{"word": "hi", "start": 0.0, "end": 1.0}],
                              "en", 1.0))
    seen = []
    monkeypatch.setattr(
        dt, "_load_model", lambda *args: seen.append("whisper") or object())
    out = dt.transcribe(tmp_path / "video.mp4", tmp_path, "tiny", "cpu",
                        "int8", asr_backend="parakeet")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert seen == ["whisper"]
    assert data["asr"] == "faster-whisper"
    assert data["model"] == "tiny"


def test_chunked_uses_parakeet_for_each_part(tmp_path, monkeypatch):
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")
    monkeypatch.setattr(fast_asr, "available", lambda: True)
    monkeypatch.setattr(dt, "CHUNK_SECONDS", 10)
    monkeypatch.setattr(dt, "OVERLAP_SECONDS", 0)
    monkeypatch.setattr(dt, "_probe_duration", lambda path: 20.0)
    monkeypatch.setattr(
        dt, "_extract_chunk",
        lambda source, start, end, path: path.write_bytes(b"wav"))
    seen = []

    def fake(video, offset=0.0, wav=None, max_workers=None):
        seen.append(offset)
        return ([{"start": offset, "end": offset + 1, "text": "hi"}],
                [{"word": "hi", "start": offset, "end": offset + 1}], "en", 1.0)

    monkeypatch.setattr(dt, "_fast_transcribe_media", fake)
    monkeypatch.setattr(dt, "_load_model", lambda *args: 1 / 0)
    out = dt.transcribe_chunked(source, tmp_path, "tiny", "cpu", "int8", 20.0,
                                asr_backend="parakeet")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert sorted(seen) == [0.0, 10.0]
    assert data["asr"] == "parakeet"
    assert [w["start"] for w in data["words"]] == [0.0, 10.0]


def test_asr_none_skips_transcription(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "_load_model", lambda *args: 1 / 0)
    out = dt.transcribe(tmp_path / "video.mp4", tmp_path, "tiny", "cpu",
                        "int8", 100.0, asr_backend="none")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["asr"] == "none"
    assert data["words"] == [] and data["segments"] == []
    assert data["duration"] == 100.0


def test_long_source_transcribes_parts_in_parallel(tmp_path, monkeypatch):
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")
    monkeypatch.setenv("CHOPIFY_ASR_PARTS", "2")
    monkeypatch.setattr(fast_asr, "available", lambda: True)
    monkeypatch.setattr(dt, "CHUNK_SECONDS", 10)
    monkeypatch.setattr(dt, "OVERLAP_SECONDS", 0)
    monkeypatch.setattr(dt, "_probe_duration", lambda path: 20.0)
    monkeypatch.setattr(
        dt, "_extract_chunk",
        lambda source, start, end, path: path.write_bytes(b"wav"))

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def slow_fake(video, offset=0.0, wav=None, max_workers=None):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return ([], [{"word": "hi", "start": offset, "end": offset + 1}],
                "en", 1.0)

    monkeypatch.setattr(dt, "_fast_transcribe_media", slow_fake)
    monkeypatch.setattr(dt, "_load_model", lambda *args: 1 / 0)
    dt.transcribe_chunked(source, tmp_path, "tiny", "cpu", "int8", 20.0,
                          asr_backend="parakeet")
    assert state["peak"] == 2
