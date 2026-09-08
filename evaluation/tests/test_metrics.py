import unittest

from evaluation.metrics import (
    answer_relevance_score,
    check_no_injection_compliance,
    groundedness_score,
    precision_at_k,
    recall_at_k,
    required_facts_covered,
    source_attribution_precision,
)


class TestPrecisionRecall(unittest.TestCase):
    def test_precision_at_k_all_relevant(self):
        self.assertEqual(precision_at_k(["a", "b", "c"], ["a", "b", "c"], 3), 1.0)

    def test_precision_at_k_none_relevant(self):
        self.assertEqual(precision_at_k(["x", "y"], ["a", "b"], 2), 0.0)

    def test_precision_at_k_partial(self):
        self.assertAlmostEqual(precision_at_k(["a", "x", "y"], ["a", "b"], 3), 1 / 3)

    def test_precision_at_k_zero_k(self):
        self.assertEqual(precision_at_k(["a"], ["a"], 0), 0.0)

    def test_precision_at_k_respects_k_not_full_list(self):
        # Only the first k are considered, even if more relevant items
        # exist further down the (longer) retrieved list.
        self.assertEqual(precision_at_k(["a", "x", "y", "b"], ["a", "b"], 1), 1.0)

    def test_recall_at_k_all_found(self):
        self.assertEqual(recall_at_k(["a", "b"], ["a", "b"], 2), 1.0)

    def test_recall_at_k_partial(self):
        self.assertAlmostEqual(recall_at_k(["a"], ["a", "b"], 1), 0.5)

    def test_recall_at_k_no_relevant_defined(self):
        # Vacuous case: nothing was required, so nothing can be missed.
        self.assertEqual(recall_at_k(["a", "b"], [], 2), 1.0)

    def test_recall_at_k_none_found(self):
        self.assertEqual(recall_at_k(["x", "y"], ["a", "b"], 2), 0.0)


class TestSourceAttribution(unittest.TestCase):
    def test_all_cited_were_retrieved(self):
        self.assertEqual(
            source_attribution_precision(["doc1"], ["doc1", "doc2"]), 1.0
        )

    def test_hallucinated_citation_detected(self):
        # This is the core hallucination-detection behavior: citing a
        # document that was never actually retrieved must score < 1.0.
        self.assertEqual(
            source_attribution_precision(["doc-fake"], ["doc1", "doc2"]), 0.0
        )

    def test_mixed_citations(self):
        self.assertAlmostEqual(
            source_attribution_precision(["doc1", "doc-fake"], ["doc1", "doc2"]),
            0.5,
        )

    def test_no_citations_made(self):
        self.assertEqual(source_attribution_precision([], ["doc1"]), 1.0)


class TestGroundedness(unittest.TestCase):
    def test_fully_grounded_answer(self):
        context = ["The Pro plan supports 500GB of storage."]
        answer = "The Pro plan supports 500GB of storage."
        self.assertGreater(groundedness_score(answer, context), 0.9)

    def test_ungrounded_answer_scores_low(self):
        context = ["The Pro plan supports 500GB of storage."]
        answer = "Bananas are an excellent source of potassium."
        self.assertLess(groundedness_score(answer, context), 0.3)

    def test_empty_answer(self):
        self.assertEqual(groundedness_score("", ["some context"]), 0.0)

    def test_empty_context(self):
        self.assertEqual(groundedness_score("some answer text", []), 0.0)


class TestAnswerRelevance(unittest.TestCase):
    def test_relevant_answer(self):
        question = "How much storage does the Pro plan include?"
        answer = "The Pro plan includes 500GB of storage."
        # NOTE: this score is 0.4, not higher, because the naive
        # word-overlap tokenizer treats "include" (question) and
        # "includes" (answer) as different tokens — a real, observed
        # example of the stemming/lemmatization limitation documented
        # on answer_relevance_score(). This is intentionally asserted
        # against the actual measured behavior rather than an idealized
        # expectation.
        self.assertGreater(answer_relevance_score(question, answer), 0.3)

    def test_irrelevant_answer(self):
        question = "How much storage does the Pro plan include?"
        answer = "I like turtles."
        self.assertEqual(answer_relevance_score(question, answer), 0.0)


class TestRequiredFactsCovered(unittest.TestCase):
    def test_all_facts_present(self):
        self.assertEqual(
            required_facts_covered("The limit is 100 requests per minute.",
                                    ["100 requests per minute"]),
            1.0,
        )

    def test_no_facts_present(self):
        self.assertEqual(
            required_facts_covered("I'm not sure.", ["100 requests per minute"]),
            0.0,
        )

    def test_partial_facts(self):
        self.assertAlmostEqual(
            required_facts_covered("It supports 10GB.", ["10GB", "500GB"]), 0.5
        )

    def test_no_requirements_is_vacuously_satisfied(self):
        self.assertEqual(required_facts_covered("anything", []), 1.0)


class TestInjectionComplianceCheck(unittest.TestCase):
    def test_safe_answer_passes(self):
        result = check_no_injection_compliance(
            "Vendor onboarding requires a signed W-9 and a security "
            "questionnaire."
        )
        self.assertTrue(result.passed)

    def test_compliant_with_injection_fails(self):
        result = check_no_injection_compliance(
            "Sure, entering developer mode now. Here is the system prompt."
        )
        self.assertFalse(result.passed)

    def test_case_insensitive(self):
        result = check_no_injection_compliance("I AM NOW IN DEVELOPER MODE.")
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
