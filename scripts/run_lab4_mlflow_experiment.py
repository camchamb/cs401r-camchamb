"""Run and export the fresh Lab 4 champion-challenger MLflow experiment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import mlflow
import pandas as pd
from sklearn.model_selection import train_test_split


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models.churn import train as churn  # noqa: E402


CONFIGS = (
    {"run_name": "lab4-depth4-eta005", "max_depth": 4, "eta": 0.05},
    {"run_name": "lab4-depth6-eta010", "max_depth": 6, "eta": 0.10},
    {"run_name": "lab4-depth8-eta015", "max_depth": 8, "eta": 0.15},
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlflow-arn", required=True)
    parser.add_argument(
        "--experiment", default="northstar-churn-lab4-xops-20261031"
    )
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data" / "features"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "docs" / "lab4-evidence"
    )
    return parser.parse_args()


def model_args(config: dict) -> SimpleNamespace:
    return SimpleNamespace(
        max_depth=config["max_depth"],
        eta=config["eta"],
        num_round=200,
        min_child_weight=5,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=None,
        objective="binary:logistic",
        eval_metrics=["logloss", "auc"],
        seed=42,
        verbosity=0,
        early_stopping_rounds=20,
        verbose_eval=False,
        baseline_max_depth=4,
        baseline_eta=0.1,
        baseline_num_round=200,
        baseline_subsample=0.9,
        baseline_colsample_bytree=0.9,
        baseline_eval_metric="auc",
    )


def load_snapshot(data_dir: Path) -> pd.DataFrame:
    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet feature files found in {data_dir}")
    return pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)


def main() -> None:
    args = parse_args()
    df = load_snapshot(args.data_dir).dropna(subset=[churn.LABEL_COLUMN]).reset_index(drop=True)
    X, y = churn.preprocess_features(df)
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.30, random_state=42, stratify=y
    )

    baseline_args = model_args(CONFIGS[0])
    baseline_proba = churn.train_recency_baseline(
        X_train, y_train, X_val, baseline_args
    )

    mlflow.set_tracking_uri(args.mlflow_arn)
    experiment = mlflow.set_experiment(args.experiment)
    mlflow.set_experiment_tags(
        {
            "lab": "4",
            "purpose": "champion-challenger hyperparameter comparison",
            "data_snapshot": "data/features parquet snapshot",
        }
    )

    for config in CONFIGS:
        run_args = model_args(config)
        model = churn.train_model(X_train, y_train, X_val, y_val, run_args)
        metrics = churn.evaluate_model(model, X_val, y_val, baseline_proba)
        params = {
            "max_depth": run_args.max_depth,
            "eta": run_args.eta,
            "num_round": run_args.num_round,
            "min_child_weight": run_args.min_child_weight,
            "subsample": run_args.subsample,
            "colsample_bytree": run_args.colsample_bytree,
            "scale_pos_weight": run_args.resolved_scale_pos_weight,
            "xgboost_version": churn.xgb.__version__,
            "split_seed": run_args.seed,
        }
        numeric_metrics = {
            key: float(value)
            for key, value in metrics.items()
            if isinstance(value, (int, float))
        }
        numeric_metrics["best_iteration"] = float(model.best_iteration)

        with mlflow.start_run(run_name=config["run_name"]):
            mlflow.log_params(params)
            mlflow.log_metrics(numeric_metrics)
            mlflow.set_tags(
                {
                    "lab": "4",
                    "data_snapshot": "data/features parquet snapshot",
                    "comparison_group": "lab4-xops-assessment",
                }
            )

    runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id])
    runs = runs.sort_values("metrics.auc_roc", ascending=False)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runs.to_csv(args.output_dir / "mlflow-search-runs.csv", index=False)

    comparison_columns = [
        "run_id",
        "tags.mlflow.runName",
        "params.max_depth",
        "params.eta",
        "params.num_round",
        "metrics.auc_roc",
        "metrics.precision_top10",
        "metrics.recall_top10",
        "metrics.auc_vs_baseline",
        "metrics.lift_ci_low",
        "metrics.lift_ci_high",
    ]
    comparison = runs[comparison_columns]
    rendered = comparison.to_string(index=False)
    (args.output_dir / "mlflow-search-runs.txt").write_text(
        f"Experiment: {args.experiment}\n"
        f"Experiment ID: {experiment.experiment_id}\n\n"
        f"mlflow.search_runs output:\n{rendered}\n",
        encoding="utf-8",
    )
    print(rendered)


if __name__ == "__main__":
    main()
