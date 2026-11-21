"""Prueba live del StreamManager contra Deriv (opt-in).

Se omite salvo ``QUANTLAB_LIVE=1``; no forma parte de la suite rápida ni escribe
en disco. Ejecutar con::

    QUANTLAB_LIVE=1 uv run pytest tests/unit/test_live_stream_live.py -m live
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from quantlab.config import QuantLabConfig
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_client import DerivClient
from quantlab.live.stream_manager import StreamManager
from quantlab.state import InstrumentState

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("QUANTLAB_LIVE") != "1",
        reason="requiere QUANTLAB_LIVE=1 y acceso a Deriv",
    ),
]


async def test_live_stream_receives_and_processes_ticks() -> None:
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
    client = DerivClient()
    manager = StreamManager(client, state, RAMCache(config.ram))

    await manager.start("BOOM1000")
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            status = await manager.get_status("BOOM1000")
            if status.processed >= 3:
                break
            await asyncio.sleep(0.1)

        status = await manager.get_status("BOOM1000")
        assert status.running is True
        assert status.connected is True
        assert status.processed >= 3
        assert status.received >= status.processed
        assert status.last_tick_epoch is not None
        assert status.active_endpoint
        assert len(state.live_ticks) >= 3
        assert state.dropped_ticks == 0
    finally:
        await manager.stop("BOOM1000")
        await client.close()
