"""Paridad de la validación walk-forward (T028)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats as scipy_stats
from statsmodels.stats.multitest import multipletests

from quantlab.analytics.walkforward import (
    WALK_FORWARD_WARNING,
    aggregate_walk_forward_results,
    walk_forward_result,
    walk_forward_validation,
)
from quantlab.config import AnalyzerConfig, GateConfig
from tests.unit._analytics_fixtures import make_candles


def sample_wf_table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fold": [1, 2, 3, 4, 5],
            "train_start_epoch": [0, 1000, 2000, 3000, 4000],
            "train_end_epoch": [900, 1900, 2900, 3900, 4900],
            "test_start_epoch": [901, 1901, 2901, 3901, 4901],
            "test_end_epoch": [1000, 2000, 3000, 4000, 5000],
            "bars_train": [900] * 5,
            "bars_test": [100] * 5,
            "zone_type": ["support", "resistance", "support", "resistance", "support"],
            "zone_price": [100.0, 101.0, 102.0, 103.0, 104.0],
            "zone_tolerance": [0.5] * 5,
            "train_touches": [5, 6, 7, 8, 9],
            "oos_evaluated": [10, 20, 30, 40, 50],
            "oos_reactions": [7, 10, 29, 20, 30],
            "oos_failures": [3, 10, 1, 20, 20],
            "oos_reaction_rate_pct": [70.0, 50.0, 96.66666666666667, 50.0, 60.0],
            "oos_reaction_ci_lower_pct": [62.0, 30.0, 83.0, 35.0, 45.0],
            "oos_reaction_ci_upper_pct": [78.0, 70.0, 99.0, 65.0, 75.0],
            "oos_p_value_vs_random": [0.01, 0.50, 0.001, 0.60, 0.04],
            "oos_median_favourable_atr": [1.2, 0.8, 1.5, 0.7, 1.0],
            "oos_median_adverse_atr": [0.5, 0.4, 0.3, 0.6, 0.5],
        }
    )


def test_aggregate_matches_prototype_formulas() -> None:
    table = sample_wf_table()

    result = aggregate_walk_forward_results(table)

    pvalues = table["oos_p_value_vs_random"].to_numpy()
    rejected, adjusted, _, _ = multipletests(pvalues, alpha=0.05, method="fdr_bh")
    weights = table["oos_evaluated"].to_numpy()
    weighted = float(np.average(table["oos_reaction_rate_pct"], weights=weights))
    positives = int((table["oos_reaction_rate_pct"] > 50).sum())
    sign_p = float(scipy_stats.binom.sf(positives - 1, len(table), 0.5))

    assert result["n_folds"] == 5
    assert result["n_significant_fdr"] == int(rejected.sum())
    np.testing.assert_allclose(result["weighted_mean_reaction_rate_pct"], weighted, atol=1e-10)
    np.testing.assert_allclose(
        result["median_reaction_rate_pct"],
        float(table["oos_reaction_rate_pct"].median()),
        atol=1e-10,
    )
    np.testing.assert_allclose(result["sign_test_p_value"], sign_p, atol=1e-12)
    np.testing.assert_allclose(
        [record["oos_p_value_adj"] for record in result["folds_summary"]],
        adjusted,
        atol=1e-12,
    )


def test_aggregate_handles_empty_and_all_nan_tables() -> None:
    assert "error" in aggregate_walk_forward_results(pd.DataFrame())

    table = sample_wf_table()
    table.loc[:, "oos_reaction_rate_pct"] = np.nan
    assert "error" in aggregate_walk_forward_results(table)


def test_walk_forward_validation_produces_clean_folds() -> None:
    candles = make_candles(6000, seed=31)

    table = walk_forward_validation(
        candles, AnalyzerConfig(), "5min", train_days=5, test_days=2, step_days=1
    )

    assert not table.empty
    assert (table["oos_evaluated"] >= 3).all()
    assert (table["test_start_epoch"] > table["train_end_epoch"]).all()
    assert table["test_start_epoch"].is_monotonic_increasing
    assert table["oos_reaction_rate_pct"].between(0.0, 100.0).all()
    assert set(table["zone_type"]) <= {"support", "resistance"}


def test_walk_forward_validation_requires_history() -> None:
    with pytest.raises(ValueError, match="Insuficientes"):
        walk_forward_validation(make_candles(100), AnalyzerConfig(), "5min", 5, 2, 1)
    with pytest.raises(ValueError, match="temporalidad"):
        walk_forward_validation(make_candles(1000), AnalyzerConfig(), "2min", 5, 2, 1)
    with pytest.raises(ValueError, match="deben ser"):
        walk_forward_validation(make_candles(1000), AnalyzerConfig(), "5min", 0, 2, 1)


def test_walk_forward_result_contract_and_warning() -> None:
    table = sample_wf_table()
    aggregate = aggregate_walk_forward_results(table)

    result = walk_forward_result(
        table,
        aggregate,
        timeframe="5min",
        train_days=5,
        test_days=2,
        step_days=1,
        gates=GateConfig(),
    )

    assert result["status"] == "ready"
    assert result["method"] == "anchored"
    assert result["warning"] == WALK_FORWARD_WARNING
    assert result["aggregate"]["independent"] is False
    assert len(result["folds"]) == len(table)
    assert result["config"]["min_folds"] == GateConfig().wf_min_folds
    for fold in result["folds"]:
        assert fold["test_start_epoch"] > fold["train_end_epoch"]
        assert fold["stage"] == "oos"
        assert 0.0 <= fold["reaction_rate"] <= 1.0


def test_walk_forward_result_sanitises_nan_values() -> None:
    table = sample_wf_table()
    table.loc[0, "oos_p_value_vs_random"] = np.nan
    table.loc[1, "oos_reaction_ci_lower_pct"] = np.nan
    aggregate = aggregate_walk_forward_results(table)

    result = walk_forward_result(
        table,
        aggregate,
        timeframe="5min",
        train_days=5,
        test_days=2,
        step_days=1,
        gates=GateConfig(),
    )

    assert result["folds"][0]["p_value_raw"] == 1.0
    assert result["folds"][1]["ci_lower"] == 0.0


def test_walk_forward_result_is_insufficient_without_folds() -> None:
    empty = pd.DataFrame()
    aggregate = {
        "n_folds": 0,
        "weighted_mean_reaction_rate_pct": 0.0,
        "sign_test_p_value": 1.0,
    }

    result = walk_forward_result(
        empty,
        aggregate,
        timeframe="5min",
        train_days=5,
        test_days=2,
        step_days=1,
        gates=GateConfig(),
    )

    assert result["status"] == "insufficient"
    assert result["folds"] == []
    assert result["recommendation"]
