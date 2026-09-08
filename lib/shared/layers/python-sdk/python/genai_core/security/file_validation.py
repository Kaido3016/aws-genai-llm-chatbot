"""
A5 — server-side upload validation.

REAL GAP THIS CLOSES (found by tracing the actual pipeline, not assumed):

  getUploadFileURL (documents.py) checks the requested filename's
  extension against an allow-list *before* generating a presigned S3
  POST URL. But the presigned POST's only real constraint (see
  genai_core/presign.py) is `content-length-range` — nothing constrains
  the actual bytes uploaded to match the claimed extension, and nothing
  downstream re-checks it either:

    upload-handler/index.py (S3 ObjectCreated -> SQS -> this Lambda)
      -> Kendra workspaces: copies the object as-is, no validation at all
      -> other engines: starts the Step Functions file-import workflow
         -> lib/shared/file-import-batch-job/main.py determines the
            extension via os.path.splitext() and, for anything other
            than ".txt", hands the raw bytes straight to LangChain's
            S3FileLoader (backed by the `unstructured` library) with
            NO check that the bytes actually match the claimed format.

  So a file uploaded as "report.pdf" (passing the extension check) can
  contain arbitrary bytes with nothing to catch the mismatch before an
  extraction library attempts to parse it. This module provides the
  checks that close that gap; see upload-handler/index.py for where
  they're wired in.

SCOPE, STATED HONESTLY: this is signature/magic-byte and structural
verification, not full content inspection. It confirms a file's bytes
are *consistent with* its claimed format family (PDF, ZIP-based Office,
legacy OLE2 Office, RTF) or, for genuinely-plain-text formats, that the
bytes are decodable text without embedded NUL bytes. It cannot and does
not claim to detect every malicious payload a valid-format file could
carry (e.g. a well-formed PDF with an embedded exploit) — that is a
different, much larger problem (a full anti-malware/content-scanning
pipeline) that is out of scope for this pass and is documented as such
in SECURITY.md.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Optional

# --- Filename safety -------------------------------------------------

MAX_FILENAME_LENGTH = 255

_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_PATH_SEPARATOR_PATTERN = re.compile(r"[/\\]")
_TRAVERSAL_PATTERN = re.compile(r"\.\.")


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: Optional[str] = None


def validate_filename(filename: Optional[str]) -> ValidationResult:
    """Reject filenames that could be unsafe if ever used to construct a
    filesystem or storage path, independent of whatever the caller does
    with the S3 key.

    Note: genai_core/presign.py already applies `os.path.basename()`
    before building the S3 object key, which neutralizes `/`-based
    traversal in that one specific call path. This function is a
    broader, independent check — applied explicitly rather than relying
    on that single downstream call to be present on every path a
    filename might travel.
    """
    if not filename or not isinstance(filename, str):
        return ValidationResult(False, "Filename is empty or missing")
    if filename != filename.strip():
        return ValidationResult(False, "Filename has leading/trailing whitespace")
    if len(filename) > MAX_FILENAME_LENGTH:
        return ValidationResult(
            False, f"Filename exceeds maximum length of {MAX_FILENAME_LENGTH}"
        )
    if _CONTROL_CHAR_PATTERN.search(filename):
        return ValidationResult(False, "Filename contains control characters")
    if _PATH_SEPARATOR_PATTERN.search(filename):
        return ValidationResult(False, "Filename contains path separator characters")
    if _TRAVERSAL_PATTERN.search(filename):
        return ValidationResult(False, "Filename contains a path traversal sequence")
    if filename in (".", ".."):
        return ValidationResult(False, "Filename is not a valid file name")
    return ValidationResult(True)


# --- Content/extension consistency -----------------------------------

# Format "families" with a genuine, checkable magic-byte signature.
_PDF_SIGNATURE = b"%PDF-"
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_RTF_SIGNATURE = b"{\\rtf"

# Maps each extension this project's own allow-list
# (documents.py: allowed_workspace_extensions / allowed_session_extensions)
# actually supports to the verification family used below. Extensions in
# the app's allow-list that are genuinely plain text (no fixed magic
# bytes to check) fall into "text".
EXTENSION_FAMILY = {
    ".pdf": "pdf",
    ".docx": "zip",
    ".xlsx": "zip",
    ".pptx": "zip",
    ".epub": "zip",
    ".odt": "zip",
    ".doc": "ole2",
    ".ppt": "ole2",
    ".xls": "ole2",
    ".msg": "ole2",
    ".rtf": "rtf",
    ".csv": "text",
    ".tsv": "text",
    ".txt": "text",
    ".md": "text",
    ".rst": "text",
    ".json": "text",
    ".xml": "text",
    ".html": "text",
    ".eml": "text",
    # Session-upload-only, non-document types this module doesn't
    # attempt to signature-check (image/video formats) — treated as
    # "unchecked": extension allow-list + size limit are the controls
    # for these, consistent with what the rest of the app already does
    # for media.
    ".jpg": "unchecked",
    ".jpeg": "unchecked",
    ".png": "unchecked",
    ".mp4": "unchecked",
}


def _looks_like_text(content: bytes) -> bool:
    """Heuristic, not a guarantee: real plain-text content should not
    contain NUL bytes and should be decodable as UTF-8 or a common
    single-byte encoding. This catches the common case of a binary
    file (executable, image, archive) renamed to a text extension —
    it does not attempt to detect a maliciously crafted text-like
    payload, which is a different problem (see module docstring).
    """
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass
    try:
        content.decode("latin-1")
        return True
    except UnicodeDecodeError:
        return False


def check_content_matches_extension(
    filename: str, content: bytes
) -> ValidationResult:
    """Verify the file's actual bytes are consistent with its claimed
    extension's format family. See module docstring for exactly what
    this does and does not prove.
    """
    if content is None:
        return ValidationResult(False, "No content to validate")
    if len(content) == 0:
        return ValidationResult(False, "File is empty")

    extension = _extract_extension(filename)
    family = EXTENSION_FAMILY.get(extension)
    if family is None:
        return ValidationResult(False, f"Unsupported file extension: {extension}")

    if family == "unchecked":
        return ValidationResult(True)

    if family == "pdf":
        if not content.startswith(_PDF_SIGNATURE):
            return ValidationResult(
                False, "File content does not match the PDF format signature"
            )
        return ValidationResult(True)

    if family == "zip":
        if not content.startswith(_ZIP_SIGNATURES):
            return ValidationResult(
                False,
                "File content does not match the expected ZIP-based "
                "Office/document format signature",
            )
        return check_zip_bomb_risk(content)

    if family == "ole2":
        if not content.startswith(_OLE2_SIGNATURE):
            return ValidationResult(
                False,
                "File content does not match the expected legacy Office "
                "(OLE2) format signature",
            )
        return ValidationResult(True)

    if family == "rtf":
        if not content.startswith(_RTF_SIGNATURE):
            return ValidationResult(
                False, "File content does not match the RTF format signature"
            )
        return ValidationResult(True)

    if family == "text":
        if not _looks_like_text(content):
            return ValidationResult(
                False,
                "File content does not appear to be text, but the "
                f"extension ({extension}) claims a text-based format",
            )
        return ValidationResult(True)

    # Should be unreachable given EXTENSION_FAMILY's fixed value set,
    # but never silently accept an unrecognized family.
    return ValidationResult(False, f"Unrecognized format family: {family}")


def _extract_extension(filename: str) -> str:
    if not filename or "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


# --- ZIP-bomb / archive-structure risk --------------------------------

# Reasoning for these defaults (per the instruction not to invent
# arbitrary limits without explaining why): uploads are already capped
# at 10MB compressed (genai_core/presign.py MAX_FILE_SIZE) before this
# check ever runs.
#   - MAX_ENTRIES=2000: a legitimate docx/xlsx/pptx/odt/epub — even a
#     large one with many embedded images/media/slides — very rarely
#     approaches this; it's a generous ceiling meant to catch a
#     deliberately constructed archive with an excessive entry count.
#   - MAX_UNCOMPRESSED_BYTES=250MB: a 25:1 expansion ceiling on a 10MB
#     input, well above what realistic Office document compression
#     ratios produce (typically 2-10x), while still bounding worst-case
#     memory/disk use during extraction to a small fraction of the
#     Fargate task's provisioned 4GB memory / 40GB ephemeral storage
#     (see lib/rag-engines/data-import/file-import-batch-job.ts).
#   - MAX_COMPRESSION_RATIO=100: classic zip-bomb constructions achieve
#     ratios in the thousands-to-millions; legitimate office documents
#     essentially never exceed ~20x. 100x leaves comfortable headroom
#     for legitimate files while still catching bomb-shaped archives.
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 100


def check_zip_bomb_risk(
    content: bytes,
    max_entries: int = MAX_ZIP_ENTRIES,
    max_uncompressed_bytes: int = MAX_ZIP_UNCOMPRESSED_BYTES,
    max_compression_ratio: int = MAX_ZIP_COMPRESSION_RATIO,
) -> ValidationResult:
    """Structural checks against a ZIP-based document (docx/xlsx/pptx/
    odt/epub) using only the standard library's `zipfile` module — no
    new dependency. Reads only the archive's central directory (entry
    metadata), never extracts or decompresses entry contents.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return ValidationResult(False, "File is not a valid ZIP-based document")

    infolist = zf.infolist()

    if len(infolist) > max_entries:
        return ValidationResult(
            False,
            f"Archive has too many entries ({len(infolist)} > {max_entries})",
        )

    total_uncompressed = sum(info.file_size for info in infolist)
    total_compressed = sum(info.compress_size for info in infolist) or 1

    if total_uncompressed > max_uncompressed_bytes:
        return ValidationResult(
            False,
            f"Archive's uncompressed size ({total_uncompressed} bytes) "
            f"exceeds the safe limit ({max_uncompressed_bytes} bytes)",
        )

    ratio = total_uncompressed / total_compressed
    if ratio > max_compression_ratio:
        return ValidationResult(
            False,
            f"Archive's compression ratio ({ratio:.1f}x) exceeds the "
            f"safe threshold ({max_compression_ratio}x)",
        )

    for info in infolist:
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or ".." in name:
            return ValidationResult(
                False, f"Archive contains an entry with an unsafe path: {name!r}"
            )

    return ValidationResult(True)


# --- Top-level orchestration -------------------------------------------


def validate_upload(filename: str, content: bytes) -> ValidationResult:
    """Single entry point combining filename safety and content/
    extension consistency — the function upload-handler/index.py calls
    for every uploaded file before proceeding with ingestion.
    """
    filename_result = validate_filename(filename)
    if not filename_result.valid:
        return filename_result
    return check_content_matches_extension(filename, content)
