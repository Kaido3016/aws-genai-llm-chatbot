# DEPLOYMENT.md

**Deployment configuration is implemented in this repository but live
deployment has NOT been executed or verified in an AWS account as part
of the A1–A6 work.** This sandbox has no network access and no AWS
credentials — every command below is documented from reading
`package.json`, `cdk.json`, and the CDK source; none were run to
completion here beyond a syntax-level `tsc --noEmit` parse of the files
this session modified.

Do not read anything in this document as "this was deployed" or "this
was confirmed to work end-to-end." It describes what the repository's
own configuration says to do.

---

## 1. Prerequisites (as declared by the project)

- Node.js `>=18.0.0 <21.0.0` (from `package.json` `engines`). This
  sandbox has Node v22.22.2 — **outside the declared supported range** —
  which is itself worth noting before attempting a real deployment in an
  environment matching this sandbox's Node version.
- An AWS account with credentials configured (not present here).
- `npm install` to populate `node_modules` (not run here — no network
  access; `node_modules` is empty in this sandbox).
- Python dependencies per `pytest_requirements.txt` (includes `boto3`,
  `pydantic`, `pytest`, `langchain`, etc. — none installed in this
  sandbox; confirmed by direct `import` failures throughout A1–A6
  testing).
- Bedrock model access enabled in the target AWS account/region for
  whichever models the deployed config selects (not verified here).

## 2. Configuration / Environment Variables

Deployment configuration is CDK-context-driven, not raw environment
variables: `bin/config.ts` reads `bin/default-config.json` (or a
generated `bin/config.json` from the CLI wizard) to select the RAG
engine(s), auth provider, default models, and feature flags. This is
pre-existing, config-driven architecture (praised in `AUDIT.md`) — not
modified in A1–A6.

**A4 addition**: `props.advancedMonitoring` (an existing CDK prop, not
newly introduced) now also gates the new AI-observability
`MetricFilter`s in `lib/monitoring/index.ts`, exactly as it already
gated the pre-existing `TokenUsage` filter. No new environment
variable or config flag was introduced for A4 — it reuses the existing
opt-in.

## 3. Docker Architecture

One `Dockerfile` exists in this repository:
`lib/shared/alpine-zip/Dockerfile` (used for a Lambda-layer packaging
step, not for running the application itself). There is no
docker-compose-based local development environment in this repository.
**IMPLEMENTED** (source-level); not built or run in this sandbox
(**UNVERIFIED — REQUIRES DEPENDENCY INSTALL**, no Docker daemon
exercised here).

## 4. CDK Architecture

Entry point: `bin/aws-genai-llm-chatbot.ts` → `lib/aws-genai-llm-chatbot-stack.ts`,
composing `Authentication`, `Models`, `RagEngines`, `ChatBotApi`,
`LangChainInterface`, `BedrockAgentsInterface`, `IdeficsInterface`,
`UserInterface`, `Shared`, and `Monitoring` (this session's A4 changes
live inside `Monitoring`). See `ARCHITECTURE.md` section 10 for the full
composition.

## 5. AWS Services Used/Referenced

Cognito, AppSync, Lambda, SQS, Step Functions, S3, DynamoDB, Aurora
(pgvector), OpenSearch, Kendra, Bedrock (incl. Knowledge Bases,
Guardrails, Agents), SageMaker (optional), CloudFront, WAF, Secrets
Manager, CloudWatch (Logs, Metric Filters, Dashboards, Alarms), SNS.
All pre-existing; A4 adds no new AWS service, only new CloudWatch
`MetricFilter`s and dashboard widgets within the existing `Monitoring`
construct.

## 6. Deployment Flow (as declared, not executed)

```
npm install
npm run build        # amplify codegen && tsc
npx cdk bootstrap     # first time per account/region
npm run deploy        # npx cdk deploy
```

`package.json` also defines `npm run hotswap` (`cdk deploy --hotswap`)
for faster iterative deploys, and `npm run watch` (`cdk watch`).
**None of these commands were executed in this sandbox.** `tsc` itself
is available here (checked: `npx tsc --version` → 6.0.3), but running
the *project's* `npm run build` requires `node_modules`, which is empty.

## 7. Monitoring / CloudWatch

Once deployed with `advancedMonitoring: true` in the stack config, the
`Monitoring` construct creates:
- The pre-existing `TokenUsage` CloudWatch metric + dashboard section.
- **A4 additions**: `AIRequests`, `AIFailures`, `AIRequestLatency`,
  `InputTokens`, `OutputTokens`, `EstimatedAICostUSD`,
  `RAGRetrievalLatency`, `RAGDocumentsRetrieved` — see `OBSERVABILITY.md`
  for full detail.

**UNVERIFIED — REQUIRES AWS ENVIRONMENT**: whether any of these metrics
actually appear in CloudWatch after a real deployment, whether the
dashboard renders sensibly, and whether the `SEARCH()` expressions used
in the dashboard widgets return the expected data. Nothing here was
observed in a live account.

## 8. Secrets / Configuration Management

Handled via AWS Secrets Manager (pre-existing, e.g. OIDC client
secrets). No secrets are contained in this repository's source or in any
file created/modified during A1–A6 — confirmed by manual review (see
`SECURITY.md` section 13).

## 9. Testing Before Deployment

Recommended, based on what actually exists:

```
python3 -m unittest discover -s evaluation/tests -p "test_*.py"                              # A1: 36
python3 -m unittest discover -s tests/shared/layers/python-sdk/genai_core/security -p "*_test.py"   # A2 + A5 (file_validation): 18 + 35
python3 -m unittest discover -s tests/shared/layers/python-sdk/genai_core/langchain -p "*_test.py"  # A2.1: 7
python3 -m unittest discover -s tests/shared/layers/python-sdk/genai_core/citations -p "*_test.py"  # A3 (part)
python3 -m unittest discover -s tests/chatbot-api/functions/api-handler/routes -p "sessions_citations_test.py"  # A3 (part)
python3 -m unittest discover -s tests/shared/layers/python-sdk/genai_core/observability -p "*_test.py"  # A4: 45
python3 -m unittest discover -s tests/rag-engines/data-import/functions/upload-handler -p "*_test.py"  # A5 (upload-handler integration): 7
python3 -m unittest discover -s tests/github-config -p "*_test.py"  # A6: 19
make evaluate                 # A1 mock evaluation report
```

All of the above were actually run in this sandbox — **211/211 passing**
as of A6 (exact count re-verified this session, not assumed).

**Also recommended, NOT run here** (requires dependency install):
```
npm install && pip install -r pytest_requirements.txt
npm run test        # Jest (frontend/CDK unit tests)
npm run pytest       # the project's own pytest suite (tests/)
npm run integtest    # integtests/ (requires a deployed stack for some tests)
npx cdk synth        # infrastructure validation
```

## 10. Rollback Considerations

Not modified in A1–A6; this is standard CDK/CloudFormation rollback
behavior (a failed `cdk deploy` rolls back automatically; `cdk deploy
--rollback` for explicit rollback of a stuck stack). No custom rollback
tooling was added or reviewed in this session. **NOT INDEPENDENTLY
VERIFIED.**

## 11. Production Deployment Checklist (aspirational — not executed)

- [ ] `npm install` completes without error
- [ ] `npm run pytest` passes with real dependencies (not run here)
- [ ] `npx cdk synth` succeeds (not run here)
- [ ] Bedrock model access confirmed enabled for the target region
- [ ] `advancedMonitoring: true` set if A4's metrics/dashboard are wanted
- [ ] Pricing entries in `genai_core/observability/pricing.py` confirmed
      against `https://aws.amazon.com/bedrock/pricing/` for the actual
      models selected in config (only Claude 3.5 Sonnet/v2 is currently
      verified — see `OBSERVABILITY.md`)
- [ ] `npx cdk deploy` completes successfully (not run here)
- [ ] Post-deploy smoke test: upload a document, ask a question, confirm
      an answer with citations is returned (not run here)
- [ ] CloudWatch dashboard reviewed for the new AI-observability section
      (not run here)

None of the above checklist items have been completed in this sandbox.
This checklist is a starting point for whoever deploys this repository,
not a report of work already done.
