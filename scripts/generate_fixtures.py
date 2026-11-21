"""Genera las fixtures deterministas de los tests (T029).

Uso::

    uv run python scripts/generate_fixtures.py

Escribe únicamente en ``tests/fixtures/`` (artefactos de test); no toca la caché
de runtime ni ningún otro directorio. Todas las series usan
``np.random.default_rng(42)`` y los CSV se serializan con ``%.10g`` para que dos
ejecuciones produzcan bytes idénticos.

Nota: el proyecto no incluye ``pyarrow``/``fastparquet`` (dependencia extra no
declarada en ``pyproject.toml``), así que las velas se guardan como
``boom1000_5min.csv``. ``load_candles`` en los tests acepta también un
``boom1000_5min.parquet`` si algún día se genera con pyarrow.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SEED = 42
EPOCH0 = 1_700_000_000
BARS_30_DAYS_5MIN = 25_920
TICKS = 5_000
FIXED_NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


# ------------------------------- generadores -------------------------------


def make_candles(n: int = BARS_30_DAYS_5MIN) -> pd.DataFrame:
    """30 días de velas 5min con dos regímenes de volatilidad."""
    rng = np.random.default_rng(SEED)
    split = n // 2
    vol = np.concatenate([np.full(split, 0.0025), np.full(n - split, 0.006)])
    close = 1000.0 * np.exp(np.cumsum(rng.normal(0.0, vol)))
    open_ = np.concatenate([[1000.0], close[:-1]])
    wick = np.abs(rng.normal(0.0, 0.0012, n))
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    epoch = EPOCH0 + np.arange(n, dtype=np.int64) * 300
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(epoch, unit="s", utc=True),
            "epoch": epoch,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
        }
    )


def make_ticks(n: int = TICKS) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    price = 1000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.0004, n)))
    epoch = EPOCH0 + np.arange(n, dtype=np.int64)
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(epoch, unit="s", utc=True),
            "epoch": epoch,
            "price": price,
        }
    )


def make_evt_returns(n: int = 5000) -> np.ndarray:
    """GPD(xi=0.2, sigma=1.5) desplazada para que el umbral caiga en la cola."""
    from scipy import stats

    rng = np.random.default_rng(SEED)
    return 0.005 + stats.genpareto.rvs(0.2, scale=1.5, size=n, random_state=rng)


def make_garch_returns(n: int = 5000) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    omega, alpha, beta = 1e-7, 0.08, 0.91
    variance = np.empty(n)
    variance[0] = omega / (1.0 - alpha - beta)
    shocks = rng.normal(0.0, 1.0, n)
    for i in range(1, n):
        variance[i] = (
            omega + alpha * (shocks[i - 1] ** 2) * variance[i - 1] + beta * variance[i - 1]
        )
    return shocks * np.sqrt(variance) * 100.0


def make_regime_returns(n: int = 5000) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    split = n // 2
    return (
        np.concatenate(
            [
                rng.normal(0.0, np.sqrt(4e-4), split),
                rng.normal(0.0, np.sqrt(1.2e-3), n - split),
            ]
        )
        * 100.0
    )


# --------------------------------- escritura --------------------------------


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, float_format="%.10g", lineterminator="\n")


def write_data_fixtures(output: Path = FIXTURES) -> list[Path]:
    """Escribe velas, ticks y las tres series sintéticas; devuelve las rutas."""
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    candles_path = output / "boom1000_5min.csv"
    _write_csv(make_candles(), candles_path)
    written.append(candles_path)

    try:  # parquet sólo si el entorno lo soporta (no es dependencia del proyecto)
        import pyarrow  # noqa: F401

        parquet_path = output / "boom1000_5min.parquet"
        make_candles().to_parquet(parquet_path, index=False)
        written.append(parquet_path)
    except ImportError:
        pass

    ticks_path = output / "crash500_ticks.csv"
    _write_csv(make_ticks(), ticks_path)
    written.append(ticks_path)

    for name, values in (
        ("synthetic_evt.csv", make_evt_returns()),
        ("synthetic_garch.csv", make_garch_returns()),
        ("synthetic_regimes.csv", make_regime_returns()),
    ):
        path = output / name
        _write_csv(pd.DataFrame({"return": values}), path)
        written.append(path)
    return written


async def _build_snapshot() -> object:
    from quantlab.analytics.engine import AnalyticsEngine
    from quantlab.config import QuantLabConfig
    from quantlab.data.cache import RAMCache
    from quantlab.data.quality import DataQualityReport
    from quantlab.services.feature_registry import FeatureRegistry
    from quantlab.services.snapshot_service import SnapshotService
    from quantlab.services.snapshot_store import SnapshotStore
    from quantlab.state import InstrumentState

    candles = make_candles()
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

    class FixtureMarketData:
        async def get_candles(self, symbol, timeframe, start_epoch, end_epoch):
            report = DataQualityReport(
                status="ok",
                expected_bars=len(candles),
                received_bars=len(candles),
                coverage_pct=100.0,
                missing_bars=0,
                gaps_count=0,
                duplicates_removed=0,
                invalid_ohlc=0,
                first_epoch=int(candles["epoch"].iloc[0]),
                last_epoch=int(candles["epoch"].iloc[-1]),
            )
            return candles.copy(), report

    service = SnapshotService(
        SnapshotStore(),
        FeatureRegistry(state.gate_config),
        FixtureMarketData(),  # type: ignore[arg-type]
        AnalyticsEngine(state.gate_config),
        RAMCache(state.ram_config),
        states={state.symbol: state},
        clock=lambda: FIXED_NOW,
    )
    return await service.build_snapshot("BOOM1000", timeframes=["5min"])


def write_snapshot_fixture(output: Path = FIXTURES) -> Path:
    """Construye el snapshot con el motor real y lo serializa de forma estable."""
    snapshot = asyncio.run(_build_snapshot())
    payload = snapshot.model_dump(mode="json")  # type: ignore[attr-defined]
    path = output / "snapshot_boom1000.json"
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def verify_legacy_hash() -> None:
    """Comprueba que ``legacy_sha256.json`` sigue presente y es legible."""
    path = FIXTURES / "legacy_sha256.json"
    if not path.exists():
        raise SystemExit(f"Falta {path}; T000 debe haberlo generado.")
    json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    include_snapshot = "--no-snapshot" not in args
    verify_legacy_hash()
    written = write_data_fixtures()
    if include_snapshot:
        written.append(write_snapshot_fixture())
    for path in written:
        print(f"escrito {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
