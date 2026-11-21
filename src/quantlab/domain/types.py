"""Alias de tipos compartidos por los modelos de dominio (T011-T013).

Los valores replican literalmente los enums de ``data-model.md`` y de
``contracts/snapshot.schema.json``. Al vivir en un único módulo, los modelos
de ``domain/`` no pueden divergir entre sí (por ejemplo, en la lista de
temporalidades o en los estados de análisis).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AnalysisStatus",
    "Direction",
    "DomainModel",
    "EventKind",
    "FeatureCategory",
    "OverallStatus",
    "Source",
    "Stage",
    "Timeframe",
    "ZoneType",
]

Direction = Literal["up", "down"]
Timeframe = Literal["1min", "5min", "15min", "1h", "4h", "1d"]
Source = Literal["deriv", "cache", "fixture"]
EventKind = Literal["candle", "tick"]
ZoneType = Literal["support", "resistance"]
Stage = Literal["in_sample", "oos"]
AnalysisStatus = Literal[
    "pending",
    "computing",
    "ready",
    "insufficient",
    "not_applicable",
    "error",
    "stale",
]
OverallStatus = Literal["ready", "partial", "stale", "error"]
FeatureCategory = Literal[
    "events",
    "zones",
    "evt",
    "volatility",
    "survival",
    "diagnostics",
    "walkforward",
    "data",
]


class DomainModel(BaseModel):
    """Base estricta e inmutable de los modelos de dominio.

    ``extra="forbid"`` impide que un payload con campos desconocidos entre al
    snapshot; ``frozen=True`` bloquea la reasignación de campos y
    ``allow_inf_nan=False`` rechaza ``NaN``/``Inf`` en la entrada (data-model
    §9.3: los valores no calculables se representan como ``null`` + ``reason``,
    nunca como infinitos).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
