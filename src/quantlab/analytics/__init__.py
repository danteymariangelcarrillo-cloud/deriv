"""Paquete de analítica pura de la Fase 1 (T017-T028).

Todo el cálculo es síncrono, puro y sin I/O. La fachada de consumo es
:class:`quantlab.analytics.engine.AnalyticsEngine`; las funciones exportadas aquí
son las migraciones directas del prototipo que usan los tests de paridad.

Las exportaciones son **perezosas** (PEP 562): importar ``quantlab.analytics``
no arrastra ``scipy``/``statsmodels``, de modo que el arranque del dashboard no
paga ~4 s de imports que sólo necesita el primer cálculo.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY: dict[str, tuple[str, str]] = {
    "WALK_FORWARD_WARNING": ("walkforward", "WALK_FORWARD_WARNING"),
    "AnalyticsEngine": ("engine", "AnalyticsEngine"),
    "add_atr": ("indicators", "add_atr"),
    "aggregate_walk_forward_results": ("walkforward", "aggregate_walk_forward_results"),
    "autocorrelation_result": ("diagnostics", "autocorrelation_result"),
    "benjamini_hochberg_fdr": ("stats", "benjamini_hochberg_fdr"),
    "binomial_test_pvalue": ("stats", "binomial_test_pvalue"),
    "bootstrap_ci": ("stats", "bootstrap_ci"),
    "candle_events_series": ("events", "candle_events_series"),
    "cluster_levels": ("zones", "cluster_levels"),
    "conditional_event_table": ("events", "conditional_event_table"),
    "conditional_result": ("events", "conditional_result"),
    "detect_candle_extremes": ("events", "detect_candle_extremes"),
    "detect_reaction_zones": ("zones", "detect_reaction_zones"),
    "detect_tick_extremes": ("events", "detect_tick_extremes"),
    "detect_volatility_regimes": ("volatility", "detect_volatility_regimes"),
    "directional_returns": ("indicators", "directional_returns"),
    "evaluate_zone": ("zones", "evaluate_zone"),
    "event_index_kaplan_meier": ("survival", "event_index_kaplan_meier"),
    "find_pivots": ("zones", "find_pivots"),
    "fit_evt_gpd": ("evt", "fit_evt_gpd"),
    "fit_garch": ("volatility", "fit_garch"),
    "fit_gpd_mle": ("evt", "fit_gpd_mle"),
    "gpd_return_level": ("evt", "gpd_return_level"),
    "kaplan_meier": ("survival", "kaplan_meier"),
    "ks_exponential": ("survival", "ks_exponential"),
    "log_returns": ("indicators", "log_returns"),
    "residual_diagnostics": ("diagnostics", "residual_diagnostics"),
    "rolling_quantile": ("indicators", "rolling_quantile"),
    "survival_result": ("survival", "survival_result"),
    "tick_events_series": ("events", "tick_events_series"),
    "time_kaplan_meier": ("survival", "time_kaplan_meier"),
    "to_reaction_zones": ("zones", "to_reaction_zones"),
    "walk_forward_result": ("walkforward", "walk_forward_result"),
    "walk_forward_validation": ("walkforward", "walk_forward_validation"),
    "wilson_score_interval": ("stats", "wilson_score_interval"),
}

__all__ = [
    "WALK_FORWARD_WARNING",
    "AnalyticsEngine",
    "add_atr",
    "aggregate_walk_forward_results",
    "autocorrelation_result",
    "benjamini_hochberg_fdr",
    "binomial_test_pvalue",
    "bootstrap_ci",
    "candle_events_series",
    "cluster_levels",
    "conditional_event_table",
    "conditional_result",
    "detect_candle_extremes",
    "detect_reaction_zones",
    "detect_tick_extremes",
    "detect_volatility_regimes",
    "directional_returns",
    "evaluate_zone",
    "event_index_kaplan_meier",
    "find_pivots",
    "fit_evt_gpd",
    "fit_garch",
    "fit_gpd_mle",
    "gpd_return_level",
    "kaplan_meier",
    "ks_exponential",
    "log_returns",
    "residual_diagnostics",
    "rolling_quantile",
    "survival_result",
    "tick_events_series",
    "time_kaplan_meier",
    "to_reaction_zones",
    "walk_forward_result",
    "walk_forward_validation",
    "wilson_score_interval",
]


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
