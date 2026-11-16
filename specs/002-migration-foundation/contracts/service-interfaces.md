# Service Interfaces — Migration Foundation

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16

Contratos de servicio (interfaces) para la Fase 1. Cada interfaz define métodos, tipos de entrada/salida, y comportamientos esperados.

---

## 1. DerivClient (`data/deriv_client.py`)

```python
class DerivClient:
    """
    Cliente asíncrono para API pública de Deriv (solo lectura).
    Mantiene UNA conexión WebSocket persistente por símbolo.
    Rate limiting: token bucket 150/min (capacity=30, refill=2.5/s).
    Fallback automático a endpoint legacy en errores de transporte.
    Reconexión exponencial + jitter (1s, 2s, 4s, 8s, max 15s).
    """

    def __init__(
        self,
        endpoints: list[str] | None = None,
        rate_limit_per_min: int = 150,
        timeout: float = 20.0,
        retries_per_endpoint: int = 1,
    ) -> None: ...

    async def request(
        self,
        payload: dict[str, Any],
        expected_types: tuple[str, ...],
        expected_key: str,
    ) -> dict[str, Any]:
        """
        Ejecuta request HTTP-style sobre WebSocket con req_id.
        Reintenta solo en errores de transporte (timeout, 520, 502, 503, 504, connection reset).
        Lanza DerivAPIError en errores de negocio (símbolo inválido, parámetros malos).
        """
        ...

    async def subscribe_ticks(self, symbol: str) -> AsyncIterator[dict[str, Any]]:
        """
        Generador asíncrono que yield ticks en tiempo real.
        Maneja reconexión internamente; yield None en reconexión.
        """
        ...

    async def get_active_symbols(self) -> list[dict[str, Any]]: ...

    def resolve_symbol(self, target_name: str, override: str | None = None) -> tuple[str, str]: ...

    @property
    def active_endpoint(self) -> str: ...

    @property
    def connection_state(self) -> ConnectionState: ...

    async def close(self) -> None: ...
```

### ConnectionState

```python
@dataclass
class ConnectionState:
    active_endpoint: str
    connected: bool
    last_message_at: datetime | None
    reconnect_count: int
    last_error: ErrorRecord | None
    subscriptions: list[str]  # ["ticks:BOOM1000", "ticks:CRASH500"]
    latency_ms_p50: float | None  # Últimas 100 peticiones
```

---

## 2. RateLimiter (`data/rate_limiter.py`)

```python
class TokenBucket:
    """
    Token bucket thread-safe para asyncio.
    capacity=30, refill_per_second=2.5 → 150/min sostenido, burst 30.
    """

    def __init__(self, capacity: int, refill_per_second: float) -> None: ...

    async def acquire(self, tokens: int = 1) -> None:
        """Bloquea hasta tener tokens disponibles."""
        ...

    def try_acquire(self, tokens: int = 1) -> bool:
        """No bloquea; retorna True si hay tokens."""
        ...

    @property
    def available_tokens(self) -> float: ...

    @property
    def state(self) -> RateLimitState: ...


@dataclass
class RateLimitState:
    tokens: float
    capacity: int
    refill_per_second: float
    calls_last_minute: int
    waited_ms_last_request: int
    budget_per_minute: int  # 150
```

---

## 3. MarketDataService (`data/market_data_service.py`)

```python
class MarketDataService:
    """
    Servicio de datos de mercado: velas paginadas, normalización OHLC, calidad.
    SIN persistencia en disco — solo retorna DataFrames y reportes.
    """

    def __init__(
        self,
        deriv_client: DerivClient,
        cache: RAMCache,
        rate_limiter: TokenBucket,
    ) -> None: ...

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start_epoch: int,
        end_epoch: int,
        limit: int = 1000,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        """
        Descarga velas con paginación (máx 1000 por request).
        Retorna DataFrame normalizado + reporte de calidad.
        No guarda en disco.
        """
        ...

    async def get_tick_history(
        self,
        symbol: str,
        count: int = 5000,
        start_epoch: int | None = None,
    ) -> pd.DataFrame:
        """
        Descarga historial de ticks (máx 5000).
        Retorna DataFrame con columnas: epoch, price, datetime, sequence.
        """
        ...

    async def warm_cache(
        self,
        symbol: str,
        timeframes: list[str],
        force: bool = False,
    ) -> dict[str, int]:
        """
        Precarga caché para símbolo y temporalidades.
        Retorna dict {timeframe: n_velas_guardadas}.
        Si force=True, ignora caché existente y re-descarga.
        """
        ...
```

---

## 4. RAMCache (`data/cache.py`)

```python
class RAMCache:
    """
    Caché exclusiva en RAM con buffers circulares (deque) y evicción LRU global.
    CERO DISCO — no crea directorios ni archivos.
    """

    def __init__(self, ram_config: RAMConfig) -> None: ...

    # Buffers por símbolo
    def append_ticks(self, symbol: str, ticks: list[TickRecord]) -> int:
        """Añade ticks a historical_ticks; retorna n_descartados_por_maxlen."""
        ...

    def append_live_tick(self, symbol: str, tick: TickRecord) -> bool:
        """Añade tick a live_ticks; retorna True si se descartó por maxlen."""
        ...

    def append_candles(self, symbol: str, timeframe: str, candles: list[CandleRecord]) -> int:
        """Añade velas a candles_by_tf[timeframe]; retorna n_descartados."""
        ...

    def get_ticks(self, symbol: str, max_n: int | None = None) -> list[TickRecord]: ...
    def get_live_ticks(self, symbol: str, max_n: int | None = None) -> list[TickRecord]: ...
    def get_candles(self, symbol: str, timeframe: str, max_n: int | None = None) -> list[CandleRecord]: ...

    # Live queue
    def enqueue_live(self, symbol: str, tick: TickRecord) -> bool:
        """Encola tick en live_queue; retorna True si queue llena (dropped)."""
        ...

    def dequeue_live(self, symbol: str) -> TickRecord | None: ...

    # Monitor y evicción
    def check_ram_and_evict(self, process_rss_mb: float) -> int:
        """
        Si RSS > ram_warning_mb: evicta velas TF menos usado (LRU).
        Si RSS > ram_critical_mb: evicción agresiva + reduce figure_cache.
        Retorna MB liberados.
        """
        ...

    def get_stats(self, symbol: str) -> CacheStats: ...


@dataclass
class CacheStats:
    symbol: str
    historical_ticks: int
    live_ticks: int
    candles_by_tf: dict[str, int]
    directional_returns: int
    live_queue_depth: int
    dropped_ticks: int
    ram_usage_mb: float
```

---

## 5. SnapshotService (`services/snapshot_service.py`)

```python
class SnapshotService:
    """
    Orquestador principal: config → gates → analytics → snapshot inmutable → atomic swap.
    """

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        feature_registry: FeatureRegistry,
        market_data_service: MarketDataService,
        analytics_engine: AnalyticsEngine,
        cache: RAMCache,
    ) -> None: ...

    async def build_snapshot(
        self,
        symbol: str,
        timeframes: list[str] | None = None,
    ) -> InstrumentSnapshot:
        """
        Construye snapshot completo para símbolo.
        1. Valida config_hash vs store
        2. Para cada TF: verifica gates → computa analytics → FeatureStatus
        3. Construye InstrumentSnapshot (frozen)
        4. Atomic swap en SnapshotStore
        """
        ...

    async def recompute_timeframe(
        self,
        symbol: str,
        timeframe: str,
    ) -> TimeframeAnalysis:
        """
        Recalcula una temporalidad específica (tras cambio de config).
        Usa recompute_lock del InstrumentState.
        """
        ...

    async def update_config(
        self,
        symbol: str,
        config_update: ConfigUpdateRequest,
    ) -> ConfigUpdateResponse:
        """
        Actualiza configuración (partial update), valida, recalcula background.
        1. Merge config actual + update → valida Pydantic
        2. Calcula nuevo config_hash
        3. Si hash unchanged: retorna 304 Not Modified
        4. Actualiza InstrumentState.config + config_hash
        5. Invalida snapshot (snapshot_version += 1)
        6. Lanza recompute background para TFs afectados (ThreadPoolExecutor)
        7. Retorna inmediatamente con recalculated_timeframes
        8. Push WebSocket config_updated al terminar cada TF
        """
        ...

    async def reset_config(self, symbol: str) -> ConfigUpdateResponse:
        """Restaura defaults de AnalyzerConfig, ModelConfig, GateConfig, RAMConfig."""
        ...
```

---

## 6. SnapshotStore (`services/snapshot_store.py`)

```python
class SnapshotStore:
    """
    Almacén de snapshots en memoria con swap atómico y versionado.
    CERO DISCO — no persiste nada.
    """

    def __init__(self) -> None: ...

    def get(self, symbol: str) -> InstrumentSnapshot | None:
        """Lectura atómica — nunca ve estado parcial."""
        ...

    def swap(self, symbol: str, snapshot: InstrumentSnapshot) -> int:
        """
        Reemplaza snapshot atómicamente.
        Retorna nuevo snapshot_version.
        """
        ...

    def get_version(self, symbol: str) -> int: ...

    def clear(self, symbol: str) -> None: ...
```

---

## 7. StreamManager (`live/stream_manager.py`)

```python
class StreamManager:
    """
    Gestor de stream en vivo por símbolo.
    WebSocket Deriv → asyncio.Queue → consumer task → _process_live_tick (algoritmo exacto original).
    """

    def __init__(
        self,
        deriv_client: DerivClient,
        instrument_state: InstrumentState,
        cache: RAMCache,
    ) -> None: ...

    async def start(self, symbol: str) -> None:
        """
        Inicia stream para símbolo.
        1. Limpia buffers live + live_queue
        2. Warm-up directional_returns con histórico (si existe)
        3. Lanza consumer task (_consumer_loop)
        4. Lanza WS task (_ws_loop con reconexión)
        """
        ...

    async def stop(self, symbol: str) -> None:
        """Cancela tasks, cierra WS, drena queue."""
        ...

    async def get_status(self, symbol: str) -> StreamState: ...

    # Internal: algoritmo exacto de boom_crash_quant_analyzer.py
    async def _consumer_loop(self, symbol: str) -> None: ...
    async def _process_live_tick(self, symbol: str, tick: dict[str, Any]) -> None: ...
    async def _ws_loop(self, symbol: str) -> None: ...
```

### StreamState

```python
@dataclass
class StreamState:
    symbol: str
    running: bool
    connected: bool
    received: int
    processed: int
    dropped_ticks: int
    queue_depth: int
    last_tick_epoch: int | None
    warmup_complete: bool  # directional_returns >= 300
    live_events: int
    latency_ms: float | None
    active_endpoint: str | None
```

---

## 8. AnalyticsEngine (`analytics/__init__.py`)

```python
class AnalyticsEngine:
    """
    Motor analítico puro (sin estado, sin I/O).
    Todas las funciones son determinísticas dado mismo input + config.
    """

    def __init__(self, gate_config: GateConfig) -> None: ...

    # Stats
    def wilson_score_interval(self, successes: int, trials: int, alpha: float = 0.05) -> tuple[float, float]: ...
    def binomial_test_pvalue(self, successes: int, trials: int, p_null: float, alternative: str = "greater") -> float: ...
    def benjamini_hochberg_fdr(self, pvalues: np.ndarray, alpha: float = 0.05) -> tuple[np.ndarray, np.ndarray]: ...
    def bootstrap_ci(self, data: np.ndarray, statistic_func: Callable, n_bootstrap: int = 1000, alpha: float = 0.05, seed: int | None = None) -> tuple[float, float]: ...

    # EVT
    def fit_gpd_mle(self, excesses: np.ndarray) -> tuple[float, float, dict]: ...
    def gpd_return_level(self, xi: float, sigma: float, threshold: float, n_obs: int, n_excesses: int, return_period: int) -> float: ...
    def fit_evt_gpd(
        self,
        returns: np.ndarray,
        threshold_quantile: float = 0.95,
        direction: str = "directional",
        min_excesses: int = 30,
    ) -> dict: ...

    # Events
    def detect_candle_extremes(self, df: pd.DataFrame, config: AnalyzerConfig, direction: str) -> pd.DataFrame: ...
    def detect_tick_extremes(self, ticks: pd.DataFrame, config: AnalyzerConfig, direction: str) -> pd.DataFrame: ...
    def conditional_event_table(self, ticks: pd.DataFrame, events: pd.DataFrame, horizon: int, bin_size: int, max_ticks: int) -> pd.DataFrame: ...

    # Zones
    def find_pivots(self, df: pd.DataFrame, side: str, window: int) -> pd.DataFrame: ...
    def cluster_levels(self, pivots: pd.DataFrame, tolerance: float) -> list[list[dict]]: ...
    def evaluate_zone(
        self,
        df: pd.DataFrame,
        centre: float,
        half_width: float,
        zone_type: str,
        available_from_idx: int,
        event_prices: np.ndarray | None,
        config: AnalyzerConfig,
    ) -> dict: ...
    def detect_reaction_zones(self, df: pd.DataFrame, config: AnalyzerConfig, side: str, event_prices: np.ndarray | None) -> pd.DataFrame: ...

    # Volatility
    def fit_garch(self, returns: np.ndarray, p: int, q: int) -> dict: ...
    def detect_volatility_regimes(
        self,
        returns: np.ndarray,
        n_regimes: int = 2,
        method: str = "markov_switching",
    ) -> dict: ...

    # Survival
    def kaplan_meier(self, durations: np.ndarray, censored: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...

    # Diagnostics
    def residual_diagnostics(self, returns: np.ndarray, max_lag: int = 20) -> dict: ...

    # Walk-forward
    def walk_forward_validation(self, df: pd.DataFrame, config: AnalyzerConfig, timeframe: str, train_days: int, test_days: int, step_days: int) -> pd.DataFrame: ...
    def aggregate_walk_forward_results(self, wf_df: pd.DataFrame) -> dict: ...
```

---

## 9. ExportService (`services/export_service.py`)

```python
class ExportService:
    """
    Exportación en memoria (BytesIO) → StreamingResponse.
    CERO DISCO — no escribe archivos temporales.
    """

    def __init__(self, snapshot_store: SnapshotStore) -> None: ...

    def export_json(self, symbol: str) -> bytes:
        """Snapshot completo → JSON bytes (UTF-8)."""
        ...

    def export_csv_bytes(self, symbol: str) -> bytes:
        """Snapshot → CSV bytes (UTF-8 BOM, separador ; para Excel ES)."""
        ...

    def export_zip(self, symbol: str) -> bytes:
        """Snapshot → ZIP bytes conteniendo JSON + CSVs por TF."""
        ...

    def get_media_type(self, format: str) -> str:
        return {"json": "application/json", "csv": "text/csv", "zip": "application/zip"}[format]

    def get_filename(self, symbol: str, format: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"{symbol}_{timestamp}.{format}"
```

---

## 10. FastAPI Routes (`dashboard/routes.py`)

```python
# REST Endpoints
@router.get("/api/health", response_model=HealthResponse)
async def health_check(): ...

@router.get("/api/instruments", response_model=list[InstrumentInfo])
async def list_instruments(): ...

@router.get("/api/snapshot/{symbol}", response_model=InstrumentSnapshot)
async def get_snapshot(symbol: str, tf: str | None = None): ...

@router.get("/api/config/{symbol}", response_model=ConfigResponse)
async def get_config(symbol: str): ...

@router.post("/api/config/{symbol}", response_model=ConfigUpdateResponse)
async def update_config(symbol: str, request: ConfigUpdateRequest): ...

@router.post("/api/config/{symbol}/reset", response_model=ConfigUpdateResponse)
async def reset_config(symbol: str): ...

@router.get("/api/live/{symbol}/status", response_model=StreamState)
async def get_stream_status(symbol: str): ...

@router.get("/api/export/{symbol}")
async def export_snapshot(symbol: str, format: str = "json"): ...

# WebSocket
@router.websocket("/api/ws/{symbol}")
async def websocket_endpoint(websocket: WebSocket, symbol: str): ...
```

---

## 11. WebSocket Protocol

### Client → Server

```json
{ "type": "subscribe", "channels": ["ticks", "events", "zones", "config"] }
{ "type": "unsubscribe", "channels": ["ticks"] }
{ "type": "set_timeframe", "timeframe": "5min" }
{ "type": "get_chart", "chart_id": "candles_zones", "max_bars": 500 }
{ "type": "ping" }
```

### Server → Client

```json
{ "type": "tick", "data": {"epoch": 1700000000, "price": 1234.56, "is_event": false, "seq": 1}, "timestamp": 1700000000.123 }
{ "type": "event", "data": {"epoch": 1700000000, "price": 1234.56, "magnitude_pct": 0.12, "kind": "tick"}, "timestamp": 1700000000.123 }
{ "type": "zone_update", "data": [{"zone_id": "uuid...", "center": 1234.5, "reaction_rate_pct": 65.2, ...}], "timestamp": 1700000000.123 }
{ "type": "config_updated", "data": {"config_hash": "sha256:...", "recalculated": ["5min", "1h"]}, "timestamp": 1700000000.123 }
{ "type": "connection_state", "data": {"connected": true, "latency_ms": 42, "endpoint": "wss://..."}, "timestamp": 1700000000.123 }
{ "type": "chart_data", "data": {"chart_id": "candles_zones", "figure_json": {...}}, "timestamp": 1700000000.123 }
{ "type": "pong", "data": {"ts": 1700000000.123}, "timestamp": 1700000000.123 }
{ "type": "error", "data": {"code": "INSUFFICIENT_DATA", "message": "Datos insuficientes para EVT"}, "timestamp": 1700000000.123 }
```