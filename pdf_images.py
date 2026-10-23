"""Ordered image-to-PDF documents with shared preview/export processing."""
import io
import math
from pathlib import Path
import re
import time
import uuid
import warnings

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from reportlab.lib import pagesizes
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from projects import atomic_json, read_json

MAX_UPLOAD = 20 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_PAGES = 100
FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}
PAPERS = {"A4": pagesizes.A4, "A5": pagesizes.A5,
          "Letter": pagesizes.letter, "Legal": pagesizes.legal}
IMAGE_DEFAULTS = {"filter": "original", "brightness": 1.0, "contrast": 1.0,
                  "saturation": 1.0, "sharpness": 1.0, "threshold": 160,
                  "rotation": 0, "flip_h": False, "flip_v": False,
                  "autocontrast": False, "invert": False, "crop": [0, 0, 100, 100]}
PDF_DEFAULTS = {"paper": "A4", "orientation": "auto", "fit": "contain", "margin": 10,
                "background": "#FFFFFF", "dpi": 150, "quality": 90,
                "page_numbers": False, "title": "Image document", "filename": "images"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{12}", value):
        raise ValueError("Invalid image or document ID.")
    return value


def asset_path(root, asset_id):
    return Path(root) / "pdf" / "assets" / (identifier(asset_id) + ".png")


def export_path(root, doc_id, extension="pdf"):
    if extension not in {"pdf", "json"}:
        raise ValueError("Unknown document format.")
    return Path(root) / "pdf" / "exports" / (identifier(doc_id) + "." + extension)


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return value


def validate_image_options(options):
    if not isinstance(options, dict) or set(options) - set(IMAGE_DEFAULTS):
        raise ValueError("Unknown image adjustment.")
    result = {**IMAGE_DEFAULTS, **options}
    if result["filter"] not in {"original", "grayscale", "bw", "scan", "sepia"}:
        raise ValueError("Choose original, grayscale, black & white, scan or sepia.")
    for key in ("brightness", "contrast", "saturation", "sharpness"):
        number(result[key], 0, 3, key)
    number(result["threshold"], 0, 255, "Threshold")
    if isinstance(result["rotation"], bool) or result["rotation"] not in (0, 90, 180, 270):
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.")
    for key in ("flip_h", "flip_v", "autocontrast", "invert"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be true or false.")
    crop = result["crop"]
    if not isinstance(crop, list) or len(crop) != 4:
        raise ValueError("Crop needs left, top, right and bottom percentages.")
    for value in crop:
        number(value, 0, 100, "Crop")
    if crop[2] - crop[0] < 1 or crop[3] - crop[1] < 1:
        raise ValueError("Crop must retain at least 1% of the width and height.")
    return result


def validate_pdf_options(options):
    if not isinstance(options, dict) or set(options) - set(PDF_DEFAULTS):
        raise ValueError("Unknown PDF setting.")
    result = {**PDF_DEFAULTS, **options}
    if result["paper"] not in {*PAPERS, "Image"}:
        raise ValueError("Unknown paper size.")
    if result["orientation"] not in {"auto", "portrait", "landscape"} or result["fit"] not in {"contain", "cover", "stretch"}:
        raise ValueError("Invalid page orientation or fit.")
    number(result["margin"], 0, 40, "Margin in mm")
    number(result["dpi"], 72, 300, "DPI")
    number(result["quality"], 40, 100, "JPEG quality")
    if not isinstance(result["page_numbers"], bool):
        raise ValueError("Page numbers must be true or false.")
    if not isinstance(result["background"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result["background"]):
        raise ValueError("Background must be a #RRGGBB color.")
    for key in ("title", "filename"):
        if not isinstance(result[key], str) or not result[key].strip() or len(result[key]) > 150:
            raise ValueError(f"{key} must contain 1 to 150 characters.")
    return result


def upload(root, filename, data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD:
        raise ValueError("Each image must be 20 MB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in FORMATS or source.width * source.height > MAX_PIXELS:
                    raise ValueError("Use JPEG, PNG, WebP, BMP or TIFF, up to 24 megapixels.")
                source.seek(0)
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("This image could not be decoded. Use a supported, undamaged image.") from exc
    asset_id = uuid.uuid4().hex[:12]
    path = asset_path(root, asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Re-encoding strips EXIF, GPS and embedded text metadata.
    clean = Image.new("RGBA", image.size)
    clean.paste(image)
    clean.save(path, format="PNG")
    thumb = clean.copy()
    thumb.thumbnail((220, 220))
    thumb.save(path.with_suffix(".thumb.png"))
    item = {"id": asset_id, "name": str(filename).replace("\\", "/").split("/")[-1][:150] or "image",
            "width": clean.width, "height": clean.height, "created": time.time()}
    atomic_json(path.with_suffix(".json"), item)
    return item


def assets(root):
    result = []
    for path in (Path(root) / "pdf" / "assets").glob("*.json"):
        try:
            result.append(read_json(path))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["created"])


def process_image(source, settings):
    settings = validate_image_options(settings)
    rgba = ImageOps.exif_transpose(source).convert("RGBA")
    image = Image.new("RGB", rgba.size, "white")
    image.paste(rgba, mask=rgba.getchannel("A"))
    left, top, right, bottom = settings["crop"]
    x0, y0 = int(image.width * left / 100), int(image.height * top / 100)
    x1, y1 = max(x0 + 1, round(image.width * right / 100)), max(y0 + 1, round(image.height * bottom / 100))
    image = image.crop((x0, y0, x1, y1))
    if settings["flip_h"]:
        image = ImageOps.mirror(image)
    if settings["flip_v"]:
        image = ImageOps.flip(image)
    if settings["rotation"]:
        image = image.rotate(-settings["rotation"], expand=True)
    if settings["autocontrast"]:
        image = ImageOps.autocontrast(image)
    for key, enhancer in [("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast),
                          ("saturation", ImageEnhance.Color), ("sharpness", ImageEnhance.Sharpness)]:
        if settings[key] != 1:
            image = enhancer(image).enhance(settings[key])
    mode = settings["filter"]
    if mode != "original":
        gray = ImageOps.grayscale(image)
        if mode == "sepia":
            image = ImageOps.colorize(gray, "#241408", "#FFF0CF")
        else:
            if mode == "scan":
                gray = ImageOps.autocontrast(gray).filter(ImageFilter.UnsharpMask(radius=1.5, percent=160))
            if mode in {"bw", "scan"}:
                gray = gray.point(lambda value: 255 if value >= settings["threshold"] else 0)
            image = gray.convert("RGB")
    if settings["invert"]:
        image = ImageOps.invert(image)
    return image


def compose_page(source, settings, options, index, total, preview=False):
    options = validate_pdf_options(options)
    image = process_image(source, settings)
    margin_pt = options["margin"] * 72 / 25.4
    footer_pt = 18 if options["page_numbers"] else 0
    if options["paper"] == "Image":
        size = (image.width * 72 / options["dpi"] + margin_pt * 2,
                image.height * 72 / options["dpi"] + margin_pt * 2 + footer_pt)
    else:
        size = PAPERS[options["paper"]]
    landscape = options["orientation"] == "landscape" or (
        options["orientation"] == "auto" and image.width > image.height)
    size = (max(size), min(size)) if landscape else (min(size), max(size))
    dpi = min(options["dpi"], 96) if preview else options["dpi"]
    # Image-sized pages can be very large; cap rasterization independently of points.
    dpi = min(dpi, 3508 * 72 / max(size))
    width, height = [max(1, round(points * dpi / 72)) for points in size]
    margin = round(margin_pt * dpi / 72)
    footer = round(footer_pt * dpi / 72)
    box = (width - margin * 2, height - margin * 2 - footer)
    if min(box) < 2:
        raise ValueError("Margins leave no room for the image. Reduce the margin.")
    if options["fit"] == "cover":
        image = ImageOps.fit(image, box, method=Image.Resampling.LANCZOS)
    elif options["fit"] == "stretch":
        image = image.resize(box, Image.Resampling.LANCZOS)
    else:
        image = ImageOps.contain(image, box, method=Image.Resampling.LANCZOS)
    page = Image.new("RGB", (width, height), options["background"])
    page.paste(image, (margin + (box[0] - image.width) // 2, margin + (box[1] - image.height) // 2))
    if options["page_numbers"]:
        draw = ImageDraw.Draw(page)
        label = f"{index} / {total}"
        rgb = tuple(int(options["background"][i:i+2], 16) for i in (1,3,5))
        color = "black" if sum(rgb) > 380 else "white"
        draw.text((width / 2, height - margin - footer / 2), label, fill=color,
                  anchor="mm", font_size=max(10, round(9 * dpi / 72)))
    return page, size


def preview(root, page, options, index=1, total=1):
    path = asset_path(root, page["asset"])
    with Image.open(path) as source:
        rendered, _ = compose_page(source, page.get("settings", {}), options, index, total, preview=True)
    buffer = io.BytesIO()
    rendered.save(buffer, "JPEG", quality=round(validate_pdf_options(options)["quality"]), subsampling=0)
    return buffer.getvalue()


def export(root, pages, options):
    options = validate_pdf_options(options)
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError("Add between 1 and 100 images to the PDF.")
    clean_pages = []
    for page in pages:
        if not isinstance(page, dict) or set(page) - {"asset", "settings"}:
            raise ValueError("Invalid PDF page.")
        path = asset_path(root, page.get("asset"))
        if not path.is_file():
            raise ValueError("An image is missing. Upload it again.")
        clean_pages.append({"asset": page["asset"], "settings": validate_image_options(page.get("settings", {}))})
    doc_id = uuid.uuid4().hex[:12]
    output = export_path(root, doc_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".part")
    try:
        document_canvas = canvas.Canvas(str(temp), pageCompression=1)
        document_canvas.setTitle(options["title"])
        document_canvas.setAuthor("Chopify Studio")
        for index, page in enumerate(clean_pages, 1):
            with Image.open(asset_path(root, page["asset"])) as source:
                rendered, size = compose_page(source, page["settings"], options, index, len(clean_pages))
            buffer = io.BytesIO()
            rendered.save(buffer, "JPEG", quality=round(options["quality"]), subsampling=0)
            buffer.seek(0)
            document_canvas.setPageSize(size)
            document_canvas.drawImage(ImageReader(buffer), 0, 0, width=size[0], height=size[1])
            document_canvas.showPage()
            rendered.close()
        document_canvas.save()
        temp.replace(output)
        filename = re.sub(r"[^\w -]", "", options["filename"]).strip()[:80] or "images"
        result = {"id": doc_id, "filename": filename + ".pdf", "title": options["title"],
                  "count": len(clean_pages), "created": time.time(), "size": output.stat().st_size,
                  "pages": clean_pages, "options": options}
        atomic_json(export_path(root, doc_id, "json"), result)
        return result
    except Exception:
        temp.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        raise


def document(root, doc_id):
    return read_json(export_path(root, doc_id, "json"))


def history(root):
    result = []
    for path in (Path(root) / "pdf" / "exports").glob("*.json"):
        try:
            item = read_json(path)
            result.append({key: item[key] for key in ("id", "filename", "title", "count", "created", "size")})
        except (ValueError, OSError, KeyError):
            continue
    return sorted(result, key=lambda item: item["created"], reverse=True)
