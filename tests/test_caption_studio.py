"""Caption Studio: validation, style resolution, ASS/SRT output, CLI and projects."""
import argparse
import json
import subprocess
from pathlib import Path

import pytest

import branding
import captions
import projects
import render_clips


def word(text, start, dur=0.4, **flags):
    return {"word": text, "start": round(start, 3), "end": round(start + dur, 3), **flags}


WORDS = [word("This", 0.0), word("costs", 0.5), word("100", 1.0),
         word("dollars", 1.5), word("today", 2.0)]


def style_fields(content):
    line = next(l for l in content.splitlines() if l.startswith("Style: "))
    return line.split(",")


def dialogue_texts(content):
    lines = [l for l in content.splitlines() if l.startswith("Dialogue: ")]
    return [l.split(",", 9)[9] for l in lines]


def make_ass(tmp_path, words=None, **kwargs):
    out = tmp_path / "caption.ass"
    kwargs.setdefault("font_size", 72)
    kwargs.setdefault("margin_v", 95)
    render_clips.build_ass(words or WORDS, 0.0, 10.0, out, 1920, 1080, **kwargs)
    return out.read_text(encoding="utf-8")


# ---------------------------------------------------------------- validation

class TestCaptionValidation:
    def test_accepts_every_field(self):
        clean = branding.validate_caption({
            "font": "Arial Black", "font_size": 54, "color": "#112233",
            "highlight": "#445566", "emphasis_color": "#778899",
            "outline_color": "#000000", "uppercase": True, "words_per_cue": 2,
            "position": "top", "margin": 40, "outline": 3, "shadow": 1,
            "background": "box", "background_color": "#101010",
            "background_opacity": 55, "animation": "pop",
            "highlight_mode": "karaoke", "emoji": True, "emphasis": True,
        })
        assert clean["words_per_cue"] == 2
        assert clean["background"] == "box"

    def test_null_means_inherit(self):
        assert branding.validate_caption({"font": None, "emoji": None}) == {}

    def test_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="Unknown caption fields"):
            branding.validate_caption({"sparkles": True})

    def test_rejects_bad_color(self):
        with pytest.raises(ValueError, match="RRGGBB"):
            branding.validate_caption({"color": "red"})

    def test_rejects_int_for_boolean(self):
        with pytest.raises(ValueError, match="true or false"):
            branding.validate_caption({"emoji": 1})

    def test_rejects_boolean_for_int(self):
        with pytest.raises(ValueError, match="integer"):
            branding.validate_caption({"words_per_cue": True})

    @pytest.mark.parametrize("payload", [
        {"words_per_cue": 0}, {"words_per_cue": 9}, {"font_size": 11},
        {"font_size": 201}, {"background_opacity": 101}, {"outline": 13},
        {"position": "left"}, {"animation": "spin"}, {"highlight_mode": "all"},
        {"font": "Comic Sans; rm -rf"},
    ])
    def test_rejects_out_of_range_and_unknown_values(self, payload):
        with pytest.raises(ValueError):
            branding.validate_caption(payload)

    def test_brand_keeps_legacy_fields_and_accepts_caption_fields(self, tmp_path):
        brand = branding.validate_brand({
            "color": "#112233", "highlight": "#FFFF00", "font_size": 80,
            "words_per_cue": 4, "background": "box",
        }, tmp_path)
        assert brand["logo"] is None
        assert brand["words_per_cue"] == 4
        with pytest.raises(ValueError, match="Unknown brand fields"):
            branding.validate_brand({"nope": 1}, tmp_path)

    def test_brand_leaves_unset_new_caption_fields_to_the_preset(self, tmp_path):
        brand = branding.validate_brand({"color": "#112233"}, tmp_path)
        assert brand["animation"] is None
        assert brand["emphasis"] is None
        style = render_clips.resolve_caption("bold", brand)
        assert style["animation"] == "pop"          # preset survives
        assert style["words_per_cue"] == 2
        assert style["color"] == "#112233"          # brand still wins

    def test_brand_null_color_inherits_the_preset(self, tmp_path):
        preset = tmp_path / "brand.json"
        preset.write_text(json.dumps({
            "color": None, "highlight": None, "animation": "fade"}))
        brand = branding.load_brand(preset)
        assert brand["color"] is None and brand["highlight"] is None
        style = render_clips.resolve_caption("bold", brand)
        assert style["color"] == "#FFFFFF"          # bold preset survives
        assert style["highlight"] == "#FF7A00"
        assert style["animation"] == "fade"         # explicit null does not block


# ------------------------------------------------------------ style layering

class TestStyleResolution:
    def test_preset_is_a_complete_style(self):
        style = render_clips.resolve_caption("bold")
        assert style["font"] == "Impact"
        assert style["animation"] == "pop"
        assert style["words_per_cue"] == 2

    def test_precedence_preset_then_brand_then_project(self):
        brand = {"highlight": "#112233", "font": "Georgia"}
        caption = {"highlight": "#445566"}
        style = render_clips.resolve_caption("clean", brand, caption)
        assert style["highlight"] == "#445566"      # project wins
        assert style["font"] == "Georgia"           # brand wins over preset
        assert style["words_per_cue"] == 3          # preset survives

    def test_brand_may_supply_ass_colors(self, tmp_path):
        brand = {"highlight": "&H0034AB12&", "color": "&H00332211&"}
        content = make_ass(tmp_path, brand=brand)
        assert "&H0034AB12&" in content
        assert "&H00332211&" in content

    def test_every_preset_resolves(self):
        for name in render_clips.CAPTION_STYLES:
            style = render_clips.resolve_caption(name)
            for key in branding.CAPTION_FIELDS - {"font_size", "margin"}:
                assert style.get(key) is not None, (name, key)


# ------------------------------------------------------------------- layout

class TestAssLayout:
    def test_position_maps_to_alignment(self, tmp_path):
        for position, alignment in (("bottom", "2"), ("center", "5"), ("top", "8")):
            fields = style_fields(make_ass(tmp_path, caption={"position": position}))
            assert fields[18] == alignment

    def test_outline_and_shadow_scale(self, tmp_path):
        content = make_ass(tmp_path, caption={
            "outline": 4, "shadow": 6, "font_size": 54, "margin": 120})
        fields = style_fields(content)
        assert fields[2] == "54"
        assert fields[16] == "4"
        assert fields[17] == "6"
        assert fields[21] == "120"

    def test_background_box_uses_border_style_and_alpha(self, tmp_path):
        content = make_ass(tmp_path, caption={
            "background": "box", "background_color": "#112233",
            "background_opacity": 80, "outline": 6})
        fields = style_fields(content)
        assert fields[15] == "3"                    # BorderStyle
        assert fields[5] == "&H33332211&"           # box color plus alpha
        assert fields[6] == "&H33332211&"           # back colour matches
        assert fields[16] == "6"                    # padding

    def test_none_background_keeps_outline(self, tmp_path):
        fields = style_fields(make_ass(tmp_path, caption={"background": "none"}))
        assert fields[15] == "1"
        assert fields[5] == "&H00000000&"

    def test_uppercase_changes_display_not_source(self, tmp_path):
        content = make_ass(tmp_path, caption={"uppercase": True})
        assert "THIS" in content
        assert "This" not in content
        assert WORDS[0]["word"] == "This"

    def test_words_per_cue_groups_events(self, tmp_path):
        texts = dialogue_texts(make_ass(tmp_path, caption={"words_per_cue": 2}))
        assert "This" in texts[0] and "costs" in texts[0]
        assert "100" not in texts[0]
        assert "dollars" in texts[2]

    def test_hidden_words_are_skipped(self, tmp_path):
        words = [word("keep", 0.0), word("skip", 0.5, hidden=True), word("also", 1.0)]
        content = make_ass(tmp_path, words=words, caption={"words_per_cue": 2})
        assert "skip" not in content
        assert "keep" in content and "also" in content


# ------------------------------------------------------------------- motion

class TestAssMotion:
    def test_pop_bounce_and_fade_tags(self, tmp_path):
        assert r"\fscx135" in make_ass(tmp_path, caption={"animation": "pop"})
        assert r"\fscx88" in make_ass(tmp_path, caption={"animation": "bounce"})
        assert r"\fad(150,0)" in make_ass(tmp_path, caption={"animation": "fade"})

    def test_typewriter_reveals_one_word_at_a_time(self, tmp_path):
        texts = dialogue_texts(make_ass(tmp_path, caption={
            "animation": "typewriter", "words_per_cue": 3}))
        assert texts[0].count("}") == 1 and "This" in texts[0]
        assert "costs" in texts[1] and "100" not in texts[1]
        assert "100" in texts[2]

    def test_word_highlight_only_marks_active_word(self, tmp_path):
        texts = dialogue_texts(make_ass(tmp_path, caption={
            "highlight_mode": "word", "words_per_cue": 3}))
        assert texts[0].count("&H0000FFFF&") == 1

    def test_karaoke_marks_active_and_earlier_words(self, tmp_path):
        texts = dialogue_texts(make_ass(tmp_path, caption={
            "highlight_mode": "karaoke", "words_per_cue": 3}))
        assert texts[1].count("&H0000FFFF&") == 2

    def test_none_highlight_leaves_every_word_plain(self, tmp_path):
        texts = dialogue_texts(make_ass(tmp_path, caption={
            "highlight_mode": "none", "words_per_cue": 3}))
        assert "&H0000FFFF&" not in texts[0]

    def test_emphasis_colors_inactive_power_word(self, tmp_path):
        texts = dialogue_texts(make_ass(tmp_path, caption={
            "emphasis": True, "words_per_cue": 3}))
        assert "&H0000D4FF&" in texts[0]            # 100 is emphasised
        assert texts[2].count("&H0000FFFF&") == 1   # active word stays highlight

    def test_forced_emphasis_flag_survives(self, tmp_path):
        words = [word("plain", 0.0), word("pebble", 0.5, emphasis=True),
                 word("word", 1.0)]
        texts = dialogue_texts(make_ass(tmp_path, words=words,
                                        caption={"emphasis": False}))
        assert "&H0000D4FF&" in texts[0]

    def test_emoji_attaches_once_per_cue(self, tmp_path):
        words = [word("money", 0.0), word("money", 0.5), word("money", 1.0)]
        texts = dialogue_texts(make_ass(tmp_path, words=words,
                                        caption={"emoji": True}))
        assert texts[0].count("💰") == 1
        assert r"\fnSegoe UI Emoji" in texts[0]
        assert r"{\fnArial Black}" in texts[0]       # restores the caption font


# ---------------------------------------------------------------------- SRT

class TestSrt:
    def test_group_size_controls_cues(self, tmp_path):
        target = tmp_path / "out.srt"
        captions.write_srt(WORDS, 0.0, 10.0, target, group_size=2)
        assert target.read_text(encoding="utf-8").count("-->") == 3

    def test_hidden_words_are_skipped(self, tmp_path):
        words = [word("keep", 0.0), word("skip", 0.5, hidden=True),
                 word("also", 1.0)]
        target = tmp_path / "out.srt"
        captions.write_srt(words, 0.0, 10.0, target, group_size=3)
        text = target.read_text(encoding="utf-8")
        assert "skip" not in text
        assert "keep also" in text

    def test_invalid_group_size_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            captions.write_srt(WORDS, 0.0, 10.0, tmp_path / "out.srt", group_size=0)


class TestTranscriptFlags:
    def test_hidden_and_emphasis_survive(self):
        data = {"duration": 5.0, "words": [], "segments": []}
        updated = captions.update_transcript(data, [
            word("keep", 0.0), word("skip", 0.5, hidden=True),
            word("loud", 1.0, emphasis=True)])
        assert updated["words"][1]["hidden"] is True
        assert updated["words"][2]["emphasis"] is True
        assert "hidden" not in updated["words"][0]

    def test_non_boolean_flag_rejected(self):
        data = {"duration": 5.0, "words": [], "segments": []}
        with pytest.raises(ValueError, match="true or false"):
            captions.update_transcript(data, [word("keep", 0.0, hidden="yes")])


# ------------------------------------------------------------------ project

class TestProjectCaption:
    def test_defaults_include_no_caption(self):
        assert projects.validate_options({})["caption"] is None

    def test_roundtrip_preserves_caption(self):
        caption = {"words_per_cue": 2, "animation": "pop", "background": "box",
                   "background_color": "#101010", "background_opacity": 50}
        assert projects.validate_options({"caption": caption})["caption"] == caption

    def test_invalid_caption_rejected(self):
        with pytest.raises(ValueError, match="words_per_cue"):
            projects.validate_options({"caption": {"words_per_cue": 99}})

    def test_caption_reaches_renderer_and_cache(self, tmp_path, monkeypatch):
        source = tmp_path / "video.mp4"
        source.write_bytes(b"video")
        state = projects.create_project(
            tmp_path / "projects", str(source),
            {"caption": {"words_per_cue": 2}})
        path = projects.project_path(tmp_path / "projects", state["id"])
        projects.atomic_json(path / "transcript.json",
                             {"video": str(source), "duration": 20,
                              "words": [{"word": "Hello.", "start": 0, "end": 1}]})
        projects.atomic_json(path / "segments.json",
                             [{"start": 0, "end": 5, "hook": "same", "overall": 7}])
        monkeypatch.setattr(projects.render_clips, "ffprobe_dims", lambda p: (640, 360))
        seen = []

        def fake_render(segment, *args, **kwargs):
            seen.append(kwargs.get("caption"))
            out = kwargs["out_dir"] / f"{segment['id']}.mp4"
            for ext in (".mp4", ".srt", ".png", ".meta.json"):
                out.with_suffix(ext).write_text("rendered")
            return out

        monkeypatch.setattr(projects.render_clips, "render", fake_render)
        projects.run_project(path, action="render")
        assert seen == [{"words_per_cue": 2}]
        assert len(projects.read_project(path)["renders"]) == 1

        saved = projects.read_project(path)
        saved["options"]["caption"] = {"words_per_cue": 3}
        projects.save_project(path, saved)
        projects.run_project(path, action="render")
        assert seen[-1] == {"words_per_cue": 3}
        assert len(projects.read_project(path)["renders"]) == 2


# ---------------------------------------------------------------------- CLI

def caption_parser():
    ap = argparse.ArgumentParser()
    render_clips.add_caption_arguments(ap)
    return ap


class TestCaptionCli:
    def test_no_flags_means_no_override(self):
        args = caption_parser().parse_args([])
        assert render_clips.caption_from_args(args) is None

    def test_flags_build_validated_override(self):
        args = caption_parser().parse_args([
            "--words-per-cue", "2", "--uppercase", "--caption-position", "top",
            "--caption-animation", "pop", "--caption-background", "box",
            "--caption-outline", "4", "--caption-shadow", "1", "--emoji",
            "--emphasis", "--caption-font", "Impact", "--caption-color", "#112233",
            "--caption-highlight", "#445566", "--caption-emphasis-color", "#778899"])
        caption = render_clips.caption_from_args(args)
        assert caption["words_per_cue"] == 2
        assert caption["uppercase"] is True
        assert caption["position"] == "top"
        assert caption["animation"] == "pop"
        assert caption["background"] == "box"
        assert caption["font"] == "Impact"
        assert caption["highlight"] == "#445566"

    def test_negative_boolean_flags(self):
        args = caption_parser().parse_args(["--no-uppercase", "--no-emoji",
                                            "--no-emphasis"])
        caption = render_clips.caption_from_args(args)
        assert caption == {"uppercase": False, "emoji": False, "emphasis": False}

    def test_caption_json_is_a_base_for_flags(self, tmp_path):
        path = tmp_path / "caption.json"
        path.write_text(json.dumps({"words_per_cue": 5, "font": "Georgia"}),
                        encoding="utf-8")
        args = caption_parser().parse_args(["--caption-json", str(path),
                                            "--words-per-cue", "2"])
        assert render_clips.caption_from_args(args) == {
            "words_per_cue": 2, "font": "Georgia"}

    def test_bad_caption_json_fails_cleanly(self, tmp_path):
        path = tmp_path / "caption.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid"):
            render_clips.caption_from_args(
                caption_parser().parse_args(["--caption-json", str(path)]))

    def test_missing_caption_json_fails_cleanly(self, tmp_path):
        with pytest.raises(ValueError, match="Could not read"):
            render_clips.caption_from_args(
                caption_parser().parse_args(["--caption-json", str(tmp_path / "nope.json")]))

    def test_cli_has_caption_flags(self):
        text = Path(projects.__file__).with_name("chopify.py").read_text(encoding="utf-8")
        assert "add_caption_arguments" in text
        assert "caption_from_args" in text
