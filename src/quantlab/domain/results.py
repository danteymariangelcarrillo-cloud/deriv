"""Modelos de resultados analíticos embebidos en el snapshot (T013).

Replican los ``$defs`` de ``contracts/snapshot.schema.json`` para que
``TimeframeAnalysis`` sea completamente tipado en vez de transportar ``dict``
sueltos. Los cálculos de T021-T028 devuelven diccionarios; ``T017`` los valida
con estos modelos antes de construir el snapshot.

Nota de contrato: el schema exige ``return_levels`` con las claves 10/50/100/250
y ``params``/``p_values`` con omega/alpha1/beta1 incluso en resultados
``insufficient``, donde no son calculables. Aquí se exige el tipo (número) pero
no la presencia de esas claves, para no bloquear el estado ``insufficient``; la
validación estricta del schema queda para el test de contrato (T038).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from quantlab.domain.types import AnalysisStatus, DomainModel

__all__ = [
    "ACFResult",
    "ConditionalBin",
    "ConditionalResult",
    "DiagnosticsResult",
    "EVTResult",
    "ForecastPoint",
    "GARCHResult",
    "MeanExcessPlot",
    "RegimePoint",
    "RegimeResult",
    "RegimeSegment",
    "SurvivalPoint",
    "SurvivalResult",
    "WalkForwardAggregate",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardResult",
]


class MeanExcessPlot(DomainModel):
    """Puntos del gráfico de exceso medio por umbral."""

    thresholds: tuple[float, ...] = ()
    mean_excess: tuple[float, ...] = ()
    n_excess: tuple[int, ...] = ()


class EVTResult(DomainModel):
    """Ajuste GPD por exceso de umbral (``analytics/evt.py``)."""

    status: AnalysisStatus
    threshold: float
    threshold_quantile: float
    n_excesses: int = Field(ge=0)
    xi: float | None = None
    sigma: float | None = None
    xi_se: float | None = None
    sigma_se: float | None = None
    log_likelihood: float | None = None
    converged: bool = False
    ks_stat: float | None = None
    ks_p: float | None = None
    return_levels: dict[str, float] = Field(default_factory=dict)
    mean_excess_plot: MeanExcessPlot = Field(default_factory=MeanExcessPlot)
    message: str | None = None


class ForecastPoint(DomainModel):
    """Pronóstico GARCH de un paso."""

    step: int = Field(ge=1)
    mean: float
    variance: float
    sigma: float
    ci_lower: float
    ci_upper: float


class GARCHResult(DomainModel):
    """Ajuste GARCH(1,1) con diagnóstico y pronóstico."""

    status: AnalysisStatus
    model: Literal["GARCH(1,1)"] = "GARCH(1,1)"
    distribution: Literal["normal", "t"] = "normal"
    n_obs: int = Field(ge=0)
    params: dict[str, float] = Field(default_factory=dict)
    p_values: dict[str, float] = Field(default_factory=dict)
    persistence: float | None = None
    aic: float | None = None
    bic: float | None = None
    log_likelihood: float | None = None
    forecast: tuple[ForecastPoint, ...] = Field(default=(), max_length=10)
    diagnostics: dict[str, dict[str, float]] = Field(default_factory=dict)
    converged: bool = False
    message: str | None = None


class RegimePoint(DomainModel):
    """Probabilidad de régimen de alta volatilidad en un epoch."""

    epoch: int
    prob_high: float = Field(ge=0.0, le=1.0)


class RegimeSegment(DomainModel):
    """Tramo continuo con un régimen dominante."""

    start_epoch: int
    end_epoch: int
    regime: Literal[0, 1]
    mean_variance: float

    @model_validator(mode="after")
    def _ordered(self) -> RegimeSegment:
        if self.end_epoch < self.start_epoch:
            raise ValueError(
                f"end_epoch={self.end_epoch} es anterior a start_epoch={self.start_epoch}"
            )
        return self


class RegimeResult(DomainModel):
    """Regímenes de volatilidad (Markov-switching o CUSUM)."""

    status: AnalysisStatus
    method: Literal["markov_switching", "cusum"]
    n_regimes: Literal[2] = 2
    n_obs: int = Field(ge=0)
    high_vol_prob_current: float | None = Field(default=None, ge=0.0, le=1.0)
    series: tuple[RegimePoint, ...] = Field(default=(), max_length=5000)
    segments: tuple[RegimeSegment, ...] = ()
    last_switch_epoch: int | None = None
    converged: bool = False
    message: str | None = None


class SurvivalPoint(DomainModel):
    """Punto de la curva de supervivencia Kaplan-Meier."""

    time: float
    survival: float = Field(ge=0.0, le=1.0)
    ci_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_upper: float | None = Field(default=None, ge=0.0, le=1.0)
    at_risk: int = Field(ge=0)


class SurvivalResult(DomainModel):
    """Supervivencia entre eventos consecutivos."""

    status: AnalysisStatus
    mode: Literal["event_index", "time"]
    n_events: int = Field(ge=0)
    n_censored: int = Field(ge=0)
    median: float | None = None
    median_ci: tuple[float, float] | None = None
    curve: tuple[SurvivalPoint, ...] = ()
    message: str | None = None


class ACFResult(DomainModel):
    """Autocorrelación de retornos y de retornos al cuadrado."""

    n_obs: int = Field(ge=100)
    max_lag: int = Field(ge=1, le=100)
    acf_returns: tuple[float, ...] = ()
    acf_squared: tuple[float, ...] = ()
    conf_band: float
    ljung_box: dict[str, float] = Field(default_factory=dict)
    message: str | None = None


class DiagnosticsResult(DomainModel):
    """Contrastes sobre los residuos del modelo."""

    jarque_bera: dict[str, float] = Field(default_factory=dict)
    # ``lags`` es entero en el contrato; ``object`` evita que pydantic lo
    # convierta a float al serializar.
    arch_lm: dict[str, object] = Field(default_factory=dict)
    adf: dict[str, object] = Field(default_factory=dict)
    message: str | None = None

    @model_validator(mode="after")
    def _required_stats(self) -> DiagnosticsResult:
        if set(self.jarque_bera) < {"stat", "p"}:
            raise ValueError("jarque_bera debe incluir 'stat' y 'p'")
        if set(self.arch_lm) < {"stat", "p", "lags"}:
            raise ValueError("arch_lm debe incluir 'stat', 'p' y 'lags'")
        if set(self.adf) < {"stat", "p", "lags", "critical_values"}:
            raise ValueError("adf debe incluir 'stat', 'p', 'lags' y 'critical_values'")
        return self


class ConditionalBin(DomainModel):
    """Bin del histograma condicional de eventos."""

    bin_start: int
    bin_end: int
    n_events: int = Field(ge=0)
    n_trials: int = Field(ge=0)
    probability: float = Field(ge=0.0, le=1.0)
    ci_lower: float = Field(ge=0.0, le=1.0)
    ci_upper: float = Field(ge=0.0, le=1.0)


class ConditionalResult(DomainModel):
    """Tabla condicional de eventos por bin."""

    status: AnalysisStatus
    mode: Literal["bars", "ticks"]
    horizon: int = Field(ge=1)
    bin_size: int = Field(ge=1)
    bins: tuple[ConditionalBin, ...] = ()
    note: str = ""


class WalkForwardConfig(DomainModel):
    """Ventanas de la validación walk-forward."""

    train_bars: int = Field(ge=0)
    test_bars: int = Field(ge=0)
    step_bars: int = Field(ge=1)
    min_folds: int = Field(ge=1)


class WalkForwardFold(DomainModel):
    """Un fold temporal ya evaluado sobre datos fuera de muestra."""

    fold_id: str
    train_start_epoch: int
    train_end_epoch: int
    test_start_epoch: int
    test_end_epoch: int
    bars_train: int = Field(ge=0)
    bars_test: int = Field(ge=0)
    zones_evaluated: int = Field(ge=0)
    reactions: int = Field(ge=0)
    evaluated_touches: int = Field(ge=0)
    reaction_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_lower: float | None = Field(default=None, ge=0.0, le=1.0)
    ci_upper: float | None = Field(default=None, ge=0.0, le=1.0)
    p_value_raw: float | None = Field(default=None, ge=0.0, le=1.0)
    p_value_adj: float | None = Field(default=None, ge=0.0, le=1.0)
    stage: Literal["oos"] = "oos"

    @model_validator(mode="after")
    def _no_leakage(self) -> WalkForwardFold:
        if self.test_start_epoch <= self.train_end_epoch:
            raise ValueError(
                "test_start_epoch debe ser posterior a train_end_epoch (sin fuga temporal)"
            )
        return self


class WalkForwardAggregate(DomainModel):
    """Agregado de folds; el sign test es descriptivo (no independiente)."""

    folds: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    reactions: int = Field(ge=0)
    rate: float = Field(ge=0.0, le=1.0)
    ci: dict[str, float] = Field(default_factory=dict)
    sign_test_p: float = Field(ge=0.0, le=1.0)
    independent: Literal[False] = False

    @model_validator(mode="after")
    def _ci_bounds(self) -> WalkForwardAggregate:
        if set(self.ci) != {"lower", "upper"}:
            raise ValueError("ci debe incluir exactamente 'lower' y 'upper'")
        return self


class WalkForwardResult(DomainModel):
    """Validación walk-forward anclada con advertencia de no independencia."""

    status: AnalysisStatus
    method: Literal["anchored"] = "anchored"
    config: WalkForwardConfig
    folds: tuple[WalkForwardFold, ...] = ()
    aggregate: WalkForwardAggregate
    warning: str
    recommendation: str | None = None
