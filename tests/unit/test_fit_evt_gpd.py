"""Tests de ``fit_evt_gpd`` completa (T022/T030)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from quantlab.analytics.evt import fit_evt_gpd


def gpd_returns(n: int = 4000, *, c: float = 0.2, scale: float = 1.5, seed: int = 3) -> np.ndarray:
    """Excesos GPD desplazados para que el umbral caiga en la cola."""
    rng = np.random.default_rng(seed)
    return 5.0 + stats.genpareto.rvs(c, loc=0.0, scale=scale, size=n, random_state=rng)


def test_fit_evt_gpd_recovers_shape_and_scale() -> None:
    result = fit_evt_gpd(gpd_returns(), threshold_quantile=0.95, min_excesses=30)

    assert result["status"] == "ready"
    assert result["n_excesses"] >= 30
    assert result["xi"] == pytest.approx(0.2, abs=0.12)
    # La GPD es "memoryless": al subir el umbral, la escala de los excesos es
    # sigma + xi * u. El prototipo no reajusta sigma a la baja, así que se
    # comprueba contra esa relación y no contra el sigma original.
    expected_sigma = 1.5 + 0.2 * (result["threshold"] - 5.0)
    assert result["sigma"] == pytest.approx(expected_sigma, rel=0.20)
    assert result["converged"] is True
    assert 0.0 <= result["ks_p"] <= 1.0
    assert np.isfinite(result["ks_stat"])


def test_fit_evt_gpd_threshold_is_quantile_of_positive_returns() -> None:
    returns = gpd_returns()

    result = fit_evt_gpd(returns, threshold_quantile=0.9, min_excesses=30)

    positive = returns[returns > 0]
    np.testing.assert_allclose(
        result["threshold"], float(np.quantile(positive, 0.9)), rtol=0.0, atol=1e-12
    )


def test_fit_evt_gpd_return_levels_are_increasing() -> None:
    result = fit_evt_gpd(gpd_returns(), min_excesses=30)

    levels = [result["return_levels"][key] for key in ("10", "50", "100", "250")]
    assert all(np.isfinite(level) for level in levels)
    assert levels == sorted(levels)
    # Con 10 periodos la probabilidad (10 * n_excesses/n_obs) puede ser < 1, en
    # cuyo caso la fórmula del prototipo devuelve un nivel bajo el umbral.
    assert all(
        level > result["threshold"]
        for key, level in zip(("10", "50", "100", "250"), levels, strict=True)
        if 50 <= int(key)
    )


def test_fit_evt_gpd_mean_excess_plot_is_finite_and_ordered() -> None:
    result = fit_evt_gpd(gpd_returns(), min_excesses=30)

    plot = result["mean_excess_plot"]
    assert len(plot["thresholds"]) == len(plot["mean_excess"]) == len(plot["n_excess"])
    assert plot["thresholds"] == sorted(plot["thresholds"])
    assert all(np.isfinite(value) for value in plot["mean_excess"])
    assert all(value > 0 for value in plot["n_excess"])


def test_fit_evt_gpd_insufficient_when_too_few_excesses() -> None:
    result = fit_evt_gpd(np.linspace(0.0, 1.0, 200), min_excesses=1000)

    assert result["status"] == "insufficient"
    assert result["xi"] is None
    assert result["return_levels"] == {}
    assert "insuficientes" in result["message"]


def test_fit_evt_gpd_directional_filters_direction() -> None:
    returns = np.array([-3.0, -2.0, 1.0, 2.0, 3.0, 4.0] * 50, dtype=float)

    up = fit_evt_gpd(returns, direction="up", min_excesses=1000)
    down = fit_evt_gpd(returns, direction="down", min_excesses=1000)

    assert up["threshold"] > 0
    assert down["threshold"] > 0
    assert up["n_excesses"] == down["n_excesses"]


def test_fit_evt_gpd_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="threshold_quantile"):
        fit_evt_gpd(np.arange(10.0), threshold_quantile=1.5)
    with pytest.raises(ValueError, match="direction"):
        fit_evt_gpd(np.arange(10.0), direction="sideways")


def test_fit_evt_gpd_is_deterministic() -> None:
    returns = gpd_returns()

    first = fit_evt_gpd(returns)
    second = fit_evt_gpd(returns)

    assert first == second
