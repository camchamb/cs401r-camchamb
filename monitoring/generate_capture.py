"""Generate a distinct production comparison window for Lab 6 Task 1.

The canary deployment script sends one sample repeatedly to verify routing.
Evidently correctly deduplicates those smoke-test payloads, so this utility sends
distinct rows from the committed baseline and records the business-layer churn
alert volume needed by publish_metrics.py.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path

import boto3
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="northstar-churn-prod")
    parser.add_argument("--variant", default="champion")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--count", type=int, default=800)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--alert-threshold", type=float, default=0.5)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("monitoring/baseline/baseline.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("monitoring/reports/capture_summary.json"),
    )
    args = parser.parse_args()

    if args.count < 500:
        parser.error("--count must be at least 500 for a powered drift window")

    frame = pd.read_csv(args.baseline).drop_duplicates()
    if len(frame) < args.count:
        parser.error(
            f"baseline has only {len(frame)} distinct rows; requested {args.count}"
        )
    payloads = [
        ",".join(str(value) for value in row) for row in frame.iloc[: args.count].values
    ]
    runtime = boto3.client("sagemaker-runtime", region_name=args.region)

    def invoke(payload: str) -> float:
        response = runtime.invoke_endpoint(
            EndpointName=args.endpoint,
            TargetVariant=args.variant,
            ContentType="text/csv",
            Body=payload.encode("utf-8"),
        )
        return float(response["Body"].read().decode("utf-8").strip())

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        predictions = list(executor.map(invoke, payloads))

    churn_alert_volume = sum(
        prediction >= args.alert_threshold for prediction in predictions
    )
    summary = {
        "endpoint": args.endpoint,
        "variant": args.variant,
        "request_count": len(predictions),
        "distinct_payloads": len(set(payloads)),
        "alert_threshold": args.alert_threshold,
        "churn_alert_volume": churn_alert_volume,
        "prediction_min": min(predictions),
        "prediction_max": max(predictions),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
