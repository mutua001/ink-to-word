import io
import random
import time
import unittest
from unittest.mock import Mock, patch

import pypdfium2 as pdfium
from PIL import Image
from docx import Document

import app


class AppTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_homepage_renders(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your notes", response.data)

    def test_ocr_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True), patch("app.requests.post") as post:
            response = self.client.post(
                "/api/ocr",
                data={"file": (io.BytesIO(b"scan"), "notes.png")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("API key", response.get_json()["error"])
        post.assert_not_called()

    @patch("app.requests.post")
    def test_ocr_returns_recognized_pages(self, post):
        upstream = Mock()
        upstream.status_code = 200
        upstream.json.return_value = {
            "IsErroredOnProcessing": False,
            "ParsedResults": [
                {"ParsedText": "First page"},
                {"ParsedText": "Second page"},
            ],
        }
        post.return_value = upstream

        response = self.client.post(
            "/api/ocr",
            data={
                "api_key": "test-key",
                "file": (io.BytesIO(b"scan"), "notes.png"),
            },
            content_type="multipart/form-data",
        )

        _, result = self._submit_and_wait(response)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["text"], "First page\n\nSecond page")
        self.assertEqual(post.call_args.kwargs["headers"], {"apikey": "test-key"})

    @patch("app.requests.post")
    def test_large_multipage_pdf_is_split_before_upload(self, post):
        content = self._make_pdf(2)
        content += b" " * (app.MAX_OCR_IMAGE_BYTES + 1024)

        post.side_effect = [
            self._ocr_response("First page"),
            self._ocr_response("Second page"),
        ]
        response = self.client.post(
            "/api/ocr",
            data={
                "api_key": "test-key",
                "file": (io.BytesIO(content), "notes.pdf"),
            },
            content_type="multipart/form-data",
        )

        _, result = self._submit_and_wait(response)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["text"], "First page\n\nSecond page")
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            uploaded_name, uploaded_file, mime_type = call.kwargs["files"]["file"]
            self.assertTrue(uploaded_name.endswith(".jpg"))
            self.assertEqual(mime_type, "image/jpeg")
            self.assertLessEqual(len(uploaded_file.getvalue()), app.MAX_OCR_IMAGE_BYTES)

    @patch("app.requests.post")
    def test_oversized_image_is_compressed_before_upload(self, post):
        image_bytes = io.BytesIO()
        image = Image.frombytes(
            "RGB",
            (1500, 1500),
            random.Random(7).randbytes(1500 * 1500 * 3),
        )
        image.save(image_bytes, format="PNG", compress_level=1)
        content = image_bytes.getvalue()
        self.assertGreater(len(content), app.MAX_OCR_IMAGE_BYTES)
        post.return_value = self._ocr_response("Recognized text")

        response = self.client.post(
            "/api/ocr",
            data={
                "api_key": "test-key",
                "file": (io.BytesIO(content), "large-scan.png"),
            },
            content_type="multipart/form-data",
        )

        _, result = self._submit_and_wait(response)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["text"], "Recognized text")
        uploaded_name, uploaded_file, mime_type = post.call_args.kwargs["files"]["file"]
        self.assertEqual(uploaded_name, "page.jpg")
        self.assertEqual(mime_type, "image/jpeg")
        self.assertLessEqual(len(uploaded_file.getvalue()), app.MAX_OCR_IMAGE_BYTES)

    @patch("app.requests.post")
    def test_ocr_job_reports_api_key_rejection(self, post):
        response = Mock()
        response.status_code = 403
        response.json.return_value = {}
        post.return_value = response

        submission = self.client.post(
            "/api/ocr",
            data={
                "api_key": "test-key",
                "file": (io.BytesIO(b"scan"), "notes.png"),
            },
            content_type="multipart/form-data",
        )
        _, result = self._submit_and_wait(submission)

        self.assertEqual(result["status"], "failed")
        self.assertIn("HTTP 403", result["error"])

    def _submit_and_wait(self, submission):
        self.assertEqual(submission.status_code, 202, submission.get_json())
        job_id = submission.get_json()["job_id"]
        for _ in range(500):
            response = self.client.get(f"/api/ocr/{job_id}")
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            if result["status"] in {"done", "failed"}:
                return job_id, result
            time.sleep(0.01)
        self.fail("OCR job did not finish in time.")

    @staticmethod
    def _ocr_response(text):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "IsErroredOnProcessing": False,
            "ParsedResults": [{"ParsedText": text}],
        }
        return response

    @staticmethod
    def _make_pdf(page_count):
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids ["
            + b" ".join(f"{page * 2 + 3} 0 R".encode() for page in range(page_count))
            + f"] /Count {page_count} >>".encode(),
        ]
        for page in range(page_count):
            page_id = page * 2 + 3
            stream_id = page_id + 1
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] "
                f"/Resources << >> /Contents {stream_id} 0 R >>".encode()
            )
            objects.append(b"<< /Length 3 >>\nstream\nq Q\nendstream")

        content = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for object_id, body in enumerate(objects, start=1):
            offsets.append(len(content))
            content.extend(f"{object_id} 0 obj\n".encode() + body + b"\nendobj\n")

        xref_offset = len(content)
        content.extend(f"xref\n0 {len(objects) + 1}\n".encode())
        content.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            content.extend(f"{offset:010} 00000 n \n".encode())
        content.extend(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode()
        )
        return bytes(content)

    def test_docx_exports_edited_text_and_safe_filename(self):
        response = self.client.post(
            "/api/docx",
            json={"text": "Corrected first line\nSecond line", "source_name": "../../notes.txt"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("notes-transcription.docx", response.headers["Content-Disposition"])
        document = Document(io.BytesIO(response.data))
        self.assertEqual([paragraph.text for paragraph in document.paragraphs], ["Corrected first line", "Second line"])

    def test_docx_rejects_empty_text(self):
        response = self.client.post("/api/docx", json={"text": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertIn("text", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
