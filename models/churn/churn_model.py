"""Your Lab 3 churn model, packaged for the Lab 4 pipeline.

YOU WRITE THIS FILE. The pipeline's training job runs train_sagemaker.py
(beside this file; do not edit it), which imports this module and calls
train(). Everything the job needs from you goes through the three names below.

Contract (train_sagemaker.py checks it in the first seconds of the job):

    FEATURE_COLUMNS   list[str]   model input columns, in order
    LABEL             str         "churn_label"
    train(df, seed=42) -> (xgboost.Booster, dict)

`df` is the full labeled feature table: every column of your Lab 2 output,
including `loyalty_tier` for slice evaluation. It is already loaded. Do not
query Athena or the Feature Store from here; the training container stages
the data for you and has no business making service calls for its input.

The metrics dict must include the four numbers the CI gate in
tests/test_model.py reads. Your Lab 3 evaluate_model() already returns them:

    precision_top10, recall_top10      gated at 0.50 / 0.25
    lift_ci_low, lift_ci_high          bootstrap 95% CI on AUC lift over the
                                       recency-only baseline; lift_ci_low > 0

Include anything else you reported in Lab 3 (auc_roc, baseline_auc,
positive_rate_val, slice results). It is all written to evaluation_metrics.json
and attached to the registered model.

What runs in the container: the SageMaker XGBoost 1.7-1 image ships pandas,
numpy, scikit-learn, xgboost and boto3. It does NOT ship mlflow or sagemaker,
so do not import them at the top of this file. MLflow tracking stays in your
Lab 3 script.

Only this directory (models/churn/) is uploaded to the training job, so the
code must live here. `import churn_training_skeleton` will fail inside the
container even though it works on your laptop. Copy the functions you need.

Test it locally before pushing:

    python models/churn/train_sagemaker.py \
        --train-channel <dir containing your features CSV or Parquet> \
        --model-dir /tmp/model --output-data-dir /tmp/out
"""

# Keeps Lab 3 annotations like `-> tuple[np.ndarray, np.ndarray]` from being
# evaluated, whatever Python the training container runs.
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

# Must match the columns your Lab 3 model was trained on, in the same order.
FEATURE_COLUMNS = [
    "days_since_last_purchase",
    "customer_tenure_days",
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    "category_diversity_score",
    "online_to_store_ratio",
]

LABEL = "churn_label"
SLICE_COLUMN = "loyalty_tier"
BASELINE_FEATURE = "days_since_last_purchase"

EVAL_THRESHOLDS = {
    "precision_top10": 0.50,
    "recall_top10": 0.25,
}
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42


def preprocess_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Apply Lab 3's missing-value handling and convert features and labels."""
    df = df.dropna(subset=[LABEL]).copy()
    if df.empty:
        raise ValueError("No samples remain after dropping rows with missing labels.")

    X = df[FEATURE_COLUMNS].fillna(-1).to_numpy(dtype=np.float32)
    y = df[LABEL].to_numpy(dtype=int)
    if not np.isfinite(X).all():
        raise ValueError("Feature matrix contains infinite or non-finite values.")
    if len(np.unique(y)) < 2:
        raise ValueError("Training data must contain at least two classes.")
    return X, y


def train_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    seed: int,
) -> xgb.Booster:
    """Train the Lab 3 XGBoost model with its validation-based early stopping."""
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_COLUMNS)
    scale_pos_weight = float(
        (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    )
    params = {
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "max_depth": 6,
        "eta": 0.1,
        "min_child_weight": 5,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "scale_pos_weight": scale_pos_weight,
        # More days since purchase must not reduce the predicted churn risk.
        "monotone_constraints": tuple(
            1 if feature == BASELINE_FEATURE else 0
            for feature in FEATURE_COLUMNS
        ),
        "seed": seed,
        "verbosity": 1,
    }
    return xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=200,
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=20,
        verbose_eval=25,
    )


def train_recency_baseline(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    seed: int,
) -> np.ndarray:
    """Train and score the same recency-only baseline used in Lab 3."""
    feature_index = FEATURE_COLUMNS.index(BASELINE_FEATURE)
    dtrain = xgb.DMatrix(
        X_train[:, [feature_index]],
        label=y_train,
        feature_names=[BASELINE_FEATURE],
    )
    dval = xgb.DMatrix(
        X_val[:, [feature_index]],
        feature_names=[BASELINE_FEATURE],
    )
    scale_pos_weight = float(
        (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    )
    params = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "max_depth": 4,
        "eta": 0.1,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "scale_pos_weight": scale_pos_weight,
        "seed": seed,
    }
    baseline = xgb.train(params, dtrain, num_boost_round=200)
    return baseline.predict(dval)


def bootstrap_lift_ci(
    y_true: np.ndarray,
    proba: np.ndarray,
    baseline_proba: np.ndarray,
    n: int = BOOTSTRAP_N,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Compute a paired percentile CI for model AUC lift over the baseline."""
    rng = np.random.default_rng(seed)
    indices = np.arange(len(y_true))
    differences = []
    for _ in range(n):
        sample = rng.choice(indices, size=len(indices), replace=True)
        sampled_labels = y_true[sample]
        if len(np.unique(sampled_labels)) < 2:
            continue
        differences.append(
            roc_auc_score(sampled_labels, proba[sample])
            - roc_auc_score(sampled_labels, baseline_proba[sample])
        )
    if len(differences) < n // 2:
        raise ValueError(
            "Too few usable bootstrap replicates to form a confidence interval. "
            "Your validation set is too small or too imbalanced to support this gate."
        )
    differences.sort()
    return (
        float(np.quantile(differences, alpha / 2)),
        float(np.quantile(differences, 1 - alpha / 2)),
    )


def evaluate_model(
    model: xgb.Booster,
    X_val: np.ndarray,
    y_val: np.ndarray,
    baseline_proba: np.ndarray,
) -> dict:
    """Return Lab 3 metrics and raise when any promotion gate fails."""
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    probabilities = model.predict(dval)
    auc_roc = roc_auc_score(y_val, probabilities)

    top10_threshold = np.percentile(probabilities, 90)
    top10_mask = probabilities >= top10_threshold
    precision_top10 = (
        y_val[top10_mask].mean() if top10_mask.sum() > 0 else 0.0
    )
    recall_top10 = (
        y_val[top10_mask].sum() / y_val.sum() if y_val.sum() > 0 else 0.0
    )
    baseline_auc = roc_auc_score(y_val, baseline_proba)
    lift_ci_low, lift_ci_high = bootstrap_lift_ci(
        y_val, probabilities, baseline_proba
    )
    metrics = {
        "auc_roc": round(float(auc_roc), 4),
        "precision_top10": round(float(precision_top10), 4),
        "recall_top10": round(float(recall_top10), 4),
        "auc_vs_baseline": round(float(auc_roc - baseline_auc), 4),
        "baseline_auc": round(float(baseline_auc), 4),
        "lift_ci_low": round(float(lift_ci_low), 4),
        "lift_ci_high": round(float(lift_ci_high), 4),
        "positive_rate_val": round(float(y_val.mean()), 4),
        "n_val_samples": int(len(y_val)),
        "eval_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    failures = [
        f"{metric}={metrics[metric]:.4f} < {threshold}"
        for metric, threshold in EVAL_THRESHOLDS.items()
        if metrics[metric] < threshold
    ]
    if lift_ci_low <= 0:
        failures.append(
            f"auc_lift 95% CI [{lift_ci_low:.4f}, {lift_ci_high:.4f}] includes "
            "zero - no evidence the model beats the recency-only baseline"
        )
    if failures:
        raise ValueError(
            f"Model failed evaluation thresholds: {'; '.join(failures)}. "
            "Do not promote to Model Registry."
        )
    return metrics


def evaluate_slices(
    model: xgb.Booster,
    X_val: np.ndarray,
    y_val: np.ndarray,
    slice_column: pd.Series,
) -> dict:
    """Return Lab 3's validation metrics grouped by loyalty tier."""
    if len(X_val) != len(y_val) or len(y_val) != len(slice_column):
        raise ValueError("X_val, y_val, and slice_column must have the same length.")

    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    probabilities = model.predict(dval)
    aggregate_count = max(1, int(np.ceil(len(y_val) * 0.10)))
    aggregate_top = np.argsort(probabilities, kind="mergesort")[-aggregate_count:]
    aggregate_recall = (
        float(y_val[aggregate_top].sum() / y_val.sum()) if y_val.sum() else 0.0
    )

    slice_values = pd.Series(slice_column).reset_index(drop=True)
    results = {}
    for slice_value in slice_values.drop_duplicates():
        mask = (slice_values == slice_value).to_numpy()
        labels = y_val[mask]
        slice_probabilities = probabilities[mask]
        selected_count = max(1, int(np.ceil(len(labels) * 0.10)))
        selected = np.argsort(slice_probabilities, kind="mergesort")[-selected_count:]
        recall_top10 = (
            float(labels[selected].sum() / labels.sum()) if labels.sum() else 0.0
        )
        slice_auc = (
            float(roc_auc_score(labels, slice_probabilities))
            if len(np.unique(labels)) == 2
            else None
        )
        recall_gap = aggregate_recall - recall_top10
        positives = int(labels.sum())
        results[str(slice_value)] = {
            "n_samples": int(mask.sum()),
            "positives": positives,
            "auc_roc": round(slice_auc, 4) if slice_auc is not None else None,
            "recall_top10": round(recall_top10, 4),
            "recall_gap": round(recall_gap, 4),
            "flagged": recall_gap > 0.10,
            "too_small_to_conclude": positives < 20,
        }
    return results


def train(df: pd.DataFrame, seed: int = 42) -> tuple[xgb.Booster, dict]:
    """Train the churn model on `df` and return (booster, metrics).

    TODO (Lab 4): implement this from your Lab 3 code. The usual shape:

      1. X, y from FEATURE_COLUMNS and LABEL (handle missing values the way
         your Lab 3 preprocess_features() did)
      2. train_test_split(..., test_size=0.30, random_state=seed, stratify=y)
         -- carry the tier through the same call if you report slices. Pass
         df["loyalty_tier"] (a Series) or .to_numpy(dtype=object); NOT
         .values, which under pandas 3 is an Arrow array that
         train_test_split cannot index
      3. train the XGBoost model (your Lab 3 train_model())
      4. train the recency-only baseline (train_recency_baseline())
      5. metrics = evaluate_model(...)   # keep Lab 3's raise on a failed gate:
                                         # it fails the training step, so a
                                         # model that misses a gate is never
                                         # registered
      6. return model, metrics

    Return an xgboost.Booster (what xgb.train() gives you). If you used
    XGBClassifier, return clf.get_booster().
    """
    missing = [
        column
        for column in FEATURE_COLUMNS + [LABEL, SLICE_COLUMN]
        if column not in df.columns
    ]
    if missing:
        raise ValueError(f"Training data missing required columns: {missing}")

    df = df.dropna(subset=[LABEL]).reset_index(drop=True)
    X, y = preprocess_features(df)
    (
        X_train,
        X_val,
        y_train,
        y_val,
        _,
        tier_val,
    ) = train_test_split(
        X,
        y,
        df[SLICE_COLUMN],
        test_size=0.30,
        random_state=seed,
        stratify=y,
    )

    model = train_model(X_train, y_train, X_val, y_val, seed)
    baseline_proba = train_recency_baseline(X_train, y_train, X_val, seed)
    metrics = evaluate_model(model, X_val, y_val, baseline_proba)
    metrics["loyalty_tier_slices"] = evaluate_slices(
        model, X_val, y_val, tier_val
    )
    metrics["flagged_tiers"] = [
        tier
        for tier, result in metrics["loyalty_tier_slices"].items()
        if result["flagged"]
    ]
    return model, metrics
