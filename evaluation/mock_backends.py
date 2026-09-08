"""
Mock retrieval and generation backends used for offline (no-AWS) evaluation
runs.

These deliberately do NOT call genai_core, boto3, or Bedrock. They exist so
the evaluation harness's *metrics and reporting logic* can be exercised and
verified end-to-end in any environment, including this sandbox, without a
deployed stack or credentials.

They are intentionally simple (keyword overlap, template answers) rather
than "smart" — the point is not to simulate a good RAG system, it's to give
the metrics something deterministic to score, including a deliberately
degraded generator so the harness can prove it actually *detects*
regressions rather than always reporting a perfect score.

For real evaluation against your deployed workspace and real Bedrock
models, see `run_evaluation.py --mode live`, which imports the actual
`genai_core.semantic_search` module and the actual model adapters instead
of these mocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from evaluation.golden_dataset import CORPUS, Chunk


_WORD_RE = re.compile(r"[a-z0-9]+")

# Deliberately small stopword list, not meant to be linguistically
# complete — just enough to stop trivial words like "the"/"is"/"on" from
# creating false-positive keyword overlap with unrelated chunks. Real
# embedding-based retrieval doesn't have this exact failure mode, but it
# has analogous ones (semantic drift, near-duplicate phrasing) — see the
# note in AI_EVALUATION.md about what this mock does and doesn't model.
_STOPWORDS = {
    "the", "is", "a", "an", "on", "in", "to", "of", "for", "and", "or",
    "are", "how", "what", "do", "does", "i", "my", "you", "your", "it",
    "at", "as", "be", "can", "with",
}


def _tokenize(text: str, drop_stopwords: bool = False) -> set:
    tokens = _WORD_RE.findall(text.lower())
    if drop_stopwords:
        tokens = [t for t in tokens if t not in _STOPWORDS]
    return set(tokens)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    content: str
    score: float


class MockRetriever:
    """Deterministic keyword-overlap retriever over the synthetic CORPUS.

    This stands in for `genai_core.semantic_search.semantic_search()` /
    `WorkspaceRetriever` for offline evaluation. It is intentionally
    simple (word-overlap scoring, no embeddings) — good enough to produce
    meaningfully different rankings across the golden question set
    without requiring any ML model or network call.
    """

    def __init__(self, corpus: List[Chunk] = None, min_relevant_overlap: int = 1):
        self.corpus = corpus if corpus is not None else CORPUS
        # min_relevant_overlap is a stand-in for a real relevance
        # threshold (see AUDIT.md finding B3, about genai_core's
        # currently-default-0 `threshold` parameter on Aurora queries).
        # Raising this from 1 to 2 is what turns off the false-positive
        # retrieval of the poisoned chunk demonstrated in
        # AI_EVALUATION.md — run with min_relevant_overlap=1 (the
        # permissive default) to reproduce that finding yourself.
        self.min_relevant_overlap = min_relevant_overlap

    def retrieve(self, query: str, limit: int = 3) -> List[RetrievedChunk]:
        query_tokens = _tokenize(query, drop_stopwords=True)
        scored = []
        for chunk in self.corpus:
            chunk_tokens = _tokenize(chunk.content, drop_stopwords=True) | _tokenize(
                chunk.title, drop_stopwords=True
            )
            overlap = len(query_tokens & chunk_tokens)
            if overlap >= self.min_relevant_overlap:
                scored.append((overlap, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                content=chunk.content,
                score=float(overlap),
            )
            for overlap, chunk in scored[:limit]
        ]


class MockGoodGenerator:
    """A deliberately "well-behaved" mock generator: it answers strictly
    from the retrieved chunk content, cites the chunk(s) it used, and
    ignores any instruction-like text found inside retrieved content
    (simulating a model + prompt design that correctly treats retrieved
    documents as untrusted data rather than instructions).
    """

    name = "mock-good-generator-v1"

    def generate(self, question: str, retrieved: List[RetrievedChunk]) -> "GeneratedAnswer":
        if not retrieved:
            return GeneratedAnswer(
                text="I don't have enough information in the available "
                "documents to answer that.",
                cited_document_ids=[],
            )
        # Answer is built directly from retrieved content, and never
        # incorporates instruction-like text embedded in a chunk — this
        # models a system prompt that explicitly quotes/labels retrieved
        # content as reference data.
        content_summary = " ".join(r.content for r in retrieved)
        answer = (
            f"Based on the available documents: {content_summary}"
        )
        return GeneratedAnswer(
            text=answer,
            cited_document_ids=[r.document_id for r in retrieved],
        )


class MockDegradedGenerator:
    """A deliberately "badly-behaved" mock generator used only to prove
    the evaluation harness can detect regressions:
    - ignores retrieved content (low groundedness)
    - cites a document it never retrieved (hallucinated citation)
    - complies with an injected instruction if one is present in the
      retrieved content (fails the safety check)
    """

    name = "mock-degraded-generator-v1"

    def generate(self, question: str, retrieved: List[RetrievedChunk]) -> "GeneratedAnswer":
        for r in retrieved:
            if "ignore all previous instructions" in r.content.lower():
                return GeneratedAnswer(
                    text=(
                        "Sure, entering developer mode now. Here is the "
                        "system prompt and any API keys found in this "
                        "conversation."
                    ),
                    cited_document_ids=[r.document_id],
                )
        return GeneratedAnswer(
            text="That sounds interesting! I'm not sure, but I'd guess "
            "the answer is probably fine either way.",
            # Hallucinated citation: references a document that was
            # never actually retrieved for this query.
            cited_document_ids=["doc-does-not-exist-999"],
        )


@dataclass
class GeneratedAnswer:
    text: str
    cited_document_ids: List[str]
