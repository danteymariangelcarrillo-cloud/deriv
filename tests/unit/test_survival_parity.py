"""Paridad de Kaplan-Meier (T026)."""

from __future__ import annotations

import numpy as np
import pytest

from quantlab.analytics.survival import (
    event_index_kaplan_meier,
    kaplan_meier,
    ks_exponential,
    survival_result,
    time_kaplan_meier,
)


def reference_kaplan_meier(
    durations: np.ndarray, censored: np.ndarray
) -> tuple[list[float], list[float], list[int]]:
    """Product-limit escalar, independiente de la implementación migrada."""
    times = [0.0]
    curve = [1.0]
    at_risk_values = [len(durations)]
    survival = 1.0
    for value in sorted(np.unique(durations)):
        at_risk = int(np.sum(durations >= value))
        events = int(np.sum((durations == value) & ~censored))
        if at_risk and events:
            survival *= 1.0 - events / at_risk
        times.append(float(value))
        curve.append(float(survival))
        at_risk_values.append(at_risk)
    return times, curve, at_risk_values


def test_kaplan_meier_matches_reference() -> None:
    durations = np.array([2.0, 4.0, 4.0, 6.0, 8.0, 10.0])
    censored = np.array([False, False, True, False, True, False])

    times, curve, lower, upper, at_risk = kaplan_meier(durations, censored)
    expected_times, expected_curve, expected_at_risk = reference_kaplan_meier(durations, censored)

    np.testing.assert_allclose(times, expected_times, atol=1e-12)
    np.testing.assert_allclose(curve, expected_curve, atol=1e-12)
    np.testing.assert_array_equal(at_risk, expected_at_risk)
    assert curve[0] == 1.0 and times[0] == 0.0
    assert np.all(np.diff(curve) <= 1e-12)
    assert np.all(lower <= curve + 1e-12)
    assert np.all(upper >= curve - 1e-12)


def test_kaplan_meier_without_censoring_is_complement_of_ecdf() -> None:
    durations = np.arange(1.0, 11.0)

    _, curve, _, _, _ = kaplan_meier(durations)
    expected = [
        (len(durations) - int(np.sum(durations <= value))) / len(durations) + 0.0
        for value in [0.0, *durations]
    ]

    np.testing.assert_allclose(curve, expected, atol=1e-12)


def test_kaplan_meier_validates_inputs() -> None:
    with pytest.raises(ValueError, match="1-D"):
        kaplan_meier(np.array([[1.0, 2.0]]))
    with pytest.raises(ValueError, match="positivas"):
        kaplan_meier(np.array([0.0, 1.0]))
    with pytest.raises(ValueError, match="misma longitud"):
        kaplan_meier(np.array([1.0, 2.0]), np.array([True]))


def test_event_index_kaplan_meier_matches_prototype() -> None:
    indices = np.array([5, 17, 40, 40, 92, 150])
    n_observations = 300

    times, curve, _lower, _upper, at_risk, n_events = event_index_kaplan_meier(
        indices, n_observations
    )

    unique = np.sort(np.unique(indices))
    durations = list(np.diff(unique))
    observed = [True] * len(durations)
    durations.append(max(1, n_observations - 1 - int(unique[-1])))
    observed.append(False)
    expected_times, expected_curve, _ = reference_kaplan_meier(
        np.asarray(durations, dtype=float), np.asarray(observed, dtype=bool)
    )

    assert n_events == len(unique)
    np.testing.assert_allclose(times, expected_times, atol=1e-12)
    np.testing.assert_allclose(curve, expected_curve, atol=1e-12)
    assert at_risk[0] == len(durations)


def test_event_index_requires_two_events() -> None:
    times, curve, lower, upper, at_risk, n_events = event_index_kaplan_meier([3], 100)

    assert len(times) == len(curve) == len(lower) == len(upper) == len(at_risk) == 0
    assert n_events == 1


def test_time_kaplan_meier_uses_observation_end_as_censoring() -> None:
    timestamps = np.array([10.0, 25.0, 40.0, 90.0])

    times, curve, _, _, _, n_events = time_kaplan_meier(timestamps, observation_end=100.0)

    durations = list(np.diff(timestamps)) + [10.0]
    expected_times, expected_curve, _ = reference_kaplan_meier(
        np.asarray(durations), np.array([True, True, True, False])
    )

    assert n_events == 4
    np.testing.assert_allclose(times, expected_times, atol=1e-12)
    np.testing.assert_allclose(curve, expected_curve, atol=1e-12)


def test_ks_exponential_returns_valid_pvalue() -> None:
    rng = np.random.default_rng(2)
    durations = rng.exponential(scale=5.0, size=400)

    result = ks_exponential(durations)

    assert 0.0 <= result["stat"] <= 1.0
    assert 0.0 <= result["p"] <= 1.0
    assert result["p"] > 0.01


def test_survival_result_packages_contract() -> None:
    indices = np.array([5, 20, 44, 70, 120])
    times, curve, lower, upper, at_risk, n_events = event_index_kaplan_meier(indices, 200)
    durations = np.diff(np.sort(indices)).astype(float)

    result = survival_result(
        durations,
        times,
        curve,
        lower,
        upper,
        at_risk,
        mode="event_index",
        n_events=n_events,
    )

    assert result["mode"] == "event_index"
    assert result["n_censored"] == 1
    assert len(result["curve"]) == len(curve)
    assert set(result["curve"][0]) == {"time", "survival", "ci_lower", "ci_upper", "at_risk"}
    if result["median"] is not None:
        assert result["median_ci"] is None or len(result["median_ci"]) == 2


def test_survival_result_without_curve_is_insufficient() -> None:
    empty = np.array([], dtype=float)

    result = survival_result(
        empty, empty, empty, empty, empty, np.array([], dtype=int), mode="time", n_events=0
    )

    assert result["status"] == "insufficient"
    assert result["curve"] == []
    assert result["median"] is None
