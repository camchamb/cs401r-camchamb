"""Export the Lab 2 training feature baseline required by Lab 6 Task 1.

This reproduces the Lab 2 transform and feature cutoff locally from the source
CSV, then writes only the eleven ordered endpoint inputs. The resulting CSV is
safe to commit: it contains aggregate numeric features and no customer IDs,
labels, risk scores, or direct identifiers.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

# Running this file directly places monitoring/ rather than the repository root
# on sys.path. Add the root so the Lab 2 and Lab 3 modules remain the source of
# truth for feature engineering and endpoint column order.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from data.feature_engineering import FEATURE_CUTOFF, build_churn_feature_set
from models.churn.churn_model import FEATURE_COLUMNS


STRING_COLUMNS = ["payment_method", "channel", "store_id", "product_category"]
NUMERIC_COLUMNS = ["order_value", "num_items"]


def clean_transactions(path: Path) -> pd.DataFrame:
    """Apply the same casts, imputations, and deduplication as Glue Lab 2."""
    frame = pd.read_csv(path, dtype=str)
    for column in frame.columns:
        frame[column] = frame[column].str.strip().replace("", pd.NA)

    frame["purchase_date"] = pd.to_datetime(
        frame["purchase_date"], format="mixed", errors="coerce"
    ).dt.normalize()
    frame = frame.dropna(subset=["customer_id", "purchase_date"])

    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        median = frame[column].median()
        fallback = 0 if pd.isna(median) else median
        if column == "num_items":
            fallback = int(round(fallback))
        frame[column] = frame[column].fillna(fallback)
    frame["num_items"] = frame["num_items"].astype(int)
    frame[STRING_COLUMNS] = frame[STRING_COLUMNS].fillna("unknown")

    return (
        frame.sort_values(
            ["transaction_id", "purchase_date", "order_value"],
            ascending=[True, False, False],
        )
        .drop_duplicates("transaction_id", keep="first")
        .reset_index(drop=True)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/northstar-raw-sample.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("monitoring/baseline/baseline.csv"),
    )
    args = parser.parse_args()

    transactions = clean_transactions(args.input)
    history = transactions.loc[
        transactions["purchase_date"] <= FEATURE_CUTOFF
    ].copy()
    customers = history[["customer_id"]].drop_duplicates().reset_index(drop=True)
    features = build_churn_feature_set(customers, history, FEATURE_CUTOFF)
    baseline = features[FEATURE_COLUMNS]

    if baseline.isna().any().any():
        nulls = baseline.isna().sum()
        raise ValueError(f"baseline contains nulls: {nulls[nulls > 0].to_dict()}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    baseline.to_csv(args.output, index=False)
    print(
        f"wrote {len(baseline):,} rows x {len(baseline.columns)} features "
        f"to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
