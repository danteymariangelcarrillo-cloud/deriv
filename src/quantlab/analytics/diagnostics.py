"""Diagnósticos de residuos y autocorrelación (T027 y T015).

``residual_diagnostics`` migra ``residual_diagnostics`` del prototipo
(L1554-L1596): Ljung-Box, Jarque-Bera, ARCH-LM y ADF sobre retornos ya
expresados en porcentaje. ``autocorrelation_result`` implementa el panel de
autocorrelación de ``plot_autocorrelation`` (L1483-L1519) como datos, sin Plotly.

Cada contraste se ejecuta de forma aislada: si una librería falla, su bloque
contiene ``{"error": ...}`` y el resto sigue disponible. El motor (T017) decide
si eso invalida el análisis.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.tsa.stattools import adfuller

__all__ = ["autocorrelation_result", "residual_diagnostics"]


def _clean_returns(returns: pd.Series | np.ndarray) -> np.ndarray:
    values = np.asarray(returns, dtype=float)
    return values[np.isfinite(values)]


def residual_diagnostics(
    returns: pd.Series | np.ndarray,
    max_lag: int = 20,
) -> dict[str, Any]:
    """Contrastes de normalidad, autocorrelación, heterocedasticidad y raíz unitaria."""
    if max_lag < 1:
        raise ValueError("max_lag debe ser >= 1")
    values = _clean_returns(returns)
    if len(values) < max_lag + 10:
        return {"error": "Insuficientes datos para diagnosticos."}

    result: dict[str, Any] = {"n_observations": len(values)}

    try:
        box = acorr_ljungbox(values, lags=[max_lag], return_df=True)
        p_value = float(box["lb_pvalue"].iloc[0])
        result["ljung_box"] = {
            "stat": float(box["lb_stat"].iloc[0]),
            "p": p_value,
            "reject": p_value < 0.05,
        }
    except Exception as exc:  # noqa: BLE001 - se informa por bloque
        result["ljung_box"] = {"error": str(exc)}

    try:
        jb_stat, jb_p = stats.jarque_bera(values)
        result["jarque_bera"] = {
            "stat": float(jb_stat),
            "p": float(jb_p),
            "reject": float(jb_p) < 0.05,
        }
    except Exception as exc:  # noqa: BLE001
        result["jarque_bera"] = {"error": str(exc)}

    try:
        arch_stat, arch_p, _, _ = het_arch(values, nlags=max_lag)
        result["arch_lm"] = {
            "stat": float(arch_stat),
            "p": float(arch_p),
            "lags": int(max_lag),
            "reject": float(arch_p) < 0.05,
        }
    except Exception as exc:  # noqa: BLE001
        result["arch_lm"] = {"error": str(exc)}

    try:
        adf = adfuller(values, maxlag=max_lag, autolag="AIC", result_object=False)
        result["adf"] = {
            "stat": float(adf[0]),
            "p": float(adf[1]),
            "lags": int(adf[2]),
            "is_stationary": float(adf[1]) < 0.05,
            "critical_values": {key: float(value) for key, value in adf[4].items()},
        }
    except Exception as exc:  # noqa: BLE001
        result["adf"] = {"error": str(exc)}

    return result


def autocorrelation_result(
    returns: pd.Series | np.ndarray,
    max_lag: int = 50,
) -> dict[str, Any]:
    """ACF de retornos y retornos al cuadrado con bandas aproximadas al 95 %.

    Replica el cálculo de ``plot_autocorrelation``: una correlación por retardo
    y banda ``1.96 / sqrt(n)``. Con ``n <= max_lag + 5`` devuelve error, igual
    que el prototipo.
    """
    if max_lag < 1 or max_lag > 100:
        raise ValueError("max_lag debe estar entre 1 y 100")
    values = _clean_returns(returns)
    if len(values) <= max_lag + 5:
        return {"error": "Hay muy pocos retornos para el máximo retardo solicitado."}

    series = pd.Series(values)
    squared = series.pow(2)
    acf_returns = [float(series.autocorr(lag)) for lag in range(1, max_lag + 1)]
    acf_squared = [float(squared.autocorr(lag)) for lag in range(1, max_lag + 1)]
    band = float(1.96 / np.sqrt(len(values)))

    # El contrato del snapshot expone los p-valores de Ljung-Box en los
    # retardos 10 y 20 (no el estadístico del retardo máximo).
    ljung = acorr_ljungbox(values, lags=[10, 20], return_df=True)
    return {
        "n_obs": len(values),
        "max_lag": int(max_lag),
        "acf_returns": acf_returns,
        "acf_squared": acf_squared,
        "conf_band": band,
        "ljung_box": {
            "lag_10_p": float(ljung["lb_pvalue"].iloc[0]),
            "lag_20_p": float(ljung["lb_pvalue"].iloc[1]),
        },
    }
