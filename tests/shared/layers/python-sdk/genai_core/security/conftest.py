"""
conftest.py for genai_core.security tests.

Sandbox note (read before assuming this is needed in your dev environment):

In the environment this test suite was authored and executed in, `boto3`/
`botocore` are not installed and there is no network access to install
them. `genai_core/__init__.py` unconditionally imports `boto_config`,
which imports `botocore` — so even a zero-dependency submodule like
`genai_core.security.prompt_guard` cannot be imported the normal way
(`from genai_core.security.prompt_guard import ...`) without boto3/
botocore present, regardless of the submodule's own dependencies.

This is a real, minor architectural observation worth noting in
AUDIT.md/IMPLEMENTATION_SUMMARY.md: a package `__init__.py` that
unconditionally pulls in AWS SDK dependencies makes it impossible to unit
test pure-logic submodules in isolation without either installing boto3
or working around the import, as this file does.

In your actual dev environment (per pytest_requirements.txt, which does
include boto3), `from genai_core.security.prompt_guard import ...` will
work directly and this workaround is unnecessary — but it is harmless to
leave in place, since it only activates as a fallback.
"""

import importlib.util
import sys
from pathlib import Path

_PROMPT_GUARD_PATH = (
    Path(__file__).resolve().parents[6]
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "security"
    / "prompt_guard.py"
)
_FILE_VALIDATION_PATH = (
    Path(__file__).resolve().parents[6]
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "security"
    / "file_validation.py"
)


def _load_prompt_guard_standalone():
    """Load prompt_guard.py directly, bypassing genai_core/__init__.py,
    for environments without boto3/botocore installed. See module
    docstring above.
    """
    spec = importlib.util.spec_from_file_location(
        "genai_core_security_prompt_guard_standalone", _PROMPT_GUARD_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def get_prompt_guard():
    """Return the prompt_guard module, importing it the normal way if
    boto3/botocore are available, falling back to a standalone load
    (bypassing genai_core/__init__.py) if not.
    """
    try:
        from genai_core.security import prompt_guard

        return prompt_guard
    except ImportError:
        return _load_prompt_guard_standalone()


def _load_file_validation_standalone():
    spec = importlib.util.spec_from_file_location(
        "genai_core_security_file_validation_standalone", _FILE_VALIDATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def get_file_validation():
    """Same fallback pattern as get_prompt_guard(), for A5's
    genai_core.security.file_validation module."""
    try:
        from genai_core.security import file_validation

        return file_validation
    except ImportError:
        return _load_file_validation_standalone()
