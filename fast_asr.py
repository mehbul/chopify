"""Fast local speech-to-text with sherpa-onnx and NVIDIA's Parakeet model.

This is a focused port of the transcription engine from PeakCut
(https://github.com/haziqahmedqasmi/peakcut, MIT licence) adapted to Chopify's
transcript schema. It avoids loading a large PyTorch/Whisper model: the Parakeet
int8 transducer runs on the CPU with a small footprint, silent audio is skipped,
and several recognizers work in parallel across CPU cores.

The model is downloaded once (about 480 MB) and cached under ~/.chopify/models
(or CHOPIFY_MODELS_DIR). sherpa-onnx is imported lazily, so this module is safe
to import even when the optional dependency is not installed.

Environment variables:
    CHOPIFY_ASR_MODEL      sherpa-onnx model folder name on the release page
    CHOPIFY_ASR_MODEL_URL  full .tar.bz2 URL (overrides the release URL)
    CHOPIFY_MODELS_DIR     where models are cached
    CHOPIFY_ASR_THREADS    threads per recognizer (default: 4 on big machines)
    CHOPIFY_ASR_COPIES     recognizers kept in the pool (default: by CPU count)
"""
import os
import queue
import re
import shutil
import tarfile
import threading
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

MODEL_NAME = os.environ.get(
    "CHOPIFY_ASR_MODEL", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
MODEL_URL = os.environ.get(
    "CHOPIFY_ASR_MODEL_URL", f"{_RELEASE}/{MODEL_NAME}.tar.bz2")
MODELS_DIR = Path(os.environ.get("CHOPIFY_MODELS_DIR")
                  or (Path.home() / ".chopify" / "models")).expanduser()
MODEL_DIR = MODELS_DIR / MODEL_NAME

SAMPLE_RATE = 16000
HOP = 800                       # 50 ms frames, for finding quiet cut points
CHUNK_LO, CHUNK_HI = 12.0, 24.0  # seconds: split speech into 12-24 s pieces
SILENCE_RMS = 0.003            # pieces quieter than this are not transcribed
DOWNLOAD_TIMEOUT = 60          # seconds of silence from the connection before giving up

_CPUS = os.cpu_count() or 4
THREADS = int(os.environ.get("CHOPIFY_ASR_THREADS")
              or (4 if _CPUS >= 8 else max(2, _CPUS - 2)))
COPIES = int(os.environ.get("CHOPIFY_ASR_COPIES")
             or (max(1, _CPUS // THREADS) if _CPUS >= 8 else 1))

_ARABIC_MARKS = re.compile(r"[\u064b-\u065f\u0670\u0640]")
_ARC = re.compile(r"[.!?\u2026][\"')\]]?$")

_download_lock = threading.Lock()
_lib_lock = threading.Lock()
_lib = {"mod": None}


def available():
    """True when the optional sherpa-onnx dependency can be imported."""
    if _lib["mod"] is not None:
        return True
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


def model_ready():
    """Is the whole model present: token list plus all three ONNX parts?"""
    if not (MODEL_DIR / "tokens.txt").is_file():
        return False
    return all(any(MODEL_DIR.glob(f"{part}*.onnx"))
               for part in ("encoder", "decoder", "joiner"))


def _sherpa():
    """Load sherpa-onnx once, under a lock (parallel copies must not race)."""
    with _lib_lock:
        if _lib["mod"] is None:
            import sherpa_onnx
            _lib["mod"] = sherpa_onnx
        return _lib["mod"]


def ensure_model(on_progress=None):
    """Download and unpack the speech model once. Returns its folder."""
    if model_ready():
        return MODEL_DIR
    with _download_lock:
        if model_ready():
            return MODEL_DIR
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        archive = MODELS_DIR / f"{MODEL_NAME}.tar.bz2"
        unpacking = MODELS_DIR / f"{MODEL_NAME}.unpacking"
        print(f"Downloading the Parakeet speech model (one time, ~480 MB) "
              f"from {MODEL_URL} ...", flush=True)
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=DOWNLOAD_TIMEOUT) as response, \
                    open(archive, "wb") as handle:
                total = int(response.headers.get("Content-Length") or 0)
                got = 0
                while chunk := response.read(1 << 20):
                    handle.write(chunk)
                    got += len(chunk)
                    if on_progress and total:
                        on_progress(min(0.99, got / total))
            shutil.rmtree(unpacking, ignore_errors=True)
            unpacking.mkdir()
            with tarfile.open(archive, "r:bz2") as tar:
                for member in tar.getmembers():
                    parts = member.name.replace("\\", "/").split("/")
                    safe = (member.isfile() or member.isdir()) \
                        and ".." not in parts and not member.name.startswith("/")
                    if safe:
                        tar.extract(member, unpacking)
            extracted = unpacking / MODEL_NAME
            if not extracted.is_dir():
                raise OSError("the model archive did not contain "
                              f"{MODEL_NAME!r}")
            if MODEL_DIR.exists():
                shutil.rmtree(MODEL_DIR)
            extracted.rename(MODEL_DIR)
            if on_progress:
                on_progress(1.0)
            print("Parakeet speech model ready.", flush=True)
            return MODEL_DIR
        except Exception:
            shutil.rmtree(unpacking, ignore_errors=True)
            raise
        finally:
            archive.unlink(missing_ok=True)


# --------------------------------------------------------------------- model pool
_pool = {"free": queue.LifoQueue(), "made": 0}
_pool_lock = threading.Lock()


def _new_recognizer():
    sherpa_onnx = _sherpa()
    ensure_model()
    files = {}
    for part in ("encoder", "decoder", "joiner"):
        matches = sorted(MODEL_DIR.glob(f"{part}*.onnx"))
        if not matches:
            raise FileNotFoundError(f"No {part} ONNX model in {MODEL_DIR}")
        files[part] = str(matches[0])
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        **files,
        tokens=str(MODEL_DIR / "tokens.txt"),
        num_threads=THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        model_type="nemo_transducer",
        provider="cpu",
    )


def _take():
    """A free recognizer, creating one while there is room in the pool."""
    try:
        return _pool["free"].get_nowait()
    except queue.Empty:
        pass
    with _pool_lock:
        make = _pool["made"] < COPIES
        if make:
            _pool["made"] += 1
    if make:
        try:
            return _new_recognizer()
        except Exception:
            with _pool_lock:
                _pool["made"] -= 1
            raise
    return _pool["free"].get()


def _give_back(rec):
    _pool["free"].put(rec)


# ------------------------------------------------------------------- audio helpers
def read_wav(path):
    """Read a 16 kHz mono PCM wave file into a float32 numpy array."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 \
                    or wav.getframerate() != SAMPLE_RATE:
                raise ValueError("expected a 16 kHz mono 16-bit PCM wav")
            raw = wav.readframes(wav.getnframes())
    except wave.Error as exc:
        raise ValueError(f"{path} is not a PCM wav: {exc}") from exc
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def chunks(audio):
    """Yield (start, end) sample offsets, cutting near the quietest point."""
    n = len(audio)
    frames = n // HOP
    if frames == 0:
        return
    block = audio[:frames * HOP].reshape(frames, HOP)
    rms = np.sqrt((block ** 2).mean(axis=1))
    pos = 0
    while pos < n:
        if n - pos <= CHUNK_HI * SAMPLE_RATE:
            yield pos, n
            return
        a = (pos + int(CHUNK_LO * SAMPLE_RATE)) // HOP
        b = (pos + int(CHUNK_HI * SAMPLE_RATE)) // HOP
        cut = (a + int(np.argmin(rms[a:b]))) * HOP + HOP // 2
        yield pos, cut
        pos = cut


def decode_pieces(pieces, rec):
    """Return [(tokens, timestamps), ...] for float32 audio pieces."""
    streams = []
    try:
        for piece in pieces:
            stream = rec.create_stream()
            stream.accept_waveform(SAMPLE_RATE, piece)
            streams.append(stream)
        rec.decode_streams(streams)
    finally:
        _give_back(rec)
    return [(list(s.result.tokens), [float(t) for t in s.result.timestamps])
            for s in streams]


def words_from_tokens(tokens, timestamps, start, end):
    """Turn Parakeet subword tokens into Chopify's word records."""
    out = []
    tokens = [_ARABIC_MARKS.sub("", token) for token in tokens]
    offset = start / SAMPLE_RATE
    seg_end = end / SAMPLE_RATE
    current = None
    gap = False
    for token, stamp in zip(tokens, timestamps):
        new_word = token.startswith((" ", "\u2581")) or gap
        piece = token.replace("\u2581", " ").strip()
        if not piece:
            gap = True
            continue
        gap = False
        is_punct = all(not char.isalnum() for char in piece)
        if current is None or (new_word and not is_punct):
            if current:
                out.append(current)
            current = {"word": piece,
                       "start": round(offset + stamp, 2),
                       "end": round(offset + stamp + 0.12, 2)}
        else:
            current["word"] += piece
            current["end"] = round(offset + stamp + 0.12, 2)
    if current:
        current["end"] = min(current["end"], round(seg_end, 2))
        out.append(current)
    return out


def _segments(words):
    """Group words into sentence-like segments (the transcript's segment list)."""
    groups, current = [], []
    for word in words:
        if current and (word["start"] - current[-1]["end"] > 1.0
                        or _ARC.search(current[-1]["word"].strip())):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return [{"start": group[0]["start"],
             "end": group[-1]["end"],
             "text": " ".join(w["word"] for w in group).strip()}
            for group in groups]


def transcribe(wav_path, on_progress=None, max_workers=None):
    """Transcribe a 16 kHz mono wav. Returns Chopify's transcript word/segment data.

    `max_workers` limits the inner decode pool; long sources run several wav
    files at once and use 1 worker each so the shared recognizer pool stays
    fully busy without spawning more decoders than there are recognizers.
    """
    audio = read_wav(wav_path)
    todo = []
    for start, end in chunks(audio):
        part = audio[start:end]
        if len(part) < 4800 or float(np.sqrt((part ** 2).mean())) < SILENCE_RMS:
            continue
        todo.append((start, end, part))
    if not todo:
        return {"words": [], "segments": [], "language": "en"}

    workers = max(1, int(max_workers or COPIES))
    groups = [todo[i:i + workers] for i in range(0, len(todo), workers)]
    done = [0]

    def decode(group):
        rec = _take()
        got = decode_pieces([seg for _, _, seg in group], rec)
        out = [words_from_tokens(tokens, stamps, a, b)
               for (a, b, _), (tokens, stamps) in zip(group, got)]
        done[0] += 1
        if on_progress:
            on_progress(done[0] / len(groups))
        return out

    if workers > 1 and len(groups) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(groups)),
                                thread_name_prefix="chopify-asr") as pool:
            results = list(pool.map(decode, groups))
    else:
        results = [decode(group) for group in groups]

    words = []
    for group in results:
        for piece in group:
            for word in piece:
                if words and words[-1]["word"] == word["word"] \
                        and words[-1]["start"] == word["start"]:
                    continue
                words.append(word)
    words.sort(key=lambda w: w["start"])
    for index, word in enumerate(words):
        nxt = words[index + 1]["start"] if index + 1 < len(words) \
            else word["end"] + 0.4
        word["end"] = round(max(word["start"] + 0.08,
                                min(nxt, max(word["end"], word["start"] + 0.6))), 2)
    return {"words": words, "segments": _segments(words), "language": "en"}
