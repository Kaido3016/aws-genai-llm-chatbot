"""
A4 — AI-specific observability metrics.

DESIGN DECISION (read before adding a competing telemetry mechanism):
This codebase does NOT use AWS Lambda Powertools' `Metrics`/EMF anywhere
(confirmed: `aws_lambda_powertools.metrics` is not imported by any file
in this repository). What it DOES already have is a working, deployed
mechanism for turning structured log lines into CloudWatch custom
metrics: a Powertools `Logger.info(...)` call with a `metric_type` field,
picked up by a CDK `MetricFilter` in `lib/monitoring/index.ts`
(`addMetricFilter`, pre-existing, producing the `TokenUsage` metric).

Per the instruction to reuse existing observability infrastructure rather
than build a competing system, every function in this module follows
that SAME pattern — a structured `logger.info("AI Observability Metric",
metric_type=..., ...)` call — rather than introducing Powertools EMF.
`lib/monitoring/index.ts` is extended with new `MetricFilter`s that key
off these new `metric_type` values, exactly mirroring the existing
`TokenUsage` filter.

PRIVACY, BY FUNCTION SIGNATURE, NOT BY CONVENTION: every function here
only accepts the specific, narrow set of non-sensitive fields it's meant
to log (model id, mode, a small fixed engine-name string, numeric
counts/latencies, an exception CLASS NAME). There is no `**kwargs` or
generic dict parameter anywhere in this module, so a caller cannot
accidentally pass a prompt, a document, a session id, or a user id
through — the function signatures themselves are the allow-list, the
same defensive pattern used in genai_core.citations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from aws_lambda_powertools import Logger

logger = Logger()

_METRIC_MESSAGE = "AI Observability Metric"


def log_ai_request_success(model_id: str, mode: str, latency_ms: float) -> None:
    """Emitted once per successfully completed top-level AI request
    (i.e. one call to ModelAdapter.run() that did not raise)."""
    logger.info(
        _METRIC_MESSAGE,
        metric_type="ai_request_success",
        model=model_id,
        mode=mode,
        value=1,
    )
    log_ai_request_latency(model_id, mode, latency_ms)


def log_ai_request_failure(
    model_id: str, mode: str, latency_ms: Optional[float], error_type: str
) -> None:
    """Emitted once per top-level AI request that raised an exception.

    `error_type` MUST be an exception class name (e.g. via
    `type(exc).__name__`), never `str(exc)` — an exception message can
    contain fragments of the request (a botocore validation error can
    quote back part of the offending input, for example). Callers should
    never pass anything else here.
    """
    logger.info(
        _METRIC_MESSAGE,
        metric_type="ai_request_failure",
        model=model_id,
        mode=mode,
        value=1,
        error_type=error_type,
    )
    if latency_ms is not None:
        log_ai_request_latency(model_id, mode, latency_ms)


def log_ai_request_latency(model_id: str, mode: str, latency_ms: float) -> None:
    logger.info(
        _METRIC_MESSAGE,
        metric_type="ai_request_latency",
        model=model_id,
        mode=mode,
        value=round(latency_ms, 2),
    )


def log_input_tokens(model_id: str, input_tokens: Optional[int]) -> None:
    """Uses only the token count already returned by the model provider
    via langchain's usage_metadata (see LLMStartHandler in base.py) —
    never estimates or invents a token count."""
    if input_tokens is None:
        return
    logger.info(
        _METRIC_MESSAGE,
        metric_type="input_tokens",
        model=model_id,
        value=input_tokens,
    )


def log_output_tokens(model_id: str, output_tokens: Optional[int]) -> None:
    if output_tokens is None:
        return
    logger.info(
        _METRIC_MESSAGE,
        metric_type="output_tokens",
        model=model_id,
        value=output_tokens,
    )


def log_estimated_cost(model_id: str, cost_usd: Optional[Decimal]) -> None:
    """Skips emitting entirely when cost is unavailable (unknown model or
    missing token counts) — never emits a fabricated or zero-by-default
    value. See genai_core.observability.pricing for how cost_usd is
    computed and its verification status."""
    if cost_usd is None:
        return
    logger.info(
        _METRIC_MESSAGE,
        metric_type="estimated_cost_usd",
        model=model_id,
        value=float(cost_usd),
    )


def log_rag_retrieval_latency(engine: str, latency_ms: float) -> None:
    """`engine` is one of the small, fixed set of retrieval backend
    names this project already uses internally (aurora/opensearch/
    kendra/bedrock_kb — see genai_core.semantic_search) — never a
    workspace id or any other per-tenant identifier."""
    logger.info(
        _METRIC_MESSAGE,
        metric_type="rag_retrieval_latency",
        engine=engine,
        value=round(latency_ms, 2),
    )


def log_rag_documents_retrieved(engine: str, document_count: int) -> None:
    logger.info(
        _METRIC_MESSAGE,
        metric_type="rag_documents_retrieved",
        engine=engine,
        value=document_count,
    )
