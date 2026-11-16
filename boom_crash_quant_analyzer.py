"""Analizador cuantitativo exploratorio para índices Crash/Boom de Deriv.

Dependencias: websocket-client, pandas, numpy, plotly, scipy, statsmodels, arch.
Datos de mercado públicos; no ejecuta operaciones.

IMPORTANTE:
- Los eventos detectados son candidatos estadísticos definidos por reglas, no etiquetas oficiales de Deriv.
- Las zonas de precio son niveles técnicos derivados del histórico; no representan órdenes ni liquidez real.
- Las estadísticas de zonas calculadas sobre toda la muestra son descriptivas. Para validar una estrategia se
  debe hacer un replay temporal/ walk-forward para evitar sesgo retrospectivo.
"""
from __future__ import annotations

# Version marker for notebook validation
MODULE_VERSION = "2.2.0"

import json
import logging
import os
import queue
import threading
import time
import warnings
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable, Iterable, Optional, Tuple, List, Dict

import numpy as np
import pandas as pd
import websocket

# Optional heavy dependencies for advanced statistics
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    go = None
    make_subplots = None

try:
    from scipy import stats
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    stats = None

try:
    import statsmodels.api as sm
    from statsmodels.tsa.stattools import acf, pacf, adfuller
    from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
    sm = None

try:
    from arch import arch_model
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False

LOG = logging.getLogger("boom_crash_quant")
MODULE_VERSION = "2.2.0"  # Updated version with statistical enhancements

# ──────────────────────────────────────────────────────────────────────────────
# Statistical Helper Functions (Pure Python / NumPy / SciPy)
# ──────────────────────────────────────────────────────────────────────────────

def wilson_score_interval(successes: int, trials: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Wilson score interval for binomial proportion."""
    if trials <= 0: return (np.nan, np.nan)
    if not SCIPY_AVAILABLE:
        p = successes / trials; z = 1.96; se = np.sqrt(p * (1 - p) / trials)
        return (max(0.0, p - z * se), min(1.0, p + z * se))
    z = stats.norm.ppf(1 - alpha / 2)
    p = successes / trials
    denom = 1 + z**2 / trials
    centre = (p + z**2 / (2 * trials)) / denom
    half = z * np.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def binomial_test_pvalue(successes: int, trials: int, p_null: float, alternative: str = 'greater') -> float:
    """Exact binomial test p-value. Compatible with SciPy <1.11 (binom_test) and >=1.11 (binomtest)."""
    if trials <= 0: return np.nan
    if not SCIPY_AVAILABLE: return np.nan
    # Map alternative strings to new API
    alt_map = {'greater': 'greater', 'less': 'less', 'two-sided': 'two-sided'}
    alt = alt_map.get(alternative, 'greater')
    try:
        # New API (SciPy >= 1.11): binomtest returns object with .pvalue attribute
        return stats.binomtest(successes, trials, p_null, alternative=alt).pvalue
    except AttributeError:
        # Fallback for old API (SciPy < 1.11): binom_test function
        return stats.binom_test(successes, trials, p_null, alternative=alt)


def benjamini_hochberg_fdr(pvalues: np.ndarray, alpha: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg FDR correction."""
    pvalues = np.asarray(pvalues); m = len(pvalues)
    if m == 0: return np.array([], dtype=bool), np.array([], dtype=float)
    sorted_idx = np.argsort(pvalues); sorted_p = pvalues[sorted_idx]
    critical = np.arange(1, m + 1) * alpha / m
    rejected_sorted = sorted_p <= critical
    if not rejected_sorted.any(): rejected = np.zeros(m, dtype=bool)
    else:
        k = np.where(rejected_sorted)[0].max()
        rejected_sorted = np.zeros(m, dtype=bool); rejected_sorted[:k+1] = True
    rejected = np.zeros(m, dtype=bool); rejected[sorted_idx] = rejected_sorted
    adjusted = np.minimum.accumulate(sorted_p[::-1] * m / np.arange(m, 0, -1))[::-1]
    adjusted = np.minimum(adjusted, 1.0)
    adjusted_pvalues = np.zeros(m); adjusted_pvalues[sorted_idx] = adjusted
    return rejected, adjusted_pvalues


def fit_gpd_mle(excesses: np.ndarray) -> Tuple[float, float, Dict]:
    """Fit GPD to excesses over threshold via MLE. Returns (xi, sigma, diagnostics)."""
    if not SCIPY_AVAILABLE or len(excesses) < 10:
        return np.nan, np.nan, {"error": "SciPy not available or insufficient data"}
    excesses = np.asarray(excesses); excesses = excesses[excesses > 0]
    if len(excesses) < 10: return np.nan, np.nan, {"error": "Insufficient positive excesses"}
    def neg_log_lik(params):
        xi, sigma = params
        if sigma <= 0: return 1e10
        if xi == 0: return np.sum(np.log(sigma) + excesses / sigma)
        if np.any(1 + xi * excesses / sigma <= 0): return 1e10
        return np.sum(np.log(sigma) + (1 + 1/xi) * np.log(1 + xi * excesses / sigma))
    mean_exc = np.mean(excesses); var_exc = np.var(excesses, ddof=1)
    if var_exc > 0 and mean_exc > 0:
        xi_init = 0.5 * (1 - mean_exc**2 / var_exc); sigma_init = mean_exc * (1 - xi_init)
    else: xi_init, sigma_init = 0.1, mean_exc
    bounds = [(-0.99, 0.99), (1e-8, None)]
    try:
        result = minimize(neg_log_lik, [xi_init, sigma_init], bounds=bounds, method='L-BFGS-B')
        xi_hat, sigma_hat = result.x
        eps = 1e-6; hess = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                params = np.array([xi_hat, sigma_hat])
                params_p = params.copy(); params_p[i] += eps
                params_m = params.copy(); params_m[i] -= eps
                f_p = neg_log_lik(params_p); f_m = neg_log_lik(params_m)
                grad_p = (f_p - f_m) / (2 * eps)
                params_p2 = params.copy(); params_p2[j] += eps
                params_m2 = params.copy(); params_m2[j] -= eps
                f_p2 = neg_log_lik(params_p2); f_m2 = neg_log_lik(params_m2)
                grad_m = (f_p2 - f_m2) / (2 * eps)
                hess[i, j] = (grad_p - grad_m) / (2 * eps)
        try:
            cov = np.linalg.inv(hess); se_xi, se_sigma = np.sqrt(np.diag(cov))
        except np.linalg.LinAlgError: se_xi, se_sigma = np.nan, np.nan
        return float(xi_hat), float(sigma_hat), {
            "n_excesses": len(excesses), "xi": float(xi_hat), "sigma": float(sigma_hat),
            "xi_se": float(se_xi) if np.isfinite(se_xi) else np.nan,
            "sigma_se": float(se_sigma) if np.isfinite(se_sigma) else np.nan,
            "log_likelihood": -float(result.fun), "converged": result.success,
        }
    except Exception as e: return np.nan, np.nan, {"error": str(e)}


def gpd_return_level(xi: float, sigma: float, threshold: float, 
                     n_obs: int, n_excesses: int, return_period: int) -> float:
    """Compute return level for given return period (in observations)."""
    if n_excesses <= 0 or n_obs <= 0: return np.nan
    p = n_excesses / n_obs
    if xi == 0: return threshold + sigma * np.log(return_period * p)
    else: return threshold + sigma / xi * ((return_period * p) ** xi - 1)


def bootstrap_ci(data: np.ndarray, statistic_func: Callable, n_bootstrap: int = 1000, 
                 alpha: float = 0.05, random_state: Optional[int] = None) -> Tuple[float, float]:
    """Percentile bootstrap confidence interval for any statistic."""
    if len(data) == 0: return np.nan, np.nan
    if random_state is not None: np.random.seed(random_state)
    stats_vals = []; n = len(data)
    for _ in range(n_bootstrap):
        sample = data[np.random.choice(n, n, replace=True)]
        try: stats_vals.append(statistic_func(sample))
        except Exception: stats_vals.append(np.nan)
    stats_vals = np.array(stats_vals); stats_vals = stats_vals[np.isfinite(stats_vals)]
    if len(stats_vals) < 10: return np.nan, np.nan
    return float(np.percentile(stats_vals, 100 * alpha / 2)), float(np.percentile(stats_vals, 100 * (1 - alpha / 2)))


# Endpoint público recomendado en la guía principal actual de Deriv (llms.txt).
# Primary endpoint documented in developers.deriv.com/llms.txt for public market data.
# The legacy market-data WebSocket is retained as a compatibility fallback because
# Deriv's public docs currently show both endpoint families in different workflows.
DEFAULT_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
LEGACY_MARKET_WS_URL = "wss://ws.binaryws.com/websockets/v3"
TIMEFRAMES: dict[int, str] = {
    60: "1min", 300: "5min", 900: "15min", 3600: "1h", 14400: "4h", 86400: "1d"
}


class DerivAPIError(RuntimeError):
    """Error devuelto por la API pública de Deriv."""


class DerivMarketDataClient:
    """Cliente de solo lectura para datos públicos de mercado de Deriv.

    Usa primero el endpoint público actual de llms.txt y, si la conexión falla
    en la capa de transporte, prueba el endpoint de market-data mostrado por la
    guía oficial de workflows. Ninguno requiere token para estos datos públicos.

    Nota: un HTTP 520 devuelto por Cloudflare puede indicar un fallo externo de
    gateway/origen; el código lo diagnostica, pero no puede forzar al servidor a
    aceptar la conexión.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        timeout: float = 20.0,
        fallback_urls: Optional[Iterable[str]] = None,
        retries_per_endpoint: int = 1,
    ):
        # Leer variables de entorno en runtime, no como argumento por defecto:
        # evita quedar atado al valor que existía al importar el módulo.
        self.url = url or os.getenv("DERIV_MARKET_WS_URL", DEFAULT_WS_URL)
        defaults = [DEFAULT_WS_URL, LEGACY_MARKET_WS_URL]
        self.fallback_urls = list(fallback_urls) if fallback_urls is not None else defaults
        self.fallback_urls = [u for u in self.fallback_urls if u and u != self.url]
        self.timeout = float(timeout)
        self.retries_per_endpoint = max(0, int(retries_per_endpoint))
        self._request_ids = count(1)
        self.active_endpoint: Optional[str] = None
        self.last_connection_error: Optional[str] = None

    @staticmethod
    def _is_transport_failure(exc: Exception) -> bool:
        detail = str(exc).lower()
        markers = (
            "handshake status 520", "handshake status 502", "handshake status 503",
            "handshake status 504", "connection reset", "connection aborted",
            "timed out", "temporary failure in name resolution", "name or service not known",
            "remote end closed connection", "websocket is already closed", "connection refused",
        )
        return isinstance(exc, (TimeoutError, OSError, websocket.WebSocketException)) or any(
            marker in detail for marker in markers
        )

    def _request_once(
        self,
        url: str,
        payload: dict[str, Any],
        expected_types: Iterable[str],
        expected_key: str,
    ) -> dict[str, Any]:
        req_id = next(self._request_ids)
        request = dict(payload)
        request["req_id"] = req_id
        deadline = time.monotonic() + self.timeout
        ws = None
        try:
            # No enviamos un Origin HTTP artificial generado por websocket-client.
            # Para un cliente Python de servidor a servidor no es necesario.
            ws = websocket.create_connection(
                url,
                timeout=self.timeout,
                enable_multithread=True,
                suppress_origin=True,
            )
            ws.send(json.dumps(request))
            expected = set(expected_types)
            while time.monotonic() < deadline:
                ws.settimeout(max(0.5, deadline - time.monotonic()))
                raw = ws.recv()
                if not raw:
                    continue
                try:
                    response = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise DerivAPIError(f"La API devolvió JSON inválido: {exc}") from exc
                if not isinstance(response, dict):
                    continue
                if response.get("error"):
                    error = response["error"]
                    code = error.get("code", "Error") if isinstance(error, dict) else "Error"
                    message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                    raise DerivAPIError(f"{code}: {message}")
                response_req_id = response.get("req_id")
                if response_req_id is not None and str(response_req_id) != str(req_id):
                    continue
                if response.get("msg_type") in expected or expected_key in response:
                    self.active_endpoint = url
                    self.last_connection_error = None
                    return response
            raise TimeoutError(f"Tiempo de espera agotado para solicitud {payload!r}")
        except (websocket.WebSocketException, OSError) as exc:
            raise DerivAPIError(f"Falló el transporte WebSocket en {url}: {exc}") from exc
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def request(
        self,
        payload: dict[str, Any],
        expected_types: Iterable[str],
        expected_key: str,
    ) -> dict[str, Any]:
        """Solicita datos; reintenta y cambia de endpoint solo en errores de transporte."""
        endpoints = [self.url] + [u for u in self.fallback_urls if u != self.url]
        failures: list[str] = []
        for endpoint in endpoints:
            for attempt in range(self.retries_per_endpoint + 1):
                try:
                    return self._request_once(endpoint, payload, expected_types, expected_key)
                except Exception as exc:
                    # Los errores JSON de negocio se informan inmediatamente; cambiar
                    # de endpoint no corrige un símbolo inexistente o una petición inválida.
                    if not self._is_transport_failure(exc):
                        raise
                    detail = str(exc)
                    failures.append(f"{endpoint} (intento {attempt + 1}): {detail}")
                    self.last_connection_error = detail
                    if attempt < self.retries_per_endpoint:
                        time.sleep(0.75 * (2 ** attempt))
                        continue
                    break

        all_520 = failures and all("520" in f for f in failures)
        if all_520:
            reason = (
                "Deriv/Cloudflare devolvió HTTP 520 durante el handshake en todos los endpoints probados. "
                "El handshake ocurre antes de enviar el JSON; no es el App ID, el símbolo ni los indicadores. "
                "El cliente ya probó el endpoint público actual y el endpoint de market-data de la guía oficial. "
                "Este resultado puede deberse al gateway/origen o a la ruta de red de Colab y no puede corregirse "
                "cambiando parámetros de análisis. Prueba la misma celda desde una red/entorno diferente y "
                "conserva el cf-ray para soporte si persiste."
            )
        else:
            reason = "No se pudo completar una solicitud pública de mercado con los endpoints probados."
        raise DerivAPIError(reason + "\nDetalles:\n- " + "\n- ".join(failures[-6:]))

    def get_active_symbols(self) -> list[dict[str, Any]]:
        # Esquema actual: product_type ya no forma parte del payload nuevo.
        response = self.request(
            {"active_symbols": "brief"},
            expected_types=("active_symbols",),
            expected_key="active_symbols",
        )
        symbols = response.get("active_symbols", [])
        if not isinstance(symbols, list):
            raise DerivAPIError("La respuesta de active_symbols no contiene una lista válida.")
        return symbols

    @staticmethod
    def _normalise_name(value: Any) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

    def resolve_symbol(self, target_name: str, override: Optional[str] = None) -> tuple[str, str]:
        """Resuelve el símbolo por nombre; permite override explícito, pero no lo inventa."""
        symbols = self.get_active_symbols()
        if override:
            known_codes = {
                str(item.get("underlying_symbol") or item.get("symbol") or "")
                for item in symbols
            }
            if override not in known_codes:
                raise DerivAPIError(
                    f"El símbolo configurado {override!r} no aparece activo en la respuesta de Deriv."
                )
            name = next(
                (str(item.get("underlying_symbol_name") or item.get("display_name") or override)
                 for item in symbols
                 if str(item.get("underlying_symbol") or item.get("symbol") or "") == override),
                override,
            )
            return override, name

        target = self._normalise_name(target_name)
        candidates: list[tuple[int, str, str]] = []
        for item in symbols:
            code = str(item.get("underlying_symbol") or item.get("symbol") or "")
            name = str(item.get("underlying_symbol_name") or item.get("display_name") or "")
            norm_name = self._normalise_name(name)
            if not code or not norm_name:
                continue
            if norm_name == target:
                candidates.append((0, code, name))
            elif norm_name.startswith(target):
                candidates.append((1, code, name))
            elif target in norm_name:
                candidates.append((2, code, name))
        if not candidates:
            related = []
            token = "crash" if "crash" in target else "boom" if "boom" in target else target
            for item in symbols:
                name = str(item.get("underlying_symbol_name") or item.get("display_name") or "")
                if token in self._normalise_name(name):
                    related.append(name)
            raise DerivAPIError(
                f"No se encontró {target_name!r} entre los símbolos activos. "
                f"Nombres relacionados devueltos por Deriv: {related[:30]}"
            )
        candidates.sort(key=lambda x: (x[0], len(x[2])))
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0] and candidates[0][2] != candidates[1][2]:
            exact = [c for c in candidates if c[0] == 0]
            if not exact:
                LOG.warning("Hay varios símbolos que coinciden con %s: %s. Se selecciona %s; revisa el nombre.",
                            target_name, [c[2] for c in candidates], candidates[0][2])
        return candidates[0][1], candidates[0][2]

    def get_candles(self, symbol: str, start: int, end: int, granularity: int, count_limit: int = 1000) -> list[dict[str, Any]]:
        response = self.request(
            {
                "ticks_history": symbol,
                "start": int(start),
                "end": int(end),
                "count": int(count_limit),
                "style": "candles",
                "granularity": int(granularity),
            },
            expected_types=("candles", "history"),
            expected_key="candles",
        )
        candles = response.get("candles", [])
        if not isinstance(candles, list):
            raise DerivAPIError("La respuesta histórica no contiene una lista de velas.")
        return candles

    def get_tick_history(self, symbol: str, count_limit: int = 5000, start: Optional[int] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ticks_history": symbol,
            "end": "latest",
            "count": int(count_limit),
            "style": "ticks",
        }
        if start is not None:
            payload["start"] = int(start)
        response = self.request(
            payload,
            expected_types=("history",),
            expected_key="history",
        )
        history = response.get("history", {})
        if not isinstance(history, dict):
            raise DerivAPIError("La respuesta de historial de ticks no es válida.")
        return history


@dataclass(frozen=True)
class AnalyzerConfig:
    """Parámetros analíticos. Ajustarlos requiere nueva validación fuera de muestra."""
    days: int = 20
    candle_event_quantile: float = 0.99
    event_lookback_bars: int = 200
    min_move_pct: float = 0.0
    tick_event_quantile: float = 0.999
    tick_event_lookback: int = 2000
    zone_tolerance_atr: float = 0.5
    pivot_window: int = 3
    min_zone_touches: int = 3
    touch_separation_bars: int = 3
    response_horizon_bars: int = 10
    favourable_atr: float = 1.0
    adverse_atr: float = 0.75


class SyntheticIndexAnalyzer:
    """Analizador de un instrumento. Instanciar uno por índice para no mezclar estados."""

    def __init__(
        self,
        instrument_name: str = "Boom 1000",
        direction: str = "up",
        days: int = 20,
        symbol_override: Optional[str] = None,
        client: Optional[DerivMarketDataClient] = None,
        config: Optional[AnalyzerConfig] = None,
    ):
        if direction not in {"up", "down"}:
            raise ValueError("direction debe ser 'up' o 'down'.")
        if days < 1:
            raise ValueError("days debe ser >= 1.")
        self.instrument_name = instrument_name
        self.direction = direction
        self.days = days
        self.symbol_override = symbol_override
        self.client = client or DerivMarketDataClient()
        self.config = config or AnalyzerConfig(days=days)
        self.symbol: Optional[str] = None
        self.resolved_name: Optional[str] = None
        self.data: dict[str, pd.DataFrame] = {}
        self.results: dict[str, dict[str, pd.DataFrame]] = {}
        self.errors: list[str] = []
        self.live_tick_count = 0
        self.ticks_since_event = 0
        self.live_events: list[dict[str, Any]] = []
        self.live_ticks_records: list[dict[str, Any]] = []
        self.live_dropped_ticks = 0
        self.ws_live: Optional[websocket.WebSocketApp] = None
        self._live_running = False
        self._live_stop_event = threading.Event()
        self._live_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100_000)
        self._consumer_thread: Optional[threading.Thread] = None
        self._last_live_price: Optional[float] = None
        self._last_live_epoch: Optional[int] = None
        self._directional_returns: deque[float] = deque(maxlen=10_000)

    def _ensure_symbol(self) -> str:
        if self.symbol is None:
            self.symbol, self.resolved_name = self.client.resolve_symbol(self.instrument_name, self.symbol_override)
            LOG.info("Instrumento resuelto por Deriv: %s (%s)", self.resolved_name, self.symbol)
        return self.symbol

    @staticmethod
    def _clean_candles(raw: list[dict[str, Any]], start_epoch: int, end_epoch: int) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame(columns=["datetime", "epoch", "Open", "High", "Low", "Close"])
        df = pd.DataFrame(raw)
        required = {"epoch", "open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"La respuesta de velas no contiene campos obligatorios: {sorted(missing)}")
        for col in ["epoch", "open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["epoch", "open", "high", "low", "close"])
        df["epoch"] = df["epoch"].astype("int64")
        df = df[(df["epoch"] >= start_epoch) & (df["epoch"] <= end_epoch)]
        df = df.drop_duplicates(subset=["epoch"], keep="last").sort_values("epoch")
        valid = (
            (df["high"] >= df["low"])
            & (df["high"] >= df[["open", "close"]].max(axis=1))
            & (df["low"] <= df[["open", "close"]].min(axis=1))
        )
        df = df.loc[valid].copy()
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        return df[["datetime", "epoch", "Open", "High", "Low", "Close"]].reset_index(drop=True)

    def fetch_historical_data(self, max_pages_per_timeframe: int = 100, batch_size: int = 1000) -> dict[str, pd.DataFrame]:
        """Descarga velas con paginación, control de progreso, deduplicación y timestamps UTC."""
        symbol = self._ensure_symbol()
        end_epoch = int(datetime.now(timezone.utc).timestamp())
        start_epoch = end_epoch - self.days * 86400
        for granularity, label in TIMEFRAMES.items():
            raw_all: list[dict[str, Any]] = []
            page_end = end_epoch
            previous_oldest: Optional[int] = None
            try:
                for page_no in range(1, max_pages_per_timeframe + 1):
                    raw = self.client.get_candles(
                        symbol=symbol, start=start_epoch, end=page_end,
                        granularity=granularity, count_limit=batch_size,
                    )
                    if not raw:
                        break
                    epochs: list[int] = []
                    for candle in raw:
                        try:
                            epochs.append(int(candle["epoch"]))
                        except (KeyError, TypeError, ValueError):
                            continue
                    if not epochs:
                        self.errors.append(f"{label}: lote sin marcas epoch válidas.")
                        break
                    raw_all.extend(raw)
                    oldest = min(epochs)
                    if oldest <= start_epoch:
                        break
                    if previous_oldest is not None and oldest >= previous_oldest:
                        self.errors.append(f"{label}: paginación detenida por falta de progreso temporal.")
                        break
                    previous_oldest = oldest
                    page_end = oldest - 1
                    if len(raw) < batch_size:
                        break
                else:
                    self.errors.append(f"{label}: alcanzó max_pages_per_timeframe={max_pages_per_timeframe}.")

                df = self._clean_candles(raw_all, start_epoch, end_epoch)
                if not df.empty:
                    self.data[label] = df
                    LOG.info("%s: %s velas desde %s hasta %s UTC", label, len(df), df.datetime.iloc[0], df.datetime.iloc[-1])
                else:
                    self.errors.append(f"{label}: no se recibieron velas válidas.")
                    LOG.warning("%s: no se recibieron velas válidas.", label)
            except Exception as exc:
                message = f"Error histórico en {label}: {exc}"
                self.errors.append(message)
                LOG.exception(message)
            time.sleep(0.25)  # Evita ráfagas innecesarias de solicitudes.
        return self.data

    def fetch_tick_history(self, count_limit: int = 5000, start_epoch: Optional[int] = None) -> pd.DataFrame:
        """Obtiene hasta count_limit ticks. Sin start_epoch, Deriv aplica su intervalo predeterminado."""
        if count_limit < 2:
            raise ValueError("count_limit debe ser >= 2.")
        symbol = self._ensure_symbol()
        history = self.client.get_tick_history(symbol, count_limit=count_limit, start=start_epoch)
        prices = history.get("prices")
        times = history.get("times")
        if not isinstance(prices, list) or not isinstance(times, list) or len(prices) != len(times):
            raise DerivAPIError("El historial de ticks no trae arrays prices/times del mismo tamaño.")
        df = pd.DataFrame({"epoch": pd.to_numeric(pd.Series(times), errors="coerce"),
                           "price": pd.to_numeric(pd.Series(prices), errors="coerce")})
        df = df.dropna().copy()
        df["epoch"] = df["epoch"].astype("int64")
        # No deduplicar por epoch/precio: la marca temporal puede tener precisión de segundos y dos ticks válidos
        # pueden compartir timestamp y quote. Mantener el orden de origen para esos empates.
        df = df.sort_values(["epoch"], kind="stable").reset_index(drop=True)
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
        self.data["ticks"] = df[["datetime", "epoch", "price"]]
        LOG.info("Ticks históricos: %s observaciones", len(df))
        return self.data["ticks"]

    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        out = df.copy()
        prev_close = out["Close"].shift(1)
        true_range = pd.concat(
            [out["High"] - out["Low"], (out["High"] - prev_close).abs(), (out["Low"] - prev_close).abs()], axis=1
        ).max(axis=1)
        out["ATR"] = true_range.rolling(period, min_periods=period).mean()
        return out

    def detect_candle_extremes(
        self,
        timeframe: str = "5min",
        lookback: Optional[int] = None,
        quantile: Optional[float] = None,
        min_move_pct: Optional[float] = None,
    ) -> pd.DataFrame:
        """Detecta excursiones extremas direccionales con umbral calculado solo con barras anteriores.

        Para Boom mide desde el cierre previo hasta el máximo de la vela; para Crash, desde el cierre previo
        hasta el mínimo. Una vela OHLC no demuestra el recorrido tick a tick, por eso los resultados se llaman
        eventos extremos candidatos, no spikes oficiales.
        """
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}; ejecuta fetch_historical_data() primero.")
        lookback = lookback or self.config.event_lookback_bars
        quantile = self.config.candle_event_quantile if quantile is None else quantile
        min_move_pct = self.config.min_move_pct if min_move_pct is None else min_move_pct
        if lookback < 10 or not 0.5 < quantile < 1.0 or min_move_pct < 0:
            raise ValueError("Usa lookback >= 10, quantile entre 0.5 y 1.0 y min_move_pct >= 0.")

        df = self.add_atr(self.data[timeframe])
        previous_close = df["Close"].shift(1)
        if self.direction == "up":
            move_points = (df["High"] - previous_close).clip(lower=0)
            event_price = df["High"]
            event_direction = "UPWARD_EXTREME_CANDIDATE"
        else:
            move_points = (previous_close - df["Low"]).clip(lower=0)
            event_price = df["Low"]
            event_direction = "DOWNWARD_EXTREME_CANDIDATE"
        move_pct = move_points / previous_close.replace(0, np.nan) * 100.0
        min_periods = min(lookback, max(30, lookback // 4))
        threshold_pct = move_pct.shift(1).rolling(lookback, min_periods=min_periods).quantile(quantile)
        threshold_pct = threshold_pct.replace([np.inf, -np.inf], np.nan)
        candidates = df.copy()
        candidates["previous_close"] = previous_close
        candidates["directional_excursion_points"] = move_points
        candidates["directional_excursion_pct"] = move_pct
        candidates["threshold_pct_prior_only"] = threshold_pct
        candidates["event_price"] = event_price
        candidates["event_type"] = event_direction
        candidates["move_in_atr"] = move_points / df["ATR"].shift(1).replace(0, np.nan)
        mask = (
            threshold_pct.notna()
            & move_pct.notna()
            & (move_pct > 0)
            & (move_pct >= threshold_pct)
            & (move_pct >= float(min_move_pct))
        )
        events = candidates.loc[mask].copy().reset_index(drop=True)
        self.data[f"events_{timeframe}"] = events
        return events

    def detect_tick_extremes(
        self,
        quantile: Optional[float] = None,
        lookback: Optional[int] = None,
        min_periods: int = 300,
    ) -> pd.DataFrame:
        """Candidatos direccionales en retornos entre ticks, con umbral móvil sin datos futuros."""
        if "ticks" not in self.data or self.data["ticks"].empty:
            raise KeyError("Primero ejecuta fetch_tick_history().")
        quantile = self.config.tick_event_quantile if quantile is None else quantile
        lookback = self.config.tick_event_lookback if lookback is None else lookback
        if not 0.5 < quantile < 1 or lookback < min_periods or min_periods < 30:
            raise ValueError("Configura quantile (0.5, 1), lookback >= min_periods >= 30.")
        df = self.data["ticks"].copy().sort_values("epoch").reset_index(drop=True)
        returns = df["price"].pct_change() * 100.0
        directional = returns.clip(lower=0) if self.direction == "up" else (-returns).clip(lower=0)
        threshold = directional.shift(1).rolling(lookback, min_periods=min_periods).quantile(quantile)
        mask = threshold.notna() & directional.gt(0) & directional.ge(threshold)
        events = df.loc[mask, ["datetime", "epoch", "price"]].copy()
        events["tick_index"] = np.flatnonzero(mask.to_numpy())
        events["return_pct"] = returns.loc[mask].to_numpy()
        events["directional_move_pct"] = directional.loc[mask].to_numpy()
        events["threshold_pct_prior_only"] = threshold.loc[mask].to_numpy()
        events["event_type"] = "UPWARD_TICK_EXTREME_CANDIDATE" if self.direction == "up" else "DOWNWARD_TICK_EXTREME_CANDIDATE"
        events = events.reset_index(drop=True)
        self.data["tick_events"] = events
        return events

    @staticmethod
    def _find_pivots(df: pd.DataFrame, side: str, window: int) -> pd.DataFrame:
        if side not in {"support", "resistance"}:
            raise ValueError("side debe ser support o resistance")
        if window < 1:
            raise ValueError("window debe ser >= 1")
        col = "Low" if side == "support" else "High"
        values = df[col].to_numpy(dtype=float)
        records: list[dict[str, Any]] = []
        for i in range(window, len(df) - window):
            chunk = values[i - window:i + window + 1]
            centre = values[i]
            if side == "support":
                is_pivot = centre == np.nanmin(chunk) and int(np.nanargmin(chunk)) == window
            else:
                is_pivot = centre == np.nanmax(chunk) and int(np.nanargmax(chunk)) == window
            if not is_pivot or not np.isfinite(centre):
                continue
            records.append({
                "pivot_idx": i,
                "confirmed_idx": i + window,
                "datetime": df.iloc[i]["datetime"],
                "confirmed_at": df.iloc[i + window]["datetime"],
                "price": float(centre),
                "atr": float(df.iloc[i].get("ATR", np.nan)),
            })
        return pd.DataFrame(records)

    @staticmethod
    def _cluster_levels(pivots: pd.DataFrame, tolerance: float) -> list[list[dict[str, Any]]]:
        if pivots.empty:
            return []
        records = pivots.sort_values("price").to_dict("records")
        clusters: list[list[dict[str, Any]]] = []
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

    def _evaluate_zone(
        self,
        df: pd.DataFrame,
        centre: float,
        half_width: float,
        zone_type: str,
        available_from_idx: int,
        event_prices: Optional[np.ndarray] = None,
    ) -> dict[str, Any]:
        n = len(df)
        indices = np.arange(n)
        touch_mask = (df["Low"].to_numpy() <= centre + half_width) & (df["High"].to_numpy() >= centre - half_width)
        touch_indices = indices[touch_mask & (indices >= available_from_idx)]
        episodes: list[int] = []
        last_idx: Optional[int] = None
        for idx in touch_indices:
            if last_idx is None or idx - last_idx > self.config.touch_separation_bars:
                episodes.append(int(idx))
            last_idx = int(idx)

        atr_values = df["ATR"].to_numpy(dtype=float)
        highs = df["High"].to_numpy(dtype=float)
        lows = df["Low"].to_numpy(dtype=float)
        success = failure = ambiguous = pending = 0
        favourable_atr_values: list[float] = []
        adverse_atr_values: list[float] = []
        h = self.config.response_horizon_bars

        for idx in episodes:
            atr_ref = atr_values[idx - 1] if idx > 0 and np.isfinite(atr_values[idx - 1]) else atr_values[idx]
            if not np.isfinite(atr_ref) or atr_ref <= 0 or idx + 1 >= n:
                pending += 1
                continue
            end = min(n, idx + 1 + h)
            future_highs = highs[idx + 1:end]
            future_lows = lows[idx + 1:end]
            if len(future_highs) == 0:
                pending += 1
                continue
            if zone_type == "support":
                target = centre + self.config.favourable_atr * atr_ref
                stop = centre - self.config.adverse_atr * atr_ref
                target_hits = np.flatnonzero(future_highs >= target)
                stop_hits = np.flatnonzero(future_lows <= stop)
                favourable = (np.nanmax(future_highs) - centre) / atr_ref
                adverse = (centre - np.nanmin(future_lows)) / atr_ref
            else:
                target = centre - self.config.favourable_atr * atr_ref
                stop = centre + self.config.adverse_atr * atr_ref
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
            event_count = int(np.sum((event_prices >= centre - half_width) & (event_prices <= centre + half_width)))
        else:
            event_count = 0
        dt_last = df.iloc[touch_indices[-1]]["datetime"] if len(touch_indices) else pd.NaT
        
        # --- NEW: Statistical enhancements ---
        # Wilson score CI for reaction rate
        reaction_ci_lower, reaction_ci_upper = wilson_score_interval(success, evaluated) if evaluated > 0 else (np.nan, np.nan)
        
        # Binomial test vs baseline (random chance = 50% for symmetric, or config-based)
        baseline_rate = 0.5  # Null hypothesis: random reaction
        p_value = binomial_test_pvalue(success, evaluated, baseline_rate, alternative='greater') if evaluated > 0 else np.nan
        
        # Bootstrap CI for median ATR metrics
        fav_ci_lower, fav_ci_upper = (np.nan, np.nan)
        adv_ci_lower, adv_ci_upper = (np.nan, np.nan)
        if favourable_atr_values and len(favourable_atr_values) >= 5:
            fav_ci_lower, fav_ci_upper = bootstrap_ci(np.array(favourable_atr_values), np.median, n_bootstrap=500)
        if adverse_atr_values and len(adverse_atr_values) >= 5:
            adv_ci_lower, adv_ci_upper = bootstrap_ci(np.array(adverse_atr_values), np.median, n_bootstrap=500)
        # ---------------------------------------
        
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
            "reaction_rate_ci_lower_pct": 100.0 * reaction_ci_lower if np.isfinite(reaction_ci_lower) else np.nan,
            "reaction_rate_ci_upper_pct": 100.0 * reaction_ci_upper if np.isfinite(reaction_ci_upper) else np.nan,
            "reaction_p_value_vs_random": p_value,
            "median_favourable_atr": float(np.median(favourable_atr_values)) if favourable_atr_values else np.nan,
            "median_favourable_atr_ci_lower": fav_ci_lower,
            "median_favourable_atr_ci_upper": fav_ci_upper,
            "median_adverse_atr": float(np.median(adverse_atr_values)) if adverse_atr_values else np.nan,
            "median_adverse_atr_ci_lower": adv_ci_lower,
            "median_adverse_atr_ci_upper": adv_ci_upper,
            "event_count_in_zone": event_count,
            "last_touch_utc": dt_last,
            "available_from_idx": available_from_idx,
        }

    def detect_reaction_zones(
        self,
        timeframe: str = "5min",
        side: str = "both",
        min_touches: Optional[int] = None,
        pivot_window: Optional[int] = None,
        zone_tolerance_points: Optional[float] = None,
        event_prices: Optional[pd.Series | np.ndarray] = None,
    ) -> pd.DataFrame:
        """Busca niveles de reacción por pivotes y mide retesteos posteriores.

        Las métricas se calculan en la serie histórica entregada. Para afirmar capacidad predictiva hay que
        repetir el cálculo walk-forward: no uses la tasa descriptiva in-sample como probabilidad futura.
        """
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}; ejecuta fetch_historical_data() primero.")
        if side not in {"both", "support", "resistance"}:
            raise ValueError("side debe ser both, support o resistance")
        min_touches = self.config.min_zone_touches if min_touches is None else min_touches
        pivot_window = self.config.pivot_window if pivot_window is None else pivot_window
        if min_touches < 1:
            raise ValueError("min_touches debe ser >= 1.")
        df = self.add_atr(self.data[timeframe]).reset_index(drop=True)
        valid_atr = df["ATR"].dropna()
        if valid_atr.empty:
            raise ValueError(f"No hay suficientes velas de {timeframe} para calcular ATR y zonas.")
        tolerance = float(zone_tolerance_points) if zone_tolerance_points is not None else float(valid_atr.median() * self.config.zone_tolerance_atr)
        if not np.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("La tolerancia de zona debe ser un número positivo.")
        prices = np.asarray(event_prices, dtype=float) if event_prices is not None else None
        output: list[dict[str, Any]] = []
        sides = [side] if side != "both" else ["support", "resistance"]
        for one_side in sides:
            pivots = self._find_pivots(df, one_side, pivot_window)
            for cluster in self._cluster_levels(pivots, tolerance):
                if len(cluster) < min_touches:
                    continue
                centre = float(np.median([p["price"] for p in cluster]))
                # No evaluar una zona antes de que el último pivote que la constituye haya quedado confirmado.
                available_from = max(int(p["confirmed_idx"]) for p in cluster)
                stats = self._evaluate_zone(df, centre, tolerance, one_side, available_from, prices)
                stats.update({
                    "timeframe": timeframe,
                    "pivot_count": len(cluster),
                    "first_pivot_utc": min(p["datetime"] for p in cluster),
                    "last_pivot_confirmed_utc": max(p["confirmed_at"] for p in cluster),
                    "source": "historical_pivots_full_sample",
                })
                output.append(stats)
        zones = pd.DataFrame(output)
        if not zones.empty:
            zones = zones.sort_values(
                ["reaction_rate_pct", "evaluated_touches", "touches"], ascending=[False, False, False], na_position="last"
            ).reset_index(drop=True)
        self.data[f"zones_{timeframe}"] = zones
        return zones

    def full_analysis(self, timeframes: Optional[Iterable[str]] = None) -> dict[str, dict[str, pd.DataFrame]]:
        """Calcula candidatos de eventos y zonas para cada temporalidad disponible."""
        frames = list(timeframes) if timeframes is not None else [tf for tf in TIMEFRAMES.values() if tf in self.data]
        for tf in frames:
            if tf not in self.data:
                LOG.warning("Se omite %s: no hay datos.", tf)
                continue
            events = self.detect_candle_extremes(tf)
            event_prices = events["event_price"] if not events.empty else np.array([], dtype=float)
            zones = self.detect_reaction_zones(tf, side="both", event_prices=event_prices)
            self.results[tf] = {"events": events, "zones": zones}
        return self.results



    # Walk-Forward Validation Framework

    def walk_forward_validation(
        self, timeframe: str = '5min', train_window_days: int = 60,
        test_window_days: int = 14, step_days: int = 7, min_train_bars: int = 200,
    ) -> pd.DataFrame:
        if timeframe not in self.data:
            raise KeyError(f'No hay datos de {timeframe}; ejecuta fetch_historical_data() primero.')
        df_full = self.add_atr(self.data[timeframe]).reset_index(drop=True)
        if len(df_full) < min_train_bars:
            raise ValueError(f'Insuficientes datos para walk-forward (min {min_train_bars} barras).')
        tf_seconds = {v: k for k, v in TIMEFRAMES.items()}
        seconds_per_bar = tf_seconds.get(timeframe, 300)
        bars_per_day = 86400 / seconds_per_bar
        train_bars = int(train_window_days * bars_per_day)
        test_bars = int(test_window_days * bars_per_day)
        step_bars = int(step_days * bars_per_day)
        if train_bars < min_train_bars: train_bars = min_train_bars
        n = len(df_full); results = []
        for origin in range(train_bars, n - test_bars, step_bars):
            train_end = origin; test_start = origin; test_end = min(n, origin + test_bars)
            if test_end - test_start < 10: break
            train_df = df_full.iloc[:train_end].copy()
            test_df = df_full.iloc[test_start:test_end].copy()
            train_atr = train_df['ATR'].dropna()
            if train_atr.empty: continue
            tolerance = float(train_atr.median() * self.config.zone_tolerance_atr)
            for side in ['support', 'resistance']:
                pivots = self._find_pivots(train_df, side, self.config.pivot_window)
                for cluster in self._cluster_levels(pivots, tolerance):
                    if len(cluster) < self.config.min_zone_touches: continue
                    centre = float(np.median([p['price'] for p in cluster]))
                    test_indices = np.arange(len(test_df))
                    test_highs = test_df['High'].to_numpy(dtype=float)
                    test_lows = test_df['Low'].to_numpy(dtype=float)
                    test_atr = test_df['ATR'].to_numpy(dtype=float)
                    touch_mask = (test_lows <= centre + tolerance) & (test_highs >= centre - tolerance)
                    touch_indices = test_indices[touch_mask]
                    episodes = []; last_idx = None
                    for idx in touch_indices:
                        if last_idx is None or idx - last_idx > self.config.touch_separation_bars:
                            episodes.append(int(idx))
                        last_idx = int(idx)
                    h = self.config.response_horizon_bars
                    success = failure = ambiguous = pending = 0
                    favourable_atr_vals = []; adverse_atr_vals = []
                    for idx in episodes:
                        atr_ref = test_atr[idx - 1] if idx > 0 and np.isfinite(test_atr[idx - 1]) else test_atr[idx]
                        if not np.isfinite(atr_ref) or atr_ref <= 0 or idx + 1 >= len(test_df):
                            pending += 1; continue
                        end = min(len(test_df), idx + 1 + h)
                        future_highs = test_highs[idx + 1:end]
                        future_lows = test_lows[idx + 1:end]
                        if len(future_highs) == 0: pending += 1; continue
                        if side == 'support':
                            target = centre + self.config.favourable_atr * atr_ref
                            stop = centre - self.config.adverse_atr * atr_ref
                            target_hits = np.flatnonzero(future_highs >= target)
                            stop_hits = np.flatnonzero(future_lows <= stop)
                            favourable = (np.nanmax(future_highs) - centre) / atr_ref
                            adverse = (centre - np.nanmin(future_lows)) / atr_ref
                        else:
                            target = centre - self.config.favourable_atr * atr_ref
                            stop = centre + self.config.adverse_atr * atr_ref
                            target_hits = np.flatnonzero(future_lows <= target)
                            stop_hits = np.flatnonzero(future_highs >= stop)
                            favourable = (centre - np.nanmin(future_lows)) / atr_ref
                            adverse = (np.nanmax(future_highs) - centre) / atr_ref
                        favourable_atr_vals.append(float(max(0.0, favourable)))
                        adverse_atr_vals.append(float(max(0.0, adverse)))
                        t_target = int(target_hits[0]) if len(target_hits) else None
                        t_stop = int(stop_hits[0]) if len(stop_hits) else None
                        if t_target is None and t_stop is None: pending += 1
                        elif t_target is not None and t_stop is not None and t_target == t_stop: ambiguous += 1
                        elif t_target is not None and (t_stop is None or t_target < t_stop): success += 1
                        else: failure += 1
                    evaluated = success + failure
                    if evaluated >= 3:
                        reaction_rate_oos = 100.0 * success / evaluated
                        ci_lower, ci_upper = wilson_score_interval(success, evaluated)
                        p_val = binomial_test_pvalue(success, evaluated, 0.5, alternative='greater')
                        results.append({
                            'fold': len(results) + 1,
                            'train_start_utc': train_df['datetime'].iloc[0],
                            'train_end_utc': train_df['datetime'].iloc[-1],
                            'test_start_utc': test_df['datetime'].iloc[0],
                            'test_end_utc': test_df['datetime'].iloc[-1],
                            'zone_type': side, 'zone_price': centre, 'zone_tolerance': tolerance,
                            'train_touches': len(episodes), 'oos_evaluated': evaluated,
                            'oos_reactions': success, 'oos_failures': failure,
                            'oos_reaction_rate_pct': reaction_rate_oos,
                            'oos_reaction_ci_lower_pct': 100.0 * ci_lower if np.isfinite(ci_lower) else np.nan,
                            'oos_reaction_ci_upper_pct': 100.0 * ci_upper if np.isfinite(ci_upper) else np.nan,
                            'oos_p_value_vs_random': p_val,
                            'oos_median_favourable_atr': float(np.median(favourable_atr_vals)) if favourable_atr_vals else np.nan,
                            'oos_median_adverse_atr': float(np.median(adverse_atr_vals)) if adverse_atr_vals else np.nan,
                        })
        return pd.DataFrame(results)

    def aggregate_walk_forward_results(self, wf_df: pd.DataFrame) -> Dict[str, Any]:
        if wf_df.empty: return {'error': 'No walk-forward results to aggregate'}
        valid = wf_df.dropna(subset=['oos_reaction_rate_pct', 'oos_p_value_vs_random'])
        if valid.empty: return {'error': 'No valid folds with evaluated zones'}
        pvals = valid['oos_p_value_vs_random'].to_numpy()
        rejected, adj_pvals = benjamini_hochberg_fdr(pvals, alpha=0.05)
        valid = valid.copy()
        valid['oos_p_value_adj'] = adj_pvals
        valid['oos_significant_fdr'] = rejected
        weights = valid['oos_evaluated']
        weighted_rate = np.average(valid['oos_reaction_rate_pct'], weights=weights)
        n_folds = len(valid)
        n_positive = int((valid['oos_reaction_rate_pct'] > 50).sum())
        p_sign = binomial_test_pvalue(n_positive, n_folds, 0.5, alternative='greater')
        return {
            'n_folds': int(n_folds), 'n_significant_fdr': int(rejected.sum()),
            'weighted_mean_reaction_rate_pct': float(weighted_rate),
            'median_reaction_rate_pct': float(valid['oos_reaction_rate_pct'].median()),
            'sign_test_p_value': p_sign,
            'folds_summary': valid[['fold', 'zone_type', 'zone_price', 'oos_evaluated',
                                    'oos_reaction_rate_pct', 'oos_p_value_vs_random',
                                    'oos_p_value_adj', 'oos_significant_fdr']].to_dict('records'),
        }

    @staticmethod
    def _check_plotly() -> None:
        if go is None:
            raise RuntimeError("Para gráficos instala Plotly: pip install plotly")

    def plot_candles_with_zones(self, timeframe: str = "5min", max_bars: int = 500):
        self._check_plotly()
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.add_atr(self.data[timeframe]).tail(max_bars)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                            row_heights=[0.75, 0.25], subplot_titles=(f"{self.instrument_name} — {timeframe}", "ATR(14)"))
        fig.add_trace(go.Candlestick(x=df["datetime"], open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="OHLC"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["datetime"], y=df["ATR"], mode="lines", name="ATR(14)"), row=2, col=1)
        zones = self.data.get(f"zones_{timeframe}", pd.DataFrame())
        if not zones.empty:
            for _, zone in zones.head(12).iterrows():
                color = "rgba(0,180,100,0.14)" if zone["zone_type"] == "support" else "rgba(220,60,60,0.14)"
                fig.add_hrect(y0=zone["lower"], y1=zone["upper"], fillcolor=color, line_width=0,
                              annotation_text=f"{zone['zone_type']} | toques={zone['touches']} | reacción={zone['reaction_rate_pct']:.1f}%" if pd.notna(zone["reaction_rate_pct"]) else f"{zone['zone_type']} | toques={zone['touches']}",
                              annotation_position="top left", row=1, col=1)
        events = self.data.get(f"events_{timeframe}", pd.DataFrame())
        if not events.empty:
            events = events[events["datetime"] >= df["datetime"].min()]
            fig.add_trace(go.Scatter(x=events["datetime"], y=events["event_price"], mode="markers",
                                     name="Candidato extremo", marker={"size": 8, "symbol": "diamond"}), row=1, col=1)
        fig.update_layout(height=800, xaxis_rangeslider_visible=False, template="plotly_white", legend_title="Capas")
        return fig

    def plot_return_distribution(self):
        self._check_plotly()
        if "ticks" not in self.data:
            raise KeyError("Primero ejecuta fetch_tick_history().")
        returns = self.data["ticks"]["price"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        fig = go.Figure(go.Histogram(x=returns, nbinsx=100, name="Retorno por tick (%)"))
        for q, label in [(0.01, "P1"), (0.50, "Mediana"), (0.99, "P99")]:
            v = float(returns.quantile(q))
            fig.add_vline(x=v, line_dash="dash", annotation_text=f"{label}: {v:.5f}%")
        fig.update_layout(title=f"{self.instrument_name}: distribución empírica de retornos por tick", xaxis_title="Retorno (%)", yaxis_title="Frecuencia", template="plotly_white")
        return fig

    def plot_volatility(self, timeframe: str = "5min", rolling_window: int = 20):
        self._check_plotly()
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.add_atr(self.data[timeframe])
        close_return = df["Close"].pct_change().mul(100)
        realized = close_return.rolling(rolling_window, min_periods=rolling_window).std()
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                            subplot_titles=("Precio", "ATR (puntos) y desviación estándar de retornos (%)"),
                            specs=[[{}], [{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=df["datetime"], y=df["Close"], name="Close", mode="lines"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["datetime"], y=df["ATR"], name="ATR(14)", mode="lines"), row=2, col=1, secondary_y=False)
        fig.add_trace(go.Scatter(x=df["datetime"], y=realized, name=f"Volatilidad retornos ({rolling_window})", mode="lines"), row=2, col=1, secondary_y=True)
        fig.update_yaxes(title_text="ATR (puntos)", secondary_y=False, row=2, col=1)
        fig.update_yaxes(title_text="Desviación estándar (%)", secondary_y=True, row=2, col=1)
        fig.update_layout(title=f"{self.instrument_name}: volatilidad observada", template="plotly_white", height=700)
        return fig

    def plot_tick_heatmap(self, price_bin_width: Optional[float] = None):
        self._check_plotly()
        if "ticks" not in self.data or self.data["ticks"].empty:
            raise KeyError("Primero ejecuta fetch_tick_history().")
        df = self.data["ticks"].copy()
        if price_bin_width is None:
            price_bin_width = max(abs(float(df["price"].median())) * 0.0005, float(df["price"].std()) * 0.01, 1e-8)
        if price_bin_width <= 0:
            raise ValueError("price_bin_width debe ser positivo.")
        df["hour_utc"] = df["datetime"].dt.hour
        df["price_bin"] = (df["price"] / price_bin_width).round() * price_bin_width
        matrix = df.groupby(["price_bin", "hour_utc"]).size().unstack(fill_value=0).sort_index()
        fig = go.Figure(go.Heatmap(z=matrix.to_numpy(), x=matrix.columns, y=matrix.index, colorbar_title="Ticks"))
        fig.update_layout(title=f"{self.instrument_name}: frecuencia observada de ticks por hora UTC y banda de precio", xaxis_title="Hora UTC", yaxis_title=f"Precio agrupado (ancho={price_bin_width:.6g})", template="plotly_white")
        return fig

    def plot_event_heatmap(self, timeframe: Optional[str] = "5min", price_bin_width: Optional[float] = None, metric: str = "count"):
        """Mapa de eventos candidatos por hora UTC y nivel de precio; separado del mapa de frecuencia de ticks."""
        self._check_plotly()
        if metric not in {"count", "magnitude"}:
            raise ValueError("metric debe ser 'count' o 'magnitude'.")
        if timeframe is not None:
            key = f"events_{timeframe}"
            events = self.data.get(key, pd.DataFrame())
            price_col = "event_price"
            magnitude_col = "directional_excursion_pct"
        else:
            events = self.data.get("tick_events", pd.DataFrame())
            price_col = "price"
            magnitude_col = "directional_move_pct"
        if events is None or events.empty:
            raise ValueError("No hay eventos candidatos. Ejecuta primero el detector correspondiente.")
        df = events.copy()
        if "datetime" not in df or price_col not in df:
            raise ValueError("El conjunto de eventos no tiene las columnas datetime y precio esperado.")
        df["hour_utc"] = pd.to_datetime(df["datetime"], utc=True).dt.hour
        if price_bin_width is None:
            price_bin_width = max(abs(float(df[price_col].median())) * 0.0005, float(df[price_col].std()) * 0.01, 1e-8)
        if price_bin_width <= 0:
            raise ValueError("price_bin_width debe ser positivo.")
        df["price_bin"] = (pd.to_numeric(df[price_col], errors="coerce") / price_bin_width).round() * price_bin_width
        if metric == "count":
            matrix = df.groupby(["price_bin", "hour_utc"]).size().unstack(fill_value=0).sort_index()
            color_title = "Eventos"
        else:
            if magnitude_col not in df:
                raise ValueError(f"No existe la columna {magnitude_col} para medir magnitud.")
            matrix = df.pivot_table(index="price_bin", columns="hour_utc", values=magnitude_col, aggfunc="sum", fill_value=0).sort_index()
            color_title = "Magnitud acumulada (%)"
        fig = go.Figure(go.Heatmap(z=matrix.to_numpy(), x=matrix.columns, y=matrix.index, colorbar_title=color_title))
        fig.update_layout(title=f"{self.instrument_name}: mapa de calor de eventos candidatos ({metric})", xaxis_title="Hora UTC", yaxis_title=f"Precio agrupado (ancho={price_bin_width:.6g})", template="plotly_white")
        return fig

    def plot_interevent_survival(self):
        """Curva Kaplan–Meier de intervalos entre candidatos de eventos y cola final censurada."""
        self._check_plotly()
        if "tick_events" not in self.data:
            self.detect_tick_extremes()
        ticks = self.data.get("ticks", pd.DataFrame())
        events = self.data.get("tick_events", pd.DataFrame())
        if ticks.empty or events.empty:
            raise ValueError("No hay suficientes ticks/eventos candidatos para calcular intervalos.")
        idx = np.sort(events["tick_index"].astype(int).unique())
        if len(idx) < 2:
            raise ValueError("Se necesitan al menos dos eventos candidatos para medir intervalos.")
        durations = list(np.diff(idx).astype(int))
        observed = [True] * len(durations)
        tail = max(1, len(ticks) - 1 - int(idx[-1]))
        durations.append(tail)
        observed.append(False)  # El siguiente evento aún no se observó al terminar la muestra.
        t = np.asarray(durations, dtype=int)
        e = np.asarray(observed, dtype=bool)
        survival = 1.0
        out = [(0, 1.0)]
        for value in sorted(np.unique(t)):
            at_risk = int(np.sum(t >= value))
            events_at_value = int(np.sum((t == value) & e))
            if at_risk and events_at_value:
                survival *= 1.0 - events_at_value / at_risk
            out.append((int(value), float(survival)))
        curve = pd.DataFrame(out, columns=["ticks_since_event", "survival_probability"])
        fig = go.Figure(go.Scatter(x=curve["ticks_since_event"], y=curve["survival_probability"], mode="lines", line_shape="hv", name="Kaplan–Meier"))
        fig.update_layout(title=f"{self.instrument_name}: supervivencia empírica del intervalo entre candidatos", xaxis_title="Ticks transcurridos", yaxis_title="P(intervalo > x)", yaxis_range=[0, 1], template="plotly_white")
        return fig

    def plot_autocorrelation(self, max_lag: int = 50):
        self._check_plotly()
        if "ticks" not in self.data or self.data["ticks"].empty:
            raise KeyError("Primero ejecuta fetch_tick_history().")
        returns = self.data["ticks"]["price"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if len(returns) <= max_lag + 5:
            raise ValueError("Hay muy pocos retornos para el máximo retardo solicitado.")
        rows = []
        for lag in range(1, max_lag + 1):
            rows.append({"lag": lag, "returns_acf": returns.autocorr(lag), "squared_returns_acf": returns.pow(2).autocorr(lag)})
        acf = pd.DataFrame(rows)
        band = 1.96 / np.sqrt(len(returns))
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, subplot_titles=("Autocorrelación de retornos", "Autocorrelación de retornos al cuadrado"))
        fig.add_trace(go.Bar(x=acf["lag"], y=acf["returns_acf"], name="Retornos"), row=1, col=1)
        fig.add_trace(go.Bar(x=acf["lag"], y=acf["squared_returns_acf"], name="Retornos²"), row=2, col=1)
        for row in (1, 2):
            fig.add_hline(y=band, line_dash="dash", line_color="grey", row=row, col=1)
            fig.add_hline(y=-band, line_dash="dash", line_color="grey", row=row, col=1)
        fig.update_layout(title=f"{self.instrument_name}: autocorrelación exploratoria (bandas aproximadas 95%)", template="plotly_white", height=650)
        return fig

    def conditional_event_table(self, horizon_ticks: int = 100, bin_size: int = 100, max_ticks_since_event: int = 5000) -> pd.DataFrame:
        """Tasa histórica descriptiva de un candidato en los siguientes H ticks según ticks desde evento previo."""
        if "ticks" not in self.data:
            raise KeyError("Primero ejecuta fetch_tick_history().")
        events = self.data.get("tick_events")
        if events is None:
            events = self.detect_tick_extremes()
        ticks = self.data["ticks"].sort_values("epoch").reset_index(drop=True)
        event_flags = np.zeros(len(ticks), dtype=np.int8)
        event_idx = events["tick_index"].astype(int).to_numpy() if not events.empty else np.array([], dtype=int)
        event_idx = event_idx[(event_idx >= 0) & (event_idx < len(ticks))]
        event_flags[event_idx] = 1
        last_event = -1
        since = np.full(len(ticks), -1, dtype=int)
        for i, flag in enumerate(event_flags):
            since[i] = i - last_event if last_event >= 0 else -1
            if flag:
                last_event = i
        future = pd.Series(event_flags).shift(-1).iloc[::-1].rolling(horizon_ticks, min_periods=horizon_ticks).max().iloc[::-1]
        future_event = future.to_numpy()
        valid = (since >= 0) & (since <= max_ticks_since_event) & np.isfinite(future_event)
        tmp = pd.DataFrame({"ticks_since_event": since[valid], "event_in_next_horizon": future_event[valid].astype(int)})
        if tmp.empty:
            return pd.DataFrame(columns=["ticks_since_event_bin", "observations", "future_events", "empirical_probability_pct"])
        tmp["ticks_since_event_bin"] = (tmp["ticks_since_event"] // bin_size) * bin_size
        result = tmp.groupby("ticks_since_event_bin")["event_in_next_horizon"].agg(observations="size", future_events="sum").reset_index()
        result["empirical_probability_pct"] = 100 * result["future_events"] / result["observations"]
        result["horizon_ticks"] = horizon_ticks
        result["interpretation"] = "Descriptivo in-sample; no es una probabilidad validada fuera de muestra"
        return result

    def plot_conditional_event_probability(self, horizon_ticks: int = 100, bin_size: int = 100):
        self._check_plotly()
        table = self.conditional_event_table(horizon_ticks=horizon_ticks, bin_size=bin_size)
        if table.empty:
            raise ValueError("No hay suficientes eventos/ticks para estimar tasas condicionadas.")
        fig = go.Figure(go.Scatter(x=table["ticks_since_event_bin"], y=table["empirical_probability_pct"], mode="lines+markers", name="Tasa observada"))
        fig.update_layout(title=f"{self.instrument_name}: frecuencia de candidatos en los siguientes {horizon_ticks} ticks", xaxis_title="Ticks desde el candidato anterior (intervalo agrupado)", yaxis_title="Frecuencia observada (%)", template="plotly_white")
        return fig

    @staticmethod
    def plot_calibration(y_true: Iterable[int], y_probability: Iterable[float], bins: int = 10):
        """Diagrama de calibración para predicciones producidas fuera de muestra por un modelo entrenado."""
        if go is None:
            raise RuntimeError("Para gráficos instala Plotly: pip install plotly")
        y = np.asarray(list(y_true), dtype=float)
        p = np.asarray(list(y_probability), dtype=float)
        if len(y) != len(p) or len(y) == 0:
            raise ValueError("y_true y y_probability deben tener igual longitud no nula.")
        mask = np.isfinite(y) & np.isfinite(p) & (p >= 0) & (p <= 1)
        y, p = y[mask], p[mask]
        if len(y) == 0:
            raise ValueError("No hay datos válidos para calibrar.")
        edges = np.linspace(0, 1, bins + 1)
        bin_id = np.clip(np.digitize(p, edges, right=True) - 1, 0, bins - 1)
        records = []
        for b in range(bins):
            mask_b = bin_id == b
            if mask_b.any():
                records.append({"mean_predicted": float(p[mask_b].mean()), "observed_frequency": float(y[mask_b].mean()), "n": int(mask_b.sum())})
        calib = pd.DataFrame(records)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="Calibración perfecta", line={"dash": "dash", "color": "grey"}))
        fig.add_trace(go.Scatter(x=calib["mean_predicted"], y=calib["observed_frequency"], mode="lines+markers", name="Observado", text=calib["n"], hovertemplate="Predicho=%{x:.3f}<br>Observado=%{y:.3f}<br>N=%{text}<extra></extra>"))
        fig.update_layout(title="Calibración de probabilidades (usar predicciones de test/walk-forward)", xaxis_title="Probabilidad media predicha", yaxis_title="Frecuencia observada", xaxis_range=[0, 1], yaxis_range=[0, 1], template="plotly_white")
        return fig

    def plot_tick_spikes(self, threshold_quantile: float = 0.999, window_ticks: int = 2000):
        self._check_plotly()
        if "ticks" not in self.data or self.data["ticks"].empty:
            raise KeyError("Primero ejecuta fetch_tick_history().")
        df = self.data["ticks"].copy()
        returns = df["price"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        if self.direction == "up": directional = returns.clip(lower=0)
        else: directional = (-returns).clip(lower=0)
        rolling_q = directional.rolling(window_ticks, min_periods=max(100, window_ticks // 10)).quantile(threshold_quantile)
        threshold = rolling_q.reindex(df.index).fillna(method='ffill').fillna(method='bfill')
        is_spike = (directional >= threshold) & (directional > 0)
        spike_indices = df.index[is_spike]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                            subplot_titles=("Precio y Spikes Detectados", "Retorno Direccional vs Umbral"),
                            row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(x=df["datetime"], y=df["price"], mode="lines", 
                                 name="Precio", line=dict(color="blue", width=0.5)), row=1, col=1)
        if len(spike_indices) > 0:
            spike_df = df.loc[spike_indices]
            fig.add_trace(go.Scatter(x=spike_df["datetime"], y=spike_df["price"], mode="markers",
                                     name=f"Spike (q={threshold_quantile})", 
                                     marker=dict(color="red", size=8, symbol="diamond")), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["datetime"], y=directional.reindex(df.index), mode="lines",
                                 name="Retorno direccional", line=dict(color="orange", width=0.5)), row=2, col=1)
        fig.add_trace(go.Scatter(x=df["datetime"], y=threshold.reindex(df.index), mode="lines",
                                 name=f"Umbral cuantil {threshold_quantile}", line=dict(color="red", dash="dash")), row=2, col=1)
        fig.update_yaxes(title_text="Precio", row=1, col=1)
        fig.update_yaxes(title_text="Retorno (%)", row=2, col=1)
        fig.update_layout(title=f"{self.instrument_name}: Ticks con Detección de Spikes (q={threshold_quantile})",
                          template="plotly_white", height=800, showlegend=True)
        return fig

    def plot_return_distribution(self, timeframe: str = "ticks", fit_evt: bool = True, 
                                 threshold_quantile: float = 0.99):
        self._check_plotly()
        if timeframe not in self.data or self.data[timeframe].empty:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.data[timeframe]
        if timeframe == "ticks":
            returns = df["price"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        else:
            returns = df["Close"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        if self.direction == "up": directional = returns.clip(lower=0)
        else: directional = (-returns).clip(lower=0)
        directional = directional[directional > 0]
        fig = make_subplots(rows=2, cols=2, subplot_titles=(
            "Histograma Retornos Direccionales", "Q-Q Plot vs Normal",
            "Tail Fit GPD (EVT)", "Mean Excess Plot"
        ), vertical_spacing=0.12, horizontal_spacing=0.1)
        fig.add_trace(go.Histogram(x=directional, nbinsx=80, name="Empirica", 
                                   histnorm="probability density", marker_color="steelblue"), row=1, col=1)
        if SCIPY_AVAILABLE and len(directional) > 30:
            mu, sigma = directional.mean(), directional.std()
            x_range = np.linspace(directional.min(), directional.max(), 200)
            normal_pdf = stats.norm.pdf(x_range, mu, sigma)
            fig.add_trace(go.Scatter(x=x_range, y=normal_pdf, mode="lines", 
                                     name="Normal Fit", line=dict(color="red", dash="dash")), row=1, col=1)
        if SCIPY_AVAILABLE and len(directional) > 30:
            theoretical_quantiles = stats.norm.ppf(np.linspace(0.01, 0.99, len(directional)))
            sample_quantiles = np.sort(directional)
            fig.add_trace(go.Scatter(x=theoretical_quantiles, y=sample_quantiles, mode="markers",
                                     name="Q-Q", marker=dict(size=3)), row=1, col=2)
            min_q, max_q = min(theoretical_quantiles.min(), sample_quantiles.min()), max(theoretical_quantiles.max(), sample_quantiles.max())
            fig.add_trace(go.Scatter(x=[min_q, max_q], y=[min_q, max_q], mode="lines",
                                     name="y=x", line=dict(color="red", dash="dash")), row=1, col=2)
        if fit_evt and SCIPY_AVAILABLE and len(directional) >= 100:
            evt_result = self.fit_evt_gpd(timeframe=timeframe, threshold_quantile=threshold_quantile)
            if "xi" in evt_result and np.isfinite(evt_result["xi"]):
                xi = evt_result["xi"]; sigma = evt_result["sigma"]; threshold = evt_result["threshold"]
                excesses = directional[directional > threshold] - threshold
                if len(excesses) > 10:
                    x_tail = np.linspace(0, excesses.max(), 100)
                    if xi == 0:
                        gpd_pdf = np.exp(-x_tail / sigma) / sigma
                    else:
                        gpd_pdf = (1/sigma) * (1 + xi * x_tail / sigma) ** (-(1 + 1/xi))
                    fig.add_trace(go.Scatter(x=x_tail + threshold, y=gpd_pdf, mode="lines",
                                             name=f"GPD Fit (xi={xi:.3f})", line=dict(color="green", width=2)), row=2, col=1)
                for rp, rl in evt_result.get("return_levels", {}).items():
                    if np.isfinite(rl):
                        fig.add_annotation(x=rl, y=0, text=f"{rp}: {rl:.4f}%",
                                           showarrow=True, arrowhead=2, row=2, col=1)
        if fit_evt and SCIPY_AVAILABLE and len(directional) >= 100:
            evt_result = self.fit_evt_gpd(timeframe=timeframe, threshold_quantile=threshold_quantile)
            if "mean_excess_plot" in evt_result:
                me = evt_result["mean_excess_plot"]
                fig.add_trace(go.Scatter(x=me["excesses"], y=me["mean_excess"], mode="lines",
                                         name="Mean Excess", line=dict(color="purple")), row=2, col=2)
                if "xi" in evt_result and np.isfinite(evt_result["xi"]) and evt_result["xi"] > 0:
                    xi = evt_result["xi"]; sigma = evt_result["sigma"]
                    x_ref = np.linspace(min(me["excesses"]), max(me["excesses"]), 20)
                    y_ref = sigma / (1 - xi) + xi / (1 - xi) * x_ref
                    fig.add_trace(go.Scatter(x=x_ref, y=y_ref, mode="lines",
                                             name=f"GPD Linear (xi={xi:.3f})", line=dict(color="red", dash="dash")), row=2, col=2)
        fig.update_xaxes(title_text="Retorno (%)", row=1, col=1)
        fig.update_xaxes(title_text="Cuantiles Teoricos", row=1, col=2)
        fig.update_xaxes(title_text="Exceso sobre Umbral (%)", row=2, col=1)
        fig.update_xaxes(title_text="Exceso", row=2, col=2)
        fig.update_yaxes(title_text="Densidad", row=1, col=1)
        fig.update_yaxes(title_text="Cuantiles Muestrales", row=1, col=2)
        fig.update_yaxes(title_text="Densidad", row=2, col=1)
        fig.update_yaxes(title_text="Media Exceso", row=2, col=2)
        fig.update_layout(title=f"{self.instrument_name}: Distribución de Retornos y Colas Extremas (EVT)",
                          template="plotly_white", height=900, showlegend=True)
        return fig

    def plot_volatility(self, timeframe: str = "5min", rolling_window: int = 20, 
                        show_regimes: bool = True):
        self._check_plotly()
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.add_atr(self.data[timeframe]).reset_index(drop=True)
        close_return = df["Close"].pct_change().mul(100)
        realized = close_return.rolling(rolling_window, min_periods=rolling_window).std()
        atr = df["ATR"]
        regimes = None
        if show_regimes:
            regime_result = self.detect_volatility_regimes(timeframe, n_regimes=2)
            if "markov_switching" in regime_result and "regime_probs" in regime_result["markov_switching"]:
                regimes = regime_result["markov_switching"]["regime_probs"]
        fig = make_subplots(rows=3 if regimes else 2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                            subplot_titles=("Precio", "ATR y Volatilidad Realizada", "Regimenes de Volatilidad" if regimes else None),
                            row_heights=[0.4, 0.3, 0.3] if regimes else [0.5, 0.5])
        fig.add_trace(go.Scatter(x=df["datetime"], y=df["Close"], mode="lines", name="Close",
                                 line=dict(color="black", width=0.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["datetime"], y=atr, mode="lines", name="ATR(14)",
                                 line=dict(color="blue")), row=2, col=1)
        fig.add_trace(go.Scatter(x=df["datetime"], y=realized, mode="lines", name=f"Realized Vol ({rolling_window})",
                                 line=dict(color="orange")), row=2, col=1)
        if regimes is not None and len(regimes) == len(df):
            prob_high = np.array([regimes[i][1] if len(regimes[i]) > 1 else 0 for i in range(len(regimes))])
            fig.add_trace(go.Scatter(x=df["datetime"], y=prob_high, mode="lines", fill="tozeroy",
                                     name="Prob. Regimen Alta Vol", line=dict(color="red"), fillcolor="rgba(255,0,0,0.2)"), row=3, col=1)
            fig.update_yaxes(title_text="P(High Vol)", row=3, col=1)
        fig.update_yaxes(title_text="Precio", row=1, col=1)
        fig.update_yaxes(title_text="Volatilidad (puntos/%)", row=2, col=1)
        fig.update_layout(title=f"{self.instrument_name}: Volatilidad Movil y Regimenes",
                          template="plotly_white", height=900 if regimes else 700, showlegend=True)
        return fig

    def plot_interevent_survival(self, timeframe: str = "ticks", min_events: int = 10):
        self._check_plotly()
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}.")
        events = self.data.get(f"events_{timeframe}") if timeframe != "ticks" else self.data.get("tick_events")
        if events is None or len(events) < min_events:
            if timeframe == "ticks": events = self.detect_tick_extremes()
            else: events = self.detect_candle_extremes(timeframe)
        if events is None or len(events) < min_events:
            raise ValueError(f"Pocos eventos ({len(events) if events is not None else 0}) para curva de supervivencia.")
        if "epoch" in events.columns:
            times = events["epoch"].to_numpy()
        elif "datetime" in events.columns:
            times = events["datetime"].astype("int64").to_numpy() / 1e9
        else:
            raise ValueError("Eventos sin columna de tiempo valida.")
        inter_times = np.diff(np.sort(times))
        inter_times = inter_times[inter_times > 0]
        if len(inter_times) < min_events:
            raise ValueError("Intervalos entre eventos insuficientes.")
        sorted_times = np.sort(inter_times)
        n = len(sorted_times)
        survival = np.arange(n, 0, -1) / n
        exp_survival = None; ks_stat = np.nan; ks_p = np.nan
        if SCIPY_AVAILABLE:
            try:
                lambda_mle = 1 / np.mean(inter_times)
                exp_survival = np.exp(-lambda_mle * sorted_times)
                ks_stat, ks_p = stats.kstest(inter_times, 'expon', args=(0, 1/lambda_mle))
            except Exception:
                pass
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=sorted_times, y=survival, mode="lines", name="Kaplan-Meier",
                                 line=dict(color="blue", width=2)))
        if exp_survival is not None:
            fig.add_trace(go.Scatter(x=sorted_times, y=exp_survival, mode="lines", 
                                     name=f"Exponencial (lambda={lambda_mle:.6f})", line=dict(color="red", dash="dash")))
        fig.update_xaxes(title_text=f"Intervalo entre eventos ({'segundos' if timeframe == 'ticks' else 'barras'})", type="log")
        fig.update_yaxes(title_text="Supervivencia S(t) = P(T > t)", type="log")
        fig.update_layout(title=f"{self.instrument_name}: Curva de Supervivencia Inter-Eventos (log-log)",
                          template="plotly_white", height=600,
                          annotations=[dict(x=0.02, y=0.98, xref="paper", yref="paper",
                                           text=f"KS vs Exp: stat={ks_stat:.4f}, p={ks_p:.4f}" if np.isfinite(ks_stat) else "KS no disponible",
                                           showarrow=False, align="left", bgcolor="white", bordercolor="grey")])
        return fig

    def plot_autocorrelation(self, timeframe: str = "ticks", max_lag: int = 50):
        self._check_plotly()
        if timeframe not in self.data or self.data[timeframe].empty:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.data[timeframe]
        if timeframe == "ticks":
            returns = df["price"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        else:
            returns = df["Close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        if len(returns) <= max_lag + 5:
            raise ValueError("Muy pocos retornos para el retardo solicitado.")
        squared_returns = returns ** 2
        if STATSMODELS_AVAILABLE:
            acf_returns = acf(returns, nlags=max_lag, fft=True)
            acf_squared = acf(squared_returns, nlags=max_lag, fft=True)
            conf_int = 1.96 / np.sqrt(len(returns))
        else:
            acf_returns = np.array([returns.autocorr(lag) for lag in range(max_lag + 1)])
            acf_squared = np.array([squared_returns.autocorr(lag) for lag in range(max_lag + 1)])
            conf_int = 1.96 / np.sqrt(len(returns))
        lags = np.arange(max_lag + 1)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                            subplot_titles=("Autocorrelación de Retornos", "Autocorrelación de Retornos² (Volatility Clustering)"))
        colors = ["red" if abs(v) > conf_int and i > 0 else "blue" for i, v in enumerate(acf_returns)]
        fig.add_trace(go.Bar(x=lags, y=acf_returns, name="ACF Retornos", marker_color=colors), row=1, col=1)
        fig.add_hline(y=conf_int, line_dash="dash", line_color="grey", row=1, col=1)
        fig.add_hline(y=-conf_int, line_dash="dash", line_color="grey", row=1, col=1)
        colors2 = ["red" if abs(v) > conf_int and i > 0 else "orange" for i, v in enumerate(acf_squared)]
        fig.add_trace(go.Bar(x=lags, y=acf_squared, name="ACF Retornos²", marker_color=colors2), row=2, col=1)
        fig.add_hline(y=conf_int, line_dash="dash", line_color="grey", row=2, col=1)
        fig.add_hline(y=-conf_int, line_dash="dash", line_color="grey", row=2, col=1)
        fig.update_xaxes(title_text="Retardo (lags)", row=2, col=1)
        fig.update_yaxes(title_text="ACF", row=1, col=1)
        fig.update_yaxes(title_text="ACF", row=2, col=1)
        fig.update_layout(title=f"{self.instrument_name}: Autocorrelación de Retornos y Retornos² ({timeframe})",
                          template="plotly_white", height=700, showlegend=False)
        return fig

    def plot_price_heatmap(self, timeframe: str = "5min", price_bin_width: float = None):
        self._check_plotly()
        if timeframe not in self.data or self.data[timeframe].empty:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.data[timeframe].copy()
        if price_bin_width is None:
            price_bin_width = max(abs(float(df["Close"].median())) * 0.0005, float(df["Close"].std()) * 0.01, 1e-8)
        if price_bin_width <= 0:
            raise ValueError("price_bin_width debe ser positivo.")
        df["hour"] = df["datetime"].dt.hour
        df["price_bin"] = (df["Close"] / price_bin_width).round() * price_bin_width
        heatmap_data = df.groupby(["hour", "price_bin"]).size().reset_index(name="count")
        if heatmap_data.empty:
            raise ValueError("No hay datos suficientes para el heatmap.")
        pivot = heatmap_data.pivot(index="price_bin", columns="hour", values="count").fillna(0)
        pivot = pivot.sort_index(ascending=False)
        fig = go.Figure(go.Heatmap(
            z=pivot.values,
            x=[f"{h:02d}:00" for h in pivot.columns],
            y=pivot.index,
            colorscale="Viridis",
            colorbar=dict(title="Frecuencia"),
        ))
        zones_df = self.data.get(f"zones_{timeframe}", pd.DataFrame())
        if not zones_df.empty:
            for _, zone in zones_df.iterrows():
                fig.add_hline(y=zone["price"], line_color="red", line_dash="dash", 
                             annotation_text=f"{zone['zone_type']} ({zone['touches']} toques)", 
                             annotation_position="right")
        fig.update_layout(title=f"{self.instrument_name}: Heatmap Frecuencia Precio-Hora ({timeframe})",
                          xaxis_title="Hora UTC", yaxis_title="Precio", template="plotly_white", height=700)
        return fig

    def residual_diagnostics(self, timeframe: str = "ticks", max_lag: int = 20):
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.data[timeframe]
        if timeframe == "ticks":
            returns = df["price"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        else:
            returns = df["Close"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        returns = returns.dropna()
        if len(returns) < max_lag + 10:
            return {"error": "Insuficientes datos para diagnosticos."}
        result = {"timeframe": timeframe, "n_observations": len(returns)}
        if STATSMODELS_AVAILABLE:
            try:
                lb_result = acorr_ljungbox(returns, lags=[max_lag], return_df=True)
                result["ljung_box"] = {"statistic": float(lb_result["lb_stat"].iloc[0]),
                    "p_value": float(lb_result["lb_pvalue"].iloc[0]),
                    "reject_null": float(lb_result["lb_pvalue"].iloc[0]) < 0.05}
            except Exception as e:
                result["ljung_box"] = {"error": str(e)}
        if SCIPY_AVAILABLE:
            try:
                jb_stat, jb_p = stats.jarque_bera(returns)
                result["jarque_bera"] = {"statistic": float(jb_stat), "p_value": float(jb_p),
                    "reject_normality": jb_p < 0.05}
            except Exception as e:
                result["jarque_bera"] = {"error": str(e)}
        if STATSMODELS_AVAILABLE:
            try:
                arch_result = het_arch(returns, maxlags=max_lag)
                result["arch_lm"] = {"statistic": float(arch_result[0]), "p_value": float(arch_result[1]),
                    "reject_null": arch_result[1] < 0.05}
            except Exception as e:
                result["arch_lm"] = {"error": str(e)}
        if STATSMODELS_AVAILABLE:
            try:
                adf_result = adfuller(returns, maxlag=max_lag, autolag='AIC')
                result["adf"] = {"statistic": float(adf_result[0]), "p_value": float(adf_result[1]),
                    "is_stationary": adf_result[1] < 0.05,
                    "critical_values": {k: float(v) for k, v in adf_result[4].items()}}
            except Exception as e:
                result["adf"] = {"error": str(e)}
        return result

    def fit_garch(self, timeframe: str = "ticks", p: int = 1, q: int = 1):
        if not ARCH_AVAILABLE:
            return {"error": "Arch package no instalado (pip install arch)."}
        if timeframe not in self.data:
            raise KeyError(f"No hay datos de {timeframe}.")
        df = self.data[timeframe]
        if timeframe == "ticks":
            returns = df["price"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        else:
            returns = df["Close"].pct_change().mul(100).replace([np.inf, -np.inf], np.nan).dropna()
        returns = returns.dropna()
        if len(returns) < 100:
            return {"error": "Insuficientes datos para GARCH (minimo 100 observaciones)."}
        try:
            model = arch_model(returns, vol='Garch', p=p, q=q, dist='normal')
            fit = model.fit(update_freq=0, disp='off')
            forecast = fit.forecast(horizon=10)
            return {"timeframe": timeframe, "order": (p, q),
                "params": {k: float(v) for k, v in fit.params.items()},
                "t_values": {k: float(v) for k, v in fit.tvalues.items()},
                "p_values": {k: float(v) for k, v in fit.pvalues.items()},
                "log_likelihood": float(fit.loglikelihood), "aic": float(fit.aic), "bic": float(fit.bic),
                "conditional_volatility": fit.conditional_volatility.tolist(),
                "forecast_horizon_10": forecast.variance.iloc[-1].to_numpy().tolist()}
        except Exception as e:
            return {"error": str(e)}

    def export_csv(self, directory: str = "analysis_output") -> list[str]:
        os.makedirs(directory, exist_ok=True)
        written: list[str] = []
        for name, df in self.data.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                path = os.path.join(directory, f"{self._safe_name()}_{name}.csv")
                df.to_csv(path, index=False)
                written.append(path)
        if self.live_ticks_records:
            path = os.path.join(directory, f"{self._safe_name()}_live_ticks.csv")
            pd.DataFrame(self.live_ticks_records).to_csv(path, index=False)
            written.append(path)
        if self.live_events:
            path = os.path.join(directory, f"{self._safe_name()}_live_events.csv")
            pd.DataFrame(self.live_events).to_csv(path, index=False)
            written.append(path)
        for timeframe, result in self.results.items():
            for key, df in result.items():
                if not df.empty:
                    path = os.path.join(directory, f"{self._safe_name()}_{timeframe}_{key}.csv")
                    df.to_csv(path, index=False)
                    written.append(path)
        return written

    def _safe_name(self) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_" for ch in self.instrument_name).strip("_")

    # ------------------------- flujo en vivo -------------------------
    def _consume_live_queue(self) -> None:
        while not self._live_stop_event.is_set() or not self._live_queue.empty():
            try:
                tick = self._live_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_live_tick(tick)
            except Exception:
                LOG.exception("Error procesando un tick en vivo")
            finally:
                self._live_queue.task_done()

    def _process_live_tick(self, tick: dict[str, Any]) -> None:
        price = float(tick["quote"])
        epoch = int(tick["epoch"])
        if not np.isfinite(price) or price <= 0:
            return
        receive_time = datetime.now(timezone.utc)
        self.live_tick_count += 1
        record = {
            "tick_index": self.live_tick_count,
            "epoch": epoch,
            "datetime_market_utc": pd.to_datetime(epoch, unit="s", utc=True),
            "received_at_utc": receive_time.isoformat(),
            "price": price,
            "symbol": self.symbol,
        }
        self.live_ticks_records.append(record)
        is_event = False
        move_pct = np.nan
        threshold_pct = np.nan
        if self._last_live_price is not None and self._last_live_price > 0:
            signed_return = (price / self._last_live_price - 1.0) * 100.0
            directional = max(0.0, signed_return if self.direction == "up" else -signed_return)
            move_pct = signed_return
            if len(self._directional_returns) >= 300:
                threshold_pct = float(np.quantile(np.asarray(self._directional_returns), self.config.tick_event_quantile))
                is_event = directional > 0 and directional >= threshold_pct
            self._directional_returns.append(directional)
        if is_event:
            self.live_events.append({
                **record,
                "event_type": "UPWARD_TICK_EXTREME_CANDIDATE" if self.direction == "up" else "DOWNWARD_TICK_EXTREME_CANDIDATE",
                "return_pct": move_pct,
                "directional_move_pct": directional,
                "threshold_pct_prior_only": threshold_pct,
                "ticks_before_event": self.ticks_since_event,
                "definition": f"Retorno direccional >= cuantil {self.config.tick_event_quantile} de hasta {self._directional_returns.maxlen} retornos previos",
            })
            LOG.info("CANDIDATO EXTREMO %s | precio=%s | retorno=%+.6f%% | ticks previos=%s", self.instrument_name, price, move_pct, self.ticks_since_event)
            self.ticks_since_event = 0
        else:
            self.ticks_since_event += 1
        self._last_live_price = price
        self._last_live_epoch = epoch

    def start_live_ticks(self, duration_seconds: int = 300, reconnect: bool = True) -> list[dict[str, Any]]:
        """Recibe ticks mediante callback ligero y procesa en una cola separada.

        Si la cola se llena, incrementa live_dropped_ticks y deja constancia: no declara captura completa.
        La llamada bloquea hasta finalizar la sesión o alcanzar la duración indicada.
        """
        symbol = self._ensure_symbol()
        if duration_seconds < 1:
            raise ValueError("duration_seconds debe ser >= 1.")
        self._live_stop_event.clear()
        self._live_running = True
        self.live_tick_count = 0
        self.ticks_since_event = 0
        self.live_events = []
        self.live_ticks_records = []
        self.live_dropped_ticks = 0
        self._last_live_price = None
        self._last_live_epoch = None
        self._live_queue = queue.Queue(maxsize=100_000)
        self._directional_returns.clear()
        # Inicializar referencia con retornos históricos, si existen.
        if "ticks" in self.data and len(self.data["ticks"]) >= 2:
            hist_returns = self.data["ticks"]["price"].pct_change().mul(100).dropna()
            directional = hist_returns.clip(lower=0) if self.direction == "up" else (-hist_returns).clip(lower=0)
            for value in directional.tail(self._directional_returns.maxlen):
                if np.isfinite(value):
                    self._directional_returns.append(float(value))
        self._consumer_thread = threading.Thread(target=self._consume_live_queue, daemon=True, name="tick-consumer")
        self._consumer_thread.start()
        deadline = time.monotonic() + duration_seconds
        backoff = 1.0
        LOG.info("Escucha %s (%s) durante %ss; dirección analizada=%s", self.instrument_name, symbol, duration_seconds, self.direction)

        while self._live_running and not self._live_stop_event.is_set() and time.monotonic() < deadline:
            def on_open(ws_app):
                ws_app.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": 9001}))
                LOG.info("Suscripción en vivo activa para %s", symbol)

            def on_message(ws_app, message):
                if time.monotonic() >= deadline:
                    self._live_running = False
                    ws_app.close()
                    return
                try:
                    payload = json.loads(message)
                    if payload.get("error"):
                        LOG.error("Error de suscripción Deriv: %s", payload["error"])
                        return
                    if payload.get("msg_type") != "tick":
                        return
                    tick = payload.get("tick", {})
                    if "quote" not in tick or "epoch" not in tick:
                        LOG.warning("Mensaje tick incompleto descartado: faltan quote/epoch")
                        return
                    item = {"quote": tick["quote"], "epoch": tick["epoch"]}
                    try:
                        self._live_queue.put_nowait(item)
                    except queue.Full:
                        self.live_dropped_ticks += 1
                        LOG.error("Cola de ticks llena; contador de mensajes descartados=%s", self.live_dropped_ticks)
                except (json.JSONDecodeError, TypeError, ValueError):
                    LOG.exception("No se pudo interpretar un mensaje de WebSocket")

            def on_error(ws_app, error):
                LOG.warning("WebSocket en vivo reportó error: %s", error)

            def on_close(ws_app, status_code, msg):
                LOG.warning("WebSocket cerrado (código=%s, mensaje=%s)", status_code, msg)

            self.ws_live = websocket.WebSocketApp(self.client.url, on_open=on_open, on_message=on_message,
                                                  on_error=on_error, on_close=on_close)
            try:
                self.ws_live.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                LOG.exception("Fallo en stream en vivo")
            if not reconnect or self._live_stop_event.is_set() or time.monotonic() >= deadline:
                break
            LOG.warning("Reconectando en %.1f segundos...", backoff)
            time.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
            backoff = min(backoff * 2, 15.0)

        self._live_running = False
        self._live_stop_event.set()
        if self.ws_live:
            try:
                self.ws_live.close()
            except Exception:
                pass
        if self._consumer_thread:
            self._consumer_thread.join(timeout=10)
        if self._live_queue.unfinished_tasks:
            LOG.error("Al terminar quedaron %s tareas de cola sin confirmar.", self._live_queue.unfinished_tasks)
        LOG.info("Sesión finalizada. procesados=%s; candidatos=%s; descartados_por_cola=%s", len(self.live_ticks_records), len(self.live_events), self.live_dropped_ticks)
        return self.live_events

    def stop_live_ticks(self) -> None:
        self._live_running = False
        self._live_stop_event.set()
        if self.ws_live:
            try:
                self.ws_live.close()
            except Exception:
                pass


# Ejemplo de uso en Google Colab o VS Code:
# ------------------------------------------------------------
# %pip install websocket-client pandas numpy plotly
#
# boom = SyntheticIndexAnalyzer("Boom 1000", direction="up", days=20)
# boom.fetch_historical_data()
# boom.fetch_tick_history(count_limit=5000)
# results = boom.full_analysis(["1min", "5min", "15min", "1h", "4h", "1d"])
# boom.detect_tick_extremes()
# display(results["5min"]["zones"].head(20))
# boom.plot_candles_with_zones("5min").show()
# boom.plot_return_distribution().show()
# boom.plot_volatility("5min").show()
# boom.plot_tick_heatmap().show()
# boom.plot_interevent_survival().show()
# boom.plot_autocorrelation().show()
# boom.plot_conditional_event_probability(horizon_ticks=100, bin_size=100).show()
# boom.export_csv("analysis_output")
#
# Para Crash 500 usa otra instancia independiente:
# crash = SyntheticIndexAnalyzer("Crash 500", direction="down", days=20)
# ------------------------------------------------------------
