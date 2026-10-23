"""Portable JSON brand presets. Colors enter ffmpeg only after validation."""
import json
import re
from pathlib import Path

DEFAULTS = {"font": "Arial Black", "highlight": "#FFFF00", "color": "#FFFFFF",
            "font_size": 72, "margin": 95, "logo": None, "logo_width": 180}


def ass_color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Colors must use #RRGGBB.")
    return "&H00" + value[5:7] + value[3:5] + value[1:3] + "&"


def validate_brand(data, base=None):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("Unknown brand fields; see docs/WORKFLOWS.md.")
    brand = {**DEFAULTS, **data}
    if not re.fullmatch(r"[\w .-]{1,80}", str(brand["font"])):
        raise ValueError("Font must be a plain font family name.")
    for key, low, high in [("font_size", 12, 200), ("margin", 0, 700), ("logo_width", 20, 600)]:
        if isinstance(brand[key], bool) or not isinstance(brand[key], int) or not low <= brand[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}.")
    for key in ("highlight", "color"):
        ass_color(brand[key])
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
    return {**brand, "highlight": ass_color(brand["highlight"]), "color": ass_color(brand["color"])}
