"""Tests de regímenes de volatilidad (T025): CUSUM y Markov-switching."""

from __future__ import annotations

import numpy as np
import pytest

from quantlab.analytics.volatility import detect_volatility_regimes
from tests.unit._analytics_fixtures import make_regime_returns


def test_cusum_regimes_detects_high_volatility_second_half() -> None:
    returns = make_regime_returns(3000, seed=13)

    result = detect_volatility_regimes(returns, method="cusum", max_points=2000)

    assert result["status"] == "ready"
    assert result["method"] == "cusum"
    assert result["n_obs"] == len(returns) - 19
    assert 0.0 <= result["high_vol_prob_current"] <= 1.0
    assert len(result["series"]) <= 2000
    assert all(0.0 <= point["prob_high"] <= 1.0 for point in result["series"])
    # La segunda mitad tiene varianza triple: el régimen dominante debe ser alto.
    second_half = [
        point
        for point in result["series"]
        if point["epoch"] >= result["series"][len(result["series"]) // 2]["epoch"]
    ]
    assert np.mean([point["prob_high"] for point in second_half]) > 0.5


def test_markov_regimes_returns_two_state_series() -> None:
    returns = make_regime_returns(900, seed=14)

    result = detect_volatility_regimes(returns, method="markov_switching", max_points=5000)

    assert result["status"] == "ready"
    assert result["method"] in {"markov_switching", "cusum"}
    assert len(result["series"]) == result["n_obs"]
    assert len(result["segments"]) >= 1
    for segment in result["segments"]:
        assert segment["start_epoch"] <= segment["end_epoch"]
        assert segment["regime"] in {0, 1}
        assert np.isfinite(segment["mean_variance"])
    if result["method"] == "cusum":
        assert result["message"]


def test_regimes_series_is_ordered_and_segments_cover_sample() -> None:
    returns = make_regime_returns(1200, seed=15)

    result = detect_volatility_regimes(returns, method="cusum")

    epochs = [point["epoch"] for point in result["series"]]
    assert epochs == sorted(epochs)
    assert result["segments"][0]["start_epoch"] == epochs[0]
    assert result["segments"][-1]["end_epoch"] == epochs[-1]
    for previous, current in zip(result["segments"], result["segments"][1:], strict=False):
        assert current["start_epoch"] > previous["end_epoch"]


def test_regimes_needs_enough_observations() -> None:
    result = detect_volatility_regimes(np.arange(10.0), method="cusum")

    assert result["status"] == "insufficient"
    assert result["n_obs"] == 0
    assert result["series"] == []


def test_regimes_validates_arguments() -> None:
    with pytest.raises(ValueError, match="2 regímenes"):
        detect_volatility_regimes(np.arange(100.0), n_regimes=3)
    with pytest.raises(ValueError, match="method"):
        detect_volatility_regimes(np.arange(100.0), method="garch")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="misma longitud"):
        detect_volatility_regimes(np.arange(100.0), epochs=np.arange(50))


def test_regimes_are_deterministic() -> None:
    returns = make_regime_returns(700, seed=16)

    assert detect_volatility_regimes(returns, method="cusum") == detect_volatility_regimes(
        returns, method="cusum"
    )
