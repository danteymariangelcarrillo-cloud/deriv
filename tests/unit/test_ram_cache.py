"""Tests de RAMCache (T008): buffers, LRU, stats y monitor de RAM."""

from __future__ import annotations

import asyncio

import pytest

from quantlab.config import RAMConfig
from quantlab.data.cache import RAMCache
from quantlab.state import CandleRecord, TickRecord


def _config(**overrides: int) -> RAMConfig:
    base = {
        "max_ticks_per_symbol": 1_000,
        "max_candles_per_tf": 1_000,
        "max_directional_returns": 1_000,
        "max_live_queue": 1_000,
    }
    base.update(overrides)
    return RAMConfig(**base)


def _tick(epoch: int = 1, price: float = 100.0) -> TickRecord:
    return TickRecord(epoch=epoch, price=price, received_at=float(epoch), sequence=epoch)


def _ticks(count: int, offset: int = 0) -> list[TickRecord]:
    return [_tick(epoch=offset + i) for i in range(count)]


def _candle(epoch: int = 1) -> CandleRecord:
    return CandleRecord(
        epoch=epoch, open=100.0, high=101.0, low=99.0, close=100.5, timeframe="1min"
    )


def _candles(count: int, offset: int = 0) -> list[CandleRecord]:
    return [_candle(epoch=offset + i) for i in range(count)]


def test_append_ticks_reports_only_fifo_drops() -> None:
    cache = RAMCache(_config())

    assert cache.append_ticks("CRASH500", _ticks(1_000)) == 0
    assert cache.append_ticks("CRASH500", _ticks(5, offset=1_000)) == 5

    ticks = cache.get_ticks("CRASH500")
    assert len(ticks) == 1_000
    assert ticks[0].epoch == 5
    assert ticks[-1].epoch == 1_004
    assert cache.get_ticks("CRASH500", max_n=2) == ticks[-2:]


def test_append_live_tick_signals_maxlen_drop_without_touching_dropped_counter() -> None:
    cache = RAMCache(_config())

    for epoch in range(1_000):
        assert cache.append_live_tick("CRASH500", _tick(epoch)) is False

    assert cache.append_live_tick("CRASH500", _tick(1_000)) is True
    stats = cache.get_stats("CRASH500")
    assert stats.live_ticks == 1_000
    assert stats.dropped_ticks == 0
    assert stats.live_queue_depth == 0
    assert cache.get_live_ticks("CRASH500", max_n=1)[0].epoch == 1_000


def test_append_candles_per_timeframe_and_invalid_timeframe() -> None:
    cache = RAMCache(_config())

    assert cache.append_candles("BOOM1000", "5min", _candles(1_000)) == 0
    assert cache.append_candles("BOOM1000", "5min", _candles(3, offset=1_000)) == 3

    candles = cache.get_candles("BOOM1000", "5min")
    assert len(candles) == 1_000
    assert candles[0].epoch == 3
    assert cache.get_candles("BOOM1000", "1min") == []

    with pytest.raises(ValueError, match="temporalidad"):
        cache.append_candles("BOOM1000", "7min", _candles(1))
    with pytest.raises(ValueError, match="temporalidad"):
        cache.get_candles("BOOM1000", "7min")


def test_getters_unknown_symbol_and_limits() -> None:
    cache = RAMCache(_config())
    assert cache.get_ticks("CRASH500") == []
    assert cache.get_live_ticks("CRASH500") == []
    assert cache.get_candles("CRASH500", "1h") == []

    cache.append_ticks("CRASH500", _ticks(10))
    assert cache.get_ticks("CRASH500", max_n=0) == []
    assert len(cache.get_ticks("CRASH500", max_n=99)) == 10
    with pytest.raises(ValueError, match="max_n"):
        cache.get_ticks("CRASH500", max_n=-1)


def test_live_queue_fifo_and_dropped_ticks_only_when_full() -> None:
    cache = RAMCache(_config(max_live_queue=1_000))

    for epoch in range(1_000):
        assert cache.enqueue_live("CRASH500", _tick(epoch)) is False

    assert cache.enqueue_live("CRASH500", _tick(1_000)) is True
    assert cache.enqueue_live("CRASH500", _tick(1_001)) is True

    stats = cache.get_stats("CRASH500")
    assert stats.dropped_ticks == 2
    assert stats.live_queue_depth == 1_000

    assert cache.dequeue_live("CRASH500") is not None
    assert cache.get_stats("CRASH500").live_queue_depth == 999
    assert cache.dequeue_live("UNKNOWN") is None


def test_directional_returns_buffer_is_bounded() -> None:
    cache = RAMCache(_config(max_directional_returns=1_000))

    for value in range(1_000):
        assert cache.append_directional_return("CRASH500", float(value)) is False
    assert cache.append_directional_return("CRASH500", 1_000.0) is True

    assert cache.get_stats("CRASH500").directional_returns == 1_000


def test_stats_are_scoped_per_symbol() -> None:
    cache = RAMCache(_config())
    cache.append_ticks("CRASH500", _ticks(2))
    cache.append_live_tick("CRASH500", _tick(3))
    cache.append_candles("CRASH500", "1min", _candles(1))
    cache.append_directional_return("CRASH500", 0.25)
    cache.enqueue_live("CRASH500", _tick(4))

    stats = cache.get_stats("CRASH500")
    assert stats.symbol == "CRASH500"
    assert stats.historical_ticks == 2
    assert stats.live_ticks == 1
    assert stats.directional_returns == 1
    assert stats.live_queue_depth == 1
    assert stats.dropped_ticks == 0
    assert stats.candles_by_tf["1min"] == 1
    assert set(stats.candles_by_tf) == {"1min", "5min", "15min", "1h", "4h", "1d"}
    assert stats.ram_usage_mb > 0

    empty = cache.get_stats("BOOM1000")
    assert empty.historical_ticks == 0
    assert empty.candles_by_tf == {}
    assert empty.ram_usage_mb == 0.0


def test_warning_evicts_least_recently_used_candle_buffer() -> None:
    config = _config()
    cache = RAMCache(config)
    cache.append_candles("CRASH500", "1min", _candles(5))
    cache.append_candles("CRASH500", "5min", _candles(5))
    cache.append_candles("BOOM1000", "1min", _candles(5))

    # Refresca CRASH500/1min: el LRU pasa a ser CRASH500/5min.
    assert cache.get_candles("CRASH500", "1min")

    freed = cache.check_ram_and_evict(config.ram_warning_mb + 1)

    assert isinstance(freed, int)
    assert cache.get_candles("CRASH500", "5min") == []
    assert len(cache.get_candles("CRASH500", "1min")) == 5
    assert len(cache.get_candles("BOOM1000", "1min")) == 5
    assert cache.figure_cache_size == config.figure_cache_size

    # El buffer evictado se puede volver a llenar.
    assert cache.append_candles("CRASH500", "5min", _candles(2)) == 0
    assert len(cache.get_candles("CRASH500", "5min")) == 2
    assert cache.get_stats("CRASH500").candles_by_tf["1min"] == 5


def test_eviction_is_global_across_symbols() -> None:
    config = _config()
    cache = RAMCache(config)
    cache.append_candles("CRASH500", "1min", _candles(5))
    cache.append_candles("BOOM1000", "15min", _candles(5))

    cache.check_ram_and_evict(config.ram_warning_mb + 1)
    assert cache.get_candles("CRASH500", "1min") == []
    assert len(cache.get_candles("BOOM1000", "15min")) == 5

    cache.check_ram_and_evict(config.ram_warning_mb + 1)
    assert cache.get_candles("BOOM1000", "15min") == []


def test_critical_evicts_all_candles_and_shrinks_figure_cache() -> None:
    config = _config()
    cache = RAMCache(config)
    for timeframe in ("1min", "5min", "1h"):
        cache.append_candles("CRASH500", timeframe, _candles(10))

    freed = cache.check_ram_and_evict(config.ram_critical_mb + 1)

    assert isinstance(freed, int)
    assert all(cache.get_candles("CRASH500", tf) == [] for tf in ("1min", "5min", "1h"))
    assert cache.figure_cache_size == config.figure_cache_size // 2

    cache.check_ram_and_evict(config.ram_critical_mb + 1)
    cache.check_ram_and_evict(config.ram_critical_mb + 1)
    assert cache.figure_cache_size == 5


def test_below_warning_does_not_evict() -> None:
    config = _config()
    cache = RAMCache(config)
    cache.append_candles("CRASH500", "1min", _candles(5))

    assert cache.check_ram_and_evict(config.ram_warning_mb) == 0
    assert len(cache.get_candles("CRASH500", "1min")) == 5
    assert cache.figure_cache_size == config.figure_cache_size


async def test_monitor_evicts_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    cache = RAMCache(config)
    cache.append_candles("CRASH500", "1min", _candles(10))
    monkeypatch.setattr(
        RAMCache, "_rss_mb", staticmethod(lambda: float(config.ram_critical_mb + 1))
    )

    cache.start_monitor(interval=0.01)
    for _ in range(200):
        if cache.get_candles("CRASH500", "1min") == []:
            break
        await asyncio.sleep(0.01)
    else:  # pragma: no cover - diagnóstico
        raise AssertionError("el monitor no evictó a tiempo")

    await cache.stop_monitor()
    await cache.stop_monitor()
    assert cache.figure_cache_size == config.figure_cache_size // 2


async def test_monitor_rejects_double_start() -> None:
    cache = RAMCache(_config())
    cache.start_monitor(interval=3600)
    try:
        with pytest.raises(RuntimeError, match="ya está activo"):
            cache.start_monitor(interval=3600)
    finally:
        await cache.stop_monitor()


def test_monitor_requires_running_loop() -> None:
    cache = RAMCache(_config())
    with pytest.raises(RuntimeError, match="event loop"):
        cache.start_monitor()


def test_monitor_rejects_non_positive_interval() -> None:
    cache = RAMCache(_config())
    with pytest.raises(ValueError, match="positivo"):
        cache.start_monitor(interval=0)
