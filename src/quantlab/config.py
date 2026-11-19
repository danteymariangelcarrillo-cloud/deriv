"""Configuración tipada del núcleo (T001).

Modelos pydantic v2 con validación estricta para los parámetros analíticos
heredados del prototipo y para los límites de modelos, puertas de muestra y RAM.
Los defaults de ``AnalyzerConfig`` reproducen ``AnalyzerConfig`` de
``legacy/boom_crash_quant_analyzer.py`` (v2.2.0); ``days`` se conserva por
paridad aunque la Fase 1 use ``Instrument.history_days`` por temporalidad.

Este módulo es puro: no lee ``.env`` (eso es ``quantlab.settings``) ni escribe
en disco.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantlab.utils import config_hash as _payload_hash

__all__ = [
    "TIMEFRAMES",
    "AnalyzerConfig",
    "GateConfig",
    "ModelConfig",
    "QuantLabConfig",
    "RAMConfig",
    "config_hash",
]

#: Temporalidades canónicas de la Fase 1 (mismas que el prototipo).
TIMEFRAMES: tuple[str, ...] = ("1min", "5min", "15min", "1h", "4h", "1d")

_STRICT = ConfigDict(extra="forbid", frozen=True)

_WF_TRAIN_DAYS = {"1min": 30, "5min": 60, "15min": 90, "1h": 180, "4h": 365, "1d": 730}
_WF_TEST_DAYS = {"1min": 7, "5min": 14, "15min": 21, "1h": 30, "4h": 60, "1d": 90}
_WF_STEP_DAYS = {"1min": 3, "5min": 7, "15min": 10, "1h": 14, "4h": 30, "1d": 60}
_WF_MIN_DAYS = dict(_WF_TRAIN_DAYS)


def _check_timeframe_keys(values: dict[str, int], field_name: str) -> dict[str, int]:
    """Exige exactamente las seis temporalidades canónicas, todas positivas."""
    if set(values) != set(TIMEFRAMES):
        missing = sorted(set(TIMEFRAMES) - set(values))
        unknown = sorted(set(values) - set(TIMEFRAMES))
        raise ValueError(
            f"{field_name} debe cubrir exactamente {list(TIMEFRAMES)}; "
            f"faltan={missing}, sobran={unknown}"
        )
    if any(value < 1 for value in values.values()):
        raise ValueError(f"{field_name} sólo admite enteros >= 1")
    return values


class AnalyzerConfig(BaseModel):
    """Parámetros analíticos heredados del prototipo, con overrides por TF."""

    model_config = _STRICT

    days: int = Field(default=20, ge=1)
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
    timeframe_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("timeframe_overrides")
    @classmethod
    def _validate_overrides(cls, overrides: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        unknown = sorted(set(overrides) - set(TIMEFRAMES))
        if unknown:
            raise ValueError(f"timeframe_overrides usa temporalidades no válidas: {unknown}")
        return overrides


class ModelConfig(BaseModel):
    """Parámetros de los modelos estadísticos y ventanas walk-forward."""

    model_config = _STRICT

    rolling_window: int = Field(default=20, ge=5)
    garch_p: int = Field(default=1, ge=1, le=3)
    garch_q: int = Field(default=1, ge=1, le=3)
    evt_threshold_quantile: float = Field(default=0.95, gt=0.5, lt=1.0)
    wf_train_days: dict[str, int] = Field(default_factory=lambda: dict(_WF_TRAIN_DAYS))
    wf_test_days: dict[str, int] = Field(default_factory=lambda: dict(_WF_TEST_DAYS))
    wf_step_days: dict[str, int] = Field(default_factory=lambda: dict(_WF_STEP_DAYS))
    regime_method: Literal["markov_switching", "cusum"] = "markov_switching"

    @field_validator("wf_train_days")
    @classmethod
    def _validate_train(cls, values: dict[str, int]) -> dict[str, int]:
        return _check_timeframe_keys(values, "wf_train_days")

    @field_validator("wf_test_days")
    @classmethod
    def _validate_test(cls, values: dict[str, int]) -> dict[str, int]:
        return _check_timeframe_keys(values, "wf_test_days")

    @field_validator("wf_step_days")
    @classmethod
    def _validate_step(cls, values: dict[str, int]) -> dict[str, int]:
        return _check_timeframe_keys(values, "wf_step_days")


class GateConfig(BaseModel):
    """Muestras mínimas para publicar cada análisis ("datos insuficientes")."""

    model_config = _STRICT

    candle_events_min_bars: int = Field(default=50, ge=10)
    tick_events_min_ticks: int = Field(default=300, ge=50)
    zone_min_evaluated: int = Field(default=10, ge=1)
    zone_min_count: int = Field(default=5, ge=1)
    evt_min_excess: int = Field(default=30, ge=10)
    garch_min_obs: int = Field(default=200, ge=50)
    regime_min_obs: int = Field(default=500, ge=100)
    survival_min_events: int = Field(default=20, ge=5)
    acf_min_obs: int = Field(default=100, ge=20)
    conditional_min_events: int = Field(default=30, ge=10)
    wf_min_folds: int = Field(default=3, ge=1)
    wf_min_days: dict[str, int] = Field(default_factory=lambda: dict(_WF_MIN_DAYS))

    @field_validator("wf_min_days")
    @classmethod
    def _validate_min_days(cls, values: dict[str, int]) -> dict[str, int]:
        return _check_timeframe_keys(values, "wf_min_days")


class RAMConfig(BaseModel):
    """Límites de los buffers en RAM y del monitor de memoria."""

    model_config = _STRICT

    max_ticks_per_symbol: int = Field(default=50_000, ge=1_000)
    max_candles_per_tf: int = Field(default=30_000, ge=1_000)
    max_directional_returns: int = Field(default=10_000, ge=1_000)
    max_live_queue: int = Field(default=100_000, ge=1_000)
    downsample_max_points: int = Field(default=5_000, ge=1_000)
    figure_cache_size: int = Field(default=20, ge=5)
    ram_warning_mb: int = Field(default=380, ge=100)
    ram_critical_mb: int = Field(default=420, ge=200)

    @model_validator(mode="after")
    def _warning_below_critical(self) -> RAMConfig:
        if self.ram_warning_mb >= self.ram_critical_mb:
            raise ValueError("ram_warning_mb debe ser menor que ram_critical_mb")
        return self


class QuantLabConfig(BaseModel):
    """Configuración completa de un instrumento más la versión del esquema."""

    model_config = _STRICT

    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    analyzer: AnalyzerConfig = Field(default_factory=AnalyzerConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    gates: GateConfig = Field(default_factory=GateConfig)
    ram: RAMConfig = Field(default_factory=RAMConfig)

    def hash(self) -> str:
        """Hash canónico de la configuración completa (incluye ``version``)."""
        return config_hash(self.analyzer, self.model, self.gates, self.ram, self.version)

    def updated(self, patch: Mapping[str, Any]) -> QuantLabConfig:
        """Devuelve una copia validada con un patch parcial por sección.

        ``patch`` acepta ``version`` y/o cualquiera de ``analyzer``, ``model``,
        ``gates``, ``ram``; cada sección se mezcla con la actual y se revalida.
        No muta la instancia original (los modelos son ``frozen``).
        """
        allowed = {"version", "analyzer", "model", "gates", "ram"}
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise ValueError(f"secciones de configuración no válidas: {unknown}")

        payload = self.model_dump()
        for section in ("analyzer", "model", "gates", "ram"):
            if section in patch:
                section_patch = patch[section]
                if not isinstance(section_patch, Mapping):
                    raise ValueError(f"{section} debe ser un objeto")
                payload[section] = {**payload[section], **section_patch}
        if "version" in patch:
            payload["version"] = patch["version"]
        return QuantLabConfig.model_validate(payload)


def config_hash(
    analyzer: AnalyzerConfig,
    model: ModelConfig,
    gates: GateConfig,
    ram: RAMConfig,
    version: str = "1.0.0",
) -> str:
    """SHA-256 del JSON canónico de las cuatro secciones + ``version``.

    Contrato del data model (§2.5): mismo contenido lógico → mismo hash. Acepta
    modelos o mappings; cualquier valor no JSON se rechaza con ``TypeError``.
    """
    payload = {
        "version": version,
        "analyzer": analyzer.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
        "gates": gates.model_dump(mode="json"),
        "ram": ram.model_dump(mode="json"),
    }
    return _payload_hash(payload)
