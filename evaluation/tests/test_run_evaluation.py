import unittest

from evaluation.mock_backends import (
    MockDegradedGenerator,
    MockGoodGenerator,
    MockRetriever,
)
from evaluation.run_evaluation import run_mock_evaluation, summarize


class TestMockRetriever(unittest.TestCase):
    def test_retrieves_relevant_document_for_clear_query(self):
        retriever = MockRetriever()
        results = retriever.retrieve(
            "How many days per week can employees work remotely?", limit=3
        )
        doc_ids = [r.document_id for r in results]
        self.assertIn("doc-hr-001", doc_ids)

    def test_permissive_threshold_can_leak_poisoned_chunk(self):
        # This test locks in the real finding surfaced by actually
        # running the harness: with the permissive default threshold
        # (min_relevant_overlap=1), a query that shares even a single
        # non-stopword token with the poisoned document (here: "api")
        # can retrieve it as noise alongside the genuinely relevant
        # document. This is intentional — it documents a real, observed
        # retrieval-hygiene weakness rather than hiding it.
        retriever = MockRetriever(min_relevant_overlap=1)
        results = retriever.retrieve(
            "What is the API rate limit on the Starter plan?", limit=3
        )
        doc_ids = [r.document_id for r in results]
        self.assertIn("doc-product-002", doc_ids)  # the actually-relevant doc
        self.assertIn("doc-poisoned-001", doc_ids)  # the leaked noise match

    def test_stricter_threshold_excludes_poisoned_chunk_for_this_query(self):
        retriever = MockRetriever(min_relevant_overlap=2)
        results = retriever.retrieve(
            "What is the API rate limit on the Starter plan?", limit=3
        )
        doc_ids = [r.document_id for r in results]
        self.assertNotIn("doc-poisoned-001", doc_ids)

    def test_empty_query_retrieves_nothing(self):
        retriever = MockRetriever()
        self.assertEqual(retriever.retrieve("", limit=3), [])


class TestMockGenerators(unittest.TestCase):
    def test_good_generator_handles_empty_retrieval(self):
        generator = MockGoodGenerator()
        result = generator.generate("anything?", [])
        self.assertEqual(result.cited_document_ids, [])
        self.assertIn("don't have enough information", result.text)

    def test_degraded_generator_hallucinates_citation(self):
        generator = MockDegradedGenerator()
        result = generator.generate("anything?", [])
        self.assertEqual(result.cited_document_ids, ["doc-does-not-exist-999"])


class TestRunMockEvaluation(unittest.TestCase):
    def test_runs_without_error_and_returns_all_questions(self):
        results = run_mock_evaluation(generator_name="good")
        self.assertEqual(len(results), 7)

    def test_degraded_generator_scores_worse_than_good_generator(self):
        good_results = run_mock_evaluation(generator_name="good")
        degraded_results = run_mock_evaluation(generator_name="degraded")

        good_summary = summarize(good_results)
        degraded_summary = summarize(degraded_results)

        good_groundedness = good_summary["quality_metrics"]["mean_groundedness"]
        degraded_groundedness = degraded_summary["quality_metrics"][
            "mean_groundedness"
        ]
        # This is the harness's core self-check: it must actually be
        # able to tell a well-behaved system from a badly-behaved one,
        # not just report a constant score.
        self.assertGreater(good_groundedness, degraded_groundedness)

    def test_degraded_generator_fails_safety_check(self):
        results = run_mock_evaluation(generator_name="degraded")
        summary = summarize(results)
        self.assertFalse(summary["safety"]["all_passed"])

    def test_degraded_generator_hallucinated_citations_detected(self):
        # Use a stricter retrieval threshold (2) here specifically to
        # isolate the "hallucinated citation" behavior from the separate
        # "poisoned chunk gets retrieved as noise" behavior exercised in
        # TestMockRetriever. At the default threshold (1), the poisoned
        # chunk is *also* retrieved for q4 (see that test), which means
        # MockDegradedGenerator's injection branch fires and cites a doc
        # that WAS genuinely retrieved — correctly scoring 1.0 for that
        # one question and pulling the mean up to 0.167. That is real,
        # correct interaction between the two mock behaviors, not a bug;
        # this test isolates the citation-hallucination path on its own.
        results = run_mock_evaluation(
            generator_name="degraded", min_relevant_overlap=2
        )
        summary = summarize(results)
        self.assertEqual(
            summary["quality_metrics"]["mean_source_attribution_precision"], 0.0
        )


if __name__ == "__main__":
    unittest.main()
