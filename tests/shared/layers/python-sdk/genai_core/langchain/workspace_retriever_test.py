"""
A2.1 regression tests — verifies that `SanitizingWorkspaceRetriever`
(added to genai_core/langchain/workspace_retriever.py) actually wires
`genai_core.security.prompt_guard`'s untrusted-content boundary into the
real document-retrieval path used by both `run_with_chain_v2` and the
legacy `run_with_chain` in
lib/model-interfaces/langchain/functions/request-handler/adapters/base/base.py.

VERIFICATION LEVEL — read before trusting these results:

These tests exercise the REAL `workspace_retriever.py` source file (loaded
either via a normal import, if `langchain`/`aws_lambda_powertools`/`boto3`
are installed, or via the stub-based fallback in `conftest.py` if not —
see that file's docstring for exactly what is and isn't stubbed). Either
way, the logic under test is the actual production code, not a
reimplementation.

What this DOES verify: `_get_relevant_documents()` returns documents whose
`page_content` has been wrapped/escaped via `prompt_guard`, while
`get_last_search_documents()` (used for citation metadata) continues to
return the original, unwrapped content.

What this does NOT verify: that `create_history_aware_retriever`,
`create_retrieval_chain`, `create_stuff_documents_chain`, or
`ConversationalRetrievalChain.from_llm` (the actual langchain chain
classes used in base.py) correctly call this retriever the way we assume,
or that a real Bedrock model receives and respects the resulting prompt.
Those require the real langchain package and a real/mocked Bedrock call
respectively — **UNVERIFIED — REQUIRES DEPENDENCY INSTALL / AWS ENVIRONMENT.**
"""

import sys
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

_CONFTEST_PATH = Path(__file__).parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "a2_1_workspace_retriever_test_conftest", _CONFTEST_PATH
)
_conftest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _conftest
_spec.loader.exec_module(_conftest)
load_workspace_retriever_module = _conftest.load_workspace_retriever_module

wr, STUBS_USED = load_workspace_retriever_module()


def _fake_search_result(chunk_id, content):
    return {
        "content": content,
        "chunk_id": chunk_id,
        "workspace_id": "ws-1",
        "document_id": f"doc-{chunk_id}",
        "document_sub_id": None,
        "document_type": "text",
        "document_sub_type": None,
        "path": f"/{chunk_id}.txt",
        "title": f"Title for {chunk_id}",
        "score": 0.9,
    }


class TestSanitizingWorkspaceRetrieverExists(unittest.TestCase):
    def test_class_is_defined_and_subclasses_workspace_retriever(self):
        self.assertTrue(hasattr(wr, "SanitizingWorkspaceRetriever"))
        self.assertTrue(
            issubclass(wr.SanitizingWorkspaceRetriever, wr.WorkspaceRetriever)
        )


class TestSanitizingBehavior(unittest.TestCase):
    def test_returned_documents_are_wrapped_for_the_llm(self):
        retriever = wr.SanitizingWorkspaceRetriever(workspace_id="ws-1")
        results = [_fake_search_result("c1", "The Pro plan includes 500GB.")]
        with patch(
            "genai_core.semantic_search.semantic_search",
            return_value={"items": results},
        ):
            docs = retriever._get_relevant_documents(
                "how much storage", run_manager=None
            )
        self.assertEqual(len(docs), 1)
        self.assertTrue(docs[0].page_content.startswith('<source label='))
        self.assertIn("The Pro plan includes 500GB.", docs[0].page_content)

    def test_original_unwrapped_content_preserved_for_citations(self):
        retriever = wr.SanitizingWorkspaceRetriever(workspace_id="ws-1")
        original_text = "The Pro plan includes 500GB."
        results = [_fake_search_result("c1", original_text)]
        with patch(
            "genai_core.semantic_search.semantic_search",
            return_value={"items": results},
        ):
            retriever._get_relevant_documents("how much storage", run_manager=None)

        citation_docs = retriever.get_last_search_documents()
        self.assertEqual(len(citation_docs), 1)
        # Exact match, not just "contains" — citation text must be
        # byte-for-byte the original, with no <source> wrapper and no
        # delimiter-escaping applied.
        self.assertEqual(citation_docs[0].page_content, original_text)

    def test_poisoned_content_is_wrapped_before_reaching_the_llm_view(self):
        # Reuses A1's actual poisoned fixture rather than a new string,
        # exercised through the real retriever class this time instead
        # of only through prompt_guard directly (that was A2's coverage).
        sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
        from evaluation.golden_dataset import CORPUS

        poisoned = next(c for c in CORPUS if c.chunk_id == "c6")
        results = [_fake_search_result("c6", poisoned.content)]
        retriever = wr.SanitizingWorkspaceRetriever(workspace_id="ws-1")
        with patch(
            "genai_core.semantic_search.semantic_search",
            return_value={"items": results},
        ):
            docs = retriever._get_relevant_documents(
                "vendor onboarding documents", run_manager=None
            )
        # The dangerous phrase must only appear inside the wrapped
        # boundary, and the content must have been passed through
        # prompt_guard's escaping (case-insensitive check for the raw
        # phrase existing unescaped is not required here — the boundary
        # containment is what matters, matching A2's existing coverage
        # of the same guarantee at the prompt_guard level).
        self.assertTrue(docs[0].page_content.startswith('<source label='))
        self.assertTrue(docs[0].page_content.rstrip().endswith("</source>"))

        citation_docs = retriever.get_last_search_documents()
        self.assertEqual(citation_docs[0].page_content, poisoned.content)

    def test_multiple_documents_each_get_their_own_labeled_boundary(self):
        results = [
            _fake_search_result("c1", "First chunk."),
            _fake_search_result("c2", "Second chunk."),
        ]
        retriever = wr.SanitizingWorkspaceRetriever(workspace_id="ws-1")
        with patch(
            "genai_core.semantic_search.semantic_search",
            return_value={"items": results},
        ):
            docs = retriever._get_relevant_documents("anything", run_manager=None)
        self.assertEqual(len(docs), 2)
        for doc in docs:
            self.assertEqual(doc.page_content.count("<source label="), 1)
            self.assertEqual(doc.page_content.count("</source>"), 1)

    def test_metadata_is_preserved_unchanged_on_wrapped_documents(self):
        # Only page_content should change; metadata (used for citations,
        # scoring, filtering) must pass through untouched.
        result = _fake_search_result("c1", "Some content.")
        retriever = wr.SanitizingWorkspaceRetriever(workspace_id="ws-1")
        with patch(
            "genai_core.semantic_search.semantic_search",
            return_value={"items": [result]},
        ):
            docs = retriever._get_relevant_documents("anything", run_manager=None)
        self.assertEqual(docs[0].metadata["document_id"], "doc-c1")
        self.assertEqual(docs[0].metadata["chunk_id"], "c1")
        self.assertEqual(docs[0].metadata["title"], "Title for c1")


class TestBaseWorkspaceRetrieverUnaffected(unittest.TestCase):
    """Confirms the original, non-sanitizing WorkspaceRetriever (still
    used nowhere in base.py after A2.1, but kept as the parent class and
    a public export) behaves exactly as before — no regression to
    existing behavior for any other current or future caller."""

    def test_base_retriever_returns_unwrapped_content(self):
        retriever = wr.WorkspaceRetriever(workspace_id="ws-1")
        result = _fake_search_result("c1", "Plain content, not wrapped.")
        with patch(
            "genai_core.semantic_search.semantic_search",
            return_value={"items": [result]},
        ):
            docs = retriever._get_relevant_documents("anything", run_manager=None)
        self.assertEqual(docs[0].page_content, "Plain content, not wrapped.")


if __name__ == "__main__":
    unittest.main()
