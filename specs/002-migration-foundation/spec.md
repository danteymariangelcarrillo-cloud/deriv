# Feature Specification: Migration Foundation — Crash/Boom Quant Lab Dashboard

**Feature Branch**: `002-migration-foundation`  
**Created**: 2026-11-16  
**Status**: Approved  
**Parent Spec**: `001-crash-boom-dashboard` (specs/001-crash-boom-dashboard/spec.md)  
**Input**: VISION.md (análisis completo, decisiones autorizadas, arquitectura objetivo)

---

## Contexto y Alcance

Esta especificación define la **Fase 1 (Fundación)** de la migración del prototipo `boom_crash_quant_analyzer.py` v2.2.0 a la arquitectura objetivo FastAPI + WebSockets + Plotly.js documentada en VISION.md.

**Decisiones ya autorizadas (VISION.md §13):**
1. `fit_evt_gpd`: implementación **completa** (MLE + KS + Mean Excess + Return Levels)
2. `detect_volatility_regimes`: **Markov-Switching + CUSUM fallback** (statsmodels ya es dependencia)
3. Duplicados: **eliminar v1 simples**, mantener v2 canónicas
4. Frontend: **Vanilla JS + Plotly.js local** (servido por FastAPI; sin CDN)
5. Auth: **Single-user local** (sin login, según spec padre)
6. Persistencia: **Solo RAM + Export manual** (CERO DISCO — requisito hard)
7. Rate limit Deriv: **150/min** (token bucket con margen 30%)
8. Downsampling: **LTTB** (preserva forma visual, ~2 ms overhead)

**Fuera de alcance de esta fase:**
- Frontend (Fase 4)
- Gráficos Plotly.js (Fase 3)
- Páginas Metodología/Sistema (Fase 5)
- Convergencia final (Fase 6)

---

## User Scenarios & Testing

### US1 — Arranque y health check (Priority: P1)
Como desarrollador, ejecuto `uv run python -m quantlab` y la API responde en `http://127.0.0.1:8050/api/health` con `{status: "ok", version, uptime}` en < 3 segundos.

**Acceptance:**
- Comando único de arranque (run.ps1 o python -m)
- Health check responde sin depender de Deriv
- Logs estructurados en consola (no archivo)

### US2 — Configuración por instrumento en caliente (Priority: P1)
Como analista, hago `POST /api/config/BOOM1000` con nuevo `candle_event_quantile: 0.995` y el sistema:
1. Valida Pydantic (rangos, tipos, dependencias)
2. Calcula nuevo `config_hash` (SHA-256)
3. Si cambió: invalida snapshot, recalcula TODAS las temporalidades en background (ThreadPoolExecutor max_workers=2)
4. Push WebSocket `config_updated` con `recalculated: ["5min", "1h", ...]`
5. Frontend (fase futura) refresca solo paneles visibles

**Acceptance:**
- Validación estricta: rechaza valores fuera de rango (ej. `quantile > 1`)
- `config_hash` cambia **solo** si hay cambio semántico (no por timestamp)
- Recalculo usa `asyncio.to_thread` para no bloquear event loop
- Lock por instrumento evita recálculos concurrentes

### US3 — Cliente Deriv async persistente (Priority: P1)
Como sistema, mantengo **una conexión WebSocket persistente por instrumento** a Deriv con:
- Rate limiter token bucket 150/min (capacity=30, refill=2.5/s)
- Fallback automático a endpoint legacy si handshake falla (520, 502, 503, 504, timeout)
- Reconexión exponencial (1s, 2s, 4s, 8s, max 15s) + jitter
- Heartbeat ping/pong cada 20s
- Cola `asyncio.Queue(maxsize=100_000)` para ticks entrantes
- Contador `dropped_ticks` visible en `/api/live/{symbol}/status`

**Acceptance:**
- Spike técnico `scripts/spike_deriv.py` conecta y recibe ticks en < 5s
- Si Deriv cae 30s y vuelve, reconexión automática sin intervención
- Rate limiter nunca supera 150 req/min (test de carga)
- `dropped_ticks` incrementa solo si queue llena (backpressure)

### US4 — Buffers circulares en RAM (Priority: P1)
Como sistema, mantengo en memoria (sin disco):
- `historical_ticks`: `deque[TickRecord, maxlen=50_000]` por instrumento
- `live_ticks`: `deque[TickRecord, maxlen=50_000]` por instrumento
- `candles_by_tf[tf]`: `deque[CandleRecord, maxlen=30_000]` por TF
- `directional_returns`: `deque[float, maxlen=10_000]` para cuantil rolling
- Monitor RAM cada 30s: si RSS > 380 MB → evicción LRU de velas TF menos usadas

**Acceptance:**
- `psutil.Process().memory_info().rss` < 400 MB en operación normal (2 índices, 6 TF, live)
- Al llenar `deque`, descarta más antiguo (FIFO) — **sin pérdida silenciosa**
- `dropped_ticks` visible en API y logs
- Sin archivos en `data/cache/` (directorio no existe o vacío)

### US5 — Snapshot inmutable + swap atómico (Priority: P1)
Como sistema, genero `InstrumentSnapshot` inmutable (Pydantic, `frozen=True`) y lo publico con **swap atómico**:
```python
async with snapshot_lock:
    new_snapshot = build_snapshot(...)
    instrument_state.snapshot = new_snapshot
    instrument_state.snapshot_version += 1
```
Frontend (fase futura) lee `snapshot_version` para detectar cambios.

**Acceptance:**
- Snapshot nunca muta tras publicarse (`frozen=True`)
- Lectura concurrente nunca ve estado parcial (atomic swap)
- `config_hash` en snapshot permite detectar config stale
- `overall_status`: `ready` | `partial` | `stale` | `error`

---

## Requirements

### Functional Requirements

| ID | Requisito | Descripción |
|----|-----------|-------------|
| **FR-M1** | Config Pydantic | `AnalyzerConfig`, `ModelConfig`, `GateConfig`, `RAMConfig` en `config.py` con validación estricta |
| **FR-M2** | Config hash | SHA-256 de JSON canónico (sorted keys) — cambia solo si hay diferencia semántica |
| **FR-M3** | DerivClient async | Cliente WebSocket persistente, rate limiter, fallback endpoints, reconexión exponencial |
| **FR-M4** | MarketDataService | Descarga velas paginadas, normalización OHLC, calidad, **sin persistencia** |
| **FR-M5** | RAM Cache | `deque` buffers con `maxlen`, LRU evicción global, monitor 30s |
| **FR-M6** | SnapshotService | Orquestador: gates → analytics → snapshot inmutable → atomic swap |
| **FR-M7** | Live StreamManager | WebSocket Deriv → asyncio.Queue → consumer thread → `_process_live_tick` (algoritmo exacto original) |
| **FR-M8** | FastAPI App | REST endpoints + WebSocket `/api/ws/{symbol}` + health check |
| **FR-M9** | fit_evt_gpd | Implementación completa (MLE + KS + Mean Excess + Return Levels) en `analytics/evt.py` |
| **FR-M10** | detect_volatility_regimes | Markov-Switching (statsmodels) + CUSUM fallback en `analytics/volatility.py` |
| **FR-M11** | Consolidar duplicados | No portar v1; mantener v2 canónicas en el código nuevo (`legacy/` intacto) |
| **FR-M12** | No portar `export_csv` | Sustituir por `ExportService` que genera `BytesIO` en memoria para `StreamingResponse` |

### Non-Functional Requirements

| ID | Requisito | Target |
|----|-----------|--------|
| **NFR-M1** | Arranque | < 3 segundos (sin Deriv), < 30s (con Deriv warm) |
| **NFR-M2** | RAM operación normal | < 400 MB (pico < 450 MB) |
| **NFR-M3** | Rate limit Deriv | ≤ 150 req/min (token bucket propio) |
| **NFR-M4** | Cero disco | `data/cache/` no creado o vacío; no CSV/BD/archivos temporales |
| **NFR-M5** | Recalculo config | < 500 ms para temporalidad activa (background para resto) |
| **NFR-M6** | Latencia tick→queue | < 5 ms (deserialización + enqueue) |
| **NFR-M7** | Reconexión Deriv | Automática, backoff exponencial + jitter, max 15s |
| **NFR-M8** | Tests paridad numérica | 100% funciones migradas: tolerancia 1e-10 vs original |

---

## Key Entities (nuevas o extendidas vs spec padre)

### Config Models (`config.py`)
```python
class AnalyzerConfig(BaseModel):
    # Parámetros originales + extensiones por TF
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
    # Extensiones por temporalidad
    timeframe_overrides: dict[str, dict] = Field(default_factory=dict)

class ModelConfig(BaseModel):
    rolling_window: conint(ge=5) = 20
    garch_p: conint(ge=1, le=3) = 1
    garch_q: conint(ge=1, le=3) = 1
    evt_threshold_quantile: confloat(gt=0.5, lt=1.0) = 0.95
    wf_train_days: dict[str, int] = Field(default_factory=lambda: {"5min": 60, "1h": 180})
    wf_test_days: dict[str, int] = Field(default_factory=lambda: {"5min": 14, "1h": 30})
    wf_step_days: dict[str, int] = Field(default_factory=lambda: {"5min": 7, "1h": 14})
    regime_method: Literal["markov_switching", "cusum"] = "markov_switching"

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
    wf_min_days: dict[str, int] = Field(default_factory=lambda: {"5min": 60, "1h": 180})

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

### InstrumentState (`state.py`)
```python
@dataclass
class InstrumentState:
    symbol: str
    display_name: str
    direction: Literal["up", "down"]
    config: AnalyzerConfig
    model_config: ModelConfig
    gate_config: GateConfig
    ram_config: RAMConfig
    config_hash: str
    
    # Buffers RAM (deque con maxlen)
    historical_ticks: Deque[TickRecord] = field(default_factory=lambda: deque(maxlen=50_000))
    live_ticks: Deque[TickRecord] = field(default_factory=lambda: deque(maxlen=50_000))
    candles_by_tf: Dict[str, Deque[CandleRecord]] = field(default_factory=dict)
    directional_returns: Deque[float] = field(default_factory=lambda: deque(maxlen=10_000))
    
    # Estado calculado
    snapshot: Optional[InstrumentSnapshot] = None
    snapshot_version: int = 0
    
    # Live stream
    ws_task: Optional[asyncio.Task] = None
    live_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100_000))
    dropped_ticks: int = 0
    last_latency_ms: float = 0.0
    connected: bool = False
    active_endpoint: str = ""
    
    # Concurrencia
    recompute_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
```

---

## API Contracts (Fase 1)

### REST Endpoints

| Método | Ruta | Request | Response | Descripción |
|--------|------|---------|----------|-------------|
| GET | `/api/health` | — | `HealthResponse` | Health check |
| GET | `/api/instruments` | — | `list[InstrumentInfo]` | Lista instrumentos registrados |
| GET | `/api/snapshot/{symbol}` | `?tf=5min` opcional | `InstrumentSnapshot` \| `TimeframeAnalysis` | Snapshot completo o filtrado |
| GET | `/api/config/{symbol}` | — | `ConfigResponse` | Config efectiva + hash |
| POST | `/api/config/{symbol}` | `ConfigUpdateRequest` | `ConfigUpdateResponse` | Actualiza config, recalcula, push WS |
| POST | `/api/config/{symbol}/reset` | — | `ConfigUpdateResponse` | Restaura defaults, recalcula |
| GET | `/api/live/{symbol}/status` | — | `StreamState` | Estado stream en vivo |
| GET | `/api/export/{symbol}` | `?format=json\|csv\|zip` | `StreamingResponse` | Export en memoria (bytes) |

### WebSocket `/api/ws/{symbol}`

**Client → Server:**
```json
{ "type": "subscribe", "channels": ["ticks", "events", "zones", "config"] }
{ "type": "set_timeframe", "timeframe": "5min" }
{ "type": "ping" }
```

**Server → Client (push):**
```json
{ "type": "tick", "data": {"epoch": 123, "price": 1234.56, "is_event": false, "seq": 1} }
{ "type": "event", "data": {"epoch": 123, "price": 1234.56, "magnitude_pct": 0.12, "kind": "tick"} }
{ "type": "zone_update", "data": [{"zone_id": "...", "center": 1234.5, "reaction_rate_pct": 65.2, ...}] }
{ "type": "config_updated", "config_hash": "sha256...", "recalculated": ["5min", "1h"] }
{ "type": "connection_state", "connected": true, "latency_ms": 42, "endpoint": "wss://..." }
{ "type": "pong", "ts": 1234567890.123 }
```

### Schemas (Pydantic models para validación)

Ver `contracts/` para JSON schemas normativos.

---

## Migration Matrix (Fase 1 — Fundación)

| Componente Original | Acción | Destino | Estado |
|---------------------|--------|---------|--------|
| `wilson_score_interval` | Migrar + tests paridad | `analytics/stats.py` | ✅ Pendiente |
| `binomial_test_pvalue` | Migrar + tests paridad | `analytics/stats.py` | ✅ Pendiente |
| `benjamini_hochberg_fdr` | Migrar + tests paridad | `analytics/stats.py` | ✅ Pendiente |
| `fit_gpd_mle` | Migrar + estabilizar SE | `analytics/evt.py` | ✅ Pendiente |
| `gpd_return_level` | Migrar + tests | `analytics/evt.py` | ✅ Pendiente |
| `bootstrap_ci` | Migrar (usar `np.random.Generator`) | `analytics/stats.py` | ✅ Pendiente |
| `DerivAPIError` + taxonomía | Migrar + extender | `errors.py`, `data/deriv_client.py` | ✅ Pendiente |
| `DerivMarketDataClient` (sync) | **Reescribir async** | `data/deriv_client.py` | 🔄 Pendiente |
| `get_active_symbols` / `resolve_symbol` | Migrar + cache TTL | `data/deriv_client.py` | ✅ Pendiente |
| `get_candles` / `get_tick_history` | Migrar + medir rate limit | `data/deriv_client.py` | ✅ Pendiente |
| `AnalyzerConfig` | **Extender a Pydantic** | `config.py` | 🔄 Pendiente |
| `_clean_candles` | Migrar + quality flags | `data/market_data_service.py` | ✅ Pendiente |
| `fetch_historical_data` | Ventanas por TF + RAM cache | `data/market_data_service.py` | 🔄 Pendiente |
| `fetch_tick_history` | Migrar | `data/market_data_service.py` | ✅ Pendiente |
| `add_atr` | Migrar | `analytics/indicators.py` | ✅ Pendiente |
| `fit_evt_gpd` (FALTA) | **IMPLEMENTAR COMPLETA** | `analytics/evt.py` | 🚧 **Bloqueante** |
| `detect_volatility_regimes` (FALTA) | **IMPLEMENTAR Markov + CUSUM** | `analytics/volatility.py` | 🚧 **Bloqueante** |
| Duplicados v1 (4 métodos) | **ELIMINAR** | — | 🗑️ Pendiente |
| `export_csv` | **NO PORTAR** → `ExportService` en memoria | `services/export_service.py` | 🗑️ Pendiente |
| `_safe_name` | Migrar | `utils.py` | ✅ Pendiente |
| `_consume_live_queue` | Async queue | `live/stream_manager.py` | 🔄 Pendiente |
| `_process_live_tick` | Migrar (algoritmo exacto) | `live/stream_manager.py` | ✅ Pendiente |
| `start_live_ticks` / `stop_live_ticks` | WebSocket async + FastAPI WS | `live/stream_manager.py` | 🔄 Pendiente |

---

## Statistical Corrections (optativas; no se aplican por defecto)

> **Política Fase 1**: se preserva el cálculo exacto del prototipo (`p_null = 0.5`;
> BH solo en walk-forward). Las correcciones siguientes son optativas y se activan
> solo con autorización explícita, con tests de valores esperados (no de paridad 1e-10).

1. **Null de zonas geométrico (optativo)**: `p_null = b / (a + b)` con `a=favourable_atr`, `b=adverse_atr` → 0.4286
2. **FDR in-sample (optativo)**: Benjamini-Hochberg por familia `(symbol, timeframe, stage)` → `p_value_adj`
3. **Ambiguos/pendientes**: Reportar por separado, excluir del denominador principal, mostrar sensibilidad
4. **GARCH/ACF solo barras temporales**: UI advierte "usa velas, no ticks irregulares"
5. **Walk-forward**: `independent: false` siempre; sign test descriptivo; advertencia folds solapados
6. **EVT gates**: `n_excess < 30` → `insufficient`, no ajusta GPD
7. **Regímenes gates**: `n_obs < 500` → `insufficient`
8. **Config hash**: SHA-256 de `gates + analysis + runtime + version` — cambio = recálculo forzado

---

## Milestones (Fase 1)

### M1A — Config + DerivClient + RAM Cache (Semana 1)
- [ ] `config.py`: Pydantic models (AnalyzerConfig, ModelConfig, GateConfig, RAMConfig) + `config_hash`
- [ ] `errors.py`: Taxonomía completa (códigos, severidad, retryable, mensajes ES)
- [ ] `data/deriv_client.py`: Cliente async persistente, rate limiter token bucket, fallback, reconexión
- [ ] `data/cache.py`: RAM cache con `deque` buffers, LRU evicción, monitor 30s
- [ ] `data/market_data_service.py`: Velas normalizadas, calidad, sin persistencia
- [ ] Tests: unitarios config, deriv_client (mock WS), cache evicción

### M1B — SnapshotService + Live StreamManager (Semana 2)
- [ ] `domain/snapshot.py`: `InstrumentSnapshot` (Pydantic frozen) + `FeatureStatus`
- [ ] `services/snapshot_store.py`: Swap atómico, versionado, lectura concurrente segura
- [ ] `services/snapshot_service.py`: Orquestador con gates, feature registry, recálculo background
- [ ] `live/stream_manager.py`: WebSocket Deriv → asyncio.Queue → consumer → `_process_live_tick` exacto
- [ ] `analytics/indicators.py`: ATR, retornos, utilidades
- [ ] Tests: integración snapshot + live stream (fixtures)

### M1C — Analytics Core + Funciones Faltantes (Semana 3)
- [ ] `analytics/stats.py`: Wilson, Binomial, BH-FDR, Bootstrap (paridad 1e-10)
- [ ] `analytics/evt.py`: `fit_gpd_mle`, `gpd_return_level`, **`fit_evt_gpd` COMPLETA** (MLE + KS + Mean Excess + Return Levels)
- [ ] `analytics/events.py`: Eventos vela/tick, condicional (paridad)
- [ ] `analytics/zones.py`: Pivotes, clustering, evaluación (null geo, FDR, bootstrap)
- [ ] `analytics/volatility.py`: GARCH, **`detect_volatility_regimes` Markov + CUSUM**
- [ ] `analytics/survival.py`: Kaplan-Meier (event_index + time), censura
- [ ] `analytics/diagnostics.py`: Ljung-Box, Jarque-Bera, ARCH-LM, ADF
- [ ] `analytics/walkforward.py`: Validación anclada, agregación con advertencias
- [ ] Tests: **paridad numérica 100%** vs `boom_crash_quant_analyzer.py` original

### M1D — FastAPI + WebSocket + Export (Semana 4)
- [ ] `dashboard/app.py`: FastAPI app, lifespan, middleware, exception handlers
- [ ] `dashboard/routes.py`: REST endpoints + WebSocket `/api/ws/{symbol}`
- [ ] `services/export_service.py`: `BytesIO` en memoria → `StreamingResponse` (JSON/CSV/ZIP)
- [ ] `scripts/run.ps1`: Arranque único, config env, logs consola
- [ ] `scripts/spike_deriv.py`: Test conexión Deriv (endpoint, latencia, símbolos, ticks)
- [ ] `scripts/warm_cache.py`: Precarga caché (opcional)
- [ ] Tests: contract (OpenAPI), smoke API, WebSocket ping/pong

---

## Risks & Mitigations (Fase 1)

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| `fit_evt_gpd` / `detect_volatility_regimes` complejas | Bloqueante | Implementar TDD: tests con parámetros conocidos ANTES de código; fixtures sintéticas |
| Rate limit Deriv en CI | Tests fallan | Tests unitarios **sin red** (fixtures); tests live marcados `@pytest.mark.live` opcionales |
| RAM > 400 MB en i3-10100F | Crítico | Monitor 30s + LRU evicción agresiva; downsampling LTTB; `maxlen` conservadores |
| Paridad numérica falla | Credibilidad | Tolerancia 1e-10; seeds fijas; fixtures determinísticas; debug diff automático |
| WebSocket Deriv inestable | Datos incompletos | Reconexión exponencial + jitter; fallback endpoints; `dropped_ticks` visible |
| `statsmodels` Markov no converge | Regímenes fallan | CUSUM fallback automático; log warning; `method: "cusum"` en respuesta |
| Scope creep (añadir gráficos, UI) | Retraso | **Congelar alcance Fase 1**: solo backend + APIs. Frontend = Fase 4. |

---

## Success Criteria (Fase 1 — Medibles)

| Criterio | Verificación |
|----------|--------------|
| **SC-M1** | `uv run python -m quantlab` arranca en < 3s, health check ok |
| **SC-M2** | `POST /api/config/BOOM1000` valida, recalcula, push WS `config_updated` |
| **SC-M3** | `scripts/spike_deriv.py` conecta, recibe ticks, muestra latencia < 100ms |
| **SC-M4** | RAM < 400 MB tras 10 min operación (2 índices, 6 TF, live) |
| **SC-M5** | `data/cache/` no existe o vacío (cero disco) |
| **SC-M6** | Tests paridad numérica: **100% funciones migradas** pasan (tol 1e-10) |
| **SC-M7** | `fit_evt_gpd` y `detect_volatility_regimes` implementadas, testeadas, llamadas desde analytics |
| **SC-M8** | Duplicados v1 eliminados (4 métodos), v2 canónicas únicas |
| **SC-M9** | Rate limiter respeta 150/min (test de carga 5 min) |
| **SC-M10** | Reconexión Deriv automática tras corte 30s (test integración) |

---

## Assumptions

- Python 3.13 gestionado con `uv` (fijado en `.python-version` y `uv.lock`)
- Dependencias: `fastapi`, `websockets`, `pydantic`, `pydantic-settings`, `psutil`, `downsample` (LTTB)
- `statsmodels` incluye `tsa.regime_switching.markov_regression` (verificar versión ≥ 0.14)
- Hardware: Windows 10, i3-10100F, 8 GB RAM (~3.7 GB libres) — baseline real
- Usuario ejecuta en `127.0.0.1` (no exposición externa en Fase 1)
- Deriv endpoints accesibles: `wss://api.derivws.com/trading/v1/options/ws/public` + fallback legacy

---

## Próximos Pasos

1. **Crear `tasks.md`** con breakdown granular (esta fase)
2. **Ejecutar `/speckit.analyze`** para validar coherencia con spec padre `001-crash-boom-dashboard`
3. **Implementar M1A** (Config + DerivClient + RAM Cache) — base para todo lo demás
4. **Implementar funciones faltantes** (`fit_evt_gpd`, `detect_volatility_regimes`) en paralelo con tests TDD