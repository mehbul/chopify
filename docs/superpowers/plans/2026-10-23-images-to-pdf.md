# Images to PDF implementation plan

1. Add failing image/PDF behavior tests and HTTP contracts. Install Pillow and
   ReportLab as runtime dependencies; pypdf is a development dependency.
2. Implement validated upload storage, per-image processing, common page layout,
   preview/export, and saved export manifests in pdf_images.py.
3. Add authenticated PDF routes to dashboard.py and a separate browser workspace.
   Provide ordered uploads, page editing, preview, export and recent documents.
4. Test real browser interactions and render a sample PDF for visual checks.
5. Update README, workflow/API docs, product facts and public feature descriptions.
   Run regression tests/compile/JS checks; commit and sync GitHub as requested in
   the ongoing app development workflow.

## Verification completed

- 97 tests pass, including real PDF creation/inspection and authenticated HTTP
  upload, preview, export and attachment download.
- Browser: three images uploaded, reordered 1/3/2, with grayscale on only the
  second PDF page; title/page numbers set; exported document reopened; browser
  reload recovered the draft. No browser console errors were observed.
- Poppler rendered all three exported pages. Order, colors/grayscale, margins,
  image placement and footer numbering were visually checked.
- JavaScript syntax checks, Python compilation, public-site build and diff checks
  passed. Private uploaded assets and PDFs remain excluded from Git/public builds.
