"""Generate a short local HLS fixture for live pipeline tests."""
import subprocess
from pathlib import Path


def make_fixture(directory, seconds=8):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "raw.ts"
    # testsrc has a moving pattern; the analyzers sample at 2 fps and need motion.
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=" + str(seconds),
        "-f", "lavfi", "-i", "sine=frequency=440:duration=" + str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", "-f", "mpegts", str(raw)], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(raw), "-c", "copy",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
        str(directory / "index.m3u8")], check=True)
    return directory / "index.m3u8"
