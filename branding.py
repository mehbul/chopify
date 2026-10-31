"""Portable JSON brand and caption presets. Colors enter ffmpeg after validation."""
import json
import re
from pathlib import Path

# A caption style is intentionally one flat object. None means "inherit from
# the selected preset"; concrete values here are the fallback for everything
# else. Font sizes and margins are measured at 1080px output height.
CAPTION_DEFAULTS = {
    "font": "Arial Black",
    "font_size": None,
    "color": "#FFFFFF",
    "highlight": "#FFFF00",
    "emphasis_color": "#FFD400",
    "outline_color": "#000000",
    "uppercase": False,
    "words_per_cue": 3,
    "position": "bottom",
    "margin": None,
    "outline": 5,
    "shadow": 2,
    "background": "none",
    "background_color": "#000000",
    "background_opacity": 70,
    "animation": "none",
    "highlight_mode": "word",
    "emoji": False,
    "emphasis": False,
}
CAPTION_FIELDS = frozenset(CAPTION_DEFAULTS)
CAPTION_COLORS = frozenset({
    "color", "highlight", "emphasis_color", "outline_color", "background_color",
})
CAPTION_BOOLEANS = frozenset({"uppercase", "emoji", "emphasis"})
CAPTION_RANGES = {
    "font_size": (12, 200),
    "words_per_cue": (1, 8),
    "margin": (0, 700),
    "outline": (0, 12),
    "shadow": (0, 12),
    "background_opacity": (0, 100),
}
CAPTION_ENUMS = {
    "position": {"bottom", "center", "top"},
    "background": {"none", "box"},
    "animation": {"none", "pop", "bounce", "fade", "typewriter"},
    "highlight_mode": {"word", "karaoke", "none"},
}

# Brand presets keep the original defaults so existing files render exactly the
# same, then gain the new caption fields as opt-in overrides. None means the
# brand file did not set that field, so the named preset supplies it.
DEFAULTS = {
    **CAPTION_DEFAULTS,
    "font_size": 72,
    "margin": 95,
    "logo": None,
    "logo_width": 180,
}
for _key in CAPTION_FIELDS - {"font", "color", "highlight", "font_size", "margin"}:
    DEFAULTS[_key] = None


def ass_color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Colors must use #RRGGBB.")
    return "&H00" + value[5:7] + value[3:5] + value[1:3] + "&"


def _validate_caption_value(key, value):
    if key == "font":
        if not isinstance(value, str) or not re.fullmatch(r"[\w .-]{1,80}", value):
            raise ValueError("Font must be a plain font family name.")
    elif key in CAPTION_COLORS:
        ass_color(value)
    elif key in CAPTION_BOOLEANS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false.")
    elif key in CAPTION_RANGES:
        low, high = CAPTION_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}.")
    elif key in CAPTION_ENUMS:
        if value not in CAPTION_ENUMS[key]:
            choices = ", ".join(sorted(CAPTION_ENUMS[key]))
            raise ValueError(f"{key} must be one of: {choices}.")


def validate_caption(data):
    """Return a clean caption override object; null fields mean inherit."""
    if not isinstance(data, dict):
        raise ValueError("Caption style must be a JSON object.")
    unknown = set(data) - CAPTION_FIELDS
    if unknown:
        raise ValueError(f"Unknown caption fields: {', '.join(sorted(unknown))}.")
    clean = {}
    for key, value in data.items():
        if value is None:
            continue
        _validate_caption_value(key, value)
        clean[key] = value
    return clean


def validate_brand(data, base=None):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("Unknown brand fields; see docs/WORKFLOWS.md.")
    brand = {**DEFAULTS, **data}
    for key in CAPTION_FIELDS:
        if brand[key] is not None:
            _validate_caption_value(key, brand[key])
    if isinstance(brand["logo_width"], bool) or not isinstance(brand["logo_width"], int) \
            or not 20 <= brand["logo_width"] <= 600:
        raise ValueError("logo_width must be an integer between 20 and 600.")
    if brand["logo"]:
        path = Path(brand["logo"]).expanduser()
        if not path.is_absolute():
            path = Path(base or Path.cwd()) / path
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Logo must be an existing PNG, JPEG or WebP image.")
        brand["logo"] = str(path.resolve())
    return brand


def load_brand(path):
    path = Path(path).resolve()
    brand = validate_brand(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)
    return {**brand,
            "highlight": ass_color(brand["highlight"]) if brand["highlight"] else None,
            "color": ass_color(brand["color"]) if brand["color"] else None}
