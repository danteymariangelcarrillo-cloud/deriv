"""Modelos del snapshot inmutable de un instrumento (T013).

``InstrumentSnapshot`` es la unidad que consume la API y el WebSocket: se
construye una vez (``SnapshotService``, T016), se intercambia de forma atómica en
``SnapshotStore`` (T014) y nunca se muta (INV-M03). Todos los modelos heredan de
``DomainModel`` (``frozen=True``, ``extra="forbid"``, sin ``NaN``/``Inf``) y las
colecciones son tuplas, de modo que tampoco se pueden alterar por referencia.

Los nombres y campos replican ``contracts/snapshot.schema.json``. La agregación
de ``overall_status`` se documenta en ``aggregate_overall_status``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from quantlab.config import TIMEFRAMES, GateConfig
from quantlab.data.quality import DataQualityReport
from quantlab.domain.events import EventSummary
from quantlab.domain.market_data import Instrument
from quantlab.domain.results import (
    ACFResult,
    ConditionalResult,
    DiagnosticsResult,
    EVTResult,
    GARCHResult,
    RegimeResult,
    SurvivalResult,
    WalkForwardResult,
)
from quantlab.domain.types import (
    AnalysisStatus,
    DomainModel,
    FeatureCategory,
    OverallStatus,
    Source,
    Stage,
    Timeframe,
)
from quantlab.domain.zones import ReactionZone
from quantlab.errors import ErrorRecord
from quantlab.utils import ensure_utc_datetime, generate_deterministic_uuid5

__all__ = [
    "AnalysisConfig",
    "DataRange",
    "FeatureStatus",
    "InstrumentSnapshot",
    "RuntimeConfig",
    "SnapshotConfig",
    "TimeframeAnalysis",
    "aggregate_overall_status",
]

SEMVER_PATTERN = r"^\d+\.\d+\.\d+(-[a-z0-9]+)?$"
_CONFIG_SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"


def _iso_z(value: datetime) -> str:
    """ISO-8601 en UTC con sufijo ``Z`` (data-model §9.3)."""
    return ensure_utc_datetime(value).isoformat().replace("+00:00", "Z")


class AnalysisConfig(DomainModel):
    """Sección ``analysis`` del snapshot (parámetros efectivos de cálculo).

    Es la vista aplanada de ``AnalyzerConfig`` + ``ModelConfig`` que exige el
    schema: incluye ``fdr_q`` (0.05) y los máximos GARCH, que no viven en los
    modelos de configuración de la Fase 1 porque todavía no son editables por API.
    """

    candle_event_quantile: float = Field(default=0.99, gt=0.5, lt=1.0)
    event_lookback_bars: int = Field(default=200, ge=10)
    min_move_pct: float = Field(default=0.0, ge=0.0)
    tick_event_quantile: float = Field(default=0.999, gt=0.5, lt=1.0)
    tick_event_lookback: int = Field(default=2000, ge=300)
    zone_tolerance_atr: float = Field(default=0.5, gt=0.0)
    pivot_window: int = Field(default=3, ge=1)
    min_zone_touches: int = Field(default=3, ge=1)
    touch_separation_bars: int = Field(default=3, ge=1)
    response_horizon_bars: int = Field(default=10, ge=1)
    favourable_atr: float = Field(default=1.0, gt=0.0)
    adverse_atr: float = Field(default=0.75, gt=0.0)
    fdr_q: float = Field(default=0.05, gt=0.0, lt=1.0)
    evt_threshold_quantile: float = Field(default=0.95, gt=0.5, lt=1.0)
    garch_max_p: int = Field(default=1, ge=1, le=3)
    garch_max_q: int = Field(default=1, ge=1, le=3)
    regime_method: Literal["markov_switching", "cusum"] = "markov_switching"


class RuntimeConfig(DomainModel):
    """Sección ``runtime`` del snapshot: límites operativos efectivos."""

    rate_limit_per_minute: int = Field(default=150, ge=1)
    cache_limit_mb: int = Field(default=420, ge=100)
    refresh_seconds: int = Field(default=30, ge=1)
    stale_seconds: int = Field(default=120, ge=60)


class SnapshotConfig(DomainModel):
    """Configuración efectiva embebida en el snapshot (sección ``config``)."""

    version: str = Field(default="1.0.0", pattern=_CONFIG_SEMVER_PATTERN)
    gates: GateConfig = Field(default_factory=GateConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


class DataRange(DomainModel):
    """Rango temporal solicitado y realmente cubierto por una temporalidad."""

    requested_start_epoch: int
    requested_end_epoch: int
    start_epoch: int
    end_epoch: int

    @model_validator(mode="after")
    def _ranges_ordered(self) -> DataRange:
        if self.requested_end_epoch < self.requested_start_epoch:
            raise ValueError("requested_end_epoch no puede ser anterior al inicio")
        if self.end_epoch < self.start_epoch:
            raise ValueError("end_epoch no puede ser anterior a start_epoch")
        return self


class FeatureStatus(DomainModel):
    """Estado de un análisis concreto dentro de una temporalidad."""

    id: str = Field(min_length=1)
    name_es: str = Field(min_length=1)
    category: FeatureCategory
    status: AnalysisStatus
    min_sample: int = Field(ge=1)
    sample_size: int = Field(ge=0)
    reason: str | None = None
    ui_path: str = Field(min_length=1)
    stage: Stage = "in_sample"

    @model_validator(mode="after")
    def _reason_for_insufficient(self) -> FeatureStatus:
        if self.status in ("insufficient", "error") and not (self.reason or "").strip():
            raise ValueError(
                f"el análisis {self.id!r} en estado {self.status!r} requiere una razón legible"
            )
        return self


class TimeframeAnalysis(DomainModel):
    """Resultados de todos los análisis para una temporalidad."""

    timeframe: Timeframe
    status: AnalysisStatus
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data_range: DataRange
    bars: int = Field(ge=0)
    candles_ref: str = Field(min_length=1)
    quality: DataQualityReport
    events: EventSummary
    zones: tuple[ReactionZone, ...] = ()
    evt: EVTResult | None = None
    volatility: GARCHResult | None = None
    regimes: RegimeResult | None = None
    survival: SurvivalResult | None = None
    autocorrelation: ACFResult | None = None
    diagnostics: DiagnosticsResult | None = None
    conditional: ConditionalResult | None = None
    walkforward: WalkForwardResult | None = None
    features: dict[str, FeatureStatus] = Field(default_factory=dict)
    errors: tuple[ErrorRecord, ...] = ()

    @field_validator("generated_at")
    @classmethod
    def _normalise_generated_at(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value)

    @field_serializer("generated_at")
    def _serialise_generated_at(self, value: datetime) -> str:
        return _iso_z(value)


def aggregate_overall_status(
    timeframes: Mapping[str, TimeframeAnalysis] | Iterable[AnalysisStatus],
) -> OverallStatus:
    """Agrega el estado de las temporalidades en el ``overall_status``.

    Reglas (data-model §7.1), por orden:

    1. Sin temporalidades → ``error``.
    2. Todas en ``error`` → ``error`` (ninguna TF usable).
    3. Todas en ``ready`` → ``ready``.
    4. Cualquier TF en ``stale`` → ``stale`` (los datos están desactualizados).
    5. Cualquier otra combinación → ``partial``.
    """
    if isinstance(timeframes, Mapping):
        statuses = [analysis.status for analysis in timeframes.values()]
    else:
        statuses = list(timeframes)

    if not statuses:
        return "error"
    unique = set(statuses)
    if unique == {"error"}:
        return "error"
    if unique == {"ready"}:
        return "ready"
    if "stale" in unique:
        return "stale"
    return "partial"


class InstrumentSnapshot(DomainModel):
    """Snapshot inmutable y completo de un instrumento."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    snapshot_id: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    app_version: str = Field(default="0.1.0", pattern=SEMVER_PATTERN)
    config_version: str = Field(default="1.0.0", pattern=_CONFIG_SEMVER_PATTERN)
    config_hash: str = Field(pattern=_SHA256_PATTERN)
    data_version: str = Field(pattern=_SHA256_PATTERN)
    instrument: Instrument
    source: Source
    config: SnapshotConfig = Field(default_factory=SnapshotConfig)
    timeframes: dict[str, TimeframeAnalysis] = Field(min_length=1)
    features: tuple[FeatureStatus, ...] = ()
    errors: tuple[ErrorRecord, ...] = ()
    overall_status: OverallStatus | None = None
    cache_bytes: int = Field(default=0, ge=0)
    notes: tuple[str, ...] = ()

    @field_validator("generated_at")
    @classmethod
    def _normalise_generated_at(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value)

    @field_validator("timeframes")
    @classmethod
    def _validate_timeframe_keys(
        cls, values: dict[str, TimeframeAnalysis]
    ) -> dict[str, TimeframeAnalysis]:
        unknown = sorted(set(values) - set(TIMEFRAMES))
        if unknown:
            raise ValueError(f"timeframes usa temporalidades no válidas: {unknown}")
        for key, analysis in values.items():
            if analysis.timeframe != key:
                raise ValueError(
                    f"la clave {key!r} no coincide con TimeframeAnalysis.timeframe "
                    f"({analysis.timeframe!r})"
                )
        return values

    @model_validator(mode="after")
    def _fill_derived_fields(self) -> InstrumentSnapshot:
        if self.overall_status is None:
            object.__setattr__(self, "overall_status", aggregate_overall_status(self.timeframes))
        expected_id = generate_deterministic_uuid5(
            "snapshot",
            self.instrument.symbol,
            _iso_z(self.generated_at),
            self.config_hash,
        )
        if self.snapshot_id is None:
            object.__setattr__(self, "snapshot_id", expected_id)
        else:
            try:
                parsed = uuid.UUID(self.snapshot_id)
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError(f"snapshot_id no es un UUID válido: {self.snapshot_id!r}") from exc
            if str(parsed) != self.snapshot_id:
                raise ValueError(f"snapshot_id debe estar en forma canónica: {self.snapshot_id!r}")
        return self

    @field_serializer("generated_at")
    def _serialise_generated_at(self, value: datetime) -> str:
        return _iso_z(value)
