"""Non-blocking loyalty-tier fairness flags for the trained candidate model."""

import json
import os
from pathlib import Path
import warnings

import pytest


def _tier_recall_gap(slices):
    if not isinstance(slices, dict) or not slices:
        raise ValueError("Evaluation metrics must include non-empty loyalty_tier_slices.")
    recalls = {
        tier: result["recall_top10"]
        for tier, result in slices.items()
        if isinstance(result, dict) and result.get("recall_top10") is not None
    }
    if len(recalls) != len(slices):
        raise ValueError("Every loyalty-tier slice must include recall_top10.")
    if any(not 0 <= recall <= 1 for recall in recalls.values()):
        raise ValueError("Loyalty-tier recall_top10 values must be between 0 and 1.")

    highest_tier = max(recalls, key=recalls.get)
    lowest_tier = min(recalls, key=recalls.get)
    return highest_tier, lowest_tier, recalls[highest_tier] - recalls[lowest_tier]


def _warn_on_fairness_gap(slices):
    highest_tier, lowest_tier, gap = _tier_recall_gap(slices)
    if gap > 0.10:
        warnings.warn(
            "FAIRNESS FLAG: loyalty-tier recall@top10 gap is "
            f"{gap:.1%} ({highest_tier}={slices[highest_tier]['recall_top10']:.4f}, "
            f"{lowest_tier}={slices[lowest_tier]['recall_top10']:.4f}); "
            "review before approval."
        )
    return gap


def test_tier_recall_gap_finds_extreme_slices():
    slices = {
        "Bronze": {"recall_top10": 0.30},
        "Silver": {"recall_top10": 0.55},
        "Gold": {"recall_top10": 0.40},
    }
    assert _tier_recall_gap(slices) == ("Silver", "Bronze", pytest.approx(0.25))


def test_fairness_flag_warns_above_ten_percentage_points():
    slices = {
        "Bronze": {"recall_top10": 0.20},
        "Gold": {"recall_top10": 0.35},
    }
    with pytest.warns(UserWarning, match="FAIRNESS FLAG"):
        assert _warn_on_fairness_gap(slices) == pytest.approx(0.15)


def test_no_fairness_warning_at_ten_percentage_points():
    slices = {
        "Bronze": {"recall_top10": 0.20},
        "Gold": {"recall_top10": 0.30},
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _warn_on_fairness_gap(slices) == pytest.approx(0.10)
    assert not caught


def test_loyalty_tier_recall_gap_is_reported():
    metrics_path = Path(
        os.environ.get("EVAL_METRICS_PATH", "/tmp/eval_metrics.json")
    )
    if not metrics_path.exists():
        pytest.skip(f"Evaluation metrics not found at {metrics_path}.")

    with metrics_path.open(encoding="utf-8") as metrics_file:
        metrics = json.load(metrics_file)

    _warn_on_fairness_gap(metrics.get("loyalty_tier_slices"))