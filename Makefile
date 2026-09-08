# Minimal Makefile introduced alongside the A1 RAG evaluation framework.
# Scope is deliberately limited to what was actually built in this pass
# (see IMPLEMENTATION_SUMMARY.md) rather than a full developer-experience
# Makefile — additional targets (install/dev/lint/security/build) belong
# to a separate, later piece of work and shouldn't be implied as done here.

.PHONY: evaluate evaluate-degraded test-evaluation test-prompt-injection test-workspace-retriever test-all

## Run the offline RAG evaluation harness (no AWS credentials required).
evaluate:
	python3 -m evaluation.run_evaluation --mode mock --generator good

## Same, but using the deliberately-degraded mock generator, to see the
## harness detect a regression (groundedness/safety/citation scores drop).
evaluate-degraded:
	python3 -m evaluation.run_evaluation --mode mock --generator degraded

## Run the evaluation framework's own unit tests (metrics + harness logic).
test-evaluation:
	python3 -m unittest discover -s evaluation/tests -p "test_*.py" -v

## Run the A2 prompt-injection / RAG-poisoning adversarial test suite.
## Standard-library only — does not require boto3/langchain/pytest.
test-prompt-injection:
	python3 -m unittest discover -s tests/shared/layers/python-sdk/genai_core/security -p "*_test.py" -v

## Run the A2.1 integration regression tests (SanitizingWorkspaceRetriever).
## Uses real production source; falls back to dependency stubs only if
## langchain/aws_lambda_powertools/boto3 aren't installed (see that
## directory's conftest.py for exactly what is stubbed and why).
test-workspace-retriever:
	python3 -m unittest discover -s tests/shared/layers/python-sdk/genai_core/langchain -p "*_test.py" -v

## Run everything built in this pass (A1 + A2 + A2.1) in one go.
test-all: test-evaluation test-prompt-injection test-workspace-retriever
