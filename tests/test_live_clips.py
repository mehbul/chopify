import pytest

import live_clips


def test_plan_window_defaults():
    start, end = live_clips.plan_window(
        100.0, 300, 30, 5, 15, 300, 110.0)
    assert start == 70.0
    assert end == 105.0


def test_plan_window_respects_buffer():
    start, end = live_clips.plan_window(1000.0, 60, 120, 5, 15, 300, 1010.0)
    assert start == 945.0
    assert end == 1005.0


def test_plan_window_min_length():
    start, end = live_clips.plan_window(10.0, 300, 5, 0, 15, 300, 12.0)
    assert start == 0.0
    assert end == 10.0


def test_plan_window_max_length():
    start, end = live_clips.plan_window(500.0, 600, 300, 60, 15, 120, 560.0)
    assert end - start == 120.0


def test_remap_words():
    words = [{"word": "hi", "start": 12.0, "end": 12.5, "score": 1.0},
             {"word": "bye", "start": 13.0, "end": 13.5},
             {"word": "late", "start": 20.0, "end": 20.2}]
    result = live_clips.remap_words(words, 10.0, 14.0)
    assert result == [
        {"word": "hi", "start": 2.0, "end": 2.5, "score": 1.0},
        {"word": "bye", "start": 3.0, "end": 3.5}]


def test_stitch_builds_concat_list(tmp_path):
    paths = [tmp_path / f"segment{i:08d}.ts" for i in range(2, 5)]
    for p in paths:
        p.write_bytes(b"x")
    calls = []
    live_clips.stitch(paths, paths[-1].parent / "window.mp4",
                      runner=lambda cmd, **kw: calls.append(cmd))
    assert calls and "concat" in calls[0]
    listing = (paths[0].parent / "window_list.txt").read_text()
    assert "segment00000002.ts" in listing
    assert "segment00000004.ts" in listing
