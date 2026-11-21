"""Orquestador de snapshots (T016): config → gates → analytics → swap.

``SnapshotService`` une las piezas de M1B: lee el estado y los datos de un
instrumento, pide al motor analítico el tamaño de muestra de cada análisis,
consulta ``FeatureRegistry`` (T015) para decidir si se debe computar, monta un
``TimeframeAnalysis`` por temporalidad y publica un ``InstrumentSnapshot``
inmutable mediante ``SnapshotStore.swap`` (T014).

CERO DISCO: todo el trabajo ocurre en memoria. Los datos se descargan a través
de ``MarketDataService`` (que ya pagina y cachea) y se copian a los buffers del
``InstrumentState`` para que los consumidores en vivo y la API vean lo mismo.

Notas de contrato:

- El motor analítico real (T017) todavía no existe; aquí se define el protocolo
  mínimo que necesita este servicio (``sample_size`` + ``compute``). El
  ``FakeAnalyticsEngine`` de los tests lo implementa igual que lo hará T017.
- El contrato de ``update_config`` pide un ``ThreadPoolExecutor``; se usa en su
  lugar ``asyncio.create_task`` porque el ``recompute_lock`` y el cliente Deriv
  están ligados al event loop. La respuesta sigue siendo inmediata y el
  recálculo ocurre en background.
- El push WebSocket se expone como ``event_sink`` opcional (T034 conectará el
  hub real): el servicio no conoce el gestor de conexiones.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from quantlab.config import (
    TIMEFRAMES,
    AnalyzerConfig,
    GateConfig,
    ModelConfig,
    QuantLabConfig,
    RAMConfig,
)
from quantlab.data.cache import RAMCache
from quantlab.data.market_data_service import MarketDataService
from quantlab.data.quality import GRANULARITY_SECONDS, DataQualityReport, calculate_quality
from quantlab.domain.events import EventSeries, EventSummary
from quantlab.domain.market_data import get_instrument
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
from quantlab.domain.snapshot import (
    AnalysisConfig,
    DataRange,
    FeatureStatus,
    InstrumentSnapshot,
    RuntimeConfig,
    SnapshotConfig,
    TimeframeAnalysis,
)
from quantlab.domain.zones import ReactionZone
from quantlab.errors import ErrorCode, ErrorRecord
from quantlab.services.feature_registry import FeatureRegistry
from quantlab.services.snapshot_store import SnapshotStore
from quantlab.state import CandleRecord, InstrumentState
from quantlab.utils import config_hash

__all__ = [
    "AnalyticsEngineProtocol",
    "ConfigUpdateRequest",
    "ConfigUpdateResponse",
    "ConfigUpdatedEvent",
    "SnapshotService",
]

SECONDS_PER_DAY = 86_400
DEFAULT_RUNTIME_REFRESH_SECONDS = 30
DEFAULT_RUNTIME_STALE_SECONDS = 120
DEFAULT_RATE_LIMIT_PER_MINUTE = 150


# ------------------------------- contratos API -------------------------------


class ConfigUpdateRequest(BaseModel):
    """Partial update de configuración (dashboard-routes §ConfigUpdateRequest)."""

    model_config = ConfigDict(extra="forbid")

    analyzer: AnalyzerConfig | None = None
    model: ModelConfig | None = None
    gates: GateConfig | None = None
    ram: RAMConfig | None = None


class ConfigUpdateResponse(BaseModel):
    """Respuesta de ``POST /api/config/{symbol}``."""

    model_config = ConfigDict(extra="forbid")

    config_hash: str
    status: str = Field(pattern=r"^(updated|unchanged|error)$")
    recalculated_timeframes: list[str] = Field(default_factory=list)
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigUpdatedEvent:
    """Evento ``config_updated`` que T034 publicará por WebSocket."""

    symbol: str
    config_hash: str
    recalculated: tuple[str, ...]
    timestamp: float


ConfigEventSink = Callable[[ConfigUpdatedEvent], Awaitable[None] | None]


# ------------------------------ motor analítico ------------------------------

AnalysisPayload = BaseModel | tuple[ReactionZone, ...] | EventSeries


@runtime_checkable
class AnalyticsEngineProtocol(Protocol):
    """Contrato mínimo del motor analítico usado por el orquestador."""

    def sample_size(
        self,
        analysis_id: str,
        *,
        candles: pd.DataFrame,
        ticks: pd.DataFrame,
    ) -> int:
        """Muestra disponible para el análisis (barras, ticks, eventos...)."""
        ...

    def compute(
        self,
        analysis_id: str,
        *,
        symbol: str,
        timeframe: str,
        candles: pd.DataFrame,
        ticks: pd.DataFrame,
        config: QuantLabConfig,
    ) -> AnalysisPayload | None:
        """Calcula un análisis con gate ``ready``; ``None`` si no hay resultado."""
        ...


_RESULT_FIELDS: dict[str, str] = {
    "evt.gpd": "evt",
    "volatility.garch": "volatility",
    "volatility.regimes": "regimes",
    "survival.kaplan_meier": "survival",
    "autocorrelation": "autocorrelation",
    "diagnostics.residuals": "diagnostics",
    "events.conditional": "conditional",
    "walkforward.validation": "walkforward",
}

_RESULT_TYPES: dict[str, type[BaseModel]] = {
    "evt": EVTResult,
    "volatility": GARCHResult,
    "regimes": RegimeResult,
    "survival": SurvivalResult,
    "autocorrelation": ACFResult,
    "diagnostics": DiagnosticsResult,
    "conditional": ConditionalResult,
    "walkforward": WalkForwardResult,
}

_EVENT_FIELDS: dict[str, str] = {"events.candle": "candle", "events.tick": "tick"}

#: Análisis que sólo alimentan la UI (mapas de calor) o son metadatos de la
#: temporalidad (calidad); no aportan campos al ``TimeframeAnalysis``.
_NO_PAYLOAD: frozenset[str] = frozenset({"heatmap.price", "heatmap.events", "data.quality"})


class SnapshotService:
    """Orquesta la construcción y el refresco de snapshots por instrumento."""

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        feature_registry: FeatureRegistry,
        market_data_service: MarketDataService,
        analytics_engine: AnalyticsEngineProtocol,
        cache: RAMCache,
        *,
        states: Mapping[str, InstrumentState] | None = None,
        event_sink: ConfigEventSink | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = snapshot_store
        self._registry = feature_registry
        self._market_data = market_data_service
        self._engine = analytics_engine
        self._cache = cache
        self._states: Mapping[str, InstrumentState] = states or {}
        self._event_sink = event_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._background: set[asyncio.Task[None]] = set()

    # --------------------------------- setup ---------------------------------

    @property
    def store(self) -> SnapshotStore:
        return self._store

    @property
    def registry(self) -> FeatureRegistry:
        return self._registry

    def _require_state(self, symbol: str) -> InstrumentState:
        state = self._states.get(symbol)
        if state is None:
            raise KeyError(f"instrumento desconocido: {symbol!r}")
        return state

    @staticmethod
    def _validate_timeframe(timeframe: str) -> None:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"temporalidad no válida: {timeframe!r}; use {list(TIMEFRAMES)}")

    def _current_config(self, state: InstrumentState) -> QuantLabConfig:
        return QuantLabConfig(
            analyzer=state.analyzer_config,
            model=state.model_config,
            gates=state.gate_config,
            ram=state.ram_config,
        )

    def _snapshot_config(self, state: InstrumentState) -> SnapshotConfig:
        analyzer = state.analyzer_config
        model = state.model_config
        return SnapshotConfig(
            gates=state.gate_config,
            analysis=AnalysisConfig(
                candle_event_quantile=analyzer.candle_event_quantile,
                event_lookback_bars=analyzer.event_lookback_bars,
                min_move_pct=analyzer.min_move_pct,
                tick_event_quantile=analyzer.tick_event_quantile,
                tick_event_lookback=analyzer.tick_event_lookback,
                zone_tolerance_atr=analyzer.zone_tolerance_atr,
                pivot_window=analyzer.pivot_window,
                min_zone_touches=analyzer.min_zone_touches,
                touch_separation_bars=analyzer.touch_separation_bars,
                response_horizon_bars=analyzer.response_horizon_bars,
                favourable_atr=analyzer.favourable_atr,
                adverse_atr=analyzer.adverse_atr,
                evt_threshold_quantile=model.evt_threshold_quantile,
                garch_max_p=model.garch_p,
                garch_max_q=model.garch_q,
                regime_method=model.regime_method,
            ),
            runtime=RuntimeConfig(
                rate_limit_per_minute=DEFAULT_RATE_LIMIT_PER_MINUTE,
                cache_limit_mb=state.ram_config.ram_critical_mb,
                refresh_seconds=DEFAULT_RUNTIME_REFRESH_SECONDS,
                stale_seconds=DEFAULT_RUNTIME_STALE_SECONDS,
            ),
        )

    # ------------------------------ construcción ------------------------------

    async def build_snapshot(
        self,
        symbol: str,
        timeframes: Sequence[str] | None = None,
    ) -> InstrumentSnapshot:
        """Construye el snapshot completo del símbolo y lo intercambia."""
        state = self._require_state(symbol)
        selected = tuple(TIMEFRAMES if timeframes is None else timeframes)
        if not selected:
            raise ValueError("debe indicarse al menos una temporalidad")
        for timeframe in selected:
            self._validate_timeframe(timeframe)

        async with state.recompute_lock:
            analyses: dict[str, TimeframeAnalysis] = {}
            for timeframe in selected:
                analyses[timeframe] = await self._analyse_timeframe(state, timeframe)
            snapshot = self._make_snapshot(state, analyses)
            version = self._store.swap(symbol, snapshot)
            state.snapshot = snapshot
            state.snapshot_version = version
            return snapshot

    async def recompute_timeframe(self, symbol: str, timeframe: str) -> TimeframeAnalysis:
        """Recalcula una sola temporalidad y republica el snapshot."""
        state = self._require_state(symbol)
        self._validate_timeframe(timeframe)

        async with state.recompute_lock:
            analysis = await self._analyse_timeframe(state, timeframe)
            current = self._store.get(symbol)
            merged = dict(current.timeframes) if current is not None else {}
            merged[timeframe] = analysis
            snapshot = self._make_snapshot(state, merged)
            version = self._store.swap(symbol, snapshot)
            state.snapshot = snapshot
            state.snapshot_version = version
            return analysis

    def _make_snapshot(
        self,
        state: InstrumentState,
        analyses: Mapping[str, TimeframeAnalysis],
    ) -> InstrumentSnapshot:
        ordered = {tf: analyses[tf] for tf in TIMEFRAMES if tf in analyses}
        cache_bytes = round(self._cache.get_stats(state.symbol).ram_usage_mb * 1024 * 1024)
        snapshot = InstrumentSnapshot(
            generated_at=self._clock(),
            config_hash=state.config_hash,
            data_version=self._data_version(ordered),
            instrument=get_instrument(state.symbol),
            source="deriv",
            config=self._snapshot_config(state),
            timeframes=ordered,
            cache_bytes=cache_bytes,
        )
        return snapshot

    @staticmethod
    def _data_version(analyses: Mapping[str, TimeframeAnalysis]) -> str:
        payload = {
            timeframe: {
                "bars": analysis.bars,
                "start_epoch": analysis.data_range.start_epoch,
                "end_epoch": analysis.data_range.end_epoch,
            }
            for timeframe, analysis in analyses.items()
        }
        return config_hash(payload)

    # ------------------------------- una TF ---------------------------------

    async def _analyse_timeframe(
        self,
        state: InstrumentState,
        timeframe: str,
    ) -> TimeframeAnalysis:
        symbol = state.symbol
        now = self._clock()
        config = self._current_config(state)
        instrument = get_instrument(symbol)
        days = instrument.history_days[timeframe]
        requested_end = int(now.timestamp())
        requested_start = requested_end - days * SECONDS_PER_DAY
        expected_bars = days * SECONDS_PER_DAY // GRANULARITY_SECONDS[timeframe]

        errors: list[ErrorRecord] = []
        try:
            candles, quality = await self._market_data.get_candles(
                symbol, timeframe, requested_start, requested_end
            )
        except Exception as exc:  # noqa: BLE001 - una TF caída no tumba el snapshot
            candles = pd.DataFrame(columns=["epoch", "open", "high", "low", "close"])
            quality = calculate_quality(candles, timeframe, expected_bars, symbol=symbol)
            errors.append(self._error(ErrorCode.DATA_EMPTY, f"sin velas para {timeframe}", exc))

        candles = self._normalise_columns(candles)
        self._sync_candles(state, timeframe, candles)
        ticks = state.get_ticks_df()
        bars = len(candles)
        events_by_kind: dict[str, EventSeries] = {}
        zones: tuple[ReactionZone, ...] = ()
        results: dict[str, BaseModel] = {}
        features: dict[str, FeatureStatus] = {}

        for analysis_id in self._registry.analysis_ids():
            applicable = not (analysis_id == "events.tick" and ticks.empty)
            try:
                sample_size = self._engine.sample_size(analysis_id, candles=candles, ticks=ticks)
            except Exception as exc:  # noqa: BLE001 - se publica como análisis error
                features[analysis_id] = self._failed_feature(state, timeframe, analysis_id, exc)
                errors.append(self._error(ErrorCode.COMPUTE_FAILED, f"gate {analysis_id}", exc))
                continue

            status = self._registry.check_gate(
                analysis_id,
                timeframe,
                sample_size,
                symbol=symbol,
                applicable=applicable,
            )
            if status.status != "ready":
                features[analysis_id] = status
                continue

            if analysis_id in _NO_PAYLOAD:
                features[analysis_id] = status
                continue

            try:
                payload = self._engine.compute(
                    analysis_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=candles,
                    ticks=ticks,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001 - un análisis fallido se aísla
                features[analysis_id] = self._failed_feature(state, timeframe, analysis_id, exc)
                errors.append(self._error(ErrorCode.COMPUTE_FAILED, f"cómputo {analysis_id}", exc))
                continue

            if payload is None:
                features[analysis_id] = status.model_copy(
                    update={
                        "status": "error",
                        "reason": f"{status.name_es}: el motor no devolvió resultado",
                    }
                )
                continue

            features[analysis_id] = status
            if analysis_id in _RESULT_FIELDS:
                field = _RESULT_FIELDS[analysis_id]
                results[field] = self._coerce_result(field, payload)
            elif analysis_id in _EVENT_FIELDS:
                kind = _EVENT_FIELDS[analysis_id]
                if not isinstance(payload, EventSeries):
                    raise TypeError(
                        f"{analysis_id} debe devolver EventSeries, "
                        f"recibido {type(payload).__name__}"
                    )
                events_by_kind[kind] = payload
            elif analysis_id == "zones.reaction":
                zones = self._coerce_zones(payload)
            # heatmap.* y data.quality no aportan campos al snapshot.

        events = EventSummary(
            candle=events_by_kind.get("candle", self._empty_series(state, "candle")),
            tick=events_by_kind.get("tick", self._empty_series(state, "tick")),
        )

        data_range = self._data_range(candles, requested_start, requested_end)
        return TimeframeAnalysis(
            timeframe=timeframe,  # type: ignore[arg-type]
            status=self._timeframe_status(bars, quality, features),
            generated_at=now,
            data_range=data_range,
            bars=bars,
            candles_ref=f"{symbol}:{timeframe}",
            quality=quality,
            events=events,
            zones=zones,
            features=features,
            errors=tuple(errors),
            **results,
        )

    @staticmethod
    def _normalise_columns(candles: pd.DataFrame) -> pd.DataFrame:
        """Pasa ``Open/High/Low/Close`` a minúsculas (salida de T009)."""
        if candles.empty:
            return candles
        return candles.rename(columns={column: str(column).lower() for column in candles.columns})

    def _sync_candles(
        self,
        state: InstrumentState,
        timeframe: str,
        candles: pd.DataFrame,
    ) -> None:
        records = [
            CandleRecord(
                epoch=int(row.epoch),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                timeframe=timeframe,
            )
            for row in candles.itertuples(index=False)
        ]
        buffer = state.candles_by_tf.get(timeframe)
        if buffer is not None:
            buffer.clear()
            buffer.extend(records)
        self._cache.replace_candles(state.symbol, timeframe, records)

    def _empty_series(self, state: InstrumentState, kind: str) -> EventSeries:
        if kind == "candle":
            quantile = state.analyzer_config.candle_event_quantile
            lookback = state.analyzer_config.event_lookback_bars
        else:
            quantile = state.analyzer_config.tick_event_quantile
            lookback = state.analyzer_config.tick_event_lookback
        return EventSeries(
            status="insufficient",
            n_events=0,
            threshold_quantile=quantile,
            lookback=lookback,
            warmup=max(1, min(50, lookback)),
            note="sin muestra suficiente",
        )

    @staticmethod
    def _data_range(
        candles: pd.DataFrame,
        requested_start: int,
        requested_end: int,
    ) -> DataRange:
        if candles.empty:
            return DataRange(
                requested_start_epoch=requested_start,
                requested_end_epoch=requested_end,
                start_epoch=requested_end,
                end_epoch=requested_end,
            )
        return DataRange(
            requested_start_epoch=requested_start,
            requested_end_epoch=requested_end,
            start_epoch=int(candles["epoch"].min()),
            end_epoch=int(candles["epoch"].max()),
        )

    @staticmethod
    def _timeframe_status(
        bars: int,
        quality: DataQualityReport,
        features: Mapping[str, FeatureStatus],
    ) -> str:
        if bars == 0 or quality.status == "fail":
            stale = quality.stale_seconds is not None and quality.stale_seconds > 0
            return "stale" if stale and bars > 0 else "error"
        statuses = {feature.status for feature in features.values()}
        if "ready" in statuses:
            return "ready"
        # Sin ningún análisis listo no hay resultados que publicar; si además
        # falló alguno, la temporalidad queda en error.
        return "error" if "error" in statuses else "insufficient"

    @staticmethod
    def _coerce_result(field: str, payload: AnalysisPayload) -> BaseModel:
        expected = _RESULT_TYPES[field]
        if isinstance(payload, expected):
            return payload
        if isinstance(payload, BaseModel):
            raise TypeError(
                f"resultado {field} debe ser {expected.__name__}, recibido {type(payload).__name__}"
            )
        return expected.model_validate(payload)

    @staticmethod
    def _coerce_zones(payload: AnalysisPayload) -> tuple[ReactionZone, ...]:
        if isinstance(payload, BaseModel):
            raise TypeError("zones.reaction debe devolver una tupla de ReactionZone")
        if not all(isinstance(zone, ReactionZone) for zone in payload):
            raise TypeError("zones.reaction contiene objetos que no son ReactionZone")
        return tuple(payload)

    def _failed_feature(
        self,
        state: InstrumentState,
        timeframe: str,
        analysis_id: str,
        exc: Exception,
    ) -> FeatureStatus:
        base = self._registry.check_gate(
            analysis_id,
            timeframe,
            0,
            symbol=state.symbol,
        )
        return base.model_copy(
            update={
                "status": "error",
                "reason": f"{base.name_es}: {type(exc).__name__}: {exc}",
            }
        )

    @staticmethod
    def _error(code: ErrorCode, detail: str, exc: Exception) -> ErrorRecord:
        return ErrorRecord(
            code=code,
            severity="warning",
            message_es="No se pudo completar un cálculo del snapshot",
            detail=f"{detail}: {type(exc).__name__}: {exc}",
            retryable=True,
            source="snapshot_service",
        )

    # ------------------------------- config --------------------------------

    async def update_config(
        self,
        symbol: str,
        config_update: ConfigUpdateRequest,
    ) -> ConfigUpdateResponse:
        """Valida un partial update, invalida el snapshot y recalcula en background."""
        state = self._require_state(symbol)
        current = self._current_config(state)

        patch = {
            section: getattr(config_update, section).model_dump()
            for section in ("analyzer", "model", "gates", "ram")
            if getattr(config_update, section) is not None
        }
        if not patch:
            return ConfigUpdateResponse(
                config_hash=state.config_hash,
                status="unchanged",
                recalculated_timeframes=[],
                message="no se envió ninguna sección de configuración",
            )

        updated = current.updated(patch)
        new_hash = updated.hash()
        if new_hash == state.config_hash:
            return ConfigUpdateResponse(
                config_hash=state.config_hash,
                status="unchanged",
                recalculated_timeframes=[],
                message="la configuración efectiva no cambió",
            )

        changed = self._changed_sections(current, updated)
        return await self._apply_config(state, updated, changed=changed)

    async def reset_config(self, symbol: str) -> ConfigUpdateResponse:
        """Restaura los defaults de las cuatro secciones y recalcula."""
        state = self._require_state(symbol)
        current = self._current_config(state)
        defaults = QuantLabConfig(version=current.version)
        if defaults.hash() == state.config_hash:
            return ConfigUpdateResponse(
                config_hash=state.config_hash,
                status="unchanged",
                recalculated_timeframes=[],
                message="la configuración ya está en sus valores por defecto",
            )
        return await self._apply_config(
            state,
            defaults,
            changed=self._changed_sections(current, defaults),
        )

    @staticmethod
    def _changed_sections(current: QuantLabConfig, updated: QuantLabConfig) -> set[str]:
        return {
            section
            for section in ("analyzer", "model", "gates", "ram")
            if getattr(current, section) != getattr(updated, section)
        }

    async def _apply_config(
        self,
        state: InstrumentState,
        updated: QuantLabConfig,
        *,
        changed: set[str],
    ) -> ConfigUpdateResponse:
        state.analyzer_config = updated.analyzer
        state.model_config = updated.model
        state.gate_config = updated.gates
        state.ram_config = updated.ram
        state.config_hash = updated.hash()
        state.snapshot_version += 1
        self._registry.set_gate_config(updated.gates)

        # ``ram`` sólo cambia límites de buffers: no invalida cálculos.
        affected = () if changed <= {"ram"} else TIMEFRAMES
        if not affected:
            return ConfigUpdateResponse(
                config_hash=state.config_hash,
                status="updated",
                recalculated_timeframes=[],
                message="sólo cambiaron límites de RAM; no se recalcula",
            )

        for timeframe in affected:
            self._schedule(self._recompute_and_notify(state.symbol, timeframe))
        return ConfigUpdateResponse(
            config_hash=state.config_hash,
            status="updated",
            recalculated_timeframes=list(affected),
            message=f"recálculo en background para {len(affected)} temporalidades",
        )

    def _schedule(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _recompute_and_notify(self, symbol: str, timeframe: str) -> None:
        state = self._require_state(symbol)
        await self.recompute_timeframe(symbol, timeframe)
        await self._notify(
            ConfigUpdatedEvent(
                symbol=symbol,
                config_hash=state.config_hash,
                recalculated=(timeframe,),
                timestamp=time.time(),
            )
        )

    async def _notify(self, event: ConfigUpdatedEvent) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(event)
        if asyncio.iscoroutine(result):
            await result

    async def wait_for_background(self) -> None:
        """Espera a que terminen los recálculos lanzados por ``update_config``.

        Pensado para tests y para el apagado ordenado; en producción el endpoint
        responde sin esperar.
        """
        while self._background:
            pending = tuple(self._background)
            await asyncio.gather(*pending, return_exceptions=True)
