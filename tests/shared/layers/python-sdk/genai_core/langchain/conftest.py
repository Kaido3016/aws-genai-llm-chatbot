"""
Sandbox-only dependency stubs for testing `genai_core.langchain.workspace_retriever`
(specifically `SanitizingWorkspaceRetriever`, added in A2.1) without
`langchain`, `aws_lambda_powertools`, or `boto3` installed.

IMPORTANT — this only activates as a fallback:

`_install_stubs_if_needed()` first tries the real imports. If they
succeed (i.e. you're running this in the project's actual dev environment
per `pytest_requirements.txt`), this function does nothing and the test
below exercises the real `genai_core.langchain.workspace_retriever` module
imported the normal way. The stubs below exist solely because this
particular sandbox has no network access to install `langchain`/`boto3`/
`aws_lambda_powertools` — they are not a replacement for running the real
test suite (`pytest tests/`) with real dependencies installed, which is
the actually-authoritative verification and has NOT been performed here.

This is a materially bigger stub than the one in
`tests/shared/layers/python-sdk/genai_core/security/conftest.py` (which
only had to work around `genai_core/__init__.py` importing `botocore`)
because `workspace_retriever.py` also imports `langchain` and
`aws_lambda_powertools` directly. Both gaps are documented here rather
than silently worked around.
"""

import sys
import types
import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[6]
_WORKSPACE_RETRIEVER_PATH = (
    _REPO_ROOT
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "langchain"
    / "workspace_retriever.py"
)
_PROMPT_GUARD_PATH = (
    _REPO_ROOT
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "security"
    / "prompt_guard.py"
)
_AI_METRICS_PATH = (
    _REPO_ROOT
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "observability"
    / "ai_metrics.py"
)


class _StubDocument:
    """Stands in for langchain_core's Document: a plain page_content +
    metadata container. The real Document has more behavior (e.g.
    serialization), none of which workspace_retriever.py relies on."""

    def __init__(self, page_content, metadata=None):
        self.page_content = page_content
        self.metadata = metadata or {}

    def __eq__(self, other):
        return (
            isinstance(other, _StubDocument)
            and self.page_content == other.page_content
            and self.metadata == other.metadata
        )


class _StubBaseRetriever:
    """Stands in for langchain's pydantic-based BaseRetriever. Real
    BaseRetriever validates fields declared as class annotations
    (workspace_id: str, documents_found: List[Document] = []); this stub
    just accepts and stores whatever kwargs are passed, which is
    sufficient for exercising _get_relevant_documents()."""

    documents_found = []

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _install_stubs_if_needed():
    """Returns True if stubs were installed (real deps unavailable),
    False if real langchain/aws_lambda_powertools/genai_core are already
    importable and nothing was stubbed."""
    try:
        import langchain.schema  # noqa: F401
        import langchain.callbacks.manager  # noqa: F401
        import aws_lambda_powertools  # noqa: F401
        import genai_core.semantic_search  # noqa: F401
        import genai_core.security.prompt_guard  # noqa: F401
        import genai_core.observability.ai_metrics  # noqa: F401

        return False
    except ImportError:
        pass

    # --- aws_lambda_powertools.Logger stub ---
    powertools_mod = types.ModuleType("aws_lambda_powertools")

    class _StubLogger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def exception(self, *args, **kwargs):
            pass

    powertools_mod.Logger = _StubLogger
    sys.modules["aws_lambda_powertools"] = powertools_mod

    # --- langchain.schema / langchain.callbacks.manager stubs ---
    schema_mod = types.ModuleType("langchain.schema")
    schema_mod.BaseRetriever = _StubBaseRetriever
    schema_mod.Document = _StubDocument

    callbacks_manager_mod = types.ModuleType("langchain.callbacks.manager")
    callbacks_manager_mod.CallbackManagerForRetrieverRun = object

    callbacks_mod = types.ModuleType("langchain.callbacks")
    callbacks_mod.manager = callbacks_manager_mod

    langchain_mod = types.ModuleType("langchain")
    langchain_mod.schema = schema_mod
    langchain_mod.callbacks = callbacks_mod

    sys.modules["langchain"] = langchain_mod
    sys.modules["langchain.schema"] = schema_mod
    sys.modules["langchain.callbacks"] = callbacks_mod
    sys.modules["langchain.callbacks.manager"] = callbacks_manager_mod

    # --- genai_core package stub (avoids executing the real
    # genai_core/__init__.py, which imports botocore) ---
    genai_core_mod = types.ModuleType("genai_core")
    sys.modules["genai_core"] = genai_core_mod

    # --- genai_core.semantic_search stub ---
    semantic_search_mod = types.ModuleType("genai_core.semantic_search")

    def _stub_semantic_search(workspace_id, query, limit=3, full_response=False):
        raise AssertionError(
            "semantic_search() should be monkeypatched per-test; the "
            "default stub is intentionally not a working implementation."
        )

    semantic_search_mod.semantic_search = _stub_semantic_search
    sys.modules["genai_core.semantic_search"] = semantic_search_mod
    genai_core_mod.semantic_search = semantic_search_mod

    # --- genai_core.security.prompt_guard: load the REAL module (pure
    # stdlib, no stubbing needed for its own sake), just registered under
    # the package path workspace_retriever.py expects to import from. ---
    security_mod = types.ModuleType("genai_core.security")
    spec = importlib.util.spec_from_file_location(
        "genai_core.security.prompt_guard", _PROMPT_GUARD_PATH
    )
    prompt_guard_mod = importlib.util.module_from_spec(spec)
    sys.modules["genai_core.security.prompt_guard"] = prompt_guard_mod
    spec.loader.exec_module(prompt_guard_mod)
    security_mod.prompt_guard = prompt_guard_mod
    sys.modules["genai_core.security"] = security_mod
    genai_core_mod.security = security_mod

    # --- genai_core.observability.ai_metrics: load the REAL module (A4).
    # It only needs aws_lambda_powertools.Logger, already stubbed above. ---
    observability_mod = types.ModuleType("genai_core.observability")
    ai_metrics_spec = importlib.util.spec_from_file_location(
        "genai_core.observability.ai_metrics", _AI_METRICS_PATH
    )
    ai_metrics_mod = importlib.util.module_from_spec(ai_metrics_spec)
    sys.modules["genai_core.observability.ai_metrics"] = ai_metrics_mod
    ai_metrics_spec.loader.exec_module(ai_metrics_mod)
    observability_mod.ai_metrics = ai_metrics_mod
    sys.modules["genai_core.observability"] = observability_mod
    genai_core_mod.observability = observability_mod

    return True


def load_workspace_retriever_module():
    """Import (or stub-load) genai_core.langchain.workspace_retriever and
    return the module, along with whether stubs were used."""
    stubs_used = _install_stubs_if_needed()

    if not stubs_used:
        import genai_core.langchain.workspace_retriever as wr

        return wr, False

    genai_core_langchain_mod = types.ModuleType("genai_core.langchain")
    sys.modules["genai_core.langchain"] = genai_core_langchain_mod
    sys.modules["genai_core"].langchain = genai_core_langchain_mod

    spec = importlib.util.spec_from_file_location(
        "genai_core.langchain.workspace_retriever", _WORKSPACE_RETRIEVER_PATH
    )
    wr = importlib.util.module_from_spec(spec)
    sys.modules["genai_core.langchain.workspace_retriever"] = wr
    spec.loader.exec_module(wr)
    return wr, True
