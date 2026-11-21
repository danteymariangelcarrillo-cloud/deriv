"""Tests de ``InstrumentState`` (T004)."""

from __future__ import annotations

import asyncio

import pytest

from quantlab.config import TIMEFRAMES, QuantLabConfig, RAMConfig
from quantlab.state import CandleRecord, InstrumentState, TickRecord


def make_state(ram: RAMConfig | None = None) -> InstrumentState:
    config = QuantLabConfig(ram=ram or RAMConfig())
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


def tick(epoch: int) -> TickRecord:
    return TickRecord(epoch=epoch, price=100.0 + epoch, received_at=1.5, sequence=epoch)


def candle(epoch: int, tf: str = "5min") -> CandleRecord:
    return CandleRecord(epoch=epoch, open=1.0, high=2.0, low=0.5, close=1.5, timeframe=tf)


# ----------------------------- construcción -----------------------------


def test_state_wires_config_and_hash() -> None:
    config = QuantLabConfig()
    state = InstrumentState(
        symbol="CRASH500",
        display_name="Crash 500",
        direction="down",
        analyzer_config=config.analyzer,
        model_config=config.model,
        gate_config=config.gates,
        ram_config=config.ram,
        config_hash=config.hash(),
    )

    assert state.config_hash == config.hash()
    assert state.ram_config is config.ram
    assert state.snapshot is None
    assert state.snapshot_version == 0
    assert state.dropped_ticks == 0
    assert state.connected is False
    assert state.active_endpoint == ""
    assert set(state.candles_by_tf) == set(TIMEFRAMES)
    assert isinstance(state.recompute_lock, asyncio.Lock)


@pytest.mark.parametrize(
    ("symbol", "direction", "config_hash"),
    [
        ("", "up", "sha256:x"),
        ("BOOM1000", "sideways", "sha256:x"),
        ("BOOM1000", "up", ""),
    ],
)
def test_state_rejects_invalid_inputs(symbol: str, direction: str, config_hash: str) -> None:
    config = QuantLabConfig()
    with pytest.raises(ValueError):
        InstrumentState(
            symbol=symbol,
            display_name="Boom 1000",
            direction=direction,  # type: ignore[arg-type]
            analyzer_config=config.analyzer,
            model_config=config.model,
            gate_config=config.gates,
            ram_config=config.ram,
            config_hash=config_hash,
        )


def test_state_uses_slots() -> None:
    assert "historical_ticks" in InstrumentState.__slots__
    assert not hasattr(tick(1), "__dict__")
    assert not hasattr(candle(1), "__dict__")


# ------------------------------- buffers --------------------------------


def test_tick_buffers_respect_configured_maxlen() -> None:
    state = make_state(RAMConfig(max_ticks_per_symbol=1_000))

    for epoch in range(1_005):
        state.historical_ticks.append(tick(epoch))

    assert len(state.historical_ticks) == 1_000
    assert state.historical_ticks[0].epoch == 5
    assert state.historical_ticks[-1].epoch == 1_004


def test_candle_buffers_respect_configured_maxlen() -> None:
    state = make_state(RAMConfig(max_candles_per_tf=1_000))

    for epoch in range(1_005):
        state.candles_by_tf["5min"].append(candle(epoch))

    assert len(state.candles_by_tf["5min"]) == 1_000
    assert len(state.candles_by_tf["1h"]) == 0


def test_directional_returns_respect_configured_maxlen() -> None:
    state = make_state(RAMConfig(max_directional_returns=1_000))

    for index in range(1_005):
        state.directional_returns.append(float(index))

    assert len(state.directional_returns) == 1_000
    assert state.directional_returns[0] == 5.0


# ------------------------------ live stream ------------------------------


def test_append_live_tick_drops_only_when_queue_is_full() -> None:
    state = make_state(RAMConfig(max_live_queue=1_000))

    accepted = [state.append_live_tick(tick(epoch)) for epoch in range(1_000)]
    rejected = [state.append_live_tick(tick(epoch)) for epoch in range(1_000, 1_005)]

    assert all(accepted)
    assert not any(rejected)
    assert state.dropped_ticks == 5
    assert len(state.live_ticks) == 1_000
    assert state.live_queue.full()


async def test_recompute_lock_serializes_concurrent_work() -> None:
    state = make_state()
    order: list[str] = []

    async def job(tag: str, delay: float) -> None:
        async with state.recompute_lock:
            order.append(f"{tag}:start")
            await asyncio.sleep(delay)
            order.append(f"{tag}:end")

    await asyncio.gather(job("a", 0.02), job("b", 0.0))

    assert order == ["a:start", "a:end", "b:start", "b:end"]


# ------------------------------ dataframes -------------------------------


def test_get_ticks_df_is_empty_with_columns() -> None:
    frame = make_state().get_ticks_df()

    assert frame.empty
    assert list(frame.columns) == ["epoch", "price", "received_at", "sequence"]


def test_get_ticks_df_merges_history_and_live_sorted_by_epoch() -> None:
    state = make_state()
    state.historical_ticks.append(tick(3))
    state.historical_ticks.append(tick(1))
    state.append_live_tick(tick(2))

    frame = state.get_ticks_df()

    assert list(frame.columns) == ["epoch", "price", "received_at", "sequence"]
    assert frame["epoch"].tolist() == [1, 2, 3]
    assert frame.iloc[1]["price"] == 102.0


def test_get_candles_df_returns_ohlc_sorted_by_epoch() -> None:
    state = make_state()
    state.candles_by_tf["15min"].append(candle(20, "15min"))
    state.candles_by_tf["15min"].append(candle(10, "15min"))

    frame = state.get_candles_df("15min")

    assert list(frame.columns) == ["epoch", "open", "high", "low", "close"]
    assert frame["epoch"].tolist() == [10, 20]


def test_get_candles_df_rejects_unknown_timeframe() -> None:
    with pytest.raises(ValueError, match="temporalidad no válida"):
        make_state().get_candles_df("2min")


def test_ticks_df_records_are_plain_json_serializable_values() -> None:
    state = make_state()
    state.historical_ticks.append(tick(7))

    record = state.get_ticks_df().to_dict("records")[0]

    assert record == {
        "epoch": 7,
        "price": 107.0,
        "received_at": 1.5,
        "sequence": 7,
    }
