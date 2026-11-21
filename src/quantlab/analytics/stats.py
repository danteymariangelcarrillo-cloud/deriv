"""Funciones estadísticas base (T021).

Migración del prototipo ``legacy/boom_crash_quant_analyzer.py`` L72-L116 y de
``bootstrap_ci`` (L171-L183). Las tres primeras son deterministas y se replican
línea a línea para conservar la paridad 1e-10; el bootstrap usa un
``np.random.Generator`` local en lugar del ``np.random.seed`` global del
prototipo, así que su paridad se valida estadísticamente y no valor a valor.

Todas las funciones son puras: no mutan sus argumentos ni el estado global de
NumPy, y no acceden a disco ni a red.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from scipy import stats

__all__ = [
    "benjamini_hochberg_fdr",
    "binomial_test_pvalue",
    "bootstrap_ci",
    "wilson_score_interval",
]

_ALTERNATIVES = {"greater": "greater", "less": "less", "two-sided": "two-sided"}


def wilson_score_interval(successes: int, trials: int, alpha: float = 0.05) -> tuple[float, float]:
    """Intervalo de confianza de Wilson para una proporción binomial.

    Reproduce ``wilson_score_interval`` del prototipo: centro y semi-ancho
    corregidos por ``z`` y truncados a ``[0, 1]``. Es el intervalo que usan las
    zonas (``reaction_rate``) y el análisis condicional.

    Args:
        successes: número de éxitos observados.
        trials: número de ensayos; si es ``<= 0`` devuelve ``(nan, nan)``.
        alpha: nivel de significación (``0.05`` → IC del 95 %).

    Returns:
        Tupla ``(límite_inferior, límite_superior)``.
    """
    if trials <= 0:
        return (np.nan, np.nan)
    z = stats.norm.ppf(1 - alpha / 2)
    p = successes / trials
    denom = 1 + z**2 / trials
    centre = (p + z**2 / (2 * trials)) / denom
    half = z * np.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def binomial_test_pvalue(
    successes: int, trials: int, p_null: float, alternative: str = "greater"
) -> float:
    """p-valor del test binomial exacto.

    Compatible con SciPy >= 1.11 (``binomtest``) y < 1.11 (``binom_test``), como
    el prototipo. Una ``alternative`` desconocida se interpreta como
    ``"greater"``, igual que la migración original.

    Args:
        successes: número de éxitos observados.
        trials: número de ensayos; si es ``<= 0`` devuelve ``nan``.
        p_null: probabilidad de éxito bajo la hipótesis nula.
        alternative: ``"greater"``, ``"less"`` o ``"two-sided"``.

    Returns:
        El p-valor como ``float``.
    """
    if trials <= 0:
        return np.nan
    alt = _ALTERNATIVES.get(alternative, "greater")
    try:
        return float(stats.binomtest(successes, trials, p_null, alternative=alt).pvalue)
    except AttributeError:
        return float(stats.binom_test(successes, trials, p_null, alternative=alt))


def benjamini_hochberg_fdr(
    pvalues: np.ndarray, alpha: float = 0.05
) -> tuple[np.ndarray, np.ndarray]:
    """Corrección Benjamini-Hochberg de múltiples comparaciones.

    Replica el procedimiento del prototipo: ordena los p-valores, rechaza hasta
    el mayor ``k`` que cumple ``p_(k) <= k * alpha / m`` y calcula los p-valores
    ajustados con el mínimo acumulado desde el final.

    Args:
        pvalues: p-valores sin corregir.
        alpha: tasa de falsos descubrimientos objetivo.

    Returns:
        ``(rejected, adjusted)``: máscara booleana de rechazos y p-valores
        ajustados, ambos en el orden de entrada. Con ``m == 0`` devuelve dos
        arrays vacíos (``bool`` y ``float``).
    """
    values = np.asarray(pvalues, dtype=float)
    m = len(values)
    if m == 0:
        return np.array([], dtype=bool), np.array([], dtype=float)

    sorted_idx = np.argsort(values)
    sorted_p = values[sorted_idx]
    critical = np.arange(1, m + 1) * alpha / m
    rejected_sorted = sorted_p <= critical
    if not rejected_sorted.any():
        rejected_sorted = np.zeros(m, dtype=bool)
    else:
        k = np.where(rejected_sorted)[0].max()
        rejected_sorted = np.zeros(m, dtype=bool)
        rejected_sorted[: k + 1] = True

    rejected = np.zeros(m, dtype=bool)
    rejected[sorted_idx] = rejected_sorted

    adjusted = np.minimum.accumulate(sorted_p[::-1] * m / np.arange(m, 0, -1))[::-1]
    adjusted = np.minimum(adjusted, 1.0)
    adjusted_pvalues = np.zeros(m)
    adjusted_pvalues[sorted_idx] = adjusted
    return rejected, adjusted_pvalues


def bootstrap_ci(
    data: np.ndarray,
    statistic_func: Callable[[np.ndarray], Any],
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> tuple[float, float]:
    """Intervalo de confianza percentil por bootstrap.

    A diferencia del prototipo (que sembraba el generador global con
    ``np.random.seed``), aquí se crea un ``np.random.Generator`` local: la
    función es reproducible con ``seed`` y no altera el estado aleatorio del
    proceso. Las réplicas cuyo estadístico no es finito se descartan; si quedan
    menos de 10, el resultado es ``(nan, nan)``.

    Args:
        data: muestra observada.
        statistic_func: estadístico a remuestrear (recibe un array).
        n_bootstrap: número de remuestras.
        alpha: nivel de significación (``0.05`` → IC del 95 %).
        seed: semilla del generador local; ``None`` usa entropía del sistema.

    Returns:
        Tupla ``(límite_inferior, límite_superior)``.
    """
    values = np.asarray(data)
    n = len(values)
    if n == 0:
        return (np.nan, np.nan)

    rng = np.random.default_rng(seed)
    if statistic_func is np.median:
        # Ruta vectorizada para el estadístico que usa el bootstrap de zonas
        # (500 réplicas por zona): misma semilla, matriz (n_bootstrap, n).
        indices = rng.integers(0, n, size=(n_bootstrap, n))
        finite = np.median(values[indices], axis=1).astype(float)
        finite = finite[np.isfinite(finite)]
        if len(finite) < 10:
            return (np.nan, np.nan)
        return (
            float(np.percentile(finite, 100 * alpha / 2)),
            float(np.percentile(finite, 100 * (1 - alpha / 2))),
        )

    stats_vals: list[float] = []
    for _ in range(n_bootstrap):
        sample = values[rng.choice(n, n, replace=True)]
        try:
            stats_vals.append(statistic_func(sample))
        except Exception:  # noqa: BLE001 - una réplica fallida no aborta el IC
            stats_vals.append(np.nan)

    finite = np.asarray(stats_vals, dtype=float)
    finite = finite[np.isfinite(finite)]
    if len(finite) < 10:
        return (np.nan, np.nan)
    return (
        float(np.percentile(finite, 100 * alpha / 2)),
        float(np.percentile(finite, 100 * (1 - alpha / 2))),
    )
