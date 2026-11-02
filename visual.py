"""Local visual evidence: action arcs from sampled video frames.

Frames are decoded at 2 fps and 160x90 with ffmpeg, then reduced to motion,
scene/UI change, face presence, and face motion with OpenCV. Nothing here leaves
the machine. Candidate ranges are analyzed only after the audio shortlist, and
results are cached by source identity and range.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import zipfile

import cv2
import numpy as np

import evidence

FEATURE_VERSION = 1
SAMPLE_FPS = 2
FRAME_WIDTH = 160
FRAME_HEIGHT = 90
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3
CACHE_DIR = "visual_sections"
BASELINE_CACHE = "visual_baseline.json"
BASELINE_SAMPLES = 12
BASELINE_SECONDS = 5.0
CUT_THRESHOLD = 0.45
READ_BYTES = FRAME_BYTES

_CASCADE = None


def _face_cascade():
    global _CASCADE
    if _CASCADE is None:
        factory = getattr(cv2, "CascadeClassifier", None)
        data = getattr(cv2, "data", None)
        if factory is None or data is None:
            _CASCADE = False
            return _CASCADE
        path = Path(data.haarcascades) / "haarcascade_frontalface_default.xml"
        _CASCADE = factory(str(path))
    return _CASCADE


def _rolling_median(values, window=61):
    values = np.asarray(values, dtype=np.float32).reshape(-1)
    if len(values) == 0:
        return np.empty(0, dtype=np.float32)
    half = max(0, int(window) // 2)
    padded = np.pad(values, (half, half), mode="edge")
    return np.median(
        np.lib.stride_tricks.sliding_window_view(padded, window),
        axis=1).astype(np.float32)


def _histogram(frame):
    hist = cv2.calcHist([frame], [0], None, [32], [0, 256])
    hist = np.asarray(hist, dtype=np.float32).reshape(-1)
    total = float(hist.sum())
    return hist / total if total > 0 else hist


def _histogram_distance(first, second):
    if first is None:
        return 0.0
    return float(np.clip(0.5 * np.abs(first - second).sum(), 0.0, 1.0))


def _primary_face(gray):
    cascade = _face_cascade()
    if not cascade or (hasattr(cascade, "empty") and cascade.empty()):
        return None
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4, minSize=(12, 12))
    if len(faces) == 0:
        return None
    x, y, width, height = max(faces, key=lambda item: item[2] * item[3])
    return (float(x + width / 2.0) / FRAME_WIDTH,
            float(y + height / 2.0) / FRAME_HEIGHT,
            float(width * height) / float(FRAME_WIDTH * FRAME_HEIGHT))


@dataclass
class VisualFeatures:
    """Per-frame visual features for one decoded range."""

    start: float
    end: float
    times: np.ndarray
    motion: np.ndarray
    cut: np.ndarray
    change: np.ndarray
    center_change: np.ndarray
    face: np.ndarray
    face_motion: np.ndarray
    baseline: np.ndarray | None = None

    def baseline_at(self, second):
        if len(self.motion) == 0:
            return 0.0
        index = int(round((float(second) - self.start) * SAMPLE_FPS))
        index = max(0, min(len(self.motion) - 1, index))
        source = self.baseline if self.baseline is not None \
            and len(self.baseline) == len(self.motion) \
            else _rolling_median(self.motion)
        return float(source[index])

    def score(self, start, end):
        return visual_evidence(self, start, end)


class _VisualBuilder:
    """Streaming frame accumulator; keeps only per-frame scalars."""

    def __init__(self):
        self.previous_gray = None
        self.previous_full = None
        self.previous_center = None
        self.previous_face = None
        self.frames = 0
        self.motion, self.cut, self.change, self.center_change = [], [], [], []
        self.face, self.face_motion = [], []

    def add(self, frame):
        if frame is None:
            return
        frame = np.asarray(frame, dtype=np.uint8)
        if frame.shape[0] != FRAME_HEIGHT or frame.shape[1] != FRAME_WIDTH:
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        if self.previous_gray is None:
            motion = 0.0
        else:
            difference = np.abs(gray.astype(np.float32)
                                - self.previous_gray.astype(np.float32))
            motion = float(np.clip(np.mean(difference) / 64.0, 0.0, 1.0))
        full = _histogram(gray)
        center = _histogram(
            gray[:max(1, int(FRAME_HEIGHT * 0.4)),
                 max(0, int(FRAME_WIDTH * 0.2)):max(1, int(FRAME_WIDTH * 0.8))])
        change = _histogram_distance(self.previous_full, full)
        center_change = _histogram_distance(self.previous_center, center)
        found = _primary_face(gray)
        face_motion = 0.0
        if found is not None and self.previous_face is not None:
            dx = found[0] - self.previous_face[0]
            dy = found[1] - self.previous_face[1]
            area_change = abs(math.log(max(found[2], 1e-6)
                                       / max(self.previous_face[2], 1e-6)))
            face_motion = float(np.clip(
                (math.hypot(dx, dy) / 0.25 + min(area_change, 1.0)) / 2.0,
                0.0, 1.0))
        self.motion.append(motion)
        self.cut.append(bool(change > CUT_THRESHOLD))
        self.change.append(change)
        self.center_change.append(center_change)
        self.face.append(found is not None)
        self.face_motion.append(face_motion)
        self.previous_gray = gray
        self.previous_full = full
        self.previous_center = center
        self.previous_face = found
        self.frames += 1

    def finish(self, start):
        start = float(start)
        motion = np.asarray(self.motion, dtype=np.float32)
        return VisualFeatures(
            start=start,
            end=start + self.frames / float(SAMPLE_FPS),
            times=(start + np.arange(self.frames, dtype=np.float64)
                   / float(SAMPLE_FPS)),
            motion=motion,
            cut=np.asarray(self.cut, dtype=bool),
            change=np.asarray(self.change, dtype=np.float32),
            center_change=np.asarray(self.center_change, dtype=np.float32),
            face=np.asarray(self.face, dtype=bool),
            face_motion=np.asarray(self.face_motion, dtype=np.float32),
            baseline=_rolling_median(motion) if len(motion) else None,
        )


def features_from_frames(frames, start=0.0):
    """Build visual features from RGB uint8 frames without invoking ffmpeg."""
    builder = _VisualBuilder()
    for frame in frames:
        builder.add(frame)
    return builder.finish(start)


def extract_features(source, start, end):
    """Decode one range at 2 fps with ffmpeg and return visual features."""
    if not source:
        return None
    path = Path(source)
    if not path.is_file():
        return None
    try:
        start = max(0.0, float(start))
        end = float(end)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    command = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
        "-i", str(path), "-an",
        "-vf", f"fps={SAMPLE_FPS},scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    builder = _VisualBuilder()
    leftover = b""
    try:
        assert process.stdout is not None
        while True:
            block = process.stdout.read(READ_BYTES)
            if not block:
                break
            leftover += block
            while len(leftover) >= FRAME_BYTES:
                frame = np.frombuffer(
                    leftover[:FRAME_BYTES], dtype=np.uint8
                ).reshape(FRAME_HEIGHT, FRAME_WIDTH, 3)
                builder.add(frame)
                leftover = leftover[FRAME_BYTES:]
        code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if code != 0 or builder.frames == 0:
        return None
    return builder.finish(start)


def _cache_path(workdir, source, start, end):
    identity = evidence.source_identity(source)
    key = hashlib.sha256(json.dumps([
        FEATURE_VERSION, identity, round(float(start), 2),
        round(float(end), 2),
    ]).encode("utf-8")).hexdigest()
    return Path(workdir) / CACHE_DIR / (key + ".npz")


def save_features(features, path, identity=""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "version": np.int64(FEATURE_VERSION),
        "identity": np.array(str(identity or "")),
        "start": np.float64(features.start),
        "end": np.float64(features.end),
        "times": np.asarray(features.times, dtype=np.float64),
        "motion": np.asarray(features.motion, dtype=np.float32),
        "cut": np.asarray(features.cut, dtype=bool),
        "change": np.asarray(features.change, dtype=np.float32),
        "center_change": np.asarray(features.center_change, dtype=np.float32),
        "face": np.asarray(features.face, dtype=bool),
        "face_motion": np.asarray(features.face_motion, dtype=np.float32),
    }
    if features.baseline is not None:
        payload["baseline"] = np.asarray(features.baseline, dtype=np.float32)
    with open(temp, "wb") as handle:
        np.savez_compressed(handle, **payload)
    temp.replace(path)


def load_features(path, identity=None):
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
            start = float(data["start"])
            end = float(data["end"])
            times = np.asarray(data["times"], dtype=np.float64)
            motion = np.asarray(data["motion"], dtype=np.float32)
            cut = np.asarray(data["cut"], dtype=bool)
            change = np.asarray(data["change"], dtype=np.float32)
            center_change = np.asarray(
                data["center_change"], dtype=np.float32)
            face = np.asarray(data["face"], dtype=bool)
            face_motion = np.asarray(data["face_motion"], dtype=np.float32)
            baseline = (np.asarray(data["baseline"], dtype=np.float32)
                        if "baseline" in data else None)
    except (OSError, ValueError, KeyError, TypeError, EOFError,
            zipfile.BadZipFile):
        return None
    lengths = {len(times), len(motion), len(cut), len(change),
               len(center_change), len(face), len(face_motion)}
    if lengths != {len(times)} or (baseline is not None
                                   and len(baseline) != len(times)):
        return None
    return VisualFeatures(
        start=start, end=end, times=times, motion=motion, cut=cut,
        change=change, center_change=center_change, face=face,
        face_motion=face_motion, baseline=baseline)


def load_or_analyze(source, workdir, ranges):
    """Return cached or freshly decoded features for each requested range."""
    if not source or not Path(source).is_file():
        return []
    identity = evidence.source_identity(source)
    workdir = Path(workdir)
    sections = []
    for start, end in ranges:
        path = _cache_path(workdir, source, start, end)
        cached = load_features(path, identity)
        if cached is None:
            cached = extract_features(source, start, end)
            if cached is None:
                continue
            try:
                save_features(cached, path, identity)
            except OSError:
                pass
        sections.append(cached)
    return sections


def visual_features_for(sections, start, end):
    """Return the sampled section containing a candidate range."""
    for section in sections:
        if section.start <= start + 1e-6 and section.end >= end - 1e-6:
            return section
    return None


# ------------------------------------------------------------ source baseline

@dataclass
class VisualBaseline:
    """Sparse whole-source visual baseline."""

    value: float
    motion_norm: float
    cut_rate: float
    facecam_norm: float
    samples: int


def baseline_ranges(duration, samples=BASELINE_SAMPLES,
                    seconds=BASELINE_SECONDS):
    """Evenly spaced short sample ranges that cover the source."""
    duration = float(duration or 0.0)
    if duration <= 0:
        return []
    seconds = min(float(seconds), duration)
    count = max(1, min(int(samples), int(duration // seconds) or 1))
    ranges = []
    for index in range(count):
        center = (index + 0.5) * duration / count
        start = min(max(0.0, center - seconds / 2.0), duration - seconds)
        ranges.append((round(start, 3), round(start + seconds, 3)))
    return ranges


def _baseline_path(workdir):
    return Path(workdir) / BASELINE_CACHE


def _load_baseline(path, identity):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("version", -1)) != FEATURE_VERSION:
            return None
        if str(data.get("identity", "")) != str(identity):
            return None
        return VisualBaseline(
            value=float(data["value"]),
            motion_norm=float(data["motion_norm"]),
            cut_rate=float(data["cut_rate"]),
            facecam_norm=float(data["facecam_norm"]),
            samples=int(data["samples"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_baseline(path, identity, baseline, duration):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps({
        "version": FEATURE_VERSION,
        "identity": str(identity),
        "duration": round(float(duration), 3),
        "value": round(baseline.value, 4),
        "motion_norm": round(baseline.motion_norm, 4),
        "cut_rate": round(baseline.cut_rate, 4),
        "facecam_norm": round(baseline.facecam_norm, 4),
        "samples": baseline.samples,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def sample_baseline(source, workdir, duration):
    """Sample up to 12 five-second ranges and cache the visual baseline."""
    if not source or not Path(source).is_file() or float(duration or 0) <= 0:
        return None
    identity = evidence.source_identity(source)
    path = _baseline_path(workdir)
    cached = _load_baseline(path, identity)
    if cached is not None:
        return cached

    ranges = baseline_ranges(duration)
    sections = []
    for index, (start, end) in enumerate(ranges):
        features = extract_features(source, start, end)
        if features is None:
            if index == 0:
                return None
            continue
        sections.append(features)
    if not sections:
        return None

    motion = np.concatenate([item.motion for item in sections])
    faces = np.concatenate([item.face for item in sections])
    cuts = sum(int(item.cut.sum()) for item in sections)
    sampled = sum(max(0.0, item.end - item.start + 1.0 / SAMPLE_FPS)
                  for item in sections)
    mean_motion = float(np.mean(motion)) if len(motion) else 0.0
    cut_rate = (cuts / sampled * 60.0) if sampled > 0 else 0.0
    facecam_norm = float(np.mean(faces)) if len(faces) else 0.0
    motion_norm = evidence.clamp(mean_motion / 0.25)
    value = evidence.clamp(0.5 * motion_norm
                           + 0.3 * evidence.clamp(cut_rate / 30.0)
                           + 0.2 * facecam_norm)
    baseline = VisualBaseline(
        value=value,
        motion_norm=motion_norm,
        cut_rate=cut_rate,
        facecam_norm=facecam_norm,
        samples=len(sections),
    )
    try:
        _save_baseline(path, identity, baseline, duration)
    except OSError:
        pass
    return baseline


# ------------------------------------------------------------ range planning

BOUNDARY_RADIUS = 1.5


def merge_ranges(candidates, duration, pad=2.0):
    """Merge padded candidate ranges into the smallest useful decode set."""
    duration = max(0.0, float(duration or 0.0))
    padded = []
    for item in candidates or []:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (TypeError, KeyError):
            continue
        start = max(0.0, start - float(pad))
        end = min(duration, end + float(pad)) if duration else end + float(pad)
        if end > start:
            padded.append((start, end))
    padded.sort()
    merged = []
    for start, end in padded:
        if merged and start <= merged[-1][1] + float(pad) + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _local_extreme_times(times, values, low, high, mode="min"):
    times = np.asarray(times, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    if len(times) < 3 or len(values) != len(times):
        return []
    found = []
    for index in np.flatnonzero((times >= float(low)) & (times <= float(high))):
        if index <= 0 or index >= len(values) - 1:
            continue
        value = values[index]
        left, right = values[index - 1], values[index + 1]
        if mode == "min" and value <= left and value <= right:
            found.append(float(times[index]))
        elif mode == "max" and value >= left and value >= right:
            found.append(float(times[index]))
    return found


def _audio_rises(timeline, low, high):
    if timeline is None or len(timeline.rms) < 2:
        return []
    rms = np.asarray(timeline.rms, dtype=np.float64)
    first = max(1, int(math.floor(float(low))))
    last = min(len(rms) - 1, int(math.ceil(float(high))))
    rises = []
    for index in range(first, last + 1):
        if rms[index] - rms[index - 1] >= 2.0:
            rises.append(float(index))
    return rises


def _visual_onsets(features, low, high):
    if features is None or len(features.times) == 0:
        return []
    mask = ((features.times >= float(low)) & (features.times <= float(high)))
    indices = np.flatnonzero(mask)
    onsets = [float(features.times[index]) for index in indices
              if features.cut[index]]
    for index in indices:
        if index <= 0 or index >= len(features.motion) - 1:
            continue
        value = float(features.motion[index])
        if value < float(features.motion[index - 1]):
            continue
        if value < float(features.motion[index + 1]):
            continue
        baseline = features.baseline_at(float(features.times[index]))
        if value > max(0.03, baseline * 1.5):
            onsets.append(float(features.times[index]))
    return onsets


def _preferred_boundary(values, target, prefer_after):
    """Pick a boundary close to target, preferring resolution over wind-up."""
    if not values:
        return None
    values = sorted(set(float(value) for value in values))
    after = [value for value in values if value >= target]
    before = [value for value in values if value < target]
    if prefer_after and after:
        return after[0]
    if not prefer_after and before:
        return before[-1]
    return min(values, key=lambda value: abs(value - target))


def refine_media_boundary(features, timeline, start, end, min_len, max_len):
    """Snap a wordless action window to nearby media onsets and resolutions."""
    start = float(start)
    end = float(end)
    onsets = _audio_rises(
        timeline, start - BOUNDARY_RADIUS, start + BOUNDARY_RADIUS)
    onsets += _visual_onsets(
        features, start - BOUNDARY_RADIUS, start + BOUNDARY_RADIUS)
    onsets = [value for value in onsets
              if start - BOUNDARY_RADIUS <= value <= start + BOUNDARY_RADIUS]

    candidate_start = start
    if onsets:
        chosen = min(onsets, key=lambda value: (
            abs(value - start), value > start, value))
        candidate_start = max(start - BOUNDARY_RADIUS,
                              min(start + BOUNDARY_RADIUS, chosen - 0.10))

    resolutions = []
    if timeline is not None and len(timeline.rms):
        resolutions.extend(_local_extreme_times(
            np.arange(len(timeline.rms), dtype=np.float64), timeline.rms,
            end - BOUNDARY_RADIUS, end + BOUNDARY_RADIUS, mode="min"))
        resolutions.append(timeline.trough_near(end, radius=BOUNDARY_RADIUS))
    if features is not None and len(features.times):
        resolutions.extend(_local_extreme_times(
            features.times, features.motion, end - BOUNDARY_RADIUS,
            end + BOUNDARY_RADIUS, mode="min"))
        resolutions.extend([float(features.times[index])
                            for index in np.flatnonzero(
                                (features.times >= end - BOUNDARY_RADIUS)
                                & (features.times <= end + BOUNDARY_RADIUS))
                            if features.cut[index]])
        peak = visual_peak_second(features, start, end)
        if peak is not None and peak <= end + BOUNDARY_RADIUS:
            resolutions.append(min(end + BOUNDARY_RADIUS, float(peak) + 0.75))
    resolutions = [value for value in resolutions
                   if end - BOUNDARY_RADIUS <= value <= end + BOUNDARY_RADIUS]

    candidate_end = end
    if resolutions:
        chosen = _preferred_boundary(resolutions, end, prefer_after=True)
        if chosen is not None:
            candidate_end = max(end - BOUNDARY_RADIUS,
                                min(end + BOUNDARY_RADIUS, chosen + 0.10))

    def valid(low_value, high_value):
        return (high_value - low_value >= float(min_len) - 1e-9
                and high_value - low_value <= float(max_len) + 1e-9
                and high_value > low_value)

    if valid(candidate_start, candidate_end):
        return candidate_start, candidate_end
    if valid(candidate_start, end):
        return candidate_start, end
    if valid(start, candidate_end):
        return start, candidate_end
    return start, end


def visual_peak_second(features, start, end):
    """Second of strongest motion inside a range, or None."""
    if features is None or len(features.motion) == 0:
        return None
    mask = ((features.times >= float(start) - 1e-6)
            & (features.times <= float(end) + 1e-6))
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return None
    return float(features.times[indices[int(np.argmax(features.motion[indices]))]])


# --------------------------------------------------------------- evidence math

def visual_evidence(features, start, end):
    """Score one candidate range from sampled visual features."""
    neutral = {"score": 5.0, "motion_spike": 0.0, "scene_ui": 0.0,
               "facecam": 0.5, "event_arc": 0.0, "boundary_quiet": 0.0,
               "peak_second": None}
    if features is None or len(features.motion) == 0:
        return neutral
    mask = (features.times >= float(start) - 1e-6) \
        & (features.times <= float(end) + 1e-6)
    indices = np.flatnonzero(mask)
    if len(indices) < 2:
        return neutral
    motion = np.asarray(features.motion[indices], dtype=np.float64)
    change = np.asarray(features.change[indices], dtype=np.float64)
    center = np.asarray(features.center_change[indices], dtype=np.float64)
    face = np.asarray(features.face[indices], dtype=bool)
    face_motion = np.asarray(features.face_motion[indices], dtype=np.float64)

    peak_offset = int(np.argmax(motion))
    peak_value = float(motion[peak_offset])
    baseline = max(float(np.median(features.motion)),
                   features.baseline_at(features.times[indices[peak_offset]]))
    motion_spike = evidence.smoothstep((peak_value - baseline) / 0.20)

    scene_value = float(np.percentile(np.maximum(change, center), 90))
    scene_ui = evidence.smoothstep((scene_value - 0.15) / 0.35)

    section_faces = float(np.mean(features.face)) if len(features.face) else 0.0
    if section_faces < 0.15:
        facecam = 0.5
    else:
        presence = float(np.mean(face))
        movement = float(np.percentile(face_motion, 90))
        facecam = 0.5 + 0.5 * evidence.clamp(
            0.6 * presence + 0.4 * movement, 0.0, 1.0)

    count = len(motion)
    rise_end = max(1, int(round(count * 0.30)))
    middle_end = min(count, max(rise_end + 1, int(round(count * 0.70))))
    first = float(np.mean(motion[:rise_end]))
    middle = (float(np.mean(motion[rise_end:middle_end]))
              if middle_end > rise_end else float(motion[-1]))
    rise = evidence.smoothstep((middle - first) / 0.12)
    peak = evidence.smoothstep((peak_value - baseline) / 0.20)
    tail = motion[peak_offset + 1:]
    if tail.size:
        tail_mean = float(np.mean(tail))
        decay = evidence.smoothstep((peak_value - tail_mean) / 0.12)
        reaction = float(np.percentile(
            np.maximum(face_motion[peak_offset + 1:],
                       change[peak_offset + 1:]), 90))
        closure = max(decay, evidence.smoothstep(reaction / 0.25))
    else:
        closure = 0.0
    event_arc = 0.35 * rise + 0.45 * peak + 0.20 * closure

    start_index = int(indices[0])
    end_index = int(indices[-1])
    radius = max(1, int(round(1.5 * SAMPLE_FPS)))
    start_low = max(0, start_index - radius)
    start_high = min(len(features.motion) - 1, start_index + radius)
    end_low = max(0, end_index - radius)
    end_high = min(len(features.motion) - 1, end_index + radius)
    start_local = np.asarray(
        features.motion[start_low:start_high + 1], dtype=np.float64)
    end_local = np.asarray(
        features.motion[end_low:end_high + 1], dtype=np.float64)
    start_quiet = _quietness(float(features.motion[start_index]), start_local)
    end_quiet = _quietness(float(features.motion[end_index]), end_local)
    boundary_quiet = 0.5 * (start_quiet + end_quiet)

    score = 10.0 * (0.25 * motion_spike + 0.20 * scene_ui
                    + 0.20 * facecam + 0.25 * event_arc
                    + 0.10 * boundary_quiet)
    if event_arc < 0.30:
        score = min(score, 6.0)
    return {
        "score": round(evidence.clamp(score, 0.0, 10.0), 2),
        "motion_spike": round(evidence.clamp(motion_spike), 3),
        "scene_ui": round(evidence.clamp(scene_ui), 3),
        "facecam": round(evidence.clamp(facecam), 3),
        "event_arc": round(evidence.clamp(event_arc), 3),
        "boundary_quiet": round(evidence.clamp(boundary_quiet), 3),
        "peak_second": float(features.times[indices[peak_offset]]),
    }


def _quietness(value, local):
    if len(local) == 0:
        return 0.0
    low = float(np.min(local))
    high = float(np.max(local))
    if high - low <= 1e-9:
        return 0.0
    return evidence.smoothstep(1.0 - (value - low) / (high - low))


def combine_visual(audio=None, visual=None):
    """Merge modalities through the shared evidence rule."""
    return evidence.combine_evidence(audio=audio, visual=visual)
