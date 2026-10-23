# Images to PDF

Add an Images → PDF workspace beside the existing video tools. Uploaded images
remain under the ignored projects/pdf directory. A browser uploads images one at
a time to the authenticated loopback server. Store EXIF-oriented, metadata-free
PNG assets with opaque IDs; never trust a supplied filename as a path.

Preserve input order and offer drag/drop reorder plus accessible move controls,
natural filename sorting, reverse, duplicate and remove. Each page has independent
filter, brightness, contrast, saturation, sharpness, threshold, rotation, flips,
autocontrast, inversion and percentage crop settings. Offer apply-to-all/reset.

Global PDF controls: A4/A5/Letter/Legal/image-sized pages, auto/portrait/landscape,
contain/cover/stretch fit, margins, background, DPI, JPEG quality, title, download
filename and page numbers. Preview and export share the same image/layout code.
Generate one image per page in the visible order. Save exports and their manifests
so recent documents can be downloaded or reopened. No OCR, HEIC, or multipage TIFF
expansion is implied; first frame only for TIFF.

Use Pillow for deterministic processing and ReportLab for PDF packaging. New
code is isolated in pdf_images.py and assets/studio/pdf.js/pdf.css. Existing
video routes continue unchanged. Limit uploads to 20 MB, 24 megapixels per image,
and 100 pages per document. Validate IDs, numeric ranges, finite values, crop
bounds and format before decoding/export. Serve only registered assets/exports.

Tests inspect real PDFs for count, order/colors, page boxes, metadata, filters,
EXIF orientation, alpha flattening, fit/crop and validation. Exercise actual HTTP
upload/preview/export/download and existing auth protections. Render a sample
PDF for visual inspection; run all existing tests and check browser controls.
