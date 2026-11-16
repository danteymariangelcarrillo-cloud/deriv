# Data Model: Migration Foundation

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16 | **Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

Extiende el data model del spec padre (`001-crash-boom-dashboard`) con entidades operativas de Fase 1: configuración tipada, estado en RAM, snapshots inmutables, y contratos API/WebSocket.

---

## 1. Visión General

```text
InstrumentState (RAM, mutable)
    │ 1
    ├── buffers: historical_ticks, live_ticks, candles_by_tf, directional_returns (deque)
    ├── config: AnalyzerConfig, ModelConfig, GateConfig, RAMConfig
    ├── config_hash: str (SHA-256)
    ├── snapshot: InstrumentSnapshot | None (inmutable, frozen)
    ├── snapshot_version: int
    ├── stream: StreamManager (WS Deriv, asyncio.Queue, consumer)
    └── locks: recompute_lock (asyncio.Lock)

InstrumentSnapshot (frozen, inmutable) ← snapshot_version
    │ 1
    ├── schema_version: "1.0.0"
    ├── snapshot_id: UUID5(symbol|generated_at|config_hash)
    ├── generated_at: UTC
    ├── config_hash: str
    ├── instrument: Instrument
    ├── timeframes: map<Timeframe, TimeframeAnalysis>
    ├── features: array<FeatureStatus>
    ├── overall_status: ready|partial|stale|error
    └── errors: array<ErrorRecord>

TimeframeAnalysis (por temporalidad)
    │ 1
    ├── status: pending|computing|ready|insufficient|not_applicable|error|stale
    ├── quality: DataQualityReport
    ├── events: EventSummary {candle: EventSeries, tick: EventSeries}
    ├── zones: array<ReactionZone>
    ├── evt: EVTResult | null
    ├── volatility: GARCHResult | null
    ├── regimes: RegimeResult | null
    ├── survival: SurvivalResult | null
    ├── autocorrelation: ACFResult | null
    ├── diagnostics: DiagnosticsResult | null
    ├── conditional: ConditionalResult | null
    ├── walkforward: WalkForwardResult | null
    └── features: map<analysis_id, FeatureStatus>
```

---

## 2. Entidades de Configuración (Nuevas en Fase 1)

### 2.1 AnalyzerConfig (Extiende spec padre)

```python
class AnalyzerConfig(BaseModel):
    # Parámetros base (compatibles con boom_crash_quant_analyzer.py)
    candle_event_quantile: confloat(gt=0.5, lt=1.0) = 0.99
    event_lookback_bars: conint(ge=10) = 200
    min_move_pct: confloat(ge=0) = 0.0
    tick_event_quantile: confloat(gt=0.5, lt=1.0) = 0.999
    tick_event_lookback: conint(ge=300) = 2000
    zone_tolerance_atr: confloat(gt=0) = 0.5
    pivot_window: conint(ge=1) = 3
    min_zone_touches: conint(ge=1) = 3
    touch_separation_bars: conint(ge=1) = 3
    response_horizon_bars: conint(ge=1) = 10
    favourable_atr: confloat(gt=0) = 1.0
    adverse_atr: confloat(gt=0) = 0.75
    
    # Extensiones Fase 1: overrides por temporalidad
    timeframe_overrides: dict[str, dict] = Field(default_factory=dict)
    # Ejemplo: {"5min": {"event_lookback_bars": 150}, "1h": {"candle_event_quantile": 0.995}}
```

### 2.2 ModelConfig (Nuevo — parámetros lógicos de modelos)

```python
class ModelConfig(BaseModel):
    rolling_window: conint(ge=5) = 20
    garch_p: conint(ge=1, le=3) = 1
    garch_q: conint(ge=1, le=3) = 1
    evt_threshold_quantile: confloat(gt=0.5, lt=1.0) = 0.95
    wf_train_days: dict[str, int] = Field(default_factory=lambda: {
        "1min": 30, "5min": 60, "15min": 90, "1h": 180, "4h": 365, "1d": 730
    })
    wf_test_days: dict[str, int] = Field(default_factory=lambda: {
        "1min": 7, "5min": 14, "15min": 21, "1h": 30, "4h": 60, "1d": 90
    })
    wf_step_days: dict[str, int] = Field(default_factory=lambda: {
        "1min": 3, "5min": 7, "15min": 10, "1h": 14, "4h": 30, "1d": 60
    })
    regime_method: Literal["markov_switching", "cusum"] = "markov_switching"
```

### 2.3 GateConfig (Nuevo — puertas de muestra mínimas)

```python
class GateConfig(BaseModel):
    candle_events_min_bars: conint(ge=10) = 50
    tick_events_min_ticks: conint(ge=50) = 300
    zone_min_evaluated: conint(ge=1) = 10
    zone_min_count: conint(ge=1) = 5
    evt_min_excess: conint(ge=10) = 30
    garch_min_obs: conint(ge=50) = 200
    regime_min_obs: conint(ge=100) = 500
    survival_min_events: conint(ge=5) = 20
    acf_min_obs: conint(ge=20) = 100
    conditional_min_events: conint(ge=10) = 30
    wf_min_folds: conint(ge=1) = 3
    wf_min_days: dict[str, int] = Field(default_factory=lambda: {
        "1min": 30, "5min": 60, "15min": 90, "1h": 180, "4h": 365, "1d": 730
    })
```

### 2.4 RAMConfig (Nuevo — límites de memoria)

```python
class RAMConfig(BaseModel):
    max_ticks_per_symbol: conint(ge=1000) = 50_000
    max_candles_per_tf: conint(ge=1000) = 30_000
    max_directional_returns: conint(ge=1000) = 10_000
    max_live_queue: conint(ge=1000) = 100_000
    downsample_max_points: conint(ge=1000) = 5_000
    figure_cache_size: conint(ge=5) = 20
    ram_warning_mb: conint(ge=100) = 380
    ram_critical_mb: conint(ge=200) = 420
```

### 2.5 Config Hash (Reproducibilidad)

```python
def config_hash(
    analyzer: AnalyzerConfig,
    model: ModelConfig,
    gates: GateConfig,
    ram: RAMConfig,
    version: str = "1.0.0"
) -> str:
    """SHA-256 de JSON canónico (sorted keys). Cambia solo si hay diferencia semántica."""
    payload = {
        "version": version,
        "analyzer": analyzer.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
        "gates": gates.model_dump(mode="json"),
        "ram": ram.model_dump(mode="json"),
    }
    json_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(json_str.encode()).hexdigest()
```

**Invariante:** `config_hash` idéntico → misma configuración efectiva → snapshot reutilizable.

---

## 3. Entidades Operativas (RAM Only)

### 3.1 InstrumentState (Estado mutable por instrumento)

```python
@dataclass
class InstrumentState:
    symbol: str                    # "BOOM1000" | "CRASH500"
    display_name: str              # "Boom 1000" | "Crash 500"
    direction: Literal["up", "down"]
    
    # Configuración efectiva
    analyzer_config: AnalyzerConfig
    model_config: ModelConfig
    gate_config: GateConfig
    ram_config: RAMConfig
    config_hash: str
    
    # Buffers circulares (RAM only, sin disco)
    historical_ticks: Deque[TickRecord] = field(
        default_factory=lambda: deque(maxlen=50_000)
    )
    live_ticks: Deque[TickRecord] = field(
        default_factory=lambda: deque(maxlen=50_000)
    )
    candles_by_tf: Dict[str, Deque[CandleRecord]] = field(default_factory=dict)
    directional_returns: Deque[float] = field(
        default_factory=lambda: deque(maxlen=10_000)
    )
    
    # Estado calculado (inmutable, referencia compartida)
    snapshot: Optional[InstrumentSnapshot] = None
    snapshot_version: int = 0
    
    # Live stream
    ws_task: Optional[asyncio.Task] = None
    live_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=100_000)
    )
    dropped_ticks: int = 0
    last_latency_ms: float = 0.0
    connected: bool = False
    active_endpoint: str = ""
    
    # Concurrencia
    recompute_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
```

**Reglas de buffers:**
- `maxlen` evicción automática FIFO al llenar
- `dropped_ticks` incrementa solo si `live_queue.full()`
- Monitor RAM cada 30s: si RSS > `ram_warning_mb` → LRU evicción velas TF menos usadas

### 3.2 TickRecord / CandleRecord

```python
@dataclass(slots=True)
class TickRecord:
    epoch: int
    price: float
    received_at: float          # time.monotonic()
    sequence: int               # Monotónico por sesión

@dataclass(slots=True)
class CandleRecord:
    epoch: int
    open: float
    high: float
    low: float
    close: float
    timeframe: str              # "1min" | "5min" | ...
```

---

## 4. Resultados Analíticos (Extienden spec padre)

### 4.1 EVTResult (Completo para `fit_evt_gpd`)

```python
class EVTResult(BaseModel):
    status: Literal["ready", "insufficient", "error"]
    threshold: float
    threshold_quantile: float
    n_excesses: int
    xi: float | None
    sigma: float | None
    xi_se: float | None
    sigma_se: float | None
    log_likelihood: float | None
    converged: bool
    ks_stat: float | None
    ks_p: float | None
    return_levels: dict[str, float]  # "10", "50", "100", "250"
    mean_excess_plot: dict[str, list[float]]  # thresholds, mean_excess, n_excess
    message: str | None
```

**Gate:** `n_excesses < 30` → `status = "insufficient"`, `xi/sigma = null`.

### 4.2 RegimeResult (Markov-Switching + CUSUM)

```python
class RegimeResult(BaseModel):
    status: Literal["ready", "insufficient", "error"]
    method: Literal["markov_switching", "cusum"]
    n_regimes: int = 2
    n_obs: int
    high_vol_prob_current: float | None
    series: list[dict[str, float]]  # [{"epoch": int, "prob_high": float}, ...] downsampled ≤5000
    segments: list[dict[str, Any]]  # [{"start_epoch", "end_epoch", "regime", "mean_variance"}, ...]
    last_switch_epoch: int | None
    converged: bool
    message: str | None
```

**Gate:** `n_obs < 500` → `status = "insufficient"`.

### 4.3 FeatureStatus (Registro por análisis)

```python
class FeatureStatus(BaseModel):
    id: str                          # "evt.gpd", "volatility.garch", "zones.reaction", ...
    name_es: str                     # "Ajuste EVT/GPD", "GARCH(1,1)", "Zonas de reacción"
    category: str                    # "evt", "volatility", "zones", "survival", "diagnostics", "walkforward", "data"
    status: Literal["pending", "computing", "ready", "insufficient", "not_applicable", "error", "stale"]
    min_sample: int                  # Gate aplicado
    sample_size: int                 # Muestra disponible
    reason: str | None               # Explicación si insufficient/error
    ui_path: str                     # Ruta al panel: "/i/BOOM1000/distribution"
    stage: Literal["in_sample", "oos"]
```

---

## 5. Contratos API (Pydantic Models)

### 5.1 Request/Response Models

```python
# GET /api/health
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    version: str
    uptime_seconds: float
    instruments: list[InstrumentInfo]

# GET /api/instruments
class InstrumentInfo(BaseModel):
    symbol: str
    display_name: str
    direction: Literal["up", "down"]
    enabled: bool
    snapshot_version: int
    overall_status: Literal["ready", "partial", "stale", "error"]
    connected: bool
    latency_ms: float | None

# GET /api/snapshot/{symbol}
class SnapshotResponse(BaseModel):
    snapshot: InstrumentSnapshot  # O TimeframeAnalysis si ?tf=5min

# GET /api/config/{symbol}
class ConfigResponse(BaseModel):
    analyzer: AnalyzerConfig
    model: ModelConfig
    gates: GateConfig
    ram: RAMConfig
    config_hash: str
    version: str

# POST /api/config/{symbol}
class ConfigUpdateRequest(BaseModel):
    analyzer: AnalyzerConfig | None = None
    model: ModelConfig | None = None
    gates: GateConfig | None = None
    ram: RAMConfig | None = None
    # Solo campos enviados se actualizan (partial update)

class ConfigUpdateResponse(BaseModel):
    config_hash: str
    status: Literal["updated", "unchanged", "error"]
    recalculated_timeframes: list[str]
    message: str | None

# GET /api/live/{symbol}/status
class StreamState(BaseModel):
    symbol: str
    running: bool
    connected: bool
    received: int
    processed: int
    dropped_ticks: int
    queue_depth: int
    last_tick_epoch: int | None
    warmup_complete: bool
    live_events: int
    latency_ms: float | None
    active_endpoint: str | None

# GET /api/export/{symbol}
# Response: StreamingResponse (BytesIO) — no modelo Pydantic
```

### 5.2 WebSocket Messages

**Client → Server:**
```python
class WSClientMessage(BaseModel):
    type: Literal["subscribe", "unsubscribe", "set_timeframe", "ping", "get_chart"]
    channels: list[Literal["ticks", "events", "zones", "config"]] | None = None
    timeframe: Literal["1min", "5min", "15min", "1h", "4h", "1d"] | None = None
    chart_id: str | None = None
    max_bars: int | None = None
```

**Server → Client:**
```python
class WSServerMessage(BaseModel):
    type: Literal[
        "tick", "event", "zone_update", "config_updated", 
        "connection_state", "chart_data", "pong", "error"
    ]
    data: dict[str, Any]
    timestamp: float  # time.time()
```

---

## 6. Entidades de Dominio (Heredadas + Extensiones)

### 6.1 Instrument (Configuración, no datos)

```python
class Instrument(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{3,20}$")
    display_name: str
    direction: Literal["up", "down"]
    enabled: bool = True
    history_days: dict[str, int]  # 6 TFs obligatorios
    tick_history_count: int = Field(ge=2, le=5000)
    color: str | None = None
    notes: str | None = None
```

**Registro por defecto:**
| Symbol | Display | Direction | History Days (1m/5m/15m/1h/4h/1d) |
|--------|---------|-----------|-----------------------------------|
| BOOM1000 | Boom 1000 | up | 30/90/180/365/730/1825 |
| CRASH500 | Crash 500 | down | 30/90/180/365/730/1825 |

### 6.2 Candle / Tick (Datos de entrada)

```python
class Candle(BaseModel):
    symbol: str
    timeframe: Literal["1min", "5min", "15min", "1h", "4h", "1d"]
    epoch: int = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    source: Literal["deriv", "cache", "fixture"]
    quality_flags: list[str] = Field(default_factory=list)

class Tick(BaseModel):
    symbol: str
    epoch: int = Field(gt=0)
    price: float = Field(gt=0)
    received_at: datetime  # UTC
    sequence: int = Field(ge=0)
```

### 6.3 EventCandidate (Resultado analítico)

```python
class EventCandidate(BaseModel):
    event_id: str  # UUID5 determinista
    symbol: str
    timeframe: str
    kind: Literal["candle", "tick"]
    direction: Literal["up", "down"]
    event_epoch: int
    event_price: float
    magnitude_points: float = Field(ge=0)
    magnitude_pct: float = Field(ge=0)
    threshold_points: float
    threshold_basis: Literal["rolling_quantile"]
    lookback: int
    warmup_ok: bool
    reversal_pct: float | None = None
    label: Literal["candidato de spike"] = "candidato de spike"
    computed_at: datetime
```

### 6.4 ReactionZone (p_null=0.5 por defecto; geometría/FDR optativos; Bootstrap)

```python
class ReactionZone(BaseModel):
    zone_id: str  # UUID5 determinista
    symbol: str
    timeframe: str
    zone_type: Literal["support", "resistance"]
    center: float
    lower: float
    upper: float
    half_width: float = Field(gt=0)
    touch_bars: int
    episodes: int
    evaluated_touches: int
    reactions: int
    failures: int
    ambiguous_touches: int
    pending_touches: int
    reaction_rate: float | None  # null si evaluated=0
    ci_lower: float | None
    ci_upper: float | None
    p_null: float | None         # 0.5 por defecto; b/(a+b) optativo
    p_value_raw: float | None
    p_value_adj: float | None
    reject: bool
    median_favourable_atr: float | None
    favourable_ci: list[float] | None
    median_adverse_atr: float | None
    adverse_ci: list[float] | None
    event_count_in_zone: int
    last_touch_epoch: int | None
    available_from_epoch: int
    stage: Literal["in_sample", "oos"]
    status: Literal["ready", "insufficient", "error"]
    sensitivity: dict[str, float]  # rate_ambiguous_as_failure_pct, rate_pending_as_failure_pct
    reason: str | None
```

---

## 7. Ciclos de Vida

### 7.1 Snapshot (Inmutable)

```text
BUILDING → READY
    │
    ├── PARTIAL (algunas TF ready, otras insufficient)
    ├── STALE (datos > stale_seconds)
    └── ERROR (ninguna TF usable)
```

### 7.2 TimeframeAnalysis

```text
PENDING → COMPUTING → READY
                │
                ├── INSUFFICIENT (gate no cumplido)
                ├── NOT_APPLICABLE (ej. walkforward en 1min)
                └── ERROR (excepción)
READY → STALE → COMPUTING (recompute)
```

### 7.3 Stream

```text
STOPPED → CONNECTING → SUBSCRIBED → RUNNING
                │              │
                └── ERROR ←────┴── RECONNECTING → SUBSCRIBED
```

---

## 8. Disposición de Caché (RAM Only)

```text
NO EXISTE data/cache/ en disco

En memoria (InstrumentState):
├── historical_ticks: deque[TickRecord] maxlen=50_000
├── live_ticks: deque[TickRecord] maxlen=50_000
├── candles_by_tf: dict[str, deque[CandleRecord]] maxlen=30_000 c/u
├── directional_returns: deque[float] maxlen=10_000
├── live_queue: asyncio.Queue maxsize=100_000
├── snapshot: InstrumentSnapshot (referencia inmutable)
└── figure_cache: LRU dict[str, go.Figure] maxsize=20
```

**Política de evicción (LRU global):**
1. Monitor cada 30s: `psutil.Process().memory_info().rss`
2. Si > `ram_warning_mb` (380 MB): evict velas TF menos accedido recientemente
3. Si > `ram_critical_mb` (420 MB): evict agresivo + log warning + reducir `figure_cache_size`

---

## 9. Reglas de Validación

### 9.1 Config Validation (Pydantic)
- Todos los campos `confloat`, `conint` con rangos estrictos
- `config_hash` recalculado en cada `POST /api/config/{symbol}`
- Partial update: solo campos enviados se validan y actualizan

### 9.2 Business Rules (Python)

| Regla | Dónde |
|-------|-------|
| `evaluated = reactions + failures` | `analytics/zones.py` |
| `p_null = 0.5` por defecto; `b/(a+b)` optativo | `analytics/zones.py` |
| BH solo walk-forward por defecto; in-sample optativo | `analytics/stats.py` |
| Gate por análisis antes de computar | `services/snapshot_service.py` |
| `n_excess < 30` → EVT insufficient | `analytics/evt.py` |
| `n_obs < 500` → Regimes insufficient | `analytics/volatility.py` |
| Walk-forward sin fuga temporal | `analytics/walkforward.py` |
| Snapshot frozen tras build | `domain/snapshot.py` |
| RAM ≤ límite configurado | `data/cache.py` monitor |
| Cero archivos en disco | Test contrato + inspección |

### 9.3 Serialización
- `NaN`/`Inf` → `null` + `reason` en campo padre
- `datetime` → ISO 8601 UTC con `Z`
- `float` sin redondear en núcleo; UI formatea
- `UUID5` determinista para IDs (reproducibilidad)

---

## 10. Taxonomía de Errores (Extendida)

| Código | Severidad | Retryable | Origen | Mensaje ES |
|--------|-----------|-----------|--------|------------|
| `CONFIG_INVALID` | critical | No | config | "Configuración inválida: {detalle}" |
| `CONFIG_HASH_UNCHANGED` | info | No | config | "Sin cambios semánticos en configuración" |
| `DERIV_TRANSPORT` | error | Sí | deriv | "No se pudo conectar con Deriv; reintentando" |
| `DERIV_TIMEOUT` | error | Sí | deriv | "Deriv no respondió a tiempo" |
| `DERIV_RATE_LIMIT` | warning | Sí | deriv | "Límite de Deriv alcanzado; esperando" |
| `DERIV_API_ERROR` | error | No | deriv | "Deriv rechazó la solicitud: {code}" |
| `SYMBOL_NOT_ACTIVE` | error | No | deriv | "El símbolo no está activo en tu jurisdicción" |
| `DATA_EMPTY` | warning | No | data | "No se recibieron velas para esta temporalidad" |
| `DATA_STALE` | warning | No | data | "Los datos están desactualizados (> {s}s)" |
| `CACHE_EVICTION` | info | No | cache | "Se liberó espacio de caché (LRU)" |
| `COMPUTE_INSUFFICIENT` | info | No | analytics | "Datos insuficientes para {análisis}: {min} req, {actual} disp" |
| `COMPUTE_FAILED` | error | No | analytics | "El análisis {id} falló: {detalle}" |
| `SNAPSHOT_STALE` | warning | No | services | "El snapshot está desactualizado" |
| `STREAM_DROPPED` | warning | No | live | "Se descartaron {n} ticks por saturación de cola" |
| `EXPORT_FAILED` | error | No | services | "No se pudo generar el archivo de exportación" |
| `RATE_LIMIT_EXCEEDED` | warning | Sí | rate_limiter | "Rate limit propio excedido; esperando token" |
| `RAM_WARNING` | warning | No | cache | "RAM > {mb} MB; iniciando evicción LRU" |
| `RAM_CRITICAL` | error | No | cache | "RAM > {mb} MB; evicción agresiva activa" |

---

## 11. Resumen de Invariantes Críticas (Fase 1)

| ID | Invariante | Test |
|----|------------|------|
| INV-M01 | `config_hash` cambia solo si hay diferencia semántica | `test_config_hash.py` |
| INV-M02 | Buffers `deque` nunca superan `maxlen` | `test_ram_cache.py` |
| INV-M03 | Snapshot `frozen=True` nunca muta tras build | `test_snapshot_immutable.py` |
| INV-M04 | Atomic swap: lectura concurrente nunca ve estado parcial | `test_snapshot_store.py` |
| INV-M05 | `dropped_ticks` incrementa solo si `live_queue.full()` | `test_live_stream.py` |
| INV-M06 | Rate limiter ≤ 150 req/min (token bucket) | `test_rate_limit.py` |
| INV-M07 | `fit_evt_gpd` retorna `insufficient` si `n_excess < 30` | `test_evt.py` |
| INV-M08 | `detect_volatility_regimes` retorna `insufficient` si `n_obs < 500` | `test_volatility.py` |
| INV-M09 | Cero archivos en `data/cache/` (o directorio no existe) | `test_zero_disk.py` |
| INV-M10 | Paridad numérica 1e-10 vs `boom_crash_quant_analyzer.py` | `test_*_parity.py` |

---

## 12. Esquema JSON Normativo

Ver `contracts/snapshot.schema.json` (extiende parent spec) y `contracts/api.schema.json` (nuevo para Fase 1).