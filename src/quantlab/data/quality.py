"""Calidad de datos de velas (T010).

Define el modelo ``DataQualityReport`` del contrato y la función pura
``calculate_quality`` que calcula cobertura, huecos, duplicados, OHLC inválidos
y antigüedad. ``MarketDataService`` (T009) delega aquí para que los umbrales y
la semántica de ``status`` tengan una única definición.

Reglas (``specs/001-crash-boom-dashboard/data-model.md §3.6``):

- ``coverage_pct = 100 * received_bars / expected_bars``, acotada a ``[0, 100]``
  y 0 cuando ``expected_bars == 0`` (INV-050).
- ``ok`` solo con cobertura ≥ 98 %, sin huecos y sin OHLC inválidos (INV-051).
- ``warn`` con cobertura en ``[90, 98)``, huecos o OHLC inválidos (INV-052).
- ``fail`` con cobertura < 90 %, sin barras o datos que exceden la antigüedad
  máxima cuando el llamador la informa (INV-053).

La función no escribe en disco ni muta el ``DataFrame`` recibido.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Final, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from quantlab.config import TIMEFRAMES
from quantlab.errors import DataError, ErrorCode, ErrorRecord

__all__ = [
    "CRITICAL_GAP_MIN_BARS",
    "CRITICAL_GAP_RATIO",
    "GAP_FACTOR",
    "GRANULARITY_SECONDS",
    "OK_COVERAGE_PCT",
    "WARN_COVERAGE_PCT",
    "DataQualityReport",
    "QualityStatus",
    "calculate_quality",
]

QualityStatus = Literal["ok", "warn", "fail"]

#: Segundos por temporalidad; coincide con la granularidad que pide Deriv.
GRANULARITY_SECONDS: Final[dict[str, int]] = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "1h": 3600,
    "4h": 14_400,
    "1d": 86_400,
}

#: Umbrales de estado del contrato (INV-051/052/053).
OK_COVERAGE_PCT: Final[float] = 98.0
WARN_COVERAGE_PCT: Final[float] = 90.0

#: Un hueco existe cuando el salto entre barras supera 1,5 granularidades.
GAP_FACTOR: Final[float] = 1.5

#: Un hueco se considera crítico si falta más del 2 % de las barras esperadas
#: (con un mínimo absoluto de 3 barras). El contrato no fija el porcentaje; se
#: documenta como la definición operativa de T009/T010.
CRITICAL_GAP_RATIO: Final[float] = 0.02
CRITICAL_GAP_MIN_BARS: Final[int] = 3

_CANDLE_COLUMNS: Final[tuple[str, ...]] = ("epoch", "open", "high", "low", "close")


class DataQualityReport(BaseModel):
    """Calidad de una serie de velas para un símbolo y temporalidad.

    Los campos obligatorios del schema (``status``, conteos, ``errors`` y
    ``notes``) se serializan siempre; ``first_epoch``, ``last_epoch`` y
    ``stale_seconds`` son ``null`` cuando no hay datos.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: QualityStatus
    expected_bars: int = Field(ge=0)
    received_bars: int = Field(ge=0)
    coverage_pct: float = Field(ge=0.0, le=100.0)
    missing_bars: int = Field(ge=0)
    gaps_count: int = Field(ge=0)
    duplicates_removed: int = Field(ge=0)
    invalid_ohlc: int = Field(ge=0)
    first_epoch: int | None = None
    last_epoch: int | None = None
    stale_seconds: int | None = None
    dropped_ticks: int = Field(default=0, ge=0)
    errors: list[ErrorRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _is_int(value: object) -> bool:
    """``bool`` es subclase de ``int``; no debe aceptarse como conteo."""
    return isinstance(value, int) and not isinstance(value, bool)


def _require_int(name: str, value: object, *, minimum: int = 0) -> int:
    if not _is_int(value) or value < minimum:  # type: ignore[operator]
        raise ValueError(f"{name} debe ser un entero >= {minimum}")
    return value  # type: ignore[return-value]


def _normalise_candles(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Normaliza OHLC, deduplica por epoch y descarta filas incoherentes.

    Acepta columnas en minúsculas (formato crudo de Deriv) o capitalizadas
    (formato de ``MarketDataService``). Devuelve ``(df_limpio, duplicados,
    inválidos)``; un ``DataFrame`` vacío se devuelve sin exigir columnas.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df debe ser un pandas.DataFrame")
    if df.empty:
        return pd.DataFrame(columns=_CANDLE_COLUMNS), 0, 0

    aliases: dict[object, str] = {}
    for column in df.columns:
        canonical = str(column).lower()
        if canonical not in _CANDLE_COLUMNS:
            continue
        if canonical in aliases.values():
            raise ValueError(f"Columna OHLC duplicada: {canonical!r}")
        aliases[column] = canonical

    work = df.rename(columns=aliases)
    missing = [column for column in _CANDLE_COLUMNS if column not in work.columns]
    if missing:
        raise ValueError(f"Faltan columnas obligatorias para calidad: {missing}")

    work = work.loc[:, list(_CANDLE_COLUMNS)].copy()
    for column in _CANDLE_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")

    # NaN/Inf en OHLC se cuantifican como inválidos (INV-014). Un epoch no
    # numérico no es un OHLC inválido: simplemente no identifica una barra.
    ohlc_finite = np.isfinite(work.loc[:, ["open", "high", "low", "close"]]).all(axis=1)
    epoch_finite = np.isfinite(work["epoch"])
    early_invalid = int((~ohlc_finite & epoch_finite).sum())
    work = work.loc[ohlc_finite & epoch_finite]
    if work.empty:
        return pd.DataFrame(columns=_CANDLE_COLUMNS), 0, early_invalid
    work["epoch"] = work["epoch"].astype("int64")

    deduped = work.drop_duplicates(subset=["epoch"], keep="last")
    duplicates = len(work) - len(deduped)
    deduped = deduped.sort_values("epoch")

    valid = (
        (deduped["high"] >= deduped["low"])
        & (deduped["high"] >= deduped[["open", "close"]].max(axis=1))
        & (deduped["low"] <= deduped[["open", "close"]].min(axis=1))
    )
    invalid = early_invalid + int((~valid).sum())
    return deduped.loc[valid].reset_index(drop=True), duplicates, invalid


def calculate_quality(
    df: pd.DataFrame,
    timeframe: str,
    expected_bars: int,
    *,
    duplicates_removed: int | None = None,
    invalid_ohlc: int | None = None,
    dropped_ticks: int = 0,
    errors: Sequence[ErrorRecord] | None = None,
    notes: Sequence[str] | None = None,
    now: int | None = None,
    max_stale_seconds: int | None = None,
    symbol: str | None = None,
) -> DataQualityReport:
    """Construye el reporte de calidad de una serie de velas.

    Args:
        df: velas con ``epoch`` y OHLC (minúsculas o capitalizadas). Puede
            contener duplicados y filas inválidas; se cuentan y descartan aquí.
        timeframe: una de ``TIMEFRAMES``; determina la granularidad de los
            huecos.
        expected_bars: barras esperadas para el rango solicitado, ``>= 0``.
        duplicates_removed: conteo ya auditado por el llamador (p. ej. sobre el
            payload crudo de Deriv). Si es ``None`` se detecta en ``df``.
        invalid_ohlc: conteo ya auditado de OHLC inválidos. Si es ``None`` se
            detecta en ``df``.
        dropped_ticks: ticks descartados por el stream en vivo.
        errors: errores previos que deben conservarse en el reporte.
        notes: notas previas (p. ej. truncado de paginación).
        now: epoch de referencia para ``stale_seconds``; por defecto la hora
            actual. Inyectarlo mantiene los tests deterministas.
        max_stale_seconds: antigüedad máxima permitida. Si se informa y el
            último dato la excede, ``status`` pasa a ``fail`` (INV-053). Por
            defecto no se evalúa: una descarga histórica no es "stale".
        symbol: solo para el contexto de los errores ``DATA_EMPTY``/``DATA_STALE``.

    Raises:
        TypeError: si ``df`` no es un ``DataFrame``.
        ValueError: temporalidad, ``expected_bars`` o conteos inválidos; o
            columnas obligatorias ausentes en un ``DataFrame`` no vacío.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe debe ser uno de {list(TIMEFRAMES)}")
    expected = _require_int("expected_bars", expected_bars)
    dropped = _require_int("dropped_ticks", dropped_ticks)
    max_stale = (
        None if max_stale_seconds is None else _require_int("max_stale_seconds", max_stale_seconds)
    )
    now_epoch = int(time.time()) if now is None else _require_int("now", now)
    duplicates_given = (
        None
        if duplicates_removed is None
        else _require_int("duplicates_removed", duplicates_removed)
    )
    invalid_given = None if invalid_ohlc is None else _require_int("invalid_ohlc", invalid_ohlc)

    clean, detected_duplicates, detected_invalid = _normalise_candles(df)
    duplicates = detected_duplicates if duplicates_given is None else duplicates_given
    invalid = detected_invalid if invalid_given is None else invalid_given

    granularity = GRANULARITY_SECONDS[timeframe]
    received = len(clean)
    missing = max(0, expected - received)
    coverage_exact = 0.0 if expected == 0 else min(100.0, max(0.0, received / expected * 100.0))
    coverage = round(coverage_exact, 2)

    gaps_count = 0
    missing_in_gaps = 0
    critical_gaps = 0
    if received > 1:
        diffs = clean["epoch"].diff().dropna()
        gaps = diffs[diffs > GAP_FACTOR * granularity]
        gaps_count = len(gaps)
        if gaps_count:
            per_gap = gaps // granularity - 1
            missing_in_gaps = int(per_gap.clip(lower=0).sum())
            critical_threshold = max(CRITICAL_GAP_MIN_BARS, expected * CRITICAL_GAP_RATIO)
            critical_gaps = int((per_gap > critical_threshold).sum())

    first_epoch = int(clean["epoch"].iloc[0]) if received else None
    last_epoch = int(clean["epoch"].iloc[-1]) if received else None
    stale_seconds = max(0, now_epoch - last_epoch) if last_epoch is not None else None
    stale_failure = (
        stale_seconds is not None and max_stale is not None and stale_seconds > max_stale
    )

    report_errors = list(errors or [])
    report_notes = list(notes or [])
    context = {"timeframe": timeframe}
    if symbol is not None:
        context["symbol"] = symbol

    if received == 0:
        status: QualityStatus = "fail"
        report_notes.append("No se recibieron velas válidas para el rango solicitado.")
        report_errors.append(
            DataError(
                "No se recibieron velas válidas para esta temporalidad",
                code=ErrorCode.DATA_EMPTY,
                context=context,
            ).to_error_record()
        )
    else:
        if duplicates:
            report_notes.append(f"{duplicates} velas duplicadas eliminadas.")
        if invalid:
            report_notes.append(f"{invalid} velas con OHLC inválido descartadas.")
        if gaps_count:
            report_notes.append(f"{gaps_count} huecos detectados; faltan {missing_in_gaps} barras.")
            if critical_gaps:
                report_notes.append(
                    f"{critical_gaps} hueco(s) crítico(s) por superar el "
                    f"{CRITICAL_GAP_RATIO:.0%} de las barras esperadas."
                )
        if stale_failure:
            report_notes.append(f"Datos desactualizados: {stale_seconds}s > {max_stale}s.")
            report_errors.append(
                DataError(
                    f"Los datos están desactualizados ({stale_seconds}s)",
                    code=ErrorCode.DATA_STALE,
                    context={
                        **context,
                        "stale_seconds": stale_seconds,
                        "max_stale_seconds": max_stale,
                    },
                ).to_error_record()
            )
        if coverage < 100.0 and not gaps_count:
            report_notes.append(f"Cobertura {coverage:.2f}% ({received}/{expected} barras).")
        if coverage == 100.0 and not duplicates and not invalid and not gaps_count:
            report_notes.append("Cobertura completa, sin huecos ni duplicados.")

        if stale_failure or coverage_exact < WARN_COVERAGE_PCT:
            status = "fail"
        elif coverage_exact < OK_COVERAGE_PCT or gaps_count or invalid:
            status = "warn"
        else:
            status = "ok"

    return DataQualityReport(
        status=status,
        expected_bars=expected,
        received_bars=received,
        coverage_pct=coverage,
        missing_bars=missing,
        gaps_count=gaps_count,
        duplicates_removed=duplicates,
        invalid_ohlc=invalid,
        first_epoch=first_epoch,
        last_epoch=last_epoch,
        stale_seconds=stale_seconds,
        dropped_ticks=dropped,
        errors=report_errors,
        notes=report_notes,
    )
