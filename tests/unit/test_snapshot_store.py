"""Tests de SnapshotStore: swap atómico, versionado y limpieza (T014)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from quantlab.data.quality import DataQualityReport
from quantlab.domain.events import EventSeries, EventSummary
from quantlab.domain.market_data import DEFAULT_INSTRUMENTS
from quantlab.domain.snapshot import DataRange, InstrumentSnapshot, TimeframeAnalysis
from quantlab.services.snapshot_store import SnapshotStore

CONFIG_HASH = "sha256:" + "a" * 64
DATA_VERSION = "sha256:" + "b" * 64


def _snapshot(minute: int = 0) -> InstrumentSnapshot:
    empty = {
        "status": "insufficient",
        "n_events": 0,
        "threshold_quantile": 0.99,
        "lookback": 200,
        "warmup": 50,
    }
    analysis = TimeframeAnalysis(
        timeframe="5min",
        status="ready",
        generated_at=datetime(2026, 11, 20, 12, minute, tzinfo=UTC),
        data_range=DataRange(
            requested_start_epoch=1_699_000_000,
            requested_end_epoch=1_700_000_000,
            start_epoch=1_699_000_060,
            end_epoch=1_699_999_980,
        ),
        bars=100,
        candles_ref="BOOM1000:5min",
        quality=DataQualityReport(
            status="ok",
            expected_bars=100,
            received_bars=100,
            coverage_pct=100.0,
            missing_bars=0,
            gaps_count=0,
            duplicates_removed=0,
            invalid_ohlc=0,
        ),
        events=EventSummary(candle=EventSeries(**empty), tick=EventSeries(**empty)),
    )
    return InstrumentSnapshot(
        generated_at=datetime(2026, 11, 20, 12, minute, tzinfo=UTC),
        config_hash=CONFIG_HASH,
        data_version=DATA_VERSION,
        instrument=DEFAULT_INSTRUMENTS["BOOM1000"],
        source="deriv",
        timeframes={"5min": analysis},
    )


def test_store_starts_empty() -> None:
    store = SnapshotStore()

    assert store.get("BOOM1000") is None
    assert store.get_version("BOOM1000") == 0
    assert store.symbols() == ()
    assert len(store) == 0
    assert "BOOM1000" not in store


def test_swap_increments_version_and_returns_it() -> None:
    store = SnapshotStore()
    first = _snapshot(0)
    second = _snapshot(1)

    assert store.swap("BOOM1000", first) == 1
    assert store.get("BOOM1000") is first
    assert store.get_version("BOOM1000") == 1
    assert store.swap("BOOM1000", second) == 2
    assert store.get("BOOM1000") is second
    assert store.get_version("BOOM1000") == 2


def test_store_tracks_symbols_independently() -> None:
    store = SnapshotStore()
    store.swap("BOOM1000", _snapshot(0))
    store.swap("CRASH500", _snapshot(1))

    assert store.symbols() == ("BOOM1000", "CRASH500")
    assert len(store) == 2
    assert store.get_version("CRASH500") == 1
    assert store.get_version("BOOM1000") == 1


def test_clear_removes_entry_and_version() -> None:
    store = SnapshotStore()
    store.swap("BOOM1000", _snapshot(0))

    store.clear("BOOM1000")

    assert store.get("BOOM1000") is None
    assert store.get_version("BOOM1000") == 0
    # Tras limpiar, el siguiente swap vuelve a empezar en 1.
    assert store.swap("BOOM1000", _snapshot(1)) == 1
    store.clear("NOEXISTE")


def test_swap_validates_arguments() -> None:
    store = SnapshotStore()

    with pytest.raises(ValueError, match="symbol"):
        store.swap("  ", _snapshot())
    with pytest.raises(TypeError, match="InstrumentSnapshot"):
        store.swap("BOOM1000", {"no": "snapshot"})  # type: ignore[arg-type]


def test_concurrent_readers_never_observe_partial_snapshots() -> None:
    store = SnapshotStore()
    snapshots = [_snapshot(minute) for minute in range(3)]
    store.swap("BOOM1000", snapshots[0])
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer() -> None:
        for index in range(300):
            store.swap("BOOM1000", snapshots[index % 3])

    def reader() -> None:
        try:
            while not stop.is_set():
                seen = store.get("BOOM1000")
                assert isinstance(seen, InstrumentSnapshot)
                # El snapshot debe estar completo y coherente, nunca a medias.
                assert seen.config_hash == CONFIG_HASH
                assert set(seen.timeframes) == {"5min"}
                assert seen.timeframes["5min"].bars == 100
        except (AssertionError, TypeError) as exc:  # pragma: no cover - sólo si falla la invariante
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers:
        thread.start()
    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    writer_thread.join()
    stop.set()
    for thread in readers:
        thread.join()

    assert errors == []
    assert store.get_version("BOOM1000") == 301
