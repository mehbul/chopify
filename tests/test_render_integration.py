"""Actual ffmpeg renders catch filtergraph, codec, path and subtitle regressions."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import render_clips
import branding

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source with spaces.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=12:duration=3", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@pytest.mark.parametrize("layout,preview", [("single", True), ("split", True), ("split", False)])
def test_render_playable_with_subtitles_and_brand(source, tmp_path, layout, preview):
    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=lime:s=80x40", "-frames:v", "1", str(logo)], check=True)
    preset = tmp_path / "brand.json"
    preset.write_text(json.dumps({"font": "Arial", "color": "#FF00FF", "logo": str(logo)}))
    out_dir = tmp_path / "render output"
    out_dir.mkdir()
    words = [{"word": "Hello", "start": 0.5, "end": 1.0},
             {"word": "world!", "start": 1.1, "end": 2.4}]
    out = render_clips.render({"id": "001", "start": 0.5, "end": 2.5, "hook": "same hook"},
        words, source, 640, 360, tmp_path, "9:16" if layout == "split" else "16:9",
        out_dir=out_dir, layout=layout, preview=preview, brand=branding.load_brand(preset))
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json",
                                        str(out)], capture_output=True, text=True, check=True).stdout)["streams"]
    assert {s["codec_name"] for s in streams} >= {"h264", "aac"}
    assert "00:00:00,000" in out.with_suffix(".srt").read_text()
    if layout == "split":
        video = next(s for s in streams if s["codec_type"] == "video")
        expected = render_clips.PREVIEW["9:16"] if preview else render_clips.ASPECTS["9:16"][:2]
        assert (video["width"], video["height"]) == expected
        assert video["pix_fmt"] == "yuv420p"
    if not preview:
        assert out.with_suffix(".png").is_file()
        assert out.with_suffix(".meta.json").is_file()


def test_stacked_layout_graph_and_seam_caption():
    import render_clips
    box, margin = render_clips.stacked_graph(
        1920, 1080, 1080, 1920, 0.34, (0.0, 0.62, 0.26, 0.30))
    assert "vstack=inputs=2" in box
    assert "split=2" in box
    assert "crop=498:324" in box
    assert "crop=920:1080" in box
    assert "scale=1080:652" in box
    assert margin == 1256


def test_stacked_layout_renders_real_video(source, tmp_path):
    out = render_clips.render(
        {"start": 0, "end": 2, "hook": "stacked"},
        [{"word": "Hi", "start": 0.2, "end": 0.8}],
        source, 640, 360, tmp_path, "9:16", out_dir=tmp_path / "stacked",
        layout="stacked", facecam_box=[0.0, 0.6, 0.3, 0.35], preview=True)
    meta = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
        capture_output=True, text=True, check=True).stdout)
    v = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == render_clips.PREVIEW["9:16"]


def test_clamp_facecam_box():
    import render_clips
    assert render_clips.clamp_facecam_box([0.1, 0.6, 0.2, 0.3]) == (0.1, 0.6, 0.2, 0.3)
    assert render_clips.clamp_facecam_box([0.95, 0.95, 0.5, 0.5]) == (0.95, 0.95, 0.05, 0.05)
    assert render_clips.clamp_facecam_box([-2, 2, 0.01, 9]) == (0.0, 0.95, 0.05, 0.05)


def test_resolve_facecam_box_manual_and_fallback(tmp_path):
    import render_clips
    box, warning = render_clips.resolve_facecam_box(
        None, 1920, 1080, tmp_path, [0.05, 0.65, 0.25, 0.3])
    assert box == (0.05, 0.65, 0.25, 0.3) and warning is None
    box, warning = render_clips.resolve_facecam_box(
        None, 1920, 1080, tmp_path, None, detector=object())
    assert box == (0.0, 0.62, 0.26, 0.30) and warning


def test_contain_fit_keeps_whole_frame_with_bars(tmp_path):
    source = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=white:s=640x360:rate=12:duration=2", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    out = render_clips.render({"start": 0, "end": 1, "hook": "contain"}, [],
                              source, 640, 360, tmp_path, "9:16",
                              out_dir=tmp_path / "contain", preview=True, fit="contain")
    video = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json",
                                       str(out)], capture_output=True, text=True, check=True).stdout)
    stream = next(s for s in video["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == render_clips.PREVIEW["9:16"]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.3", "-i", str(out),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    width, height = render_clips.PREVIEW["9:16"]
    top = raw[0:3]
    center = raw[(height // 2) * width * 3:(height // 2) * width * 3 + 3]
    assert max(top) < 20                 # letterbox bar
    assert min(center) > 180             # whole source frame still visible


def test_brand_colors_appear_in_ass(tmp_path):
    output = tmp_path / "cap.ass"
    preset = tmp_path / "brand.json"
    preset.write_text(json.dumps({"font": "Georgia", "color": "#112233", "highlight": "#ABCDEF"}))
    render_clips.build_ass([{"word": "hello", "start": 0, "end": 1}], 0, 1, output,
                          1920, 1080, 72, 95, brand=branding.load_brand(preset))
    ass = output.read_text()
    assert "Georgia" in ass
    assert "&H00332211" in ass
    assert "&H00EFCDAB" in ass


def test_tightened_srt_tracks_actual_shortened_video(source, tmp_path):
    words = [{"word": "Hello", "start": 0.1, "end": 0.5},
             {"word": "um", "start": 0.6, "end": 1.0},
             {"word": "world.", "start": 2.0, "end": 2.5}]
    out = render_clips.render({"start": 0, "end": 3, "hook": "tightened"}, words,
        source, 640, 360, tmp_path, "16:9", out_dir=tmp_path / "tight", do_tighten=True, preview=True)
    import tighten
    keeps = tighten.plan_cuts(words, 0, 3)
    _, expected = tighten.remap_words(words, 0, keeps)
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(out)], capture_output=True, text=True, check=True).stdout)
    assert duration == pytest.approx(expected, abs=0.25)
    srt = out.with_suffix(".srt").read_text()
    assert "world." in srt
    assert " um " not in srt


def test_boxed_pop_caption_render_and_matching_srt_groups(source, tmp_path):
    words = [{"word": "Hello", "start": 0.5, "end": 0.8},
             {"word": "skip", "start": 0.9, "end": 1.0, "hidden": True},
             {"word": "world!", "start": 1.1, "end": 1.4},
             {"word": "again", "start": 1.5, "end": 2.4}]
    out = render_clips.render(
        {"id": "001", "start": 0.5, "end": 2.5, "hook": "boxed pop"},
        words, source, 640, 360, tmp_path, "16:9", out_dir=tmp_path / "render",
        preview=True, caption={"background": "box", "background_color": "#112233",
                               "background_opacity": 70, "animation": "pop",
                               "words_per_cue": 2, "uppercase": True})
    streams = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
        capture_output=True, text=True, check=True).stdout)["streams"]
    assert {s["codec_name"] for s in streams} >= {"h264", "aac"}
    srt = out.with_suffix(".srt").read_text(encoding="utf-8")
    assert srt.count("-->") == 2
    assert "skip" not in srt
    assert "Hello world!" in srt


def test_cli_render_preserves_project_settings_and_copies_exports(source, tmp_path):
    import os
    import sys
    import projects
    path = tmp_path / "saved project"
    projects.init_project(path, str(source), {"aspect": "9:16", "layout": "split"})
    projects.atomic_json(path / "transcript.json", {"video": str(source), "duration": 3,
        "words": [{"word": "Hello.", "start": 0.5, "end": 1.5}]})
    projects.atomic_json(path / "segments.json", [{"start": 0.5, "end": 2, "hook": "hello", "overall": 6}])
    export = tmp_path / "export folder"
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "chopify.py"),
        "--project", str(path), "--action", "render", "--preview", "--no-preflight", "--out", str(export)],
        capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr + result.stdout
    assert projects.read_project(path)["options"]["layout"] == "split"
    assert len(list(export.glob("*.mp4"))) == len(list(export.glob("*.srt"))) == 1


def test_render_from_section_offset_reads_local_video_not_source_start(tmp_path):
    source = tmp_path / "sections.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=green:s=320x180:d=3",
        "-f", "lavfi", "-i", "color=c=red:s=320x180:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        check=True)
    out = render_clips.render(
        {"id": "001", "start": 3.5, "end": 5.0, "hook": "offset section"},
        [{"word": "hello", "start": 3.5, "end": 4.0}], source, 320, 180,
        tmp_path, "16:9", out_dir=tmp_path / "render", preview=True,
        source_offset=3.0)
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "0.2", "-i", str(out),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    red, green = frame[0], frame[1]
    assert green > 100 and red < 80
    assert "00:00:00,000" in out.with_suffix(".srt").read_text()
