"""
conftest.py for genai_core.observability tests — same fallback pattern as
tests/shared/layers/python-sdk/genai_core/security/conftest.py.

`pricing.py` is pure stdlib and only needs the genai_core-package-avoidance
workaround. `ai_metrics.py` additionally needs `aws_lambda_powertools`
stubbed. Both only activate as a fallback if the real packages aren't
importable.
"""

import importlib.util
import sys
import types
from pathlib import Path

_OBSERVABILITY_DIR = (
    Path(__file__).resolve().parents[6]
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "observability"
)


def _load_standalone(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def get_pricing_module():
    try:
        from genai_core.observability import pricing

        return pricing
    except ImportError:
        return _load_standalone(
            "genai_core_observability_pricing_standalone",
            _OBSERVABILITY_DIR / "pricing.py",
        )


def get_ai_metrics_module():
    try:
        import aws_lambda_powertools  # noqa: F401
        from genai_core.observability import ai_metrics

        return ai_metrics
    except ImportError:
        pass

    # Stub aws_lambda_powertools.Logger if not already stubbed by another
    # test module in this process.
    if "aws_lambda_powertools" not in sys.modules:
        powertools_mod = types.ModuleType("aws_lambda_powertools")

        class _StubLogger:
            def __init__(self, *args, **kwargs):
                pass

            def debug(self, *a, **kw):
                pass

            def info(self, *a, **kw):
                pass

            def warning(self, *a, **kw):
                pass

            def exception(self, *a, **kw):
                pass

        powertools_mod.Logger = _StubLogger
        sys.modules["aws_lambda_powertools"] = powertools_mod

    return _load_standalone(
        "genai_core_observability_ai_metrics_standalone",
        _OBSERVABILITY_DIR / "ai_metrics.py",
    )
