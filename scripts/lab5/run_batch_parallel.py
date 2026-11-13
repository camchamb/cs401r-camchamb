"""Run the Lab 5 Batch Transform parallel comparison (v9 incumbent, v10 candidate)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
GROUP = "northstar-churn-models"
INCUMBENT_VERSION = 9
CANDIDATE_VERSION = 10
INSTANCE_TYPE = "ml.m5.large"
FEATURE_PREFIX = "features/customers/"
RUN_PREFIX = "artifacts/lab5/batch-parallel"
FEATURE_COLUMNS = [
    "days_since_last_purchase", "customer_tenure_days",
    "purchase_frequency_30d", "purchase_frequency_90d",
    "purchase_frequency_180d", "avg_order_value", "total_spend_90d",
    "total_lifetime_value", "avg_basket_size_6m",
    "category_diversity_score", "online_to_store_ratio",
]


def package_arn(account_id: str, version: int) -> str:
    return f"arn:aws:sagemaker:{REGION}:{account_id}:model-package/{GROUP}/{version}"


def prepare_input(s3, bucket: str, work: Path, run_id: str) -> str:
    response = s3.list_objects_v2(Bucket=bucket, Prefix=FEATURE_PREFIX)
    keys = sorted(
        obj["Key"] for obj in response.get("Contents", [])
        if obj["Key"].endswith(".parquet") and obj["Size"] > 0
    )
    if not keys:
        raise RuntimeError(f"No feature Parquet files under s3://{bucket}/{FEATURE_PREFIX}")
    frames = []
    for index, key in enumerate(keys):
        local = work / f"features-{index}.parquet"
        s3.download_file(bucket, key, str(local))
        frames.append(pd.read_parquet(local))
    source = pd.concat(frames, ignore_index=True)
    missing = sorted(set(FEATURE_COLUMNS) - set(source.columns))
    if missing:
        raise RuntimeError(f"Missing model features: {missing}")

    input_prefix = f"{RUN_PREFIX}/{run_id}/input"
    csv_path = work / "customer-features.csv"
    source[FEATURE_COLUMNS].fillna(0).to_csv(csv_path, header=False, index=False)
    s3.upload_file(str(csv_path), bucket, f"{input_prefix}/customer-features.csv")

    # Both transform jobs consume this exact manifest URI.
    manifest = work / "input.manifest"
    manifest.write_text(
        json.dumps({"prefix": f"s3://{bucket}/{input_prefix}/"}) + "\n"
        + json.dumps("customer-features.csv") + "\n",
        encoding="utf-8",
    )
    manifest_key = f"{RUN_PREFIX}/{run_id}/input.manifest"
    s3.upload_file(str(manifest), bucket, manifest_key)
    return f"s3://{bucket}/{manifest_key}"


def create_model(sm, name: str, arn: str, role_arn: str) -> None:
    existing = {model["ModelName"] for model in sm.list_models(NameContains=name)["Models"]}
    if name in existing:
        sm.delete_model(ModelName=name)
    sm.create_model(
        ModelName=name,
        ExecutionRoleArn=role_arn,
        Containers=[{"ModelPackageName": arn}],
        Tags=[
            {"Key": "Project", "Value": "northstar"},
            {"Key": "Lab", "Value": "5"},
            {"Key": "Deployment", "Value": "batch-parallel"},
        ],
    )


def run_job(sm, name: str, model: str, manifest: str, output: str) -> dict:
    sm.create_transform_job(
        TransformJobName=name,
        ModelName=model,
        MaxConcurrentTransforms=1,
        MaxPayloadInMB=6,
        BatchStrategy="MultiRecord",
        TransformInput={
            "DataSource": {"S3DataSource": {"S3DataType": "ManifestFile", "S3Uri": manifest}},
            "ContentType": "text/csv",
            "SplitType": "Line",
        },
        TransformOutput={"S3OutputPath": output, "Accept": "text/csv", "AssembleWith": "Line"},
        TransformResources={"InstanceType": INSTANCE_TYPE, "InstanceCount": 1},
        Tags=[
            {"Key": "Project", "Value": "northstar"},
            {"Key": "Lab", "Value": "5"},
            {"Key": "Deployment", "Value": "batch-parallel"},
        ],
    )
    sm.get_waiter("transform_job_completed_or_stopped").wait(
        TransformJobName=name, WaiterConfig={"Delay": 30, "MaxAttempts": 60}
    )
    result = sm.describe_transform_job(TransformJobName=name)
    if result["TransformJobStatus"] != "Completed":
        raise RuntimeError(f"{name}: {result['TransformJobStatus']} - {result.get('FailureReason')}")
    return result


def safe_json(value: object) -> str:
    return json.dumps(value, indent=2, default=str)


def find_output(s3, bucket: str, prefix: str) -> str:
    objects = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    keys = [obj["Key"] for obj in objects if obj["Size"] > 0]
    if len(keys) != 1:
        raise RuntimeError(f"Expected one output at s3://{bucket}/{prefix}; found {keys}")
    return keys[0]


def read_scores(s3, bucket: str, key: str, local: Path) -> np.ndarray:
    s3.download_file(bucket, key, str(local))
    return np.asarray(np.loadtxt(local, delimiter=",", ndmin=1), dtype=float).reshape(-1)


def describe(scores: np.ndarray) -> dict[str, int | float]:
    return {
        "count": int(scores.size), "minimum": float(scores.min()),
        "p10": float(np.quantile(scores, 0.10)), "p25": float(np.quantile(scores, 0.25)),
        "median": float(np.median(scores)), "p75": float(np.quantile(scores, 0.75)),
        "p90": float(np.quantile(scores, 0.90)), "maximum": float(scores.max()),
        "mean": float(scores.mean()), "stddev": float(scores.std()),
    }


def write_comparison(path: Path, run_id: str, manifest: str, jobs: dict[str, str],
                     incumbent: np.ndarray, candidate: np.ndarray, limit: float) -> float:
    if incumbent.shape != candidate.shape:
        raise RuntimeError(f"Output counts differ: {incumbent.size} vs {candidate.size}")
    class_threshold = 0.5
    disagreement = float(np.mean((incumbent >= class_threshold) != (candidate >= class_threshold)))
    incumbent_stats, candidate_stats = describe(incumbent), describe(candidate)
    rows = []
    for metric in incumbent_stats:
        if metric == "count":
            rows.append(f"| {metric} | {incumbent_stats[metric]} | {candidate_stats[metric]} |")
        else:
            rows.append(f"| {metric} | {incumbent_stats[metric]:.6f} | {candidate_stats[metric]:.6f} |")
    passed = disagreement <= limit
    decision = "PASS - candidate is eligible for promotion" if passed else "ROLLBACK - candidate remains shadow-only"
    path.write_text(
        "# Lab 5 Batch Parallel Comparison\n\n"
        f"- Run: `{run_id}`\n- Shared input manifest: `{manifest}`\n"
        f"- Incumbent: approved package v9, job `{jobs['incumbent']}`\n"
        f"- Candidate: approved package v10, job `{jobs['candidate']}`\n"
        f"- Classification threshold: `{class_threshold}`\n"
        f"- Observed disagreement rate: **{disagreement:.4%}**\n"
        f"- Maximum allowed disagreement: **{limit:.2%}**\n"
        f"- Decision: **{decision}**\n\n"
        "Candidate output is a shadow artifact and is not connected to a downstream consumer. "
        "On gate failure, incumbent output remains authoritative.\n\n"
        "## Score distributions\n\n| Statistic | Incumbent v9 | Candidate v10 |\n"
        "|---|---:|---:|\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return disagreement


def registry_table() -> str:
    return subprocess.run(
        ["aws", "sagemaker", "list-model-packages", "--model-package-group-name", GROUP,
         "--region", REGION,
         "--query", "ModelPackageSummaryList[*].[ModelPackageVersion,ModelApprovalStatus,CreationTime]",
         "--output", "table"],
        check=True, capture_output=True, text=True,
    ).stdout


def main() -> int:
    session = boto3.Session(region_name=REGION)
    sts, sm, s3 = session.client("sts"), session.client("sagemaker"), session.client("s3")
    iam, quotas = session.client("iam"), session.client("service-quotas")
    account = sts.get_caller_identity()["Account"]
    bucket = f"northstar-dev-data-{account}"
    role = iam.get_role(RoleName="northstar-dev-MLEngineer")["Role"]["Arn"]
    quota = quotas.get_service_quota(ServiceCode="sagemaker", QuotaCode="L-236AE59F")["Quota"]
    if quota["Value"] < 1:
        raise RuntimeError(f"Batch Transform quota is {quota['Value']}; at least 1 is required")
    history = quotas.list_requested_service_quota_change_history_by_quota(
        ServiceCode="sagemaker", QuotaCode="L-236AE59F"
    )["RequestedQuotas"]

    arns = {}
    for version in (INCUMBENT_VERSION, CANDIDATE_VERSION):
        arn = package_arn(account, version)
        package = sm.describe_model_package(ModelPackageName=arn)
        if package["ModelApprovalStatus"] != "Approved":
            raise RuntimeError(f"Package v{version} is not Approved")
        arns[version] = arn

    rollback = json.loads((ROOT / "deployment/configs/batch-rollback.json").read_text())
    limit = float(rollback["maximum_allowed"])
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    evidence = ROOT / "docs/lab5-deployment-output.txt"
    comparison = ROOT / "docs/lab5-batch-comparison.md"
    evidence.write_text(
        "===== 0. identity, approved transform quota, and registry =====\n"
        f"Account: {account}  Region: {REGION}\nCaptured: {datetime.now(timezone.utc).isoformat()}\n"
        f"Applied quota: {quota['Value']}\nQuota request history:\n{safe_json(history)}\n\n"
        f"Model registry:\n{registry_table()}\n",
        encoding="utf-8",
    )

    with tempfile.TemporaryDirectory(prefix="northstar-lab5-") as temp:
        work = Path(temp)
        manifest = prepare_input(s3, bucket, work, run_id)
        model_names = {"incumbent": "northstar-churn-v9-batch", "candidate": "northstar-churn-v10-batch"}
        create_model(sm, model_names["incumbent"], arns[9], role)
        create_model(sm, model_names["candidate"], arns[10], role)
        jobs = {
            "incumbent": f"northstar-churn-incumbent-v9-{run_id}",
            "candidate": f"northstar-churn-candidate-v10-{run_id}",
        }
        output_prefix = f"{RUN_PREFIX}/{run_id}/output"
        incumbent_job = run_job(sm, jobs["incumbent"], model_names["incumbent"], manifest,
                                f"s3://{bucket}/{output_prefix}/incumbent")
        candidate_job = run_job(sm, jobs["candidate"], model_names["candidate"], manifest,
                                f"s3://{bucket}/{output_prefix}/candidate")
        incumbent_key = find_output(s3, bucket, f"{output_prefix}/incumbent/")
        candidate_key = find_output(s3, bucket, f"{output_prefix}/candidate/")
        incumbent_scores = read_scores(s3, bucket, incumbent_key, work / "incumbent.out")
        candidate_scores = read_scores(s3, bucket, candidate_key, work / "candidate.out")
        disagreement = write_comparison(
            comparison, run_id, manifest, jobs, incumbent_scores, candidate_scores, limit
        )
        with evidence.open("a", encoding="utf-8") as stream:
            stream.write("===== 1. incumbent Batch Transform job =====\n" + safe_json(incumbent_job) + "\n\n")
            stream.write("===== 2. candidate shadow Batch Transform job =====\n" + safe_json(candidate_job) + "\n\n")
            stream.write("===== 3. rollback gate definition =====\n" + safe_json(rollback) + "\n\n")
            stream.write("===== 4. observed comparison =====\n" + comparison.read_text() + "\n")

    print(f"Incumbent job: {jobs['incumbent']}")
    print(f"Candidate job: {jobs['candidate']}")
    print(f"Disagreement: {disagreement:.4%}; allowed: {limit:.2%}")
    print(f"Evidence: {evidence}")
    print(f"Comparison: {comparison}")
    return 0 if disagreement <= limit else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
