"""Volatilidad: GARCH(1,1) y regímenes Markov/CUSUM (T025).

``fit_garch`` es la migración de ``fit_garch`` del prototipo (L1598-L1623).
``detect_volatility_regimes`` no existía en el prototipo como función: se
implementa según ``tasks.md`` T025 y ``data-model.md`` §4.3, con
``MarkovRegression`` (statsmodels) y caída a CUSUM cuando no convergen o no hay
muestra suficiente.

Todo es puro y determinista salvo el optimizador numérico de ``arch`` y el EM de
statsmodels; no hay semillas aleatorias. La serie se reduce con LTTB a
``max_points`` para no publicar más de 5000 puntos (schema del snapshot).
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from arch import arch_model
from scipy.special import expit
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

from quantlab.analytics.diagnostics import residual_diagnostics
from quantlab.utils import downsample_lttb

__all__ = ["CUSUM_THRESHOLD", "detect_volatility_regimes", "fit_garch"]

CUSUM_THRESHOLD = 5.0
REGIME_WINDOW = 20


def _returns_array(returns: pd.Series | np.ndarray) -> np.ndarray:
    values = np.asarray(returns, dtype=float)
    return values[np.isfinite(values)]


def fit_garch(
    returns: pd.Series | np.ndarray,
    p: int = 1,
    q: int = 1,
) -> dict[str, Any]:
    """Ajusta un GARCH(p, q) normal a retornos ya expresados en porcentaje.

    Migración de ``fit_garch``: mismos parámetros, mismo ``arch_model``, mismo
    pronóstico a 10 pasos. Devuelve ``{"error": ...}`` en lugar de lanzar si no
    hay muestra suficiente o el ajuste falla, como el prototipo.
    """
    if p < 1 or q < 1:
        raise ValueError("p y q deben ser >= 1")
    values = _returns_array(returns)
    if len(values) < 100:
        return {"error": "Insuficientes datos para GARCH (minimo 100 observaciones)."}
    try:
        model = arch_model(values, vol="Garch", p=p, q=q, dist="normal")
        fit = model.fit(update_freq=0, disp="off")
        forecast = fit.forecast(horizon=10)
        variance = forecast.variance.iloc[-1].to_numpy(dtype=float)
        mean = np.asarray(forecast.mean.iloc[-1].to_numpy(dtype=float))
        sigma = np.sqrt(variance)
        # Diagnósticos sobre residuos estandarizados; el contrato del snapshot
        # exige ``ljung_box`` y ``arch_lm`` cuando el ajuste está listo.
        standardized = fit.resid / fit.conditional_volatility
        raw = residual_diagnostics(standardized, max_lag=20)
        diagnostics = {
            "ljung_box": {
                "stat": float(raw.get("ljung_box", {}).get("stat", 0.0)),
                "p": float(raw.get("ljung_box", {}).get("p", 1.0)),
            },
            "arch_lm": {
                "stat": float(raw.get("arch_lm", {}).get("stat", 0.0)),
                "p": float(raw.get("arch_lm", {}).get("p", 1.0)),
            },
        }
        return {
            "order": (p, q),
            "params": {k: float(v) for k, v in fit.params.items()},
            "t_values": {k: float(v) for k, v in fit.tvalues.items()},
            "p_values": {k: float(v) for k, v in fit.pvalues.items()},
            "log_likelihood": float(fit.loglikelihood),
            "aic": float(fit.aic),
            "bic": float(fit.bic),
            "conditional_volatility": fit.conditional_volatility.tolist(),
            "forecast_horizon_10": variance.tolist(),
            "forecast": [
                {
                    "step": step + 1,
                    "mean": float(mean[step]),
                    "variance": float(variance[step]),
                    "sigma": float(sigma[step]),
                    "ci_lower": float(mean[step] - 1.96 * sigma[step]),
                    "ci_upper": float(mean[step] + 1.96 * sigma[step]),
                }
                for step in range(len(variance))
            ],
            "n_obs": len(values),
            "converged": bool(fit.convergence_flag == 0),
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001 - el prototipo degrada a error
        return {"error": str(exc)}


def _segments(
    probabilities: np.ndarray,
    epochs: np.ndarray,
    realized_vol: np.ndarray,
) -> list[dict[str, Any]]:
    """Tramos continuos entre cambios del régimen dominante."""
    dominant = (probabilities >= 0.5).astype(int)
    segments: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(dominant)):
        if dominant[index] != dominant[start]:
            chunk = realized_vol[start:index]
            segments.append(
                {
                    "start_epoch": int(epochs[start]),
                    "end_epoch": int(epochs[index - 1]),
                    "regime": int(dominant[start]),
                    "mean_variance": float(np.mean(np.square(chunk))) if len(chunk) else 0.0,
                }
            )
            start = index
    chunk = realized_vol[start:]
    segments.append(
        {
            "start_epoch": int(epochs[start]),
            "end_epoch": int(epochs[-1]),
            "regime": int(dominant[start]),
            "mean_variance": float(np.mean(np.square(chunk))) if len(chunk) else 0.0,
        }
    )
    return segments


def _markov_regimes(
    realized_vol: np.ndarray,
) -> tuple[np.ndarray, bool, str | None]:
    """Probabilidades suavizadas del régimen de alta volatilidad (columna 1)."""
    try:
        model = MarkovRegression(
            realized_vol,
            k_regimes=2,
            switching_variance=True,
        )
        fitted = model.fit(disp=False)
        probabilities = np.asarray(
            getattr(
                fitted,
                "smoothed_marginal_probabilities",
                fitted.filtered_marginal_probabilities,
            )
        )
        if probabilities.ndim != 2 or probabilities.shape[1] != 2:
            return np.empty(0), False, "probabilidades de régimen con forma inesperada"
        params = fitted.params
        sigma_names = [name for name in params.index if name.startswith("sigma2")]
        if len(sigma_names) == 2 and float(params[sigma_names[0]]) > float(params[sigma_names[1]]):
            probabilities = probabilities[:, ::-1]
        return probabilities[:, 1], True, None
    except Exception as exc:  # noqa: BLE001 - se cae a CUSUM
        return np.empty(0), False, f"Markov no disponible: {type(exc).__name__}: {exc}"


def _cusum_regimes(realized_vol: np.ndarray) -> np.ndarray:
    """CUSUM normalizado con umbral ``CUSUM_THRESHOLD`` sobre la media."""
    target = float(np.median(realized_vol))
    scale = float(np.std(realized_vol, ddof=1))
    if not np.isfinite(scale) or scale <= 0:
        scale = 1.0
    values = np.empty(len(realized_vol))
    accumulator = 0.0
    for i, value in enumerate(realized_vol):
        accumulator = max(0.0, accumulator + (value - target) / scale)
        values[i] = accumulator
    return expit(values - CUSUM_THRESHOLD / 2.0)


def detect_volatility_regimes(
    returns: pd.Series | np.ndarray,
    n_regimes: int = 2,
    method: Literal["markov_switching", "cusum"] = "markov_switching",
    *,
    epochs: np.ndarray | pd.Series | None = None,
    max_points: int = 5000,
) -> dict[str, Any]:
    """Detecta dos regímenes de volatilidad (baja/alta) y su probabilidad actual.

    ``realized_vol`` es la desviación estándar móvil de 20 retornos. Con
    ``method="markov_switching"`` y al menos 500 observaciones se ajusta un
    ``MarkovRegression`` de dos regímenes con varianza conmutada; si no converge
    (o si falla), se aplica el CUSUM documentado en T025.

    Args:
        returns: retornos en porcentaje.
        n_regimes: sólo se admite 2 (contrato del snapshot).
        method: ``"markov_switching"`` o ``"cusum"``.
        epochs: epochs alineados con ``returns``; si faltan se usan índices.
        max_points: máximo de puntos de ``series`` (LTTB).
    """
    if n_regimes != 2:
        raise ValueError("sólo se admiten 2 regímenes")
    if method not in {"markov_switching", "cusum"}:
        raise ValueError("method debe ser markov_switching o cusum")

    values = np.asarray(returns, dtype=float)
    if epochs is None:
        aligned_epochs = np.arange(len(values), dtype=np.int64)
    else:
        aligned_epochs = np.asarray(epochs, dtype=np.int64)
        if len(aligned_epochs) != len(values):
            raise ValueError("epochs debe tener la misma longitud que returns")

    realized = pd.Series(values).rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).std().dropna()
    if realized.empty:
        return {
            "status": "insufficient",
            "method": method,
            "n_regimes": 2,
            "n_obs": 0,
            "high_vol_prob_current": None,
            "series": [],
            "segments": [],
            "last_switch_epoch": None,
            "converged": False,
            "message": "sin observaciones suficientes para la volatilidad realizada",
        }

    offset = REGIME_WINDOW - 1
    realized_values = realized.to_numpy(dtype=float)
    realized_epochs = aligned_epochs[offset : offset + len(realized_values)]

    used_method: Literal["markov_switching", "cusum"] = method
    message: str | None = None
    converged = False
    probabilities = np.empty(0)

    if method == "markov_switching" and len(realized_values) >= 500:
        probabilities, converged, message = _markov_regimes(realized_values)
        if not converged:
            used_method = "cusum"

    if used_method == "cusum" or probabilities.size == 0:
        probabilities = _cusum_regimes(realized_values)
        converged = False
        if method == "markov_switching" and message is None:
            message = "muestra < 500: se usó CUSUM"

    series_frame = np.column_stack([realized_epochs.astype(float), probabilities.astype(float)])
    if len(series_frame) > max_points:
        series_frame = downsample_lttb(series_frame, max_points)

    series = [
        {"epoch": int(epoch), "prob_high": float(np.clip(prob, 0.0, 1.0))}
        for epoch, prob in series_frame
    ]

    dominant = (probabilities >= 0.5).astype(int)
    switches = np.flatnonzero(np.diff(dominant) != 0)
    last_switch_epoch = int(realized_epochs[switches[-1] + 1]) if len(switches) else None

    return {
        "status": "ready",
        "method": used_method,
        "n_regimes": 2,
        "n_obs": len(realized_values),
        "high_vol_prob_current": float(np.clip(probabilities[-1], 0.0, 1.0)),
        "series": series,
        "segments": _segments(probabilities, realized_epochs, realized_values),
        "last_switch_epoch": last_switch_epoch,
        "converged": converged,
        "message": message,
    }
