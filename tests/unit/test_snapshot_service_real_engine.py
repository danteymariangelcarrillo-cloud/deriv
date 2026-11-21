"""Integración del orquestador con el ``AnalyticsEngine`` real (T017)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from quantlab.analytics.engine import AnalyticsEngine
from quantlab.config import QuantLabConfig
from quantlab.data.cache import RAMCache
from quantlab.data.quality import DataQualityReport
from quantlab.services.feature_registry import FeatureRegistry
from quantlab.services.snapshot_service import SnapshotService
from quantlab.services.snapshot_store import SnapshotStore
from quantlab.state import InstrumentState

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
BARS = 2500


class RandomWalkMarketData:
    """Velas OHLC realistas y deterministas, sin red ni disco."""

    def __init__(self, bars: int = BARS, seed: int = 23) -> None:
        self.bars = bars
        self.seed = seed

    async def get_candles(
        self, symbol: str, timeframe: str, start_epoch: int, end_epoch: int
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        rng = np.random.default_rng(self.seed)
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.003, self.bars)))
        open_ = np.concatenate([[1000.0], close[:-1]])
        wick = np.abs(rng.normal(0.0, 0.0015, self.bars))
        high = np.maximum(open_, close) * (1.0 + wick)
        low = np.minimum(open_, close) * (1.0 - wick)
        step = max(1, (end_epoch - start_epoch) // self.bars)
        epochs = [end_epoch - (self.bars - index) * step for index in range(self.bars)]
        candles = pd.DataFrame(
            {
                "epoch": epochs,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
            }
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


def _state(config: QuantLabConfig) -> InstrumentState:
    return InstrumentState(
        symbol="BOOM1000",
        display_name="Boom 1000",
        direction="up",
        analyzer_config=config.analyzer,
        model_config=config.model,
        gate_config=config.gates,
        ram_config=config.ram,
        config_hash=config.hash(),
    )


def _service(state: InstrumentState) -> SnapshotService:
    cache = RAMCache(state.ram_config)
    return SnapshotService(
        SnapshotStore(),
        FeatureRegistry(state.gate_config),
        RandomWalkMarketData(),  # type: ignore[arg-type]
        AnalyticsEngine(state.gate_config),
        cache,
        states={state.symbol: state},
        clock=lambda: NOW,
    )


def test_real_engine_builds_snapshot_without_network() -> None:
    config = QuantLabConfig()
    state = _state(config)
    service = _service(state)

    snapshot = asyncio.run(service.build_snapshot("BOOM1000", timeframes=["5min"]))

    analysis = snapshot.timeframes["5min"]
    assert analysis.bars == BARS
    assert analysis.status in {"ready", "partial", "insufficient"}
    assert analysis.features["events.candle"].status == "ready"
    assert analysis.features["evt.gpd"].status == "ready"
    assert analysis.features["zones.reaction"].status == "ready"
    assert analysis.features["events.tick"].status == "not_applicable"
    assert analysis.features["walkforward.validation"].status == "insufficient"

    assert analysis.events.candle.n_events > 0
    assert analysis.evt is not None and analysis.evt.status == "ready"
    assert analysis.volatility is not None
    assert analysis.regimes is not None
    assert analysis.survival is not None
    assert analysis.autocorrelation is not None
    assert analysis.diagnostics is not None
    assert len(analysis.zones) > 0
    assert snapshot.config_hash == state.config_hash


def test_real_engine_is_deterministic_across_rebuilds() -> None:
    config = QuantLabConfig()
    first_state = _state(config)
    second_state = _state(config)

    first_snapshot = asyncio.run(
        _service(first_state).build_snapshot("BOOM1000", timeframes=["5min"])
    )
    second_snapshot = asyncio.run(
        _service(second_state).build_snapshot("BOOM1000", timeframes=["5min"])
    )
    first = first_snapshot.timeframes["5min"]
    second = second_snapshot.timeframes["5min"]

    assert first_snapshot.data_version == second_snapshot.data_version
    assert first.events.candle.model_dump() == second.events.candle.model_dump()
    assert [zone.model_dump() for zone in first.zones] == [
        zone.model_dump() for zone in second.zones
    ]
    assert first.evt == second.evt
    assert first.diagnostics == second.diagnostics


def test_real_engine_publishes_feature_errors_isolated() -> None:
    config = QuantLabConfig()
    state = _state(config)
    service = _service(state)
    # Con muy pocas barras el motor no puede ajustar GARCH; el resto sigue.
    service._market_data.bars = 150  # type: ignore[attr-defined]

    analysis = asyncio.run(service.build_snapshot("BOOM1000", timeframes=["5min"])).timeframes[
        "5min"
    ]

    assert analysis.bars == 150
    assert analysis.features["volatility.garch"].status == "insufficient"
    assert analysis.volatility is None
    assert analysis.events.candle.n_events >= 0
