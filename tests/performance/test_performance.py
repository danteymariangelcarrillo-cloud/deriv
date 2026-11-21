"""Benchmarks locales de rendimiento (T041).

Excluidos por defecto (``-m "not performance"``). Ejecutar con::

    uv run pytest tests/performance -m performance
"""

from __future__ import annotations

import os
import statistics
import time
from datetime import UTC, datetime

import pandas as pd
import psutil
import pytest

from quantlab.analytics.engine import AnalyticsEngine
from quantlab.config import QuantLabConfig
from quantlab.dashboard.app import build_services
from quantlab.data.rate_limiter import TokenBucket
from quantlab.state import InstrumentState, TickRecord
from tests.unit._analytics_fixtures import make_candles

pytestmark = pytest.mark.performance


def _median_runs(function, runs: int = 5) -> float:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def test_startup_time_under_three_seconds() -> None:
    assert _median_runs(build_services) < 3.0


def test_recompute_time_per_timeframe_under_500ms() -> None:
    engine = AnalyticsEngine()
    candles = make_candles(1500, seed=51)
    config = QuantLabConfig()

    def compute() -> None:
        engine.compute(
            "zones.reaction",
            symbol="BOOM1000",
            timeframe="5min",
            candles=candles,
            ticks=pd.DataFrame(),
            config=config,
        )

    assert _median_runs(compute, runs=3) < 0.5


def test_tick_enqueue_latency_under_five_ms() -> None:
    config = QuantLabConfig()
    state = InstrumentState(
        symbol="BOOM1000",
        display_name="Boom 1000",
        direction="up",
        analyzer_config=config.analyzer,
        model_config=config.model,
        gate_config=config.gates,
        ram_config=config.ram,
        config_hash=config.hash(),
    )

    def enqueue_batch() -> None:
        for index in range(1000):
            state.append_live_tick(
                TickRecord(
                    epoch=int(datetime.now(UTC).timestamp()) + index,
                    price=1000.0 + index * 0.01,
                    received_at=time.monotonic(),
                    sequence=index,
                )
            )

    per_tick = _median_runs(enqueue_batch, runs=5) / 1000.0
    assert per_tick < 0.005


def test_ram_usage_below_400_mb() -> None:
    rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    assert rss_mb < 400.0


def test_rate_limit_budget_is_150_per_minute() -> None:
    bucket = TokenBucket(capacity=30, refill_per_second=2.5)

    assert bucket.state.budget_per_minute == 150
    granted = sum(1 for _ in range(30) if bucket.try_acquire())
    assert granted == 30
    assert bucket.try_acquire() is False


def test_series_calculation_does_not_scale_pathologically() -> None:
    engine = AnalyticsEngine()
    config = QuantLabConfig()
    small = make_candles(1000, seed=52)
    large = make_candles(3000, seed=52)

    def run(candles: pd.DataFrame) -> float:
        started = time.perf_counter()
        engine.compute(
            "events.candle",
            symbol="BOOM1000",
            timeframe="5min",
            candles=candles,
            ticks=pd.DataFrame(),
            config=config,
        )
        return time.perf_counter() - started

    assert run(large) < run(small) * 8.0


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("QUANTLAB_PERF_LIVE") != "1",
    reason="warm start real requiere QUANTLAB_PERF_LIVE=1 y red Deriv",
)
def test_warm_start_under_30_seconds() -> None:
    import asyncio

    from scripts.warm_cache import warm

    started = time.perf_counter()
    asyncio.run(warm(["BOOM1000"], ["5min"], force=False))
    assert time.perf_counter() - started < 30.0
