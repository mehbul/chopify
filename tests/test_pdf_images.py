import io

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader


def image_bytes(color="red", size=(120, 80), mode="RGB", fmt="PNG", **kwargs):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def test_upload_preserves_exif_orientation_and_strips_metadata(tmp_path):
    import pdf_images as pdf
    exif = Image.Exif()
    exif[274] = 6
    item = pdf.upload(tmp_path, "../../camera.jpg", image_bytes(size=(120,80), fmt="JPEG", exif=exif))
    assert (item["width"], item["height"]) == (80, 120)
    assert item["name"] == "camera.jpg"
    with Image.open(pdf.asset_path(tmp_path, item["id"])) as image:
        assert not image.getexif()
    with pytest.raises(ValueError):
        pdf.upload(tmp_path, "bad.png", b"not an image")
    with pytest.raises(ValueError):
        pdf.asset_path(tmp_path, "../outside")


def test_filters_crop_rotate_and_alpha_flatten():
    import pdf_images as pdf
    source = Image.new("RGBA", (100, 60), (200, 30, 10, 0))
    assert pdf.process_image(source, {}).getpixel((0,0)) == (255,255,255)
    source = Image.new("RGB", (100, 60), (200, 30, 10))
    cropped = pdf.process_image(source, {"crop": [0, 0, 50, 100], "rotation": 90})
    assert cropped.size == (60, 50)
    gray = pdf.process_image(source, {"filter": "grayscale"})
    r,g,b = gray.getpixel((0,0))
    assert r == g == b
    bw = pdf.process_image(source, {"filter": "bw", "threshold": 120})
    assert bw.getpixel((0,0)) == (0,0,0)
    inverted = pdf.process_image(source, {"invert": True})
    assert inverted.getpixel((0,0)) == (55,225,245)


@pytest.mark.parametrize("settings", [{"brightness": float("nan")}, {"rotation": 45},
    {"filter": "unknown"}, {"crop": [80,0,20,100]}, {"contrast": 5}, {"flip_h": "false"}])
def test_invalid_image_settings_rejected(settings):
    import pdf_images as pdf
    with pytest.raises(ValueError):
        pdf.validate_image_options(settings)


def test_pdf_order_page_sizes_title_and_saved_manifest(tmp_path):
    import pdf_images as pdf
    red = pdf.upload(tmp_path, "red.png", image_bytes("red"))
    blue = pdf.upload(tmp_path, "blue.png", image_bytes("blue", size=(80,120)))
    pages = [{"asset": blue["id"], "settings": {}}, {"asset": red["id"], "settings": {}}]
    result = pdf.export(tmp_path, pages, {"title": "My album", "filename": "album", "paper": "A4",
        "orientation": "auto", "dpi": 72, "margin": 0, "fit": "contain"})
    reader = PdfReader(pdf.export_path(tmp_path, result["id"], "pdf"))
    assert len(reader.pages) == 2
    assert reader.metadata.title == "My album"
    first, second = reader.pages
    assert first.mediabox.height > first.mediabox.width
    assert second.mediabox.width > second.mediabox.height
    pixels = [list(page.images)[0].image.convert("RGB") for page in reader.pages]
    assert pixels[0].getpixel((pixels[0].width//2,pixels[0].height//2))[2] > 240
    assert pixels[1].getpixel((pixels[1].width//2,pixels[1].height//2))[0] > 240
    manifest = pdf.document(tmp_path, result["id"])
    assert [p["asset"] for p in manifest["pages"]] == [blue["id"], red["id"]]
    assert pdf.history(tmp_path)[0]["id"] == result["id"]


def test_contain_cover_and_stretch_layout():
    import pdf_images as pdf
    image = Image.new("RGB", (100, 50), "red")
    options = {"paper": "A4", "orientation": "portrait", "margin": 0, "dpi": 72, "background": "#00FF00"}
    contain, _ = pdf.compose_page(image, {}, {**options, "fit": "contain"}, 1, 1)
    cover, _ = pdf.compose_page(image, {}, {**options, "fit": "cover"}, 1, 1)
    stretch, _ = pdf.compose_page(image, {}, {**options, "fit": "stretch"}, 1, 1)
    assert contain.getpixel((0,0)) == (0,255,0)
    assert cover.getpixel((0,0)) == stretch.getpixel((0,0)) == (255,0,0)


def test_export_rejects_empty_or_invalid_pages_without_partial_output(tmp_path):
    import pdf_images as pdf
    for pages in [[], [{"asset": "../../secret", "settings": {}}]]:
        with pytest.raises(ValueError):
            pdf.export(tmp_path, pages, {})
    assert not list((tmp_path / "pdf" / "exports").glob("*.pdf"))


def test_image_sized_pages_keep_exact_physical_aspect():
    import pdf_images as pdf
    _, size = pdf.compose_page(Image.new("RGB", (120,80), "red"), {},
        {"paper":"Image", "dpi":150, "margin":0}, 1, 1)
    assert size == pytest.approx((57.6,38.4))


def test_scan_sepia_and_page_number_rendering():
    import pdf_images as pdf
    source = Image.new("RGB", (20,10), (150,150,150))
    ImageDraw.Draw(source).rectangle((10,0,19,9), fill=(170,170,170))
    scan = pdf.process_image(source, {"filter":"scan"})
    assert scan.getpixel((2,5)) == (0,0,0)
    assert scan.getpixel((18,5)) == (255,255,255)
    sepia = pdf.process_image(source, {"filter":"sepia"})
    r,g,b = sepia.getpixel((2,5))
    assert r > g > b
    page, _ = pdf.compose_page(Image.new("RGB", (80,100), "white"), {},
                              {"dpi":72,"page_numbers":True}, 2, 3)
    footer = page.crop((0,page.height-45,page.width,page.height)).convert("L")
    assert footer.getextrema()[0] < 100  # Actual number glyphs, not just a saved option.
