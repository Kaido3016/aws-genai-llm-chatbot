# ARCHITECTURE.md

This document describes the architecture that **actually exists in this
repository** as of the A1–A6 work (RAG evaluation, prompt-injection
defense, citation exposure, AI observability, upload/file security, and
static-analysis/dependency-security automation). It does not describe a
deployed system — see the **IMPLEMENTED vs. AWS-DEPENDENT/UNVERIFIED**
labels throughout, and `DEPLOYMENT.md` for what deployment would require.

---

## 1. System Overview

This is `aws-samples/aws-genai-llm-chatbot`, a CDK-deployed, multi-tenant
RAG chatbot on AWS, extended with the A1–A6 work in this session. At a
high level:

```
React frontend (Amplify/Cognito auth)
        |
        v
AppSync GraphQL API  -->  Lambda resolvers (chatbot-api/functions/api-handler)
        |                         |
        v                         v
   SQS queue            DynamoDB (sessions, documents metadata)
        |
        v
Lambda request-handler (model-interfaces/langchain)
        |             |
        v             v
  Bedrock/SageMaker   RAG retrieval (Aurora pgvector / OpenSearch /
  /OpenAI/etc. via     Kendra / Bedrock Knowledge Bases)
  ModelAdapter                 |
        |                      v
        |              S3 (documents) + Step Functions
        |              (ingestion pipeline)
        v
  CloudWatch (logs, custom metrics via Metric Filters, dashboards)
```

**IMPLEMENTED**: every component named above exists as source code in
this repository. **AWS-DEPENDENT/UNVERIFIED**: that this composition
actually deploys and functions together in a live AWS account — not
executed in this sandbox (no network, no AWS credentials).

## 2. Frontend/Backend Split

- **Frontend**: React app (`lib/user-interface/react-app`), Cloudscape
  Design components, Amplify-generated GraphQL client. **IMPLEMENTED**
  (source exists); build/runtime **UNVERIFIED — REQUIRES DEPENDENCY
  INSTALL** (no `npm`/`node_modules` in this sandbox).
- **Backend**: AppSync GraphQL API (`lib/chatbot-api/schema/schema.graphql`)
  backed by Python Lambda resolvers
  (`lib/chatbot-api/functions/api-handler/routes/*.py`), plus a
  separate SQS-driven Lambda for the actual model/RAG work
  (`lib/model-interfaces/langchain/functions/request-handler`).
  **IMPLEMENTED**.

## 3. Python/LangChain Architecture

`ModelAdapter` (`lib/model-interfaces/langchain/functions/request-handler/adapters/base/base.py`)
is the base class every provider adapter (Bedrock, SageMaker, OpenAI,
Azure OpenAI, Bedrock Agents) extends. Key methods:

- `run()` — public entry point; as of A4, a thin wrapper that times the
  call and emits `AIRequests`/`AIFailures`/`AIRequestLatency`, then
  delegates to...
- `_run_dispatch()` — the original mode-dispatch logic (chain / RAG /
  image / video generation), unchanged in behavior by A4.
- `run_with_chain_v2()` — the primary RAG/chat path (Bedrock Converse
  API style, `create_history_aware_retriever` +
  `create_stuff_documents_chain`).
- `run_with_chain()` — legacy path (`ConversationalRetrievalChain`) for
  non-Converse LLMs.
- `_emit_token_and_cost_metrics()` (A4) — shared helper called from all
  three of the above, emitting `InputTokens`/`OutputTokens`/
  `EstimatedAICostUSD` from real, provider-returned token counts.

**IMPLEMENTED**: this class structure and control flow, confirmed by
direct source review and (for the `run()`/`_run_dispatch()` pattern) an
isolated behavioral reproduction test — see `OBSERVABILITY.md` section 8
for why the full file isn't imported/executed here (large langchain/
boto3 import surface).

## 4. RAG Architecture

`genai_core.semantic_search.semantic_search(workspace_id, query, ...)`
dispatches to one of four pluggable retrieval engines based on the
workspace's configuration:

| Engine | Module | Notes |
|---|---|---|
| Aurora (pgvector) | `genai_core/aurora/query.py` | Hybrid (vector + keyword) search, cross-encoder reranking, language detection |
| OpenSearch | `genai_core/opensearch/query.py` | |
| Kendra | `genai_core/kendra/query.py` | |
| Bedrock Knowledge Bases | `genai_core/bedrock_kb/query.py` | |

Every engine's result dict includes an `engine` field (used, unmodified,
as the A4 `RAGRetrievalLatency`/`RAGDocumentsRetrieved` dimension).
**IMPLEMENTED**.

Retrieval is wrapped by `WorkspaceRetriever` /
`SanitizingWorkspaceRetriever` (`genai_core/langchain/workspace_retriever.py`,
A2.1), which:
1. Calls `semantic_search()`, timing the call for A4's RAG latency metric.
2. Stores the **original** documents in `self.documents_found` (used
   later for citations — A3).
3. Returns a **separate, wrapped/escaped** copy of the documents (via
   `genai_core.security.prompt_guard`, A2) to the LLM chain.

This original-vs-wrapped split is the core trust boundary described in
section 8 below. **IMPLEMENTED**, verified by 7 tests in
`tests/shared/layers/python-sdk/genai_core/langchain/workspace_retriever_test.py`.

## 5. Model Adapter Architecture

Provider abstraction lives under
`lib/model-interfaces/langchain/functions/request-handler/adapters/`:
`bedrock/`, `sagemaker/`, `openai/`, `azureopenai/`, `bedrock_agent/`,
`genaieh/`. Each subclasses `ModelAdapter` and overrides prompt-template
construction (`get_qa_prompt`, `get_prompt`, `get_condense_question_prompt`)
and LLM instantiation (`get_llm`). Model selection is a per-workspace/
per-request configuration value, not hardcoded — **IMPLEMENTED**.

## 6. Bedrock Integration

- Model invocation via `langchain_aws` (`ChatBedrockConverse` for the v2
  chain path). **IMPLEMENTED** (source-level).
- Bedrock Guardrails integration (`ModelAdapter.apply_bedrock_guardrails`)
  — pre-existing, not built in this session, described accurately here
  rather than claimed as new work. **IMPLEMENTED** (source-level);
  actual guardrail behavior **UNVERIFIED — REQUIRES AWS ENVIRONMENT**.
- Token usage capture via `LLMStartHandler` reading
  `generation.message.usage_metadata` — **IMPLEMENTED**, real
  provider-returned values, never estimated (see `OBSERVABILITY.md`
  section 3).

## 7. Agent/Tool Architecture

A `bedrock_agent` adapter and `bedrock-agents` Lambda exist
(`lib/model-interfaces/bedrock-agents/`), providing a Bedrock Agents
integration path distinct from the LangChain adapters. This was not
modified or deeply audited in A1–A6 (out of scope for the RAG/security/
observability work done). **IMPLEMENTED (pre-existing, not
independently re-verified in this session)**.

No MCP (Model Context Protocol) server or client implementation exists
anywhere in this repository. **NOT IMPLEMENTED.**

## 8. Trust Boundaries / Security Boundaries

This is the architecture's core safety property, built across A2/A2.1/A3:

```
Retrieved document (untrusted -- attacker/compromised-upload-controlled)
        |
        +--> genai_core.security.prompt_guard.wrap_untrusted_context()
        |    (escapes forged delimiters, wraps in labeled <source> tags)
        |         |
        |         v
        |    Fed into LLM prompt as {context}   [LLM chain boundary]
        |
        +--> genai_core.citations.build_safe_citations()
             (allow-list: title/sourceUrl/snippet/index ONLY --
              structurally cannot emit workspace_id/document_id/
              chunk_id/score/path)
                  |
                  v
             Exposed to ALL authenticated users via GraphQL `citations`
             field                                [API/UI boundary]
```

A single retrieval produces **two independent representations** of the
same underlying documents — this is deliberate, not incidental. The
`{context}` side and the `citations` side never share code paths after
`WorkspaceRetriever._get_relevant_documents()`/`get_last_search_documents()`
diverge. **IMPLEMENTED**, regression-tested in A2/A2.1/A3 (79 tests
across those phases specifically target this boundary).

Known limitation: `prompt_guard`'s defenses close a specific
delimiter-forgery vector and provide instruction text; they do not
guarantee a given LLM will always refuse plain-text embedded
instructions with no special characters. See `SECURITY.md`.

Session/workspace authorization boundary:
`genai_core.sessions.get_session(session_id, user_id)` — a DynamoDB
`get_item` on composite key `(SessionId, UserId)`, where `user_id` comes
from the authenticated Cognito identity, never client input. Pre-existing
mechanism, re-verified (not modified) in A3. **IMPLEMENTED**.

## 9. Observability Architecture (A4)

See `OBSERVABILITY.md` for full detail. Summary: structured
`Logger.info(metric_type=..., ...)` log lines -> CDK `MetricFilter` ->
CloudWatch custom metric -> `cdk-monitoring-constructs` dashboard widget.
No Powertools EMF/Metrics module is used anywhere in this repository —
A4 deliberately extended the pre-existing log-filter mechanism rather
than introducing a second one. **IMPLEMENTED** (source + CDK config);
**UNVERIFIED — REQUIRES AWS ENVIRONMENT** for actual CloudWatch ingestion
and dashboard rendering.

## 10. CDK / Infrastructure Architecture

Single top-level stack (`lib/aws-genai-llm-chatbot-stack.ts`) composing:
`Authentication`, `Models`, `RagEngines`, `ChatBotApi`, `LangChainInterface`,
`BedrockAgentsInterface`, `IdeficsInterface`, `UserInterface`, `Shared`,
`Monitoring`. Config-driven via `bin/config.ts`/`bin/default-config.json`
(model choice, RAG engine choice, auth options are parameters, not
hardcoded). **IMPLEMENTED** (source-level); `cdk synth`/`cdk deploy`
**UNVERIFIED — REQUIRES DEPENDENCY INSTALL / AWS ENVIRONMENT** (no `npm`,
no AWS credentials in this sandbox).

## 11. Data Flow (document ingestion to answer)

```
User uploads file -> S3 (presigned POST, size/extension validated)
  -> SQS -> upload-handler Lambda -> Step Functions
  -> text extraction -> chunking -> embeddings -> vector store (per engine)

User asks question -> AppSync -> SQS -> request-handler Lambda
  -> SanitizingWorkspaceRetriever (retrieval + A4 latency timing)
  -> prompt_guard wrapping (A2/A2.1) -> LLM chain -> answer
  -> citations built from ORIGINAL documents (A3) -> GraphQL response
  -> token/cost/latency metrics emitted (A4) -> CloudWatch (log-filter-based)
```

**IMPLEMENTED** at the source level for every step; end-to-end execution
**UNVERIFIED — REQUIRES AWS ENVIRONMENT**.

## 12. Testing / Evaluation Architecture

- `evaluation/` (A1): standard-library-only RAG evaluation harness — mock
  retriever/generator, golden dataset, precision/recall/groundedness/
  safety metrics. **LOCALLY VERIFIED** (36 tests).
- `tests/shared/layers/python-sdk/genai_core/security/` (A2): adversarial
  prompt-injection/RAG-poisoning suite. **LOCALLY VERIFIED** (18 tests).
- `tests/shared/layers/python-sdk/genai_core/langchain/` (A2.1):
  integration tests against the real `workspace_retriever.py`, via
  dependency stubs where `langchain`/`boto3` are absent. **LOCALLY
  VERIFIED** (7 tests).
- `tests/.../citations/` + `tests/.../routes/sessions_citations_test.py`
  (A3): citation allow-list + session-exposure tests. **LOCALLY
  VERIFIED** (44 tests).
- `tests/.../observability/` (A4): pricing/metrics/dispatch-pattern
  tests. **LOCALLY VERIFIED** (45 tests).
- `tests/shared/layers/python-sdk/genai_core/security/file_validation_test.py`
  + `tests/rag-engines/data-import/functions/upload-handler/` (A5):
  file-content/signature validation and real upload-handler integration
  tests. **LOCALLY VERIFIED** (42 tests).
- `tests/github-config/workflow_config_test.py` (A6): CodeQL workflow
  and Dependabot configuration validation against the actual repository
  structure. **LOCALLY VERIFIED** (19 tests).
- The project's own pre-existing `pytest`-based suite
  (`tests/`, `integtests/`) — exists, was read and partially exercised
  via stubs, but **not run as `pytest tests/`** in this sandbox (pytest/
  boto3/pydantic not installed, no network to install them).
  **UNVERIFIED — REQUIRES DEPENDENCY INSTALL.**

Combined new-work regression total, as of A6: **211/211 passing**, run
both individually per-suite and combined in a single process (to detect
cross-suite state contamination, which was found and fixed during
A2/A2.1 and again during A5 development).

## 13. What This Document Does Not Claim

This architecture has not been deployed. No AWS account was used. No
`cdk synth`, `npm install`, or live Bedrock/CloudWatch call happened in
producing this repository's A1–A6 work. Every "IMPLEMENTED" label above
means "exists as reviewed, tested-where-possible source code" — not
"running in production."
