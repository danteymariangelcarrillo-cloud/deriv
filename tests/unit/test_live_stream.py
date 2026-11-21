"""Tests del StreamManager y del algoritmo de eventos en vivo (T019)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from quantlab.config import QuantLabConfig, RAMConfig
from quantlab.data.cache import RAMCache
from quantlab.live.stream_manager import WARMUP_RETURNS, StreamManager
from quantlab.state import InstrumentState, TickRecord

_STOP = object()


# --------------------------------- dobles ---------------------------------


class FakeDerivClient:
    """``subscribe_ticks`` controlado por colas: push manual desde los tests."""

    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue[Any]] = {}
        self.subscribe_calls: list[str] = []
        self.active_endpoint = "wss://fake"
        self.connection_state = SimpleNamespace(latency_ms_p50=12.5)

    def subscribe_ticks(self, symbol: str) -> Any:
        self.subscribe_calls.append(symbol)
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self.queues[symbol] = queue

        async def generator() -> Any:
            while True:
                item = await queue.get()
                if item is _STOP:
                    return
                yield item

        return generator()

    async def push(self, symbol: str, payload: dict[str, Any] | None) -> None:
        await self.queues[symbol].put(payload)

    async def push_tick(self, symbol: str, quote: float, epoch: int) -> None:
        await self.push(
            symbol,
            {"msg_type": "ticks", "tick": {"symbol": symbol, "quote": quote, "epoch": epoch}},
        )


def _state(
    symbol: str = "BOOM1000",
    *,
    direction: str = "up",
    ram: RAMConfig | None = None,
) -> InstrumentState:
    config = QuantLabConfig(ram=ram or RAMConfig())
    return InstrumentState(
        symbol=symbol,
        display_name="Boom 1000" if symbol == "BOOM1000" else "Crash 500",
        direction=direction,  # type: ignore[arg-type]
        analyzer_config=config.analyzer,
        model_config=config.model,
        gate_config=config.gates,
        ram_config=config.ram,
        config_hash=config.hash(),
    )


def _fill_history(state: InstrumentState, prices: list[float]) -> None:
    for index, price in enumerate(prices):
        state.historical_ticks.append(
            TickRecord(epoch=1_700_000_000 + index, price=price, received_at=0.0, sequence=index)
        )


def reference_session(
    prices: list[float],
    direction: str,
    quantile: float = 0.999,
) -> tuple[list[float], int, int]:
    """Implementación escalar independiente del algoritmo legacy."""
    directional: list[float] = []
    events = 0
    since_event = 0
    last_price: float | None = None
    for price in prices:
        is_event = False
        if last_price is not None and last_price > 0:
            signed = (price / last_price - 1.0) * 100.0
            value = max(0.0, signed if direction == "up" else -signed)
            if len(directional) >= 300:
                threshold = float(np.quantile(np.asarray(directional), quantile))
                is_event = value > 0 and value >= threshold
            directional.append(value)
        if is_event:
            events += 1
            since_event = 0
        else:
            since_event += 1
        last_price = price
    return directional, events, since_event


async def wait_until(predicate: Any, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condición no alcanzada a tiempo")


async def wait_for_subscriber(fake: FakeDerivClient, symbol: str) -> None:
    await wait_until(lambda: symbol in fake.queues)


# ---------------------------- algoritmo del tick ----------------------------


async def test_process_live_tick_matches_legacy_within_1e10() -> None:
    prices = [
        100.0,
        100.05,
        99.98,
        100.30,
        100.29,
        100.90,
        100.40,
        99.70,
        99.71,
        100.60,
    ] * 40  # 400 ticks, cruza el warm-up de 300
    state = _state()
    manager = StreamManager(FakeDerivClient(), state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    try:
        for epoch, price in enumerate(prices, start=1_700_000_000):
            await manager._process_live_tick("BOOM1000", {"quote": price, "epoch": epoch})

        expected_values, expected_events, _ = reference_session(
            prices, "up", state.analyzer_config.tick_event_quantile
        )
        assert len(state.directional_returns) == len(expected_values)
        np.testing.assert_allclose(
            np.asarray(state.directional_returns, dtype=float),
            np.asarray(expected_values, dtype=float),
            rtol=0.0,
            atol=1e-10,
        )
        status = await manager.get_status("BOOM1000")
        assert status.processed == len(prices)
        assert status.live_events == expected_events
        assert status.live_events > 0
        assert status.last_tick_epoch == 1_700_000_000 + len(prices) - 1
    finally:
        await manager.stop("BOOM1000")


async def test_process_live_tick_uses_previous_returns_only() -> None:
    state = _state()
    manager = StreamManager(FakeDerivClient(), state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    try:
        for _ in range(WARMUP_RETURNS):
            state.directional_returns.append(0.0)
        await manager._process_live_tick("BOOM1000", {"quote": 100.0, "epoch": 1})
        await manager._process_live_tick("BOOM1000", {"quote": 101.0, "epoch": 2})

        status = await manager.get_status("BOOM1000")
        assert status.live_events == 1
        assert state.directional_returns[-1] == pytest.approx(1.0)
        # El umbral se calculó con 300 ceros previos, no con el 1.0 actual.
        assert status.warmup_complete is True
    finally:
        await manager.stop("BOOM1000")


async def test_directional_events_work_for_crash_down() -> None:
    state = _state("CRASH500", direction="down")
    manager = StreamManager(FakeDerivClient(), state, RAMCache(state.ram_config))
    await manager.start("CRASH500")
    try:
        for _ in range(WARMUP_RETURNS):
            state.directional_returns.append(0.0)
        await manager._process_live_tick("CRASH500", {"quote": 100.0, "epoch": 1})
        await manager._process_live_tick("CRASH500", {"quote": 101.0, "epoch": 2})
        assert (await manager.get_status("CRASH500")).live_events == 0

        await manager._process_live_tick("CRASH500", {"quote": 99.0, "epoch": 3})
        assert (await manager.get_status("CRASH500")).live_events == 1
    finally:
        await manager.stop("CRASH500")


async def test_invalid_prices_are_not_processed() -> None:
    state = _state()
    manager = StreamManager(FakeDerivClient(), state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    try:
        await manager._process_live_tick("BOOM1000", {"quote": 0.0, "epoch": 1})
        await manager._process_live_tick("BOOM1000", {"quote": float("nan"), "epoch": 2})
        await manager._process_live_tick("BOOM1000", {"quote": -5.0, "epoch": 3})

        assert (await manager.get_status("BOOM1000")).processed == 0
    finally:
        await manager.stop("BOOM1000")


# ------------------------------- warm-up y cola -------------------------------


async def test_start_warms_directional_returns_from_history() -> None:
    state = _state()
    history = [100.0 + 0.01 * (index % 11) for index in range(400)]
    _fill_history(state, history)
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))

    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    try:
        status = await manager.get_status("BOOM1000")
        assert status.warmup_complete is True
        assert status.running is True
        assert len(state.directional_returns) == len(history) - 1
        assert len(state.live_ticks) == 0
        assert state.dropped_ticks == 0
        assert fake.subscribe_calls == ["BOOM1000"]
    finally:
        await manager.stop("BOOM1000")

    status = await manager.get_status("BOOM1000")
    assert status.running is False
    assert status.connected is False


async def test_ws_loop_enqueues_and_consumer_processes_ticks() -> None:
    state = _state()
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    try:
        for index, price in enumerate([100.0, 100.4, 99.9]):
            await fake.push_tick("BOOM1000", price, 1_700_000_000 + index)

        await wait_until(
            lambda: (
                len(state.live_ticks) == 3
                and state.live_ticks[-1].price == 99.9
                and manager._runtimes["BOOM1000"].processed == 3
            )
        )
        status = await manager.get_status("BOOM1000")
        assert status.received == 3
        assert status.processed == 3
        assert status.connected is True
        assert status.queue_depth == 0
        assert status.active_endpoint == "wss://fake"
        assert status.latency_ms == 12.5
    finally:
        await manager.stop("BOOM1000")


async def test_incomplete_and_unparseable_messages_are_ignored() -> None:
    state = _state()
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    try:
        await fake.push("BOOM1000", {"msg_type": "ticks", "tick": {"quote": 100.0}})
        await fake.push("BOOM1000", {"msg_type": "ticks", "tick": {"quote": "abc", "epoch": 1}})
        await fake.push("BOOM1000", {"msg_type": "ticks"})
        await fake.push_tick("BOOM1000", 100.0, 1_700_000_000)

        await wait_until(lambda: manager._runtimes["BOOM1000"].received == 1)
        await wait_until(lambda: manager._runtimes["BOOM1000"].processed == 1)
        status = await manager.get_status("BOOM1000")
        assert status.received == 1
        assert status.processed == 1
        assert len(state.live_ticks) == 1
    finally:
        await manager.stop("BOOM1000")


async def test_reconnect_marker_marks_disconnected_then_resumes() -> None:
    state = _state()
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    try:
        await fake.push_tick("BOOM1000", 100.0, 1_700_000_001)
        await wait_until(lambda: state.connected)

        await fake.push("BOOM1000", None)
        await wait_until(lambda: not state.connected)

        await fake.push_tick("BOOM1000", 100.5, 1_700_000_002)
        await wait_until(lambda: state.connected)
        status = await manager.get_status("BOOM1000")
        assert status.received == 2
    finally:
        await manager.stop("BOOM1000")


async def test_dropped_ticks_increase_only_when_queue_is_full() -> None:
    state = _state(ram=RAMConfig(max_live_queue=1_000))
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    consumer = manager._runtimes["BOOM1000"].consumer_task
    assert consumer is not None
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    try:
        for index in range(1_001):
            await fake.push_tick("BOOM1000", 100.0, 1_700_000_000 + index)

        await wait_until(lambda: manager._runtimes["BOOM1000"].received == 1_001)
        status = await manager.get_status("BOOM1000")
        assert status.dropped_ticks == 1
        assert status.queue_depth == 1_000
        assert status.received == 1_001
        assert len(state.live_ticks) == 1_000
    finally:
        await manager.stop("BOOM1000")


# -------------------------------- ciclo de vida --------------------------------


async def test_start_is_idempotent_and_stop_is_safe_twice() -> None:
    state = _state()
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))

    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    first_ws = manager._runtimes["BOOM1000"].ws_task
    await manager.start("BOOM1000")
    assert manager._runtimes["BOOM1000"].ws_task is first_ws
    assert fake.subscribe_calls == ["BOOM1000"]

    await manager.stop("BOOM1000")
    await manager.stop("BOOM1000")
    assert (await manager.get_status("BOOM1000")).running is False


async def test_restart_clears_previous_session() -> None:
    state = _state()
    fake = FakeDerivClient()
    manager = StreamManager(fake, state, RAMCache(state.ram_config))
    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    try:
        await fake.push_tick("BOOM1000", 100.0, 1_700_000_001)
        await wait_until(lambda: manager._runtimes["BOOM1000"].processed == 1)
        await manager.stop("BOOM1000")

        await manager.start("BOOM1000")
        status = await manager.get_status("BOOM1000")
        assert status.received == 0
        assert status.processed == 0
        assert status.live_events == 0
        assert len(state.live_ticks) == 0
        assert len(state.directional_returns) == 0
        assert state.dropped_ticks == 0
    finally:
        await manager.stop("BOOM1000")


async def test_unknown_symbol_is_rejected() -> None:
    manager = StreamManager(FakeDerivClient(), _state(), RAMCache(RAMConfig()))
    try:
        await manager.start("NOPE")
    except KeyError as exc:
        assert "desconocido" in str(exc)
    else:  # pragma: no cover - el test debe fallar si no lanza
        raise AssertionError("start debería rechazar un símbolo desconocido")


async def test_status_from_multiple_instrument_states() -> None:
    boom = _state("BOOM1000")
    crash = _state("CRASH500", direction="down")
    fake = FakeDerivClient()
    manager = StreamManager(fake, {"BOOM1000": boom, "CRASH500": crash}, RAMCache(boom.ram_config))

    await manager.start("BOOM1000")
    await wait_for_subscriber(fake, "BOOM1000")
    try:
        assert (await manager.get_status("BOOM1000")).running is True
        assert (await manager.get_status("CRASH500")).running is False
    finally:
        await manager.stop("BOOM1000")
