"""Indicadores técnicos puros (T018).

Migración exacta de las utilidades del prototipo (``legacy/
boom_crash_quant_analyzer.py`` L617-L624) y de los cálculos de retornos que
usaban los análisis. Todas las funciones son puras, vectorizadas y no escriben en
disco ni mutan sus argumentos.

``add_atr`` reproduce línea a línea ``Analyzer.add_atr``: la primera barra queda
en ``NaN`` (no hay cierre previo) y el ATR sólo aparece cuando hay ``period``
valores de *true range*, porque ``min_periods=period``. Esa es la definición que
consumen las zonas de reacción (``zone_tolerance_atr``), así que aquí no se
"mejora" el cálculo: se replica.

El resto de funciones aplican la misma semántica que el prototipo:

- ``log_returns``: retornos logarítmicos (``np.log(precios).diff()``), la forma
  que consumen EVT/GARCH en la Fase 1.
- ``directional_returns``: recorta los retornos al sentido del instrumento
  (Boom mide subidas, Crash mide bajadas), como ``detect_tick_extremes``.
- ``rolling_quantile``: cuantil móvil **sin incluir la barra actual**
  (``shift(1)``), para que el umbral de evento no use datos futuros.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantlab.domain.types import Direction

__all__ = [
    "add_atr",
    "directional_returns",
    "log_returns",
    "rolling_quantile",
]


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Añade la columna ``ATR`` (Average True Range) sin mutar ``df``.

    Migración exacta del prototipo: ``true_range`` es el máximo entre el rango
    de la barra y las distancias al cierre previo; la media es simple (no
    Wilder) con ``min_periods=period``.

    Args:
        df: velas con columnas ``High``, ``Low`` y ``Close``.
        period: número de barras de la media móvil (``>= 1``).

    Returns:
        Copia de ``df`` con la columna ``ATR`` añadida.
    """
    out = df.copy()
    prev_close = out["Close"].shift(1)
    true_range = pd.concat(
        [
            out["High"] - out["Low"],
            (out["High"] - prev_close).abs(),
            (out["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["ATR"] = true_range.rolling(period, min_periods=period).mean()
    return out


def log_returns(prices: pd.Series | pd.Index | np.ndarray) -> pd.Series:
    """Retornos logarítmicos de una serie de precios.

    Equivale a ``np.log(prices).diff()``: el primer valor es ``NaN`` y los
    precios no positivos producen ``-inf``/``NaN`` (el consumidor decide si los
    descarta), igual que cualquier transformación logarítmica.
    """
    series = prices if isinstance(prices, pd.Series) else pd.Series(prices)
    return np.log(series).diff()


def directional_returns(returns: pd.Series, direction: Direction) -> pd.Series:
    """Retornos recortados al sentido del instrumento, sin mutar la entrada.

    ``up`` conserva los retornos positivos y anula los negativos; ``down`` hace
    lo simétrico sobre el retorno invertido (``-returns``). Es la misma
    operación de ``detect_tick_extremes`` en el prototipo.

    Raises:
        ValueError: si ``direction`` no es ``"up"`` ni ``"down"``.
    """
    if direction == "up":
        return returns.clip(lower=0)
    if direction == "down":
        return (-returns).clip(lower=0)
    raise ValueError(f"direction inválida: {direction!r}; use 'up' o 'down'")


def rolling_quantile(
    series: pd.Series,
    window: int,
    quantile: float,
    min_periods: int,
) -> pd.Series:
    """Cuantil móvil calculado sólo con las ``window`` observaciones previas.

    ``shift(1)`` garantiza que el umbral de la barra ``t`` no incluye la barra
    ``t`` (sin fuga temporal). ``min_periods`` fija cuántas observaciones hacen
    falta para publicar el primer cuantil.

    Raises:
        ValueError: si los parámetros están fuera de rango.
    """
    if window < 1:
        raise ValueError("window debe ser >= 1")
    if not 1 <= min_periods <= window:
        raise ValueError("min_periods debe estar entre 1 y window")
    if not 0.0 < quantile <= 1.0:
        raise ValueError("quantile debe estar en (0, 1]")

    return series.shift(1).rolling(window, min_periods=min_periods).quantile(quantile)
