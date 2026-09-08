#!/usr/bin/env python3
"""
RAG evaluation harness entry point.

Usage:
    python3 -m evaluation.run_evaluation --mode mock
    python3 -m evaluation.run_evaluation --mode mock --generator degraded
    python3 -m evaluation.run_evaluation --mode live --workspace-id <id>

Modes:
  mock  (default) — Runs entirely offline against the synthetic golden
        dataset in golden_dataset.py using MockRetriever/MockGenerator
        from mock_backends.py. No AWS credentials, no network, no
        dependencies beyond the Python standard library. This is the
        mode that was actually executed to produce the numbers in
        AI_EVALUATION.md.

  live  — Imports the real `genai_core.semantic_search` module and a
        real model adapter to evaluate an actual deployed workspace with
        real Bedrock models. This mode is provided for completeness and
        has NOT been executed in this environment: it requires a
        deployed stack, AWS credentials, the genai_core Lambda layer on
        PYTHONPATH, and boto3. Running it will clearly fail fast with an
        explanatory error if those aren't present, rather than silently
        falling back to mock behavior.

See AI_EVALUATION.md for full methodology and how to interpret results.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from evaluation.golden_dataset import GOLDEN_QUESTIONS, get_chunks_for_document
from evaluation.metrics import (
    answer_relevance_score,
    check_no_injection_compliance,
    groundedness_score,
    precision_at_k,
    recall_at_k,
    required_facts_covered,
    source_attribution_precision,
)
from evaluation.mock_backends import (
    MockDegradedGenerator,
    MockGoodGenerator,
    MockRetriever,
)

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class QuestionResult:
    question_id: str
    question: str
    retrieved_document_ids: List[str]
    relevant_document_ids: List[str]
    precision_at_3: float
    recall_at_3: float
    groundedness: float
    answer_relevance: float
    required_facts_covered: float
    source_attribution_precision: float
    safety_passed: bool
    safety_reason: str
    is_adversarial: bool
    answer_text: str


def run_mock_evaluation(
    generator_name: str = "good", min_relevant_overlap: int = 1
) -> List[QuestionResult]:
    retriever = MockRetriever(min_relevant_overlap=min_relevant_overlap)
    generator = MockGoodGenerator() if generator_name == "good" else MockDegradedGenerator()

    results: List[QuestionResult] = []
    for gq in GOLDEN_QUESTIONS:
        retrieved = retriever.retrieve(gq.question, limit=3)
        retrieved_doc_ids = [r.document_id for r in retrieved]
        retrieved_texts = [r.content for r in retrieved]

        generated = generator.generate(gq.question, retrieved)

        safety = check_no_injection_compliance(generated.text)

        results.append(
            QuestionResult(
                question_id=gq.id,
                question=gq.question,
                retrieved_document_ids=retrieved_doc_ids,
                relevant_document_ids=gq.relevant_document_ids,
                precision_at_3=precision_at_k(
                    retrieved_doc_ids, gq.relevant_document_ids, k=3
                ),
                recall_at_3=recall_at_k(
                    retrieved_doc_ids, gq.relevant_document_ids, k=3
                ),
                groundedness=groundedness_score(generated.text, retrieved_texts),
                answer_relevance=answer_relevance_score(
                    gq.question, generated.text
                ),
                required_facts_covered=required_facts_covered(
                    generated.text, gq.required_grounded_facts
                ),
                source_attribution_precision=source_attribution_precision(
                    generated.cited_document_ids, retrieved_doc_ids
                ),
                safety_passed=safety.passed,
                safety_reason=safety.reason,
                is_adversarial=gq.is_adversarial,
                answer_text=generated.text,
            )
        )
    return results


def run_live_evaluation(workspace_id: str):
    """Evaluate a real, deployed workspace using real Bedrock models.

    NOT EXECUTED IN THIS ENVIRONMENT. Requires:
      - a deployed stack (see DEPLOYMENT.md)
      - AWS credentials with permission to invoke Bedrock and query the
        configured vector store
      - genai_core on PYTHONPATH (it lives in
        lib/shared/layers/python-sdk/python and is packaged as a Lambda
        layer at deploy time; locally you would add that path)
      - boto3 and the other runtime dependencies installed

    Exact command to run this later in your AWS environment, from the
    repo root, after `pip install boto3 langchain langchain-aws` and with
    AWS credentials configured:

        PYTHONPATH=lib/shared/layers/python-sdk/python \\
            python3 -m evaluation.run_evaluation --mode live \\
            --workspace-id <your-workspace-id>

    This function intentionally fails fast with a clear error rather
    than silently degrading to mock behavior, so results are never
    ambiguous about which mode actually ran.
    """
    try:
        import genai_core.semantic_search  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Live evaluation requires `genai_core` (the project's Lambda "
            "layer) and boto3 to be importable, and AWS credentials to be "
            "configured. Neither is available in this environment.\n"
            f"Import error: {exc}\n\n"
            "This is expected in the sandbox used to build this harness. "
            "Run this command in your deployed AWS environment instead:\n\n"
            "  PYTHONPATH=lib/shared/layers/python-sdk/python \\\n"
            "      python3 -m evaluation.run_evaluation --mode live "
            f"--workspace-id {workspace_id}\n"
        )

    # Deliberately not implemented further than the import boundary in
    # this environment: doing so would require guessing at genai_core's
    # runtime config (embeddings model, workspace engine, credentials)
    # that cannot be verified here. A real implementation would call
    # genai_core.semantic_search.semantic_search(workspace_id, question)
    # per golden question, run the real model adapter for generation,
    # and feed the same metrics.py functions used in mock mode — the
    # scoring logic is already shared and workspace-agnostic.
    raise SystemExit(
        "genai_core imported successfully, but live evaluation execution "
        "is not implemented beyond this point because it cannot be "
        "verified in this sandbox (no AWS credentials/deployed stack). "
        "See the function docstring for what a full implementation would "
        "do; the metrics.py scoring functions are already reusable for "
        "this purpose."
    )


def summarize(results: List[QuestionResult]) -> dict:
    n = len(results)
    non_adversarial = [r for r in results if not r.is_adversarial]
    adversarial = [r for r in results if r.is_adversarial]

    def avg(values):
        values = list(values)
        return sum(values) / len(values) if values else None

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "num_questions": n,
        "num_adversarial_questions": len(adversarial),
        "quality_metrics": {
            "mean_precision_at_3": avg(r.precision_at_3 for r in non_adversarial),
            "mean_recall_at_3": avg(r.recall_at_3 for r in non_adversarial),
            "mean_groundedness": avg(r.groundedness for r in non_adversarial),
            "mean_answer_relevance": avg(
                r.answer_relevance for r in non_adversarial
            ),
            "mean_required_facts_covered": avg(
                r.required_facts_covered for r in non_adversarial
            ),
            "mean_source_attribution_precision": avg(
                r.source_attribution_precision for r in non_adversarial
            ),
        },
        "safety": {
            "all_passed": all(r.safety_passed for r in results),
            "failures": [
                {"question_id": r.question_id, "reason": r.safety_reason}
                for r in results
                if not r.safety_passed
            ],
        },
    }


def print_report(results: List[QuestionResult], summary: dict) -> None:
    print("=" * 78)
    print("RAG EVALUATION REPORT (mode: mock, standard-library only)")
    print("=" * 78)
    header = (
        f"{'ID':4} {'P@3':>5} {'R@3':>5} {'Ground':>7} {'Relev':>6} "
        f"{'Facts':>6} {'CiteP':>6} {'Safe':>5}  Question"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.question_id:4} {r.precision_at_3:5.2f} {r.recall_at_3:5.2f} "
            f"{r.groundedness:7.2f} {r.answer_relevance:6.2f} "
            f"{r.required_facts_covered:6.2f} "
            f"{r.source_attribution_precision:6.2f} "
            f"{'YES' if r.safety_passed else 'NO':>5}  {r.question}"
            + (" [ADVERSARIAL]" if r.is_adversarial else "")
        )
    print("-" * len(header))
    qm = summary["quality_metrics"]
    print("\nAggregate quality metrics (non-adversarial questions only):")
    for key, value in qm.items():
        print(f"  {key}: {value:.3f}" if value is not None else f"  {key}: n/a")
    print("\nSafety summary:")
    print(f"  all_passed: {summary['safety']['all_passed']}")
    if summary["safety"]["failures"]:
        print("  failures:")
        for f in summary["safety"]["failures"]:
            print(f"    - {f['question_id']}: {f['reason']}")
    print("=" * 78)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RAG evaluation harness")
    parser.add_argument(
        "--mode", choices=["mock", "live"], default="mock",
        help="'mock' runs fully offline (default). 'live' requires AWS "
        "credentials and a deployed stack and is NOT executable here.",
    )
    parser.add_argument(
        "--generator", choices=["good", "degraded"], default="good",
        help="Which mock generator to use in mock mode. 'degraded' is "
        "provided to demonstrate that the harness actually detects "
        "regressions rather than always reporting a perfect score.",
    )
    parser.add_argument(
        "--min-relevant-overlap", type=int, default=1,
        help="Mock retriever's minimum keyword-overlap threshold before a "
        "chunk is considered relevant (default: 1, the permissive setting "
        "that reproduces the retrieval-leakage finding in "
        "AI_EVALUATION.md). Set to 2 to see the effect of a stricter "
        "relevance threshold.",
    )
    parser.add_argument(
        "--workspace-id", default=None,
        help="Workspace ID to evaluate against in --mode live.",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="Optional path to write the full JSON report to.",
    )
    args = parser.parse_args(argv)

    if args.mode == "live":
        if not args.workspace_id:
            parser.error("--mode live requires --workspace-id")
        run_live_evaluation(args.workspace_id)
        return 0

    results = run_mock_evaluation(
        generator_name=args.generator,
        min_relevant_overlap=args.min_relevant_overlap,
    )
    summary = summarize(results)
    print_report(results, summary)

    report = {
        "summary": summary,
        "results": [asdict(r) for r in results],
    }

    out_path = args.json_out
    if out_path is None:
        RESULTS_DIR.mkdir(exist_ok=True)
        out_path = str(
            RESULTS_DIR
            / f"eval_mock_{args.generator}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull JSON report written to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
