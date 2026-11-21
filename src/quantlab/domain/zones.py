"""Zonas de reacción y su sensibilidad (T012).

Aplica las invariantes de ``001-crash-boom-dashboard/data-model.md`` §3.5:

- ``INV-040``: ``lower <= center <= upper`` y ``upper - lower = 2 * half_width``.
- ``INV-041``: ``evaluated_touches = reactions + failures``.
- ``INV-042``: ``reactions + failures + ambiguous_touches + pending_touches = episodes``.
- ``INV-043``: con ``evaluated_touches == 0`` la tasa, sus IC y los p-valores son
  ``null`` (nunca ``0``).
- ``INV-044``: ``p_null = 0.5`` por defecto (réplica exacta del prototipo); la
  corrección geométrica ``b/(a+b)`` es optativa y debe pasarse explícitamente.

El ``zone_id`` es un UUID5 determinista de
``symbol|timeframe|zone_type|center_rounded|available_from_epoch``; el centro se
redondea a 6 decimales para que pequeñas diferencias de coma flotante no cambien
el identificador.
"""

from __future__ import annotations

import math
import uuid

from pydantic import Field, field_validator, model_validator

from quantlab.domain.types import AnalysisStatus, DomainModel, Stage, ZoneType
from quantlab.utils import generate_deterministic_uuid5

__all__ = ["DEFAULT_P_NULL", "ReactionZone", "ZoneSensitivity", "compute_zone_id"]

#: ``p_null`` por defecto: réplica exacta del prototipo (INV-044).
DEFAULT_P_NULL = 0.5

SENSITIVITY_NOTE = "Excluidos del denominador principal"


def compute_zone_id(
    symbol: str,
    timeframe: str,
    zone_type: str,
    center: float,
    available_from_epoch: int,
) -> str:
    """UUID5 determinista de la zona (namespace ``zone``)."""
    return generate_deterministic_uuid5(
        "zone",
        symbol,
        timeframe,
        zone_type,
        repr(round(float(center), 6)),
        str(int(available_from_epoch)),
    )


class ZoneSensitivity(DomainModel):
    """Tasas alternativas contando ambigüedades/pendientes como fallo."""

    rate_ambiguous_as_failure_pct: float = Field(ge=0.0, le=100.0)
    rate_pending_as_failure_pct: float = Field(ge=0.0, le=100.0)
    note: str = SENSITIVITY_NOTE


class ReactionZone(DomainModel):
    """Zona de reacción candidata con su evidencia estadística."""

    zone_id: str | None = None
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    zone_type: ZoneType
    center: float
    lower: float
    upper: float
    half_width: float = Field(gt=0)
    touch_bars: int = Field(ge=0)
    episodes: int = Field(ge=0)
    evaluated_touches: int = Field(ge=0)
    reactions: int = Field(ge=0)
    failures: int = Field(ge=0)
    ambiguous_touches: int = Field(ge=0)
    pending_touches: int = Field(ge=0)
    reaction_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_upper: float | None = Field(default=None, ge=0.0, le=1.0)
    p_null: float | None = DEFAULT_P_NULL
    p_value_raw: float | None = Field(default=None, ge=0.0, le=1.0)
    p_value_adj: float | None = Field(default=None, ge=0.0, le=1.0)
    reject: bool = False
    median_favourable_atr: float | None = None
    favourable_ci: tuple[float, float] | None = None
    median_adverse_atr: float | None = None
    adverse_ci: tuple[float, float] | None = None
    event_count_in_zone: int = Field(ge=0)
    last_touch_epoch: int | None = None
    available_from_epoch: int = Field(ge=0)
    stage: Stage
    status: AnalysisStatus
    sensitivity: ZoneSensitivity = Field(
        default_factory=lambda: ZoneSensitivity(
            rate_ambiguous_as_failure_pct=0.0,
            rate_pending_as_failure_pct=0.0,
        )
    )
    reason: str | None = None

    @field_validator("favourable_ci", "adverse_ci")
    @classmethod
    def _ci_ordered(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is not None and value[0] > value[1]:
            raise ValueError(f"intervalo de confianza invertido: {value}")
        return value

    @model_validator(mode="after")
    def _geometry(self) -> ReactionZone:
        if not (self.lower <= self.center <= self.upper):
            raise ValueError(
                f"geometría inválida: debe cumplirse lower <= center <= upper "
                f"({self.lower}, {self.center}, {self.upper})"
            )
        if not math.isclose(self.upper - self.lower, 2 * self.half_width, rel_tol=1e-9):
            raise ValueError(
                f"upper - lower ({self.upper - self.lower}) debe ser 2 * half_width "
                f"({2 * self.half_width})"
            )
        return self

    @model_validator(mode="after")
    def _counts(self) -> ReactionZone:
        if self.evaluated_touches != self.reactions + self.failures:
            raise ValueError(
                f"evaluated_touches={self.evaluated_touches} debe ser "
                f"reactions + failures ({self.reactions + self.failures})"
            )
        total = self.reactions + self.failures + self.ambiguous_touches + self.pending_touches
        if total != self.episodes:
            raise ValueError(
                f"reactions + failures + ambiguos + pendientes ({total}) debe ser "
                f"episodes ({self.episodes})"
            )
        return self

    @model_validator(mode="after")
    def _null_when_not_evaluated(self) -> ReactionZone:
        nullable = (
            self.reaction_rate,
            self.ci_lower,
            self.ci_upper,
            self.p_value_raw,
            self.p_value_adj,
        )
        if self.evaluated_touches == 0 and any(value is not None for value in nullable):
            raise ValueError(
                "con evaluated_touches == 0, reaction_rate, IC y p-valores deben ser null"
            )
        return self

    @model_validator(mode="after")
    def _fill_zone_id(self) -> ReactionZone:
        expected = compute_zone_id(
            self.symbol,
            self.timeframe,
            self.zone_type,
            self.center,
            self.available_from_epoch,
        )
        if self.zone_id is None:
            object.__setattr__(self, "zone_id", expected)
            return self
        try:
            parsed = uuid.UUID(self.zone_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"zone_id no es un UUID válido: {self.zone_id!r}") from exc
        if str(parsed) != self.zone_id:
            raise ValueError(f"zone_id debe estar en forma canónica: {self.zone_id!r}")
        return self
