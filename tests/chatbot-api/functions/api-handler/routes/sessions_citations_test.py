"""
A3 tests — exercises the REAL `get_session()` resolver in
lib/chatbot-api/functions/api-handler/routes/sessions.py, verifying:

  - a regular authenticated user receives `citations` but not `metadata`
  - an admin/workspace_manager continues to receive full `metadata`
    (unchanged) AND the new `citations` field
  - session ownership (workspace/user isolation) is unaffected — a
    session lookup for a different user's session yields no data,
    exactly as before A3
  - a forged/malformed stored citation record cannot leak internal
    fields, via `reapply_citation_allowlist`

VERIFICATION LEVEL: see `_a3_sessions_stub_loader.py`'s module docstring
for exactly what is real production code vs. stubbed in this sandbox
(pydantic and aws_lambda_powertools are stubbed; `genai_core.auth`,
`genai_core.utils.json`, `genai_core.citations`, `common.constant`,
`common.validation`, and `routes.sessions` itself are all loaded from
their real source files). Pydantic's actual field-validation behavior is
NOT exercised here — see that file's caveat.

`genai_core.sessions.get_session` (the DynamoDB lookup) is monkeypatched
per test, exactly as the project's real pytest-based
`tests/chatbot-api/functions/api-handler/routes/sessions_test.py` does
with `mocker.patch(...)` — this file does not replace or duplicate that
one; it adds coverage specific to A3's new behavior.
"""

import sys
import unittest
import importlib.util
from pathlib import Path

_LOADER_PATH = Path(__file__).parent / "_a3_sessions_stub_loader.py"
_spec = importlib.util.spec_from_file_location(
    "a3_sessions_stub_loader", _LOADER_PATH
)
_loader = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _loader
_spec.loader.exec_module(_loader)

sessions_route, STUBS_USED, sessions_backend = _loader.load_sessions_route_module()


def _set_identity(user_id, roles):
    sessions_route.router.current_event = {
        "identity": {
            "sub": user_id,
            "claims": {"cognito:groups": roles},
        }
    }


def _make_session(additional_kwargs):
    return {
        "SessionId": "session-1",
        "StartTime": "2026-01-01T00:00:00",
        "History": [
            {"type": "human", "data": {"content": "What is the policy?"}},
            {
                "type": "ai",
                "data": {
                    "content": "Employees may work remotely up to 3 days.",
                    "additional_kwargs": additional_kwargs,
                },
            },
        ],
    }


SAFE_CITATIONS = [
    {
        "documentTitle": "Remote Work Policy",
        "sourceUrl": None,
        "snippet": "Employees may work remotely up to 3 days per week.",
        "citationIndex": 1,
    }
]

FULL_ADMIN_METADATA = {
    "modelId": "bedrock.anthropic.claude-3",
    "modelKwargs": {"temperature": 0.5},
    "sessionId": "session-1",
    "userId": "user-1",
    "documents": [
        {
            "page_content": "Employees may work remotely up to 3 days per week.",
            "metadata": {
                "chunk_id": "c1",
                "workspace_id": "ws-1",
                "document_id": "doc-1",
                "path": "internal/remote-work.pdf",
                "title": "Remote Work Policy",
                "score": 0.95,
            },
        }
    ],
    "prompts": ["SYSTEM: You are a helpful assistant... {context}"],
    "citations": SAFE_CITATIONS,
}


class TestRegularUserGetsCitationsNotMetadata(unittest.TestCase):
    def test_regular_user_receives_citations_but_no_metadata(self):
        session = _make_session(additional_kwargs={"citations": SAFE_CITATIONS})
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("user-1", ["user"])

        result = sessions_route.get_session("session-1")

        ai_message = result["history"][1]
        self.assertNotIn("metadata", ai_message)
        self.assertEqual(ai_message["citations"], SAFE_CITATIONS)

    def test_regular_user_gets_no_citations_key_when_none_stored(self):
        # Non-RAG chat turn: no citations were ever persisted for this
        # message (matches base.py's "elif citations:" — nothing is
        # written when there are none).
        session = _make_session(additional_kwargs={})
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("user-1", ["user"])

        result = sessions_route.get_session("session-1")

        ai_message = result["history"][1]
        self.assertNotIn("metadata", ai_message)
        self.assertNotIn("citations", ai_message)


class TestAdminGetsFullMetadataPlusCitations(unittest.TestCase):
    def test_admin_receives_both_metadata_and_citations_unchanged(self):
        session = _make_session(additional_kwargs=FULL_ADMIN_METADATA)
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("admin-user", ["admin"])

        result = sessions_route.get_session("session-1")

        ai_message = result["history"][1]
        self.assertIn("metadata", ai_message)  # unchanged existing behavior
        self.assertIn("modelId", ai_message["metadata"])  # full blob, not redacted
        self.assertIn("prompts", ai_message["metadata"])
        self.assertEqual(ai_message["citations"], SAFE_CITATIONS)

    def test_workspace_manager_also_gets_full_metadata(self):
        session = _make_session(additional_kwargs=FULL_ADMIN_METADATA)
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("wm-user", ["workspace_manager"])

        result = sessions_route.get_session("session-1")
        self.assertIn("metadata", result["history"][1])


class TestSessionOwnershipUnaffected(unittest.TestCase):
    def test_session_lookup_for_wrong_user_returns_none(self):
        # genai_core.sessions.get_session(session_id, user_id) is a
        # DynamoDB get_item on the composite key (SessionId, UserId).
        # Simulate "not this user's session" by having the backend
        # return {} (exactly what a real key-mismatch get_item does),
        # regardless of what citations exist for the real owner.
        sessions_backend.get_session = lambda session_id, user_id: {}
        _set_identity("someone-else", ["user"])

        result = sessions_route.get_session("session-1")
        self.assertIsNone(result)

    def test_backend_is_called_with_the_authenticated_users_id_not_client_input(self):
        captured = {}

        def fake_get_session(session_id, user_id):
            captured["session_id"] = session_id
            captured["user_id"] = user_id
            return _make_session({"citations": SAFE_CITATIONS})

        sessions_backend.get_session = fake_get_session
        _set_identity("the-real-authenticated-user", ["user"])

        sessions_route.get_session("session-1")

        # The user_id passed to the backend must come from the
        # authenticated identity, not be something a client could
        # override via the `id` argument.
        self.assertEqual(captured["user_id"], "the-real-authenticated-user")
        self.assertEqual(captured["session_id"], "session-1")


class TestMaliciousStoredMetadataContainment(unittest.TestCase):
    """Simulates a forged/legacy additional_kwargs blob that somehow
    contains citation-shaped data with extra internal fields injected —
    e.g. from a future write-path bug, or hand-crafted DynamoDB data —
    to prove sessions.py's reapply_citation_allowlist() (read-time
    defense-in-depth, independent of build_safe_citations at write time)
    actually gets invoked and strips them before they reach the client.
    """

    def test_forged_internal_fields_in_stored_citations_are_stripped(self):
        forged_additional_kwargs = {
            "citations": [
                {
                    "documentTitle": "Looks safe",
                    "sourceUrl": None,
                    "snippet": "safe snippet",
                    "citationIndex": 1,
                    # forged/leaked internal fields that must never
                    # reach a regular user, even if present in storage
                    "workspace_id": "ws-1",
                    "document_id": "doc-1",
                    "chunk_id": "c1",
                    "score": 0.99,
                    "modelKwargs": {"temperature": 0.9},
                }
            ]
        }
        session = _make_session(additional_kwargs=forged_additional_kwargs)
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("user-1", ["user"])

        result = sessions_route.get_session("session-1")
        citation = result["history"][1]["citations"][0]

        self.assertNotIn("workspace_id", citation)
        self.assertNotIn("document_id", citation)
        self.assertNotIn("chunk_id", citation)
        self.assertNotIn("score", citation)
        self.assertNotIn("modelKwargs", citation)
        self.assertEqual(
            set(citation.keys()),
            {"documentTitle", "sourceUrl", "snippet", "citationIndex"},
        )

    def test_forged_javascript_source_url_in_storage_is_nulled(self):
        forged_additional_kwargs = {
            "citations": [
                {
                    "documentTitle": "Evil",
                    "sourceUrl": "javascript:alert(1)",
                    "snippet": "x",
                    "citationIndex": 1,
                }
            ]
        }
        session = _make_session(additional_kwargs=forged_additional_kwargs)
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("user-1", ["user"])

        result = sessions_route.get_session("session-1")
        self.assertIsNone(result["history"][1]["citations"][0]["sourceUrl"])

    def test_malformed_additional_kwargs_does_not_crash(self):
        # additional_kwargs stored as a raw string (matches the OLD,
        # pre-A3 shape seen in the existing sessions_test.py fixture —
        # "additional_kwargs": "additional_kwargs") rather than a dict.
        session = _make_session(additional_kwargs="not-a-dict")
        sessions_backend.get_session = lambda session_id, user_id: session
        _set_identity("user-1", ["user"])

        result = sessions_route.get_session("session-1")
        ai_message = result["history"][1]
        self.assertNotIn("citations", ai_message)
        self.assertNotIn("metadata", ai_message)


if __name__ == "__main__":
    unittest.main()
