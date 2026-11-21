"""Modelos de dominio de QuantLab (T011-T013).

- ``types``: alias literales compartidos.
- ``market_data``: ``Instrument``, ``Candle``, ``Tick``.
- ``events``: ``EventCandidate`` y series de eventos.
- ``zones``: ``ReactionZone`` y sensibilidad.
- ``results``: resultados analíticos embebidos en el snapshot.
- ``snapshot``: ``FeatureStatus``, ``TimeframeAnalysis`` e ``InstrumentSnapshot``.
"""

from quantlab.domain.events import (
    EventCandidate,
    EventSeries,
    EventSummary,
    ThresholdSummary,
    compute_event_id,
)
from quantlab.domain.market_data import (
    DEFAULT_INSTRUMENTS,
    Candle,
    Instrument,
    Tick,
    get_instrument,
)
from quantlab.domain.results import (
    ACFResult,
    ConditionalBin,
    ConditionalResult,
    DiagnosticsResult,
    EVTResult,
    ForecastPoint,
    GARCHResult,
    MeanExcessPlot,
    RegimePoint,
    RegimeResult,
    RegimeSegment,
    SurvivalPoint,
    SurvivalResult,
    WalkForwardAggregate,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardResult,
)
from quantlab.domain.snapshot import (
    AnalysisConfig,
    DataRange,
    FeatureStatus,
    InstrumentSnapshot,
    RuntimeConfig,
    SnapshotConfig,
    TimeframeAnalysis,
    aggregate_overall_status,
)
from quantlab.domain.types import (
    AnalysisStatus,
    Direction,
    DomainModel,
    EventKind,
    FeatureCategory,
    OverallStatus,
    Source,
    Stage,
    Timeframe,
    ZoneType,
)
from quantlab.domain.zones import (
    DEFAULT_P_NULL,
    ReactionZone,
    ZoneSensitivity,
    compute_zone_id,
)

__all__ = [
    "DEFAULT_INSTRUMENTS",
    "DEFAULT_P_NULL",
    "ACFResult",
    "AnalysisConfig",
    "AnalysisStatus",
    "Candle",
    "ConditionalBin",
    "ConditionalResult",
    "DataRange",
    "DiagnosticsResult",
    "Direction",
    "DomainModel",
    "EVTResult",
    "EventCandidate",
    "EventKind",
    "EventSeries",
    "EventSummary",
    "FeatureCategory",
    "FeatureStatus",
    "ForecastPoint",
    "GARCHResult",
    "Instrument",
    "InstrumentSnapshot",
    "MeanExcessPlot",
    "OverallStatus",
    "ReactionZone",
    "RegimePoint",
    "RegimeResult",
    "RegimeSegment",
    "RuntimeConfig",
    "SnapshotConfig",
    "Source",
    "Stage",
    "SurvivalPoint",
    "SurvivalResult",
    "ThresholdSummary",
    "Tick",
    "Timeframe",
    "TimeframeAnalysis",
    "WalkForwardAggregate",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardResult",
    "ZoneSensitivity",
    "ZoneType",
    "aggregate_overall_status",
    "compute_event_id",
    "compute_zone_id",
    "get_instrument",
]
