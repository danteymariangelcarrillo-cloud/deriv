"""Eventos candidatos y su resumen por temporalidad (T011).

``EventCandidate`` es una excursión extrema detectada por velas o ticks: un
**candidato**, no un spike oficial. El ``event_id`` es un UUID5 determinista de
``symbol|timeframe|kind|event_epoch|event_price`` (data-model §2.3), de modo que
un mismo evento se identifica igual entre recálculos y procesos.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import Field, field_validator, model_validator

from quantlab.domain.types import (
    AnalysisStatus,
    Direction,
    DomainModel,
    EventKind,
    Timeframe,
)
from quantlab.utils import ensure_utc_datetime, generate_deterministic_uuid5

__all__ = [
    "EventCandidate",
    "EventSeries",
    "EventSummary",
    "ThresholdSummary",
    "compute_event_id",
]

#: Máximo de eventos serializados por serie (contracts/snapshot.schema.json).
MAX_EVENTS = 5000

EVENT_LABEL = "candidato de spike"


def _price_token(price: float) -> str:
    """Representación estable de un precio para el nombre del UUID5.

    Se usa ``repr(float(...))`` (equivalente a ``str`` en CPython 3) para que
    ``123.4`` y ``123.40`` produzcan el mismo identificador.
    """
    return repr(float(price))


def compute_event_id(
    symbol: str,
    timeframe: str,
    kind: str,
    event_epoch: int,
    event_price: float,
) -> str:
    """UUID5 determinista del evento (namespace ``event``)."""
    return generate_deterministic_uuid5(
        "event",
        symbol,
        timeframe,
        kind,
        str(int(event_epoch)),
        _price_token(event_price),
    )


class EventCandidate(DomainModel):
    """Excursión extrema detectada sobre velas o ticks."""

    event_id: str | None = None
    symbol: str = Field(min_length=1)
    timeframe: Timeframe
    kind: EventKind
    direction: Direction
    event_epoch: int = Field(ge=0)
    event_price: float = Field(gt=0)
    magnitude_points: float = Field(ge=0)
    magnitude_pct: float = Field(ge=0)
    threshold_points: float
    threshold_basis: str = "rolling_quantile"
    lookback: int = Field(ge=1)
    warmup_ok: bool
    reversal_pct: float | None = None
    label: str = EVENT_LABEL
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("threshold_basis")
    @classmethod
    def _rolling_quantile_only(cls, value: str) -> str:
        if value != "rolling_quantile":
            raise ValueError("threshold_basis sólo admite 'rolling_quantile'")
        return value

    @field_validator("label")
    @classmethod
    def _label_is_candidate(cls, value: str) -> str:
        if value != EVENT_LABEL:
            raise ValueError(f"label debe ser {EVENT_LABEL!r}")
        return value

    @field_validator("computed_at")
    @classmethod
    def _normalise_computed_at(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value)

    @model_validator(mode="after")
    def _fill_and_validate_event_id(self) -> EventCandidate:
        expected = compute_event_id(
            self.symbol,
            self.timeframe,
            self.kind,
            self.event_epoch,
            self.event_price,
        )
        if self.event_id is None:
            object.__setattr__(self, "event_id", expected)
            return self
        try:
            parsed = uuid.UUID(self.event_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"event_id no es un UUID válido: {self.event_id!r}") from exc
        if str(parsed) != self.event_id:
            raise ValueError(f"event_id debe estar en forma canónica: {self.event_id!r}")
        return self


class ThresholdSummary(DomainModel):
    """Rango de umbrales rolling usados por una serie de eventos."""

    min: float | None = None
    median: float | None = None
    max: float | None = None


class EventSeries(DomainModel):
    """Eventos candidatos de un tipo (vela o tick) en una temporalidad."""

    status: AnalysisStatus
    n_events: int = Field(ge=0)
    threshold_quantile: float = Field(gt=0.5, lt=1.0)
    lookback: int = Field(ge=1)
    warmup: int = Field(ge=1)
    events: tuple[EventCandidate, ...] = Field(default=(), max_length=MAX_EVENTS)
    threshold_summary: ThresholdSummary = Field(default_factory=ThresholdSummary)
    note: str = ""

    @model_validator(mode="after")
    def _n_events_covers_serialised(self) -> EventSeries:
        # La lista puede truncarse a MAX_EVENTS, así que n_events (total
        # detectado) puede ser mayor que los eventos serializados.
        if self.n_events < len(self.events):
            raise ValueError(f"n_events={self.n_events} es menor que {len(self.events)} eventos")
        return self


class EventSummary(DomainModel):
    """Serie de eventos por velas y por ticks."""

    candle: EventSeries
    tick: EventSeries
