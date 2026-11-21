"""Paridad de los indicadores con el prototipo y con cálculo directo (T018)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quantlab.analytics.indicators import (
    add_atr,
    directional_returns,
    log_returns,
    rolling_quantile,
)


def sample_candles(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1000.0 + np.cumsum(rng.normal(0.0, 0.5, n))
    open_ = close + rng.normal(0.0, 0.2, n)
    high = np.maximum(open_, close) + rng.uniform(0.0, 0.4, n)
    low = np.minimum(open_, close) - rng.uniform(0.0, 0.4, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close})


def reference_atr(df: pd.DataFrame, period: int) -> list[float]:
    """Cálculo escalar independiente del ``add_atr`` del prototipo."""
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)

    true_ranges: list[float] = []
    for index in range(len(df)):
        candidates = [highs[index] - lows[index]]
        if index > 0:
            previous = closes[index - 1]
            candidates.append(abs(highs[index] - previous))
            candidates.append(abs(lows[index] - previous))
        true_ranges.append(max(candidates))

    atr: list[float] = []
    for index in range(len(true_ranges)):
        if index < period - 1:
            atr.append(math.nan)
        else:
            window = true_ranges[index - period + 1 : index + 1]
            atr.append(sum(window) / period)
    return atr


# ----------------------------------- ATR -----------------------------------


def test_add_atr_matches_prototype_within_1e10() -> None:
    candles = sample_candles()

    result = add_atr(candles)

    expected = reference_atr(candles, 14)
    np.testing.assert_allclose(
        result["ATR"].to_numpy(dtype=float),
        np.asarray(expected, dtype=float),
        rtol=0.0,
        atol=1e-10,
        equal_nan=True,
    )


@pytest.mark.parametrize("period", [1, 2, 5, 14, 20])
def test_add_atr_honours_period_and_min_periods(period: int) -> None:
    candles = sample_candles(60)

    result = add_atr(candles, period=period)

    assert result["ATR"].isna().sum() == period - 1
    np.testing.assert_allclose(
        result["ATR"].to_numpy(dtype=float),
        np.asarray(reference_atr(candles, period), dtype=float),
        rtol=0.0,
        atol=1e-10,
        equal_nan=True,
    )


def test_add_atr_does_not_mutate_input_and_preserves_columns() -> None:
    candles = sample_candles(30)
    original = candles.copy(deep=True)

    result = add_atr(candles)

    pd.testing.assert_frame_equal(candles, original)
    assert list(result.columns) == [*candles.columns, "ATR"]
    assert result["ATR"].dtype == np.float64
    assert math.isnan(result["ATR"].iloc[0])


def test_add_atr_requires_ohlc_columns() -> None:
    with pytest.raises(KeyError):
        add_atr(pd.DataFrame({"close": [1.0, 2.0]}))


# ------------------------------- log returns -------------------------------


def test_log_returns_match_numpy_log_diff() -> None:
    prices = pd.Series([100.0, 101.0, 99.5, 100.25])

    result = log_returns(prices)

    expected = np.log(prices).diff()
    pd.testing.assert_series_equal(result, expected)
    assert math.isnan(result.iloc[0])
    np.testing.assert_allclose(
        result.iloc[1:].to_numpy(),
        np.asarray([np.log(101 / 100), np.log(99.5 / 101), np.log(100.25 / 99.5)]),
        atol=1e-10,
    )


def test_log_returns_accept_arrays_and_keep_index_alignment() -> None:
    series = pd.Series([10.0, 20.0, 40.0], index=[5, 6, 7])

    from_array = log_returns(np.array([10.0, 20.0, 40.0]))
    from_series = log_returns(series)

    np.testing.assert_allclose(from_array.iloc[1:].to_numpy(), np.log([2.0, 2.0]))
    assert list(from_series.index) == [5, 6, 7]
    assert math.isnan(from_series.iloc[0])


# ---------------------------- directional returns ----------------------------


def test_directional_returns_up_clips_negatives() -> None:
    returns = pd.Series([2.0, -1.5, 0.0, 3.25])

    result = directional_returns(returns, "up")

    pd.testing.assert_series_equal(result, pd.Series([2.0, 0.0, 0.0, 3.25]))
    pd.testing.assert_series_equal(returns, pd.Series([2.0, -1.5, 0.0, 3.25]))


def test_directional_returns_down_inverts_and_clips() -> None:
    returns = pd.Series([2.0, -1.5, 0.0, -3.25])

    result = directional_returns(returns, "down")

    pd.testing.assert_series_equal(result, pd.Series([0.0, 1.5, 0.0, 3.25]))


def test_directional_returns_matches_prototype_expression() -> None:
    returns = pd.Series(np.random.default_rng(3).normal(0.0, 1.0, 50))

    up = directional_returns(returns, "up")
    down = directional_returns(returns, "down")

    pd.testing.assert_series_equal(up, returns.clip(lower=0))
    pd.testing.assert_series_equal(down, (-returns).clip(lower=0))
    np.testing.assert_allclose(up.to_numpy() + down.to_numpy(), returns.abs().to_numpy())


def test_directional_returns_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        directional_returns(pd.Series([1.0]), "sideways")  # type: ignore[arg-type]


# ----------------------------- rolling quantile -----------------------------


def test_rolling_quantile_excludes_current_bar() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    series = pd.Series(values)

    result = rolling_quantile(series, window=3, quantile=0.5, min_periods=1)

    # La barra 4 (100.0) no debe influir en su propio umbral: la ventana
    # disponible es sólo [2.0, 3.0, 4.0].
    expected_previous = pd.Series([2.0, 3.0, 4.0]).quantile(0.5)
    assert result.iloc[4] == pytest.approx(expected_previous)
    assert result.iloc[4] < series.iloc[4]
    assert math.isnan(result.iloc[0])
    assert result.iloc[1] == pytest.approx(1.0)


def test_rolling_quantile_matches_shift_rolling_expression() -> None:
    series = pd.Series(np.linspace(0.0, 5.0, 40) ** 2)
    window, min_periods, quantile = 10, 4, 0.9

    result = rolling_quantile(series, window, quantile, min_periods)

    expected = series.shift(1).rolling(window, min_periods=min_periods).quantile(quantile)
    pd.testing.assert_series_equal(result, expected)
    assert result.isna().sum() == min_periods


def test_rolling_quantile_has_no_future_leakage() -> None:
    series = pd.Series(np.arange(20, dtype=float))

    thresholds = rolling_quantile(series, window=5, quantile=1.0, min_periods=1)

    for index in range(1, len(series)):
        assert thresholds.iloc[index] <= series.iloc[index - 1]


@pytest.mark.parametrize(
    ("window", "quantile", "min_periods"),
    [(0, 0.9, 1), (10, 0.0, 1), (10, 1.5, 1), (10, 0.9, 0), (10, 0.9, 11)],
)
def test_rolling_quantile_validates_arguments(
    window: int, quantile: float, min_periods: int
) -> None:
    with pytest.raises(ValueError):
        rolling_quantile(pd.Series([1.0, 2.0, 3.0]), window, quantile, min_periods)
