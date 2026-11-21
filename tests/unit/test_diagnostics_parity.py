"""Paridad de diagnósticos y autocorrelación (T027)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.tsa.stattools import adfuller

from quantlab.analytics.diagnostics import (
    autocorrelation_result,
    residual_diagnostics,
)
from tests.unit._analytics_fixtures import make_returns


def test_residual_diagnostics_matches_statsmodels_and_scipy() -> None:
    returns = make_returns(800, seed=3)

    result = residual_diagnostics(returns, max_lag=20)

    box = acorr_ljungbox(returns, lags=[20], return_df=True)
    np.testing.assert_allclose(
        result["ljung_box"]["stat"], float(box["lb_stat"].iloc[0]), atol=1e-10
    )
    np.testing.assert_allclose(
        result["ljung_box"]["p"], float(box["lb_pvalue"].iloc[0]), atol=1e-12
    )

    jb_stat, jb_p = stats.jarque_bera(returns)
    np.testing.assert_allclose(result["jarque_bera"]["stat"], float(jb_stat), atol=1e-10)
    np.testing.assert_allclose(result["jarque_bera"]["p"], float(jb_p), atol=1e-12)

    arch_stat, arch_p, _, _ = het_arch(returns, nlags=20)
    np.testing.assert_allclose(result["arch_lm"]["stat"], float(arch_stat), atol=1e-10)
    np.testing.assert_allclose(result["arch_lm"]["p"], float(arch_p), atol=1e-12)
    assert result["arch_lm"]["lags"] == 20

    adf = adfuller(returns, maxlag=20, autolag="AIC", result_object=False)
    np.testing.assert_allclose(result["adf"]["stat"], float(adf[0]), atol=1e-10)
    np.testing.assert_allclose(result["adf"]["p"], float(adf[1]), atol=1e-12)
    assert result["adf"]["lags"] == int(adf[2])
    for key, value in adf[4].items():
        np.testing.assert_allclose(result["adf"]["critical_values"][key], float(value), atol=1e-12)


def test_residual_diagnostics_reports_insufficient_sample() -> None:
    result = residual_diagnostics(np.arange(10.0), max_lag=20)

    assert result == {"error": "Insuficientes datos para diagnosticos."}


def test_residual_diagnostics_cleans_non_finite_values() -> None:
    returns = make_returns(500, seed=4).copy()
    returns[[0, 10, 20]] = np.nan
    returns[30] = np.inf

    result = residual_diagnostics(returns)

    assert result["n_observations"] == 496
    assert "stat" in result["jarque_bera"]


def test_residual_diagnostics_validates_lag() -> None:
    with pytest.raises(ValueError):
        residual_diagnostics(np.arange(100.0), max_lag=0)


def test_autocorrelation_matches_pandas_autocorr() -> None:
    returns = make_returns(1000, seed=5)

    result = autocorrelation_result(returns, max_lag=50)

    series = pd.Series(returns)
    expected_returns = [series.autocorr(lag) for lag in range(1, 51)]
    expected_squared = [series.pow(2).autocorr(lag) for lag in range(1, 51)]

    np.testing.assert_allclose(result["acf_returns"], expected_returns, atol=1e-12)
    np.testing.assert_allclose(result["acf_squared"], expected_squared, atol=1e-12)
    np.testing.assert_allclose(result["conf_band"], 1.96 / np.sqrt(len(returns)), atol=1e-12)
    assert result["n_obs"] == 1000
    assert result["max_lag"] == 50
    box = acorr_ljungbox(returns, lags=[10, 20], return_df=True)
    np.testing.assert_allclose(
        result["ljung_box"]["lag_10_p"], float(box["lb_pvalue"].iloc[0]), atol=1e-12
    )
    np.testing.assert_allclose(
        result["ljung_box"]["lag_20_p"], float(box["lb_pvalue"].iloc[1]), atol=1e-12
    )


def test_autocorrelation_rejects_short_series_and_bad_lag() -> None:
    assert "error" in autocorrelation_result(make_returns(20, seed=1), max_lag=50)
    with pytest.raises(ValueError):
        autocorrelation_result(make_returns(200), max_lag=0)
    with pytest.raises(ValueError):
        autocorrelation_result(make_returns(200), max_lag=101)


def test_diagnostics_are_deterministic() -> None:
    returns = make_returns(600, seed=6)

    assert residual_diagnostics(returns) == residual_diagnostics(returns)
    assert autocorrelation_result(returns) == autocorrelation_result(returns)
