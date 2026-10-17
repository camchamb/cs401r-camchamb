"""
NorthStar Retail — Churn Prediction Training Script (Skeleton)
CS 401R Lab 3 Starter Kit

This script is the SageMaker XGBoost training entry point for the NorthStar
churn prediction model. It reads features from the SageMaker Feature Store,
trains an XGBoost classifier, tracks the run in a SageMaker MLflow App, and
registers the model in the Model Registry.

Experiment tracking needs two packages that are NOT in the training container:

    pip install mlflow sagemaker-mlflow

`sagemaker-mlflow` is the SigV4 auth plugin for arn:aws:sagemaker:... tracking
URIs. Install `mlflow` alone and you get a connection error that never mentions
credentials. See track_run() for the cost warning about the OTHER MLflow
product -- read it before you create anything.

Usage (local testing):
    python churn_training_skeleton.py \
        --feature-group-name northstar-dev-customer-features \
        --artifacts-bucket northstar-dev-data-<account> \
        --max-depth 6 --eta 0.1 --num-round 200 \
        --mlflow-arn arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX \
        --run-name xgb-depth6-eta0.1

Usage (SageMaker training job — parameters passed via hyperparameter dict):
    Configured via the SageMaker Python SDK Estimator.
"""

import argparse
import json
import os
import pickle
import tarfile
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd

# SageMaker imports — available in SageMaker training containers.
#
# The two failure modes below are different problems and used to produce the
# same message. sagemaker 3.x REMOVED the feature_store package, so on a 3.x
# install the SDK imports fine and only the second line fails. Reporting that
# as "sagemaker not available" sends you looking for a missing install that is
# not missing. Pin sagemaker<3.0.0 (see requirements.txt, defect 55).
try:
    import sagemaker
except ImportError:
    sagemaker = None
    SAGEMAKER_AVAILABLE = False
    print("WARNING: sagemaker not installed — running in local test mode")
else:
    try:
        from sagemaker.feature_store.feature_group import FeatureGroup
        from sagemaker.session import Session
        SAGEMAKER_AVAILABLE = True
    except ImportError:
        SAGEMAKER_AVAILABLE = False
        print("WARNING: sagemaker is installed but has no feature_store module. "
              "This is sagemaker 3.x, which dropped it. Run "
              "`pip install 'sagemaker>=2.200.0,<3.0.0'` — the Feature Store "
              "path in this script cannot work on 3.x.")

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    raise ImportError("xgboost is required: pip install xgboost")

from sklearn.metrics import (auc, classification_report, precision_recall_curve,
                              confusion_matrix, roc_auc_score, recall_score)
from sklearn.model_selection import train_test_split

# ── Argument Parsing ───────────────────────────────────────────────────────────
# SageMaker passes hyperparameters as CLI arguments.

def parse_args():
    parser = argparse.ArgumentParser(description="NorthStar churn prediction training")

    # Data configuration
    # Names come from Lab 2's Terraform: `terraform output -raw feature_group_name`
    # and `terraform output -raw s3_bucket_name`. Do not invent new ones.
    parser.add_argument("--feature-group-name", type=str,
                        default=os.environ.get("FEATURE_GROUP_NAME"),
                        help="SageMaker Feature Store feature group name (Lab 2 output)")
    # These dates bound the INGEST batch, not the feature window. Lab 2 stamps
    # event_time with the wall-clock time its Glue job ran (time.time()), not
    # with the feature cutoff T = 2026-04-01 -- T was already enforced when the
    # features were computed. A window that ends before your Lab 2 run (the old
    # default here was 2026-06-01) matches zero rows. These defaults match the
    # reference implementation and cover any Lab 2 run during the semester.
    parser.add_argument("--training-start-date", type=str,
                        default=os.environ.get("TRAINING_START_DATE", "2026-01-01"),
                        help="Earliest Lab 2 ingest date to include (YYYY-MM-DD)")
    parser.add_argument("--training-end-date", type=str,
                        default=os.environ.get("TRAINING_END_DATE", "2027-01-01"),
                        help="Latest Lab 2 ingest date to include (YYYY-MM-DD)")
    parser.add_argument("--artifacts-bucket", type=str,
                        default=os.environ.get("ARTIFACTS_BUCKET"), required=False,
                        help="Your Lab 1 data bucket, northstar-dev-data-<account>. "
                             "Athena results and model artifacts go under its artifacts/ "
                             "prefix, the only prefix your MLEngineer role can write")
    parser.add_argument("--local-data-path", type=str, default=None,
                        help="Path to local feature CSV for development testing (bypasses Feature Store)")

    # XGBoost hyperparameters
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--eta", type=float, default=0.1)
    parser.add_argument("--num-round", type=int, default=200)
    parser.add_argument("--min-child-weight", type=int, default=5)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--objective", type=str, default="binary:logistic")
    parser.add_argument("--eval-metrics", nargs="+", default=["logloss", "auc"],
                        help="XGBoost metrics; the final metric drives early stopping")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verbosity", type=int, default=1)
    parser.add_argument("--early-stopping-rounds", type=int, default=20)
    parser.add_argument("--verbose-eval", type=int, default=25)
    parser.add_argument("--validation-fraction", type=float, default=0.30)
    parser.add_argument("--baseline-max-depth", type=int, default=4)
    parser.add_argument("--baseline-eta", type=float, default=0.1)
    parser.add_argument("--baseline-num-round", type=int, default=200)
    parser.add_argument("--baseline-subsample", type=float, default=0.9)
    parser.add_argument("--baseline-colsample-bytree", type=float, default=0.9)
    parser.add_argument("--baseline-eval-metric", type=str, default="auc")
    parser.add_argument("--scale-pos-weight", type=float, default=None,
                        help=(
                            "XGBoost positive-class weight. Leave unset and it is computed "
                            "from YOUR training split as negatives/positives, which is what "
                            "you want. The reference 10,000-customer dataset is 22.0%% "
                            "churners, giving (1 - 0.220) / 0.220 = 3.545. Passing a value "
                            "tuned for a different class balance will quietly skew your "
                            "precision/recall trade-off, so only override this deliberately."
                        ))
    # Experiment tracking (Task 1, 5 points)
    parser.add_argument("--mlflow-arn", type=str,
                        default=os.environ.get("MLFLOW_APP_ARN"),
                        help=(
                            "ARN of your SageMaker MLflow APP, e.g. "
                            "arn:aws:sagemaker:us-east-1:<account>:mlflow-app/app-XXXX. "
                            "Create it once with `aws sagemaker create-mlflow-app`. "
                            "This is NOT an MLflow Tracking Server -- see the warning "
                            "on track_run() below. Omit to skip tracking."
                        ))
    parser.add_argument("--mlflow-experiment", type=str, default="northstar-churn",
                        help="MLflow experiment name; runs group under this")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Name for this run. Make it descriptive -- 'run3' tells "
                             "you nothing three weeks later.")

    parser.add_argument("--model-dir", type=str,
                        default=os.environ.get("SM_MODEL_DIR", "./model"))
    parser.add_argument("--output-data-dir", type=str,
                        default=os.environ.get("SM_OUTPUT_DATA_DIR",
                                               "docs/lab3-evidence"))
    parser.add_argument("--model-package-group-name", type=str,
                        default=os.environ.get("MODEL_PACKAGE_GROUP_NAME",
                                               "northstar-churn-models"))
    parser.add_argument("--artifact-prefix", type=str,
                        default=os.environ.get("ARTIFACT_PREFIX", "artifacts/models/churn"))

    args = parser.parse_args()
    if not args.artifacts_bucket:
        parser.error("--artifacts-bucket or ARTIFACTS_BUCKET is required")
    if not args.local_data_path and not args.feature_group_name:
        parser.error("--feature-group-name or FEATURE_GROUP_NAME is required unless --local-data-path is used")
    if not 0 < args.validation_fraction < 1:
        parser.error("--validation-fraction must be between zero and one")
    return args


# ── Feature Store Loading ──────────────────────────────────────────────────────

# These are exactly the features your Lab 2 pipeline wrote to the Feature
# Group. If a name here does not match your feature group, the Athena query
# below will fail - check `aws sagemaker describe-feature-group` first.
#
# Note what is NOT in this list: churn_label (the target) and customer_id
# (an identifier, not a signal). Including either as an input is leakage.
FEATURE_COLUMNS = [
    # Recency and tenure
    "days_since_last_purchase",
    "customer_tenure_days",
    # Frequency
    "purchase_frequency_30d",
    "purchase_frequency_90d",
    "purchase_frequency_180d",
    # Monetary
    "avg_order_value",
    "total_spend_90d",
    "total_lifetime_value",
    "avg_basket_size_6m",
    # Behavioural - these are the features that catch churners who still
    # look active on recency alone. Drop them and your model collapses
    # toward the recency-only baseline.
    "category_diversity_score",
    "online_to_store_ratio",
]

# churn_risk_score is available in the feature group but deliberately excluded
# here: it is a pure recency heuristic and doubles as the baseline you must
# beat. If you add it, report your metrics both with and without it.
BASELINE_COLUMN = "churn_risk_score"

# The recency-only baseline model uses just this one feature. Task 1 requires
# you to train it and show your full model beats it by a margin whose 95%
# confidence interval excludes zero. There is no fixed AUC-lift threshold: a
# ">= 0.03 lift" gate lived here until 2026-08-02 and was removed because 0.03
# is smaller than the metric's own run-to-run standard deviation, so it failed
# on 21% of splits regardless of model quality. See EVAL_THRESHOLDS below.
BASELINE_FEATURE = "days_since_last_purchase"

LABEL_COLUMN = "churn_label"

# Not a feature. This is the column Task 1's slice evaluation groups by, so it
# has to come back from the query even though it never enters the model. Keep it
# out of FEATURE_COLUMNS: loyalty_tier correlates with spend, and feeding it in
# both leaks and makes the per-tier fairness check circular.
SLICE_COLUMN = "loyalty_tier"


def load_features_from_feature_store(feature_group_name: str,
                                     start_date: str,
                                     end_date: str,
                                     artifacts_bucket: str) -> pd.DataFrame:
    """
    Load features from the SageMaker Feature Store offline store using Athena.

    Args:
        feature_group_name: your Lab 2 feature group
        start_date, end_date: ISO dates (YYYY-MM-DD) bounding the event_time range
        artifacts_bucket: S3 bucket that Athena writes its result set to. Athena
            will not run a query without an output location, so this has to be
            threaded in from --artifacts-bucket rather than assumed.

    Reference: https://docs.aws.amazon.com/sagemaker/latest/dg/feature-store-use-with-studio.html
    """
    if os.environ.get("SM_TRAINING_ENV"):
        raise RuntimeError(
            "Athena queries must run locally under the user's AWS credentials, "
            "not inside a SageMaker training container. Run this Feature Store "
            "query locally, write the result under artifacts/, and provide that "
            "file to the training job with --local-data-path."
        )
    if not SAGEMAKER_AVAILABLE:
        raise RuntimeError("SageMaker SDK not available - use --local-data-path for local testing")

    # event_time was written as Fractional (epoch seconds) by the Lab 2 job, so
    # the date bounds must be converted before comparison. Passing ISO strings
    # straight into the query fails with TYPE_MISMATCH rather than returning
    # rows, so at least the failure is loud.
    start_epoch = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    end_epoch = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    session = Session()
    feature_group = FeatureGroup(name=feature_group_name, sagemaker_session=session)

    # Two things in the query below are easy to get wrong, so read them:
    #
    # 1. event_time is Fractional - Unix epoch SECONDS, not an ISO string.
    #    Comparing it to '2026-04-01' fails the query outright with:
    #      TYPE_MISMATCH: Cannot check if double is BETWEEN varchar(10)...
    #    Convert your date bounds to epoch seconds before substituting them.
    #
    # 2. The offline store is append-only. Re-running the Lab 2 feature job
    #    writes a SECOND record for every customer, and a naive SELECT then
    #    returns duplicate customers with stale feature values. Deduplicate
    #    with a window function keyed on customer_id, keeping the most recent
    #    event_time. Filtering on a global MAX(write_time) does NOT work -
    #    it keeps only the customers written in the final microsecond.
    #
    #    The offline store also soft-deletes: rows carry is_deleted, and
    #    deleted records must be excluded.
    #
    # 3. The outer SELECT must ORDER BY customer_id, and this is the one people
    #    skip because it looks cosmetic. It is not. Athena parallelises the scan
    #    across the offline store's Parquet objects and returns rows in whatever
    #    order the splits happen to finish, which varies from run to run.
    #    train_test_split(random_state=42) is deterministic only for a GIVEN row
    #    order -- so without ORDER BY, the identical data produces a different
    #    train/test split, and therefore different metrics, on every single run.
    #
    #    This was measured, not theorised: four runs on byte-identical data
    #    produced AUC between 0.7276 and 0.7431, and a Platinum-slice AUC
    #    between 0.430 and 0.700. An entire "the model is worse than random on
    #    your best customers" finding turned out to be an artefact of row order.
    #
    #    The rn = 1 filter guarantees customer_id is unique here, so ordering on
    #    it is a TOTAL order and the pipeline becomes reproducible.
    #
    #    If your metrics move between runs and your data did not, this is why.

    #    loyalty_tier is selected but is NOT in FEATURE_COLUMNS, and that is
    #    deliberate. It is not an input to the model - it is the column Task 1's
    #    slice evaluation groups by. Keep it out of the feature matrix (see
    #    preprocess_features, which selects FEATURE_COLUMNS explicitly) and
    #    carry it alongside X and y through the split so each row's prediction
    #    can still be attributed to a tier:
    #
    #        X_train, X_val, y_train, y_val, tier_train, tier_val = \
    #            train_test_split(X, y, df[SLICE_COLUMN], test_size=0.30,
    #                             random_state=42, stratify=y)
    #
    #    Split X and y without it and you cannot line the tiers back up.

    query = feature_group.athena_query()
    query_string = f"""
        WITH ranked AS (
            SELECT
                customer_id,
                {", ".join(FEATURE_COLUMNS)},
                {LABEL_COLUMN},
                {SLICE_COLUMN},
                event_time,
                ROW_NUMBER() OVER (
                    PARTITION BY customer_id
                    ORDER BY event_time DESC, write_time DESC
                ) AS rn
            FROM "{query.table_name}"
            WHERE event_time BETWEEN {start_epoch} AND {end_epoch}
              AND NOT is_deleted
        )
        SELECT
            customer_id,
            {", ".join(FEATURE_COLUMNS)},
            {LABEL_COLUMN},
            {SLICE_COLUMN}
        FROM ranked
        WHERE rn = 1
        ORDER BY customer_id
    """

    query.run(query_string=query_string,
              output_location=f"s3://{artifacts_bucket}/artifacts/athena-results/")
    query.wait()
    print("Athena QueryExecutionId: "
          f"{query.get_query_execution()['QueryExecution']['QueryExecutionId']}")
    df = query.as_dataframe()
    return df


def load_features_local(path: str) -> pd.DataFrame:
    """Load features from a local CSV file (development mode)."""
    print(f"Loading features from local file: {path}")
    df = pd.read_csv(path)
    missing = [c for c in FEATURE_COLUMNS + [LABEL_COLUMN] if c not in df.columns]
    if missing:
        raise ValueError(f"Local data missing required columns: {missing}")
    # Not fatal - the model trains fine without it - but Task 1's slice
    # evaluation cannot be done at all, so fail the task rather than the run.
    if SLICE_COLUMN not in df.columns:
        print(f"WARNING: '{SLICE_COLUMN}' not in local data — Task 1 slice "
              f"evaluation will not be possible with this file")
    return df


# ── Feature Preprocessing ──────────────────────────────────────────────────────

def preprocess_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    Clean and prepare features for XGBoost training.

    Missing feature values are imputed with -1 as a sentinel value.
    This preserves all labeled samples and gives the model a consistent
    numeric representation for missing values.

    Rows with missing labels are dropped because they cannot be used
    for supervised training.

    Returns:
        X: Feature matrix of shape (n_samples, n_features).
        y: Integer label array of shape (n_samples,).
    """
    print(f"Preprocessing {len(df):,} samples...")

    # Remove rows without labels; supervised learning requires known targets.
    df = df.dropna(subset=[LABEL_COLUMN]).copy()

    if df.empty:
        raise ValueError("No samples remain after dropping rows with missing labels.")

    X_df = df[FEATURE_COLUMNS].copy()
    X_df = X_df.fillna(-1)

    X = X_df.to_numpy(dtype=np.float32)

    y = df[LABEL_COLUMN].to_numpy(dtype=int)

    if not np.isfinite(X).all():
        raise ValueError("Feature matrix contains infinite or non-finite values.")

    if len(np.unique(y)) < 2:
        raise ValueError("Training data must contain at least two classes.")

    print(f"  Samples after dropping missing labels: {len(df):,}")
    print(f"  Missing feature values imputed: {int(df[FEATURE_COLUMNS].isna().sum().sum()):,}")
    print(f"  Positive class rate: {y.mean():.1%}")
    print(f"  Feature matrix shape: {X.shape}")

    return X, y


# ── Model Training ─────────────────────────────────────────────────────────────

def train_model(X_train: np.ndarray,
                y_train: np.ndarray,
                X_val: np.ndarray,
                y_val: np.ndarray,
                args) -> xgb.Booster:
    """Train the XGBoost churn model."""

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_COLUMNS)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_COLUMNS)

    # Class imbalance, computed from the training split rather than hardcoded.
    # On the reference dataset this lands at 3.545.
    spw = (args.scale_pos_weight if args.scale_pos_weight is not None
           else float((y_train == 0).sum() / max((y_train == 1).sum(), 1)))

    # Record the RESOLVED value so track_run() can log what was actually used.
    # Logging args.scale_pos_weight instead would log None on every run that
    # let it compute itself -- which is most of them -- and your MLflow
    # comparison would be missing the one parameter most likely to explain a
    # precision/recall difference between runs.
    args.resolved_scale_pos_weight = spw

    params = {
        # eval_metric ORDER MATTERS. XGBoost early-stops on the LAST metric in
        # this list, not the first. This used to read ["auc", "logloss"], which
        # silently made logloss the early-stopping criterion: under
        # scale_pos_weight the reweighted logloss keeps improving long after
        # ranking quality peaks, so training ran the full 200 rounds instead of
        # stopping near round 31 and validation AUC came out 0.7603 instead of
        # 0.7822. Measured across 50 splits, that cost the model the promotion
        # gate on 28% of them -- a student doing everything right was told their
        # features did not beat recency. Keep "auc" last (defect 49).
        "objective": args.objective,
        "eval_metric": args.eval_metrics,
        "max_depth": args.max_depth,
        "eta": args.eta,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "scale_pos_weight": spw,
        "seed": args.seed,
        "verbosity": args.verbosity,
    }

    print(f"\nTraining XGBoost with params: {json.dumps(params, indent=2)}")

    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_round,
        evals=[(dtrain, "train"), (dval, "validation")],
        early_stopping_rounds=args.early_stopping_rounds,
        verbose_eval=args.verbose_eval,
    )

    return model


# ── Evaluation ─────────────────────────────────────────────────────────────────

EVAL_THRESHOLDS = {
    "precision_top10":   0.50,   # Minimum precision @ top 10% scored customers
    "recall_top10":      0.25,   # Minimum recall @ top 10% scored customers
    # Note the recall ceiling: with ~22% positives, targeting the top 10% of
    # customers caps achievable recall near 0.45. Hitting 0.25 means capturing
    # roughly half of what is reachable within that contact budget.
}

# There is deliberately NO absolute AUC threshold. One used to live here
# (auc_roc >= 0.72) and it was removed on 2026-08-02: measured across 200
# random train/test splits of the same data, the reference model fell below
# 0.72 on 58% of them. A gate the reference clears by luck of the shuffle
# grades your random seed, not your model. Report AUC; do not gate on it.
#
# The baseline gate is an INTERVAL, not a threshold: the 95% CI on
# (your AUC - recency-only baseline AUC) must exclude zero.
# BASELINE_FEATURE is defined once, near FEATURE_COLUMNS above.
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42


def train_recency_baseline(X_train: np.ndarray,
                           y_train: np.ndarray,
                           X_val: np.ndarray,
                           args) -> np.ndarray:
    """Train the recency-only baseline and score the validation split.

    The baseline is a model over `days_since_last_purchase` ALONE, trained on
    the same training rows and scored on the same validation rows as the full
    model. That is what makes the comparison fair.

    It is deliberately NOT a constant predictor. A constant prediction has an
    AUC of exactly 0.5 by construction, so "beating" it proves only that your
    model is better than a coin flip. The question Lab 3 asks is whether your
    feature engineering beats the rule the business already has for free:
    "contact whoever has not purchased in a while."
    """
    i = FEATURE_COLUMNS.index(BASELINE_FEATURE)
    dtr = xgb.DMatrix(X_train[:, [i]], label=y_train, feature_names=[BASELINE_FEATURE])
    dva = xgb.DMatrix(X_val[:, [i]], feature_names=[BASELINE_FEATURE])
    spw = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))
    params = {
        "objective": args.objective, "eval_metric": args.baseline_eval_metric,
        "max_depth": args.baseline_max_depth, "eta": args.baseline_eta,
        "subsample": args.baseline_subsample,
        "colsample_bytree": args.baseline_colsample_bytree,
        "scale_pos_weight": spw, "seed": args.seed,
    }
    return xgb.train(params, dtr, num_boost_round=args.baseline_num_round).predict(dva)


def bootstrap_lift_ci(y_true: np.ndarray,
                      proba: np.ndarray,
                      baseline_proba: np.ndarray,
                      n: int = BOOTSTRAP_N,
                      seed: int = BOOTSTRAP_SEED,
                      alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI for (model AUC - baseline AUC), by resampling the val set.

    PAIRED: each replicate resamples row indices once and scores BOTH models on
    those same rows, so the interval is on the difference and the correlation
    between the two models is preserved. Resampling them independently breaks
    the pairing and inflates the interval, which would make a real improvement
    look inconclusive.

    Replicates whose resample happens to be single-class are skipped, because
    AUC is undefined there.

    The seed is fixed so the gate is reproducible: the same split must always
    produce the same verdict.
    """
    rng = np.random.default_rng(seed)
    y_true = np.asarray(y_true)
    idx = np.arange(len(y_true))
    diffs = []
    for _ in range(n):
        s = rng.choice(idx, size=len(idx), replace=True)
        ys = y_true[s]
        if len(np.unique(ys)) < 2:
            continue
        diffs.append(roc_auc_score(ys, proba[s]) - roc_auc_score(ys, baseline_proba[s]))
    if len(diffs) < n // 2:
        raise ValueError(
            "Too few usable bootstrap replicates to form a confidence interval. "
            "Your validation set is too small or too imbalanced to support this gate."
        )
    d = np.sort(diffs)
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))


def evaluate_model(model: xgb.Booster,
                   X_val: np.ndarray,
                   y_val: np.ndarray,
                   baseline_proba: np.ndarray) -> dict:
    """
    Evaluate the trained model against required thresholds.
    Returns a metrics dict. Raises ValueError if thresholds are not met.
    """
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = model.predict(dval)
    y_pred_binary = (y_pred_proba > 0.5).astype(int)

    auc_roc = roc_auc_score(y_val, y_pred_proba)

    # Precision and recall @ top 10%
    top10_threshold = np.percentile(y_pred_proba, 90)
    top10_mask = y_pred_proba >= top10_threshold
    precision_top10 = y_val[top10_mask].mean() if top10_mask.sum() > 0 else 0.0
    recall_top10 = y_val[top10_mask].sum() / y_val.sum() if y_val.sum() > 0 else 0.0

    # Baseline: a model trained on days_since_last_purchase ALONE.
    #
    # This used to be `np.full_like(y_pred_proba, y_val.mean())` - a constant
    # prediction, whose AUC is exactly 0.5 by definition. Beating a coin flip
    # is not evidence that your feature engineering did anything, and it is not
    # what Lab 3 asks for. The comparison that matters is against recency,
    # because recency is the rule the business already has for free.
    #
    baseline_auc = roc_auc_score(y_val, baseline_proba)
    auc_vs_baseline = auc_roc - baseline_auc

    lift_ci_low, lift_ci_high = bootstrap_lift_ci(
        y_val, y_pred_proba, baseline_proba)

    metrics = {
        "auc_roc": round(float(auc_roc), 4),
        "precision_top10": round(float(precision_top10), 4),
        "recall_top10": round(float(recall_top10), 4),
        "auc_vs_baseline": round(float(auc_vs_baseline), 4),
        "baseline_auc": round(float(baseline_auc), 4),
        "lift_ci_low": round(float(lift_ci_low), 4),
        "lift_ci_high": round(float(lift_ci_high), 4),
        "positive_rate_val": round(float(y_val.mean()), 4),
        "n_val_samples": int(len(y_val)),
        "eval_timestamp": datetime.utcnow().isoformat(),
    }

    print("\n── Evaluation Results ──────────────────────────────")
    failures = []
    for metric, threshold in EVAL_THRESHOLDS.items():
        value = metrics[metric]
        status = "✓ PASS" if value >= threshold else "✗ FAIL"
        print(f"  {metric:25s}: {value:.4f}  (threshold: {threshold:.4f})  {status}")
        if value < threshold:
            failures.append(f"{metric}={value:.4f} < {threshold}")

    # The baseline gate: the interval must exclude zero.
    ci_status = "\u2713 PASS" if lift_ci_low > 0 else "\u2717 FAIL"
    print(f"  {'auc_lift 95% CI':25s}: [{lift_ci_low:.4f}, {lift_ci_high:.4f}]"
          f"  (must exclude 0)  {ci_status}")
    if lift_ci_low <= 0:
        failures.append(
            f"auc_lift 95% CI [{lift_ci_low:.4f}, {lift_ci_high:.4f}] includes "
            f"zero - no evidence the model beats the recency-only baseline")

    if failures:
        raise ValueError(
            f"Model failed evaluation thresholds: {'; '.join(failures)}. "
            f"Do not promote to Model Registry."
        )

    print("  All thresholds passed ✓")

    return metrics


# ── Slice Evaluation ───────────────────────────────────────────────────────────

def evaluate_slices(model: xgb.Booster,
                    X_val: np.ndarray,
                    y_val: np.ndarray,
                    slice_column: pd.Series) -> dict:
    """
    Evaluate model performance by slice (e.g., loyalty_tier, age_band).
    Flag slices where recall@10% drops more than 10pp below aggregate.

    Each slice is scored using its own top 10 percent, so a small slice is not
    silently omitted just because the overall top 10 percent contains no rows
    from it.
    """
    if len(X_val) != len(y_val) or len(y_val) != len(slice_column):
        raise ValueError("X_val, y_val, and slice_column must have the same length.")

    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    y_pred_proba = model.predict(dval)
    y_val = np.asarray(y_val)

    def recall_at_top_10_percent(labels: np.ndarray,
                                 probabilities: np.ndarray) -> float:
        if len(labels) == 0 or labels.sum() == 0:
            return 0.0
        # argsort selects exactly ceil(10% * n), even when prediction scores tie.
        n_selected = max(1, int(np.ceil(len(labels) * 0.10)))
        selected = np.argsort(probabilities, kind="mergesort")[-n_selected:]
        return float(labels[selected].sum() / labels.sum())

    aggregate_recall = recall_at_top_10_percent(y_val, y_pred_proba)
    slice_values = pd.Series(slice_column).reset_index(drop=True)

    slice_results = {}
    for slice_value in slice_values.drop_duplicates():
        mask = (slice_values == slice_value).to_numpy()
        labels = y_val[mask]
        probabilities = y_pred_proba[mask]
        recall_top10 = recall_at_top_10_percent(labels, probabilities)
        slice_auc = (float(roc_auc_score(labels, probabilities))
                     if len(np.unique(labels)) == 2 else None)
        recall_gap = aggregate_recall - recall_top10
        flagged = recall_gap > 0.10
        positives = int(labels.sum())
        too_small_to_conclude = positives < 20

        slice_results[str(slice_value)] = {
            "n_samples": int(mask.sum()),
            "positives": positives,
            "auc_roc": round(slice_auc, 4) if slice_auc is not None else None,
            "recall_top10": round(recall_top10, 4),
            "recall_gap": round(recall_gap, 4),
            "flagged": flagged,
            "too_small_to_conclude": too_small_to_conclude,
        }

    print("\n── Slice Evaluation ─────────────────────────────────")
    print(f"  Aggregate recall@10%: {aggregate_recall:.4f}")
    for value, result in slice_results.items():
        status = "FLAG" if result["flagged"] else "OK"
        auc_display = (f"{result['auc_roc']:.4f}"
                       if result["auc_roc"] is not None else "N/A")
        print(f"  {value}: n={result['n_samples']}, AUC={auc_display}, "
              f"recall@10%={result['recall_top10']:.4f}, "
              f"positives={result['positives']} [{status}]")

    return slice_results


def save_evaluation_artifacts(model: xgb.Booster,
                              X_val: np.ndarray,
                              y_val: np.ndarray,
                              slice_results: dict,
                              output_dir: str) -> None:
    """Write the Lab 3 validation artifacts to the requested evidence directory."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is required to create the Lab 3 PNG evidence artifacts."
        ) from exc

    os.makedirs(output_dir, exist_ok=True)
    dval = xgb.DMatrix(X_val, feature_names=FEATURE_COLUMNS)
    probabilities = model.predict(dval)
    # The model's operational decision threshold is 0.5; top-10% metrics are
    # reported separately and are not a binary classification threshold.
    predictions = (probabilities >= 0.5).astype(int)

    matrix = confusion_matrix(y_val, predictions, labels=[0, 1])
    figure, axis = plt.subplots(figsize=(5, 4))
    image = axis.imshow(matrix, interpolation="nearest", cmap="Blues")
    figure.colorbar(image, ax=axis)
    axis.set(
        xticks=[0, 1], yticks=[0, 1],
        xticklabels=["Retained", "Churn"],
        yticklabels=["Retained", "Churn"],
        xlabel="Predicted label", ylabel="Actual label",
        title="Churn confusion matrix (threshold = 0.50)",
    )
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            axis.text(column, row, str(matrix[row, column]), ha="center", va="center",
                      color="white" if matrix[row, column] > matrix.max() / 2 else "black")
    figure.tight_layout()
    figure.savefig(os.path.join(output_dir, "confusion-matrix.png"), dpi=160)
    plt.close(figure)

    gains = model.get_score(importance_type="gain")
    importance = pd.Series(
        {feature: float(gains.get(feature, 0.0)) for feature in FEATURE_COLUMNS}
    ).sort_values()
    figure, axis = plt.subplots(figsize=(8, 5))
    axis.barh(importance.index, importance.values, color="#2563eb")
    axis.set_xlabel("Average gain")
    axis.set_title("XGBoost feature importance")
    figure.tight_layout()
    figure.savefig(os.path.join(output_dir, "feature-importance.png"), dpi=160)
    plt.close(figure)

    slice_rows = [
        {
            "tier": tier,
            "n": result["n_samples"],
            "positives": result["positives"],
            "auc": result["auc_roc"],
            "recall": result["recall_top10"],
            "too_small_to_conclude": result["too_small_to_conclude"],
        }
        for tier, result in slice_results.items()
    ]
    pd.DataFrame(slice_rows).to_csv(
        os.path.join(output_dir, "slice-evaluation.csv"), index=False
    )
    print(f"Saved confusion matrix, feature importance, and slice evaluation to {output_dir}")


# ── Experiment Tracking (MLflow App) ───────────────────────────────────────────

def track_run(args, metrics: dict, scale_pos_weight: float) -> None:
    """
    Log this training run to your SageMaker MLflow App.

    Worth 5 points in Task 1: >=3 runs, each with logged params AND metrics,
    retrievable via mlflow.search_runs.

    ###########################################################################
    #  THERE ARE TWO MLflow PRODUCTS ON SAGEMAKER. USE THE APP.               #
    #                                                                         #
    #    CreateMlflowApp            serverless   NO ADDITIONAL CHARGE   <-- ok #
    #    CreateMlflowTrackingServer $0.60/hour   until you delete it    <-- NO #
    #                                                                         #
    #  $0.60/hr breaches the entire $10 course budget in 16.7 hours and costs #
    #  about $43 over a weekend -- more per hour than any endpoint in this    #
    #  course. It is not an endpoint, so "did I delete my endpoints?" will    #
    #  not find it. Most tutorials describe the Tracking Server because it    #
    #  shipped first. If anything asks you to choose a size (Small/Medium),   #
    #  you are on the wrong product.                                          #
    ###########################################################################

    Two things that fail in ways the error does not explain:

      1. `create-mlflow-app` postdates many installed AWS CLIs. An old CLI
         says "Invalid choice: 'create-mlflow-app'", which reads like a typo
         rather than a version problem. Check `aws --version` and upgrade.

      2. You need BOTH `mlflow` and `sagemaker-mlflow` installed. The second
         is the SigV4 auth plugin for arn:aws:sagemaker:... tracking URIs.
         Without it you get a connection error that never mentions credentials.

    The tracking URI is a SageMaker MLflow App ARN; sagemaker-mlflow supplies
    the authentication plugin used by MLflow for that URI.
    """
    if not args.mlflow_arn:
        print("\n(no --mlflow-arn given; skipping experiment tracking)")
        return

    # LOG THE XGBOOST VERSION AS A PARAM. Lab 3 documents that identical data
    # and an identical split produce different metrics on XGBoost 3.2.0 vs 1.7
    # (the SageMaker training container is 1.7). If you train some runs locally
    # and some through SageMaker and do not record the version, you will end up
    # comparing two things that were never comparable and cannot tell why.
    #
    # THREE RUNS IS THE FLOOR, NOT THE GOAL. Three runs with identical
    # hyperparameters and different seeds is the same experiment three times;
    # it earns 2 of 5. Vary something you can defend, and be able to say what
    # the variation did to the metrics.

    try:
        import mlflow
    except ImportError as exc:
        raise RuntimeError(
            "MLflow tracking was requested, but mlflow and sagemaker-mlflow "
            "must be installed in the training environment."
        ) from exc

    params = {
        "max_depth": args.max_depth,
        "eta": args.eta,
        "num_round": args.num_round,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "scale_pos_weight": scale_pos_weight,
        "xgboost_version": xgb.__version__,
    }
    numeric_metrics = {
        key: float(value) for key, value in metrics.items()
        if isinstance(value, (int, float, np.integer, np.floating))
        and np.isfinite(value)
    }

    mlflow.set_tracking_uri(args.mlflow_arn)
    mlflow.set_experiment(args.mlflow_experiment)
    with mlflow.start_run(run_name=args.run_name):
        mlflow.log_params(params)
        mlflow.log_metrics(numeric_metrics)
    print(f"Logged run to MLflow experiment '{args.mlflow_experiment}'.")


# ── Model Saving & Registry ────────────────────────────────────────────────────

def save_and_register_model(model: xgb.Booster,
                             metrics: dict,
                             args) -> None:
    """
    Save the model artifact and register it in SageMaker Model Registry.
    Status is set to PendingManualApproval — human approval required before deployment.
    """
    os.makedirs(args.model_dir, exist_ok=True)

    # Save model
    model_path = os.path.join(args.model_dir, "model.xgb")
    model.save_model(model_path)
    print(f"\n✓ Model saved to {model_path}")

    # Save feature metadata alongside model
    metadata = {
        "feature_columns": FEATURE_COLUMNS,
        "label_column": LABEL_COLUMN,
        "hyperparameters": {
            "max_depth": args.max_depth,
            "eta": args.eta,
            "num_round": args.num_round,
        },
        "evaluation_metrics": metrics,
        "training_date": datetime.utcnow().isoformat(),
    }
    with open(os.path.join(args.model_dir, "model_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    # A model package needs a model artifact in S3. Package the native XGBoost
    # file under SageMaker's conventional name before uploading it.
    model_archive = os.path.join(args.model_dir, "model.tar.gz")
    with tarfile.open(model_archive, "w:gz") as archive:
        archive.add(model_path, arcname="xgboost-model")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_key = f"{args.artifact_prefix.rstrip('/')}/{timestamp}/model.tar.gz"
    s3_client = boto3.client("s3")
    s3_client.upload_file(model_archive, args.artifacts_bucket, artifact_key)
    model_data_url = f"s3://{args.artifacts_bucket}/{artifact_key}"

    try:
        from sagemaker import image_uris
        image_uri = image_uris.retrieve(
            framework="xgboost",
            region=boto3.session.Session().region_name,
            image_scope="inference",
            instance_type="ml.m5.large",
        )
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "SageMaker SDK image resolution is required to register the model."
        ) from exc

    scalar_metrics = {
        key: str(value) for key, value in metrics.items()
        if isinstance(value, (str, int, float, bool, np.integer, np.floating))
    }
    description = (
        f"Churn model trained {datetime.now(timezone.utc).date()}. "
        f"Metrics: {json.dumps(scalar_metrics, sort_keys=True)}"
    )
    response = boto3.client("sagemaker").create_model_package(
        ModelPackageGroupName=args.model_package_group_name,
        ModelPackageDescription=description[:1024],
        InferenceSpecification={
            "Containers": [{"Image": image_uri, "ModelDataUrl": model_data_url}],
            "SupportedContentTypes": ["text/csv"],
            "SupportedResponseMIMETypes": ["text/csv"],
        },
        ModelApprovalStatus="PendingManualApproval",
        CustomerMetadataProperties=scalar_metrics,
    )
    print("Registered pending-approval model package: "
          f"{response.get('ModelPackageArn', '<unknown ARN>')}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    os.makedirs(args.model_dir, exist_ok=True)
    os.makedirs(args.output_data_dir, exist_ok=True)

    print("=" * 60)
    print("NorthStar Churn Prediction — Training Script")
    print("=" * 60)

    # 1. Load features
    if args.local_data_path:
        df = load_features_local(args.local_data_path)
    else:
        df = load_features_from_feature_store(
            args.feature_group_name,
            args.training_start_date,
            args.training_end_date,
            args.artifacts_bucket,
        )

    # 2. Preprocess
    df = df.dropna(subset=[LABEL_COLUMN]).reset_index(drop=True)

    X, y = preprocess_features(df)

    # 3. Train/validation split (temporal split is better — split by customer_id here for simplicity)
    # test_size=0.30 matches the reference run and every published figure in
    # Lab 3 (6,999 train / 3,000 test on the 10k dataset). The bootstrap lift CI
    # is calibrated on a test set that size; a 0.20 split leaves 2,000 rows and
    # widens the interval enough to matter.
    #
    if SLICE_COLUMN not in df.columns:
        raise ValueError(
            f"'{SLICE_COLUMN}' is required for loyalty-tier slice evaluation."
        )
    X_train, X_val, y_train, y_val, _, tier_val = train_test_split(
        X, y, df[SLICE_COLUMN], test_size=args.validation_fraction,
        random_state=args.seed, stratify=y
    )
    print(f"\nTrain: {len(X_train):,} | Validation: {len(X_val):,}")

    # 4. Train
    model = train_model(X_train, y_train, X_val, y_val, args)

    # 5. Evaluate
    baseline_proba = train_recency_baseline(X_train, y_train, X_val, args)
    metrics = evaluate_model(model, X_val, y_val, baseline_proba)
    metrics["loyalty_tier_slices"] = evaluate_slices(
        model, X_val, y_val, tier_val
    )
    metrics["flagged_tiers"] = [
        tier for tier, result in metrics["loyalty_tier_slices"].items()
        if result["flagged"]
    ]
    save_evaluation_artifacts(
        model, X_val, y_val, metrics["loyalty_tier_slices"], args.output_data_dir
    )

    # 6. Save and register
    save_and_register_model(model, metrics, args)

    # 7. Track this run in your MLflow App (Task 1, 5 pts)
    track_run(args, metrics, getattr(args, "resolved_scale_pos_weight", None))

    # 8. Write metrics to output. SageMaker also collects these as job output;
    #    that is job lineage, NOT experiment tracking. It records that a job
    #    ran, not what you varied or why. Step 7 is the graded one.
    metrics_path = os.path.join(args.output_data_dir, "evaluation_metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print("\n" + "=" * 60)
    print(f"Training complete. AUC-ROC: {metrics['auc_roc']:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
