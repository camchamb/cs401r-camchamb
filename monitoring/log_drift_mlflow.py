"""Log a completed Evidently drift run to the NorthStar MLflow App."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mlflow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlflow-arn", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--variant", default="champion")
    parser.add_argument("--experiment", default="northstar-drift")
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("monitoring/reports/drift_violations.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("monitoring/reports/drift_report.html"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/lab6-mlflow-runs.txt"),
    )
    args = parser.parse_args()

    with args.summary.open(encoding="utf-8") as handle:
        summary = json.load(handle)
    if not args.report.is_file():
        raise FileNotFoundError(f"missing Evidently HTML report: {args.report}")

    mlflow.set_tracking_uri(args.mlflow_arn)
    experiment = mlflow.set_experiment(args.experiment)
    mlflow.set_experiment_tags(
        {
            "lab": "6",
            "purpose": "production drift monitoring",
            "endpoint": "northstar-churn-prod",
        }
    )

    with mlflow.start_run(run_name=f"drift-{args.job_name}") as active_run:
        mlflow.log_params(
            {
                "baseline_records": summary["baseline_records"],
                "captured_records": summary["captured_records"],
                "variant": args.variant,
                "features_evaluated": summary["features_evaluated"],
                "underpowered_window": summary["underpowered_window"],
                "processing_job": args.job_name,
            }
        )
        for feature, result in summary["per_feature"].items():
            mlflow.log_metric(
                f"{result['method']}_{feature}", float(result["value"])
            )
        mlflow.log_metric("violation_count", summary["violation_count"])
        mlflow.set_tags(
            {
                "lab": "6",
                "endpoint": "northstar-churn-prod",
                "drift_detected": str(summary["drift_detected"]).lower(),
            }
        )
        mlflow.log_artifact(str(args.report))
        run_id = active_run.info.run_id

    runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id])
    wanted = [
        "run_id",
        "tags.mlflow.runName",
        "status",
        "params.processing_job",
        "params.variant",
        "params.baseline_records",
        "params.captured_records",
        "params.underpowered_window",
        "metrics.violation_count",
        *[
            f"metrics.{result['method']}_{feature}"
            for feature, result in summary["per_feature"].items()
        ],
    ]
    present = [column for column in wanted if column in runs.columns]
    rendered = runs[present].to_string(index=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        f"Experiment: {args.experiment}\n"
        f"Experiment ID: {experiment.experiment_id}\n"
        f"Logged run ID: {run_id}\n\n"
        f"mlflow.search_runs output:\n{rendered}\n",
        encoding="utf-8",
    )
    print(f"logged MLflow run {run_id} to experiment {args.experiment}")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
