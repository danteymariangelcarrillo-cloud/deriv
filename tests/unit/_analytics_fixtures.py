"""Datos sintéticos deterministas compartidos por los tests de analítica (T030)."""

from __future__ import annotations

import numpy as np
import pandas as pd

EPOCH0 = 1_700_000_000


def make_candles(
    n: int = 1500,
    *,
    seed: int = 42,
    step_seconds: int = 300,
    start_price: float = 1000.0,
    volatility: float = 0.004,
) -> pd.DataFrame:
    """Velas OHLC capitalizadas con ``datetime`` UTC y ``epoch`` únicos."""
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0, volatility, n)
    close = start_price * np.exp(np.cumsum(log_returns))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.normal(0.0, volatility / 3.0, n))
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    epoch = EPOCH0 + np.arange(n, dtype=np.int64) * step_seconds
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(epoch, unit="s", utc=True),
            "epoch": epoch,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
        }
    )


def make_ticks(
    n: int = 5000,
    *,
    seed: int = 7,
    volatility: float = 0.0004,
    start_price: float = 1000.0,
) -> pd.DataFrame:
    """Ticks con epoch consecutivos (1 s) y precio log-normal."""
    rng = np.random.default_rng(seed)
    price = start_price * np.exp(np.cumsum(rng.normal(0.0, volatility, n)))
    epoch = EPOCH0 + np.arange(n, dtype=np.int64)
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(epoch, unit="s", utc=True),
            "epoch": epoch,
            "price": price,
        }
    )


def make_returns(n: int = 2000, *, seed: int = 11, volatility: float = 1.0) -> np.ndarray:
    """Retornos porcentuales independientes (para ACF/diagnósticos)."""
    return np.random.default_rng(seed).normal(0.0, volatility, n)


def make_garch_returns(n: int = 4000, *, seed: int = 5) -> np.ndarray:
    """Retornos porcentuales de un GARCH(1,1) con omega=1e-7, alpha=.08, beta=.91.

    La varianza se genera en escala decimal y los retornos se expresan en
    porcentaje (x100), que es la unidad que consume ``fit_garch``.
    """
    rng = np.random.default_rng(seed)
    omega, alpha, beta = 1e-7, 0.08, 0.91
    variance = np.empty(n)
    variance[0] = omega / (1.0 - alpha - beta)
    shocks = rng.normal(0.0, 1.0, n)
    for i in range(1, n):
        variance[i] = (
            omega + alpha * (shocks[i - 1] ** 2) * variance[i - 1] + beta * variance[i - 1]
        )
    return shocks * np.sqrt(variance) * 100.0


def make_regime_returns(n: int = 3000, *, seed: int = 13) -> np.ndarray:
    """Serie con dos regímenes de volatilidad (var 4e-4 / 1.2e-3)."""
    rng = np.random.default_rng(seed)
    split = n // 2
    low = rng.normal(0.0, np.sqrt(4e-4), split)
    high = rng.normal(0.0, np.sqrt(1.2e-3), n - split)
    return np.concatenate([low, high]) * 100.0
