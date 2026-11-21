"""Determinismo byte-a-byte de las fixtures de T029."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.generate_fixtures import (
    BARS_30_DAYS_5MIN,
    FIXTURES,
    TICKS,
    write_data_fixtures,
    write_snapshot_fixture,
)

DATA_FIXTURES = [
    "boom1000_5min.csv",
    "crash500_ticks.csv",
    "synthetic_evt.csv",
    "synthetic_garch.csv",
    "synthetic_regimes.csv",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", DATA_FIXTURES)
def test_data_fixture_is_committed_and_reproducible(name: str, tmp_path: Path) -> None:
    committed = FIXTURES / name
    assert committed.exists(), f"falta la fixture {name}; ejecuta scripts/generate_fixtures.py"

    write_data_fixtures(tmp_path)

    assert sha256(committed) == sha256(tmp_path / name)


def test_candles_fixture_shape_and_ohlc_validity() -> None:
    candles = pd.read_csv(FIXTURES / "boom1000_5min.csv")

    assert len(candles) == BARS_30_DAYS_5MIN
    assert (candles["High"] >= candles["Low"]).all()
    assert (candles["High"] >= candles[["Open", "Close"]].max(axis=1)).all()
    assert (candles["Low"] <= candles[["Open", "Close"]].min(axis=1)).all()
    assert candles["epoch"].is_monotonic_increasing
    assert candles["epoch"].diff().dropna().eq(300).all()


def test_ticks_fixture_shape() -> None:
    ticks = pd.read_csv(FIXTURES / "crash500_ticks.csv")

    assert len(ticks) == TICKS
    assert ticks["epoch"].is_unique
    assert (ticks["price"] > 0).all()


def test_snapshot_fixture_is_stable_json() -> None:
    path = FIXTURES / "snapshot_boom1000.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    canonical = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert path.read_text(encoding="utf-8") == canonical
    assert payload["generated_at"] == "2026-11-20T12:00:00Z"
    assert payload["instrument"]["symbol"] == "BOOM1000"
    assert set(payload["timeframes"]) == {"5min"}
    analysis = payload["timeframes"]["5min"]
    assert analysis["bars"] == BARS_30_DAYS_5MIN
    assert analysis["events"]["candle"]["n_events"] > 0
    assert analysis["zones"]
    assert analysis["evt"] is not None


def test_snapshot_fixture_is_reproducible(tmp_path: Path) -> None:
    committed = FIXTURES / "snapshot_boom1000.json"

    generated = write_snapshot_fixture(tmp_path)

    assert sha256(committed) == sha256(generated)
