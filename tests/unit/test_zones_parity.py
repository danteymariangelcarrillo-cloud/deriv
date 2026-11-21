"""Paridad y determinismo de zonas de reacción (T024)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantlab.analytics.indicators import add_atr
from quantlab.analytics.zones import (
    BOOTSTRAP_SEED,
    cluster_levels,
    detect_reaction_zones,
    evaluate_zone,
    find_pivots,
    to_reaction_zones,
)
from quantlab.config import AnalyzerConfig
from quantlab.domain.zones import DEFAULT_P_NULL
from tests.unit._analytics_fixtures import make_candles


def reference_find_pivots(df: pd.DataFrame, side: str, window: int) -> list[tuple[int, float]]:
    """Implementación escalar independiente de ``_find_pivots`` (prototipo L709)."""
    column = "Low" if side == "support" else "High"
    values = df[column].to_numpy(dtype=float)
    pivots: list[tuple[int, float]] = []
    for i in range(window, len(df) - window):
        chunk = values[i - window : i + window + 1]
        centre = values[i]
        if side == "support":
            is_pivot = centre == np.nanmin(chunk) and int(np.nanargmin(chunk)) == window
        else:
            is_pivot = centre == np.nanmax(chunk) and int(np.nanargmax(chunk)) == window
        if is_pivot and np.isfinite(centre):
            pivots.append((i, float(centre)))
    return pivots


@pytest.mark.parametrize("side", ["support", "resistance"])
def test_find_pivots_matches_prototype(side: str) -> None:
    candles = add_atr(make_candles(400))

    result = find_pivots(candles, side, window=3)

    expected = reference_find_pivots(candles, side, 3)
    assert list(result["pivot_idx"]) == [index for index, _ in expected]
    assert list(result["price"]) == [price for _, price in expected]
    assert (result["confirmed_idx"] - result["pivot_idx"] == 3).all()


def test_cluster_levels_matches_prototype() -> None:
    pivots = pd.DataFrame({"price": [10.0, 10.2, 10.1, 12.0, 12.1, 20.0]})

    clusters = cluster_levels(pivots, tolerance=0.5)

    assert [len(cluster) for cluster in clusters] == [3, 2, 1]
    assert clusters[0][0]["price"] == 10.0
    assert cluster_levels(pd.DataFrame(), 0.5) == []


def test_evaluate_zone_counts_are_consistent() -> None:
    candles = add_atr(make_candles(800, seed=21))
    config = AnalyzerConfig()

    stats = evaluate_zone(
        candles,
        centre=float(candles["Close"].median()),
        half_width=float(candles["ATR"].median() * 0.5),
        zone_type="support",
        available_from_idx=50,
        event_prices=None,
        config=config,
    )

    assert stats["touches"] == (
        stats["evaluated_touches"] + stats["ambiguous_touches"] + stats["pending_or_uncensored"]
    )
    assert stats["evaluated_touches"] == stats["reactions"] + stats["failures"]
    if stats["evaluated_touches"]:
        expected_rate = 100.0 * stats["reactions"] / stats["evaluated_touches"]
        assert stats["reaction_rate_pct"] == pytest.approx(expected_rate, abs=1e-12)
    assert stats["p_null"] == DEFAULT_P_NULL
    assert stats["lower"] == pytest.approx(stats["price"] - stats["tolerance_points"])
    assert stats["upper"] == pytest.approx(stats["price"] + stats["tolerance_points"])


def test_detect_reaction_zones_is_deterministic() -> None:
    candles = make_candles(1200, seed=9)

    first = detect_reaction_zones(candles, AnalyzerConfig())
    second = detect_reaction_zones(candles, AnalyzerConfig())

    pd.testing.assert_frame_equal(first, second)
    assert first["pivot_count"].ge(AnalyzerConfig().min_zone_touches).all()
    assert first["touches"].ge(0).all()
    assert {
        "zone_type",
        "price",
        "tolerance_points",
        "reaction_rate_pct",
        "reaction_p_value_vs_random",
        "source",
    } <= set(first.columns)


def test_detect_reaction_zones_with_fdr_keeps_raw_pvalues() -> None:
    candles = make_candles(1200, seed=10)

    zones = detect_reaction_zones(candles, AnalyzerConfig(), fdr_alpha=0.05)

    finite = zones["reaction_p_value_vs_random"].notna()
    assert zones.loc[finite, "p_value_adj"].notna().all()
    assert (
        zones.loc[finite, "p_value_adj"] >= zones.loc[finite, "reaction_p_value_vs_random"] - 1e-12
    ).all()


def test_detect_reaction_zones_validates_arguments() -> None:
    candles = make_candles(100)

    with pytest.raises(ValueError, match="side"):
        detect_reaction_zones(candles, AnalyzerConfig(), side="diagonal")
    with pytest.raises(ValueError, match="min_touches"):
        detect_reaction_zones(candles, AnalyzerConfig(), min_touches=0)


def test_to_reaction_zones_builds_valid_domain_objects() -> None:
    candles = make_candles(1200, seed=12)
    zones = detect_reaction_zones(candles, AnalyzerConfig())

    converted = to_reaction_zones(zones, symbol="BOOM1000", timeframe="5min", stage="in_sample")

    assert len(converted) == len(zones)
    for zone in converted:
        assert zone.symbol == "BOOM1000"
        assert zone.timeframe == "5min"
        assert zone.stage == "in_sample"
        assert zone.lower < zone.center < zone.upper
        if zone.reaction_rate is not None:
            assert 0.0 <= zone.reaction_rate <= 1.0
        assert zone.p_null == DEFAULT_P_NULL


def test_bootstrap_seed_is_fixed() -> None:
    assert BOOTSTRAP_SEED == 42
