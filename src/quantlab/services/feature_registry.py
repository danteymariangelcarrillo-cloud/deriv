"""Registro central de análisis y puertas de muestra mínima (T015).

``FeatureRegistry`` es la única fuente de verdad de qué análisis existen, cómo se
llaman, qué panel del frontend los muestra y cuánta muestra necesitan. El
``SnapshotService`` (T016) lo consulta antes de pedir un cálculo al motor
analítico, de modo que un análisis sin muestra suficiente nunca se computa: se
publica como ``insufficient`` con una razón legible.

Los umbrales viven en ``GateConfig`` (T001); el registro sólo los lee, así que un
cambio de configuración en caliente sólo exige ``set_gate_config`` y volver a
evaluar. Las 14 entradas base son las de ``tasks.md`` T015.

Desviaciones documentadas respecto al stub de la tarea:

- ``ui_path`` se construye por símbolo (``/i/BOOM1000/distribution``), por eso
  ``get_ui_path`` y ``check_gate`` reciben ``symbol``; sin él no se puede formar
  una ruta real como la del ``snapshot.example.json``.
- ``heatmap.price``, ``heatmap.events`` y ``data.quality`` usan la categoría
  ``data``: el enum del schema no tiene ``heatmap``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from quantlab.config import TIMEFRAMES, GateConfig
from quantlab.domain.snapshot import FeatureStatus
from quantlab.domain.types import FeatureCategory, Stage

__all__ = [
    "ANALYSIS_IDS",
    "AnalysisSpec",
    "FeatureRegistry",
]

#: Análisis base que publica cada temporalidad (orden estable de presentación).
ANALYSIS_IDS: tuple[str, ...] = (
    "data.quality",
    "events.candle",
    "events.tick",
    "events.conditional",
    "evt.gpd",
    "volatility.garch",
    "volatility.regimes",
    "zones.reaction",
    "survival.kaplan_meier",
    "autocorrelation",
    "diagnostics.residuals",
    "walkforward.validation",
    "heatmap.price",
    "heatmap.events",
)

#: Campo de ``GateConfig`` que fija la muestra mínima, o constante si no hay campo.
_GATE_SOURCE: dict[str, str | int] = {
    "data.quality": 1,
    "events.candle": "candle_events_min_bars",
    "events.tick": "tick_events_min_ticks",
    "events.conditional": "conditional_min_events",
    "evt.gpd": "evt_min_excess",
    "volatility.garch": "garch_min_obs",
    "volatility.regimes": "regime_min_obs",
    "zones.reaction": "zone_min_evaluated",
    "survival.kaplan_meier": "survival_min_events",
    "autocorrelation": "acf_min_obs",
    "diagnostics.residuals": "acf_min_obs",
    "walkforward.validation": "wf_min_folds",
    "heatmap.price": 2,
    "heatmap.events": 1,
}


@dataclass(frozen=True, slots=True)
class AnalysisSpec:
    """Metadatos estáticos de un análisis y la puerta que lo habilita."""

    id: str
    name_es: str
    category: FeatureCategory
    panel: str
    stage: Stage = "in_sample"
    gate: str | int = 1


_SPECS: tuple[AnalysisSpec, ...] = (
    AnalysisSpec("data.quality", "Calidad de Datos", "data", "diagnostics", gate=1),
    AnalysisSpec(
        "events.candle", "Eventos de Velas", "events", "events", gate="candle_events_min_bars"
    ),
    AnalysisSpec(
        "events.tick", "Eventos de Ticks", "events", "realtime", gate="tick_events_min_ticks"
    ),
    AnalysisSpec(
        "events.conditional",
        "Eventos Condicionales",
        "events",
        "distribution",
        gate="conditional_min_events",
    ),
    AnalysisSpec("evt.gpd", "Ajuste EVT/GPD", "evt", "distribution", gate="evt_min_excess"),
    AnalysisSpec(
        "volatility.garch", "GARCH(1,1)", "volatility", "volatility", gate="garch_min_obs"
    ),
    AnalysisSpec(
        "volatility.regimes",
        "Regímenes de Volatilidad",
        "volatility",
        "volatility",
        gate="regime_min_obs",
    ),
    AnalysisSpec(
        "zones.reaction", "Zonas de Reacción", "zones", "realtime", gate="zone_min_evaluated"
    ),
    AnalysisSpec(
        "survival.kaplan_meier",
        "Curva Kaplan-Meier",
        "survival",
        "survival",
        gate="survival_min_events",
    ),
    AnalysisSpec(
        "autocorrelation", "Autocorrelación", "diagnostics", "diagnostics", gate="acf_min_obs"
    ),
    AnalysisSpec(
        "diagnostics.residuals",
        "Diagnósticos Residuales",
        "diagnostics",
        "diagnostics",
        gate="acf_min_obs",
    ),
    AnalysisSpec(
        "walkforward.validation",
        "Validación Walk-Forward",
        "walkforward",
        "diagnostics",
        stage="oos",
        gate="wf_min_folds",
    ),
    AnalysisSpec("heatmap.price", "Mapa de Calor de Precios", "data", "heatmap", gate=2),
    AnalysisSpec("heatmap.events", "Mapa de Calor de Eventos", "data", "heatmap", gate=1),
)


class FeatureRegistry:
    """Catálogo inmutable de análisis parametrizado por ``GateConfig``."""

    def __init__(self, gate_config: GateConfig) -> None:
        self._gate_config = gate_config
        self._specs: MappingProxyType[str, AnalysisSpec] = MappingProxyType(
            {spec.id: spec for spec in _SPECS}
        )

    # ------------------------------- catálogo -------------------------------

    @property
    def gate_config(self) -> GateConfig:
        return self._gate_config

    def set_gate_config(self, gate_config: GateConfig) -> None:
        """Adopta una configuración nueva (hot-reload de gates)."""
        self._gate_config = gate_config

    @property
    def specs(self) -> tuple[AnalysisSpec, ...]:
        return _SPECS

    def analysis_ids(self) -> tuple[str, ...]:
        return ANALYSIS_IDS

    def resolve(self, analysis_id: str) -> AnalysisSpec:
        try:
            return self._specs[analysis_id]
        except KeyError as exc:
            raise KeyError(f"análisis desconocido: {analysis_id!r}") from exc

    def min_sample(self, analysis_id: str, timeframe: str) -> int:
        """Muestra mínima exigida por el gate para esa temporalidad."""
        spec = self.resolve(analysis_id)
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"temporalidad no válida: {timeframe!r}")
        if isinstance(spec.gate, int):
            return spec.gate
        if spec.gate == "wf_min_days":
            return self._gate_config.wf_min_days[timeframe]
        return int(getattr(self._gate_config, spec.gate))

    def get_ui_path(self, analysis_id: str, symbol: str) -> str:
        """Ruta del panel que muestra el análisis (``/i/<symbol>/<panel>``)."""
        spec = self.resolve(analysis_id)
        if not symbol.strip():
            raise ValueError("symbol no puede estar vacío")
        return f"/i/{symbol}/{spec.panel}"

    # -------------------------------- gates --------------------------------

    def register_all(
        self,
        symbol: str,
        timeframe: str,
        *,
        status: Literal["pending", "computing"] = "pending",
    ) -> dict[str, FeatureStatus]:
        """Crea el catálogo completo de ``FeatureStatus`` sin evaluar gates.

        Útil para publicar los 14 análisis desde el primer momento con estado
        ``pending``/``computing``; ``check_gate`` los sustituye al evaluarlos.
        """
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"temporalidad no válida: {timeframe!r}")
        catalog: dict[str, FeatureStatus] = {}
        for spec in _SPECS:
            catalog[spec.id] = FeatureStatus(
                id=spec.id,
                name_es=spec.name_es,
                category=spec.category,
                status=status,
                min_sample=self.min_sample(spec.id, timeframe),
                sample_size=0,
                ui_path=self.get_ui_path(spec.id, symbol),
                stage=spec.stage,
            )
        return catalog

    def check_gate(
        self,
        analysis_id: str,
        timeframe: str,
        sample_size: int,
        *,
        symbol: str,
        applicable: bool = True,
    ) -> FeatureStatus:
        """Evalúa la puerta y devuelve el ``FeatureStatus`` resultante.

        ``ready`` si hay muestra suficiente, ``insufficient`` con razón legible
        si no, y ``not_applicable`` cuando el análisis no aplica a esos datos.
        """
        if sample_size < 0:
            raise ValueError("sample_size no puede ser negativo")
        spec = self.resolve(analysis_id)
        min_sample = self.min_sample(analysis_id, timeframe)
        ui_path = self.get_ui_path(analysis_id, symbol)

        if not applicable:
            return FeatureStatus(
                id=spec.id,
                name_es=spec.name_es,
                category=spec.category,
                status="not_applicable",
                min_sample=min_sample,
                sample_size=sample_size,
                reason=f"{spec.name_es}: no aplica a esta temporalidad o fuente de datos",
                ui_path=ui_path,
                stage=spec.stage,
            )

        if sample_size >= min_sample:
            return FeatureStatus(
                id=spec.id,
                name_es=spec.name_es,
                category=spec.category,
                status="ready",
                min_sample=min_sample,
                sample_size=sample_size,
                ui_path=ui_path,
                stage=spec.stage,
            )

        return FeatureStatus(
            id=spec.id,
            name_es=spec.name_es,
            category=spec.category,
            status="insufficient",
            min_sample=min_sample,
            sample_size=sample_size,
            reason=(
                f"{spec.name_es}: muestra insuficiente ({sample_size} < {min_sample} requeridos)"
            ),
            ui_path=ui_path,
            stage=spec.stage,
        )
