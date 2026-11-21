"""Paridad del ajuste GARCH(1,1) con ``arch`` (T025)."""

from __future__ import annotations

import numpy as np
import pytest
from arch import arch_model

from quantlab.analytics.volatility import fit_garch
from tests.unit._analytics_fixtures import make_garch_returns


def test_fit_garch_matches_direct_arch_fit() -> None:
    returns = make_garch_returns(1500, seed=4)

    result = fit_garch(returns, p=1, q=1)

    direct = arch_model(returns, vol="Garch", p=1, q=1, dist="normal").fit(
        update_freq=0, disp="off"
    )
    assert result["order"] == (1, 1)
    # ``fit_garch`` conserva los nombres de ``arch``; el contrato del snapshot
    # (alpha1/beta1) se aplica en ``AnalyticsEngine``.
    for name in ("omega", "alpha[1]", "beta[1]"):
        np.testing.assert_allclose(
            result["params"][name], float(direct.params[name]), rtol=0.0, atol=1e-8
        )
        np.testing.assert_allclose(
            result["p_values"][name], float(direct.pvalues[name]), rtol=0.0, atol=1e-10
        )
    np.testing.assert_allclose(result["log_likelihood"], float(direct.loglikelihood), atol=1e-6)
    np.testing.assert_allclose(result["aic"], float(direct.aic), atol=1e-6)
    np.testing.assert_allclose(result["bic"], float(direct.bic), atol=1e-6)
    assert result["n_obs"] == len(returns)
    assert result["converged"] is (direct.convergence_flag == 0)


def test_fit_garch_forecast_has_ten_positive_steps() -> None:
    result = fit_garch(make_garch_returns(1200, seed=6))

    forecast = result["forecast"]
    assert len(forecast) == 10
    assert [point["step"] for point in forecast] == list(range(1, 11))
    assert all(point["variance"] > 0 for point in forecast)
    assert all(point["sigma"] > 0 for point in forecast)
    assert all(point["ci_lower"] < point["mean"] < point["ci_upper"] for point in forecast)


def test_fit_garch_returns_error_without_sample() -> None:
    result = fit_garch(np.linspace(-1.0, 1.0, 99))

    assert "error" in result
    assert "100" in result["error"]


def test_fit_garch_ignores_non_finite_returns() -> None:
    returns = make_garch_returns(500, seed=8).copy()
    returns[[3, 50, 100]] = np.nan

    result = fit_garch(returns)

    assert result["n_obs"] == 497


def test_fit_garch_validates_order() -> None:
    with pytest.raises(ValueError):
        fit_garch(make_garch_returns(200), p=0)
    with pytest.raises(ValueError):
        fit_garch(make_garch_returns(200), q=0)


def test_fit_garch_is_deterministic() -> None:
    returns = make_garch_returns(800, seed=9)

    assert fit_garch(returns) == fit_garch(returns)
