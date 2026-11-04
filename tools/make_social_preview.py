"""Render the 1280x640 Chopify social preview.

Run from the repository root:

    python tools/make_social_preview.py

The composition uses ``assets/studio-preview.png`` so the card shows the real
Studio instead of a mockup. Regenerate that screenshot from a local Studio
session at 1440x900, then crop the content area (left sidebar removed).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "assets" / "studio-preview.png"
OUTPUT = ROOT / "assets" / "social-preview.png"

W, H = 1280, 640
INK = (238, 240, 233)
MUTED = (157, 169, 172)
DIM = (111, 114, 133)
CYAN = (94, 231, 227)
LIME = (212, 239, 131)
PINK = (255, 46, 99)
MAGENTA = (248, 87, 166)

FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/System/Library/Fonts"),
]


def load_font(names, size):
    for folder in FONT_DIRS:
        for name in names:
            path = folder / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    raise SystemExit(f"Could not find any of these fonts: {names}")


def bold(size):
    return load_font(["segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"], size)


def semibold(size):
    return load_font(["seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"], size)


def regular(size):
    return load_font(["segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"], size)


def italic(size):
    return load_font(["segoeuii.ttf", "DejaVuSans-Oblique.ttf", "Arial Italic.ttf"], size)


def mono(size):
    return load_font(["consola.ttf", "DejaVuSansMono.ttf", "Courier New.ttf"], size)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def vertical_gradient(size, top, bottom):
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        draw.line((0, y, size[0], y), fill=lerp(top, bottom, y / (size[1] - 1)))
    return image


def horizontal_gradient(size, stops):
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for x in range(size[0]):
        t = x / (size[0] - 1)
        if t <= 0.5:
            color = lerp(stops[0], stops[1], t * 2)
        else:
            color = lerp(stops[1], stops[2], (t - 0.5) * 2)
        draw.line((x, 0, x, size[1]), fill=color)
    return image


def glow(base, center, radius, color, alpha):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    mask = Image.new("L", base.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        (center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius),
        fill=alpha,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius * 0.55))
    layer.paste(Image.new("RGBA", base.size, (*color, 255)), (0, 0), mask)
    return Image.alpha_composite(base.convert("RGBA"), layer)


def tracked_text(draw, xy, text, font, fill, tracking=0):
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += font.getlength(character) + tracking
    return x


def gradient_text(base, xy, text, font, stops):
    left, top, right, bottom = font.getbbox(text)
    width = right - left
    height = bottom - top
    mask = Image.new("L", (width + 4, height + 4), 0)
    ImageDraw.Draw(mask).text((2 - left, 2 - top), text, font=font, fill=255)
    gradient = horizontal_gradient((width + 4, height + 4), stops)
    base.paste(gradient, (round(xy[0]) - 2, round(xy[1]) - 2), mask)


def wrap(draw, text, font, max_width):
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def chip(base, xy, text, font, dot=True):
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    pad_x, height = 18, 42
    dot_space = 18 if dot else 0
    width = round(draw.textlength(text, font=font)) + pad_x * 2 + dot_space
    x, y = xy
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=21,
        fill=(255, 255, 255, 16),
        outline=(255, 255, 255, 40),
        width=1,
    )
    text_x = x + pad_x
    if dot:
        draw.ellipse((text_x, y + 18, text_x + 6, y + 24), fill=LIME)
        text_x += dot_space
    draw.text((text_x, y + 9), text, font=font, fill=INK)
    base.alpha_composite(overlay)
    return width

def screenshot_card(base, xy, size):
    x, y = xy
    width, height = size
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x + 10, y + 18, x + width + 10, y + height + 18),
        radius=22,
        fill=(0, 0, 0, 130),
    )
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(26)))

    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), 18, fill=(14, 13, 28, 255),
                           outline=(64, 58, 96, 255), width=2)

    bar = 40
    draw.rounded_rectangle((2, 2, width - 3, bar + 18), 16, fill=(22, 20, 42, 255))
    draw.rectangle((2, bar - 2, width - 3, bar + 18), fill=(22, 20, 42, 255))
    draw.line((2, bar, width - 3, bar), fill=(44, 39, 72, 255), width=1)

    for index, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        cx = 22 + index * 18
        draw.ellipse((cx - 5, 15, cx + 5, 25), fill=color)

    pill = (94, 10, width - 18, 30)
    draw.rounded_rectangle(pill, 10, fill=(9, 8, 18, 255))
    draw.text((pill[0] + 14, 12), "127.0.0.1:8765", font=mono(11), fill=MUTED)

    shot = Image.open(SCREENSHOT).convert("RGB")
    target = (width - 4, height - bar - 4)
    shot = shot.resize(target, Image.LANCZOS)
    mask = rounded_mask(target, 12)
    card.paste(shot, (2, bar + 1), mask)
    base.alpha_composite(card, (x, y))

    # The brand gradient runs across the top edge of the card.
    line = horizontal_gradient((width - 20, 3), (PINK, MAGENTA, CYAN))
    base.paste(line, (x + 10, y), rounded_mask(line.size, 2))


def main():
    if not SCREENSHOT.exists():
        raise SystemExit(
            f"Missing {SCREENSHOT}. Capture Studio at 1440x900 and crop the content area first."
        )

    base = vertical_gradient((W, H), (16, 9, 36), (4, 4, 12)).convert("RGBA")
    base = glow(base, (80, 20), 360, PINK, 46)
    base = glow(base, (1210, 640), 420, CYAN, 34)
    base = glow(base, (700, 300), 300, MAGENTA, 14)

    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    for x in range(0, W, 48):
        grid_draw.line((x, 0, x, H), fill=(255, 255, 255, 6))
    for y in range(0, H, 48):
        grid_draw.line((0, y, W, y), fill=(255, 255, 255, 6))
    base.alpha_composite(grid)

    top = horizontal_gradient((W, 6), (PINK, MAGENTA, CYAN))
    base.paste(top, (0, 0))

    draw = ImageDraw.Draw(base)

    tracked_text(draw, (72, 64), "OPEN SOURCE  ·  100% LOCAL", semibold(15), CYAN, 2.6)

    title_font = bold(124)
    chop_width = title_font.getlength("CHOP")
    draw.text((70, 84), "CHOP", font=title_font, fill=INK)
    gradient_text(base, (70 + chop_width, 84), "IFY", title_font, (PINK, MAGENTA, CYAN))

    draw.text((72, 250), "Turn long videos into", font=semibold(36), fill=INK)
    draw.text((72, 298), "scored, captioned shorts.", font=italic(38), fill=(203, 203, 230))

    sub_font = regular(20)
    lines = wrap(
        draw,
        "A local Studio and CLI with AI clip scoring, auto captions, "
        "face-tracked vertical crops, and SRT exports.",
        sub_font,
        545,
    )
    for index, line in enumerate(lines):
        draw.text((72, 374 + index * 29), line, font=sub_font, fill=MUTED)

    feature_font = semibold(15)
    x = 72
    for label in ("AI clip scoring", "Auto captions", "9:16 · 1:1 · 16:9"):
        x += chip(base, (x, 458), label, feature_font) + 12

    draw.rounded_rectangle((72, 556, 104, 588), 9, fill=LIME)
    draw.text((80, 559), "c/", font=bold(19), fill=(29, 44, 21))
    draw.text((118, 561), "github.com/mehbul/chopify", font=mono(19), fill=(203, 203, 230))
    draw.text((72, 604), "MIT licensed  ·  Python 3.11+  ·  Windows, macOS, Linux", font=regular(13), fill=DIM)

    screenshot_card(base, (672, 82), (536, 478))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(OUTPUT, optimize=True)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
