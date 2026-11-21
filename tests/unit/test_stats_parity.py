"""Paridad de las funciones estadísticas con el prototipo y con SciPy (T021)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats as scipy_stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

from quantlab.analytics.stats import (
    benjamini_hochberg_fdr,
    binomial_test_pvalue,
    bootstrap_ci,
    wilson_score_interval,
)

# ---------------------------------- Wilson ---------------------------------


@pytest.mark.parametrize(
    ("successes", "trials"),
    [(0, 10), (1, 3), (5, 10), (9, 10), (10, 10), (999, 1000), (0, 1)],
)
@pytest.mark.parametrize("alpha", [0.05, 0.01, 0.10])
def test_wilson_matches_statsmodels(successes: int, trials: int, alpha: float) -> None:
    low, high = wilson_score_interval(successes, trials, alpha=alpha)

    expected_low, expected_high = proportion_confint(
        successes, trials, alpha=alpha, method="wilson"
    )

    np.testing.assert_allclose(low, expected_low, rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(high, expected_high, rtol=0.0, atol=1e-12)


@pytest.mark.parametrize("trials", [0, -1, -50])
def test_wilson_returns_nan_without_trials(trials: int) -> None:
    low, high = wilson_score_interval(3, trials)

    assert np.isnan(low)
    assert np.isnan(high)


@pytest.mark.parametrize("successes", [0, 10])
def test_wilson_is_clamped_to_unit_interval(successes: int) -> None:
    low, high = wilson_score_interval(successes, 10)

    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert low < high


def test_wilson_clamps_at_boundaries() -> None:
    assert wilson_score_interval(0, 10)[0] == 0.0
    assert wilson_score_interval(10, 10)[1] == pytest.approx(1.0, abs=1e-12)


# --------------------------------- Binomial --------------------------------


@pytest.mark.parametrize(
    ("successes", "trials", "p_null"),
    [(7, 10, 0.5), (0, 10, 0.5), (10, 10, 0.5), (3, 20, 0.1), (18, 20, 0.9)],
)
def test_binomial_greater_matches_manual_pmf_sum(
    successes: int, trials: int, p_null: float
) -> None:
    pvalue = binomial_test_pvalue(successes, trials, p_null, alternative="greater")

    expected = scipy_stats.binom.sf(successes - 1, trials, p_null)
    np.testing.assert_allclose(pvalue, expected, rtol=0.0, atol=1e-12)


@pytest.mark.parametrize(
    ("successes", "trials", "p_null"),
    [(7, 10, 0.5), (0, 10, 0.5), (10, 10, 0.5), (3, 20, 0.1), (18, 20, 0.9)],
)
def test_binomial_less_matches_manual_pmf_sum(successes: int, trials: int, p_null: float) -> None:
    pvalue = binomial_test_pvalue(successes, trials, p_null, alternative="less")

    expected = scipy_stats.binom.cdf(successes, trials, p_null)
    np.testing.assert_allclose(pvalue, expected, rtol=0.0, atol=1e-12)


def test_binomial_two_sided_is_symmetric_for_half_null() -> None:
    left = binomial_test_pvalue(3, 10, 0.5, alternative="two-sided")
    right = binomial_test_pvalue(7, 10, 0.5, alternative="two-sided")

    np.testing.assert_allclose(left, right, rtol=0.0, atol=1e-12)
    assert 0.0 <= left <= 1.0


def test_binomial_unknown_alternative_falls_back_to_greater() -> None:
    fallback = binomial_test_pvalue(7, 10, 0.5, alternative="dos-colas")
    greater = binomial_test_pvalue(7, 10, 0.5, alternative="greater")

    assert fallback == greater


@pytest.mark.parametrize("trials", [0, -3])
def test_binomial_returns_nan_without_trials(trials: int) -> None:
    assert np.isnan(binomial_test_pvalue(2, trials, 0.5))


# ------------------------------ Benjamini-Hochberg --------------------------


def test_benjamini_hochberg_matches_statsmodels() -> None:
    pvalues = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205])

    rejected, adjusted = benjamini_hochberg_fdr(pvalues, alpha=0.05)

    expected_rejected, expected_adjusted, _, _ = multipletests(pvalues, alpha=0.05, method="fdr_bh")
    assert np.array_equal(rejected, expected_rejected)
    np.testing.assert_allclose(adjusted, expected_adjusted, rtol=0.0, atol=1e-12)


def test_benjamini_hochberg_preserves_input_order_when_unsorted() -> None:
    pvalues = np.array([0.50, 0.01, 0.20, 0.03, 0.04])

    rejected, adjusted = benjamini_hochberg_fdr(pvalues, alpha=0.05)

    expected_rejected, expected_adjusted, _, _ = multipletests(pvalues, alpha=0.05, method="fdr_bh")
    assert np.array_equal(rejected, expected_rejected)
    np.testing.assert_allclose(adjusted, expected_adjusted, rtol=0.0, atol=1e-12)


def test_benjamini_hochberg_rejects_nothing_for_high_pvalues() -> None:
    rejected, adjusted = benjamini_hochberg_fdr(np.array([0.9, 0.8, 0.7]))

    assert not rejected.any()
    assert np.all(adjusted <= 1.0)
    assert np.all(adjusted >= np.array([0.9, 0.8, 0.7]) - 1e-12)


def test_benjamini_hochberg_rejects_all_for_tiny_pvalues() -> None:
    rejected, _ = benjamini_hochberg_fdr(np.array([1e-6, 2e-6, 3e-6]), alpha=0.05)

    assert rejected.all()


def test_benjamini_hochberg_empty_input() -> None:
    rejected, adjusted = benjamini_hochberg_fdr(np.array([]))

    assert rejected.dtype == bool
    assert adjusted.dtype == float
    assert rejected.size == 0
    assert adjusted.size == 0


def test_benjamini_hochberg_adjusted_is_monotone_in_sorted_order() -> None:
    pvalues = np.array([0.02, 0.004, 0.3, 0.0001, 0.04])
    _, adjusted = benjamini_hochberg_fdr(pvalues)

    order = np.argsort(pvalues)
    assert np.all(np.diff(adjusted[order]) >= -1e-12)


# --------------------------------- Bootstrap --------------------------------


def test_bootstrap_is_deterministic_with_same_seed() -> None:
    data = np.random.default_rng(3).normal(0.0, 1.0, 200)

    first = bootstrap_ci(data, np.mean, n_bootstrap=200, seed=11)
    second = bootstrap_ci(data, np.mean, n_bootstrap=200, seed=11)

    assert first == second


def test_bootstrap_changes_with_different_seed() -> None:
    data = np.random.default_rng(4).normal(0.0, 1.0, 200)

    first = bootstrap_ci(data, np.mean, n_bootstrap=200, seed=1)
    second = bootstrap_ci(data, np.mean, n_bootstrap=200, seed=2)

    assert first != second


def test_bootstrap_does_not_touch_global_rng() -> None:
    np.random.seed(123)
    before = np.random.get_state()[1].copy()

    bootstrap_ci(np.arange(50.0), np.mean, n_bootstrap=50, seed=7)

    after = np.random.get_state()[1]
    assert np.array_equal(before, after)


def test_bootstrap_interval_brackets_sample_mean() -> None:
    data = np.random.default_rng(5).normal(10.0, 2.0, 300)

    low, high = bootstrap_ci(data, np.mean, n_bootstrap=500, seed=42)

    assert low < float(np.mean(data)) < high
    assert low < high


def test_bootstrap_of_constant_data_is_degenerate() -> None:
    low, high = bootstrap_ci(np.full(20, 7.0), np.mean, n_bootstrap=100, seed=0)

    assert low == high == 7.0


def test_bootstrap_returns_nan_without_data() -> None:
    low, high = bootstrap_ci(np.array([]), np.mean)

    assert np.isnan(low)
    assert np.isnan(high)


def test_bootstrap_returns_nan_with_too_few_finite_replicas() -> None:
    low, high = bootstrap_ci(np.arange(10.0), np.mean, n_bootstrap=5)

    assert np.isnan(low)
    assert np.isnan(high)


def test_bootstrap_discards_failing_replicas() -> None:
    def fragile(sample: np.ndarray) -> float:
        if sample.max() > 5:
            raise ValueError("réplica no válida")
        return 1.0

    # Las remuestras que incluyen el 10 fallan; las válidas valen 1.0, así que
    # un IC degenerado demuestra que las réplicas inválidas se descartaron.
    low, high = bootstrap_ci(np.array([0.0, 1.0, 2.0, 10.0]), fragile, n_bootstrap=200, seed=0)

    assert low == high == pytest.approx(1.0)


def test_bootstrap_works_with_pandas_series() -> None:
    import pandas as pd

    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])

    low, high = bootstrap_ci(series, np.median, n_bootstrap=100, seed=3)

    assert low <= 3.0 <= high
