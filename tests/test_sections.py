"""Section planning, download caching, and cleanup."""
import json

import sections


def test_nearby_clips_merge_and_far_clips_split():
    segments = [{"start": 100, "end": 130}, {"start": 150, "end": 180},
                {"start": 300, "end": 330}]
    groups = sections.plan_sections(segments, 600)
    assert [(g["start"], g["end"], g["clips"]) for g in groups] == [
        (90.0, 190.0, [0, 1]),
        (290.0, 340.0, [2])]


def test_section_cap_splits_and_single_long_clip_is_not_truncated():
    segments = [{"start": i * 60, "end": i * 60 + 30} for i in range(12)]
    groups = sections.plan_sections(segments, 1000)
    assert len(groups) > 1
    assert all(g["end"] - g["start"] <= 600 for g in groups)
    assert sorted(index for group in groups for index in group["clips"]) == list(
        range(12))
    long = sections.plan_sections([{"start": 100, "end": 800}], 1000)
    assert long == [{"start": 90.0, "end": 810.0, "clips": [0]}]


def test_plan_clamps_sections_to_source_duration():
    groups = sections.plan_sections(
        [{"start": 0, "end": 30}, {"start": 300, "end": 330}], 320)
    assert groups[0]["start"] == 0.0
    assert groups[-1]["end"] == 320.0


def test_ensure_section_reuses_matching_metadata(tmp_path, monkeypatch):
    calls = []

    def download(url, start, end, out, quality=1080, runner=None):
        calls.append(quality)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"section")
        return out

    monkeypatch.setattr(sections.ingest, "download_section", download)
    first = sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    second = sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    assert first == second
    assert calls == [1080]


def test_ensure_section_invalidates_on_range_or_url_change(tmp_path, monkeypatch):
    calls = []

    def download(url, start, end, out, quality=1080, runner=None):
        calls.append((url, start, end, quality))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"section")
        return out

    monkeypatch.setattr(sections.ingest, "download_section", download)
    sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    sections.ensure_section("https://e/x", tmp_path, 1, 0, 40)
    sections.ensure_section("https://e/y", tmp_path, 1, 0, 40)
    assert [call[1:] for call in calls] == [
        (0.0, 30.0, 1080), (0.0, 40.0, 1080), (0.0, 40.0, 1080)]


def test_ensure_section_falls_back_through_qualities(tmp_path, monkeypatch):
    calls = []

    def download(url, start, end, out, quality=1080, runner=None):
        calls.append(quality)
        if quality is not None:
            raise RuntimeError(f"no quality {quality}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"section")
        return out

    monkeypatch.setattr(sections.ingest, "download_section", download)
    path = sections.ensure_section("https://e/x", tmp_path, 2, 100, 160)
    assert path.is_file()
    assert calls == [1080, 720, None]
    meta = json.loads(
        (tmp_path / "sections" / "section002.json").read_text(encoding="utf-8"))
    assert meta["quality"] is None


def test_ensure_section_raises_after_all_qualities_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sections.ingest, "download_section",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("404")))
    try:
        sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    except RuntimeError as exc:
        assert "Section download failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_ensure_section_stops_retrying_after_a_stall(tmp_path, monkeypatch):
    calls = []

    def download(url, start, end, out, quality=1080, runner=None):
        calls.append(quality)
        raise sections.ingest.SectionDownloadTimeout("ranged download stalled")

    monkeypatch.setattr(sections.ingest, "download_section", download)
    try:
        sections.ensure_section("https://e/x", tmp_path, 1, 0, 30)
    except sections.ingest.SectionDownloadTimeout as exc:
        assert "stalled" in str(exc)
    else:
        raise AssertionError("expected SectionDownloadTimeout")
    assert calls == [1080]


def test_find_full_source_and_cleanup(tmp_path):
    assert sections.find_full_source(tmp_path) is None
    full = tmp_path / "source_full.mp4"
    full.write_bytes(b"full source")
    assert sections.find_full_source(tmp_path) == full
    sections.cleanup_full_source(tmp_path)
    assert not full.exists()


def test_ensure_full_source_reuses_cached_file(tmp_path, monkeypatch):
    full = tmp_path / "source_full.mkv"
    full.write_bytes(b"full source")

    def fail(*args, **kwargs):
        raise AssertionError("should not download a cached full source")

    monkeypatch.setattr(sections.ingest, "download_full", fail)
    assert sections.ensure_full_source("https://e/x", tmp_path) == full


def test_cleanup_only_removes_sections(tmp_path):
    keep = tmp_path / "clips" / "clip.mp4"
    keep.parent.mkdir()
    keep.write_bytes(b"clip")
    section = tmp_path / "sections" / "section001.mp4"
    section.parent.mkdir()
    section.write_bytes(b"section")
    (section.parent / "section001.json").write_text("{}", encoding="utf-8")
    sections.cleanup_sections(tmp_path)
    assert keep.is_file()
    assert not (tmp_path / "sections").exists()
