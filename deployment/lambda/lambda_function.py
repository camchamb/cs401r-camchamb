"""
CodePipeline Lambda action: start a SageMaker canary deployment.

Responsibilities:
  1. Read the candidate Model Package ARN from deployment/model-package.json
     inside the CodePipeline BuildArtifact ZIP.
  2. Approve that exact package after the pipeline's Manual Approval stage.
  3. Find the model currently serving the "champion" variant on the endpoint.
  4. Create a SageMaker Model for the candidate package.
  5. Read deployment/configs/endpoint-config-canary.json from the same artifact,
     substitute the champion/canary model names, and create a new endpoint config.
  6. Call UpdateEndpoint to begin the canary deployment.

This Lambda intentionally does NOT:
  - evaluate canary quality
  - promote the canary to champion
  - roll back the endpoint

Prerequisites:
  - The endpoint already exists.
  - Its current production variant is named "champion" (or it has exactly
    one production variant, which will be treated as the champion).
  - The candidate Model Package is PendingManualApproval (or already Approved)
    and Completed.
  - The CodePipeline input artifact contains:
        deployment/model-package.json
        deployment/configs/endpoint-config-canary.json
  - The Lambda execution role can read the artifact, create/describe SageMaker
    models and endpoint configs, describe the endpoint/config/package, and
    update the endpoint. It also needs iam:PassRole for SAGEMAKER_EXECUTION_ROLE_ARN.

Environment variables:
  ENDPOINT_NAME                 e.g. northstar-churn-prod
  SAGEMAKER_EXECUTION_ROLE_ARN  execution role used by SageMaker Models
  AWS_REGION                    e.g. us-east-1 (normally provided by Lambda)
"""

import json
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
ENDPOINT_NAME = os.environ["ENDPOINT_NAME"]
SAGEMAKER_EXECUTION_ROLE_ARN = os.environ["SAGEMAKER_EXECUTION_ROLE_ARN"]
SCHEDULER_ROLE_ARN = os.environ["SCHEDULER_ROLE_ARN"]
CANARY_MAX_MINUTES = int(os.environ.get("CANARY_MAX_MINUTES", "60"))

ARTIFACT_MANIFEST_PATH = "deployment/model-package.json"
ENDPOINT_CONFIG_TEMPLATE_PATH = "deployment/configs/endpoint-config-canary.json"

sagemaker = boto3.client("sagemaker", region_name=REGION)
codepipeline = boto3.client("codepipeline", region_name=REGION)
scheduler = boto3.client("scheduler", region_name=REGION)


def _safe_name(value: str, max_length: int = 63) -> str:
    """Convert a CodePipeline execution identifier into a SageMaker-safe suffix."""
    value = re.sub(r"[^A-Za-z0-9-]", "-", value).strip("-").lower()
    value = re.sub(r"-+", "-", value)
    return (value or "execution")[-max_length:]


def _get_input_artifact(event: dict, destination: str) -> Path:
    """
    Download the first CodePipeline input artifact using the temporary artifact
    credentials included in the Lambda action event.
    """
    job_data = event["CodePipeline.job"]["data"]
    artifacts = job_data.get("inputArtifacts", [])
    if not artifacts:
        raise ValueError("CodePipeline event contains no inputArtifacts.")

    location = artifacts[0]["location"]["s3Location"]
    credentials = job_data["artifactCredentials"]

    artifact_s3 = boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        aws_session_token=credentials["sessionToken"],
    )

    artifact_path = Path(destination) / "input-artifact.zip"
    artifact_s3.download_file(
        location["bucketName"],
        location["objectKey"],
        str(artifact_path),
    )
    return artifact_path


def _read_json_from_zip(artifact_path: Path, member_name: str) -> dict:
    with zipfile.ZipFile(artifact_path, "r") as archive:
        try:
            raw = archive.read(member_name)
        except KeyError as exc:
            raise FileNotFoundError(
                f"Required file '{member_name}' was not found in BuildArtifact."
            ) from exc
    return json.loads(raw.decode("utf-8"))


def _get_candidate_package_arn(manifest: dict) -> str:
    arn = manifest.get("ModelPackageArn")
    if not arn or not arn.startswith("arn:aws:sagemaker:"):
        raise ValueError(
            f"{ARTIFACT_MANIFEST_PATH} must contain a valid "
            "'ModelPackageArn' field."
        )
    return arn


def _get_current_champion_model_name() -> str:
    endpoint = sagemaker.describe_endpoint(EndpointName=ENDPOINT_NAME)
    if endpoint["EndpointStatus"] not in ("InService", "Updating"):
        raise RuntimeError(
            f"Endpoint {ENDPOINT_NAME} is not available for an update; "
            f"status is {endpoint['EndpointStatus']}."
        )

    config_name = endpoint["EndpointConfigName"]
    config = sagemaker.describe_endpoint_config(
        EndpointConfigName=config_name
    )
    variants = config["ProductionVariants"]

    champion_variants = [
        variant for variant in variants
        if variant["VariantName"].lower() == "champion"
    ]

    if len(champion_variants) == 1:
        return champion_variants[0]["ModelName"]

    if len(variants) == 1:
        # Supports the one-time initial endpoint if its sole variant has
        # a different name. The new config will use the "champion" name.
        return variants[0]["ModelName"]

    raise RuntimeError(
        "Could not identify the current champion. The endpoint config must "
        "contain a variant named 'champion', or exactly one production variant."
    )


def _ensure_candidate_model(model_name: str, package_arn: str) -> None:
    package = sagemaker.describe_model_package(
        ModelPackageName=package_arn
    )

    if package.get("ModelPackageStatus") != "Completed":
        raise RuntimeError(
            f"Candidate package status is {package.get('ModelPackageStatus')!r}; "
            "it must be Completed."
        )

    approval_status = package.get("ModelApprovalStatus")
    if approval_status == "PendingManualApproval":
        sagemaker.update_model_package(
            ModelPackageArn=package_arn,
            ModelApprovalStatus="Approved",
            ApprovalDescription=(
                "Approved by the NorthStar CodePipeline manual approval stage."
            ),
        )
        logger.info("Approved candidate package %s.", package_arn)
    elif approval_status != "Approved":
        raise RuntimeError(
            f"Candidate package approval status is {approval_status!r}; "
            "expected PendingManualApproval or Approved."
        )

    try:
        sagemaker.describe_model(ModelName=model_name)
        logger.info("Candidate model %s already exists; reusing it.", model_name)
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in (
            "ValidationException",
            "ResourceNotFound",
        ):
            raise

    sagemaker.create_model(
        ModelName=model_name,
        ExecutionRoleArn=SAGEMAKER_EXECUTION_ROLE_ARN,
        PrimaryContainer={"ModelPackageName": package_arn},
        Tags=[
            {"Key": "Project", "Value": "NorthStar"},
            {"Key": "Purpose", "Value": "Canary"},
            {"Key": "ModelPackageArn", "Value": package_arn},
        ],
    )
    logger.info("Created candidate SageMaker model %s.", model_name)


def _render_endpoint_config(
    template: dict,
    champion_model_name: str,
    candidate_model_name: str,
    endpoint_config_name: str,
) -> dict:
    """Replace model-name tokens while preserving all other template settings."""
    rendered = json.loads(json.dumps(template))
    replacements = {
        "{{CHAMPION_MODEL_NAME}}": champion_model_name,
        "{{CANARY_MODEL_NAME}}": candidate_model_name,
    }

    def replace_tokens(value):
        if isinstance(value, dict):
            return {key: replace_tokens(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_tokens(item) for item in value]
        if isinstance(value, str):
            for token, replacement in replacements.items():
                value = value.replace(token, replacement)
        return value

    rendered = replace_tokens(rendered)

    # EndpointConfigName is supplied dynamically, not stored in the template.
    rendered["EndpointConfigName"] = endpoint_config_name

    variants = rendered.get("ProductionVariants", [])
    if len(variants) != 2:
        raise ValueError(
            "The endpoint config template must define exactly two "
            "ProductionVariants: champion and canary."
        )

    names = {variant.get("VariantName") for variant in variants}
    if names != {"champion", "canary"}:
        raise ValueError(
            "Template ProductionVariants must be named 'champion' and 'canary'."
        )

    for variant in variants:
        if not variant.get("ModelName") or "{{" in variant["ModelName"]:
            raise ValueError(
                f"Unresolved ModelName placeholder in variant "
                f"{variant.get('VariantName')}."
            )

    return rendered


def _create_endpoint_config(config: dict) -> None:
    # The template is an API-shaped SageMaker CreateEndpointConfig request.
    sagemaker.create_endpoint_config(**config)
    logger.info(
        "Created endpoint config %s.",
        config["EndpointConfigName"],
    )


def _schedule_rollback(function_arn: str, schedule_suffix: str) -> tuple[str, str]:
    """Schedule a one-time rollback so a canary cannot run past its limit."""
    rollback_at = datetime.now(timezone.utc) + timedelta(
        minutes=CANARY_MAX_MINUTES
    )
    schedule_name = f"northstar-canary-rollback-{schedule_suffix}"[:64]

    scheduler.create_schedule(
        Name=schedule_name,
        Description=(
            f"Roll back {ENDPOINT_NAME} after the {CANARY_MAX_MINUTES}-minute "
            "canary window."
        ),
        ScheduleExpression=f"at({rollback_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        Target={
            "Arn": function_arn,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps({
                "action": "rollback",
                "endpointName": ENDPOINT_NAME,
                "scheduleName": schedule_name,
            }),
            "RetryPolicy": {
                "MaximumEventAgeInSeconds": 3600,
                "MaximumRetryAttempts": 2,
            },
        },
    )
    return schedule_name, rollback_at.isoformat()


def _rollback_canary(endpoint_name: str) -> dict:
    """Route all new traffic to champion and none to canary."""
    sagemaker.update_endpoint_weights_and_capacities(
        EndpointName=endpoint_name,
        DesiredWeightsAndCapacities=[
            {"VariantName": "champion", "DesiredWeight": 1.0},
            {"VariantName": "canary", "DesiredWeight": 0.0},
        ],
    )
    logger.info("Started scheduled rollback for endpoint %s.", endpoint_name)
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Scheduled canary rollback started.",
            "endpoint": endpoint_name,
        }),
    }


def lambda_handler(event, context):
    """
    CodePipeline Lambda action handler.

    CodePipeline waits for the Lambda to call put_job_success_result or
    put_job_failure_result. The Lambda reports success once UpdateEndpoint
    accepts the update; it does not wait for the endpoint to finish updating.
    """
    if event.get("action") == "rollback":
        return _rollback_canary(event.get("endpointName", ENDPOINT_NAME))

    job_id = event["CodePipeline.job"]["id"]

    try:
        execution_id = (
            event["CodePipeline.job"]["data"]
            .get("actionConfiguration", {})
            .get("configuration", {})
            .get("UserParameters", "")
        )
        if not execution_id:
            execution_id = getattr(context, "aws_request_id", "execution")

        suffix = _safe_name(execution_id)
        candidate_model_name = f"northstar-churn-candidate-{suffix}"[:63]
        endpoint_config_name = f"northstar-churn-canary-config-{suffix}"[:63]

        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_zip = _get_input_artifact(event, temp_dir)
            manifest = _read_json_from_zip(
                artifact_zip, ARTIFACT_MANIFEST_PATH
            )
            template = _read_json_from_zip(
                artifact_zip, ENDPOINT_CONFIG_TEMPLATE_PATH
            )

        candidate_package_arn = _get_candidate_package_arn(manifest)
        champion_model_name = _get_current_champion_model_name()

        logger.info("Current champion model: %s", champion_model_name)
        logger.info("Candidate package: %s", candidate_package_arn)
        logger.info("Candidate model name: %s", candidate_model_name)

        _ensure_candidate_model(
            candidate_model_name,
            candidate_package_arn,
        )

        endpoint_config = _render_endpoint_config(
            template=template,
            champion_model_name=champion_model_name,
            candidate_model_name=candidate_model_name,
            endpoint_config_name=endpoint_config_name,
        )
        _create_endpoint_config(endpoint_config)

        function_arn = getattr(context, "invoked_function_arn", "")
        if not function_arn:
            raise RuntimeError("Lambda invocation did not include a function ARN.")
        schedule_name, rollback_at = _schedule_rollback(function_arn, suffix)

        try:
            sagemaker.update_endpoint(
                EndpointName=ENDPOINT_NAME,
                EndpointConfigName=endpoint_config_name,
            )
        except Exception:
            scheduler.delete_schedule(Name=schedule_name)
            raise

        logger.info(
            "Started canary deployment for endpoint %s using config %s; "
            "rollback schedule %s runs at %s.",
            ENDPOINT_NAME,
            endpoint_config_name,
            schedule_name,
            rollback_at,
        )

        codepipeline.put_job_success_result(
            jobId=job_id,
            executionDetails={
                "summary": (
                    f"Canary endpoint update started. Champion={champion_model_name}; "
                    f"candidate={candidate_model_name}; endpoint config={endpoint_config_name}; "
                    f"automatic rollback at {rollback_at}."
                ),
                "externalExecutionId": getattr(context, "aws_request_id", "unknown"),
            },
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Canary deployment update started.",
                "endpoint": ENDPOINT_NAME,
                "championModel": champion_model_name,
                "candidateModel": candidate_model_name,
                "endpointConfig": endpoint_config_name,
                "rollbackSchedule": schedule_name,
                "rollbackAt": rollback_at,
            }),
        }

    except Exception as exc:
        logger.exception("Canary deployment trigger failed.")
        try:
            codepipeline.put_job_failure_result(
                jobId=job_id,
                failureDetails={
                    "type": "JobFailed",
                    "message": str(exc)[:500],
                },
            )
        except Exception:
            logger.exception("Could not report failure to CodePipeline.")
        raise
