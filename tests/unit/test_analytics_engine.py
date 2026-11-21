"""Tests de la fachada ``AnalyticsEngine`` (T017)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantlab.analytics.engine import AnalyticsEngine
from quantlab.config import QuantLabConfig
from quantlab.domain.events import EventSeries
from quantlab.domain.results import (
    ACFResult,
    ConditionalResult,
    DiagnosticsResult,
    EVTResult,
    GARCHResult,
    RegimeResult,
    SurvivalResult,
)
from quantlab.domain.zones import ReactionZone
from tests.unit._analytics_fixtures import make_candles, make_ticks


@pytest.fixture(scope="module")
def candles() -> pd.DataFrame:
    return make_candles(900, seed=17)


@pytest.fixture(scope="module")
def ticks() -> pd.DataFrame:
    return make_ticks(4000, seed=18)


@pytest.fixture(scope="module")
def config() -> QuantLabConfig:
    return QuantLabConfig()


def compute(engine: AnalyticsEngine, analysis_id: str, candles, ticks, config):
    return engine.compute(
        analysis_id,
        symbol="BOOM1000",
        timeframe="5min",
        candles=candles,
        ticks=ticks,
        config=config,
    )


def test_sample_size_delegates_by_analysis(candles, ticks) -> None:
    engine = AnalyticsEngine()
    candles_lower = candles.rename(columns=str.lower)

    assert engine.sample_size("events.candle", candles=candles_lower, ticks=ticks) == len(candles)
    assert engine.sample_size("events.tick", candles=candles_lower, ticks=ticks) == len(ticks)
    assert (
        engine.sample_size("volatility.garch", candles=candles_lower, ticks=ticks)
        == len(candles) - 1
    )
    assert (
        engine.sample_size("zones.reaction", candles=candles_lower, ticks=ticks)
        == len(candles) // 20
    )
    assert engine.sample_size("walkforward.validation", candles=candles_lower, ticks=ticks) == 0
    assert engine.sample_size("data.quality", candles=candles_lower, ticks=ticks) == 1


def test_compute_returns_domain_models(candles, ticks, config) -> None:
    engine = AnalyticsEngine()

    assert isinstance(compute(engine, "events.candle", candles, ticks, config), EventSeries)
    assert isinstance(compute(engine, "events.tick", candles, ticks, config), EventSeries)
    assert isinstance(
        compute(engine, "events.conditional", candles, ticks, config), ConditionalResult
    )
    assert isinstance(compute(engine, "evt.gpd", candles, ticks, config), EVTResult)
    assert isinstance(compute(engine, "volatility.garch", candles, ticks, config), GARCHResult)
    assert isinstance(compute(engine, "volatility.regimes", candles, ticks, config), RegimeResult)
    assert isinstance(
        compute(engine, "survival.kaplan_meier", candles, ticks, config), SurvivalResult
    )
    assert isinstance(compute(engine, "autocorrelation", candles, ticks, config), ACFResult)
    assert isinstance(
        compute(engine, "diagnostics.residuals", candles, ticks, config), DiagnosticsResult
    )

    zones = compute(engine, "zones.reaction", candles, ticks, config)
    assert isinstance(zones, tuple)
    assert all(isinstance(zone, ReactionZone) for zone in zones)

    assert compute(engine, "heatmap.price", candles, ticks, config) is None
    assert compute(engine, "data.quality", candles, ticks, config) is None


def test_engine_garch_uses_contract_parameter_names(candles, ticks, config) -> None:
    result = compute(AnalyticsEngine(), "volatility.garch", candles, ticks, config)

    assert {"omega", "alpha1", "beta1"} <= set(result.params)
    assert {"omega", "alpha1", "beta1"} <= set(result.p_values)
    assert set(result.diagnostics) == {"ljung_box", "arch_lm"}


def test_compute_is_deterministic(candles, ticks, config) -> None:
    engine = AnalyticsEngine()

    first = compute(engine, "events.candle", candles, ticks, config)
    second = compute(engine, "events.candle", candles, ticks, config)
    assert first == second

    first_zones = compute(engine, "zones.reaction", candles, ticks, config)
    second_zones = compute(engine, "zones.reaction", candles, ticks, config)
    assert first_zones == second_zones

    first_garch = compute(engine, "volatility.garch", candles, ticks, config)
    second_garch = compute(engine, "volatility.garch", candles, ticks, config)
    assert first_garch == second_garch


def test_engine_accepts_lowercase_columns(candles, ticks, config) -> None:
    engine = AnalyticsEngine()
    lower = candles.rename(columns=str.lower)

    result = compute(engine, "events.candle", lower, ticks, config)

    assert isinstance(result, EventSeries)
    assert compute(engine, "autocorrelation", lower, ticks, config) == compute(
        engine, "autocorrelation", candles, ticks, config
    )


def test_engine_survival_uses_ticks_when_available(candles, ticks, config) -> None:
    engine = AnalyticsEngine()

    with_ticks = compute(engine, "survival.kaplan_meier", candles, ticks, config)
    without_ticks = compute(engine, "survival.kaplan_meier", candles, pd.DataFrame(), config)

    assert with_ticks.mode == without_ticks.mode == "event_index"
    assert with_ticks.n_events >= 1
    assert without_ticks.n_events >= 1


def test_engine_validates_timeframe_and_analysis(candles, ticks, config) -> None:
    engine = AnalyticsEngine()

    with pytest.raises(ValueError, match="temporalidad"):
        engine.compute(
            "events.candle",
            symbol="BOOM1000",
            timeframe="2min",
            candles=candles,
            ticks=ticks,
            config=config,
        )
    with pytest.raises(KeyError, match="análisis desconocido"):
        compute(engine, "does.not.exist", candles, ticks, config)


def test_engine_handles_crash_instrument_direction(candles, ticks, config) -> None:
    engine = AnalyticsEngine()

    result = engine.compute(
        "events.candle",
        symbol="CRASH500",
        timeframe="5min",
        candles=candles,
        ticks=ticks,
        config=config,
    )

    assert all(event.direction == "down" for event in result.events)
    assert all(event.kind == "candle" for event in result.events)


def test_engine_works_with_empty_data(config) -> None:
    engine = AnalyticsEngine()
    empty = pd.DataFrame(columns=["epoch", "open", "high", "low", "close"])

    assert engine.sample_size("evt.gpd", candles=empty, ticks=empty) == 0
    assert engine.sample_size("events.tick", candles=empty, ticks=empty) == 0
    assert compute(engine, "zones.reaction", empty, empty, config) == ()
    series = compute(engine, "events.candle", empty, empty, config)
    assert series.n_events == 0
    assert series.status == "insufficient"


def test_engine_garch_error_is_reported_as_insufficient(candles, config) -> None:
    engine = AnalyticsEngine()
    short = candles.iloc[:50]

    result = compute(engine, "volatility.garch", short, pd.DataFrame(), config)

    assert result.status == "insufficient"
    assert result.n_obs == 49
    assert result.message


def test_engine_regimes_with_cusum_config(candles, ticks) -> None:
    engine = AnalyticsEngine()
    config = QuantLabConfig(model={"regime_method": "cusum"})  # type: ignore[arg-type]

    result = compute(engine, "volatility.regimes", candles, ticks, config)

    assert result.status == "ready"
    assert result.method == "cusum"
    assert len(result.series) <= config.ram.downsample_max_points
    assert result.series == tuple(sorted(result.series, key=lambda point: point.epoch))
    assert all(0.0 <= point.prob_high <= 1.0 for point in result.series)


def test_engine_does_not_mutate_inputs(candles, ticks, config) -> None:
    engine = AnalyticsEngine()
    candles_copy = candles.copy(deep=True)
    ticks_copy = ticks.copy(deep=True)

    for analysis_id in ("events.candle", "events.tick", "zones.reaction", "survival.kaplan_meier"):
        compute(engine, analysis_id, candles, ticks, config)

    pd.testing.assert_frame_equal(candles, candles_copy)
    pd.testing.assert_frame_equal(ticks, ticks_copy)


def test_engine_zones_have_no_nan_optional_fields(candles, ticks, config) -> None:
    engine = AnalyticsEngine()

    for zone in compute(engine, "zones.reaction", candles, ticks, config):
        if zone.reaction_rate is not None:
            assert np.isfinite(zone.reaction_rate)
        if zone.p_value_raw is not None:
            assert 0.0 <= zone.p_value_raw <= 1.0
        assert zone.status in {"ready", "insufficient"}
