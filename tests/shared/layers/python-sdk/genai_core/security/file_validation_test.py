"""
A5 — tests for genai_core.security.file_validation.

SCOPE: this suite exercises the actual validation logic used by
lib/rag-engines/data-import/functions/upload-handler/index.py to
re-verify uploaded file content server-side (see that module's docstring
for the full trace of the real gap this closes). The Lambda handler
itself (boto3/aws_lambda_powertools-dependent) is exercised separately
in upload_handler_validation_test.py via a lighter integration check.
"""

import sys
import unittest
import importlib.util
from pathlib import Path

_CONFTEST_PATH = Path(__file__).parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "a5_file_validation_test_conftest", _CONFTEST_PATH
)
_conftest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _conftest
_spec.loader.exec_module(_conftest)

fv = _conftest.get_file_validation()

import io  # noqa: E402
import zipfile  # noqa: E402


def _make_zip(entries, compress_type=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress_type) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestValidFiles(unittest.TestCase):
    """Case 1-3 from the requested matrix: valid supported files must
    continue to pass — this is the regression-safety half of A5."""

    def test_valid_pdf(self):
        content = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF"
        result = fv.validate_upload("report.pdf", content)
        self.assertTrue(result.valid)

    def test_valid_txt(self):
        result = fv.validate_upload("notes.txt", b"Just plain text content.")
        self.assertTrue(result.valid)

    def test_valid_docx(self):
        docx_bytes = _make_zip(
            {
                "[Content_Types].xml": "<Types/>",
                "word/document.xml": "<document>Hello world</document>",
            }
        )
        result = fv.validate_upload("report.docx", docx_bytes)
        self.assertTrue(result.valid)

    def test_valid_csv(self):
        result = fv.validate_upload("data.csv", b"a,b,c\n1,2,3\n")
        self.assertTrue(result.valid)

    def test_valid_legacy_doc_ole2(self):
        ole2_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        result = fv.validate_upload("legacy.doc", ole2_header)
        self.assertTrue(result.valid)

    def test_valid_rtf(self):
        result = fv.validate_upload("note.rtf", b"{\\rtf1\\ansi Hello}")
        self.assertTrue(result.valid)

    def test_unchecked_image_extension_passes_through(self):
        # Session uploads allow image types this module doesn't
        # signature-check (see EXTENSION_FAMILY "unchecked") — confirms
        # that's a deliberate pass-through, not an oversight.
        result = fv.validate_upload("photo.jpg", b"\xff\xd8\xff\xe0anything")
        self.assertTrue(result.valid)


class TestUnsupportedExtension(unittest.TestCase):
    def test_exe_extension_rejected(self):
        result = fv.validate_upload("malware.exe", b"MZ\x90\x00fake")
        self.assertFalse(result.valid)
        self.assertIn("Unsupported file extension", result.reason)

    def test_sh_extension_rejected(self):
        result = fv.validate_upload("script.sh", b"#!/bin/bash\necho hi")
        self.assertFalse(result.valid)

    def test_no_extension_rejected(self):
        result = fv.validate_upload("noextension", b"content")
        self.assertFalse(result.valid)


class TestMismatchedExtensionContent(unittest.TestCase):
    """The core A5 finding: extension allow-listed at request time does
    not guarantee the uploaded bytes actually match that format."""

    def test_exe_renamed_to_pdf_is_rejected(self):
        result = fv.validate_upload("report.pdf", b"MZ\x90\x00\x03\x00\x00\x00")
        self.assertFalse(result.valid)
        self.assertIn("PDF", result.reason)

    def test_random_bytes_renamed_to_docx_is_rejected(self):
        result = fv.validate_upload("report.docx", b"not a zip at all, just text")
        self.assertFalse(result.valid)

    def test_pdf_renamed_to_docx_is_rejected(self):
        result = fv.validate_upload("fake.docx", b"%PDF-1.4\nreal pdf bytes")
        self.assertFalse(result.valid)

    def test_binary_content_renamed_to_txt_is_rejected(self):
        result = fv.validate_upload("notes.txt", bytes(range(256)))
        self.assertFalse(result.valid)
        self.assertIn("does not appear to be text", result.reason)

    def test_exe_renamed_to_doc_ole2_is_rejected(self):
        result = fv.validate_upload("legacy.doc", b"MZ\x90\x00fake exe content")
        self.assertFalse(result.valid)


class TestSpoofedMimeTypeIsIrrelevant(unittest.TestCase):
    """This module deliberately never looks at a client-supplied
    Content-Type/MIME string at all — it only ever inspects the actual
    bytes. These tests document that by construction: validate_upload()
    has no MIME-type parameter to spoof in the first place."""

    def test_function_signature_has_no_mime_parameter(self):
        import inspect

        sig = inspect.signature(fv.validate_upload)
        self.assertNotIn("mime", " ".join(sig.parameters.keys()).lower())
        self.assertNotIn("content_type", " ".join(sig.parameters.keys()).lower())

    def test_real_bytes_determine_the_result_regardless_of_claimed_type(self):
        # Same bytes, same (correct) extension -> same result, proving
        # there's no separate "claimed type" input anywhere to spoof.
        pdf_bytes = b"%PDF-1.4\nreal"
        self.assertEqual(
            fv.validate_upload("a.pdf", pdf_bytes).valid,
            fv.validate_upload("b.pdf", pdf_bytes).valid,
        )


class TestEmptyAndInvalidFiles(unittest.TestCase):
    def test_empty_file_rejected(self):
        result = fv.validate_upload("empty.pdf", b"")
        self.assertFalse(result.valid)
        self.assertIn("empty", result.reason.lower())

    def test_none_content_rejected(self):
        result = fv.check_content_matches_extension("a.pdf", None)
        self.assertFalse(result.valid)


class TestFilenameSafety(unittest.TestCase):
    def test_dangerous_traversal_filename_rejected(self):
        result = fv.validate_filename("../../etc/passwd")
        self.assertFalse(result.valid)

    def test_absolute_path_filename_rejected(self):
        result = fv.validate_filename("/etc/passwd")
        self.assertFalse(result.valid)

    def test_windows_style_traversal_rejected(self):
        result = fv.validate_filename("..\\..\\windows\\system32\\config")
        self.assertFalse(result.valid)

    def test_control_character_filename_rejected(self):
        result = fv.validate_filename("report\x00.pdf")
        self.assertFalse(result.valid)

    def test_excessively_long_filename_rejected(self):
        result = fv.validate_filename("a" * 300 + ".pdf")
        self.assertFalse(result.valid)

    def test_bare_dotdot_rejected(self):
        result = fv.validate_filename("..")
        self.assertFalse(result.valid)

    def test_normal_filename_accepted(self):
        result = fv.validate_filename("Q3-Report_final.v2.pdf")
        self.assertTrue(result.valid)

    def test_filename_with_leading_trailing_whitespace_rejected(self):
        result = fv.validate_filename("  report.pdf  ")
        self.assertFalse(result.valid)

    def test_full_validate_upload_rejects_traversal_filename_even_with_valid_content(self):
        # End-to-end: even a well-formed PDF must be rejected if its
        # filename itself is unsafe — filename and content checks are
        # both mandatory, not either/or.
        result = fv.validate_upload("../../etc/report.pdf", b"%PDF-1.4\nreal")
        self.assertFalse(result.valid)
        self.assertIn("path separator", result.reason.lower())


class TestMalformedDocuments(unittest.TestCase):
    def test_malformed_pdf_missing_header(self):
        result = fv.validate_upload("broken.pdf", b"this is not a pdf at all")
        self.assertFalse(result.valid)

    def test_malformed_docx_bad_zip_structure(self):
        result = fv.validate_upload(
            "broken.docx", b"PK\x03\x04" + b"\x00" * 20 + b"not really a zip"
        )
        self.assertFalse(result.valid)
        self.assertIn("valid ZIP", result.reason)

    def test_truncated_pdf_header_only(self):
        # Even a truncated file, as long as it has the real magic bytes,
        # passes THIS check — signature verification is not full-file
        # structural validation. Documented limitation, not a bug.
        result = fv.validate_upload("truncated.pdf", b"%PDF-1.4")
        self.assertTrue(result.valid)


class TestArchiveResourceExhaustion(unittest.TestCase):
    def test_realistic_zip_bomb_is_rejected(self):
        # Highly compressible 50MB payload compressing to ~50KB —
        # a canonical zip-bomb shape.
        bomb = _make_zip({"bomb.xml": b"A" * (50 * 1024 * 1024)})
        self.assertLess(len(bomb), 1024 * 1024)  # confirms it's a REAL bomb shape
        result = fv.validate_upload("report.docx", bomb)
        self.assertFalse(result.valid)
        self.assertIn("compression ratio", result.reason)

    def test_excessive_entry_count_is_rejected(self):
        entries = {f"f{i}.xml": "x" for i in range(fv.MAX_ZIP_ENTRIES + 1)}
        archive = _make_zip(entries, compress_type=zipfile.ZIP_STORED)
        result = fv.validate_upload("report.pptx", archive)
        self.assertFalse(result.valid)
        self.assertIn("too many entries", result.reason)

    def test_archive_entry_path_traversal_is_rejected(self):
        archive = _make_zip({"../../etc/passwd": "malicious"})
        result = fv.validate_upload("report.xlsx", archive)
        self.assertFalse(result.valid)
        self.assertIn("unsafe path", result.reason)

    def test_legitimate_large_ish_docx_is_not_falsely_flagged(self):
        # A realistic, moderately large but legitimate document must NOT
        # trip the zip-bomb heuristics — regression safety for real
        # users. Uses varied paragraph text (not one repeated string,
        # which compresses unrealistically well) and pseudo-random bytes
        # standing in for an already-compressed embedded image (real
        # PNG/JPEG data does not compress further inside a zip, unlike
        # a block of repeated bytes) — the first version of this test
        # used repeated content and, correctly, tripped the ratio
        # check itself; fixed by using higher-entropy content that
        # actually represents realistic document structure.
        import random

        random.seed(42)
        words = [
            "quarterly",
            "revenue",
            "growth",
            "strategy",
            "customer",
            "engineering",
            "roadmap",
            "analysis",
            "forecast",
            "summary",
        ]
        paragraph_text = " ".join(
            random.choice(words) for _ in range(6000)
        )  # varied, not repetitive
        pseudo_image_bytes = bytes(random.getrandbits(8) for _ in range(20000))
        archive = _make_zip(
            {
                "[Content_Types].xml": "<Types/>",
                "word/document.xml": f"<p>{paragraph_text}</p>",
                "word/media/image1.png": pseudo_image_bytes,
            }
        )
        result = fv.validate_upload("legit_report.docx", archive)
        self.assertTrue(result.valid, result.reason)


if __name__ == "__main__":
    unittest.main()
