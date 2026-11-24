"""Publish the Lab 6 model and business metrics to CloudWatch.

The Evidently processing container deliberately does not publish metrics. Its
dependency installation leaves botocore with an unsupported urllib3 version,
so this program runs locally after the processing job and reads the downloaded
drift_violations.json artifact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3


def load_summary(path: Path) -> dict:
    """Load and minimally validate the drift summary produced by Evidently."""
    with path.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    required = {
        "drift_detected",
        "violation_count",
        "captured_records",
        "underpowered_window",
        "per_feature",
    }
    missing = sorted(required.difference(summary))
    if missing:
        raise ValueError(f"{path} is missing required fields: {missing}")
    return summary


def metric_data(summary: dict, endpoint: str, churn_alert_volume: int) -> list[dict]:
    """Build model-layer and business-layer CloudWatch metric data."""
    dimensions = [{"Name": "EndpointName", "Value": endpoint}]
    metrics = [
        {
            "MetricName": "DriftDetected",
            "Dimensions": dimensions,
            "Value": 1.0 if summary["drift_detected"] else 0.0,
            "Unit": "Count",
        },
        {
            "MetricName": "DriftViolationCount",
            "Dimensions": dimensions,
            "Value": float(summary["violation_count"]),
            "Unit": "Count",
        },
        {
            "MetricName": "CapturedRecords",
            "Dimensions": dimensions,
            "Value": float(summary["captured_records"]),
            "Unit": "Count",
        },
        {
            "MetricName": "UnderpoweredWindow",
            "Dimensions": dimensions,
            "Value": 1.0 if summary["underpowered_window"] else 0.0,
            "Unit": "Count",
        },
        {
            "MetricName": "ChurnAlertVolume",
            "Dimensions": dimensions,
            "Value": float(churn_alert_volume),
            "Unit": "Count",
        },
    ]

    for feature, result in summary["per_feature"].items():
        metrics.append(
            {
                "MetricName": "FeatureDriftValue",
                "Dimensions": dimensions
                + [
                    {"Name": "FeatureName", "Value": feature},
                    {"Name": "Method", "Value": result["method"].upper()},
                ],
                "Value": float(result["value"]),
                "Unit": "None",
            }
        )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--violations",
        default="monitoring/reports/drift_violations.json",
        type=Path,
        help="Downloaded Evidently drift summary",
    )
    parser.add_argument("--endpoint", default="northstar-churn-prod")
    parser.add_argument("--namespace", default="NorthStar/ChurnModel")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--churn-alert-volume",
        required=True,
        type=int,
        help="Number of churn alerts emitted in the measured daily window",
    )
    args = parser.parse_args()

    if args.churn_alert_volume < 0:
        parser.error("--churn-alert-volume must be non-negative")

    summary = load_summary(args.violations)
    metrics = metric_data(summary, args.endpoint, args.churn_alert_volume)
    cloudwatch = boto3.client("cloudwatch", region_name=args.region)

    # PutMetricData accepts at most 1000 MetricDatum objects per request. This
    # publisher currently emits fewer than 20, but chunking keeps the contract
    # correct if the monitored feature set grows later.
    for start in range(0, len(metrics), 1000):
        cloudwatch.put_metric_data(
            Namespace=args.namespace,
            MetricData=metrics[start : start + 1000],
        )

    print(
        f"published {len(metrics)} metrics to {args.namespace} "
        f"for endpoint {args.endpoint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
