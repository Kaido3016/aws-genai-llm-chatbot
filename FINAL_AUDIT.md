# FINAL_AUDIT.md

Consolidated audit of the A1–A6 work performed on top of
`aws-samples/aws-genai-llm-chatbot`. Re-verified immediately before
writing this document (exact commands and output below), not carried
forward from memory of earlier reports.

---

## Executive Summary

Starting from a mature but unevaluated/unhardened RAG chatbot sample
(see `AUDIT.md` for the original Phase 1 findings), this project added:
a standard-library-only RAG evaluation harness (A1); a prompt-injection
and RAG-poisoning defense layer with a self-discovered-and-fixed
vulnerability (A2); its integration into the real retrieval path with a
second self-discovered-and-fixed regression (A2.1); redacted,
allow-listed citation exposure to all authenticated users where
previously only admins got any source information at all (A3); and
AI-specific observability — token/cost/latency/RAG metrics — reusing the
project's existing CloudWatch Logs Metric Filter mechanism rather than
introducing a competing telemetry system (A4); and a traced, evidence-based
upload-security review (A5) that confirmed one suspected vulnerability
(filename path traversal) was not actually exploitable while finding and
fixing a real one (uploaded file content was never verified against its
claimed extension past the initial request); and static-analysis/
dependency-security automation (A6) — a CodeQL workflow and a Dependabot
configuration, both confirmed genuinely absent beforehand (not assumed)
and added covering exactly the languages/ecosystems this repository
actually has, with documented, verified coverage gaps rather than
overreaching claims.

None of this work has been deployed. No AWS account, network access, or
installed Node/Python dependencies were available in the environment
this work was performed in. Every verification claim below is labeled by
exactly what was and wasn't actually executed.

## Completed Phases

| Phase | Summary |
|---|---|
| A1 | RAG evaluation framework: golden dataset, precision/recall/groundedness/safety metrics, mock retriever/generator, reproducible `make evaluate` command. |
| A2 | Prompt-injection/RAG-poisoning defense: `genai_core/security/prompt_guard.py`, adversarial test suite, one self-found-and-fixed delimiter-forgery vulnerability. |
| A2.1 | Integration of A2's defenses into the real RAG path (`SanitizingWorkspaceRetriever`, Bedrock adapter system prompt); one self-found-and-fixed regression (legacy citation metadata would have leaked wrapped markup). |
| A3 | Redacted citation exposure to all authenticated users via a new `Citation` GraphQL type and `genai_core/citations.py` allow-list; admin-only full metadata behavior preserved unchanged. |
| A4 | AI observability: 8 CloudWatch metrics via structured logging + `MetricFilter`, versioned/partially-AWS-verified pricing table, RAG retrieval latency instrumentation. |
| A5 | Upload/file security: traced the real upload pipeline end-to-end; confirmed filename path-traversal is NOT exploitable (existing `os.path.basename()` mitigation verified, including against a Unicode homoglyph bypass attempt); found and fixed a real gap — extension allow-listing at request time was never re-verified against actual uploaded bytes — via magic-byte/structural content validation wired into the real upload-handler Lambda for both the Kendra and Step-Functions engine branches. |
| A6 | CodeQL + Dependabot: confirmed both were genuinely absent (not assumed); added a least-privilege CodeQL workflow covering Python and JavaScript/TypeScript, and a Dependabot configuration covering exactly the ecosystems/directories this repository actually has manifests for, with two documented, verified coverage gaps (non-standard-named Dockerfiles; sample-script Python requirements not already treated as first-class by the project's own CI). |

## Test Evidence (re-verified at time of writing this document)

```
A1:   36
A2:   18
A2.1:  7
A3:   44
A4:   45
A5:   42
A6:   19
------------
Total: 211
```

Command used and raw result (re-run at time of writing this update, all 12 test modules loaded and run together):
```
$ python3 -m unittest ... [all 12 test modules loaded and run together]
Ran 211 tests in 0.519s
OK
```

Run both **individually per suite** and **combined in a single Python
process** (to catch cross-suite state contamination — this happened
during A2/A2.1 development and again during A5's harness development,
was found, and fixed both times). Both give the same 211/211 result. No
count in this document is assumed or rounded.

## Static Verification

| Check | Result |
|---|---|
| Python `py_compile`, every `.py` file in the repo | **PASS** (clean, re-run at time of writing) |
| TypeScript syntax check (`tsc --noEmit`, isolated dir, no `node_modules`) | **No syntax errors.** Only `TS2307 Cannot find module` errors, expected given no installed dependencies. Full type-checking **UNVERIFIED — REQUIRES DEPENDENCY INSTALL**. |
| `metric_type` string cross-check (Python emitter vs. CDK filter) | **PASS** — all 8 values match byte-for-byte (grep-verified) |
| Dimension name cross-check (`model`/`mode`/`engine`) | **PASS** — exact match, grep-verified |
| Secret/credential scan of new/modified files | **PASS** — manual review; no secrets, API keys, or credentials found or introduced |
| Broken imports | **None found** in files verifiable in this sandbox (see limitations — `base.py`'s full import chain requires langchain/boto3, not installed, so cannot be exhaustively import-checked here) |
| `ruff` / `mypy` / `pytest` / `npm` / `cdk synth` | **UNVERIFIED — REQUIRES DEPENDENCIES** (none installed; not substituted with a different tool presented as equivalent) |
| `codeql.yml` / `dependabot.yml` YAML structure, permissions, ecosystem-to-manifest correspondence | **PASS** — parsed and validated with PyYAML (genuinely available in this sandbox), 19 tests, `tests/github-config/workflow_config_test.py` |

## AWS-Dependent Verification (explicitly unverified)

- Live Bedrock model invocation, guardrail behavior — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- CloudWatch metric ingestion for any of the 8 new A4 metrics — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- CloudWatch dashboard rendering — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- `cdk synth` / `cdk deploy` — **UNVERIFIED — REQUIRES DEPENDENCY INSTALL / AWS ENVIRONMENT**
- Real production token counts, latencies, or dollar costs — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**
- Whether the project's own `pytest tests/` / `npm run test` suites currently pass — **UNVERIFIED — REQUIRES DEPENDENCY INSTALL**
- Real S3/Fargate/`unstructured` behavior for A5's upload validation — **UNVERIFIED — REQUIRES AWS ENVIRONMENT**

## GitHub-Actions-Dependent Verification (A6, explicitly unverified)

- Whether `codeql.yml` actually runs successfully on GitHub's hosted runners — **UNVERIFIED — REQUIRES GITHUB ACTIONS**
- Any actual CodeQL findings (count, severity, or absence thereof) — **UNVERIFIED — REQUIRES GITHUB ACTIONS**; none are claimed anywhere in this repository
- Whether Dependabot actually opens PRs as configured — **UNVERIFIED — REQUIRES GITHUB ACTIONS**
- Whether `build.yaml`'s existing `pip-audit`/`npm audit`/`bandit` steps currently pass — **UNVERIFIED — REQUIRES GITHUB ACTIONS**

## Security Status

Summary (full detail in `SECURITY.md`): authentication/authorization,
workspace/session isolation, input validation, SSRF protection, and
dependency scanning are pre-existing and were reused, not rebuilt.
Prompt-injection defense, RAG-poisoning mitigation, and citation
redaction were built and tested in this project (A2/A2.1/A3), including
two genuine vulnerabilities found and fixed during development (see
`SECURITY.md` sections 5 and `ARCHITECTURE.md` section 8). Upload
content/extension validation (A5) and CodeQL/Dependabot configuration
(A6) have since been added — see those sections for exact scope and
verification status. Known, undisguised gaps: A5's content verification
is signature/structural only, not full content inspection; A6's CodeQL
workflow has not yet actually analyzed the repository on GitHub's
infrastructure and no findings are claimed; prompt-injection defenses
are heuristic/structural rather than a hard guarantee; and no
third-party security audit or compliance certification has occurred.

## Observability Status

8 metrics implemented, all via structured `Logger.info(metric_type=...)`
log lines matched by CDK `MetricFilter`s (not Powertools EMF, which is
unused elsewhere in this codebase):

| Metric | Dimensions |
|---|---|
| `AIRequests` | `model`, `mode` |
| `AIFailures` | `model`, `mode` |
| `AIRequestLatency` | `model`, `mode` |
| `InputTokens` | `model` |
| `OutputTokens` | `model` |
| `EstimatedAICostUSD` | `model` |
| `RAGRetrievalLatency` | `engine` |
| `RAGDocumentsRetrieved` | `engine` |

Dimensions are restricted to `model` (Bedrock model id), `mode` (a small
fixed chatbot-mode enum), and `engine` (a small fixed retrieval-backend
enum: aurora/opensearch/kendra/bedrock_kb). **Telemetry explicitly
excludes**: prompts, model responses, retrieved document/chunk content,
session IDs, user IDs, workspace IDs, document/chunk IDs, S3 paths,
filenames, and exception messages (only the exception **class name** is
logged on failure). Enforced structurally (function signatures contain
no parameter capable of carrying these) and verified via signature-
inspection tests plus manual call-site review — see `SECURITY.md`
section 12.

## Pricing Status

| Model pattern | Rate (per 1K, in/out) | Status | Source |
|---|---|---|---|
| Claude 3.5 Sonnet / 3.5 Sonnet v2 | $0.006 / $0.030 | **VERIFIED** | Directly quoted from `https://aws.amazon.com/bedrock/pricing/`, fetched 2026-09-06: "Claude 3.5 Sonnet (Public Extended Access, Effective 1 Dec 2025)" On-Demand rate, $6.00/$30.00 per 1M tokens |
| Newer Claude Sonnet generations | $0.003 / $0.015 | **UNVERIFIED** | Third-party pricing aggregators only |
| Claude Haiku (all) | $0.001 / $0.005 | **UNVERIFIED** | Third-party pricing aggregators only |
| Claude Opus (all) | $0.005 / $0.025 | **UNVERIFIED** | Third-party pricing aggregators only |

Methodology: `estimate_cost_usd(model_id, input_tokens, output_tokens)`
is deterministic — same inputs always produce the same output — and
returns `None` (never a guessed number) for unrecognized models or
missing token counts. Pricing assumes On-Demand/Standard tier only; no
Batch, Provisioned Throughput, cross-region inference, or prompt-cache
discount is modeled — a documented limitation, not a silent omission.
**`EstimatedAICostUSD` is explicitly and repeatedly labeled an estimate
in code, tests, and this documentation — never presented as the actual
AWS bill.**

## Known Limitations (preserved exactly, not softened)

1. `base.py`'s `run()`/`_run_dispatch()` is verified via an isolated behavioral reproduction, not by importing/executing the real file (its langchain/boto3 import surface is too large to stub proportionately without risking false confidence from a fragile mock).
2. Three of four pricing table entries remain unverified against the authoritative AWS page — its current-generation model tables render via client-side JavaScript this environment's fetch tool did not capture as text.
3. Cost estimation does not model Batch/Provisioned Throughput/cross-region/prompt-caching pricing tiers.
4. A5's content-verification is magic-byte/structural, not full content inspection — cannot detect a well-formed file of the correct type carrying a malicious payload.
5. A5's zip-bomb check is a threshold-based heuristic, not a guarantee.
6. CodeQL (A6) exists but has not actually analyzed the repository on GitHub's infrastructure — no findings obtained or claimed. Dependabot (A6) does not cover two non-standard-named Dockerfiles or several sample/reference-script Python requirements files (documented, verified scoping decision, not an oversight).
7. Prompt-injection defenses are heuristic and structural; they do not guarantee resistance to a plain-text instruction embedded in retrieved content with no special characters.
8. GraphQL schema changes were validated structurally (brace balance, field presence) only — no real GraphQL parser was available to fully validate the schema.
9. Pydantic's real field-validation behavior was not exercised anywhere in this session — pydantic is not installed in this sandbox, and every stub `BaseModel` used for testing explicitly does not replicate real validation.
10. The Bedrock Agents integration path was not reviewed or modified in A1–A6.
11. Nothing in this project has been deployed, load-tested, or observed handling real traffic.

## Overall Status Table

| Item | Status |
|---|---|
| A1 RAG evaluation framework | **IMPLEMENTED**, **LOCALLY VERIFIED** (36/36) |
| A2 prompt-injection/RAG-poisoning defense | **IMPLEMENTED**, **LOCALLY VERIFIED** (18/18) |
| A2.1 integration into real RAG path | **IMPLEMENTED**, **LOCALLY VERIFIED** (7/7) |
| A3 citation exposure | **IMPLEMENTED**, **LOCALLY VERIFIED** (44/44) |
| A4 AI observability (metrics + pricing) | **IMPLEMENTED**, **LOCALLY VERIFIED** (45/45) |
| A4 CDK/CloudWatch dashboard wiring | **IMPLEMENTED**, syntax-checked; **UNVERIFIED — REQUIRES AWS ENVIRONMENT** for actual ingestion/rendering |
| A5 path-traversal investigation | **NOT EXPLOITABLE** — confirmed by tracing the real pipeline, not assumed |
| A5 content/extension mismatch fix | **IMPLEMENTED**, **LOCALLY VERIFIED** (42/42, including real-handler integration tests) |
| A6 CodeQL workflow | **IMPLEMENTED**, **LOCALLY VERIFIED** (structure/permissions/languages, 19 tests); **UNVERIFIED — REQUIRES GITHUB ACTIONS** for actual analysis/findings |
| A6 Dependabot configuration | **IMPLEMENTED**, **LOCALLY VERIFIED** (every directory confirmed against a real manifest); two documented, verified coverage gaps |
| Combined regression suite | **LOCALLY VERIFIED** — 211/211 |
| `py_compile` (all Python files) | **LOCALLY VERIFIED** — clean |
| Project's own `pytest`/`npm run test` suites | **UNVERIFIED — REQUIRES DEPENDENCY INSTALL** |
| `cdk synth` / deployment | **UNVERIFIED — REQUIRES DEPENDENCY INSTALL / AWS ENVIRONMENT** |
| CodeQL / Dependabot | **IMPLEMENTED** (A6); **UNVERIFIED — REQUIRES GITHUB ACTIONS** for actual analysis |
| Post-upload content/signature verification | **IMPLEMENTED**, **LOCALLY VERIFIED** (A5) |
| Penetration test / compliance certification | **NOT IMPLEMENTED**, none claimed |
