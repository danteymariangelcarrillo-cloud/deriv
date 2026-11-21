"""Paridad de EVT (T022): MLE de la GPD y niveles de retorno.

El prototipo no se puede importar (depende de ``websocket-client``), así que la
referencia se calcula aquí con la misma fórmula y el mismo optimizador que
``fit_gpd_mle`` en ``legacy/boom_crash_quant_analyzer.py`` L119-L170.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import minimize

from quantlab.analytics.evt import fit_gpd_mle, gpd_return_level


def reference_gpd_mle(excesses: np.ndarray) -> tuple[float, float, float]:
    """Copia independiente del MLE del prototipo: (xi, sigma, loglik)."""
    values = np.asarray(excesses, dtype=float)
    values = values[values > 0]

    def negative_log_likelihood(params: np.ndarray) -> float:
        xi, sigma = params
        if sigma <= 0:
            return 1e10
        if xi == 0:
            return float(np.sum(np.log(sigma) + values / sigma))
        if np.any(1 + xi * values / sigma <= 0):
            return 1e10
        return float(np.sum(np.log(sigma) + (1 + 1 / xi) * np.log(1 + xi * values / sigma)))

    mean_excess = float(np.mean(values))
    variance = float(np.var(values, ddof=1))
    xi_init = 0.5 * (1 - mean_excess**2 / variance) if variance > 0 else 0.1
    sigma_init = mean_excess * (1 - xi_init)
    result = minimize(
        negative_log_likelihood,
        [xi_init, sigma_init],
        bounds=[(-0.99, 0.99), (1e-8, None)],
        method="L-BFGS-B",
    )
    return float(result.x[0]), float(result.x[1]), -float(result.fun)


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_fit_gpd_mle_matches_prototype(seed: int) -> None:
    rng = np.random.default_rng(seed)
    excesses = rng.normal(1.0, 0.6, 500).clip(1e-6)

    xi, sigma, diagnostics = fit_gpd_mle(excesses)
    expected_xi, expected_sigma, expected_loglik = reference_gpd_mle(excesses)

    np.testing.assert_allclose(xi, expected_xi, rtol=0.0, atol=1e-10)
    np.testing.assert_allclose(sigma, expected_sigma, rtol=0.0, atol=1e-10)
    np.testing.assert_allclose(diagnostics["log_likelihood"], expected_loglik, rtol=0.0, atol=1e-8)
    assert diagnostics["n_excesses"] == 500
    assert diagnostics["converged"] is True


def test_fit_gpd_mle_requires_ten_positive_excesses() -> None:
    xi, sigma, diagnostics = fit_gpd_mle(np.array([0.1] * 9))

    assert np.isnan(xi)
    assert np.isnan(sigma)
    assert "error" in diagnostics


def test_fit_gpd_mle_reports_errors_without_raising() -> None:
    # Varianza nula => arranque degenerado, pero no debe propagar excepción.
    xi, sigma, diagnostics = fit_gpd_mle(np.full(20, 2.0))

    assert "error" in diagnostics or (np.isfinite(xi) and np.isfinite(sigma))


@pytest.mark.parametrize("return_period", [10, 50, 100, 250])
def test_gpd_return_level_matches_closed_form(return_period: int) -> None:
    xi, sigma, threshold, n_obs, n_excesses = 0.2, 1.5, 2.0, 10_000, 500
    probability = n_excesses / n_obs

    result = gpd_return_level(xi, sigma, threshold, n_obs, n_excesses, return_period)

    expected = threshold + sigma / xi * ((return_period * probability) ** xi - 1.0)
    np.testing.assert_allclose(result, expected, rtol=0.0, atol=1e-12)


def test_gpd_return_level_exponential_case() -> None:
    result = gpd_return_level(0.0, 2.0, 1.0, 1000, 100, 50)

    expected = 1.0 + 2.0 * np.log(50 * 0.1)
    np.testing.assert_allclose(result, expected, rtol=0.0, atol=1e-12)


@pytest.mark.parametrize(("n_obs", "n_excesses"), [(0, 10), (100, 0), (-1, 5)])
def test_gpd_return_level_is_nan_without_sample(n_obs: int, n_excesses: int) -> None:
    assert np.isnan(gpd_return_level(0.2, 1.5, 2.0, n_obs, n_excesses, 100))
