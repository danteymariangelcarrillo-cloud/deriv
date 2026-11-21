"""Supervivencia entre eventos: Kaplan-Meier (T026).

``kaplan_meier`` reproduce el estimador de ``plot_interevent_survival`` del
prototipo (L1177-L1207): product-limit con la última observación censurada. Se
añaden los intervalos de confianza de Greenwood (no estaban en el prototipo) y
el contraste KS contra una exponencial con ``lambda`` MLE, como pide T026.

Los tres modos de entrada son:

- ``durations``/``censored``: la forma canónica.
- ``event_index``: diferencias entre índices de eventos + cola censurada.
- ``time``: diferencias entre timestamps + cola censurada.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats

__all__ = [
    "event_index_kaplan_meier",
    "kaplan_meier",
    "ks_exponential",
    "survival_result",
    "time_kaplan_meier",
]

Z_95 = 1.959963984540054


def kaplan_meier(
    durations: np.ndarray,
    censored: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Estimador Kaplan-Meier con IC de Greenwood al 95 %.

    Args:
        durations: tiempos hasta el evento (o hasta la censura).
        censored: máscara booleana; ``True`` = observación censurada.

    Returns:
        ``(times, survival, ci_lower, ci_upper, at_risk)``. La curva arranca en
        ``(0, 1.0, 1.0, 1.0, n)``.
    """
    values = np.asarray(durations, dtype=float)
    if values.ndim != 1:
        raise ValueError("durations debe ser un array 1-D")
    if len(values) == 0:
        empty = np.array([], dtype=float)
        return empty, empty, empty, empty, np.array([], dtype=int)
    if censored is None:
        censored_array = np.zeros(len(values), dtype=bool)
    else:
        censored_array = np.asarray(censored, dtype=bool)
        if len(censored_array) != len(values):
            raise ValueError("censored debe tener la misma longitud que durations")
    if np.any(values <= 0):
        raise ValueError("las duraciones deben ser positivas")

    times: list[float] = [0.0]
    survival_values: list[float] = [1.0]
    lower_values: list[float] = [1.0]
    upper_values: list[float] = [1.0]
    at_risk_values: list[int] = [len(values)]

    survival = 1.0
    greenwood = 0.0
    for value in sorted(np.unique(values)):
        at_risk = int(np.sum(values >= value))
        events_at_value = int(np.sum((values == value) & ~censored_array))
        if at_risk and events_at_value:
            survival *= 1.0 - events_at_value / at_risk
            # Si todos los sujetos en riesgo fallan a la vez, la supervivencia
            # cae a 0 y el término de Greenwood es infinito: se omite para no
            # dividir por cero (el IC queda degenerado en 0).
            denominator = at_risk * (at_risk - events_at_value)
            if denominator > 0:
                greenwood += events_at_value / denominator
        se = survival * np.sqrt(greenwood) if greenwood > 0 else 0.0
        times.append(float(value))
        survival_values.append(float(survival))
        lower_values.append(float(max(0.0, survival - Z_95 * se)))
        upper_values.append(float(min(1.0, survival + Z_95 * se)))
        at_risk_values.append(at_risk)

    return (
        np.asarray(times),
        np.asarray(survival_values),
        np.asarray(lower_values),
        np.asarray(upper_values),
        np.asarray(at_risk_values, dtype=int),
    )


def event_index_kaplan_meier(
    event_indices: np.ndarray | list[int],
    n_observations: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Kaplan-Meier sobre índices de eventos; la cola final queda censurada.

    Devuelve además ``n_events`` (número de candidatos usados).
    """
    indices = np.sort(np.unique(np.asarray(event_indices, dtype=int)))
    indices = indices[(indices >= 0) & (indices < n_observations)]
    if len(indices) < 2:
        empty = np.array([], dtype=float)
        return empty, empty, empty, empty, np.array([], dtype=int), len(indices)
    durations = list(np.diff(indices).astype(int))
    observed = [True] * len(durations)
    tail = max(1, n_observations - 1 - int(indices[-1]))
    durations.append(tail)
    observed.append(False)
    times, survival, lower, upper, at_risk = kaplan_meier(
        np.asarray(durations, dtype=float), np.asarray(observed, dtype=bool)
    )
    return times, survival, lower, upper, at_risk, len(indices)


def time_kaplan_meier(
    event_times: np.ndarray | list[float],
    observation_end: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Kaplan-Meier sobre timestamps de eventos con cola censurada."""
    timestamps = np.sort(np.unique(np.asarray(event_times, dtype=float)))
    timestamps = timestamps[timestamps <= observation_end]
    if len(timestamps) < 2:
        empty = np.array([], dtype=float)
        return empty, empty, empty, empty, np.array([], dtype=int), len(timestamps)
    durations = list(np.diff(timestamps))
    observed = [True] * len(durations)
    durations.append(max(float(observation_end) - float(timestamps[-1]), 1e-9))
    observed.append(False)
    times, survival, lower, upper, at_risk = kaplan_meier(
        np.asarray(durations, dtype=float), np.asarray(observed, dtype=bool)
    )
    return times, survival, lower, upper, at_risk, len(timestamps)


def ks_exponential(durations: np.ndarray) -> dict[str, float]:
    """Contraste KS contra una exponencial con ``lambda`` MLE.

    El resultado no forma parte de ``SurvivalResult`` (el contrato del snapshot
    no expone el contraste), pero T026 exige calcularlo; los tests de paridad lo
    consumen directamente.
    """
    positive = durations[durations > 0]
    if len(positive) < 2:
        return {"stat": float("nan"), "p": float("nan")}
    mean = float(np.mean(positive))
    if mean <= 0:
        return {"stat": float("nan"), "p": float("nan")}
    statistic, pvalue = stats.kstest(positive, "expon", args=(0, mean))
    return {"stat": float(statistic), "p": float(pvalue)}


def survival_result(
    durations: np.ndarray,
    times: np.ndarray,
    survival: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    at_risk: np.ndarray,
    *,
    mode: str,
    n_events: int,
) -> dict[str, Any]:
    """Empaqueta la curva en el contrato ``SurvivalResult`` de data-model §4.4."""
    curve = [
        {
            "time": float(time),
            "survival": float(value),
            "ci_lower": float(lower[i]) if np.isfinite(lower[i]) else None,
            "ci_upper": float(upper[i]) if np.isfinite(upper[i]) else None,
            "at_risk": int(at_risk[i]),
        }
        for i, (time, value) in enumerate(zip(times, survival, strict=False))
    ]
    median = None
    median_ci = None
    for i, value in enumerate(survival):
        if value <= 0.5:
            median = float(times[i])
            if np.isfinite(lower[i]) and np.isfinite(upper[i]):
                median_ci = (float(lower[i]), float(upper[i]))
            break
    return {
        "status": "ready" if curve else "insufficient",
        "mode": mode,
        "n_events": int(n_events),
        "n_censored": 1 if curve else 0,
        "median": median,
        "median_ci": median_ci,
        "curve": curve,
        "message": None if curve else "se necesitan al menos dos eventos",
    }
