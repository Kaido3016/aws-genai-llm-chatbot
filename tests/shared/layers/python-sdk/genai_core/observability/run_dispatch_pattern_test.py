"""
A4 — isolated regression test for the run()/_run_dispatch() control-flow
pattern added to ModelAdapter in
lib/model-interfaces/langchain/functions/request-handler/adapters/base/base.py.

WHY THIS TEST EXISTS AND WHAT IT DOES NOT COVER (read first):

`base.py` imports langchain, langchain_core, langchain_aws, and several
genai_core submodules that themselves require boto3/botocore — a much
larger dependency surface than genai_core.langchain.workspace_retriever
(which A2.1 fully stubbed and exercised for real). Stubbing all of it
here would require a large, fragile mock surface whose own correctness
would be hard to trust — more likely to produce false confidence than
real verification.

Instead, this test reproduces the EXACT control-flow shape that was
added to `run()` — timing, try/except-log-reraise, and the specific
positional-argument-forwarding pattern used to call `_run_dispatch()` —
as a minimal, standalone, fully executable reproduction. It is NOT a
test of base.py itself; it is a regression guard for the *pattern*,
written after that pattern's real version (in base.py) was manually
reviewed and confirmed structurally identical.

This test already caught one real bug during development: an earlier
version of the forwarding call in base.py mixed keyword arguments with
`*args`, which raises `TypeError: got multiple values for argument`
whenever `*args` is non-empty. Reproduced and fixed here; base.py now
uses the same all-positional-then-*args-then-**kwargs form verified
below.

VERIFICATION LEVEL: this confirms the pattern's correctness in isolation
(LOCALLY VERIFIED). It does NOT confirm base.py's `run()` method
actually behaves this way at import/runtime — that requires the real
dependencies and is UNVERIFIED — REQUIRES DEPENDENCY INSTALL.
"""

import time
import unittest


class _FakeAiMetrics:
    """Stands in for genai_core.observability.ai_metrics — records calls
    instead of emitting real log lines, so tests can assert on exactly
    what would have been emitted."""

    def __init__(self):
        self.success_calls = []
        self.failure_calls = []

    def log_ai_request_success(self, model_id, mode, latency_ms):
        self.success_calls.append(
            {"model_id": model_id, "mode": mode, "latency_ms": latency_ms}
        )

    def log_ai_request_failure(self, model_id, mode, latency_ms, error_type):
        self.failure_calls.append(
            {
                "model_id": model_id,
                "mode": mode,
                "latency_ms": latency_ms,
                "error_type": error_type,
            }
        )


class _FakeAdapter:
    """Reproduces the exact shape of ModelAdapter.run()/_run_dispatch()
    added in A4 — see base.py for the real (structurally identical)
    version. `dispatch_impl` is swappable per test to simulate success,
    failure, or argument-forwarding edge cases.
    """

    def __init__(self, ai_metrics, model_id="model-x", mode="chain"):
        self.ai_metrics = ai_metrics
        self.model_id = model_id
        self._mode = mode
        self.dispatch_impl = lambda *a, **kw: {"content": "ok"}
        self.dispatch_calls = []

    def run(self, prompt, workspace_id=None, images=None, documents=None,
            videos=None, user_groups=None, system_prompts=None, *args, **kwargs):
        start_time = time.monotonic()
        try:
            response = self._run_dispatch(
                prompt,
                workspace_id,
                images,
                documents,
                videos,
                user_groups,
                system_prompts,
                *args,
                **kwargs,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            self.ai_metrics.log_ai_request_failure(
                model_id=self.model_id,
                mode=self._mode,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
            )
            raise

        latency_ms = (time.monotonic() - start_time) * 1000
        self.ai_metrics.log_ai_request_success(
            model_id=self.model_id, mode=self._mode, latency_ms=latency_ms
        )
        return response

    def _run_dispatch(self, *args, **kwargs):
        self.dispatch_calls.append({"args": args, "kwargs": kwargs})
        return self.dispatch_impl(*args, **kwargs)


class TestSuccessPath(unittest.TestCase):
    def test_success_emits_success_metric_not_failure(self):
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)
        result = adapter.run("hello")
        self.assertEqual(result, {"content": "ok"})
        self.assertEqual(len(metrics.success_calls), 1)
        self.assertEqual(len(metrics.failure_calls), 0)

    def test_success_metric_has_positive_latency(self):
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)
        adapter.run("hello")
        self.assertGreaterEqual(metrics.success_calls[0]["latency_ms"], 0)

    def test_success_metric_carries_model_and_mode(self):
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics, model_id="my-model", mode="rag")
        adapter.run("hello")
        self.assertEqual(metrics.success_calls[0]["model_id"], "my-model")
        self.assertEqual(metrics.success_calls[0]["mode"], "rag")


class TestFailurePath(unittest.TestCase):
    def test_failure_emits_failure_metric_and_reraises(self):
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)
        adapter.dispatch_impl = lambda *a, **kw: (_ for _ in ()).throw(
            ValueError("boom")
        )

        with self.assertRaises(ValueError):
            adapter.run("hello")

        self.assertEqual(len(metrics.failure_calls), 1)
        self.assertEqual(len(metrics.success_calls), 0)

    def test_failure_metric_uses_exception_class_name_not_message(self):
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)
        adapter.dispatch_impl = lambda *a, **kw: (_ for _ in ()).throw(
            ValueError("sensitive detail that must not be logged")
        )

        with self.assertRaises(ValueError):
            adapter.run("hello")

        self.assertEqual(metrics.failure_calls[0]["error_type"], "ValueError")
        self.assertNotIn(
            "sensitive detail", str(metrics.failure_calls[0])
        )

    def test_exception_propagates_unchanged_to_caller(self):
        # This is the core "do not break existing behavior" guarantee:
        # the caller (index.py's handle_run(), which has no try/except
        # of its own) must see the exact same exception it always would.
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)

        class CustomError(Exception):
            pass

        adapter.dispatch_impl = lambda *a, **kw: (_ for _ in ()).throw(
            CustomError("specific failure")
        )

        with self.assertRaises(CustomError) as ctx:
            adapter.run("hello")
        self.assertEqual(str(ctx.exception), "specific failure")


class TestArgumentForwarding(unittest.TestCase):
    """Regression test for the real bug found during development: mixing
    keyword arguments with a non-empty *args in the forwarding call
    raises TypeError. This locks in the fixed, all-positional form."""

    def test_forwards_keyword_style_call_correctly(self):
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)
        adapter.run(
            "prompt-text",
            workspace_id="ws-1",
            images=[],
            documents=[],
            videos=[],
            user_groups=["user"],
            system_prompts={},
        )
        call = adapter.dispatch_calls[0]
        self.assertEqual(
            call["args"],
            ("prompt-text", "ws-1", [], [], [], ["user"], {}),
        )

    def test_forwards_extra_args_and_kwargs_without_raising(self):
        # This is exactly the case that failed with the buggy
        # keyword-then-*args form: TypeError: got multiple values for
        # argument 'workspace_id'.
        metrics = _FakeAiMetrics()
        adapter = _FakeAdapter(metrics)
        adapter.run(
            "prompt-text",
            "ws-1",
            [],
            [],
            [],
            ["user"],
            {},
            "extra-positional",
            extra_kw="value",
        )
        call = adapter.dispatch_calls[0]
        self.assertIn("extra-positional", call["args"])
        self.assertEqual(call["kwargs"], {"extra_kw": "value"})
        # And critically: no exception was raised getting here.
        self.assertEqual(len(metrics.success_calls), 1)


if __name__ == "__main__":
    unittest.main()
