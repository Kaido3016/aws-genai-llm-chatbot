"""
A5 — integration tests for the real
lib/rag-engines/data-import/functions/upload-handler/index.py, verifying
that _validate_uploaded_object()/process_record() actually wire in
genai_core.security.file_validation and short-circuit correctly on
invalid uploads, for both the Kendra and non-Kendra (Step Functions)
engine branches.

VERIFICATION LEVEL: exercises the real index.py and the real
file_validation.py, via dependency stubs for boto3/aws_lambda_powertools
(neither installed in this sandbox) — see
_a5_upload_handler_stub_loader.py's docstring for exactly what's stubbed.
Does not verify actual S3/Step Functions/Kendra behavior in AWS —
UNVERIFIED — REQUIRES AWS ENVIRONMENT for that.
"""

import sys
import unittest
import importlib.util
from pathlib import Path

_LOADER_PATH = Path(__file__).parent / "_a5_upload_handler_stub_loader.py"
_spec = importlib.util.spec_from_file_location(
    "a5_upload_handler_stub_loader", _LOADER_PATH
)
_loader = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _loader
_spec.loader.exec_module(_loader)

# Loaded ONCE at module level, matching the established convention in
# workspace_retriever_test.py / sessions_citations_test.py — this also
# sidesteps a real bug found while building this harness: calling the
# stub installer more than once per process made its own
# already-installed stubs look like "real dependencies are available"
# (now fixed with an idempotency sentinel in the loader itself too, but
# calling once here is the primary, convention-matching fix).
MODULE, STUBS_USED, FAKE_S3, FAKE_SFN, DOCUMENTS_MOD, WORKSPACES_MOD = (
    _loader.load_upload_handler_module()
)


def _make_record(bucket, key, size):
    return {
        "s3": {
            "bucket": {"name": bucket},
            "object": {"key": key, "size": size},
        }
    }


class UploadHandlerValidationTest(unittest.TestCase):
    def setUp(self):
        self.module = MODULE
        self.fake_s3 = FAKE_S3
        self.fake_sfn = FAKE_SFN
        self.documents_mod = DOCUMENTS_MOD
        self.workspaces_mod = WORKSPACES_MOD
        # Fresh state per test.
        self.documents_mod._documents.clear()
        self.documents_mod._next_id[0] = 1
        self.workspaces_mod._workspaces.clear()
        self.fake_s3.objects.clear()
        self.fake_s3.copy_calls.clear()
        self.fake_s3.put_calls.clear()
        self.fake_sfn.start_execution_calls.clear()

        self.workspaces_mod._workspaces["ws-1"] = {
            "engine": "aurora",
            "title": "Test workspace",
        }
        self.workspaces_mod._workspaces["ws-kendra"] = {
            "engine": "kendra",
            "title": "Kendra workspace",
        }

    def test_valid_pdf_upload_proceeds_to_step_functions(self):
        self.fake_s3.objects["ws-1/report.pdf"] = b"%PDF-1.4\nreal pdf content"
        record = _make_record("upload-bucket", "ws-1/report.pdf", 25)

        self.module.process_record(record)

        self.assertEqual(len(self.fake_sfn.start_execution_calls), 1)
        doc = next(iter(self.documents_mod._documents.values()))
        self.assertNotEqual(doc["status"], "error")

    def test_spoofed_pdf_upload_is_rejected_before_step_functions(self):
        # The core A5 finding: an .exe renamed to .pdf, allowed through
        # the extension check at presign time, must be caught here.
        self.fake_s3.objects["ws-1/report.pdf"] = b"MZ\x90\x00\x03\x00fake-exe"
        record = _make_record("upload-bucket", "ws-1/report.pdf", 20)

        self.module.process_record(record)

        self.assertEqual(len(self.fake_sfn.start_execution_calls), 0)
        doc = next(iter(self.documents_mod._documents.values()))
        self.assertEqual(doc["status"], "error")

    def test_spoofed_upload_to_kendra_workspace_is_also_rejected(self):
        # Confirms the fix applies BEFORE the Kendra branch too — the
        # pre-A5 code path copied straight to Kendra with zero
        # validation for this engine.
        self.fake_s3.objects["ws-kendra/report.pdf"] = b"not a real pdf"
        record = _make_record("upload-bucket", "ws-kendra/report.pdf", 15)

        self.module.process_record(record)

        self.assertEqual(len(self.fake_s3.copy_calls), 0)
        self.assertEqual(len(self.fake_s3.put_calls), 0)
        doc = next(iter(self.documents_mod._documents.values()))
        self.assertEqual(doc["status"], "error")

    def test_valid_upload_to_kendra_workspace_proceeds(self):
        self.fake_s3.objects["ws-kendra/report.pdf"] = b"%PDF-1.4\nreal"
        record = _make_record("upload-bucket", "ws-kendra/report.pdf", 15)

        self.module.process_record(record)

        self.assertEqual(len(self.fake_s3.copy_calls), 1)
        doc = next(iter(self.documents_mod._documents.values()))
        self.assertEqual(doc["status"], "processed")

    def test_empty_file_upload_is_rejected(self):
        self.fake_s3.objects["ws-1/empty.txt"] = b""
        record = _make_record("upload-bucket", "ws-1/empty.txt", 0)

        self.module.process_record(record)

        self.assertEqual(len(self.fake_sfn.start_execution_calls), 0)
        doc = next(iter(self.documents_mod._documents.values()))
        self.assertEqual(doc["status"], "error")

    def test_unsupported_extension_upload_is_rejected(self):
        self.fake_s3.objects["ws-1/malware.exe"] = b"MZ\x90\x00fake"
        record = _make_record("upload-bucket", "ws-1/malware.exe", 10)

        self.module.process_record(record)

        self.assertEqual(len(self.fake_sfn.start_execution_calls), 0)
        doc = next(iter(self.documents_mod._documents.values()))
        self.assertEqual(doc["status"], "error")

    def test_document_record_is_still_created_on_rejection(self):
        # Preserves user-visible behavior: a failed upload should still
        # show up (as "error") in the workspace's document list, not
        # silently vanish.
        self.fake_s3.objects["ws-1/bad.pdf"] = b"not a pdf"
        record = _make_record("upload-bucket", "ws-1/bad.pdf", 9)

        self.module.process_record(record)

        self.assertEqual(len(self.documents_mod._documents), 1)


if __name__ == "__main__":
    unittest.main()
