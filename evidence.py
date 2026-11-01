"""Local creator evidence: audio features that make action-led clips scoreable.

The timeline is one value per second of mono 16 kHz audio. It is built once per
source, cached as ``audio_features.npz``, and reused for every candidate. Nothing
here leaves the machine or calls a network service.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import zipfile

import numpy as np

EVIDENCE_VERSION = 1
FEATURE_VERSION = 1
SAMPLE_RATE = 16000
FRAME_SAMPLES = 800                 # 50 ms analysis frames
FRAMES_PER_SECOND = SAMPLE_RATE // FRAME_SAMPLES
READ_BYTES = 1 << 20
AUDIO_CACHE = "audio_features.npz"
SILENCE_DB = -80.0

# A raised cosine window keeps spectral flux stable across frame boundaries.
_WINDOW = np.hanning(FRAME_SAMPLES).astype(np.float32)
_FREQUENCIES = np.fft.rfftfreq(FRAME_SAMPLES, 1.0 / SAMPLE_RATE)
_HIGH_BAND = _FREQUENCIES >= 4000.0


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def smoothstep(value):
    x = clamp(value)
    return x * x * (3.0 - 2.0 * x)


def _db(value):
    return max(SILENCE_DB, 20.0 * math.log10(float(value) + 1e-9))


@dataclass
class AudioTimeline:
    """Per-second audio features for one source."""

    rms: np.ndarray
    peak: np.ndarray
    flux: np.ndarray
    zcr: np.ndarray
    high_band: np.ndarray
    burst: np.ndarray
    duration: float
    baseline: np.ndarray | None = None

    @property
    def variability(self):
        """How much louder the loud seconds are than the typical second."""
        if len(self.rms) == 0:
            return 0.0
        p50, p90 = np.percentile(self.rms, [50, 90])
        return clamp((float(p90) - float(p50)) / 12.0)

    @property
    def burst_rate(self):
        if len(self.burst) == 0:
            return 0.0
        return float(np.mean(self.burst))

    def _baseline(self):
        if self.baseline is not None and len(self.baseline) == len(self.rms):
            return self.baseline
        return _rolling_median(self.rms)

    def baseline_at(self, second):
        if len(self.rms) == 0:
            return SILENCE_DB
        index = max(0, min(len(self.rms) - 1, int(second)))
        return float(self._baseline()[index])

    def peak_times(self, min_gap=10.0):
        """Loud event seconds, at least ``min_gap`` apart, loudest first."""
        if len(self.rms) == 0:
            return []
        global_floor = float(np.median(self.rms)) + 4.0
        threshold = np.maximum(global_floor, self._baseline() + 4.0)
        occupied = np.zeros(len(self.rms), dtype=bool)
        radius = max(1, int(math.ceil(float(min_gap))) - 1)
        chosen = []
        for index in np.argsort(-self.rms):
            index = int(index)
            if self.rms[index] < threshold[index]:
                break
            if occupied[index]:
                continue
            chosen.append(float(index))
            low = max(0, index - radius)
            high = min(len(self.rms), index + radius + 1)
            occupied[low:high] = True
        return sorted(chosen)

    def trough_near(self, second, radius=1.5):
        """Time of the quietest second within ``radius`` seconds."""
        if len(self.rms) == 0:
            return float(second)
        low = max(0, int(math.floor(float(second) - radius)))
        high = min(len(self.rms) - 1, int(math.ceil(float(second) + radius)))
        if high < low:
            return float(second)
        local = self.rms[low:high + 1]
        return float(low + int(np.argmin(local)))

    def score(self, start, end):
        return audio_evidence(self, start, end)


class _TimelineBuilder:
    """Streaming frame accumulator; memory stays bounded for long sources."""

    def __init__(self):
        self.buffer = np.empty(0, dtype=np.float32)
        self.samples = 0
        self.previous_spectrum = None
        self.pending = None           # frame metrics not yet grouped into seconds
        self.rms, self.peak, self.flux = [], [], []
        self.zcr, self.high_band = [], []

    def add(self, samples):
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return
        self.samples += int(samples.size)
        if self.buffer.size:
            samples = np.concatenate([self.buffer, samples])
        usable = (samples.size // FRAME_SAMPLES) * FRAME_SAMPLES
        if usable:
            frames = samples[:usable].reshape(-1, FRAME_SAMPLES)
            self._add_frames(frames)
        self.buffer = samples[usable:].copy()

    def _add_frames(self, frames):
        energy = frames.astype(np.float64)
        rms = np.sqrt(np.mean(energy * energy, axis=1))
        peak = np.max(np.abs(energy), axis=1)
        signs = np.signbit(frames)
        zcr = (np.count_nonzero(np.diff(signs, axis=1), axis=1)
               / max(1, FRAME_SAMPLES - 1))
        spectra = np.abs(np.fft.rfft(frames * _WINDOW, axis=1))
        if self.previous_spectrum is None:
            flux = np.zeros(len(frames), dtype=np.float64)
            if len(frames) > 1:
                diff = np.maximum(spectra[1:] - spectra[:-1], 0.0)
                flux[1:] = diff.sum(axis=1) / (spectra[1:].sum(axis=1) + 1e-9)
        else:
            previous = np.vstack([self.previous_spectrum[None, :], spectra[:-1]])
            diff = np.maximum(spectra - previous, 0.0)
            flux = diff.sum(axis=1) / (spectra.sum(axis=1) + 1e-9)
        self.previous_spectrum = spectra[-1]
        power = spectra * spectra
        high = power[:, _HIGH_BAND].sum(axis=1) / (power.sum(axis=1) + 1e-9)
        metrics = np.column_stack([
            np.maximum(SILENCE_DB, 20.0 * np.log10(rms + 1e-9)),
            np.maximum(SILENCE_DB, 20.0 * np.log10(peak + 1e-9)),
            np.clip(flux, 0.0, 1.0),
            np.clip(zcr, 0.0, 1.0),
            np.clip(high, 0.0, 1.0),
        ]).astype(np.float32)
        self.pending = metrics if self.pending is None \
            else np.concatenate([self.pending, metrics])

    def _flush_seconds(self):
        if self.pending is None or len(self.pending) < FRAMES_PER_SECOND:
            return
        full = (len(self.pending) // FRAMES_PER_SECOND) * FRAMES_PER_SECOND
        groups = self.pending[:full].reshape(-1, FRAMES_PER_SECOND, 5)
        means = groups.mean(axis=1)
        for row in means:
            self.rms.append(float(row[0]))
            self.peak.append(float(row[1]))
            self.flux.append(float(row[2]))
            self.zcr.append(float(row[3]))
            self.high_band.append(float(row[4]))
        self.pending = self.pending[full:].copy()

    def finish(self):
        self._flush_seconds()
        if self.buffer.size:
            padded = np.zeros(FRAME_SAMPLES, dtype=np.float32)
            padded[:self.buffer.size] = self.buffer
            self._add_frames(padded.reshape(1, FRAME_SAMPLES))
            self.buffer = np.empty(0, dtype=np.float32)
        if self.pending is not None and len(self.pending):
            means = self.pending.mean(axis=0)
            self.rms.append(float(means[0]))
            self.peak.append(max(float(v) for v in self.pending[:, 1]))
            self.flux.append(float(means[2]))
            self.zcr.append(float(means[3]))
            self.high_band.append(float(means[4]))
        self.pending = None
        rms = np.asarray(self.rms, dtype=np.float32)
        duration = self.samples / float(SAMPLE_RATE)
        if len(rms) == 0:
            return AudioTimeline(
                rms=np.empty(0, dtype=np.float32), peak=np.empty(0, dtype=np.float32),
                flux=np.empty(0, dtype=np.float32), zcr=np.empty(0, dtype=np.float32),
                high_band=np.empty(0, dtype=np.float32),
                burst=np.empty(0, dtype=bool), duration=duration,
                baseline=np.empty(0, dtype=np.float32))
        baseline = _rolling_median(rms)
        global_floor = float(np.median(rms)) + 8.0
        threshold = np.maximum(global_floor, baseline + 8.0)
        return AudioTimeline(
            rms=rms,
            peak=np.asarray(self.peak, dtype=np.float32),
            flux=np.asarray(self.flux, dtype=np.float32),
            zcr=np.asarray(self.zcr, dtype=np.float32),
            high_band=np.asarray(self.high_band, dtype=np.float32),
            burst=(rms > threshold),
            duration=duration,
            baseline=baseline,
        )


def _rolling_median(values, window=61, sample_limit=200000):
    """Centred median baseline, decimated for very long sources."""
    values = np.asarray(values, dtype=np.float32).reshape(-1)
    count = len(values)
    if count == 0:
        return np.empty(0, dtype=np.float32)
    half = max(0, int(window) // 2)
    if count <= sample_limit:
        padded = np.pad(values, (half, half), mode="edge")
        return np.median(
            np.lib.stride_tricks.sliding_window_view(padded, window),
            axis=1).astype(np.float32)
    step = int(math.ceil(count / float(sample_limit)))
    points = np.arange(0, count, step)
    medians = np.empty(len(points), dtype=np.float32)
    for position, index in enumerate(points):
        low = max(0, index - half)
        high = min(count, index + half + 1)
        medians[position] = float(np.median(values[low:high]))
    return np.interp(np.arange(count), points, medians).astype(np.float32)


def features_from_samples(samples):
    """Build a one-row-per-second timeline from mono float samples."""
    builder = _TimelineBuilder()
    builder.add(samples)
    return builder.finish()


def source_identity(source):
    """Stable identity for cache validation (path, size and mtime)."""
    if not source:
        return ""
    path = Path(source)
    try:
        stat = path.stat()
    except OSError:
        return str(source)
    return json.dumps([str(path.resolve()), stat.st_size, stat.st_mtime_ns])


def build_audio_timeline(source):
    """Decode the audio track with ffmpeg, streaming it through the builder."""
    if not source:
        return None
    path = Path(source)
    if not path.is_file():
        return None
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
    ]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    builder = _TimelineBuilder()
    leftover = b""
    try:
        assert process.stdout is not None
        while True:
            block = process.stdout.read(READ_BYTES)
            if not block:
                break
            leftover += block
            usable = len(leftover) - (len(leftover) % 4)
            if usable:
                builder.add(np.frombuffer(leftover[:usable], dtype=np.float32))
                leftover = leftover[usable:]
        code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if code != 0:
        return None
    timeline = builder.finish()
    return timeline if timeline.duration > 0 else None


def save_timeline(timeline, path, identity=""):
    """Atomically write the compressed timeline cache."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": np.int64(FEATURE_VERSION),
        "identity": np.array(str(identity or "")),
        "duration": np.float64(timeline.duration),
        "rms": np.asarray(timeline.rms, dtype=np.float32),
        "peak": np.asarray(timeline.peak, dtype=np.float32),
        "flux": np.asarray(timeline.flux, dtype=np.float32),
        "zcr": np.asarray(timeline.zcr, dtype=np.float32),
        "high_band": np.asarray(timeline.high_band, dtype=np.float32),
        "burst": np.asarray(timeline.burst, dtype=bool),
    }
    if timeline.baseline is not None:
        payload["baseline"] = np.asarray(timeline.baseline, dtype=np.float32)
    with open(temp, "wb") as handle:
        np.savez_compressed(handle, **payload)
    temp.replace(path)


def load_timeline(path, identity=None):
    """Return a cached timeline, or None when missing, stale, or corrupt."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            if int(data["version"]) != FEATURE_VERSION:
                return None
            stored = str(data["identity"].item())
            if identity is not None and stored != str(identity):
                return None
            rms = np.asarray(data["rms"], dtype=np.float32)
            peak = np.asarray(data["peak"], dtype=np.float32)
            flux = np.asarray(data["flux"], dtype=np.float32)
            zcr = np.asarray(data["zcr"], dtype=np.float32)
            high_band = np.asarray(data["high_band"], dtype=np.float32)
            burst = np.asarray(data["burst"], dtype=bool)
            baseline = (np.asarray(data["baseline"], dtype=np.float32)
                        if "baseline" in data else None)
            duration = float(data["duration"])
    except (OSError, ValueError, KeyError, TypeError, EOFError,
            zipfile.BadZipFile):
        return None
    lengths = {len(rms), len(peak), len(flux), len(zcr), len(high_band),
               len(burst)}
    if lengths != {len(rms)} or (baseline is not None
                                and len(baseline) != len(rms)):
        return None
    return AudioTimeline(rms=rms, peak=peak, flux=flux, zcr=zcr,
                         high_band=high_band, burst=burst,
                         duration=duration, baseline=baseline)


def load_audio_timeline(source, workdir, identity=None):
    """Cache-backed timeline for one source; never raises on decode failure."""
    workdir = Path(workdir)
    path = workdir / AUDIO_CACHE
    cache_identity = identity if identity is not None else source_identity(source)
    cached = load_timeline(path, cache_identity)
    if cached is not None:
        return cached
    timeline = build_audio_timeline(source)
    if timeline is None:
        return None
    try:
        save_timeline(timeline, path, cache_identity)
    except OSError:
        pass
    return timeline
