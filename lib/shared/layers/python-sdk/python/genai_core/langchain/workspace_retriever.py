import time

from aws_lambda_powertools import Logger
import genai_core.semantic_search
from genai_core.observability import ai_metrics
from genai_core.security import prompt_guard
from typing import List
from langchain.callbacks.manager import CallbackManagerForRetrieverRun
from langchain.schema import BaseRetriever, Document

logger = Logger()


class WorkspaceRetriever(BaseRetriever):
    workspace_id: str
    documents_found: List[Document] = []

    def get_last_search_documents(self) -> List[Document]:
        return self.documents_found

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        logger.debug("SearchRequest", query=query)

        # A4: instrument the actual retrieval call — retrieval_start to
        # retrieval_end around the real semantic_search() invocation,
        # not a mock/fake duration. `engine` (aurora/opensearch/kendra/
        # bedrock_kb) comes from the result dict every backend already
        # includes for free (see genai_core/{aurora,opensearch,kendra,
        # bedrock_kb}/query.py) — a small, fixed, non-sensitive value,
        # never the workspace_id itself.
        retrieval_start = time.monotonic()
        result = genai_core.semantic_search.semantic_search(
            self.workspace_id, query, limit=3, full_response=False
        )
        retrieval_latency_ms = (time.monotonic() - retrieval_start) * 1000

        items = result.get("items", [])
        engine = result.get("engine", "unknown")
        ai_metrics.log_rag_retrieval_latency(engine, retrieval_latency_ms)
        ai_metrics.log_rag_documents_retrieved(engine, len(items))

        self.documents_found = [self._get_document(item) for item in items]
        return self.documents_found

    def _get_document(self, item):
        content = item["content"]
        content_complement = item.get("content_complement")

        page_content = content
        if content_complement:
            page_content = content_complement

        metadata = {
            "chunk_id": item["chunk_id"],
            "workspace_id": item["workspace_id"],
            "document_id": item["document_id"],
            "document_sub_id": item["document_sub_id"],
            "document_type": item["document_type"],
            "document_sub_type": item["document_sub_type"],
            "path": item["path"],
            "title": item["title"],
            "score": item["score"],
        }

        return Document(page_content=page_content, metadata=metadata)


class SanitizingWorkspaceRetriever(WorkspaceRetriever):
    """A2.1 — drop-in replacement for WorkspaceRetriever that ensures
    retrieved document content is always passed through the untrusted-
    content boundary protection (genai_core.security.prompt_guard) before
    it can reach an LLM prompt.

    Design, and why it's the safest integration point found:

    - `_get_relevant_documents()` is the one place, shared by every LLM
      adapter (Bedrock, SageMaker, OpenAI, etc. — see
      lib/model-interfaces/langchain/functions/request-handler/adapters),
      where retrieved chunk text is converted into the `Document` objects
      that langchain's `create_stuff_documents_chain` /
      `ConversationalRetrievalChain` interpolate into the `{context}`
      variable of the QA prompt. Fixing it here protects every adapter
      uniformly, without touching per-adapter prompt-construction code.
    - It deliberately does NOT change what `get_last_search_documents()`
      returns: `self.documents_found` (inherited from WorkspaceRetriever,
      populated by the `super()` call below) keeps the ORIGINAL,
      unwrapped `page_content`. That list is what the existing admin
      citation metadata (`base.py`'s `workspace_documents`) and A3's
      user-facing citations (`genai_core/citations.py`) are both built
      from — citations should show the real document text, not our
      defensive markup. Only the *copy* handed to the LLM chain is
      wrapped/escaped.
    - This preserves the existing model-adapter architecture exactly:
      adapters still just receive a `BaseRetriever`; only which concrete
      retriever class `base.py` instantiates changes (see
      ARCHITECTURE.md section 4/8 for the two call sites and the full
      trust-boundary diagram).
    """

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        # super() populates self.documents_found with the ORIGINAL,
        # unwrapped documents — that list is preserved as-is for
        # citations/metadata (see class docstring).
        original_documents = super()._get_relevant_documents(
            query, run_manager=run_manager
        )

        sanitized_documents = [
            Document(
                page_content=prompt_guard.wrap_untrusted_context(
                    doc.page_content, source_label=f"retrieved-document-{i + 1}"
                ),
                metadata=doc.metadata,
            )
            for i, doc in enumerate(original_documents)
        ]
        return sanitized_documents
