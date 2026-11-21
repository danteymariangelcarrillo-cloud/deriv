"""Teoría de valores extremos: ajuste GPD y niveles de retorno (T022).

``fit_gpd_mle`` y ``gpd_return_level`` son migraciones exactas del prototipo
(``legacy/boom_crash_quant_analyzer.py`` L119-L170): mismo log-likelihood
manual, mismos valores iniciales, mismos límites, mismo optimizador L-BFGS-B y
mismo hessiano numérico para los errores estándar.

``fit_evt_gpd`` es la función que el prototipo **llamaba pero nunca definía**
(``plot_return_distribution`` L1363/L1380). Se implementa completa según
T022 y ``data-model.md`` §4.1: umbral por cuantil, excesos, MLE, KS, mean
excess plot y niveles de retorno, con el gate ``n_excesses < min_excesses``.

Todo es puro y síncrono: no escribe en disco, no usa red y no muta la entrada.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats
from scipy.optimize import minimize

__all__ = ["fit_evt_gpd", "fit_gpd_mle", "gpd_return_level"]

# Umbrales del mean excess plot: 20 puntos entre P90 y P99.5 (T022).
_MEAN_EXCESS_QUANTILES = np.linspace(0.90, 0.995, 20)
_RETURN_PERIODS = (10, 50, 100, 250)


def fit_gpd_mle(excesses: np.ndarray) -> tuple[float, float, dict[str, Any]]:
    """Ajusta una GPD a los excesos sobre el umbral por máxima verosimilitud.

    Migración literal de ``fit_gpd_mle`` del prototipo: si hay menos de 10
    excesos positivos devuelve ``(nan, nan, {"error": ...})``; en caso contrario
    maximiza el log-likelihood con ``L-BFGS-B`` y calcula los errores estándar
    con un hessiano numérico de diferencias finitas.

    Args:
        excesses: excesos positivos sobre el umbral (se descartan los ``<= 0``).

    Returns:
        ``(xi, sigma, diagnostics)`` con ``xi``/``sigma`` y claves
        ``n_excesses``, ``xi_se``, ``sigma_se``, ``log_likelihood`` y
        ``converged`` en ``diagnostics``.
    """
    values = np.asarray(excesses)
    if len(values) < 10:
        return (
            np.nan,
            np.nan,
            {"error": "SciPy not available or insufficient data"},
        )
    values = values[values > 0]
    if len(values) < 10:
        return np.nan, np.nan, {"error": "Insufficient positive excesses"}

    def negative_log_likelihood(params: np.ndarray) -> float:
        xi, sigma = params
        if sigma <= 0:
            return 1e10
        if xi == 0:
            return np.sum(np.log(sigma) + values / sigma)
        if np.any(1 + xi * values / sigma <= 0):
            return 1e10
        return np.sum(np.log(sigma) + (1 + 1 / xi) * np.log(1 + xi * values / sigma))

    mean_excess = np.mean(values)
    variance = np.var(values, ddof=1)
    if variance > 0 and mean_excess > 0:
        xi_init = 0.5 * (1 - mean_excess**2 / variance)
        sigma_init = mean_excess * (1 - xi_init)
    else:
        xi_init, sigma_init = 0.1, mean_excess

    bounds = [(-0.99, 0.99), (1e-8, None)]
    try:
        result = minimize(
            negative_log_likelihood,
            [xi_init, sigma_init],
            bounds=bounds,
            method="L-BFGS-B",
        )
        xi_hat, sigma_hat = result.x

        eps = 1e-6
        hessian = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                params = np.array([xi_hat, sigma_hat])
                params_plus = params.copy()
                params_plus[i] += eps
                params_minus = params.copy()
                params_minus[i] -= eps
                f_plus = negative_log_likelihood(params_plus)
                f_minus = negative_log_likelihood(params_minus)
                grad_plus = (f_plus - f_minus) / (2 * eps)

                params_plus_2 = params.copy()
                params_plus_2[j] += eps
                params_minus_2 = params.copy()
                params_minus_2[j] -= eps
                f_plus_2 = negative_log_likelihood(params_plus_2)
                f_minus_2 = negative_log_likelihood(params_minus_2)
                grad_minus = (f_plus_2 - f_minus_2) / (2 * eps)

                hessian[i, j] = (grad_plus - grad_minus) / (2 * eps)

        try:
            covariance = np.linalg.inv(hessian)
            se_xi, se_sigma = np.sqrt(np.diag(covariance))
        except np.linalg.LinAlgError:
            se_xi, se_sigma = np.nan, np.nan

        return (
            float(xi_hat),
            float(sigma_hat),
            {
                "n_excesses": len(values),
                "xi": float(xi_hat),
                "sigma": float(sigma_hat),
                "xi_se": float(se_xi) if np.isfinite(se_xi) else np.nan,
                "sigma_se": float(se_sigma) if np.isfinite(se_sigma) else np.nan,
                "log_likelihood": -float(result.fun),
                "converged": bool(result.success),
            },
        )
    except Exception as exc:  # noqa: BLE001 - el prototipo degrada a error, no lanza
        return np.nan, np.nan, {"error": str(exc)}


def gpd_return_level(
    xi: float,
    sigma: float,
    threshold: float,
    n_obs: int,
    n_excesses: int,
    return_period: int,
) -> float:
    """Nivel de retorno para un periodo expresado en observaciones.

    Migración exacta del prototipo: con ``xi == 0`` usa la forma exponencial y en
    otro caso la inversa de la GPD. La probabilidad de cola es
    ``n_excesses / n_obs``.

    Args:
        xi: parámetro de forma de la GPD.
        sigma: parámetro de escala de la GPD.
        threshold: umbral sobre el que se calcularon los excesos.
        n_obs: número total de observaciones de la serie.
        n_excesses: número de excesos sobre el umbral.
        return_period: periodo de retorno en observaciones.

    Returns:
        El nivel de retorno, o ``nan`` si ``n_obs``/``n_excesses`` no son
        positivos.
    """
    if n_excesses <= 0 or n_obs <= 0:
        return np.nan
    probability = n_excesses / n_obs
    if xi == 0:
        return float(threshold + sigma * np.log(return_period * probability))
    return float(threshold + sigma / xi * ((return_period * probability) ** xi - 1))


def fit_evt_gpd(
    returns: np.ndarray,
    threshold_quantile: float = 0.95,
    direction: str = "directional",
    min_excesses: int = 30,
) -> dict[str, Any]:
    """Ajuste EVT/GPD completo sobre retornos direccionales.

    Pipeline (T022):

    1. Recorta los retornos al sentido del instrumento: ``"up"`` conserva los
       positivos, ``"down"`` invierte y conserva los positivos, y
       ``"directional"`` asume que la serie ya viene recortada (se fuerza a no
       negativa). Los valores no finitos se descartan.
    2. Calcula el umbral como cuantil ``threshold_quantile`` de los valores
       positivos.
    3. Toma los excesos estrictamente por encima del umbral y aplica el gate
       ``n_excesses < min_excesses`` → ``status = "insufficient"``.
    4. Ajusta la GPD con :func:`fit_gpd_mle`.
    5. Contraste KS de los excesos contra la GPD ajustada.
    6. Mean excess plot con 20 umbrales entre P90 y P99.5.
    7. Niveles de retorno para 10, 50, 100 y 250 periodos.

    Args:
        returns: serie de retornos (en las mismas unidades que el umbral).
        threshold_quantile: cuantil del umbral sobre los valores positivos.
        direction: ``"up"``, ``"down"`` o ``"directional"``.
        min_excesses: mínimo de excesos para intentar el ajuste.

    Returns:
        Diccionario con el contrato ``EVTResult`` de ``data-model.md`` §4.1:
        ``status``, ``threshold``, ``threshold_quantile``, ``n_excesses``,
        ``xi``, ``sigma``, ``xi_se``, ``sigma_se``, ``log_likelihood``,
        ``converged``, ``ks_stat``, ``ks_p``, ``return_levels``,
        ``mean_excess_plot`` y ``message``.

    Raises:
        ValueError: si ``direction`` no es válida.
    """
    if not 0.0 < threshold_quantile < 1.0:
        raise ValueError("threshold_quantile debe estar en (0, 1)")

    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]

    if direction == "up":
        directional = values.clip(min=0)
    elif direction == "down":
        directional = (-values).clip(min=0)
    elif direction == "directional":
        directional = np.clip(values, 0.0, None)
    else:
        raise ValueError(f"direction inválida: {direction!r}; use 'up', 'down' o 'directional'")

    result: dict[str, Any] = {
        "status": "insufficient",
        "threshold": 0.0,
        "threshold_quantile": threshold_quantile,
        "n_excesses": 0,
        "xi": None,
        "sigma": None,
        "xi_se": None,
        "sigma_se": None,
        "log_likelihood": None,
        "converged": False,
        "ks_stat": None,
        "ks_p": None,
        "return_levels": {},
        "mean_excess_plot": {},
        "message": None,
    }

    positive = directional[directional > 0]
    if positive.size == 0:
        result["message"] = "sin retornos positivos para el umbral"
        return result

    threshold = float(np.quantile(positive, threshold_quantile))
    excesses = directional[directional > threshold] - threshold
    result["threshold"] = threshold
    result["n_excesses"] = int(excesses.size)

    if excesses.size < min_excesses:
        result["message"] = f"excesos insuficientes: {excesses.size} < {min_excesses}"
        return result

    xi, sigma, diagnostics = fit_gpd_mle(excesses)
    if "error" in diagnostics:
        result["status"] = "error"
        result["message"] = str(diagnostics["error"])
        return result

    result.update(
        {
            "status": "ready",
            "xi": xi,
            "sigma": sigma,
            "xi_se": diagnostics["xi_se"],
            "sigma_se": diagnostics["sigma_se"],
            "log_likelihood": diagnostics["log_likelihood"],
            "converged": bool(diagnostics["converged"]),
        }
    )

    ks_stat, ks_p = stats.kstest(excesses, "genpareto", args=(xi, 0, sigma))
    result["ks_stat"] = float(ks_stat)
    result["ks_p"] = float(ks_p)

    mean_excess_plot: dict[str, list[float]] = {
        "thresholds": [],
        "mean_excess": [],
        "n_excess": [],
    }
    for quantile in _MEAN_EXCESS_QUANTILES:
        level = float(np.quantile(directional, quantile))
        tail = directional[directional > level] - level
        if not tail.size:
            continue
        mean_excess_plot["thresholds"].append(level)
        mean_excess_plot["mean_excess"].append(float(tail.mean()))
        mean_excess_plot["n_excess"].append(float(tail.size))
    result["mean_excess_plot"] = mean_excess_plot

    n_obs = int(values.size)
    return_levels: dict[str, float] = {}
    for period in _RETURN_PERIODS:
        level = gpd_return_level(xi, sigma, threshold, n_obs, int(excesses.size), period)
        if np.isfinite(level):
            return_levels[str(period)] = float(level)
    result["return_levels"] = return_levels
    return result
