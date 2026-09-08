"""
Sandbox-only stub loader for `routes.sessions` (the real
lib/chatbot-api/functions/api-handler/routes/sessions.py), used to
exercise the ACTUAL A3 citation-exposure logic in `get_session()` without
`pydantic`, `boto3`, or `aws_lambda_powertools` installed.

Only activates as a fallback — if the real packages are importable (the
project's actual dev environment, per pytest_requirements.txt), this
loads the real module the normal way and none of the stubs below are
used. This sandbox lacks network access to install those packages, which
is the only reason this exists.

What's stubbed vs. real, precisely:
  - `pydantic` (BaseModel/Field)        -> STUBBED (minimal, no real
                                             validation — see caveat below)
  - `aws_lambda_powertools`             -> STUBBED (Logger/Tracer/Router
                                             are no-ops sufficient for
                                             import + direct function calls)
  - `common.constant`, `common.validation`,
    `genai_core.types`, `genai_core.auth`,
    `genai_core.utils.json`             -> REAL source files, loaded as-is
                                             (they only need pydantic,
                                             which is stubbed above)
  - `genai_core.citations`              -> REAL source file (A3's actual
                                             allow-list logic — this is
                                             the module most worth
                                             exercising for real)
  - `genai_core.presign`, `genai_core.sessions` -> STUBBED (both need
    boto3 at import time in the real file; their specific functions are
    monkeypatched per-test anyway, exactly like `mocker.patch(...)` does
    in the project's real pytest-based sessions_test.py)
  - `routes.sessions`                   -> REAL source file — this is the
                                             actual production code under
                                             test.

CAVEAT this stub does NOT cover: `pydantic`'s real field validation
(`SAFE_FILE_NAME_REGEX`, `min_length`/`max_length`/`pattern` constraints on
`FileURequestValidation`/`WorkspaceIdValidation`). The stub `BaseModel`
just stores whatever kwargs it's given without validating them. This
means these tests verify the citation-exposure LOGIC in `get_session()`
correctly, but do NOT verify that `WorkspaceIdValidation(**{"workspaceId": id})`
would actually reject a malformed `id` the way real pydantic would — that
specific guarantee is unchanged from before A3 and is exercised by the
project's real pytest suite (`pytest tests/`) in an environment with
pydantic installed, not here.
"""

import sys
import types
import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
_API_HANDLER_DIR = (
    _REPO_ROOT / "lib" / "chatbot-api" / "functions" / "api-handler"
)
_SHARED_PYTHON_DIR = (
    _REPO_ROOT / "lib" / "shared" / "layers" / "python-sdk" / "python"
)


def _install_stubs_if_needed():
    try:
        import pydantic  # noqa: F401
        import aws_lambda_powertools  # noqa: F401
        import boto3  # noqa: F401

        return False
    except ImportError:
        pass

    # --- pydantic stub ---
    pydantic_mod = types.ModuleType("pydantic")

    class _StubField:
        def __init__(self, *args, **kwargs):
            self.default = kwargs.get("default")

    class _StubBaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    pydantic_mod.BaseModel = _StubBaseModel
    pydantic_mod.Field = lambda *a, **kw: _StubField(*a, **kw)
    sys.modules["pydantic"] = pydantic_mod

    # --- aws_lambda_powertools stub ---
    powertools_mod = types.ModuleType("aws_lambda_powertools")

    class _StubLogger:
        def debug(self, *a, **kw):
            pass

        def info(self, *a, **kw):
            pass

        def warning(self, *a, **kw):
            pass

        def exception(self, *a, **kw):
            pass

    class _StubTracer:
        def capture_method(self, func):
            return func

    powertools_mod.Logger = _StubLogger
    powertools_mod.Tracer = _StubTracer

    event_handler_mod = types.ModuleType("aws_lambda_powertools.event_handler")
    appsync_mod = types.ModuleType("aws_lambda_powertools.event_handler.appsync")

    class _StubRouter:
        def __init__(self):
            self.current_event = {}

        def resolver(self, field_name=None):
            def decorator(func):
                return func

            return decorator

    appsync_mod.Router = _StubRouter
    event_handler_mod.appsync = appsync_mod
    powertools_mod.event_handler = event_handler_mod

    sys.modules["aws_lambda_powertools"] = powertools_mod
    sys.modules["aws_lambda_powertools.event_handler"] = event_handler_mod
    sys.modules["aws_lambda_powertools.event_handler.appsync"] = appsync_mod

    # --- genai_core package stub (avoid executing the real __init__.py,
    # which imports botocore) ---
    genai_core_mod = types.ModuleType("genai_core")
    sys.modules["genai_core"] = genai_core_mod

    # --- genai_core.presign: stub (only generate_user_presigned_get is
    # ever called, by get_file(), which these tests don't exercise) ---
    presign_mod = types.ModuleType("genai_core.presign")
    presign_mod.generate_user_presigned_get = lambda *a, **kw: None
    sys.modules["genai_core.presign"] = presign_mod
    genai_core_mod.presign = presign_mod

    # --- genai_core.sessions: stub with monkeypatchable functions ---
    sessions_backend_mod = types.ModuleType("genai_core.sessions")
    sessions_backend_mod.get_session = lambda session_id, user_id: {}
    sessions_backend_mod.list_sessions_by_user_id = lambda user_id: []
    sessions_backend_mod.delete_user_sessions = lambda user_id: None
    sessions_backend_mod.delete_session = lambda session_id, user_id: None
    sys.modules["genai_core.sessions"] = sessions_backend_mod
    genai_core_mod.sessions = sessions_backend_mod

    # --- real source files, loaded once pydantic is stubbed ---
    def _load_real(name, path, parent_pkg=None, attr_name=None):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        if parent_pkg is not None:
            setattr(parent_pkg, attr_name, module)
        return module

    genai_core_types_mod = _load_real(
        "genai_core.types",
        _SHARED_PYTHON_DIR / "genai_core" / "types.py",
        genai_core_mod,
        "types",
    )

    genai_core_auth_mod = _load_real(
        "genai_core.auth",
        _SHARED_PYTHON_DIR / "genai_core" / "auth.py",
        genai_core_mod,
        "auth",
    )

    genai_core_utils_pkg = types.ModuleType("genai_core.utils")
    sys.modules["genai_core.utils"] = genai_core_utils_pkg
    genai_core_mod.utils = genai_core_utils_pkg
    _load_real(
        "genai_core.utils.json",
        _SHARED_PYTHON_DIR / "genai_core" / "utils" / "json.py",
        genai_core_utils_pkg,
        "json",
    )

    genai_core_citations_mod = _load_real(
        "genai_core.citations",
        _SHARED_PYTHON_DIR / "genai_core" / "citations.py",
        genai_core_mod,
        "citations",
    )

    common_pkg = types.ModuleType("common")
    sys.modules["common"] = common_pkg
    common_constant_mod = _load_real(
        "common.constant",
        _API_HANDLER_DIR / "common" / "constant.py",
        common_pkg,
        "constant",
    )
    _load_real(
        "common.validation",
        _API_HANDLER_DIR / "common" / "validation.py",
        common_pkg,
        "validation",
    )

    return True


def load_sessions_route_module():
    """Import (or stub-load) routes.sessions and return
    (module, stubs_used, sessions_backend_stub_module_or_None).

    When stubs are used, the returned third element is the stub
    `genai_core.sessions` module — tests monkeypatch its `get_session`
    attribute directly (equivalent to `mocker.patch("genai_core.sessions.get_session", ...)`
    in the project's real pytest-based tests).
    """
    stubs_used = _install_stubs_if_needed()

    if not stubs_used:
        sys.path.insert(0, str(_API_HANDLER_DIR))
        import routes.sessions as sessions_route

        import genai_core.sessions as sessions_backend

        return sessions_route, False, sessions_backend

    routes_pkg = types.ModuleType("routes")
    sys.modules["routes"] = routes_pkg

    spec = importlib.util.spec_from_file_location(
        "routes.sessions", _API_HANDLER_DIR / "routes" / "sessions.py"
    )
    sessions_route = importlib.util.module_from_spec(spec)
    sys.modules["routes.sessions"] = sessions_route
    spec.loader.exec_module(sessions_route)

    return sessions_route, True, sys.modules["genai_core.sessions"]
