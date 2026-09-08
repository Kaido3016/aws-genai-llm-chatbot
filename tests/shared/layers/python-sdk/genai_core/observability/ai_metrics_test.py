import sys
import unittest
import importlib.util
import inspect
from pathlib import Path
from unittest.mock import patch

_CONFTEST_PATH = Path(__file__).parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "a4_ai_metrics_test_conftest", _CONFTEST_PATH
)
_conftest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _conftest
_spec.loader.exec_module(_conftest)

ai_metrics = _conftest.get_ai_metrics_module()


class _CapturingLogger:
    """Stands in for the module's Powertools Logger to capture exactly
    what would be emitted, without needing real CloudWatch/Lambda."""

    def __init__(self):
        self.calls = []

    def info(self, message, **kwargs):
        self.calls.append({"message": message, **kwargs})


class TestSuccessMetric(unittest.TestCase):
    def setUp(self):
        self.fake_logger = _CapturingLogger()
        self.patcher = patch.object(ai_metrics, "logger", self.fake_logger)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_emits_request_and_latency_metrics(self):
        ai_metrics.log_ai_request_success(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            mode="chain",
            latency_ms=842.371,
        )
        metric_types = [c["metric_type"] for c in self.fake_logger.calls]
        self.assertIn("ai_request_success", metric_types)
        self.assertIn("ai_request_latency", metric_types)

    def test_latency_is_rounded_and_numeric(self):
        ai_metrics.log_ai_request_success("model-x", "chain", 842.37123456)
        latency_call = next(
            c for c in self.fake_logger.calls if c["metric_type"] == "ai_request_latency"
        )
        self.assertEqual(latency_call["value"], 842.37)


class TestFailureMetric(unittest.TestCase):
    def setUp(self):
        self.fake_logger = _CapturingLogger()
        self.patcher = patch.object(ai_metrics, "logger", self.fake_logger)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_emits_failure_metric_with_error_class_name(self):
        try:
            raise ValueError("some detail that must never be logged")
        except ValueError as exc:
            ai_metrics.log_ai_request_failure(
                model_id="model-x",
                mode="chain",
                latency_ms=120.0,
                error_type=type(exc).__name__,
            )
        failure_call = next(
            c for c in self.fake_logger.calls if c["metric_type"] == "ai_request_failure"
        )
        self.assertEqual(failure_call["error_type"], "ValueError")
        # The exception MESSAGE must never appear anywhere in what was logged.
        self.assertNotIn(
            "some detail that must never be logged", str(self.fake_logger.calls)
        )

    def test_failure_also_emits_latency_when_available(self):
        ai_metrics.log_ai_request_failure("model-x", "chain", 50.0, "TimeoutError")
        metric_types = [c["metric_type"] for c in self.fake_logger.calls]
        self.assertIn("ai_request_latency", metric_types)

    def test_failure_without_latency_skips_latency_metric(self):
        ai_metrics.log_ai_request_failure("model-x", "chain", None, "TimeoutError")
        metric_types = [c["metric_type"] for c in self.fake_logger.calls]
        self.assertNotIn("ai_request_latency", metric_types)


class TestTokenMetrics(unittest.TestCase):
    def setUp(self):
        self.fake_logger = _CapturingLogger()
        self.patcher = patch.object(ai_metrics, "logger", self.fake_logger)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_input_tokens_emitted_when_present(self):
        ai_metrics.log_input_tokens("model-x", 512)
        self.assertEqual(len(self.fake_logger.calls), 1)
        self.assertEqual(self.fake_logger.calls[0]["value"], 512)

    def test_input_tokens_skipped_when_none(self):
        ai_metrics.log_input_tokens("model-x", None)
        self.assertEqual(len(self.fake_logger.calls), 0)

    def test_output_tokens_emitted_when_present(self):
        ai_metrics.log_output_tokens("model-x", 256)
        self.assertEqual(self.fake_logger.calls[0]["value"], 256)

    def test_output_tokens_skipped_when_none(self):
        ai_metrics.log_output_tokens("model-x", None)
        self.assertEqual(len(self.fake_logger.calls), 0)

    def test_never_invents_a_token_count(self):
        # There is no code path in log_input_tokens that computes or
        # derives a token count from something else (e.g. len() of a
        # string) — it only ever forwards the exact value it was given,
        # or skips when that value is None.
        source = inspect.getsource(ai_metrics.log_input_tokens)
        self.assertNotIn("len(", source)


class TestCostMetric(unittest.TestCase):
    def setUp(self):
        self.fake_logger = _CapturingLogger()
        self.patcher = patch.object(ai_metrics, "logger", self.fake_logger)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_cost_emitted_when_available(self):
        from decimal import Decimal

        ai_metrics.log_estimated_cost("model-x", Decimal("0.0123"))
        self.assertEqual(len(self.fake_logger.calls), 1)
        self.assertAlmostEqual(self.fake_logger.calls[0]["value"], 0.0123)

    def test_cost_skipped_entirely_when_none(self):
        ai_metrics.log_estimated_cost("model-x", None)
        self.assertEqual(len(self.fake_logger.calls), 0)


class TestRagMetrics(unittest.TestCase):
    def setUp(self):
        self.fake_logger = _CapturingLogger()
        self.patcher = patch.object(ai_metrics, "logger", self.fake_logger)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_retrieval_latency_uses_engine_not_workspace(self):
        ai_metrics.log_rag_retrieval_latency("aurora", 213.456)
        call = self.fake_logger.calls[0]
        self.assertEqual(call["engine"], "aurora")
        self.assertEqual(call["value"], 213.46)
        self.assertNotIn("workspace_id", call)
        self.assertNotIn("workspace", call)

    def test_documents_retrieved_count(self):
        ai_metrics.log_rag_documents_retrieved("opensearch", 5)
        self.assertEqual(self.fake_logger.calls[0]["value"], 5)


class TestSensitiveDataExclusionByDesign(unittest.TestCase):
    """The core privacy guarantee: every public function's SIGNATURE
    physically cannot accept a prompt, document content, session id, or
    user id — there is no field name in any of these signatures a caller
    could (mis)use to smuggle sensitive data through, and no **kwargs
    catch-all anywhere in the module."""

    PUBLIC_FUNCTIONS = [
        "log_ai_request_success",
        "log_ai_request_failure",
        "log_ai_request_latency",
        "log_input_tokens",
        "log_output_tokens",
        "log_estimated_cost",
        "log_rag_retrieval_latency",
        "log_rag_documents_retrieved",
    ]

    FORBIDDEN_PARAM_SUBSTRINGS = [
        "prompt",
        "session",
        "user_id",
        "userid",
        "document_id",
        "documentid",
        "workspace",
        "chunk",
        "path",
        "filename",
    ]

    def test_no_function_accepts_kwargs(self):
        for name in self.PUBLIC_FUNCTIONS:
            func = getattr(ai_metrics, name)
            sig = inspect.signature(func)
            kinds = [p.kind for p in sig.parameters.values()]
            self.assertNotIn(
                inspect.Parameter.VAR_KEYWORD,
                kinds,
                f"{name} must not accept **kwargs",
            )

    def test_no_function_has_a_forbidden_parameter_name(self):
        for name in self.PUBLIC_FUNCTIONS:
            func = getattr(ai_metrics, name)
            sig = inspect.signature(func)
            param_names = " ".join(sig.parameters.keys()).lower()
            for forbidden in self.FORBIDDEN_PARAM_SUBSTRINGS:
                self.assertNotIn(
                    forbidden,
                    param_names,
                    f"{name} has a suspicious parameter name containing "
                    f"'{forbidden}': {list(sig.parameters.keys())}",
                )

    def test_module_source_never_references_sensitive_field_names(self):
        # A broader static sweep of the whole module source — catches a
        # sensitive field being introduced inside a function body (e.g.
        # hardcoded into a log call) even if it didn't come from a
        # parameter.
        source = inspect.getsource(ai_metrics)
        for forbidden in ("session_id", "sessionId", "user_id", "userId"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
