"""Pandas implementations of the Glue churn feature calculations."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd


FEATURE_CUTOFF = pd.Timestamp("2026-04-01")
HOLDOUT_DAYS = 90
CATEGORY_COUNT = 8.0

TIER_BRONZE_MAX = 500.0
TIER_SILVER_MAX = 2000.0
TIER_GOLD_MAX = 5000.0


def _prepare_history(
    transactions: pd.DataFrame, snapshot_date: date | datetime | str
) -> tuple[pd.DataFrame, pd.Timestamp]:
    required = {"customer_id", "purchase_date"}
    missing = required.difference(transactions.columns)
    if missing:
        raise ValueError(f"transactions is missing required columns: {sorted(missing)}")

    cutoff = pd.Timestamp(snapshot_date).normalize()
    history = transactions.copy()
    history["purchase_date"] = pd.to_datetime(history["purchase_date"]).dt.normalize()
    history = history.loc[history["purchase_date"] <= cutoff].copy()
    return history, cutoff


def _require_columns(frame: pd.DataFrame, columns: set[str], frame_name: str) -> None:
    missing = columns.difference(frame.columns)
    if missing:
        raise ValueError(f"{frame_name} is missing required columns: {sorted(missing)}")


def compute_recency_features(
    transactions: pd.DataFrame,
    snapshot_date: date | datetime | str,
    customers: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute Glue recency and tenure in whole days from the cutoff date.

    A supplied customer roster keeps customers with no history in the output;
    they receive the same 999-day recency sentinel used by the data checks.
    """
    history, cutoff = _prepare_history(transactions, snapshot_date)
    aggregates = (
        history.groupby("customer_id", as_index=False, dropna=False)
        .agg(
            _last_purchase=("purchase_date", "max"),
            _first_purchase=("purchase_date", "min"),
        )
    )
    aggregates["days_since_last_purchase"] = (
        cutoff - aggregates["_last_purchase"]
    ).dt.days
    aggregates["customer_tenure_days"] = (
        cutoff - aggregates["_first_purchase"]
    ).dt.days
    aggregates = aggregates.drop(
        columns=["_last_purchase", "_first_purchase"]
    )

    if customers is None:
        result = aggregates
    else:
        _require_columns(customers, {"customer_id"}, "customers")
        roster = customers[["customer_id"]].drop_duplicates()
        result = roster.merge(aggregates, on="customer_id", how="left")

    result["days_since_last_purchase"] = result[
        "days_since_last_purchase"
    ].fillna(999)
    result["customer_tenure_days"] = result["customer_tenure_days"].fillna(0)
    return result


def compute_frequency_features(
    transactions: pd.DataFrame, snapshot_date: date | datetime | str
) -> pd.DataFrame:
    """Count transaction rows in the strict 30-, 90-, and 180-day Glue windows."""
    history, cutoff = _prepare_history(transactions, snapshot_date)
    _require_columns(history, {"customer_id"}, "transactions")
    feature_columns = [
        "purchase_frequency_30d",
        "purchase_frequency_90d",
        "purchase_frequency_180d",
    ]
    if history.empty:
        return pd.DataFrame(columns=["customer_id", *feature_columns])

    counts = history[["customer_id"]].drop_duplicates().copy()
    for window in (30, 90, 180):
        in_window = history.loc[
            history["purchase_date"] > cutoff - pd.Timedelta(days=window)
        ]
        window_counts = in_window.groupby("customer_id", dropna=False).size()
        column = f"purchase_frequency_{window}d"
        counts[column] = counts["customer_id"].map(window_counts).fillna(0)
    return counts


def compute_monetary_features(
    transactions: pd.DataFrame, snapshot_date: date | datetime | str
) -> pd.DataFrame:
    """Compute Glue monetary aggregates, including 180-day items per order."""
    history, cutoff = _prepare_history(transactions, snapshot_date)
    _require_columns(history, {"order_value", "num_items"}, "transactions")
    feature_columns = [
        "avg_order_value",
        "total_spend_90d",
        "total_lifetime_value",
        "avg_basket_size_6m",
    ]
    if history.empty:
        return pd.DataFrame(columns=["customer_id", *feature_columns])

    last_90_days = history["purchase_date"] > cutoff - pd.Timedelta(days=90)
    last_180_days = history["purchase_date"] > cutoff - pd.Timedelta(days=180)
    monetary = history.assign(
        _spend_90d=history["order_value"].where(last_90_days, 0)
    )
    result = (
        monetary.groupby("customer_id", as_index=False, dropna=False)
        .agg(
            avg_order_value=("order_value", "mean"),
            total_spend_90d=("_spend_90d", lambda values: values.sum(min_count=1)),
            total_lifetime_value=(
                "order_value",
                lambda values: values.sum(min_count=1),
            ),
        )
    )
    basket_sizes = (
        history["num_items"]
        .where(last_180_days)
        .groupby(history["customer_id"], dropna=False)
        .mean()
        .rename("avg_basket_size_6m")
    )
    result["avg_basket_size_6m"] = (
        result["customer_id"].map(basket_sizes).fillna(0)
    )
    return result


def compute_behavioral_features(
    transactions: pd.DataFrame,
    snapshot_date: date | datetime | str = FEATURE_CUTOFF,
) -> pd.DataFrame:
    """Compute category diversity and online-order share over all history."""
    history, _ = _prepare_history(transactions, snapshot_date)
    _require_columns(history, {"product_category", "channel"}, "transactions")
    if history.empty:
        return pd.DataFrame(
            columns=[
                "customer_id",
                "category_diversity_score",
                "online_to_store_ratio",
            ]
        )

    def diversity(categories: pd.Series) -> float:
        known_categories = categories.loc[
            categories.notna() & categories.ne("unknown")
        ]
        return float(known_categories.nunique() / CATEGORY_COUNT)

    result = (
        history.groupby("customer_id", as_index=False, dropna=False)
        .agg(
            category_diversity_score=("product_category", diversity),
            online_to_store_ratio=(
                "channel",
                lambda channels: channels.eq("online").mean(),
            ),
        )
    )
    return result


def build_churn_feature_set(
    customers: pd.DataFrame,
    transactions: pd.DataFrame,
    snapshot_date: date | datetime | str,
) -> pd.DataFrame:
    """Build one row per customer using Glue's observation and holdout windows."""
    _require_columns(customers, {"customer_id"}, "customers")
    _require_columns(
        transactions,
        {
            "customer_id",
            "purchase_date",
            "order_value",
            "num_items",
            "product_category",
            "channel",
        },
        "transactions",
    )
    if customers["customer_id"].duplicated().any():
        raise ValueError("customers must contain one row per customer_id")

    _, cutoff = _prepare_history(transactions, snapshot_date)
    transaction_ids = transactions[["customer_id"]].drop_duplicates()
    roster = pd.concat(
        [customers[["customer_id"]], transaction_ids], ignore_index=True
    ).drop_duplicates("customer_id")
    result = roster.merge(customers, on="customer_id", how="left")

    computed = (
        compute_recency_features(transactions, cutoff, roster)
        .merge(compute_frequency_features(transactions, cutoff), on="customer_id")
        .merge(compute_monetary_features(transactions, cutoff), on="customer_id")
        .merge(compute_behavioral_features(transactions, cutoff), on="customer_id")
    )
    glue_columns = [
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
    result = result.drop(
        columns=[
            column
            for column in [
                *glue_columns,
                "loyalty_tier",
                "churn_risk_score",
                "churn_label",
            ]
            if column in result.columns
        ]
    ).merge(computed, on="customer_id", how="left")

    for column in [
        "purchase_frequency_30d",
        "purchase_frequency_90d",
        "purchase_frequency_180d",
        "total_spend_90d",
        "total_lifetime_value",
        "avg_basket_size_6m",
        "category_diversity_score",
        "online_to_store_ratio",
    ]:
        result[column] = result[column].fillna(0)
    result["avg_order_value"] = result["avg_order_value"].fillna(0)

    lifetime_value = result["total_lifetime_value"]
    result["loyalty_tier"] = "Platinum"
    result.loc[lifetime_value < TIER_GOLD_MAX, "loyalty_tier"] = "Gold"
    result.loc[lifetime_value < TIER_SILVER_MAX, "loyalty_tier"] = "Silver"
    result.loc[lifetime_value < TIER_BRONZE_MAX, "loyalty_tier"] = "Bronze"

    days_since = result["days_since_last_purchase"]
    frequency_30d = result["purchase_frequency_30d"]
    high_risk = (days_since > 60) & (frequency_30d == 0)
    medium_risk = (days_since > 30) & ~high_risk
    result["churn_risk_score"] = (frequency_30d * 0.04).clip(upper=0.4)
    result.loc[medium_risk, "churn_risk_score"] = (
        0.4 + (days_since[medium_risk] - 30) / 30 * 0.3
    ).clip(upper=0.7)
    result.loc[high_risk, "churn_risk_score"] = (
        0.7 + (days_since[high_risk] - 60) / 120 * 0.3
    ).clip(upper=1.0)
    result["churn_risk_score"] = result["churn_risk_score"].clip(0, 1)

    holdout_end = cutoff + pd.Timedelta(days=HOLDOUT_DAYS)
    purchase_dates = pd.to_datetime(transactions["purchase_date"]).dt.normalize()
    holdout_customers = transactions.loc[
        (purchase_dates > cutoff) & (purchase_dates <= holdout_end), "customer_id"
    ].drop_duplicates()
    result["churn_label"] = (
        ~result["customer_id"].isin(holdout_customers)
    ).astype(int)

    for column in [
        "avg_order_value",
        "total_spend_90d",
        "total_lifetime_value",
        "avg_basket_size_6m",
        "category_diversity_score",
        "online_to_store_ratio",
    ]:
        result[column] = result[column].round(4)

    return result
