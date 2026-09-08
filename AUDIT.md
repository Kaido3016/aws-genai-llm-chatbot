# AUDIT.md — Phase 1 Static Repository Audit

**Repo:** `aws-samples/aws-genai-llm-chatbot` (v5.0.0, snapshot as uploaded)
**Audit type:** Static code review only. No `npm install`/`pip install`/`cdk synth`/deployment was executed — this sandbox has no network access and no AWS credentials. Every finding below is based on reading source, config, and test files directly; nothing here is a "verified working" claim.
**Auditor scope:** Frontend, backend (Lambda/AppSync), IaC (CDK), auth, Bedrock/LLM integration, RAG, storage, observability, error handling, testing, CI/CD, security, cost, DX.

---

## 0. Headline finding

This is **not** a bare-bones sample. It's a mature, actively engineered solution: multi-provider LLM abstraction (Bedrock, SageMaker, OpenAI, Azure OpenAI, Bedrock Agents), four pluggable RAG backends (Aurora pgvector, OpenSearch, Kendra, Bedrock Knowledge Bases), hybrid search + cross-encoder reranking, Cognito auth with RBAC, WAF rate limiting, Secrets Manager, cdk-nag suppressions tracked inline, CloudWatch dashboards/alarms, and a CI pipeline that already runs lint, `bandit`, `pip-audit`, `npm audit`, and both Jest and pytest suites.

**Implication for your portfolio strategy:** claiming credit for "adding basic security" or "adding a model abstraction layer" would be inaccurate and would not survive a technical interview — an interviewer familiar with this repo (or who reads the git history) would catch it immediately. Your genuine differentiation has to come from work **on top of** this foundation: RAG evaluation, groundedness/citation UX, prompt-injection—specific defenses and tests, AI-specific observability (cost/token/hallucination metrics), and CI hardening (CodeQL, Dependabot) — areas that are real, verifiable gaps (see below).

---

## 1. What the original sample already does well (do not rebuild these)

| Area | Evidence | Assessment |
|---|---|---|
| Model abstraction | `lib/model-interfaces/langchain/functions/request-handler/adapters/{bedrock,sagemaker,openai,azureopenai,bedrock_agent,genaieh}` with shared `base/base.py` `ModelAdapter` | Real adapter pattern, not hardcoded to one model. Already the "LLM Provider → Adapter → Application Service" shape the brief asked for. |
| Multi-engine RAG | `genai_core/semantic_search.py` dispatches to `aurora`, `opensearch`, `kendra`, `bedrock_kb` per-workspace | Retrieval engine is a workspace-level config choice, not a rewrite. |
| Hybrid search + reranking | `genai_core/aurora/query.py`: vector + keyword search, cross-encoder model, cosine/L2 metric, language detection via Comprehend | This already covers "hybrid retrieval" and "reranking" from Phase 4 of your brief. |
| Conversation memory | `DynamoDBChatMessageHistory` + `ConversationBufferMemory` wired into every adapter | Already persistent, per-session. |
| Streaming | Adapters support streaming; explicitly disabled only when Bedrock Guardrails are active (since guardrails apply post-generation) | Real, documented tradeoff — not an oversight. |
| Guardrails | `base.py: apply_bedrock_guardrails()` calls `bedrock.apply_guardrail` and intervenes on `GUARDRAIL_INTERVENED` | Bedrock Guardrails integration already exists — this is a legitimate AI-safety control you can *describe accurately* but not claim to have built. |
| Token usage tracking | `LLMStartHandler` callback aggregates `input_tokens`/`output_tokens`/`total_tokens` per request, stored in chat message metadata | Token accounting already exists at the message level; **not** surfaced as a CloudWatch metric or cost dashboard (see gap below). |
| Source/citation data | `run_with_chain()` returns `source_documents` with `page_content` + `metadata` | Citation *data* exists end-to-end already. |
| Auth/RBAC | Cognito UserPool with MFA optional, `StandardThreatProtectionMode.FULL_FUNCTION`, groups (`admin`, `workspace_manager`, `user`), `@permissions.approved_roles(...)` decorator on nearly every resolver | Real, enforced RBAC, not decorative. |
| Input validation | Pydantic models with regex-constrained fields (`SAFE_STR_REGEX`, length limits) on every route in `lib/chatbot-api/functions/api-handler/routes/*.py` | Consistent validation layer already present. |
| SSRF protection | `documents.py: _is_ip_address()`, `_is_hostname_internal()` reject IP-literal and private-network hosts before crawling a URL | A specific, correct security control most portfolio projects miss entirely. |
| File upload constraints | `presign.py`: `MAX_FILE_SIZE = 10MB` enforced via S3 presigned-POST `content-length-range` condition; extension allow-lists per upload context in `documents.py` | Size and extension are enforced. **Gap:** no post-upload content/MIME sniffing (see Section 4). |
| Secrets management | `SecretsManager` used in `lib/shared`, `lib/models`, `lib/authentication` (e.g., OIDC client secrets) | No hardcoded credentials found in the paths reviewed. |
| Network/edge security | WAF (`aws-cdk-lib/aws-wafv2`) with a **rate-based rule** (`ruleLimitRequests`) associated to the AppSync API in `lib/chatbot-api/index.ts` and `lib/shared/index.ts` | Rate limiting is already implemented at the edge — Phase 6's "rate limiting where appropriate" is already done. |
| IaC hygiene | `cdk-nag` `NagSuppressions` used throughout with what appear to be justified, itemized suppressions (not blanket disables) | Indicates deliberate security review during original development. |
| Observability | `lib/monitoring/index.ts` (533 lines) builds a `MonitoringFacade` (cdk-monitoring-constructs) with CloudWatch dashboards/alarms across AppSync, Lambda, DynamoDB, S3, SQS, Step Functions, Aurora, OpenSearch, Kendra, CloudFront, SNS alerting | Infra-level observability is comprehensive already. **Gap:** no *AI-specific* metrics (cost, groundedness, hallucination rate — see Section 7). |
| CI pipeline | `.github/workflows/build.yaml`: ESLint, `npm run build`, `npm run test` (Jest), `cdk synth`, `flake8`, `bandit`, `pip-audit` (with a tracked ignore-list), `pytest`, frontend `npm audit`/build | This already satisfies most of Phase 10's ask (lint, type-check via `tsc`, unit tests, security scanning, dependency scanning, infra validation via `cdk synth`, build). **Gap:** no CodeQL, no Dependabot config found (see Section 9). |
| Testing volume | 39 Python test files, 7 Jest test files, plus a separate `integtests/` suite with dedicated `security/` tests for RBAC (`access_control_admin_test.py`, `access_control_user_test.py`, `access_control_workspace_manager_test.py`, `unauthorized_test.py`) | Meaningful existing coverage, especially for authz. **Gap:** no tests target prompt injection, RAG poisoning, or groundedness (see Section 5/9). |

**Bottom line:** if you present this project, your README's "what I inherited vs. what I built" section needs to be honest about all of the above being pre-existing. That honesty is itself a portfolio asset — it shows you can read and correctly characterize a large unfamiliar codebase, which is exactly what a senior AI engineering role requires.

---

## 2. Frontend

- React app under `lib/user-interface/react-app` (Amplify-generated GraphQL client via `amplify codegen`), built via `npm run build` in CI.
- Not deeply reviewed component-by-component in this pass (large surface, ~3.8MB of source) — flagged as **LOW** priority for deep audit since the brief's differentiation targets are backend/AI/infra, not UI polish.
- **MEDIUM / IMPROVEMENT:** citation/source display — confirmed that `source_documents` metadata is only attached to the response `metadata` field when `is_admin_role(user_groups)` is true (`base.py`, `run_with_chain`). Regular users get `{"sessionId": ...}` only — **no citations are shown to non-admin users today.** This is a legitimate, verifiable, and valuable frontend+backend gap to close for a "trustworthy RAG" portfolio narrative.

## 3. API Architecture

- AppSync GraphQL API (`lib/chatbot-api/schema`), Lambda resolvers under `functions/api-handler/routes/*.py`, plus a dedicated `outgoing-message-appsync` function and `send-query-lambda-resolver`.
- Routes are organized by domain (`documents.py`, `sessions.py`, `semantic_search.py`, others) with consistent Pydantic request models and an RBAC decorator.
- **LOW:** CORS is handled via AppSync/CloudFront defaults; no explicit custom CORS policy found in the paths grepped. Worth a one-line documentation check, not a rebuild.
- **MEDIUM:** No per-user or per-workspace request throttling beyond the IP-based WAF rate rule — i.e., a single authenticated user cannot be individually rate-limited independent of source IP. Reasonable gap to document, low priority to fix (would need API Gateway usage plans or AppSync-level throttling, which is a bigger infra change than this portfolio needs).

## 4. RAG / Document Ingestion — Detailed Findings

- Pipeline: S3 upload (presigned POST) → SQS → `upload-handler` Lambda → Step Functions workflow (`file-import-batch-job`) → text extraction/chunking → embeddings → vector store (Aurora/OpenSearch) or Kendra data source sync.
- **HIGH:** No MIME-type/content verification against the file's actual bytes after upload — validation today is filename-extension-based only (`os.path.splitext` + allow-list). A file named `report.pdf` that is actually an executable or malformed payload would pass the extension check and be handed to the extraction pipeline. This is a real, fixable gap and a legitimate "I hardened this" portfolio story.
- **HIGH (portfolio value, not necessarily a live vulnerability):** No RAG-poisoning-specific defenses found — e.g., no detection of instruction-like text embedded inside ingested documents (prompt-injection-via-document). Given documents are only ingested by `admin`/`workspace_manager` roles, real-world risk is lower than an open-upload chatbot, but for a portfolio this is exactly the kind of "AI-specific security" finding worth writing up and partially mitigating (e.g., a content-scanning step, or explicit prompt-construction that treats retrieved content as data, not instructions).
- **CRITICAL (portfolio gap, not a security bug):** **No RAG evaluation framework exists anywhere in the repo.** No retrieval-relevance tests, no groundedness/hallucination checks, no golden question sets. This is the single biggest missing piece relative to your stated positioning ("RAG evaluation" is explicitly one of your five priority areas) and it's genuinely absent — safe to build without duplicating existing work.
- **MEDIUM:** Retrieval already supports hybrid search + reranking + a `threshold` parameter (`aurora/query.py` signature has `threshold: int = 0`), but it's not clear from this pass whether the threshold is actually wired to a config value end-to-end or effectively unused (defaults to 0 = no filtering). Needs a targeted follow-up read before claiming "relevance thresholding" as something you added or improved.
- **LOW:** Query transformation beyond history-aware condensing (`create_history_aware_retriever`, `CONDENSE_QUESTION_PROMPT`) — no query rewriting/expansion/decomposition. Reasonable to leave as-is; not a differentiator worth the complexity for this project.

## 5. Prompt Engineering / AI Safety

- System prompts are overridable per-request (`system_prompts.get("systemPromptRag")`, `condenseSystemPrompt`), which is good design — prompts aren't hardcoded strings buried in application logic.
- **HIGH:** No prompt-injection-specific test suite or defensive prompt scaffolding (e.g., explicit delimiters/instructions telling the model to treat retrieved context as untrusted data) found in the base QA/condense prompts reviewed. This is a real, buildable, and interview-relevant gap.
- **HIGH:** No output validation step — model output is returned to the user largely as-is (aside from Bedrock Guardrails, when configured). No check that citations claimed in the answer actually correspond to retrieved chunks (a basic groundedness check).
- **MEDIUM:** No documented distinction in the UI between "this came from a retrieved document" vs. "the model inferred/generated this" beyond the raw source_documents list existing in metadata (and only for admins, per Section 2). The brief's Phase 4 requirement — "clearly distinguish retrieved fact from AI-generated explanation" — is not currently met for end users.

## 6. Error Handling & Reliability

- Custom exception type exists (`genai_core.types.CommonError`) and is used consistently across routes instead of bare exceptions — good practice already in place.
- Not yet confirmed (needs follow-up read): whether Bedrock/SageMaker calls have explicit retry/backoff configured at the boto3 client level (`botocore.config.Config(retries=...)`) or rely on SDK defaults. **Flagged MEDIUM, needs verification before claiming as a gap or a strength.**
- **MEDIUM:** No visible circuit-breaker/fallback-to-another-model logic if a Bedrock model is throttled or unavailable — the adapter pattern would make this straightforward to add, and it's a legitimate "reliability engineering" story for a resume bullet.

## 7. Observability — AI-Specific Gap

- Infra observability (Section 1) is comprehensive. What's **not** present:
  - **HIGH:** No CloudWatch **custom metrics** for token usage, estimated Bedrock cost per request, or RAG retrieval latency specifically (as opposed to generic Lambda duration). Token counts are captured (Section 1) but only stored in DynamoDB message metadata — never emitted as a metric or aggregated.
  - **HIGH:** No groundedness/hallucination-rate tracking at runtime (this overlaps with Section 5/9 — evaluation and observability are the same underlying gap, viewed from two angles).
  - **MEDIUM:** Structured logging (via `aws_lambda_powertools.Logger`) is used consistently and includes context injection, but a quick review didn't confirm a single consistent correlation-ID field (e.g., request ID) is present across every log line in every function vs. relying on the Lambda request ID implicitly via Powertools defaults. Needs confirmation before claiming as a gap.

## 8. Cost

- No config for embeddings caching or de-duplication of identical document uploads observed in the ingestion path reviewed.
- Model selection is already fully configurable (Section 1), which is itself a cost-optimization lever — this exists, don't re-claim it as new.
- **MEDIUM/IMPROVEMENT:** No visible per-workspace or per-user token/cost ceiling or alerting. Given token usage is already tracked (Section 1), wiring it into a CloudWatch metric + budget alarm is a small, high-leverage addition.

## 9. Testing & CI/CD Gaps

- **HIGH:** No CodeQL workflow found (`.github/workflows` contains `deploy.yml` for docs, `build.yaml`, `stale.yml`, `e2e-validation.yml` — no `codeql-analysis.yml` or equivalent).
- **HIGH:** No Dependabot configuration (`.github/dependabot.yml` absent).
- **HIGH:** No tests target prompt injection, adversarial retrieval content, or groundedness/hallucination — confirmed by reviewing `integtests/security/*` (RBAC-only) and the general test tree.
- **MEDIUM:** `pip-audit` and `npm audit` already run in CI (Section 1) — dependency vulnerability scanning exists; CodeQL would add *static* code-level scanning (SAST) on top, which is complementary, not redundant.

## 10. Infrastructure as Code

- CDK (TypeScript) is the sole IaC framework in use — consistent with the brief's instruction to use the existing framework rather than introducing Terraform/Pulumi.
- `cdk.json`, `bin/config.ts`, `bin/default-config.json` drive a config-driven deployment (model choice, RAG engine choice, auth options, etc. are parameters, not hardcoded) — this is good architecture to preserve and describe accurately, not rebuild.
- **Not verified:** whether `cdk synth` actually succeeds — requires `npm ci` and network access, which this sandbox does not have. **UNVERIFIED — REQUIRES AWS ENVIRONMENT / NETWORK ACCESS.**

## 11. Dependency Management

- npm (`package.json`/`package-lock.json`) and pip (`pyproject.toml`, `pytest_requirements.txt`, plus per-batch-job `requirements.txt` files for `web-crawler-batch-job` and `file-import-batch-job`) are both in use and both scanned in CI already (`pip-audit`, `npm audit`).
- A `.pip-audit-known-vulns` ignore-list file is referenced in CI and grepped for comments — meaning known/accepted vulnerabilities are tracked explicitly rather than silently suppressed. This is good practice; worth mentioning in your security write-up as something you *observed and understood*, not something you added.

---

## 12. Findings Summary Table

| Severity | Count | Examples |
|---|---|---|
| CRITICAL | 1 | No RAG evaluation framework exists at all |
| HIGH | 7 | No post-upload MIME/content verification; no prompt-injection defenses/tests; no output/groundedness validation; no AI-cost/token CloudWatch metrics; no groundedness/hallucination tracking; no CodeQL; no Dependabot |
| MEDIUM | 7 | Citations hidden from non-admin users; no per-user throttling; retrieval threshold possibly unused; no model fallback on Bedrock failure; no token/cost budget alarms; correlation-ID consistency unconfirmed; retry/backoff config unconfirmed |
| LOW | 3 | Frontend not deeply audited (out of scope for differentiation); CORS policy not explicitly documented; no query rewriting/decomposition |
| IMPROVEMENT (already strong, describe don't rebuild) | 13 | Everything in Section 1's table |

---

## 13. What Cannot Be Verified in This Sandbox

Flagging explicitly per your instructions — none of the following can be confirmed true or false without a real AWS account/network access, and none should be claimed either way until you run them yourself:

- `cdk synth` / `cdk deploy` success — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- Whether the existing Jest/pytest suites currently pass — **UNVERIFIED — REQUIRES NETWORK ACCESS (dependency install)**
- Actual Bedrock model behavior, latency, or guardrail effectiveness — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- Actual AWS cost of running this stack — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- Whether CI (`build.yaml`) is currently green on the real repo — **UNVERIFIED — REQUIRES GITHUB ACTIONS EXECUTION**

---

## 14. Immediate implication for Phase 2 planning

Given the findings above, the highest-integrity, highest-portfolio-value path is **not** to add AWS services or rewrite the model/RAG layers (they're already solid), but to build the layer this repo genuinely lacks: **evaluation, AI-specific security testing, and AI-specific observability/cost tracking** — the things a production GenAI team would build *on top of* a solid base like this one. That's the basis for the prioritized plan below.
