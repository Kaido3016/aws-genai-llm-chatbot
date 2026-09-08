import os
import json
import boto3
import urllib.parse
import genai_core.documents
import genai_core.workspaces
from genai_core.security import file_validation
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.data_classes import SQSEvent, event_source
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()

sfn_client = boto3.client("stepfunctions")
s3 = boto3.client("s3")

FILE_IMPORT_WORKFLOW_ARN = os.environ.get("FILE_IMPORT_WORKFLOW_ARN")
PROCESSING_BUCKET_NAME = os.environ.get("PROCESSING_BUCKET_NAME")
DEFAULT_KENDRA_S3_DATA_SOURCE_BUCKET_NAME = os.environ.get(
    "DEFAULT_KENDRA_S3_DATA_SOURCE_BUCKET_NAME"
)


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
@event_source(data_class=SQSEvent)
def lambda_handler(event: SQSEvent, context: LambdaContext):
    for sqs_record in event.records:
        records = get_records_from_sqs_record(sqs_record)

        for record in records:
            process_record(record)


def process_record(record):
    bucket_name = record["s3"]["bucket"]["name"]
    object_key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    object_size = record["s3"]["object"]["size"]

    logger.debug(f"bucket_name: {bucket_name}")
    logger.debug(f"object_key: {object_key}")
    logger.debug(f"object_size: {object_size}")

    key_split = object_key.split("/")
    workspace_id = key_split[0]
    file_name = object_key.replace(f"{workspace_id}/", "")

    logger.debug(f"workspace_id: {workspace_id}")
    logger.debug(f"file_name: {file_name}")

    workspace = genai_core.workspaces.get_workspace(workspace_id=workspace_id)
    if not workspace:
        raise genai_core.types.CommonError("Workspace not found")

    # A5: server-side re-validation of the uploaded object, independent
    # of the extension allow-list already checked at presigned-URL
    # request time (documents.py's getUploadFileURL) — that earlier
    # check only constrains what URL was *requested*, not what bytes
    # were actually uploaded to it. See genai_core/security/
    # file_validation.py's module docstring for the full trace of why
    # this check is needed and exactly what it does/doesn't verify.
    #
    # The object is downloaded once here (bounded by the 10MB upload
    # limit already enforced in genai_core/presign.py) and validated
    # before any downstream processing — Kendra copy or the Step
    # Functions extraction workflow — is triggered.
    validation_result = _validate_uploaded_object(bucket_name, object_key, file_name)

    result = genai_core.documents.create_document(
        workspace_id=workspace_id,
        document_type="file",
        path=file_name,
        title=file_name,
        size_in_bytes=object_size,
    )
    document_id = result["document_id"]

    if not validation_result.valid:
        # Log the specific reason for operators, but do not leak it back
        # through any user-facing field beyond the existing generic
        # "error" status — the reason itself contains no internal paths
        # or secrets (see file_validation.py: reasons are static,
        # descriptive strings, never raw content).
        logger.warning(
            "Rejected uploaded file failing content validation",
            workspace_id=workspace_id,
            document_id=document_id,
            reason=validation_result.reason,
        )
        genai_core.documents.set_status(
            workspace_id=workspace_id, document_id=document_id, status="error"
        )
        return

    if workspace["engine"] == "kendra":
        kendra_object_key = f"documents/{object_key}"
        kendra_metadata_key = f"metadata/documents/{object_key}.metadata.json"

        metadata = {
            "DocumentId": document_id,
            "Attributes": {
                "workspace_id": workspace_id,
                "document_type": "file",
            },
        }

        title = workspace.get("title")
        if title:
            metadata["Title"] = title

        s3.copy_object(
            CopySource={"Bucket": bucket_name, "Key": object_key},
            Bucket=DEFAULT_KENDRA_S3_DATA_SOURCE_BUCKET_NAME,
            Key=kendra_object_key,
        )

        s3.put_object(
            Body=json.dumps(metadata),
            Bucket=DEFAULT_KENDRA_S3_DATA_SOURCE_BUCKET_NAME,
            Key=kendra_metadata_key,
            ContentType="application/json",
        )

        genai_core.documents.set_status(
            workspace_id=workspace_id, document_id=document_id, status="processed"
        )
    else:
        processing_object_key = f"{workspace_id}/{document_id}/content.txt"
        response = sfn_client.start_execution(
            stateMachineArn=FILE_IMPORT_WORKFLOW_ARN,
            input=json.dumps(
                {
                    "workspace_id": workspace_id,
                    "document_id": document_id,
                    "input_bucket_name": bucket_name,
                    "input_object_key": object_key,
                    "processing_bucket_name": PROCESSING_BUCKET_NAME,
                    "processing_object_key": processing_object_key,
                }
            ),
        )

        logger.info(response)


def _validate_uploaded_object(bucket_name: str, object_key: str, file_name: str):
    """A5: download the object once and run it through
    genai_core.security.file_validation.validate_upload(). Isolated into
    its own function so the S3 GET + validation logic can be reasoned
    about (and unit-tested via mocking) independently of the rest of
    process_record()'s control flow.
    """
    obj = s3.get_object(Bucket=bucket_name, Key=object_key)
    content = obj["Body"].read()
    return file_validation.validate_upload(file_name, content)


def get_records_from_sqs_record(record):
    logger.debug(f"Getting records from SQS record: {record}")
    body = json.loads(record.body)
    logger.debug(f"body: {body}")

    records = body.get("Records", [])
    logger.debug(f"input records: {records}")

    ret_value = []

    for record in records:
        event_name = record["eventName"]
        if not event_name.startswith("ObjectCreated"):
            logger.info(f"Skipping event {event_name} for {record}")
            continue

        ret_value.append(record)

    logger.debug(f"output records: {ret_value}")

    return ret_value
