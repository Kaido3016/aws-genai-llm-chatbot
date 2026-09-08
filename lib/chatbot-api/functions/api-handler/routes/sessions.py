from pydantic import BaseModel, Field
from common.constant import SAFE_FILE_NAME_REGEX, UserRole
from common.validation import WorkspaceIdValidation
import genai_core.presign
import genai_core.sessions
import genai_core.types
import genai_core.auth
import genai_core.utils.json
import genai_core.citations
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.event_handler.appsync import Router
import json

tracer = Tracer()
router = Router()
logger = Logger()


class FileURequestValidation(BaseModel):
    fileName: str = Field(min_length=1, max_length=500, pattern=SAFE_FILE_NAME_REGEX)


@router.resolver(field_name="getFileURL")
@tracer.capture_method
def get_file(fileName: str):
    FileURequestValidation(**{"fileName": fileName})
    user_id = genai_core.auth.get_user_id(router)
    result = genai_core.presign.generate_user_presigned_get(
        user_id, fileName, expiration=600
    )

    logger.info("Generated pre-signed for " + fileName)
    return result


@router.resolver(field_name="listSessions")
@tracer.capture_method
def get_sessions():
    user_id = genai_core.auth.get_user_id(router)
    if user_id is None:
        raise genai_core.types.CommonError("User not found")

    sessions = genai_core.sessions.list_sessions_by_user_id(user_id)

    return [
        {
            "id": session.get("SessionId"),
            "title": session.get("History", [{}])[0]
            .get("data", {})
            .get("content", "<no title>"),
            "startTime": f'{session.get("StartTime")}Z',
        }
        for session in sessions
    ]


@router.resolver(field_name="getSession")
@tracer.capture_method
def get_session(id: str):
    WorkspaceIdValidation(**{"workspaceId": id})
    user_id = genai_core.auth.get_user_id(router)
    if user_id is None:
        raise genai_core.types.CommonError("User not found")

    user_roles = genai_core.auth.get_user_roles(router)
    if user_roles is None:
        raise genai_core.types.CommonError("User does not have any roles")

    showMetadata = False
    if (
        UserRole.ADMIN.value in user_roles
        or UserRole.WORKSPACE_MANAGER.value in user_roles
    ):
        showMetadata = True

    session = genai_core.sessions.get_session(id, user_id)
    if not session:
        return None

    history = [
        {
            "type": item.get("type"),
            "content": item.get("data", {}).get("content"),
        }
        for item in session.get("History")
    ]

    # A3: citations are exposed to EVERY authenticated user for their own
    # sessions (ownership already enforced above by
    # genai_core.sessions.get_session(id, user_id) — a DynamoDB get_item
    # on the composite key (SessionId, UserId), where user_id comes from
    # the authenticated caller, never from client input). This is
    # independent of `showMetadata`, which continues to gate the full,
    # unredacted admin debug metadata exactly as before.
    #
    # reapply_citation_allowlist() re-validates already-persisted
    # citation records (defense-in-depth against legacy/pre-A3 data or
    # any future write path bypassing genai_core.citations.build_safe_citations)
    # rather than trusting whatever is in additional_kwargs verbatim.
    for item, original_item in zip(history, session.get("History")):
        additional_kwargs = original_item.get("data", {}).get("additional_kwargs") or {}
        raw_citations = (
            additional_kwargs.get("citations")
            if isinstance(additional_kwargs, dict)
            else None
        )
        safe_citations = genai_core.citations.reapply_citation_allowlist(raw_citations)
        if safe_citations:
            item["citations"] = safe_citations

    if showMetadata:
        for item, original_item in zip(history, session.get("History")):
            item["metadata"] = json.dumps(
                original_item.get("data", {}).get("additional_kwargs"),
                cls=genai_core.utils.json.CustomEncoder,
            )

    return {
        "id": session.get("SessionId"),
        "title": session.get("History", [{}])[0]
        .get("data", {})
        .get("content", "<no title>"),
        "startTime": f'{session.get("StartTime")}Z',
        "history": history,
    }


@router.resolver(field_name="deleteUserSessions")
@tracer.capture_method
def delete_user_sessions():
    user_id = genai_core.auth.get_user_id(router)
    if user_id is None:
        raise genai_core.types.CommonError("User not found")

    result = genai_core.sessions.delete_user_sessions(user_id)

    return result


@router.resolver(field_name="deleteSession")
@tracer.capture_method
def delete_session(id: str):
    WorkspaceIdValidation(**{"workspaceId": id})
    user_id = genai_core.auth.get_user_id(router)
    if user_id is None:
        raise genai_core.types.CommonError("User not found")

    result = genai_core.sessions.delete_session(id, user_id)

    return result
