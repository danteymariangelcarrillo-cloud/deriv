"""Pruebas live de MarketDataService contra Deriv (opt-in).

Se omiten salvo ``QUANTLAB_LIVE=1``; no forman parte de la suite rápida ni
escriben en disco. Ejecutar con::

    QUANTLAB_LIVE=1 uv run pytest tests/unit/test_market_data_live.py -m live
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from quantlab.config import RAMConfig
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_client import DerivClient
from quantlab.data.market_data_service import MarketDataService
from quantlab.data.rate_limiter import TokenBucket

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("QUANTLAB_LIVE") != "1",
        reason="requiere QUANTLAB_LIVE=1 y acceso a Deriv",
    ),
]


async def test_live_crash500_candles_and_ticks() -> None:
    client = DerivClient(timeout=20.0)
    service = MarketDataService(client, RAMCache(RAMConfig()), TokenBucket(30, 2.5))
    end_epoch = int(datetime.now(UTC).timestamp())
    start_epoch = end_epoch - 600
    try:
        df, report = await service.get_candles("CRASH500", "1min", start_epoch, end_epoch, limit=60)
        assert list(df.columns) == ["datetime", "epoch", "Open", "High", "Low", "Close"]
        assert str(df["datetime"].dt.tz) == "UTC"
        assert report.status in {"ok", "warn", "fail"}
        assert report.expected_bars == 11
        assert report.received_bars == len(df)

        ticks = await service.get_tick_history("BOOM1000", count=50)
        assert list(ticks.columns) == ["epoch", "price", "datetime", "sequence"]
        assert ticks.empty or ticks["epoch"].is_monotonic_increasing
    finally:
        await client.close()
