"""Tests de MarketDataService (T009): paginación, OHLC, calidad y ticks."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from quantlab.config import RAMConfig
from quantlab.data.cache import RAMCache
from quantlab.data.market_data_service import MarketDataService
from quantlab.errors import DerivAPIError, ErrorCode
from quantlab.state import CandleRecord

T0 = 1_700_000_000


def _config(**overrides: int) -> RAMConfig:
    base = {
        "max_ticks_per_symbol": 1_000,
        "max_candles_per_tf": 1_000,
        "max_directional_returns": 1_000,
        "max_live_queue": 1_000,
    }
    base.update(overrides)
    return RAMConfig(**base)


def _candle(
    epoch: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
) -> dict[str, float | int]:
    return {"epoch": epoch, "open": open_, "high": high, "low": low, "close": close}


class FakeClient:
    """Cliente Deriv en memoria: pagina velas y devuelve un historial fijo."""

    def __init__(
        self,
        candles: list[dict[str, Any]] | None = None,
        history: object | None = None,
    ) -> None:
        self.candles = candles or []
        self.history = history
        self.payloads: list[dict[str, Any]] = []
        self.calls = 0

    async def request(
        self,
        payload: dict[str, Any],
        expected_types: tuple[str, ...],
        expected_key: str,
    ) -> dict[str, Any]:
        self.calls += 1
        self.payloads.append(payload)
        if payload.get("style") == "candles":
            start = int(payload["start"])
            end = int(payload["end"])
            count = int(payload["count"])
            page = [c for c in self.candles if start <= int(c["epoch"]) <= end][-count:]
            return {"msg_type": "candles", "candles": page}
        return {"msg_type": "history", "history": self.history}


class RecordingLimiter:
    """Sustituto de TokenBucket que solo cuenta adquisiciones."""

    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, tokens: int = 1) -> None:
        self.calls += 1


def _service(
    client: FakeClient,
    limiter: RecordingLimiter | None = None,
) -> tuple[MarketDataService, RAMCache]:
    cache = RAMCache(_config())
    service = MarketDataService(client, cache, limiter or RecordingLimiter())  # type: ignore[arg-type]
    return service, cache


async def test_get_candles_normalizes_columns_and_reports_ok() -> None:
    raw = [_candle(T0), _candle(T0 + 300), _candle(T0 + 600)]
    client = FakeClient(raw)
    service, _ = _service(client)

    df, report = await service.get_candles("CRASH500", "5min", T0, T0 + 600)

    assert list(df.columns) == ["datetime", "epoch", "Open", "High", "Low", "Close"]
    assert df["epoch"].tolist() == [T0, T0 + 300, T0 + 600]
    assert str(df["datetime"].dt.tz) == "UTC"
    assert df["Open"].tolist() == [100.0, 100.0, 100.0]
    assert report.status == "ok"
    assert (report.expected_bars, report.received_bars) == (3, 3)
    assert report.coverage_pct == 100.0
    assert (report.missing_bars, report.gaps_count) == (0, 0)
    assert (report.first_epoch, report.last_epoch) == (T0, T0 + 600)
    assert report.errors == []
    assert "Cobertura completa" in report.notes[0]


async def test_get_candles_paginates_backwards_until_start() -> None:
    raw = [_candle(T0 + i * 60) for i in range(10)]
    client = FakeClient(raw)
    limiter = RecordingLimiter()
    service, _ = _service(client, limiter)

    df, report = await service.get_candles("BOOM1000", "1min", T0, T0 + 9 * 60, limit=4)

    assert df["epoch"].tolist() == [T0 + i * 60 for i in range(10)]
    assert len(df) == 10
    assert report.status == "ok"
    assert report.coverage_pct == 100.0
    assert client.calls == 3
    assert limiter.calls == 3
    ends = [int(payload["end"]) for payload in client.payloads]
    assert ends[0] == T0 + 9 * 60
    assert ends == sorted(ends, reverse=True)
    assert len(set(ends)) == len(ends)
    assert all(int(payload["count"]) == 4 for payload in client.payloads)
    assert all(int(payload["granularity"]) == 60 for payload in client.payloads)


def test_clean_candles_dedupes_invalid_and_clips_range() -> None:
    raw = [
        _candle(T0),
        _candle(T0 + 300, close=102.0),
        _candle(T0 + 300, close=105.0, high=106.0),
        _candle(T0 + 600, open_=100.0, high=98.0, low=99.0, close=100.0),
        _candle(T0 + 900),
        _candle(T0 + 1200),
        {"epoch": "no-numérico", "open": 1, "high": 1, "low": 1, "close": 1},
    ]

    df = MarketDataService._clean_candles(raw, T0, T0 + 900)

    assert df["epoch"].tolist() == [T0, T0 + 300, T0 + 900]
    kept = df.loc[df["epoch"] == T0 + 300].iloc[0]
    assert kept["Close"] == 105.0  # keep="last"
    duplicates, invalid = MarketDataService._audit_raw(raw, T0, T0 + 900)
    assert (duplicates, invalid) == (1, 1)


def test_clean_candles_rejects_missing_fields() -> None:
    with pytest.raises(ValueError, match="campos obligatorios"):
        MarketDataService._clean_candles([{"epoch": T0, "open": 1.0}], T0, T0 + 60)


async def test_quality_report_counts_gaps_and_fails_below_90() -> None:
    raw = [
        _candle(T0),
        _candle(T0 + 300, close=102.0),
        _candle(T0 + 300, close=105.0, high=106.0),
        _candle(T0 + 600, high=98.0, low=99.0),
        _candle(T0 + 900),
    ]
    service, _ = _service(FakeClient(raw))

    _, report = await service.get_candles("CRASH500", "5min", T0, T0 + 900)

    assert report.expected_bars == 4
    assert report.received_bars == 3
    assert report.coverage_pct == 75.0
    assert report.missing_bars == 1
    assert report.gaps_count == 1
    assert (report.duplicates_removed, report.invalid_ohlc) == (1, 1)
    assert report.status == "fail"


async def test_quality_report_warns_on_invalid_ohlc_with_full_coverage() -> None:
    raw = [
        _candle(T0),
        _candle(T0 + 30, high=98.0, low=99.0),
        _candle(T0 + 60),
    ]
    service, _ = _service(FakeClient(raw))

    _, report = await service.get_candles("CRASH500", "1min", T0, T0 + 60)

    assert report.received_bars == 2
    assert report.coverage_pct == 100.0
    assert report.invalid_ohlc == 1
    assert report.status == "warn"


async def test_quality_report_warns_between_90_and_98() -> None:
    raw = [_candle(T0 + i * 60) for i in range(20) if i != 10]
    service, _ = _service(FakeClient(raw))

    _, report = await service.get_candles("BOOM1000", "1min", T0, T0 + 19 * 60)

    assert report.received_bars == 19
    assert report.expected_bars == 20
    assert report.coverage_pct == 95.0
    assert report.status == "warn"


async def test_get_candles_empty_returns_fail_report_with_error() -> None:
    service, _ = _service(FakeClient([]))

    df, report = await service.get_candles("CRASH500", "1min", T0, T0 + 60)

    assert df.empty
    assert list(df.columns) == ["datetime", "epoch", "Open", "High", "Low", "Close"]
    assert report.status == "fail"
    assert report.received_bars == 0
    assert report.coverage_pct == 0.0
    assert report.first_epoch is None
    assert report.stale_seconds is None
    assert [error.code for error in report.errors] == [ErrorCode.DATA_EMPTY]


async def test_get_candles_validates_arguments() -> None:
    service, _ = _service(FakeClient([]))

    with pytest.raises(ValueError, match="symbol"):
        await service.get_candles("  ", "1min", T0, T0 + 60)
    with pytest.raises(ValueError, match="timeframe"):
        await service.get_candles("CRASH500", "2min", T0, T0 + 60)
    with pytest.raises(ValueError, match="limit"):
        await service.get_candles("CRASH500", "1min", T0, T0 + 60, limit=0)
    with pytest.raises(ValueError, match="limit"):
        await service.get_candles("CRASH500", "1min", T0, T0 + 60, limit=1001)
    with pytest.raises(ValueError, match="mayor"):
        await service.get_candles("CRASH500", "1min", T0 + 60, T0)


async def test_get_candles_rejects_non_list_response() -> None:
    class BadClient(FakeClient):
        async def request(self, payload, expected_types, expected_key):  # type: ignore[no-untyped-def]
            return {"msg_type": "candles", "candles": None}

    service, _ = _service(BadClient())
    with pytest.raises(DerivAPIError, match="candles"):
        await service.get_candles("CRASH500", "1min", T0, T0 + 60)


async def test_get_tick_history_normalizes_and_filters_start() -> None:
    history = {
        "times": [T0 + 3, T0 + 1, T0 + 2, T0 + 2],
        "prices": [130.0, 110.0, 120.0, 125.0],
    }
    service, _ = _service(FakeClient(history=history))

    df = await service.get_tick_history("BOOM1000", count=10)

    assert list(df.columns) == ["epoch", "price", "datetime", "sequence"]
    assert df["epoch"].tolist() == [T0 + 1, T0 + 2, T0 + 3]
    assert df["price"].tolist() == [110.0, 125.0, 130.0]
    assert df["sequence"].tolist() == [0, 1, 2]
    assert df["datetime"].iloc[0].tzinfo is not None

    filtered = await service.get_tick_history("BOOM1000", count=10, start_epoch=T0 + 2)
    assert filtered["epoch"].tolist() == [T0 + 2, T0 + 3]
    assert filtered["sequence"].tolist() == [0, 1]


async def test_get_tick_history_payload_has_no_start_and_validates_count() -> None:
    client = FakeClient(history={"times": [], "prices": []})
    service, _ = _service(client)

    empty = await service.get_tick_history("CRASH500")

    assert empty.empty
    assert list(empty.columns) == ["epoch", "price", "datetime", "sequence"]
    assert "start" not in client.payloads[0]
    assert client.payloads[0]["end"] == "latest"
    with pytest.raises(ValueError, match="count"):
        await service.get_tick_history("CRASH500", count=5001)
    with pytest.raises(ValueError, match="start_epoch"):
        await service.get_tick_history("CRASH500", start_epoch=-1)


async def test_get_tick_history_rejects_invalid_history() -> None:
    service, _ = _service(FakeClient(history={"prices": [1.0]}))
    with pytest.raises(DerivAPIError, match="times"):
        await service.get_tick_history("CRASH500")


async def test_warm_cache_fills_cache_skips_and_forces(monkeypatch: pytest.MonkeyPatch) -> None:
    now = T0 + 10_000
    monkeypatch.setattr(MarketDataService, "_now", staticmethod(lambda: now))
    raw = [_candle(now - 600), _candle(now - 300), _candle(now - 60)]
    client = FakeClient(raw)
    service, cache = _service(client)

    result = await service.warm_cache("CRASH500", ["1min", "5min"])

    assert result == {"1min": 3, "5min": 3}
    assert [c.epoch for c in cache.get_candles("CRASH500", "1min")] == [
        now - 600,
        now - 300,
        now - 60,
    ]
    assert all(isinstance(c, CandleRecord) for c in cache.get_candles("CRASH500", "5min"))
    calls_after_warm = client.calls
    assert calls_after_warm == 2

    starts = {int(payload["start"]) for payload in client.payloads}
    assert now - 30 * 86_400 in starts
    assert now - 90 * 86_400 in starts

    again = await service.warm_cache("CRASH500", ["1min", "5min"])
    assert again == {"1min": 3, "5min": 3}
    assert client.calls == calls_after_warm

    await service.warm_cache("CRASH500", ["1min"], force=True)
    assert client.calls == calls_after_warm + 1
    assert len(cache.get_candles("CRASH500", "1min")) == 3


async def test_warm_cache_writes_no_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(MarketDataService, "_now", staticmethod(lambda: T0 + 10_000))
    monkeypatch.chdir(tmp_path)
    service, _ = _service(FakeClient([_candle(T0 + 9_000)], history={"times": [], "prices": []}))

    await service.warm_cache("BOOM1000", ["1min"])
    await service.get_tick_history("BOOM1000")

    assert list(tmp_path.iterdir()) == []


async def test_warm_cache_validates_timeframes() -> None:
    service, _ = _service(FakeClient([]))
    with pytest.raises(ValueError, match="timeframes"):
        await service.warm_cache("CRASH500", [])
    with pytest.raises(ValueError, match="timeframe"):
        await service.warm_cache("CRASH500", ["2min"])


async def test_stale_seconds_uses_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MarketDataService, "_now", staticmethod(lambda: T0 + 900))
    service, _ = _service(FakeClient([_candle(T0)]))

    _, report = await service.get_candles("CRASH500", "1min", T0, T0)

    assert report.stale_seconds == 900
    assert pd.api.types.is_integer_dtype(pd.Series([report.stale_seconds]))
