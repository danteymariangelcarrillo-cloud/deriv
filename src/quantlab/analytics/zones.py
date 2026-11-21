"""Zonas de reacción: pivotes, clustering y evaluación (T024).

Migración de ``_find_pivots`` (L709-L734), ``_cluster_levels`` (L737-L751),
``_evaluate_zone`` (L753-L868) y ``detect_reaction_zones`` (L870-L925).

Se mantiene el ``p_null = 0.5`` del prototipo por defecto (INV-044): la
corrección geométrica ``b/(a+b)`` es optativa. La corrección BH sólo se aplica
cuando el llamador lo pide explícitamente; el walk-forward (T028) la usa por
defecto, como exige la tarea.

El bootstrap usa una semilla fija (``BOOTSTRAP_SEED``) para que el motor sea
determinista (T017): mismos datos y configuración producen siempre el mismo IC.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quantlab.analytics.indicators import add_atr
from quantlab.analytics.stats import (
    benjamini_hochberg_fdr,
    binomial_test_pvalue,
    bootstrap_ci,
    wilson_score_interval,
)
from quantlab.config import AnalyzerConfig
from quantlab.domain.zones import DEFAULT_P_NULL, ReactionZone, ZoneSensitivity

__all__ = [
    "BOOTSTRAP_SEED",
    "cluster_levels",
    "detect_reaction_zones",
    "evaluate_zone",
    "find_pivots",
    "to_reaction_zones",
]

BOOTSTRAP_SEED = 42
BOOTSTRAP_REPLICATES = 500


def find_pivots(df: pd.DataFrame, side: str, window: int) -> pd.DataFrame:
    """Pivotes confirmados por ``window`` barras a cada lado.

    Un pivote de soporte es el mínimo estricto de su ventana y de resistencia el
    máximo; se confirma ``window`` barras después, de modo que una zona nunca se
    evalúa antes de que su pivote sea visible.
    """
    if side not in {"support", "resistance"}:
        raise ValueError("side debe ser support o resistance")
    if window < 1:
        raise ValueError("window debe ser >= 1")
    column = "Low" if side == "support" else "High"
    values = df[column].to_numpy(dtype=float)
    records: list[dict[str, object]] = []
    for i in range(window, len(df) - window):
        chunk = values[i - window : i + window + 1]
        centre = values[i]
        if side == "support":
            is_pivot = centre == np.nanmin(chunk) and int(np.nanargmin(chunk)) == window
        else:
            is_pivot = centre == np.nanmax(chunk) and int(np.nanargmax(chunk)) == window
        if not is_pivot or not np.isfinite(centre):
            continue
        records.append(
            {
                "pivot_idx": i,
                "confirmed_idx": i + window,
                "datetime": df.iloc[i]["datetime"],
                "confirmed_at": df.iloc[i + window]["datetime"],
                "price": float(centre),
                "atr": float(df.iloc[i].get("ATR", np.nan)),
            }
        )
    return pd.DataFrame(records)


def cluster_levels(pivots: pd.DataFrame, tolerance: float) -> list[list[dict[str, object]]]:
    """Agrupa pivotes consecutivos cuya distancia al centro del grupo cabe en la tolerancia."""
    if pivots.empty:
        return []
    records = pivots.sort_values("price").to_dict("records")
    clusters: list[list[dict[str, object]]] = []
    for record in records:
        if not clusters:
            clusters.append([record])
            continue
        current_centre = float(np.median([x["price"] for x in clusters[-1]]))
        if abs(float(record["price"]) - current_centre) <= tolerance:
            clusters[-1].append(record)
        else:
            clusters.append([record])
    return clusters


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def evaluate_zone(
    df: pd.DataFrame,
    centre: float,
    half_width: float,
    zone_type: str,
    available_from_idx: int,
    event_prices: np.ndarray | None,
    config: AnalyzerConfig,
    *,
    p_null: float = DEFAULT_P_NULL,
) -> dict[str, object]:
    """Evalúa los retesteos de una zona sobre la serie completa (in-sample).

    Los toques se agrupan en episodios separados por ``touch_separation_bars``.
    Cada episodio se resuelve como reacción si el objetivo se alcanza antes que
    el stop dentro de ``response_horizon_bars``; si ambos ocurren en la misma
    barra queda ambiguo, y si ninguno ocurre, pendiente/censurado.
    """
    n = len(df)
    indices = np.arange(n)
    touch_mask = (df["Low"].to_numpy() <= centre + half_width) & (
        df["High"].to_numpy() >= centre - half_width
    )
    touch_indices = indices[touch_mask & (indices >= available_from_idx)]
    episodes: list[int] = []
    last_idx: int | None = None
    for idx in touch_indices:
        if last_idx is None or idx - last_idx > config.touch_separation_bars:
            episodes.append(int(idx))
        last_idx = int(idx)

    atr_values = df["ATR"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    success = failure = ambiguous = pending = 0
    favourable_atr_values: list[float] = []
    adverse_atr_values: list[float] = []
    horizon = config.response_horizon_bars

    for idx in episodes:
        atr_ref = (
            atr_values[idx - 1] if idx > 0 and np.isfinite(atr_values[idx - 1]) else atr_values[idx]
        )
        if not np.isfinite(atr_ref) or atr_ref <= 0 or idx + 1 >= n:
            pending += 1
            continue
        end = min(n, idx + 1 + horizon)
        future_highs = highs[idx + 1 : end]
        future_lows = lows[idx + 1 : end]
        if len(future_highs) == 0:
            pending += 1
            continue
        if zone_type == "support":
            target = centre + config.favourable_atr * atr_ref
            stop = centre - config.adverse_atr * atr_ref
            target_hits = np.flatnonzero(future_highs >= target)
            stop_hits = np.flatnonzero(future_lows <= stop)
            favourable = (np.nanmax(future_highs) - centre) / atr_ref
            adverse = (centre - np.nanmin(future_lows)) / atr_ref
        else:
            target = centre - config.favourable_atr * atr_ref
            stop = centre + config.adverse_atr * atr_ref
            target_hits = np.flatnonzero(future_lows <= target)
            stop_hits = np.flatnonzero(future_highs >= stop)
            favourable = (centre - np.nanmin(future_lows)) / atr_ref
            adverse = (np.nanmax(future_highs) - centre) / atr_ref
        favourable_atr_values.append(float(max(0.0, favourable)))
        adverse_atr_values.append(float(max(0.0, adverse)))
        t_target = int(target_hits[0]) if len(target_hits) else None
        t_stop = int(stop_hits[0]) if len(stop_hits) else None
        if t_target is None and t_stop is None:
            pending += 1
        elif t_target is not None and t_stop is not None and t_target == t_stop:
            ambiguous += 1
        elif t_target is not None and (t_stop is None or t_target < t_stop):
            success += 1
        else:
            failure += 1

    evaluated = success + failure
    if event_prices is not None and len(event_prices):
        event_count = int(
            np.sum((event_prices >= centre - half_width) & (event_prices <= centre + half_width))
        )
    else:
        event_count = 0

    ci_lower, ci_upper = (
        wilson_score_interval(success, evaluated) if evaluated > 0 else (np.nan, np.nan)
    )
    p_value = (
        binomial_test_pvalue(success, evaluated, p_null, alternative="greater")
        if evaluated > 0
        else np.nan
    )

    fav_ci = (np.nan, np.nan)
    adv_ci = (np.nan, np.nan)
    if len(favourable_atr_values) >= 5:
        fav_ci = bootstrap_ci(
            np.array(favourable_atr_values),
            np.median,
            n_bootstrap=BOOTSTRAP_REPLICATES,
            seed=BOOTSTRAP_SEED,
        )
    if len(adverse_atr_values) >= 5:
        adv_ci = bootstrap_ci(
            np.array(adverse_atr_values),
            np.median,
            n_bootstrap=BOOTSTRAP_REPLICATES,
            seed=BOOTSTRAP_SEED,
        )

    last_touch_epoch = int(df.iloc[int(touch_indices[-1])]["epoch"]) if len(touch_indices) else None
    available_from_epoch = (
        int(df.iloc[available_from_idx]["epoch"]) if 0 <= available_from_idx < n else 0
    )

    return {
        "zone_type": zone_type,
        "price": centre,
        "lower": centre - half_width,
        "upper": centre + half_width,
        "tolerance_points": half_width,
        "touches": len(episodes),
        "evaluated_touches": evaluated,
        "reactions": success,
        "failures": failure,
        "ambiguous_touches": ambiguous,
        "pending_or_uncensored": pending,
        "reaction_rate_pct": (100.0 * success / evaluated) if evaluated else np.nan,
        "reaction_rate_ci_lower_pct": 100.0 * ci_lower if np.isfinite(ci_lower) else np.nan,
        "reaction_rate_ci_upper_pct": 100.0 * ci_upper if np.isfinite(ci_upper) else np.nan,
        "reaction_p_value_vs_random": p_value,
        "median_favourable_atr": (
            float(np.median(favourable_atr_values)) if favourable_atr_values else np.nan
        ),
        "median_favourable_atr_ci_lower": fav_ci[0],
        "median_favourable_atr_ci_upper": fav_ci[1],
        "median_adverse_atr": (
            float(np.median(adverse_atr_values)) if adverse_atr_values else np.nan
        ),
        "median_adverse_atr_ci_lower": adv_ci[0],
        "median_adverse_atr_ci_upper": adv_ci[1],
        "event_count_in_zone": event_count,
        "last_touch_epoch": last_touch_epoch,
        "available_from_idx": available_from_idx,
        "available_from_epoch": available_from_epoch,
        "p_null": p_null,
    }


def detect_reaction_zones(
    df: pd.DataFrame,
    config: AnalyzerConfig,
    side: str = "both",
    event_prices: pd.Series | np.ndarray | None = None,
    *,
    min_touches: int | None = None,
    pivot_window: int | None = None,
    zone_tolerance_points: float | None = None,
    p_null: float = DEFAULT_P_NULL,
    fdr_alpha: float | None = None,
) -> pd.DataFrame:
    """Busca niveles de reacción por pivotes y mide retesteos posteriores.

    ``fdr_alpha`` es ``None`` por defecto (in-sample descriptivo). El
    walk-forward pasa ``0.05`` para corregir los p-valores de todos los folds
    con Benjamini-Hochberg.
    """
    if side not in {"both", "support", "resistance"}:
        raise ValueError("side debe ser both, support o resistance")
    min_touches = config.min_zone_touches if min_touches is None else min_touches
    pivot_window = config.pivot_window if pivot_window is None else pivot_window
    if min_touches < 1:
        raise ValueError("min_touches debe ser >= 1.")

    frame = add_atr(df).reset_index(drop=True)
    valid_atr = frame["ATR"].dropna()
    if valid_atr.empty:
        raise ValueError("No hay suficientes velas para calcular ATR y zonas.")
    tolerance = (
        float(zone_tolerance_points)
        if zone_tolerance_points is not None
        else float(valid_atr.median() * config.zone_tolerance_atr)
    )
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("La tolerancia de zona debe ser un número positivo.")

    prices = np.asarray(event_prices, dtype=float) if event_prices is not None else None
    output: list[dict[str, object]] = []
    sides = [side] if side != "both" else ["support", "resistance"]
    for one_side in sides:
        pivots = find_pivots(frame, one_side, pivot_window)
        for cluster in cluster_levels(pivots, tolerance):
            if len(cluster) < min_touches:
                continue
            centre = float(np.median([float(p["price"]) for p in cluster]))
            available_from = max(int(p["confirmed_idx"]) for p in cluster)
            stats = evaluate_zone(
                frame,
                centre,
                tolerance,
                one_side,
                available_from,
                prices,
                config,
                p_null=p_null,
            )
            stats.update(
                {
                    "pivot_count": len(cluster),
                    "first_pivot_utc": min(p["datetime"] for p in cluster),
                    "last_pivot_confirmed_utc": max(p["confirmed_at"] for p in cluster),
                    "source": "historical_pivots_full_sample",
                }
            )
            output.append(stats)

    zones = pd.DataFrame(output)
    if zones.empty:
        return zones
    zones = zones.sort_values(
        ["reaction_rate_pct", "evaluated_touches", "touches"],
        ascending=[False, False, False],
        na_position="last",
    ).reset_index(drop=True)

    if fdr_alpha is not None:
        pvalues = zones["reaction_p_value_vs_random"].to_numpy(dtype=float)
        finite = np.isfinite(pvalues)
        adjusted = np.full(len(zones), np.nan)
        if finite.any():
            _, adjusted_values = benjamini_hochberg_fdr(pvalues[finite], alpha=fdr_alpha)
            adjusted[finite] = adjusted_values
        zones["p_value_adj"] = adjusted
    return zones


def _sensitivity(success: int, evaluated: int, ambiguous: int, pending: int) -> ZoneSensitivity:
    ambiguous_base = evaluated + ambiguous
    pending_base = evaluated + ambiguous + pending
    return ZoneSensitivity(
        rate_ambiguous_as_failure_pct=(100.0 * success / ambiguous_base if ambiguous_base else 0.0),
        rate_pending_as_failure_pct=(100.0 * success / pending_base if pending_base else 0.0),
    )


def _ci_pair(row: pd.Series, prefix: str) -> tuple[float, float] | None:
    lower = _finite_or_none(row.get(f"{prefix}_ci_lower"))
    upper = _finite_or_none(row.get(f"{prefix}_ci_upper"))
    if lower is None or upper is None:
        return None
    return (min(lower, upper), max(lower, upper))


def to_reaction_zones(
    zones: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    stage: str = "in_sample",
) -> tuple[ReactionZone, ...]:
    """Convierte las filas de ``detect_reaction_zones`` en ``ReactionZone``."""
    converted: list[ReactionZone] = []
    for _, row in zones.iterrows():
        evaluated = int(row["evaluated_touches"])
        reactions = int(row["reactions"])
        failures = int(row["failures"])
        ambiguous = int(row["ambiguous_touches"])
        pending = int(row["pending_or_uncensored"])
        rate = None
        if evaluated > 0 and math.isfinite(float(row["reaction_rate_pct"])):
            rate = float(row["reaction_rate_pct"]) / 100.0
        ci_lower = _finite_or_none(row.get("reaction_rate_ci_lower_pct"))
        ci_upper = _finite_or_none(row.get("reaction_rate_ci_upper_pct"))
        p_raw = _finite_or_none(row.get("reaction_p_value_vs_random"))
        p_adj = _finite_or_none(row.get("p_value_adj"))
        available_from_epoch = int(row.get("available_from_epoch", 0) or 0)
        converted.append(
            ReactionZone(
                symbol=symbol,
                timeframe=timeframe,
                zone_type=str(row["zone_type"]),  # type: ignore[arg-type]
                center=float(row["price"]),
                lower=float(row["lower"]),
                upper=float(row["upper"]),
                half_width=float(row["tolerance_points"]),
                touch_bars=int(row["touches"]),
                episodes=int(row["touches"]),
                evaluated_touches=evaluated,
                reactions=reactions,
                failures=failures,
                ambiguous_touches=ambiguous,
                pending_touches=pending,
                reaction_rate=rate,
                ci_lower=None if ci_lower is None else ci_lower / 100.0,
                ci_upper=None if ci_upper is None else ci_upper / 100.0,
                p_null=float(row.get("p_null", DEFAULT_P_NULL)),
                p_value_raw=p_raw,
                p_value_adj=p_adj,
                reject=bool(p_adj is not None and p_adj < 0.05),
                median_favourable_atr=_finite_or_none(row.get("median_favourable_atr")),
                favourable_ci=_ci_pair(row, "median_favourable_atr"),
                median_adverse_atr=_finite_or_none(row.get("median_adverse_atr")),
                adverse_ci=_ci_pair(row, "median_adverse_atr"),
                event_count_in_zone=int(row.get("event_count_in_zone", 0) or 0),
                last_touch_epoch=(
                    int(row["last_touch_epoch"])
                    if row.get("last_touch_epoch") is not None
                    and not pd.isna(row.get("last_touch_epoch"))
                    else None
                ),
                available_from_epoch=available_from_epoch,
                stage=stage,  # type: ignore[arg-type]
                status="ready" if evaluated > 0 else "insufficient",
                sensitivity=_sensitivity(reactions, evaluated, ambiguous, pending),
                reason=(
                    None
                    if evaluated > 0
                    else "sin toques evaluables dentro del horizonte de respuesta"
                ),
            )
        )
    return tuple(converted)
