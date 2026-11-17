import os
import threading
import time
import uuid
import webbrowser
from io import BytesIO
from mimetypes import guess_type
from pathlib import PurePosixPath

import pypdfium2 as pdfium
import requests
from PIL import Image, UnidentifiedImageError
from docx import Document
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename


OCR_API_URL = "https://api.ocr.space/parse/image"
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_OCR_IMAGE_BYTES = 950 * 1024
MAX_ACTIVE_OCR_JOBS = 2
OCR_JOB_TTL_SECONDS = 3600
ALLOWED_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".pdf", ".png", ".tif", ".tiff"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_BYTES + 1024 * 1024
OCR_JOBS = {}
OCR_JOBS_LOCK = threading.Lock()


class OCRServiceError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _encode_ocr_image(image):
    image = image.convert("RGB")
    max_dimension = 2400
    if max(image.size) > max_dimension:
        scale = max_dimension / max(image.size)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    while True:
        for quality in (85, 75, 65, 55):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= MAX_OCR_IMAGE_BYTES:
                return output.getvalue()

        if max(image.size) <= 900:
            raise ValueError("A page could not be compressed enough for the OCR service.")

        image = image.resize(
            (max(1, round(image.width * 0.85)), max(1, round(image.height * 0.85))),
            Image.Resampling.LANCZOS,
        )


def _iter_ocr_images(filename, content):
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension == ".pdf":
        document = pdfium.PdfDocument(content)
        try:
            if not len(document):
                raise ValueError("The PDF has no pages to read.")
            for index in range(len(document)):
                page = document[index]
                bitmap = page.render(scale=2)
                try:
                    image = bitmap.to_pil()
                    page_content = _encode_ocr_image(image)
                finally:
                    bitmap.close()
                    page.close()
                yield f"page-{index + 1}.jpg", page_content, "image/jpeg"
        finally:
            document.close()
        return

    if extension in {".gif", ".tif", ".tiff"} or len(content) > MAX_OCR_IMAGE_BYTES:
        with Image.open(BytesIO(content)) as source:
            page_count = getattr(source, "n_frames", 1)
            for index in range(page_count):
                source.seek(index)
                image = source.copy()
                suffix = f"-page-{index + 1}" if page_count > 1 else ""
                yield f"page{suffix}.jpg", _encode_ocr_image(image), "image/jpeg"
        return

    yield secure_filename(filename), content, guess_type(filename)[0] or "application/octet-stream"


def _recognize_image(api_key, filename, content, mime_type, page_number):
    try:
        response = requests.post(
            OCR_API_URL,
            headers={"apikey": api_key},
            data={"language": "auto", "OCREngine": "2"},
            files={"file": (filename, BytesIO(content), mime_type)},
            timeout=90,
        )
    except requests.Timeout as error:
        raise OCRServiceError(
            f"Page {page_number}: OCR timed out. Try again with a clearer scan.", 504
        ) from error
    except requests.RequestException as error:
        raise OCRServiceError(
            f"Page {page_number}: Could not reach the OCR service. Check your internet connection and try again.",
            502,
        ) from error

    try:
        result = response.json()
    except ValueError as error:
        raise OCRServiceError(
            f"Page {page_number}: The OCR service returned an unreadable response.", 502
        ) from error

    if not isinstance(result, dict):
        raise OCRServiceError(
            f"Page {page_number}: The OCR service returned an unexpected response.", 502
        )
    if response.status_code == 403:
        raise OCRServiceError(
            f"Page {page_number}: OCR.space rejected the API key (HTTP 403). "
            "Check the key or generate a new one.",
            502,
        )
    if response.status_code >= 400:
        message = result.get("ErrorMessage") or f"The OCR service returned HTTP {response.status_code}."
        if isinstance(message, list):
            message = " ".join(str(item) for item in message)
        raise OCRServiceError(f"Page {page_number}: {message}", 502)
    if result.get("IsErroredOnProcessing"):
        message = result.get("ErrorMessage") or "The OCR service could not process this page."
        if isinstance(message, list):
            message = " ".join(str(item) for item in message)
        raise OCRServiceError(f"Page {page_number}: {message}", 422)

    pages = result.get("ParsedResults") or []
    if not isinstance(pages, list):
        raise OCRServiceError(
            f"Page {page_number}: The OCR service returned an unexpected response.", 502
        )
    return "\n\n".join(
        page.get("ParsedText", "").strip()
        for page in pages
        if isinstance(page, dict) and isinstance(page.get("ParsedText", ""), str)
    ).strip()


def _process_ocr_job(job_id, filename, content, api_key):
    try:
        page_texts = []
        for page_number, (page_filename, page_content, mime_type) in enumerate(
            _iter_ocr_images(filename, content), start=1
        ):
            with OCR_JOBS_LOCK:
                job = OCR_JOBS.get(job_id)
                if job is None:
                    return
                job.update(status="processing", current_page=page_number, updated_at=time.time())

            text = _recognize_image(api_key, page_filename, page_content, mime_type, page_number)
            if text:
                page_texts.append(text)

        text = "\n\n".join(page_texts).strip()
        if not text:
            raise OCRServiceError("No text was recognized. Try a sharper, well-lit scan.", 422)

        with OCR_JOBS_LOCK:
            job = OCR_JOBS.get(job_id)
            if job is not None:
                job.update(status="done", text=text, updated_at=time.time())
    except OCRServiceError as error:
        with OCR_JOBS_LOCK:
            job = OCR_JOBS.get(job_id)
            if job is not None:
                job.update(status="failed", error=str(error), updated_at=time.time())
    except (pdfium.PdfiumError, UnidentifiedImageError, OSError, RuntimeError, ValueError):
        with OCR_JOBS_LOCK:
            job = OCR_JOBS.get(job_id)
            if job is not None:
                job.update(
                    status="failed",
                    error="Could not read this document. Check that the file is a valid PDF or image.",
                    updated_at=time.time(),
                )
    except Exception:
        app.logger.exception("Unexpected error while processing OCR job %s", job_id)
        with OCR_JOBS_LOCK:
            job = OCR_JOBS.get(job_id)
            if job is not None:
                job.update(
                    status="failed",
                    error="An unexpected error interrupted transcription. Please try again.",
                    updated_at=time.time(),
                )


def _remove_expired_jobs(now):
    expired = [
        job_id
        for job_id, job in OCR_JOBS.items()
        if job["status"] in {"done", "failed"} and now - job["updated_at"] > OCR_JOB_TTL_SECONDS
    ]
    for job_id in expired:
        del OCR_JOBS[job_id]


@app.get("/")
def home():
    return render_template(
        "index.html",
        has_server_key=bool(os.environ.get("OCR_SPACE_API_KEY", "").strip()),
    )


@app.post("/api/ocr")
def recognize_handwriting():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(error="Choose an image or PDF to transcribe."), 400

    extension = PurePosixPath(upload.filename.replace("\\", "/")).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify(error="Use a PDF or image file (JPG, PNG, GIF, TIFF, or BMP)."), 400

    content = upload.read(MAX_FILE_BYTES + 1)
    if not content:
        return jsonify(error="The selected file is empty."), 400
    if len(content) > MAX_FILE_BYTES:
        return jsonify(error="The maximum document size is 100 MB."), 413

    api_key = request.form.get("api_key", "").strip() or os.environ.get("OCR_SPACE_API_KEY", "").strip()
    if not api_key:
        return jsonify(error="Enter your OCR.space API key to start. The key is used for this request only."), 400

    now = time.time()
    with OCR_JOBS_LOCK:
        _remove_expired_jobs(now)
        active_jobs = sum(job["status"] in {"queued", "processing"} for job in OCR_JOBS.values())
        if active_jobs >= MAX_ACTIVE_OCR_JOBS:
            return jsonify(error="Two documents are already being processed. Wait for one to finish, then try again."), 429
        job_id = str(uuid.uuid4())
        OCR_JOBS[job_id] = {"status": "queued", "current_page": 0, "updated_at": now}

    worker = threading.Thread(
        target=_process_ocr_job,
        args=(job_id, upload.filename, content, api_key),
        daemon=True,
    )
    try:
        worker.start()
    except RuntimeError:
        with OCR_JOBS_LOCK:
            OCR_JOBS.pop(job_id, None)
        return jsonify(error="Could not start transcription. Please try again."), 503
    return jsonify(job_id=job_id, status="queued"), 202


@app.get("/api/ocr/<job_id>")
def get_ocr_job(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify(error="Transcription job not found."), 404

    with OCR_JOBS_LOCK:
        _remove_expired_jobs(time.time())
        job = OCR_JOBS.get(job_id)
        if job is None:
            return jsonify(error="This transcription job has expired. Please submit the document again."), 404
        response = {"status": job["status"]}
        if job["status"] == "processing":
            response["current_page"] = job["current_page"]
        elif job["status"] == "done":
            response["text"] = job["text"]
        elif job["status"] == "failed":
            response["error"] = job["error"]
        return jsonify(response)


@app.post("/api/docx")
def create_word_document():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="Provide the transcription as a JSON object."), 400
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify(error="Add some transcription text before creating the Word document."), 400
    if len(text) > 500_000:
        return jsonify(error="The transcription is too long to export."), 413

    document = Document()
    for line in text.splitlines():
        document.add_paragraph(line)

    output = BytesIO()
    document.save(output)
    output.seek(0)

    source_name = data.get("source_name", "transcription")
    if not isinstance(source_name, str):
        source_name = "transcription"
    stem = PurePosixPath(source_name.replace("\\", "/")).stem
    safe_stem = secure_filename(stem) or "transcription"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=f"{safe_stem}-transcription.docx",
    )


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="The selected file is too large. The maximum document size is 100 MB."), 413


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
