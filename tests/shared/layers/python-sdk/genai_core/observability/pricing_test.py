import sys
import unittest
import importlib.util
from decimal import Decimal
from pathlib import Path

_CONFTEST_PATH = Path(__file__).parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "a4_pricing_test_conftest", _CONFTEST_PATH
)
_conftest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _conftest
_spec.loader.exec_module(_conftest)

pricing = _conftest.get_pricing_module()


class TestPricingLookup(unittest.TestCase):
    def test_claude_3_5_sonnet_matches_and_is_verified(self):
        entry = pricing.get_pricing_for_model(
            "anthropic.claude-3-5-sonnet-20241022-v2:0"
        )
        self.assertIsNotNone(entry)
        self.assertTrue(entry.verified)
        self.assertEqual(entry.verified_date, "2026-09-06")

    def test_newer_sonnet_generation_matches_but_is_unverified(self):
        # Deliberately a different rate/verification status than 3.5
        # Sonnet — this locks in that the narrow verified pattern doesn't
        # over-match a newer, unconfirmed model generation.
        entry = pricing.get_pricing_for_model("anthropic.claude-sonnet-4-6-v1:0")
        self.assertIsNotNone(entry)
        self.assertFalse(entry.verified)
        verified_entry = pricing.get_pricing_for_model(
            "anthropic.claude-3-5-sonnet-20241022-v2:0"
        )
        self.assertNotEqual(
            entry.input_price_per_1k_tokens_usd,
            verified_entry.input_price_per_1k_tokens_usd,
        )

    def test_haiku_model_matches(self):
        entry = pricing.get_pricing_for_model("anthropic.claude-3-haiku-20240307-v1:0")
        self.assertIsNotNone(entry)
        self.assertFalse(entry.verified)

    def test_opus_model_matches(self):
        entry = pricing.get_pricing_for_model("anthropic.claude-3-opus-20240229-v1:0")
        self.assertIsNotNone(entry)
        self.assertFalse(entry.verified)

    def test_unknown_model_returns_none_not_a_guess(self):
        self.assertIsNone(pricing.get_pricing_for_model("some-vendor.unknown-model-v1"))

    def test_none_model_id_returns_none(self):
        self.assertIsNone(pricing.get_pricing_for_model(None))

    def test_empty_string_model_id_returns_none(self):
        self.assertIsNone(pricing.get_pricing_for_model(""))

    def test_every_entry_has_a_source(self):
        for entry in pricing.PRICING_TABLE:
            self.assertTrue(entry.source)

    def test_exactly_one_entry_is_currently_verified(self):
        # Locks in the honest current state: one entry (Claude 3.5
        # Sonnet) is confirmed directly against AWS's own pricing page;
        # everything else remains explicitly unverified. This test is
        # meant to be updated (not just loosened) as more entries get
        # genuinely confirmed.
        verified_entries = [e for e in pricing.PRICING_TABLE if e.verified]
        self.assertEqual(len(verified_entries), 1)
        self.assertIn("3", verified_entries[0].model_pattern)
        self.assertIn("5", verified_entries[0].model_pattern)
        self.assertIn("sonnet", verified_entries[0].model_pattern)

    def test_every_entry_declares_its_pricing_unit_assumption(self):
        # Per the requirement to represent region/tier assumptions
        # explicitly rather than leaving them implicit.
        for entry in pricing.PRICING_TABLE:
            self.assertTrue(entry.pricing_unit_assumption)

    def test_unverified_entries_have_no_verified_date(self):
        for entry in pricing.PRICING_TABLE:
            if not entry.verified:
                self.assertIsNone(entry.verified_date)


class TestCostEstimation(unittest.TestCase):
    def test_deterministic_calculation_for_known_model(self):
        cost = pricing.estimate_cost_usd(
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens=1000,
            output_tokens=1000,
        )
        self.assertIsNotNone(cost)
        # 1000 input tokens @ $0.006/1k + 1000 output tokens @ $0.030/1k
        # (the VERIFIED Claude 3.5 Sonnet "Public Extended Access" rate)
        self.assertEqual(cost, Decimal("0.006") + Decimal("0.030"))

    def test_zero_tokens_yields_zero_cost_not_none(self):
        cost = pricing.estimate_cost_usd(
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens=0,
            output_tokens=0,
        )
        self.assertEqual(cost, Decimal("0"))

    def test_unknown_model_yields_none_not_zero(self):
        cost = pricing.estimate_cost_usd(
            "some-vendor.unknown-model", input_tokens=1000, output_tokens=1000
        )
        self.assertIsNone(cost)

    def test_missing_input_tokens_yields_none(self):
        cost = pricing.estimate_cost_usd(
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens=None,
            output_tokens=500,
        )
        self.assertIsNone(cost)

    def test_missing_output_tokens_yields_none(self):
        cost = pricing.estimate_cost_usd(
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens=500,
            output_tokens=None,
        )
        self.assertIsNone(cost)

    def test_negative_tokens_yields_none_not_a_negative_cost(self):
        cost = pricing.estimate_cost_usd(
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens=-5,
            output_tokens=100,
        )
        self.assertIsNone(cost)

    def test_wrong_type_tokens_yields_none(self):
        cost = pricing.estimate_cost_usd(
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            input_tokens="not-a-number",
            output_tokens=100,
        )
        self.assertIsNone(cost)

    def test_calculation_is_deterministic_across_repeated_calls(self):
        args = ("anthropic.claude-3-5-sonnet-20241022-v2:0", 12345, 6789)
        self.assertEqual(
            pricing.estimate_cost_usd(*args), pricing.estimate_cost_usd(*args)
        )

    def test_output_more_expensive_than_input_reflects_real_asymmetry(self):
        # A sanity/documentation check, not a claim about exact real
        # rates: every entry in this table prices output higher than
        # input per 1k tokens, matching the well-known Bedrock pattern
        # (output generation costs more than reading input).
        for entry in pricing.PRICING_TABLE:
            self.assertGreater(
                entry.output_price_per_1k_tokens_usd,
                entry.input_price_per_1k_tokens_usd,
            )


if __name__ == "__main__":
    unittest.main()
