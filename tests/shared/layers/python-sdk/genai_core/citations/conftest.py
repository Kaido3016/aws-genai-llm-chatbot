"""
conftest.py for genai_core.citations tests.

Same sandbox constraint as tests/shared/layers/python-sdk/genai_core/security/
conftest.py: genai_core/__init__.py unconditionally imports boto_config,
which imports botocore, so even genai_core.citations — which itself has
zero external dependencies (stdlib only: html, typing, urllib.parse) —
cannot be imported the normal way without boto3/botocore installed.

This only activates as a fallback: in the real dev environment
(pytest_requirements.txt includes boto3), `from genai_core import citations`
works directly.
"""

import importlib.util
import sys
from pathlib import Path

_CITATIONS_PATH = (
    Path(__file__).resolve().parents[6]
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "citations.py"
)


def _load_citations_standalone():
    spec = importlib.util.spec_from_file_location(
        "genai_core_citations_standalone", _CITATIONS_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def get_citations_module():
    try:
        from genai_core import citations

        return citations
    except ImportError:
        return _load_citations_standalone()
