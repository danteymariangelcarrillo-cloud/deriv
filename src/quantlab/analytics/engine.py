"""Fachada única del motor analítico (T017).

``AnalyticsEngine`` implementa el protocolo que usa ``SnapshotService``
(``sample_size`` + ``compute``) y delega cada análisis en su módulo puro. No
tiene estado propio (``gate_config`` es sólo lectura para el muestreo
walk-forward), no accede a disco ni a red, y es determinista: los mismos datos y
la misma configuración producen el mismo resultado (el bootstrap de zonas usa
una semilla fija).

Los métodos devuelven modelos del dominio (``EVTResult``, ``GARCHResult``,
``EventSeries``, tupla de ``ReactionZone``...) para que el orquestador no tenga
que validar diccionarios.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Los módulos de analítica importan ``scipy``/``statsmodels`` (~4 s en frío).
# Se cargan dentro de cada método para que el arranque del dashboard no los
# necesite: el primer cálculo paga el import una sola vez.
from quantlab.config import (
    TIMEFRAMES,
    AnalyzerConfig,
    GateConfig,
    ModelConfig,
    QuantLabConfig,
)
from quantlab.data.quality import GRANULARITY_SECONDS
from quantlab.domain.market_data import Instrument, get_instrument
from quantlab.domain.results import (
    ACFResult,
    DiagnosticsResult,
    EVTResult,
    GARCHResult,
    RegimeResult,
    SurvivalResult,
    WalkForwardResult,
)
from quantlab.domain.zones import ReactionZone

__all__ = ["AnalyticsEngine"]

SECONDS_PER_DAY = 86_400
_EVT_DIRECTIONAL = "directional"


class AnalyticsEngine:
    """Motor analítico stateless y determinista."""

    def __init__(self, gate_config: GateConfig | None = None) -> None:
        self.gate_config = gate_config or GateConfig()

    # ------------------------------- utilidades -------------------------------

    @staticmethod
    def _instrument(symbol: str) -> Instrument:
        try:
            return get_instrument(symbol)
        except KeyError:
            return Instrument(
                symbol=symbol,
                display_name=symbol,
                direction="up",
                history_days={tf: 30 for tf in TIMEFRAMES},
            )

    @staticmethod
    def _candles(candles: pd.DataFrame) -> pd.DataFrame:
        """Normaliza a las columnas capitalizadas que esperan los módulos."""
        if candles is None or candles.empty:
            return pd.DataFrame(columns=["datetime", "epoch", "Open", "High", "Low", "Close"])
        frame = candles.copy()
        frame = frame.rename(
            columns={
                column: column.capitalize()
                for column in frame.columns
                if str(column).lower() in {"open", "high", "low", "close"}
            }
        )
        if "datetime" not in frame.columns:
            frame["datetime"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
        for column in ("Open", "High", "Low", "Close", "epoch"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["Open", "High", "Low", "Close", "epoch"])
        frame["epoch"] = frame["epoch"].astype("int64")
        return frame.sort_values("epoch").reset_index(drop=True)

    @staticmethod
    def _ticks(ticks: pd.DataFrame) -> pd.DataFrame:
        if ticks is None or ticks.empty:
            return pd.DataFrame(columns=["datetime", "epoch", "price"])
        frame = ticks.copy()
        if "price" not in frame.columns and "quote" in frame.columns:
            frame["price"] = frame["quote"]
        frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
        frame["epoch"] = pd.to_numeric(frame["epoch"], errors="coerce")
        frame = frame.dropna(subset=["price", "epoch"])
        frame["epoch"] = frame["epoch"].astype("int64")
        frame = frame.sort_values("epoch").reset_index(drop=True)
        if "datetime" not in frame.columns:
            frame["datetime"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
        return frame[["datetime", "epoch", "price"]]

    @staticmethod
    def _returns(candles: pd.DataFrame) -> pd.Series:
        if candles.empty:
            return pd.Series(dtype=float)
        return candles["Close"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()

    @staticmethod
    def _tick_returns(ticks: pd.DataFrame) -> pd.Series:
        if ticks.empty:
            return pd.Series(dtype=float)
        return ticks["price"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()

    def _analyzer(self, config: QuantLabConfig) -> AnalyzerConfig:
        return config.analyzer

    # ------------------------------ sample_size ------------------------------

    def sample_size(
        self,
        analysis_id: str,
        *,
        candles: pd.DataFrame,
        ticks: pd.DataFrame,
    ) -> int:
        """Muestra disponible para el gate, sin computar el análisis completo."""
        import quantlab.analytics.events as events_module

        frame = self._candles(candles)
        tick_frame = self._ticks(ticks)
        if analysis_id in {"evt.gpd"}:
            returns = self._returns(frame)
            if returns.empty:
                return 0
            positive = returns[returns > 0]
            if positive.empty:
                return 0
            threshold = float(np.quantile(positive, 0.95))
            return int((positive > threshold).sum())
        if analysis_id in {
            "volatility.garch",
            "volatility.regimes",
            "autocorrelation",
            "diagnostics.residuals",
        }:
            return len(self._returns(frame))
        if analysis_id in {"events.candle", "heatmap.price"}:
            return len(frame)
        if analysis_id in {"events.tick", "heatmap.events"}:
            return len(tick_frame)
        if analysis_id == "events.conditional":
            if tick_frame.empty:
                return 0
            try:
                return len(events_module.detect_tick_extremes(tick_frame, AnalyzerConfig(), "up"))
            except Exception:  # noqa: BLE001 - sin muestra se informa 0
                return 0
        if analysis_id == "zones.reaction":
            # No se ejecuta el pipeline completo sólo para el gate: se estima un
            # toque evaluable cada ~20 barras (pivotes confirmados + retesteos).
            # ``compute`` devuelve zonas con ``evaluated_touches`` reales y
            # ``SnapshotService`` publica el estado que corresponda.
            return len(frame) // 20
        if analysis_id == "survival.kaplan_meier":
            if not tick_frame.empty:
                try:
                    return len(
                        events_module.detect_tick_extremes(tick_frame, AnalyzerConfig(), "up")
                    )
                except Exception:  # noqa: BLE001
                    return 0
            return max(0, len(frame) // 20)
        if analysis_id == "walkforward.validation":
            # Estimación conservadora de folds: ~5 000 barras por fold en las
            # temporalidades del dashboard. La cifra exacta se recalcula en
            # ``compute`` con la configuración efectiva; aquí sólo se decide si
            # merece la pena intentarlo.
            return len(frame) // 5000
        return 1

    # --------------------------------- compute --------------------------------

    def compute(
        self,
        analysis_id: str,
        *,
        symbol: str,
        timeframe: str,
        candles: pd.DataFrame,
        ticks: pd.DataFrame,
        config: QuantLabConfig,
    ) -> Any:
        """Calcula un análisis con gate ``ready`` y devuelve su modelo de dominio."""
        import quantlab.analytics.events as events_module
        import quantlab.analytics.evt as evt_module

        if timeframe not in TIMEFRAMES:
            raise ValueError(f"temporalidad no válida: {timeframe!r}")
        frame = self._candles(candles)
        tick_frame = self._ticks(ticks)
        instrument = self._instrument(symbol)
        analyzer = config.analyzer

        if analysis_id == "events.candle":
            return events_module.candle_events_series(
                frame, analyzer, instrument.direction, symbol=symbol, timeframe=timeframe
            )
        if analysis_id == "events.tick":
            return events_module.tick_events_series(
                tick_frame, analyzer, instrument.direction, symbol=symbol, timeframe=timeframe
            )
        if analysis_id == "events.conditional":
            tick_events = events_module.detect_tick_extremes(
                tick_frame, analyzer, instrument.direction
            )
            table = events_module.conditional_event_table(
                tick_frame,
                tick_events,
                horizon=100,
                bin_size=100,
                max_ticks=5000,
            )
            return events_module.conditional_result(table, horizon=100, bin_size=100)
        if analysis_id == "evt.gpd":
            returns = self._returns(frame)
            result = evt_module.fit_evt_gpd(
                returns.to_numpy(dtype=float),
                threshold_quantile=config.model.evt_threshold_quantile,
                direction=_EVT_DIRECTIONAL,
                min_excesses=config.gates.evt_min_excess,
            )
            return EVTResult.model_validate(result)
        if analysis_id == "volatility.garch":
            return self._garch_result(self._returns(frame), config)
        if analysis_id == "volatility.regimes":
            return self._regime_result(frame, config)
        if analysis_id == "zones.reaction":
            return self._zones(frame, symbol, timeframe, analyzer, instrument.direction)
        if analysis_id == "survival.kaplan_meier":
            return self._survival(frame, tick_frame, analyzer, instrument.direction)
        if analysis_id == "autocorrelation":
            from quantlab.analytics.diagnostics import autocorrelation_result

            result = autocorrelation_result(
                self._returns(frame), max_lag=min(50, config.model.rolling_window + 30)
            )
            if "error" in result:
                raise ValueError(str(result["error"]))
            return ACFResult.model_validate(result)
        if analysis_id == "diagnostics.residuals":
            from quantlab.analytics.diagnostics import residual_diagnostics

            result = residual_diagnostics(self._returns(frame), max_lag=20)
            if "error" in result:
                raise ValueError(str(result["error"]))
            return self._diagnostics_result(result)
        if analysis_id == "walkforward.validation":
            return self._walk_forward(frame, symbol, timeframe, config)
        if analysis_id in {"heatmap.price", "heatmap.events", "data.quality"}:
            return None
        raise KeyError(f"análisis desconocido: {analysis_id!r}")

    # ------------------------------ conversiones ------------------------------

    @staticmethod
    def _finite(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    def _garch_result(self, returns: pd.Series, config: QuantLabConfig) -> GARCHResult:
        from quantlab.analytics.volatility import fit_garch

        fitted = fit_garch(returns, p=config.model.garch_p, q=config.model.garch_q)
        if "error" in fitted:
            return GARCHResult(
                status="insufficient",
                n_obs=len(returns),
                message=str(fitted["error"]),
            )  # El contrato usa ``alpha1``/``beta1``; ``arch`` los nombra ``alpha[1]``.

        def _contract_name(key: str) -> str:
            return key.replace("[1]", "1").replace("[2]", "2").replace("[3]", "3")

        params = {
            _contract_name(key): self._finite(value) for key, value in fitted["params"].items()
        }
        p_values = {
            _contract_name(key): self._finite(value, 1.0)
            for key, value in fitted["p_values"].items()
        }
        alpha = next((value for key, value in params.items() if key.startswith("alpha")), 0.0)
        beta = next((value for key, value in params.items() if key.startswith("beta")), 0.0)
        return GARCHResult(
            status="ready",
            n_obs=int(fitted.get("n_obs", len(returns))),
            params=params,
            p_values=p_values,
            persistence=alpha + beta,
            aic=self._finite(fitted.get("aic")),
            bic=self._finite(fitted.get("bic")),
            log_likelihood=self._finite(fitted.get("log_likelihood")),
            forecast=tuple(fitted.get("forecast", ())[:10]),
            diagnostics={
                name: {
                    stat: self._finite(value, 1.0 if stat == "p" else 0.0)
                    for stat, value in block.items()
                }
                for name, block in fitted.get("diagnostics", {}).items()
            },
            converged=bool(fitted.get("converged", False)),
        )

    def _regime_result(self, frame: pd.DataFrame, config: QuantLabConfig) -> RegimeResult:
        returns = self._returns(frame)
        if frame.empty:
            return RegimeResult(
                status="insufficient",
                method="cusum",
                n_obs=0,
                message="sin velas",
            )
        from quantlab.analytics.volatility import detect_volatility_regimes

        epochs = frame["epoch"].iloc[1:].to_numpy(dtype=np.int64)
        result = detect_volatility_regimes(
            returns,
            n_regimes=2,
            method=config.model.regime_method,
            epochs=epochs,
            max_points=config.ram.downsample_max_points,
        )
        return RegimeResult.model_validate(
            {
                "status": result["status"],
                "method": result["method"],
                "n_regimes": 2,
                "n_obs": result["n_obs"],
                "high_vol_prob_current": result["high_vol_prob_current"],
                "series": result["series"],
                "segments": result["segments"],
                "last_switch_epoch": result["last_switch_epoch"],
                "converged": result["converged"],
                "message": result["message"],
            }
        )

    def _zones(
        self,
        frame: pd.DataFrame,
        symbol: str,
        timeframe: str,
        analyzer: AnalyzerConfig,
        direction: str,
    ) -> tuple[ReactionZone, ...]:
        import quantlab.analytics.events as events_module
        import quantlab.analytics.zones as zones_module

        if frame.empty:
            return ()
        candle_events = events_module.detect_candle_extremes(frame, analyzer, direction)
        event_prices = (
            candle_events["event_price"].to_numpy(dtype=float) if not candle_events.empty else None
        )
        table = zones_module.detect_reaction_zones(
            frame,
            analyzer,
            side="both",
            event_prices=event_prices,
        )
        return zones_module.to_reaction_zones(
            table, symbol=symbol, timeframe=timeframe, stage="in_sample"
        )

    def _survival(
        self,
        frame: pd.DataFrame,
        tick_frame: pd.DataFrame,
        analyzer: AnalyzerConfig,
        direction: str,
    ) -> SurvivalResult:
        import quantlab.analytics.events as events_module
        from quantlab.analytics.survival import survival_result

        tick_events = (
            events_module.detect_tick_extremes(tick_frame, analyzer, direction)
            if not tick_frame.empty
            else pd.DataFrame()
        )
        if not tick_events.empty and "tick_index" in tick_events.columns:
            durations, times, curve, lower, upper, at_risk, n_events = self._survival_from_indices(
                tick_events["tick_index"].to_numpy(dtype=int),
                len(tick_frame),
            )
            mode = "event_index"
        else:
            candle_events = events_module.detect_candle_extremes(frame, analyzer, direction)
            indices = (
                np.flatnonzero(
                    frame["epoch"].isin(candle_events.get("epoch", pd.Series(dtype=int)))
                )
                if not candle_events.empty
                else np.array([], dtype=int)
            )
            durations, times, curve, lower, upper, at_risk, n_events = self._survival_from_indices(
                indices, len(frame)
            )
            mode = "event_index"
        result = survival_result(
            durations,
            times,
            curve,
            lower,
            upper,
            at_risk,
            mode=mode,
            n_events=n_events,
        )
        return SurvivalResult.model_validate(result)

    @staticmethod
    def _survival_from_indices(
        indices: np.ndarray, n_observations: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
        from quantlab.analytics.survival import event_index_kaplan_meier

        (
            times,
            curve,
            lower,
            upper,
            at_risk,
            n_events,
        ) = event_index_kaplan_meier(indices, n_observations)
        sorted_indices = np.sort(np.unique(np.asarray(indices, dtype=int)))
        if len(sorted_indices) >= 2:
            durations = np.diff(sorted_indices).astype(float)
        else:
            durations = np.array([], dtype=float)
        return durations, times, curve, lower, upper, at_risk, n_events

    def _diagnostics_result(self, result: dict[str, Any]) -> DiagnosticsResult:
        for key in ("jarque_bera", "arch_lm", "adf"):
            if key not in result or "error" in result[key]:
                raise ValueError(f"diagnóstico {key} no disponible: {result.get(key)}")
        payload = {
            "jarque_bera": {
                "stat": self._finite(result["jarque_bera"]["stat"]),
                "p": self._finite(result["jarque_bera"]["p"], 1.0),
            },
            "arch_lm": {
                "stat": self._finite(result["arch_lm"]["stat"]),
                "p": self._finite(result["arch_lm"]["p"], 1.0),
                "lags": int(result["arch_lm"]["lags"]),
            },
            "adf": {
                "stat": self._finite(result["adf"]["stat"]),
                "p": self._finite(result["adf"]["p"], 1.0),
                "lags": int(result["adf"]["lags"]),
                "critical_values": {
                    key: self._finite(value)
                    for key, value in result["adf"]["critical_values"].items()
                },
            },
        }
        return DiagnosticsResult.model_validate(payload)

    def _walk_forward(
        self,
        frame: pd.DataFrame,
        symbol: str,
        timeframe: str,
        config: QuantLabConfig,
    ) -> WalkForwardResult:
        import quantlab.analytics.walkforward as wf_module

        model: ModelConfig = config.model
        train_days = model.wf_train_days[timeframe]
        test_days = model.wf_test_days[timeframe]
        step_days = model.wf_step_days[timeframe]
        try:
            table = wf_module.walk_forward_validation(
                frame,
                config.analyzer,
                timeframe,
                train_days,
                test_days,
                step_days,
            )
        except ValueError as exc:
            return WalkForwardResult(
                status="insufficient",
                config={
                    "train_bars": 0,
                    "test_bars": 0,
                    "step_bars": max(
                        1, int(step_days * SECONDS_PER_DAY / GRANULARITY_SECONDS[timeframe])
                    ),
                    "min_folds": config.gates.wf_min_folds,
                },
                aggregate={
                    "folds": 0,
                    "evaluated": 0,
                    "reactions": 0,
                    "rate": 0.0,
                    "ci": {"lower": 0.0, "upper": 1.0},
                    "sign_test_p": 1.0,
                    "independent": False,
                },
                warning=wf_module.WALK_FORWARD_WARNING,
                recommendation=str(exc),
            )
        aggregate = wf_module.aggregate_walk_forward_results(table)
        if "error" in aggregate:
            aggregate = {
                "n_folds": 0,
                "n_significant_fdr": 0,
                "weighted_mean_reaction_rate_pct": 0.0,
                "median_reaction_rate_pct": 0.0,
                "sign_test_p_value": 1.0,
                "folds_summary": [],
            }
        payload = wf_module.walk_forward_result(
            table,
            aggregate,
            timeframe=timeframe,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            gates=config.gates,
        )
        return WalkForwardResult.model_validate(payload)
