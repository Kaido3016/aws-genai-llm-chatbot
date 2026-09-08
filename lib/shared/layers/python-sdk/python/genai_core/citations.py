"""
A3 — server-side construction of safe, user-facing citations.

Scope and design intent (read before modifying):

This module builds the ONLY representation of retrieved-document
information that is ever sent to non-admin users. It is deliberately a
strict allow-list, not a filter: the output dict is constructed field by
field from scratch — nothing from the input `document`/`metadata` is ever
copied wholesale or spread into the result. That means a forged or
unexpected key on the input (e.g. an attacker-controlled document record
with an extra `"isAdmin": true` or `"apiKey": "..."` field somehow present
in its metadata) simply cannot reach the output, because the output only
ever contains the four keys explicitly assigned below.

Two independent safety layers are applied to the fields that come from
untrusted document content/metadata:
  1. `build_safe_citations()` — used at WRITE time, from the ORIGINAL
     (unwrapped) documents returned by
     `SanitizingWorkspaceRetriever.get_last_search_documents()` — see
     that class's docstring for why citations must be built from this
     list, not from the sanitized copies handed to the LLM, and never
     from anything the model generated.
  2. `reapply_citation_allowlist()` — used at READ time (sessions.py),
     re-validating already-stored citation records before they're
     returned to a client. This guards against legacy/pre-A3 data or any
     future write path that might bypass `build_safe_citations`.

No network calls, no AWS SDK, no third-party dependencies — pure standard
library, same design choice as `genai_core.security.prompt_guard`.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Deliberately short — citations are meant to help a user verify an
# answer at a glance, not reproduce the source document. Hard character
# cap (not "truncate at the nearest word after this point"), so a single
# pathologically long token with no whitespace cannot bypass truncation.
MAX_SNIPPET_LENGTH = 240

# Only these document types represent a genuinely public, independently
# fetchable source. Everything else (uploaded files, Q&A pairs, etc.)
# never gets a sourceUrl, regardless of what its `path` metadata says —
# `path` for those types is an internal S3 key fragment, not a URL. See
# AUDIT.md and the A3 design writeup for how this was confirmed by
# tracing genai_core/documents.py and the aurora/kendra/opensearch
# delete.py modules, which combine `path` with `workspace_id` to form an
# S3 object key for exactly these non-public types.
_PUBLIC_SOURCE_DOCUMENT_TYPES = {"website", "rssfeed"}

_ALLOWED_URL_SCHEMES = {"http", "https"}

# The complete, exhaustive set of keys a citation is ever allowed to
# have. Used both to build citations field-by-field (never by copying an
# input dict) and to re-validate already-stored citation records at read
# time.
ALLOWED_CITATION_KEYS = {"documentTitle", "sourceUrl", "snippet", "citationIndex"}


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def neutralize_snippet_content(text: str) -> str:
    """HTML-escape retrieved text before it is ever sent to a client.

    The React frontend also escapes text nodes by default (it is not
    rendered via `dangerouslySetInnerHTML`), so in the current frontend
    this is a second, redundant layer. It is kept as the authoritative,
    server-side guarantee anyway — the same "belt and suspenders"
    reasoning as `genai_core.security.prompt_guard`'s delimiter escaping
    — because it makes the safety property hold regardless of frontend
    implementation details, and it is what makes the guarantee testable
    here without a browser.

    Trade-off, stated plainly: a snippet that legitimately contains
    literal `<`/`>`/`&` characters will show as HTML entities
    (`&lt;`/`&gt;`/`&amp;`) rather than being decoded back to the
    original characters by the frontend. This is an accepted, minor
    display artifact in exchange for a guarantee that holds regardless
    of the consuming client.
    """
    return html.escape(text, quote=True)


def truncate_snippet(text: str, max_length: int = MAX_SNIPPET_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _extract_safe_source_url(document_type: str, path: Any) -> Optional[str]:
    if document_type not in _PUBLIC_SOURCE_DOCUMENT_TYPES:
        return None
    if not isinstance(path, str) or not path:
        return None
    try:
        parsed = urlparse(path)
    except ValueError:
        return None
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        # Rejects javascript:, data:, file:, or malformed values —
        # including cases where metadata has been tampered with or is
        # simply malformed, not just genuinely malicious input.
        return None
    return path


def build_citation(document: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Build one allow-listed citation from one ORIGINAL retrieved
    document (the `{"page_content": ..., "metadata": {...}}` shape
    produced by base.py from `retriever.get_last_search_documents()`).

    Never raises on missing/malformed fields — defaults to safe, empty
    values instead, so one malformed document can't fail an entire
    answer's citations.
    """
    metadata = document.get("metadata") if isinstance(document, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}

    raw_title = _safe_str(metadata.get("title")).strip() or "Untitled source"
    raw_page_content = _safe_str(
        document.get("page_content") if isinstance(document, dict) else None
    )
    document_type = _safe_str(metadata.get("document_type"))
    path = metadata.get("path")

    snippet = neutralize_snippet_content(truncate_snippet(raw_page_content))
    source_url = _extract_safe_source_url(document_type, path)

    # Explicit, field-by-field construction — this IS the allow-list.
    # Do not change this to `**metadata` or similar; see module
    # docstring.
    return {
        "documentTitle": html.escape(raw_title, quote=True),
        "sourceUrl": source_url,
        "snippet": snippet,
        "citationIndex": index,
    }


def build_safe_citations(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the full list of safe citations for one answer, from the
    ORIGINAL retrieved documents (never from sanitized/wrapped copies,
    and never from model-generated text — see module docstring).
    """
    if not documents:
        return []
    citations = []
    for i, doc in enumerate(documents, start=1):
        try:
            citations.append(build_citation(doc, i))
        except Exception:
            # A malformed citation must never fail the whole response.
            continue
    return citations


def reapply_citation_allowlist(
    raw_citations: Any,
) -> List[Dict[str, Any]]:
    """Re-validate already-stored citation records before returning them
    to a client (used in sessions.py at read time).

    This is defense-in-depth against legacy/pre-A3 data or any future
    write path that might bypass `build_safe_citations` — it does NOT
    reconstruct citations from raw documents (it has no access to them at
    this point); it only strips anything outside the allow-list and
    re-validates `sourceUrl`.
    """
    if not isinstance(raw_citations, list):
        return []

    safe: List[Dict[str, Any]] = []
    for item in raw_citations:
        if not isinstance(item, dict):
            continue
        filtered = {k: v for k, v in item.items() if k in ALLOWED_CITATION_KEYS}

        source_url = filtered.get("sourceUrl")
        if source_url is not None:
            valid = False
            if isinstance(source_url, str):
                try:
                    parsed = urlparse(source_url)
                    valid = (
                        parsed.scheme in _ALLOWED_URL_SCHEMES
                        and bool(parsed.netloc)
                    )
                except ValueError:
                    valid = False
            filtered["sourceUrl"] = source_url if valid else None

        safe.append(filtered)
    return safe
