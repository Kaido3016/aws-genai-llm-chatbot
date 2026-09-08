"""
Sandbox-only stub loader for
lib/rag-engines/data-import/functions/upload-handler/index.py, used to
exercise the REAL A5 validation wiring in process_record()/
_validate_uploaded_object() without boto3 or aws_lambda_powertools
installed.

Only activates as a fallback — see the equivalent, more heavily
commented pattern in
tests/chatbot-api/functions/api-handler/routes/_a3_sessions_stub_loader.py
and tests/shared/layers/python-sdk/genai_core/langchain/conftest.py for
the established approach this follows.

What's stubbed vs. real:
  - `boto3`                              -> STUBBED (a fake S3/Step
                                             Functions client whose
                                             behavior each test controls)
  - `aws_lambda_powertools`              -> STUBBED (Logger/Tracer are
                                             no-ops; SQSEvent/event_source/
                                             LambdaContext are trivial
                                             passthroughs sufficient for
                                             calling process_record()
                                             directly, which is what
                                             these tests do — they do not
                                             exercise the lambda_handler/
                                             event_source decorator path)
  - `genai_core.documents`,
    `genai_core.workspaces`              -> STUBBED (monkeypatchable
                                             per-test, matching how the
                                             project's own tests use
                                             mocker.patch on these)
  - `genai_core.security.file_validation`-> REAL source file — this is
                                             A5's actual validation logic
  - upload-handler `index.py`            -> REAL source file — the
                                             actual production code
                                             under test
"""

import sys
import types
import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
_INDEX_PATH = (
    _REPO_ROOT
    / "lib"
    / "rag-engines"
    / "data-import"
    / "functions"
    / "upload-handler"
    / "index.py"
)
_FILE_VALIDATION_PATH = (
    _REPO_ROOT
    / "lib"
    / "shared"
    / "layers"
    / "python-sdk"
    / "python"
    / "genai_core"
    / "security"
    / "file_validation.py"
)


class FakeS3Client:
    """Test-controlled stand-in for boto3's S3 client. Each test sets
    `.objects[key] = bytes` before calling process_record(), and can
    inspect `.copy_calls`/`.put_calls` afterward."""

    def __init__(self):
        self.objects = {}
        self.copy_calls = []
        self.put_calls = []

    def get_object(self, Bucket, Key):
        content = self.objects.get(Key, b"")

        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(content)}

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)


class FakeSfnClient:
    def __init__(self):
        self.start_execution_calls = []

    def start_execution(self, **kwargs):
        self.start_execution_calls.append(kwargs)
        return {"executionArn": "fake-arn"}


def _install_stubs_if_needed():
    # Idempotency guard: this function may be called more than once in
    # the same process. Without this sentinel, a second call's
    # `try: import boto3` etc. would succeed against the STUB modules
    # installed by the first call (since they're now in sys.modules),
    # and incorrectly report "real dependencies are available" — this
    # was a real bug caught while building this harness (see
    # upload_handler_validation_test.py's module-level, call-once usage,
    # which avoids the issue entirely; this guard is defense-in-depth
    # for any future caller that doesn't follow that convention).
    if getattr(sys.modules.get("genai_core"), "_a5_stub_sentinel", False):
        return True

    try:
        import boto3  # noqa: F401
        import aws_lambda_powertools  # noqa: F401
        import genai_core.documents  # noqa: F401
        import genai_core.workspaces  # noqa: F401
        import genai_core.security.file_validation  # noqa: F401

        return False
    except ImportError:
        pass

    # --- boto3 stub ---
    boto3_mod = types.ModuleType("boto3")
    _fake_s3 = FakeS3Client()
    _fake_sfn = FakeSfnClient()

    def _client(service_name, *args, **kwargs):
        if service_name == "s3":
            return _fake_s3
        if service_name == "stepfunctions":
            return _fake_sfn
        raise ValueError(f"Unexpected boto3 client requested: {service_name}")

    boto3_mod.client = _client
    sys.modules["boto3"] = boto3_mod

    # --- aws_lambda_powertools stub ---
    powertools_mod = types.ModuleType("aws_lambda_powertools")

    class _StubLogger:
        def _noop(self, *a, **kw):
            pass

        debug = info = warning = exception = _noop

    class _StubTracer:
        def capture_lambda_handler(self, func):
            return func

    powertools_mod.Logger = _StubLogger
    powertools_mod.Tracer = _StubTracer

    def _inject_lambda_context(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    powertools_mod.Logger.inject_lambda_context = staticmethod(
        lambda *a, **kw: (lambda f: f)
    )
    # inject_lambda_context is called as logger.inject_lambda_context(...)
    # on an INSTANCE in the real code, so attach it as an instance method
    # too:
    _StubLogger.inject_lambda_context = lambda self, *a, **kw: (lambda f: f)

    utilities_mod = types.ModuleType("aws_lambda_powertools.utilities")
    data_classes_mod = types.ModuleType(
        "aws_lambda_powertools.utilities.data_classes"
    )

    class _StubSQSEvent:
        def __init__(self, data):
            self.records = data.get("Records", [])

    def _event_source(data_class):
        def decorator(func):
            return func

        return decorator

    data_classes_mod.SQSEvent = _StubSQSEvent
    data_classes_mod.event_source = _event_source

    typing_mod = types.ModuleType("aws_lambda_powertools.utilities.typing")
    typing_mod.LambdaContext = object

    utilities_mod.data_classes = data_classes_mod
    utilities_mod.typing = typing_mod
    powertools_mod.utilities = utilities_mod

    sys.modules["aws_lambda_powertools"] = powertools_mod
    sys.modules["aws_lambda_powertools.utilities"] = utilities_mod
    sys.modules["aws_lambda_powertools.utilities.data_classes"] = data_classes_mod
    sys.modules["aws_lambda_powertools.utilities.typing"] = typing_mod

    # --- genai_core package stub (avoids executing the real
    # genai_core/__init__.py, which imports botocore) ---
    genai_core_mod = types.ModuleType("genai_core")
    sys.modules["genai_core"] = genai_core_mod

    documents_mod = types.ModuleType("genai_core.documents")
    documents_mod._documents = {}
    documents_mod._next_id = [1]

    def _create_document(**kwargs):
        doc_id = f"doc-{documents_mod._next_id[0]}"
        documents_mod._next_id[0] += 1
        documents_mod._documents[doc_id] = {"status": "processing", **kwargs}
        return {"document_id": doc_id}

    def _set_status(workspace_id, document_id, status):
        if document_id in documents_mod._documents:
            documents_mod._documents[document_id]["status"] = status

    documents_mod.create_document = _create_document
    documents_mod.set_status = _set_status
    sys.modules["genai_core.documents"] = documents_mod
    genai_core_mod.documents = documents_mod

    workspaces_mod = types.ModuleType("genai_core.workspaces")
    workspaces_mod._workspaces = {}
    workspaces_mod.get_workspace = lambda workspace_id: workspaces_mod._workspaces.get(
        workspace_id
    )
    sys.modules["genai_core.workspaces"] = workspaces_mod
    genai_core_mod.workspaces = workspaces_mod

    types_mod = types.ModuleType("genai_core.types")

    class _CommonError(Exception):
        pass

    types_mod.CommonError = _CommonError
    sys.modules["genai_core.types"] = types_mod
    genai_core_mod.types = types_mod

    security_mod = types.ModuleType("genai_core.security")
    fv_spec = importlib.util.spec_from_file_location(
        "genai_core.security.file_validation", _FILE_VALIDATION_PATH
    )
    fv_mod = importlib.util.module_from_spec(fv_spec)
    sys.modules["genai_core.security.file_validation"] = fv_mod
    fv_spec.loader.exec_module(fv_mod)
    security_mod.file_validation = fv_mod
    sys.modules["genai_core.security"] = security_mod
    genai_core_mod.security = security_mod

    genai_core_mod._a5_stub_sentinel = True

    return True


def load_upload_handler_module():
    """Returns (module, stubs_used, fake_s3, fake_sfn, documents_mod,
    workspaces_mod). The fakes are None when real boto3/genai_core are
    used (stubs_used=False) — in that case, tests would need to use
    mocker.patch instead, matching the project's real test conventions.
    """
    stubs_used = _install_stubs_if_needed()

    if not stubs_used:
        import boto3

        spec = importlib.util.spec_from_file_location(
            "upload_handler_index_real", _INDEX_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, False, None, None, None, None

    spec = importlib.util.spec_from_file_location(
        "upload_handler_index_stubbed", _INDEX_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return (
        module,
        True,
        sys.modules["boto3"].client("s3"),
        sys.modules["boto3"].client("stepfunctions"),
        sys.modules["genai_core.documents"],
        sys.modules["genai_core.workspaces"],
    )
