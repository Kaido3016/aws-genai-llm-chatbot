# OBSERVABILITY.md — AI-Specific Observability (A4)

This document covers the AI-specific observability layer added in A4:
token/cost accounting, request and RAG-retrieval latency, and
success/failure tracking. It assumes familiarity with the pre-existing
infrastructure described in `AUDIT.md`.

**Verification status of this entire document: mixed.** Every claim below
is labeled as one of:
- **LOCALLY VERIFIED** — executed and confirmed in this sandbox (no AWS, no network).
- **UNVERIFIED — REQUIRES DEPENDENCY INSTALL** — needs `npm`/`cdk synth`/`pytest` with real dependencies.
- **UNVERIFIED — REQUIRES AWS ENVIRONMENT** — needs an actual deployed stack and live CloudWatch.

No claim in this document should be read as "this was seen working in a real AWS account" unless explicitly marked as such — and nothing is.

---

## 1. Design decision: why this reuses CloudWatch Logs Metric Filters, not EMF

Before A4, this codebase already had exactly one custom AI metric:
`TokenUsage`, produced by a structured `Logger.info("Usage Metric",
metric_type="token_usage", model=..., value=...)` call in `base.py`,
picked up by a CDK `MetricFilter` in `lib/monitoring/index.ts`. AWS
Lambda Powertools' `Metrics`/EMF module is **not used anywhere** in this
repository (confirmed by search — **LOCALLY VERIFIED**, static grep, not
a runtime check).

A4 extends the *existing* mechanism rather than introducing EMF as a
second, competing observability system. Every new metric is a structured
log line with a `metric_type` field, matched by a new `MetricFilter`.

## 2. Metrics implemented

All emitted by `genai_core/observability/ai_metrics.py`, matched by
`lib/monitoring/index.ts`'s new `addAIObservabilityMetricFilters()`.

| `metric_type` (log field) | CloudWatch metric name | Dimensions | Emitted from |
|---|---|---|---|
| `ai_request_success` | `AIRequests` | `model`, `mode` | `ModelAdapter.run()` (top-level wrapper around every chat/RAG/media mode) |
| `ai_request_failure` | `AIFailures` | `model`, `mode` | same, on any exception, before re-raising |
| `ai_request_latency` | `AIRequestLatency` | `model`, `mode` | same, on both success and failure paths |
| `input_tokens` | `InputTokens` | `model` | `ModelAdapter._emit_token_and_cost_metrics()`, called from all 3 chain code paths (`run_with_chain_v2`, and both branches of legacy `run_with_chain`) |
| `output_tokens` | `OutputTokens` | `model` | same |
| `estimated_cost_usd` | `EstimatedAICostUSD` | `model` | same — only emitted when both token counts and a pricing entry are available |
| `rag_retrieval_latency` | `RAGRetrievalLatency` | `engine` | `WorkspaceRetriever._get_relevant_documents()` (shared by `SanitizingWorkspaceRetriever`), around the real `genai_core.semantic_search.semantic_search()` call |
| `rag_documents_retrieved` | `RAGDocumentsRetrieved` | `engine` | same |

`model` = the Bedrock model id string already tracked elsewhere in the
app. `mode` = the chatbot mode (`chain`/`rag`/image/video generation
modes — a small fixed enum from `genai_core.types.ChatbotMode`). `engine`
= the retrieval backend name (`aurora`/`opensearch`/`kendra`/
`bedrock_kb`), read from a field every backend's query function already
returns — no new lookup, and never the `workspace_id` itself.

**LOCALLY VERIFIED**: every `metric_type` string emitted by
`ai_metrics.py` matches exactly (byte-for-byte, cross-checked via grep)
the string each `MetricFilter` in `lib/monitoring/index.ts` matches on.
Same for dimension field names (`model`/`mode`/`engine`).

## 3. Token accounting

Token counts are **never computed or estimated** by this layer — they
come from `self.callback_handler.usage`, itself populated by the
pre-existing `LLMStartHandler.on_llm_end()` callback in `base.py`, which
reads `generation.message.usage_metadata` — the real, provider-returned
token counts already flowing through this codebase before A4. If a
provider/response doesn't populate usage metadata, `input_tokens`/
`output_tokens` are `None` and the corresponding metric is simply not
emitted (never a fabricated `0` or guess).

## 4. Cost estimation methodology — READ THE VERIFICATION CAVEAT

Implemented in `genai_core/observability/pricing.py`, deliberately
separate from `ai_metrics.py` (pricing config vs. application logic).

**Pricing source and verification status (as of 2026-09-06):**

| Model pattern | Rate (per 1K tokens, in/out) | Status | Source |
|---|---|---|---|
| `anthropic.claude-3[-.]5-sonnet*` | $0.006 / $0.030 | **VERIFIED** | Directly quoted from `https://aws.amazon.com/bedrock/pricing/`, fetched 2026-09-06. Anthropic's "Models with extended access" table: **"Claude 3.5 Sonnet (Public Extended Access, Effective 1 Dec 2025)"** and **"Claude 3.5 Sonnet v2"**, On-Demand: $6.00 / 1M input tokens, $30.00 / 1M output tokens. Regions: US East (N. Virginia), US East (Ohio), US West (Oregon), Europe (Frankfurt/Ireland/Zurich/Paris). This is the **premium "Public Extended Access" rate** for this specific, superseded model generation — not necessarily representative of a current-generation Sonnet's standard rate, which is why the regex pattern is scoped narrowly and does **not** match newer Sonnet ids. |
| `anthropic.claude-(newer sonnet)*` | $0.003 / $0.015 | **UNVERIFIED** | Third-party pricing-tracker aggregation only (searched 2026-09-06) — AWS's live page renders its current-generation model tables via client-side JavaScript that this environment's fetch tool did not capture as text. |
| `anthropic.claude*haiku*` | $0.001 / $0.005 | **UNVERIFIED** | Same as above. |
| `anthropic.claude*opus*` | $0.005 / $0.025 | **UNVERIFIED** | Same as above. |

**Pricing/region/tier assumption for every entry:** On-Demand (Standard)
tier — no Batch, Provisioned Throughput, cross-region inference, or
prompt-cache discount applied. Real costs under those configurations will
differ; this is a known, documented limitation, not silently ignored.

Any model id that matches no pattern returns **no cost estimate at all**
(`None`) — the code never guesses a plausible-looking number for an
unrecognized model.

**This is an ESTIMATE, not the AWS bill.** The CloudWatch metric name
itself (`EstimatedAICostUSD`) and every docstring in `pricing.py` say so
explicitly. Do not use it for billing reconciliation.

To confirm an unverified entry: visit
`https://aws.amazon.com/bedrock/pricing/`, select the model/provider/region
in the live table, update the matching `ModelPricing` entry's `verified`
flag, `source`, and `verified_date` in `pricing.py`.

## 5. RAG retrieval latency

Measured as real wall-clock time (`time.monotonic()`) strictly around the
actual `genai_core.semantic_search.semantic_search(...)` call inside
`WorkspaceRetriever._get_relevant_documents()` — not a mock or
placeholder duration. Because `SanitizingWorkspaceRetriever` (A2.1) calls
`super()._get_relevant_documents()`, this instrumentation covers both
retriever classes and therefore every chain path that uses retrieval.

## 6. Dashboard

`lib/monitoring/index.ts`'s new `addAIObservabilityMetricFilters()`
method adds one `monitorCustom()` call with 5 concise metric groups
("AI Requests", "AI Request Latency", "Token Usage", "Estimated AI Cost",
"RAG Retrieval Latency by engine") — reusing the exact `MathExpression`
+ `SEARCH(...)` pattern the pre-existing `TokenUsage` dashboard section
already uses, gated behind the same `props.advancedMonitoring` opt-in
flag and the same log groups (`props.llmRequestHandlersLogGroups`) as the
original `TokenUsage` filter.

**UNVERIFIED — REQUIRES AWS ENVIRONMENT**: whether these widgets actually
render correctly, whether the `SEARCH()` expressions return data, and
whether the dashboard is visually reasonable — none of this can be seen
without a deployed stack and live CloudWatch data.

## 7. Privacy controls

Every `ai_metrics.py` function's signature is a hard allow-list — no
`**kwargs`, no generic dict parameter, and every parameter name was
checked against a forbidden-substring list (`prompt`, `session`,
`user_id`, `document_id`, `workspace`, `chunk`, `path`, `filename`) in
`ai_metrics_test.py`. Failure metrics log `type(exc).__name__` only,
never `str(exc)`, since an exception message can echo back fragments of
the request. **LOCALLY VERIFIED** via `ai_metrics_test.py`
(`TestSensitiveDataExclusionByDesign`, 3 tests) and manual grep of every
call site in `base.py`/`workspace_retriever.py` (documented in this
session's implementation log).

## 8. What was and wasn't verified

**LOCALLY VERIFIED:**
- `pricing.py`, `ai_metrics.py`: 37 unit tests, all passing, standard library + stubbed `aws_lambda_powertools` only.
- `base.py`'s `run()`/`_run_dispatch()` control-flow pattern (timing, success/failure metric emission, exception re-raise, argument forwarding): reproduced and tested in isolation (8 tests) — **not** the actual `base.py` file itself, which has too large a langchain/boto3 import surface to stub proportionately (see test file docstring).
- `workspace_retriever.py`'s RAG latency instrumentation: exercised through the full existing A2.1 stub harness (7 tests) — this **is** the real production file.
- `py_compile` on every Python file in the repository: clean.
- `metric_type`/dimension-name consistency between the Python emitter and the CDK filters: cross-checked via grep, exact match.
- `lib/monitoring/index.ts`: brace-balanced; parsed with `tsc --noEmit` in isolation — zero syntax errors, only expected "cannot find module" errors from the absence of `node_modules`.

**UNVERIFIED — REQUIRES DEPENDENCY INSTALL:**
- `base.py` actually importing/running with real `langchain`/`boto3`.
- `pytest tests/` collecting and passing these files the way the project's real CI would.
- `cdk synth` succeeding against the modified `lib/monitoring/index.ts`.
- Full TypeScript type-checking of `lib/monitoring/index.ts` (only syntax was checked, not types, since `cdk-monitoring-constructs`/`aws-cdk-lib` type definitions aren't installed).

**UNVERIFIED — REQUIRES AWS ENVIRONMENT:**
- Any of these metrics actually appearing in CloudWatch.
- The dashboard rendering or being useful in practice.
- Real-world token counts, latencies, or costs.
