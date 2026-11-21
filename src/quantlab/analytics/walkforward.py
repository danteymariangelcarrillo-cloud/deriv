"""Validación walk-forward anclada (T028).

Migración de ``walk_forward_validation`` (legacy L944-L1039) y
``aggregate_walk_forward_results`` (L1041-L1063).

Se conserva la advertencia obligatoria de T028: los folds **no** son
independientes (zonas repetidas y solapamiento temporal), por lo que el sign
test es descriptivo y ``independent`` siempre es ``False``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from quantlab.analytics.indicators import add_atr
from quantlab.analytics.stats import (
    benjamini_hochberg_fdr,
    binomial_test_pvalue,
    wilson_score_interval,
)
from quantlab.analytics.zones import cluster_levels, find_pivots
from quantlab.config import TIMEFRAMES, AnalyzerConfig, GateConfig
from quantlab.data.quality import GRANULARITY_SECONDS

__all__ = [
    "WALK_FORWARD_WARNING",
    "aggregate_walk_forward_results",
    "walk_forward_result",
    "walk_forward_validation",
]

WALK_FORWARD_WARNING = (
    "Folds no independientes (zonas repetidas, solapamiento temporal). Sign test es descriptivo."
)
MIN_EVALUATED_PER_FOLD = 3


def walk_forward_validation(
    df: pd.DataFrame,
    config: AnalyzerConfig,
    timeframe: str,
    train_days: int,
    test_days: int,
    step_days: int,
    min_train_bars: int = 200,
) -> pd.DataFrame:
    """Repite la detección de zonas sobre ventanas temporales ancladas.

    El prototipo usa ``test_start = origin`` (la última barra de train también
    abre test); se replica para no alterar la paridad. Sólo se publican folds con
    al menos ``MIN_EVALUATED_PER_FOLD`` toques evaluados.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"temporalidad no válida: {timeframe!r}")
    if train_days < 1 or test_days < 1 or step_days < 1:
        raise ValueError("train_days, test_days y step_days deben ser >= 1")

    frame = add_atr(df).reset_index(drop=True)
    if len(frame) < min_train_bars:
        raise ValueError(f"Insuficientes datos para walk-forward (min {min_train_bars} barras).")
    seconds_per_bar = GRANULARITY_SECONDS[timeframe]
    bars_per_day = 86400 / seconds_per_bar
    train_bars = int(train_days * bars_per_day)
    test_bars = int(test_days * bars_per_day)
    step_bars = int(step_days * bars_per_day)
    train_bars = max(train_bars, min_train_bars)

    n = len(frame)
    results: list[dict[str, Any]] = []
    for origin in range(train_bars, n - test_bars, step_bars):
        train_end = origin
        test_start = origin
        test_end = min(n, origin + test_bars)
        if test_end - test_start < 10:
            break
        train_df = frame.iloc[:train_end].copy()
        test_df = frame.iloc[test_start:test_end].copy()
        train_atr = train_df["ATR"].dropna()
        if train_atr.empty:
            continue
        tolerance = float(train_atr.median() * config.zone_tolerance_atr)
        for side in ["support", "resistance"]:
            pivots = find_pivots(train_df, side, config.pivot_window)
            for cluster in cluster_levels(pivots, tolerance):
                if len(cluster) < config.min_zone_touches:
                    continue
                centre = float(np.median([float(p["price"]) for p in cluster]))
                test_indices = np.arange(len(test_df))
                test_highs = test_df["High"].to_numpy(dtype=float)
                test_lows = test_df["Low"].to_numpy(dtype=float)
                test_atr = test_df["ATR"].to_numpy(dtype=float)
                touch_mask = (test_lows <= centre + tolerance) & (test_highs >= centre - tolerance)
                touch_indices = test_indices[touch_mask]
                episodes: list[int] = []
                last_idx: int | None = None
                for idx in touch_indices:
                    if last_idx is None or idx - last_idx > config.touch_separation_bars:
                        episodes.append(int(idx))
                    last_idx = int(idx)
                horizon = config.response_horizon_bars
                success = failure = ambiguous = pending = 0
                favourable_atr_vals: list[float] = []
                adverse_atr_vals: list[float] = []
                for idx in episodes:
                    atr_ref = (
                        test_atr[idx - 1]
                        if idx > 0 and np.isfinite(test_atr[idx - 1])
                        else test_atr[idx]
                    )
                    if not np.isfinite(atr_ref) or atr_ref <= 0 or idx + 1 >= len(test_df):
                        pending += 1
                        continue
                    end = min(len(test_df), idx + 1 + horizon)
                    future_highs = test_highs[idx + 1 : end]
                    future_lows = test_lows[idx + 1 : end]
                    if len(future_highs) == 0:
                        pending += 1
                        continue
                    if side == "support":
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
                    favourable_atr_vals.append(float(max(0.0, favourable)))
                    adverse_atr_vals.append(float(max(0.0, adverse)))
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
                if evaluated < MIN_EVALUATED_PER_FOLD:
                    continue
                ci_lower, ci_upper = wilson_score_interval(success, evaluated)
                p_val = binomial_test_pvalue(success, evaluated, 0.5, alternative="greater")
                results.append(
                    {
                        "fold": len(results) + 1,
                        "train_start_epoch": int(train_df["epoch"].iloc[0]),
                        "train_end_epoch": int(train_df["epoch"].iloc[-1]),
                        "test_start_epoch": int(test_df["epoch"].iloc[0]),
                        "test_end_epoch": int(test_df["epoch"].iloc[-1]),
                        "bars_train": len(train_df),
                        "bars_test": len(test_df),
                        "zone_type": side,
                        "zone_price": centre,
                        "zone_tolerance": tolerance,
                        "train_touches": len(episodes),
                        "oos_evaluated": evaluated,
                        "oos_reactions": success,
                        "oos_failures": failure,
                        "oos_reaction_rate_pct": 100.0 * success / evaluated,
                        "oos_reaction_ci_lower_pct": (
                            100.0 * ci_lower if np.isfinite(ci_lower) else np.nan
                        ),
                        "oos_reaction_ci_upper_pct": (
                            100.0 * ci_upper if np.isfinite(ci_upper) else np.nan
                        ),
                        "oos_p_value_vs_random": p_val,
                        "oos_median_favourable_atr": (
                            float(np.median(favourable_atr_vals)) if favourable_atr_vals else np.nan
                        ),
                        "oos_median_adverse_atr": (
                            float(np.median(adverse_atr_vals)) if adverse_atr_vals else np.nan
                        ),
                    }
                )
    return pd.DataFrame(results)


def aggregate_walk_forward_results(wf_df: pd.DataFrame) -> dict[str, Any]:
    """Agrega folds con BH y sign test descriptivo (migración literal)."""
    if wf_df.empty:
        return {"error": "No walk-forward results to aggregate"}
    valid = wf_df.dropna(subset=["oos_reaction_rate_pct", "oos_p_value_vs_random"])
    if valid.empty:
        return {"error": "No valid folds with evaluated zones"}
    pvals = valid["oos_p_value_vs_random"].to_numpy()
    rejected, adj_pvals = benjamini_hochberg_fdr(pvals, alpha=0.05)
    valid = valid.copy()
    valid["oos_p_value_adj"] = adj_pvals
    valid["oos_significant_fdr"] = rejected
    weights = valid["oos_evaluated"]
    weighted_rate = float(np.average(valid["oos_reaction_rate_pct"], weights=weights))
    n_folds = len(valid)
    n_positive = int((valid["oos_reaction_rate_pct"] > 50).sum())
    p_sign = binomial_test_pvalue(n_positive, n_folds, 0.5, alternative="greater")
    return {
        "n_folds": int(n_folds),
        "n_significant_fdr": int(rejected.sum()),
        "weighted_mean_reaction_rate_pct": weighted_rate,
        "median_reaction_rate_pct": float(valid["oos_reaction_rate_pct"].median()),
        "sign_test_p_value": float(p_sign) if np.isfinite(p_sign) else 1.0,
        "folds_summary": valid[
            [
                "fold",
                "zone_type",
                "zone_price",
                "oos_evaluated",
                "oos_reaction_rate_pct",
                "oos_p_value_vs_random",
                "oos_p_value_adj",
                "oos_significant_fdr",
            ]
        ].to_dict("records"),
    }


def _finite(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    number = _finite(value, default)
    return min(max(number, low), high)


def _ci_rate(success: int, evaluated: int) -> dict[str, float]:
    lower, upper = wilson_score_interval(success, evaluated)
    return {
        "lower": float(lower) if np.isfinite(lower) else 0.0,
        "upper": float(upper) if np.isfinite(upper) else 1.0,
    }


def walk_forward_result(
    wf_df: pd.DataFrame,
    aggregate: dict[str, Any],
    *,
    timeframe: str,
    train_days: int,
    test_days: int,
    step_days: int,
    gates: GateConfig,
    recommendation: str | None = None,
) -> dict[str, Any]:
    """Empaqueta el walk-forward en el contrato ``WalkForwardResult`` (T028)."""
    seconds_per_bar = GRANULARITY_SECONDS[timeframe]
    folds: list[dict[str, Any]] = []
    for row in wf_df.itertuples(index=False):
        rate = _finite(row.oos_reaction_rate_pct) / 100.0
        ci_lower = _finite(row.oos_reaction_ci_lower_pct) / 100.0
        ci_upper = _finite(row.oos_reaction_ci_upper_pct) / 100.0
        p_raw = _clamp(row.oos_p_value_vs_random, 0.0, 1.0, 1.0)
        p_adj = _finite(getattr(row, "oos_p_value_adj", np.nan))
        folds.append(
            {
                "fold_id": (
                    f"{timeframe}-{int(row.fold)}-{row.zone_type}-{float(row.zone_price):.6f}"
                ),
                "train_start_epoch": int(row.train_start_epoch),
                "train_end_epoch": int(row.train_end_epoch),
                "test_start_epoch": int(row.test_start_epoch),
                "test_end_epoch": int(row.test_end_epoch),
                "bars_train": int(row.bars_train),
                "bars_test": int(row.bars_test),
                "zones_evaluated": int(row.oos_evaluated),
                "reactions": int(row.oos_reactions),
                "evaluated_touches": int(row.oos_evaluated),
                "reaction_rate": _clamp(rate, 0.0, 1.0, 0.0),
                "ci_lower": _clamp(ci_lower, 0.0, 1.0, 0.0),
                "ci_upper": _clamp(ci_upper, 0.0, 1.0, 1.0),
                "p_value_raw": p_raw,
                "p_value_adj": None if not math.isfinite(p_adj) else _clamp(p_adj, 0.0, 1.0, 1.0),
                "stage": "oos",
            }
        )

    evaluated = len(wf_df)
    reactions = int(wf_df["oos_reactions"].sum()) if evaluated else 0
    aggregate_model = {
        "folds": evaluated,
        "evaluated": int(wf_df["oos_evaluated"].sum()) if evaluated else 0,
        "reactions": reactions,
        "rate": (
            min(max(float(aggregate["weighted_mean_reaction_rate_pct"]) / 100.0, 0.0), 1.0)
            if "weighted_mean_reaction_rate_pct" in aggregate
            else 0.0
        ),
        "ci": _ci_rate(reactions, int(wf_df["oos_evaluated"].sum()))
        if evaluated
        else {
            "lower": 0.0,
            "upper": 1.0,
        },
        "sign_test_p": min(max(float(aggregate.get("sign_test_p_value", 1.0)), 0.0), 1.0),
        "independent": False,
    }
    return {
        "status": "ready" if folds else "insufficient",
        "method": "anchored",
        "config": {
            "train_bars": int(train_days * 86400 / seconds_per_bar),
            "test_bars": int(test_days * 86400 / seconds_per_bar),
            "step_bars": max(1, int(step_days * 86400 / seconds_per_bar)),
            "min_folds": gates.wf_min_folds,
        },
        "folds": folds,
        "aggregate": aggregate_model,
        "warning": WALK_FORWARD_WARNING,
        "recommendation": recommendation
        or (
            None
            if folds
            else "Amplía el histórico o reduce las ventanas train/test para obtener folds."
        ),
    }
