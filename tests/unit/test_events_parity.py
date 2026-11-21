"""Paridad de detección de eventos y tabla condicional (T023)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantlab.analytics.events import (
    CANDLE_DOWN,
    CANDLE_UP,
    TICK_DOWN,
    TICK_UP,
    candle_events_series,
    conditional_event_table,
    conditional_result,
    detect_candle_extremes,
    detect_tick_extremes,
    tick_events_series,
)
from quantlab.analytics.indicators import add_atr
from quantlab.config import AnalyzerConfig
from tests.unit._analytics_fixtures import make_candles, make_ticks


def reference_candle_extremes(
    df: pd.DataFrame, config: AnalyzerConfig, direction: str
) -> pd.DataFrame:
    """Expresión del prototipo (L642-L675) sin look-ahead."""
    frame = add_atr(df)
    previous_close = frame["Close"].shift(1)
    if direction == "up":
        move_points = (frame["High"] - previous_close).clip(lower=0)
        event_price = frame["High"]
        event_type = CANDLE_UP
    else:
        move_points = (previous_close - frame["Low"]).clip(lower=0)
        event_price = frame["Low"]
        event_type = CANDLE_DOWN
    move_pct = move_points / previous_close.replace(0, np.nan) * 100.0
    min_periods = min(config.event_lookback_bars, max(30, config.event_lookback_bars // 4))
    threshold = (
        move_pct.shift(1)
        .rolling(config.event_lookback_bars, min_periods=min_periods)
        .quantile(config.candle_event_quantile)
    )
    threshold = threshold.replace([np.inf, -np.inf], np.nan)
    mask = (
        threshold.notna()
        & move_pct.notna()
        & (move_pct > 0)
        & (move_pct >= threshold)
        & (move_pct >= float(config.min_move_pct))
    )
    expected = frame.loc[mask].copy()
    expected["previous_close"] = previous_close.loc[mask]
    expected["directional_excursion_points"] = move_points.loc[mask]
    expected["directional_excursion_pct"] = move_pct.loc[mask]
    expected["threshold_pct_prior_only"] = threshold.loc[mask]
    expected["event_price"] = event_price.loc[mask]
    expected["event_type"] = event_type
    expected["move_in_atr"] = (move_points / frame["ATR"].shift(1).replace(0, np.nan)).loc[mask]
    return expected.reset_index(drop=True)


@pytest.mark.parametrize("direction", ["up", "down"])
def test_detect_candle_extremes_matches_prototype(direction: str) -> None:
    candles = make_candles(1200)
    config = AnalyzerConfig(event_lookback_bars=100, candle_event_quantile=0.95)

    result = detect_candle_extremes(candles, config, direction)  # type: ignore[arg-type]
    expected = reference_candle_extremes(candles, config, direction)

    assert len(result) == len(expected)
    np.testing.assert_allclose(
        result["directional_excursion_pct"], expected["directional_excursion_pct"], atol=1e-12
    )
    np.testing.assert_allclose(result["event_price"], expected["event_price"], atol=1e-12)
    assert set(result["event_type"]) <= {CANDLE_UP, CANDLE_DOWN}
    assert (result["directional_excursion_pct"] >= 0).all()


def test_detect_candle_extremes_uses_previous_bars_only() -> None:
    candles = make_candles(400, seed=8)
    config = AnalyzerConfig(event_lookback_bars=50, candle_event_quantile=0.9)

    baseline = detect_candle_extremes(candles, config, "up")
    mutated = candles.copy()
    # Alterar el futuro de la última vela no puede cambiar los eventos previos.
    mutated.loc[mutated.index[-1], ["High", "Close"]] *= 5.0
    after = detect_candle_extremes(mutated, config, "up")

    common = min(len(baseline), len(after))
    assert common >= 1
    np.testing.assert_allclose(
        baseline["event_price"].to_numpy()[: common - 1],
        after["event_price"].to_numpy()[: common - 1],
        atol=1e-12,
    )


def test_detect_candle_extremes_validates_configuration() -> None:
    with pytest.raises(ValueError):
        detect_candle_extremes(make_candles(50), AnalyzerConfig(), "up", lookback=5)
    with pytest.raises(ValueError):
        detect_candle_extremes(make_candles(50), AnalyzerConfig(), "up", quantile=1.0)


@pytest.mark.parametrize("direction", ["up", "down"])
def test_detect_tick_extremes_matches_prototype(direction: str) -> None:
    ticks = make_ticks(6000)
    config = AnalyzerConfig(tick_event_quantile=0.99, tick_event_lookback=500)

    result = detect_tick_extremes(ticks, config, direction)  # type: ignore[arg-type]

    frame = ticks.sort_values("epoch").reset_index(drop=True)
    returns = frame["price"].pct_change() * 100.0
    directional = returns.clip(lower=0) if direction == "up" else (-returns).clip(lower=0)
    threshold = directional.shift(1).rolling(500, min_periods=300).quantile(0.99)
    mask = threshold.notna() & directional.gt(0) & directional.ge(threshold)

    assert len(result) == int(mask.sum())
    np.testing.assert_allclose(
        result["directional_move_pct"].to_numpy(),
        directional.loc[mask].to_numpy(),
        atol=1e-12,
    )
    expected_type = TICK_UP if direction == "up" else TICK_DOWN
    assert set(result["event_type"]) <= {expected_type}


def test_detect_tick_extremes_rejects_short_lookback() -> None:
    with pytest.raises(ValueError):
        detect_tick_extremes(make_ticks(500), AnalyzerConfig(), "up", lookback=100)


def test_conditional_event_table_matches_prototype() -> None:
    ticks = make_ticks(3000)
    config = AnalyzerConfig(tick_event_quantile=0.99, tick_event_lookback=500)
    events = detect_tick_extremes(ticks, config, "up")

    result = conditional_event_table(ticks, events, horizon=50, bin_size=100, max_ticks=5000)

    # Referencia literal del prototipo (L1230-L1259).
    frame = ticks.sort_values("epoch").reset_index(drop=True)
    flags = np.zeros(len(frame), dtype=np.int8)
    markers = events["tick_index"].astype(int).to_numpy()
    markers = markers[(markers >= 0) & (markers < len(frame))]
    flags[markers] = 1
    last_event = -1
    since = np.full(len(frame), -1, dtype=int)
    for i, flag in enumerate(flags):
        since[i] = i - last_event if last_event >= 0 else -1
        if flag:
            last_event = i
    future = (
        pd.Series(flags).shift(-1).iloc[::-1].rolling(50, min_periods=50).max().iloc[::-1]
    ).to_numpy()
    valid = (since >= 0) & (since <= 5000) & np.isfinite(future)
    tmp = pd.DataFrame(
        {
            "ticks_since_event_bin": (since[valid] // 100) * 100,
            "event_in_next_horizon": future[valid].astype(int),
        }
    )
    expected = (
        tmp.groupby("ticks_since_event_bin")["event_in_next_horizon"]
        .agg(observations="size", future_events="sum")
        .reset_index()
    )
    expected["empirical_probability_pct"] = (
        100 * expected["future_events"] / expected["observations"]
    )

    pd.testing.assert_frame_equal(
        result[
            ["ticks_since_event_bin", "observations", "future_events", "empirical_probability_pct"]
        ],
        expected,
    )
    assert (result["horizon_ticks"] == 50).all()
    assert (result["interpretation"].str.startswith("Descriptivo in-sample")).all()


def test_candle_events_series_converts_to_domain() -> None:
    series = candle_events_series(
        make_candles(800), AnalyzerConfig(), "up", symbol="BOOM1000", timeframe="5min"
    )

    assert series.n_events == len(series.events)
    assert series.threshold_quantile == AnalyzerConfig().candle_event_quantile
    assert series.events[0].kind == "candle"
    assert series.events[0].symbol == "BOOM1000"
    assert series.threshold_summary.min is not None


def test_tick_events_series_converts_to_domain() -> None:
    series = tick_events_series(
        make_ticks(3000), AnalyzerConfig(), "down", symbol="CRASH500", timeframe="5min"
    )

    assert all(event.kind == "tick" for event in series.events)
    assert all(event.direction == "down" for event in series.events)
    if series.events:
        assert series.status == "ready"


def test_conditional_result_matches_table() -> None:
    ticks = make_ticks(3000)
    events = detect_tick_extremes(ticks, AnalyzerConfig(), "up")
    table = conditional_event_table(ticks, events, horizon=50, bin_size=100)

    result = conditional_result(table, horizon=50, bin_size=100)

    assert len(result.bins) == len(table)
    for row, item in zip(table.itertuples(index=False), result.bins, strict=True):
        assert item.bin_start == row.ticks_since_event_bin
        assert item.probability == pytest.approx(row.future_events / row.observations, abs=1e-12)
        assert item.ci_lower <= item.probability + 1e-12
        assert item.probability <= item.ci_upper + 1e-12
