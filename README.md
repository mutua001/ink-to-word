# Ink to Word

A small local web app for transcribing handwritten scans and exporting the reviewed text as a Word document.

## Start the app

- Double-click the desktop shortcut, or run `run_app.bat`.
- On the first run, the launcher creates a Python virtual environment and installs the listed dependencies. It then opens the app at `http://127.0.0.1:5000`.
- To see startup errors, run `run_app.bat` directly from File Explorer.

The app listens only on this computer. It does not save uploaded files or transcriptions to disk.

## OCR and privacy

OCR is provided by OCR.space. The app accepts documents up to 100 MB. PDFs are rendered into one image per page, and oversized images are compressed on this computer before each page is sent separately to OCR.space to fit its 1 MB per-image limit. Your document pages are uploaded to their service for recognition, so do not use documents you are not comfortable sending to that provider. Handwriting quality depends on the scan and may need manual correction.

Get a free API key at [ocr.space](https://ocr.space/ocrapi/freekey) and enter it in the app. The key is used for the OCR request and is not stored by this app. Alternatively, set `OCR_SPACE_API_KEY` in the environment before launching to use a server-configured key.

Supported inputs: PDF, JPG/JPEG, PNG, GIF, TIFF, and BMP.
