"""Detección de eventos candidatos por velas y ticks (T023).

Migración de ``detect_candle_extremes`` (legacy L626-L678),
``detect_tick_extremes`` (L680-L706) y ``conditional_event_table``
(L1230-L1259). Las tres mantienen el umbral rolling calculado **sólo con
observaciones anteriores** (``shift(1)``), de modo que no hay fuga temporal.

Las funciones de cálculo devuelven ``DataFrame`` para conservar la paridad con
el prototipo; ``candle_events_series``, ``tick_events_series`` y
``conditional_result`` convierten esa salida a los modelos del dominio que
consume el ``AnalyticsEngine`` (T017).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from quantlab.analytics.indicators import add_atr, directional_returns, rolling_quantile
from quantlab.analytics.stats import wilson_score_interval
from quantlab.config import AnalyzerConfig
from quantlab.domain.events import EventCandidate, EventSeries, ThresholdSummary
from quantlab.domain.results import ConditionalBin, ConditionalResult
from quantlab.domain.types import Direction

__all__ = [
    "candle_events_series",
    "conditional_event_table",
    "conditional_result",
    "detect_candle_extremes",
    "detect_tick_extremes",
    "tick_events_series",
]

CANDLE_UP = "UPWARD_EXTREME_CANDIDATE"
CANDLE_DOWN = "DOWNWARD_EXTREME_CANDIDATE"
TICK_UP = "UPWARD_TICK_EXTREME_CANDIDATE"
TICK_DOWN = "DOWNWARD_TICK_EXTREME_CANDIDATE"


def detect_candle_extremes(
    df: pd.DataFrame,
    config: AnalyzerConfig,
    direction: Direction,
    *,
    lookback: int | None = None,
    quantile: float | None = None,
    min_move_pct: float | None = None,
) -> pd.DataFrame:
    """Detecta excursiones extremas direccionales en velas.

    Para un instrumento ``up`` mide del cierre previo al máximo de la vela; para
    ``down``, del cierre previo al mínimo. El umbral se calcula con
    ``shift(1).rolling(lookback).quantile(quantile)`` (sin look-ahead) y la
    excursión debe superarlo y superar ``min_move_pct``.

    Es una migración línea a línea de ``detect_candle_extremes`` del prototipo.
    """
    lookback = lookback or config.event_lookback_bars
    quantile = config.candle_event_quantile if quantile is None else quantile
    min_move_pct = config.min_move_pct if min_move_pct is None else min_move_pct
    if lookback < 10 or not 0.5 < quantile < 1.0 or min_move_pct < 0:
        raise ValueError("Usa lookback >= 10, quantile entre 0.5 y 1.0 y min_move_pct >= 0.")

    frame = add_atr(df)
    previous_close = frame["Close"].shift(1)
    if direction == "up":
        move_points = (frame["High"] - previous_close).clip(lower=0)
        event_price = frame["High"]
        event_direction = CANDLE_UP
    else:
        move_points = (previous_close - frame["Low"]).clip(lower=0)
        event_price = frame["Low"]
        event_direction = CANDLE_DOWN
    move_pct = move_points / previous_close.replace(0, np.nan) * 100.0
    min_periods = min(lookback, max(30, lookback // 4))
    threshold_pct = move_pct.shift(1).rolling(lookback, min_periods=min_periods).quantile(quantile)
    threshold_pct = threshold_pct.replace([np.inf, -np.inf], np.nan)

    candidates = frame.copy()
    candidates["previous_close"] = previous_close
    candidates["directional_excursion_points"] = move_points
    candidates["directional_excursion_pct"] = move_pct
    candidates["threshold_pct_prior_only"] = threshold_pct
    candidates["event_price"] = event_price
    candidates["event_type"] = event_direction
    candidates["move_in_atr"] = move_points / frame["ATR"].shift(1).replace(0, np.nan)
    mask = (
        threshold_pct.notna()
        & move_pct.notna()
        & (move_pct > 0)
        & (move_pct >= threshold_pct)
        & (move_pct >= float(min_move_pct))
    )
    return candidates.loc[mask].copy().reset_index(drop=True)


def detect_tick_extremes(
    ticks_df: pd.DataFrame,
    config: AnalyzerConfig,
    direction: Direction,
    *,
    quantile: float | None = None,
    lookback: int | None = None,
    min_periods: int = 300,
) -> pd.DataFrame:
    """Candidatos direccionales en retornos entre ticks (sin datos futuros)."""
    quantile = config.tick_event_quantile if quantile is None else quantile
    lookback = config.tick_event_lookback if lookback is None else lookback
    if not 0.5 < quantile < 1 or lookback < min_periods or min_periods < 30:
        raise ValueError("Configura quantile (0.5, 1), lookback >= min_periods >= 30.")

    frame = ticks_df.copy().sort_values("epoch").reset_index(drop=True)
    if "datetime" not in frame.columns:
        frame["datetime"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
    returns = frame["price"].pct_change() * 100.0
    directional = directional_returns(returns, direction)
    threshold = rolling_quantile(directional, lookback, quantile, min_periods)
    mask = threshold.notna() & directional.gt(0) & directional.ge(threshold)
    events = frame.loc[mask, ["datetime", "epoch", "price"]].copy()
    events["tick_index"] = np.flatnonzero(mask.to_numpy())
    events["return_pct"] = returns.loc[mask].to_numpy()
    events["directional_move_pct"] = directional.loc[mask].to_numpy()
    events["threshold_pct_prior_only"] = threshold.loc[mask].to_numpy()
    events["event_type"] = TICK_UP if direction == "up" else TICK_DOWN
    return events.reset_index(drop=True)


def conditional_event_table(
    ticks_df: pd.DataFrame,
    events_df: pd.DataFrame,
    horizon: int = 100,
    bin_size: int = 100,
    max_ticks: int = 5000,
) -> pd.DataFrame:
    """Tasa histórica descriptiva de un evento en los siguientes H ticks.

    Migración literal de ``conditional_event_table``: la columna
    ``ticks_since_event_bin`` agrupa los ticks transcurridos desde el candidato
    anterior y ``empirical_probability_pct`` es la tasa *in-sample*.
    """
    if horizon < 1 or bin_size < 1:
        raise ValueError("horizon y bin_size deben ser >= 1")

    ticks = ticks_df.sort_values("epoch").reset_index(drop=True)
    event_flags = np.zeros(len(ticks), dtype=np.int8)
    if not events_df.empty:
        event_idx = events_df["tick_index"].astype(int).to_numpy()
        event_idx = event_idx[(event_idx >= 0) & (event_idx < len(ticks))]
        event_flags[event_idx] = 1

    last_event = -1
    since = np.full(len(ticks), -1, dtype=int)
    for i, flag in enumerate(event_flags):
        since[i] = i - last_event if last_event >= 0 else -1
        if flag:
            last_event = i

    future = (
        pd.Series(event_flags)
        .shift(-1)
        .iloc[::-1]
        .rolling(horizon, min_periods=horizon)
        .max()
        .iloc[::-1]
    )
    future_event = future.to_numpy()
    valid = (since >= 0) & (since <= max_ticks) & np.isfinite(future_event)
    tmp = pd.DataFrame(
        {
            "ticks_since_event": since[valid],
            "event_in_next_horizon": future_event[valid].astype(int),
        }
    )
    if tmp.empty:
        return pd.DataFrame(
            columns=[
                "ticks_since_event_bin",
                "observations",
                "future_events",
                "empirical_probability_pct",
            ]
        )

    tmp["ticks_since_event_bin"] = (tmp["ticks_since_event"] // bin_size) * bin_size
    result = (
        tmp.groupby("ticks_since_event_bin")["event_in_next_horizon"]
        .agg(observations="size", future_events="sum")
        .reset_index()
    )
    result["empirical_probability_pct"] = 100 * result["future_events"] / result["observations"]
    result["horizon_ticks"] = horizon
    result["interpretation"] = (
        "Descriptivo in-sample; no es una probabilidad validada fuera de muestra"
    )
    return result


# ------------------------- conversión a modelos del dominio -------------------------


def _candidate(
    *,
    symbol: str,
    timeframe: str,
    kind: str,
    direction: Direction,
    epoch: int,
    price: float,
    magnitude_points: float,
    magnitude_pct: float,
    threshold_points: float,
    lookback: int,
    warmup: int,
) -> EventCandidate:
    return EventCandidate(
        symbol=symbol,
        timeframe=timeframe,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        direction=direction,
        event_epoch=int(epoch),
        event_price=float(price),
        magnitude_points=float(max(0.0, magnitude_points)),
        magnitude_pct=float(max(0.0, magnitude_pct)),
        threshold_points=float(threshold_points),
        lookback=lookback,
        warmup_ok=True,
        # ``computed_at`` derivado del evento (no ``now``) para que el motor sea
        # determinista: mismos datos y configuración => mismo EventSeries.
        computed_at=datetime.fromtimestamp(int(epoch), tz=UTC),
    )


def _series(
    events: list[EventCandidate],
    *,
    quantile: float,
    lookback: int,
    warmup: int,
    note: str = "",
) -> EventSeries:
    thresholds = [event.threshold_points for event in events]
    summary = ThresholdSummary(
        min=float(min(thresholds)) if thresholds else None,
        median=float(np.median(thresholds)) if thresholds else None,
        max=float(max(thresholds)) if thresholds else None,
    )
    return EventSeries(
        status="ready" if events else "insufficient",
        n_events=len(events),
        threshold_quantile=quantile,
        lookback=lookback,
        warmup=warmup,
        events=tuple(events[:5000]),
        threshold_summary=summary,
        note=note or ("" if events else "sin eventos candidatos con el umbral actual"),
    )


def candle_events_series(
    df: pd.DataFrame,
    config: AnalyzerConfig,
    direction: Direction,
    *,
    symbol: str,
    timeframe: str,
) -> EventSeries:
    """Detecta eventos de velas y los devuelve como ``EventSeries``."""
    events = detect_candle_extremes(df, config, direction)
    lookback = config.event_lookback_bars
    warmup = min(lookback, max(30, lookback // 4))
    candidates = [
        _candidate(
            symbol=symbol,
            timeframe=timeframe,
            kind="candle",
            direction=direction,
            epoch=row.epoch,
            price=row.event_price,
            magnitude_points=row.directional_excursion_points,
            magnitude_pct=row.directional_excursion_pct,
            threshold_points=row.threshold_pct_prior_only,
            lookback=lookback,
            warmup=warmup,
        )
        for row in events.itertuples(index=False)
    ]
    return _series(
        candidates,
        quantile=config.candle_event_quantile,
        lookback=lookback,
        warmup=warmup,
    )


def tick_events_series(
    ticks_df: pd.DataFrame,
    config: AnalyzerConfig,
    direction: Direction,
    *,
    symbol: str,
    timeframe: str,
) -> EventSeries:
    """Detecta eventos de ticks y los devuelve como ``EventSeries``."""
    events = detect_tick_extremes(ticks_df, config, direction)
    lookback = config.tick_event_lookback
    candidates = [
        _candidate(
            symbol=symbol,
            timeframe=timeframe,
            kind="tick",
            direction=direction,
            epoch=row.epoch,
            price=row.price,
            magnitude_points=row.directional_move_pct,
            magnitude_pct=row.directional_move_pct,
            threshold_points=row.threshold_pct_prior_only,
            lookback=lookback,
            warmup=300,
        )
        for row in events.itertuples(index=False)
    ]
    return _series(
        candidates,
        quantile=config.tick_event_quantile,
        lookback=lookback,
        warmup=300,
    )


def conditional_result(
    table: pd.DataFrame,
    *,
    horizon: int,
    bin_size: int,
) -> ConditionalResult:
    """Convierte la tabla condicional en ``ConditionalResult``."""
    bins: list[ConditionalBin] = []
    for row in table.itertuples(index=False):
        observations = int(row.observations)
        future_events = int(row.future_events)
        probability = future_events / observations if observations else 0.0
        lower, upper = wilson_score_interval(future_events, observations)
        bins.append(
            ConditionalBin(
                bin_start=int(row.ticks_since_event_bin),
                bin_end=int(row.ticks_since_event_bin) + bin_size - 1,
                n_events=observations,
                n_trials=observations,
                probability=probability,
                ci_lower=lower if np.isfinite(lower) else 0.0,
                ci_upper=upper if np.isfinite(upper) else 0.0,
            )
        )
    return ConditionalResult(
        status="ready" if bins else "insufficient",
        mode="ticks",
        horizon=horizon,
        bin_size=bin_size,
        bins=tuple(bins),
        note=(
            "Descriptivo in-sample; no es una probabilidad validada fuera de muestra"
            if bins
            else "sin suficientes eventos/ticks para estimar tasas condicionadas"
        ),
    )
