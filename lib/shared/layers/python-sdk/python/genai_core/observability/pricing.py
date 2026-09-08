"""
A4 — versioned Bedrock pricing configuration, kept deliberately separate
from application logic (genai_core.observability.ai_metrics) per the
brief: "pricing configuration/table... kept separate from application
logic".

==============================================================================
PRICING SOURCE AND VERIFICATION STATUS — READ BEFORE TRUSTING ANY NUMBER
==============================================================================
This table now includes ONE entry fetched and directly quoted from AWS's
own live pricing page (https://aws.amazon.com/bedrock/pricing/), and
several other entries that are still third-party-aggregated and
explicitly marked unverified. Do not assume the whole table has the same
confidence level — check each entry's `verified` flag and `source`.

VERIFIED entry (fetched 2026-09-06 directly from aws.amazon.com/bedrock/pricing):
  Anthropic's own published table, under "Models with extended access",
  lists on-demand pricing for two SKUs:
    "Claude 3.5 Sonnet (Public Extended Access, Effective 1 Dec 2025)"
    "Claude 3.5 Sonnet v2 (Public Extended Access, Effective 1 Dec 2025)"
  Regions: US East (N. Virginia), US East (Ohio), US West (Oregon),
  Europe (Frankfurt), Europe (Ireland), Europe (Zurich), Europe (Paris)
  (v2 additionally listed for the three US regions specifically).
  On-Demand: $6.00 per 1M input tokens / $30.00 per 1M output tokens.
  Batch: $3.00 per 1M input / $15.00 per 1M output.
  IMPORTANT CAVEAT: this is the "Public Extended Access" rate — a premium
  rate AWS charges for continued access to this now-superseded model
  generation, not necessarily representative of a *current-generation*
  Sonnet model's standard on-demand rate. It is verified as-quoted, but
  scoped narrowly to Claude 3.5 Sonnet / 3.5 Sonnet v2 specifically — the
  pattern below does NOT match newer Sonnet model ids, precisely so this
  premium rate isn't silently misapplied to a different, possibly
  cheaper, current model.

UNVERIFIED entries: AWS's live pricing page renders its main
current-generation model tables (covering newer Claude Sonnet/Haiku/Opus
versions) via client-side JavaScript that this environment's fetch tool
did not capture as text — only the "extended access" legacy-model table
above came through as plain text. For every other model family, the
figures below are aggregated from third-party pricing-tracking sites
(searched 2026-09-06), cross-checked for rough agreement across sources,
and are explicitly marked `verified=False`. **Do not use unverified
entries for real financial decisions, cost forecasting, or billing
reconciliation without confirming them against the official AWS page
yourself** — e.g. by selecting your actual model + region in the
provider dropdown at https://aws.amazon.com/bedrock/pricing/.

Region/tier assumption for ALL entries in this table: On-Demand
(Standard) tier, no Provisioned Throughput, no Batch discount, no
cross-region inference surcharge, no prompt-cache discount — i.e. the
simplest, highest-rate on-demand case. If you use Batch, Flex, Priority,
cross-region routing, or prompt caching, your real per-token rate will
differ from what's modeled here; this is documented as a limitation, not
silently ignored.

The `EstimatedAICost` metric this powers is exactly that — an estimate
for relative/operational tracking (e.g. "did cost per request spike
after a prompt change"), never a substitute for your actual AWS bill.

If you confirm a currently-unverified entry against the official page,
update its `verified` flag to True, add the exact quoted text to
`source`, and set `verified_date`.
==============================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

# Bump this whenever entries change, so emitted cost metrics/logs can be
# correlated with the pricing assumptions that produced them.
PRICING_TABLE_VERSION = "2026-09-06-v2-one-entry-verified"

_THIRD_PARTY_SOURCE_NOTE = (
    "Aggregated from third-party pricing-tracking sites, searched "
    "2026-09-06 (bacancytechnology.com, cloudforecast.io, "
    "pecollective.com, go-cloud.io); AWS's own pricing page did not "
    "render this model's current-generation table as fetchable text in "
    "this environment. NOT independently confirmed against "
    "https://aws.amazon.com/bedrock/pricing/. Confirm before production use."
)

_AWS_VERIFIED_SOURCE_NOTE = (
    "Directly quoted from https://aws.amazon.com/bedrock/pricing/ "
    "(fetched 2026-09-06), Anthropic section, 'Models with extended "
    "access' table: On-Demand $6.00 / 1M input tokens, $30.00 / 1M "
    "output tokens. This is the 'Public Extended Access' premium rate "
    "for this specific, superseded model generation — see module "
    "docstring caveat before assuming it applies to any other model."
)


@dataclass(frozen=True)
class ModelPricing:
    # Regex matched against the Bedrock model id (case-insensitive) —
    # model ids in this codebase are resolved dynamically at deploy time
    # (via Bedrock's ListFoundationModels), not hardcoded, so pricing is
    # matched by family pattern rather than an exact id string.
    model_pattern: str
    input_price_per_1k_tokens_usd: Decimal
    output_price_per_1k_tokens_usd: Decimal
    verified: bool
    source: str
    # Pricing tier/region this entry assumes — see module docstring.
    pricing_unit_assumption: str = (
        "On-Demand (Standard tier), no Batch/Provisioned Throughput/"
        "cross-region/prompt-cache discount, US East (N. Virginia)"
    )
    verified_date: Optional[str] = None


# Deliberately a small, illustrative set covering the model families most
# likely to be used with this project (Anthropic Claude on Bedrock, given
# the project's own default configuration) — not an exhaustive catalog of
# every Bedrock model. Add entries as needed; see module docstring for
# the verification requirement before trusting a new entry.
PRICING_TABLE: List[ModelPricing] = [
    # VERIFIED — directly quoted from aws.amazon.com/bedrock/pricing.
    # Narrow pattern deliberately: matches ONLY "3-5-sonnet" ids (however
    # Bedrock formats the dash/dot), not other/newer Sonnet generations,
    # so this specific "extended access" premium rate can't be
    # misapplied to a different, unverified model.
    ModelPricing(
        model_pattern=r"anthropic\.claude-3[-.]5-sonnet",
        input_price_per_1k_tokens_usd=Decimal("0.006"),
        output_price_per_1k_tokens_usd=Decimal("0.030"),
        verified=True,
        source=_AWS_VERIFIED_SOURCE_NOTE,
        pricing_unit_assumption=(
            "On-Demand, 'Public Extended Access' rate effective 1 Dec "
            "2025. Regions: US East (N. Virginia), US East (Ohio), US "
            "West (Oregon), Europe (Frankfurt/Ireland/Zurich/Paris)."
        ),
        verified_date="2026-09-06",
    ),
    # UNVERIFIED — see module docstring and _THIRD_PARTY_SOURCE_NOTE.
    ModelPricing(
        model_pattern=r"anthropic\.claude.*opus",
        input_price_per_1k_tokens_usd=Decimal("0.005"),
        output_price_per_1k_tokens_usd=Decimal("0.025"),
        verified=False,
        source=_THIRD_PARTY_SOURCE_NOTE,
    ),
    # UNVERIFIED, and deliberately excludes 3-5-sonnet (handled by the
    # verified entry above) via a negative lookahead — covers other/newer
    # Sonnet generations only.
    ModelPricing(
        model_pattern=r"anthropic\.claude-(?!3[-.]5-sonnet)[^.]*sonnet",
        input_price_per_1k_tokens_usd=Decimal("0.003"),
        output_price_per_1k_tokens_usd=Decimal("0.015"),
        verified=False,
        source=_THIRD_PARTY_SOURCE_NOTE,
    ),
    ModelPricing(
        model_pattern=r"anthropic\.claude.*haiku",
        input_price_per_1k_tokens_usd=Decimal("0.001"),
        output_price_per_1k_tokens_usd=Decimal("0.005"),
        verified=False,
        source=_THIRD_PARTY_SOURCE_NOTE,
    ),
]


def get_pricing_for_model(model_id: Optional[str]) -> Optional[ModelPricing]:
    """Look up pricing for a model id by family pattern.

    Returns None (never a guessed default) when no pattern matches — an
    unrecognized model should result in "cost unavailable", not a
    plausible-looking wrong number.
    """
    if not model_id or not isinstance(model_id, str):
        return None
    for entry in PRICING_TABLE:
        if re.search(entry.model_pattern, model_id, re.IGNORECASE):
            return entry
    return None


def estimate_cost_usd(
    model_id: Optional[str],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Optional[Decimal]:
    """Deterministic, testable cost estimate from real token counts.

    Returns None (not zero, not a guess) when pricing is unavailable for
    the model or token counts are missing — the caller must not emit an
    EstimatedAICost metric in that case. Never invents a token count.
    """
    pricing = get_pricing_for_model(model_id)
    if pricing is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None
    if not isinstance(input_tokens, (int, float)) or not isinstance(
        output_tokens, (int, float)
    ):
        return None
    if input_tokens < 0 or output_tokens < 0:
        return None

    input_cost = (Decimal(input_tokens) / Decimal(1000)) * (
        pricing.input_price_per_1k_tokens_usd
    )
    output_cost = (Decimal(output_tokens) / Decimal(1000)) * (
        pricing.output_price_per_1k_tokens_usd
    )
    return input_cost + output_cost
