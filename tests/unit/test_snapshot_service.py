"""Tests del orquestador de snapshots (T016) con motor y datos falsos."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from quantlab.config import TIMEFRAMES, AnalyzerConfig, GateConfig, QuantLabConfig
from quantlab.data.cache import RAMCache
from quantlab.data.quality import DataQualityReport
from quantlab.domain.events import EventSeries
from quantlab.domain.results import (
    ACFResult,
    ConditionalResult,
    DiagnosticsResult,
    EVTResult,
    GARCHResult,
    RegimeResult,
    SurvivalResult,
    WalkForwardAggregate,
    WalkForwardConfig,
    WalkForwardResult,
)
from quantlab.domain.zones import ReactionZone
from quantlab.services.feature_registry import FeatureRegistry
from quantlab.services.snapshot_service import (
    ConfigUpdatedEvent,
    ConfigUpdateRequest,
    SnapshotService,
)
from quantlab.services.snapshot_store import SnapshotStore
from quantlab.state import InstrumentState

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
BARS = 600


# ------------------------------- dobles de prueba -------------------------------


class FakeMarketDataService:
    """Devuelve ``bars`` velas sintéticas y un reporte de calidad ``ok``."""

    def __init__(self, bars: int = BARS) -> None:
        self.bars = bars
        self.capitalised = False
        self.calls: list[tuple[str, str, int, int]] = []

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start_epoch: int,
        end_epoch: int,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        self.calls.append((symbol, timeframe, start_epoch, end_epoch))
        step = max(1, (end_epoch - start_epoch) // max(self.bars, 1))
        epochs = [end_epoch - (self.bars - index) * step for index in range(self.bars)]
        candles = pd.DataFrame(
            {
                "epoch": epochs,
                "open": [100.0 + index * 0.1 for index in range(self.bars)],
                "high": [100.5 + index * 0.1 for index in range(self.bars)],
                "low": [99.5 + index * 0.1 for index in range(self.bars)],
                "close": [100.2 + index * 0.1 for index in range(self.bars)],
            }
        )
        if self.capitalised:
            candles = candles.rename(
                columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
            )
        report = DataQualityReport(
            status="ok",
            expected_bars=self.bars,
            received_bars=self.bars,
            coverage_pct=100.0,
            missing_bars=0,
            gaps_count=0,
            duplicates_removed=0,
            invalid_ohlc=0,
            first_epoch=epochs[0],
            last_epoch=epochs[-1],
        )
        return candles, report


def _zone() -> ReactionZone:
    return ReactionZone(
        symbol="BOOM1000",
        timeframe="5min",
        zone_type="support",
        center=100.0,
        lower=99.5,
        upper=100.5,
        half_width=0.5,
        touch_bars=2,
        episodes=1,
        evaluated_touches=1,
        reactions=1,
        failures=0,
        ambiguous_touches=0,
        pending_touches=0,
        event_count_in_zone=0,
        available_from_epoch=1_699_000_000,
        stage="in_sample",
        status="ready",
    )


class FakeAnalyticsEngine:
    """Motor mínimo: muestra configurable y resultados válidos por análisis."""

    def __init__(self) -> None:
        self.sample_sizes: dict[str, int] = {}
        self.computed: list[str] = []
        self.fail_gate: set[str] = set()
        self.fail_compute: set[str] = set()

    def sample_size(
        self,
        analysis_id: str,
        *,
        candles: pd.DataFrame,
        ticks: pd.DataFrame,
    ) -> int:
        if analysis_id in self.fail_gate:
            raise RuntimeError(f"gate roto: {analysis_id}")
        if analysis_id in self.sample_sizes:
            return self.sample_sizes[analysis_id]
        return len(ticks) if analysis_id == "events.tick" else len(candles)

    def compute(
        self,
        analysis_id: str,
        *,
        symbol: str,
        timeframe: str,
        candles: pd.DataFrame,
        ticks: pd.DataFrame,
        config: QuantLabConfig,
    ) -> Any:
        if analysis_id in self.fail_compute:
            raise RuntimeError(f"cómputo roto: {analysis_id}")
        self.computed.append(analysis_id)
        builders = {
            "events.candle": lambda: EventSeries(
                status="ready", n_events=2, threshold_quantile=0.99, lookback=200, warmup=50
            ),
            "events.tick": lambda: EventSeries(
                status="ready",
                n_events=1,
                threshold_quantile=0.999,
                lookback=2000,
                warmup=300,
            ),
            "evt.gpd": lambda: EVTResult(
                status="ready", threshold=1.0, threshold_quantile=0.95, n_excesses=40
            ),
            "volatility.garch": lambda: GARCHResult(status="ready", n_obs=len(candles)),
            "volatility.regimes": lambda: RegimeResult(
                status="ready", method="markov_switching", n_obs=len(candles)
            ),
            "survival.kaplan_meier": lambda: SurvivalResult(
                status="ready", mode="event_index", n_events=25, n_censored=0
            ),
            "autocorrelation": lambda: ACFResult(
                n_obs=max(100, len(candles)), max_lag=20, conf_band=0.1
            ),
            "diagnostics.residuals": lambda: DiagnosticsResult(
                jarque_bera={"stat": 1.0, "p": 0.1},
                arch_lm={"stat": 1.0, "p": 0.1, "lags": 5},
                adf={"stat": -3.0, "p": 0.01, "lags": 1, "critical_values": {"1%": -3.4}},
            ),
            "events.conditional": lambda: ConditionalResult(
                status="ready", mode="bars", horizon=10, bin_size=5
            ),
            "walkforward.validation": lambda: WalkForwardResult(
                status="ready",
                config=WalkForwardConfig(train_bars=100, test_bars=20, step_bars=10, min_folds=1),
                aggregate=WalkForwardAggregate(
                    folds=0,
                    evaluated=0,
                    reactions=0,
                    rate=0.0,
                    ci={"lower": 0.0, "upper": 0.1},
                    sign_test_p=1.0,
                ),
                warning="folds no independientes",
            ),
            "zones.reaction": lambda: (_zone(),),
        }
        return builders[analysis_id]()


# --------------------------------- fixtures ---------------------------------


def _state(
    symbol: str = "BOOM1000",
    *,
    config: QuantLabConfig | None = None,
) -> InstrumentState:
    config = config or QuantLabConfig()
    return InstrumentState(
        symbol=symbol,
        display_name="Boom 1000" if symbol == "BOOM1000" else "Crash 500",
        direction="up" if symbol == "BOOM1000" else "down",
        analyzer_config=config.analyzer,
        model_config=config.model,
        gate_config=config.gates,
        ram_config=config.ram,
        config_hash=config.hash(),
    )


class Harness:
    def __init__(self, state: InstrumentState | None = None) -> None:
        self.state = state or _state()
        self.store = SnapshotStore()
        self.engine = FakeAnalyticsEngine()
        self.market = FakeMarketDataService()
        self.cache = RAMCache(self.state.ram_config)
        self.events: list[ConfigUpdatedEvent] = []
        self.service = SnapshotService(
            self.store,
            FeatureRegistry(self.state.gate_config),
            self.market,  # type: ignore[arg-type]
            self.engine,
            self.cache,
            states={self.state.symbol: self.state},
            event_sink=self.events.append,
            clock=lambda: NOW,
        )


# -------------------------------- build_snapshot --------------------------------


def test_build_snapshot_assembles_frozen_snapshot_and_swaps() -> None:
    harness = Harness()

    snapshot = asyncio.run(harness.service.build_snapshot("BOOM1000", timeframes=["5min"]))

    assert snapshot.instrument.symbol == "BOOM1000"
    assert snapshot.overall_status == "ready"
    assert snapshot.config_hash == harness.state.config_hash
    assert snapshot.generated_at == NOW
    assert set(snapshot.timeframes) == {"5min"}
    assert harness.store.get("BOOM1000") is snapshot
    assert harness.store.get_version("BOOM1000") == 1
    assert harness.state.snapshot is snapshot
    assert harness.state.snapshot_version == 1
    assert snapshot.data_version.startswith("sha256:")


def test_build_snapshot_publishes_all_features_and_results() -> None:
    harness = Harness()

    analysis = asyncio.run(
        harness.service.build_snapshot("BOOM1000", timeframes=["5min"])
    ).timeframes["5min"]

    assert len(analysis.features) == 14
    assert analysis.features["evt.gpd"].status == "ready"
    assert analysis.features["events.tick"].status == "not_applicable"
    assert "events.tick" not in harness.engine.computed
    assert analysis.evt is not None and analysis.evt.n_excesses == 40
    assert analysis.volatility is not None
    assert analysis.regimes is not None
    assert analysis.survival is not None
    assert analysis.autocorrelation is not None
    assert analysis.diagnostics is not None
    assert analysis.conditional is not None
    assert analysis.walkforward is not None
    assert len(analysis.zones) == 1
    assert analysis.events.candle.n_events == 2
    assert analysis.bars == BARS
    assert analysis.quality.status == "ok"
    assert analysis.data_range.end_epoch == int(harness.state.candles_by_tf["5min"][-1].epoch)
    # Los análisis sin payload (calidad y mapas de calor) no se computan.
    assert {"data.quality", "heatmap.price", "heatmap.events"}.isdisjoint(harness.engine.computed)


def test_gates_prevent_computation_when_sample_is_insufficient() -> None:
    harness = Harness()
    harness.engine.sample_sizes["evt.gpd"] = 5

    analysis = asyncio.run(
        harness.service.build_snapshot("BOOM1000", timeframes=["5min"])
    ).timeframes["5min"]

    assert analysis.features["evt.gpd"].status == "insufficient"
    assert "muestra insuficiente" in (analysis.features["evt.gpd"].reason or "")
    assert "evt.gpd" not in harness.engine.computed
    assert analysis.evt is None
    assert analysis.status == "ready"  # el resto de análisis siguen listos


def test_engine_failures_are_isolated_per_analysis() -> None:
    harness = Harness()
    harness.engine.fail_gate.add("survival.kaplan_meier")
    harness.engine.fail_compute.add("volatility.garch")

    analysis = asyncio.run(
        harness.service.build_snapshot("BOOM1000", timeframes=["5min"])
    ).timeframes["5min"]

    assert analysis.features["survival.kaplan_meier"].status == "error"
    assert analysis.features["volatility.garch"].status == "error"
    assert analysis.survival is None
    assert analysis.volatility is None
    assert len(analysis.errors) == 2
    assert analysis.features["evt.gpd"].status == "ready"


def test_empty_data_yields_error_timeframe_and_snapshot() -> None:
    harness = Harness()
    harness.market.bars = 0
    harness.engine.sample_sizes = dict.fromkeys(harness.service.registry.analysis_ids(), 0)

    snapshot = asyncio.run(harness.service.build_snapshot("BOOM1000", timeframes=["5min"]))

    analysis = snapshot.timeframes["5min"]
    assert analysis.bars == 0
    assert analysis.status == "error"
    assert snapshot.overall_status == "error"
    assert analysis.evt is None
    assert analysis.features["data.quality"].status == "insufficient"
    assert analysis.data_range.start_epoch == analysis.data_range.end_epoch


def test_build_snapshot_accepts_market_data_capitalised_columns() -> None:
    harness = Harness()
    harness.market.capitalised = True

    analysis = asyncio.run(
        harness.service.build_snapshot("BOOM1000", timeframes=["1h"])
    ).timeframes["1h"]

    assert analysis.bars == BARS
    assert len(harness.state.candles_by_tf["1h"]) == BARS
    assert harness.state.candles_by_tf["1h"][-1].close > 0


def test_snapshot_config_mirrors_instrument_config() -> None:
    config = QuantLabConfig(
        analyzer=AnalyzerConfig(days=30, candle_event_quantile=0.95),
        gates=GateConfig(evt_min_excess=12),
    )
    harness = Harness(_state(config=config))

    snapshot = asyncio.run(harness.service.build_snapshot("BOOM1000", timeframes=["15min"]))

    assert snapshot.config.gates.evt_min_excess == 12
    assert snapshot.config.analysis.candle_event_quantile == 0.95
    assert snapshot.config.runtime.stale_seconds == 120
    assert harness.service.registry.min_sample("evt.gpd", "15min") == 12


def test_unknown_symbol_and_bad_timeframes_are_rejected() -> None:
    harness = Harness()

    with pytest.raises(KeyError, match="desconocido"):
        asyncio.run(harness.service.build_snapshot("NOPE"))
    with pytest.raises(ValueError, match="temporalidad"):
        asyncio.run(harness.service.build_snapshot("BOOM1000", timeframes=["2min"]))
    with pytest.raises(ValueError, match="al menos"):
        asyncio.run(harness.service.build_snapshot("BOOM1000", timeframes=[]))


# ------------------------------ recompute_timeframe ------------------------------


def test_recompute_timeframe_merges_into_existing_snapshot() -> None:
    async def scenario() -> None:
        harness = Harness()
        await harness.service.build_snapshot("BOOM1000", timeframes=["5min", "15min"])
        harness.engine.sample_sizes["evt.gpd"] = 1

        analysis = await harness.service.recompute_timeframe("BOOM1000", "5min")
        snapshot = harness.store.get("BOOM1000")

        assert snapshot is not None
        assert snapshot.timeframes["5min"] is analysis
        assert set(snapshot.timeframes) == {"5min", "15min"}
        assert analysis.evt is None
        assert snapshot.timeframes["15min"].evt is not None
        assert harness.store.get_version("BOOM1000") == 2
        assert harness.state.snapshot_version == 2

    asyncio.run(scenario())


# -------------------------------- update_config --------------------------------


def test_update_config_applies_and_recomputes_in_background() -> None:
    async def scenario() -> None:
        harness = Harness()
        await harness.service.build_snapshot("BOOM1000")
        old_hash = harness.state.config_hash

        response = await harness.service.update_config(
            "BOOM1000", ConfigUpdateRequest(analyzer=AnalyzerConfig(days=45))
        )

        assert response.status == "updated"
        assert response.config_hash != old_hash
        assert response.recalculated_timeframes == list(TIMEFRAMES)
        assert harness.state.config_hash == response.config_hash
        assert harness.state.analyzer_config.days == 45
        assert harness.state.snapshot_version == 2

        await harness.service.wait_for_background()

        assert {event.recalculated for event in harness.events} == {
            (timeframe,) for timeframe in TIMEFRAMES
        }
        snapshot = harness.store.get("BOOM1000")
        assert snapshot is not None
        assert snapshot.config_hash == response.config_hash
        assert harness.store.get_version("BOOM1000") == 7

    asyncio.run(scenario())


def test_update_config_is_idempotent_and_skips_ram_only_changes() -> None:
    async def scenario() -> None:
        harness = Harness()
        await harness.service.build_snapshot("BOOM1000")

        request = ConfigUpdateRequest(analyzer=AnalyzerConfig(days=45))
        first = await harness.service.update_config("BOOM1000", request)
        await harness.service.wait_for_background()
        version = harness.store.get_version("BOOM1000")

        second = await harness.service.update_config("BOOM1000", request)
        assert second.status == "unchanged"
        assert second.recalculated_timeframes == []
        assert harness.store.get_version("BOOM1000") == version
        assert harness.state.analyzer_config.days == 45

        ram_only = await harness.service.update_config(
            "BOOM1000",
            ConfigUpdateRequest(
                ram=harness.state.ram_config.model_copy(update={"ram_warning_mb": 200})
            ),
        )
        assert ram_only.status == "updated"
        assert ram_only.recalculated_timeframes == []
        assert harness.store.get_version("BOOM1000") == version

        empty = await harness.service.update_config("BOOM1000", ConfigUpdateRequest())
        assert empty.status == "unchanged"
        assert first.status == "updated"

    asyncio.run(scenario())


def test_update_config_rejects_invalid_values() -> None:
    harness = Harness()
    with pytest.raises(ValidationError):
        ConfigUpdateRequest(analyzer=AnalyzerConfig(days=0))
    with pytest.raises(ValidationError):
        ConfigUpdateRequest(unknown=AnalyzerConfig())
    with pytest.raises(KeyError, match="desconocido"):
        asyncio.run(
            harness.service.update_config(
                "NOPE", ConfigUpdateRequest(analyzer=AnalyzerConfig(days=10))
            )
        )


def test_gate_update_changes_registry_and_recalculation() -> None:
    async def scenario() -> None:
        harness = Harness()
        await harness.service.build_snapshot("BOOM1000", timeframes=["5min"])

        response = await harness.service.update_config(
            "BOOM1000", ConfigUpdateRequest(gates=GateConfig(evt_min_excess=10_000))
        )
        await harness.service.wait_for_background()

        assert response.status == "updated"
        assert harness.service.registry.min_sample("evt.gpd", "5min") == 10_000
        snapshot = harness.store.get("BOOM1000")
        assert snapshot is not None
        feature = snapshot.timeframes["5min"].features["evt.gpd"]
        assert feature.status == "insufficient"
        assert snapshot.timeframes["5min"].evt is None

    asyncio.run(scenario())


def test_reset_config_restores_defaults() -> None:
    async def scenario() -> None:
        harness = Harness(_state(config=QuantLabConfig(analyzer=AnalyzerConfig(days=90))))
        await harness.service.build_snapshot("BOOM1000", timeframes=["5min"])

        response = await harness.service.reset_config("BOOM1000")
        await harness.service.wait_for_background()

        assert response.status == "updated"
        assert response.config_hash == QuantLabConfig().hash()
        assert harness.state.analyzer_config.days == 20
        assert harness.state.config_hash == QuantLabConfig().hash()

        again = await harness.service.reset_config("BOOM1000")
        assert again.status == "unchanged"
        assert again.recalculated_timeframes == []

    asyncio.run(scenario())
