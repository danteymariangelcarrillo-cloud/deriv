# Implementation Plan: Migration Foundation — Crash/Boom Quant Lab Dashboard

**Branch**: `002-migration-foundation` | **Date**: 2026-11-16 | **Spec**: [spec.md](./spec.md)  
**Parent**: `001-crash-boom-dashboard` (specs/001-crash-boom-dashboard/plan.md)

---

## Summary

Construir la **capacidad fundacional** del dashboard: configuración tipada (Pydantic), cliente Deriv async con rate limiting y reconexión, caché RAM con buffers circulares `deque`, servicio de snapshots inmutables con swap atómico, stream manager para ticks en vivo, y **las dos funciones estadísticas faltantes** (`fit_evt_gpd` completa + `detect_volatility_regimes` Markov/CUSUM). Todo sin escribir a disco (CERO DISCO), con FastAPI + WebSocket listos para el frontend futuro.

**Decisiones autorizadas (VISION.md §13):**
- `fit_evt_gpd`: completa (MLE + KS + Mean Excess + Return Levels)
- `detect_volatility_regimes`: Markov-Switching + CUSUM fallback
- Duplicados: eliminar v1, mantener v2 canónicas
- Frontend: Vanilla JS (Fase 4) — **no en esta fase**
- Auth: Single-user local
- Persistencia: Solo RAM + Export manual (BytesIO)
- Rate limit: 150/min token bucket
- Downsampling: LTTB (implementación interna numpy; la librería `downsample` no es dependencia)

---

## Technical Context

| Aspecto | Decisión | Justificación |
|---------|----------|---------------|
| **Language** | Python 3.13 (uv) | Fijado en parent spec; `arch`/`statsmodels` compatibles |
| **Async Framework** | FastAPI + `websockets` | Native async, WebSocket support, OpenAPI auto |
| **Config** | Pydantic Settings v2 | Validación estricta, `model_dump_json` para hash |
| **Cache** | `collections.deque` + LRU dict | RAM-only, O(1) append/pop, `maxlen` evicción automática |
| **Concurrencia** | `asyncio` + `ThreadPoolExecutor(max_workers=2)` | i3-10100F = 4 hilos; 2 para CPU-bound (analytics), 2 para API/WS |
| **Estadística** | scipy, statsmodels, arch | Ya en parent spec; Markov requiere statsmodels ≥ 0.14 |
| **Downsampling** | LTTB (`downsample` si está disponible; si no, implementación interna) | ~33 ms/50k→5k con numpy interno (< 5 ms solo con la librería C `downsample`); preserva picos |
| **Testing** | pytest + fixtures determinísticas | Seeds fijas, paridad numérica 1e-10, sin red en unit |
| **Logging** | structlog (consola JSON) | Sin archivos, structured, niveles configurables |

---

## Project Structure (Fase 1 — src/quantlab/)

```text
src/quantlab/
├── __init__.py
├── config.py                 # Pydantic models: AnalyzerConfig, ModelConfig, GateConfig, RAMConfig + config_hash
├── settings.py               # BaseSettings desde .env (QUANTLAB_*)
├── errors.py                 # Taxonomía errores: códigos, severidad, retryable, mensajes ES
├── utils.py                  # _safe_name, config_hash, downsample_lttb
├── state.py                  # InstrumentState (dataclass con buffers deque + locks)
├── domain/
│   ├── __init__.py
│   ├── instrument.py         # Instrument (config, no datos)
│   ├── market_data.py        # Candle, Tick (Pydantic)
│   ├── events.py             # EventCandidate
│   ├── zones.py              # ReactionZone
│   └── snapshot.py           # InstrumentSnapshot (frozen), FeatureStatus, TimeframeAnalysis
├── data/
│   ├── __init__.py
│   ├── deriv_client.py       # DerivClient async: WS persistente, rate limiter, fallback, reconexión
│   ├── deriv_calls.py        # Allowlist llamadas Deriv (solo lectura)
│   ├── rate_limiter.py       # TokenBucket (capacity=30, refill=2.5/s)
│   ├── market_data_service.py# Velas paginadas, normalización OHLC, calidad, SIN persistencia
│   ├── cache.py              # RAMCache: deque buffers, LRU global, monitor 30s
│   └── quality.py            # DataQualityReport, coverage, gaps, duplicates
├── analytics/
│   ├── __init__.py
│   ├── indicators.py         # ATR, retornos, utilidades
│   ├── stats.py              # Wilson, Binomial, BH-FDR, Bootstrap (paridad 1e-10)
│   ├── evt.py                # fit_gpd_mle, gpd_return_level, fit_evt_gpd (COMPLETA)
│   ├── events.py             # Eventos vela/tick, condicional
│   ├── zones.py              # Pivotes, clustering, evaluación (p_null=0.5 exacto; correcciones optativas)
│   ├── volatility.py         # GARCH, detect_volatility_regimes (Markov + CUSUM)
│   ├── survival.py           # Kaplan-Meier (event_index + time), censura
│   ├── diagnostics.py        # Ljung-Box, Jarque-Bera, ARCH-LM, ADF
│   └── walkforward.py        # Validación anclada, agregación con advertencias
├── services/
│   ├── __init__.py
│   ├── snapshot_store.py     # Swap atómico, versionado, lectura concurrente
│   ├── snapshot_service.py   # Orquestador: gates → analytics → snapshot frozen → atomic swap
│   ├── feature_registry.py   # Registro análisis + gates por TF
│   └── export_service.py     # BytesIO en memoria → StreamingResponse (JSON/CSV/ZIP)
├── live/
│   ├── __init__.py
│   └── stream_manager.py     # WebSocket Deriv → asyncio.Queue → consumer → _process_live_tick (exacto)
└── dashboard/
    ├── __init__.py
    ├── app.py                # FastAPI app, lifespan, middleware, exception handlers
    ├── routes.py             # REST endpoints + WebSocket /api/ws/{symbol}
    └── schemas.py            # Pydantic models para request/response API
```

**Tests Structure:**
```text
tests/
├── unit/
│   ├── test_config.py
│   ├── test_stats.py           # Paridad Wilson, Binomial, BH, Bootstrap
│   ├── test_evt.py             # Paridad fit_gpd_mle, gpd_return_level + fit_evt_gpd
│   ├── test_zones.py           # Paridad pivotes, clustering, evaluación
│   ├── test_volatility.py      # Paridad GARCH + detect_volatility_regimes
│   ├── test_survival.py        # Paridad Kaplan-Meier
│   ├── test_diagnostics.py     # Paridad Ljung-Box, JB, ARCH-LM, ADF
│   ├── test_walkforward.py     # Paridad walk-forward
│   └── test_indicators.py      # ATR, retornos
├── integration/
│   ├── test_deriv_client.py    # Mock WS, rate limiter, fallback
│   ├── test_market_data.py     # Fixtures velas/ticks, normalización
│   ├── test_snapshot.py        # Snapshot inmutable, atomic swap, gates
│   ├── test_live_stream.py     # StreamManager, _process_live_tick exacto
│   └── test_export.py          # BytesIO JSON/CSV/ZIP
├── contract/
│   ├── test_api_schema.py      # OpenAPI schema validation
│   └── test_ws_protocol.py     # WebSocket message schemas
├── performance/
│   ├── test_ram_usage.py       # RSS < 400 MB operación normal
│   ├── test_startup_time.py    # < 3s arranque
│   └── test_rate_limit.py      # ≤ 150 req/min
└── fixtures/
    ├── boom1000_5min.parquet   # 30 días, seed=42
    ├── crash500_ticks.csv      # 5000 ticks, seed=42
    ├── snapshot_boom1000.json  # Snapshot esperado
    └── synthetic_series/       # Series con parámetros conocidos para EVT/GARCH/Regímenes
```

**Scripts:**
```text
scripts/
├── run.ps1              # Arranque único: uv run python -m quantlab
├── spike_deriv.py       # Test conexión Deriv (endpoint, latencia, símbolos, ticks)
├── warm_cache.py        # Precarga caché (opcional)
└── verify_contracts.py  # Valida schemas JSON contra snapshots
```

---

## Phase 0: Research (Completado en VISION.md)

Decisiones ya tomadas y documentadas en VISION.md §13. No hay investigación pendiente para Fase 1.

---

## Phase 1: Design (Data Models, Contracts, Tasks)

### Data Flow (Fase 1)

```text
Deriv WS Público
    ↓ (1 conexión persistente por símbolo, token bucket 150/min, fallback, reconexión)
DerivClient (async)
    ↓
MarketDataService (velas paginadas, normalización OHLC, calidad)
    ↓
RAMCache (deque buffers: ticks 50k, velas 30k/TF, returns 10k)
    ↓
SnapshotService (orquesta analytics con gates, feature registry)
    ↓
Analytics Engine (stats, evt, events, zones, volatility, survival, diagnostics, walkforward)
    ↓
InstrumentSnapshot (Pydantic frozen, inmutable)
    ↓
SnapshotStore (atomic swap, versionado)
    ↓
FastAPI Routes (REST + WebSocket push)
    ↓
Cliente (Fase 4) / ExportService (BytesIO)
```

### Service Interfaces

```python
# data/deriv_client.py
class DerivClient:
    async def request(self, payload: dict, expected_types: tuple, expected_key: str) -> dict
    async def subscribe_ticks(self, symbol: str) -> AsyncIterator[dict]  # async generator
    async def get_active_symbols(self) -> list[dict]
    def resolve_symbol(self, target_name: str, override: str | None) -> tuple[str, str]
    @property
    def active_endpoint(self) -> str
    @property
    def connection_state(self) -> ConnectionState

# data/market_data_service.py
class MarketDataService:
    async def get_candles(self, symbol: str, timeframe: str, start: int, end: int, limit: int) -> tuple[pd.DataFrame, DataQualityReport]
    async def get_tick_history(self, symbol: str, count: int, start: int | None) -> pd.DataFrame
    async def warm_cache(self, symbol: str, timeframes: list[str]) -> dict[str, int]

# services/snapshot_service.py
class SnapshotService:
    async def build_snapshot(self, symbol: str, timeframes: list[str] | None = None) -> InstrumentSnapshot
    async def recompute_timeframe(self, symbol: str, timeframe: str) -> TimeframeAnalysis
    async def update_config(self, symbol: str, config_update: ConfigUpdateRequest) -> ConfigUpdateResponse

# live/stream_manager.py
class StreamManager:
    async def start(self, symbol: str) -> None
    async def stop(self, symbol: str) -> None
    async def get_status(self, symbol: str) -> StreamState
    # Internal: _consumer_loop, _process_live_tick (algoritmo exacto original)
```

### API Routes (Fase 1)

| Ruta | Método | Handler | Descripción |
|------|--------|---------|-------------|
| `/api/health` | GET | `health_check` | `{status, version, uptime, instruments}` |
| `/api/instruments` | GET | `list_instruments` | `[{symbol, display_name, direction, enabled}]` |
| `/api/snapshot/{symbol}` | GET | `get_snapshot` | Snapshot completo o `?tf=5min` filtrado |
| `/api/config/{symbol}` | GET | `get_config` | Config efectiva + `config_hash` |
| `/api/config/{symbol}` | POST | `update_config` | Valida, recalcula background, push WS |
| `/api/config/{symbol}/reset` | POST | `reset_config` | Defaults + recálculo |
| `/api/live/{symbol}/status` | GET | `get_stream_status` | `StreamState` (connected, latency, dropped, queue) |
| `/api/export/{symbol}` | GET | `export_snapshot` | `?format=json|csv|zip` → StreamingResponse |
| `/api/ws/{symbol}` | WS | `websocket_endpoint` | Subscribe ticks/events/zones/config |

---

## Phase 2: Implementation Tasks (Ver `tasks.md`)

### Milestone M1A — Config + DerivClient + RAM Cache

| Task | Archivos | Descripción |
|------|----------|-------------|
| **T001** | `config.py`, `settings.py` | Pydantic models: AnalyzerConfig, ModelConfig, GateConfig, RAMConfig + `config_hash()` |
| **T002** | `errors.py` | Taxonomía completa (códigos, severidad, retryable, mensajes ES, context) |
| **T003** | `utils.py` | `_safe_name`, `config_hash`, `downsample_lttb` (numpy interno) |
| **T004** | `state.py` | `InstrumentState` dataclass con buffers `deque` + `asyncio.Lock` |
| **T005** | `data/rate_limiter.py` | `TokenBucket(capacity=30, refill_per_second=2.5)` async-safe |
| **T006** | `data/deriv_calls.py` | Allowlist llamadas Deriv (solo lectura: active_symbols, ticks_history, ticks subscribe) |
| **T007** | `data/deriv_client.py` | `DerivClient` async: WS persistente, rate limiter, fallback endpoints, reconexión exponencial |
| **T008** | `data/cache.py` | `RAMCache`: deque buffers por símbolo/TF, LRU global, monitor 30s + evicción |
| **T009** | `data/market_data_service.py` | `MarketDataService`: velas paginadas, normalización OHLC, calidad, sin persistencia |
| **T010** | `data/quality.py` | `DataQualityReport`: coverage, gaps, duplicates, stale, dropped_ticks |

### Milestone M1B — SnapshotService + Live StreamManager

| Task | Archivos | Descripción |
|------|----------|-------------|
| **T011** | `domain/market_data.py` | `Candle`, `Tick` Pydantic models |
| **T012** | `domain/events.py` | `EventCandidate` Pydantic |
| **T013** | `domain/zones.py` | `ReactionZone` Pydantic |
| **T014** | `domain/snapshot.py` | `InstrumentSnapshot` (frozen), `FeatureStatus`, `TimeframeAnalysis`, `OverallStatus` |
| **T015** | `services/snapshot_store.py` | `SnapshotStore`: atomic swap, versionado, lectura concurrente segura |
| **T016** | `services/feature_registry.py` | `FeatureRegistry`: gates por análisis, `FeatureStatus` por TF |
| **T017** | `services/snapshot_service.py` | `SnapshotService`: orquesta analytics, gates, background recompute |
| **T018** | `analytics/indicators.py` | `add_atr`, `log_returns`, `directional_returns` |
| **T019** | `live/stream_manager.py` | `StreamManager`: WS Deriv → asyncio.Queue → consumer → `_process_live_tick` (algoritmo exacto original) |
| **T020** | `scripts/spike_deriv.py` | Test conexión Deriv: endpoint, latencia, símbolos, 30s ticks |

### Milestone M1C — Analytics Core + Funciones Faltantes

| Task | Archivos | Descripción |
|------|----------|-------------|
| **T021** | `analytics/stats.py` | Wilson, Binomial, BH-FDR, Bootstrap (tests paridad 1e-10) |
| **T022** | `analytics/evt.py` | `fit_gpd_mle`, `gpd_return_level`, **`fit_evt_gpd` COMPLETA** (MLE + KS + Mean Excess + Return Levels) |
| **T023** | `analytics/events.py` | `detect_candle_extremes`, `detect_tick_extremes`, `conditional_event_table` (paridad) |
| **T024** | `analytics/zones.py` | `_find_pivots`, `_cluster_levels`, `_evaluate_zone` (p_null=0.5), `detect_reaction_zones` |
| **T025** | `analytics/volatility.py` | `fit_garch`, **`detect_volatility_regimes` Markov + CUSUM** |
| **T026** | `analytics/survival.py` | Kaplan-Meier (event_index + time), censura, KS vs Exponencial |
| **T027** | `analytics/diagnostics.py` | Ljung-Box, Jarque-Bera, ARCH-LM, ADF |
| **T028** | `analytics/walkforward.py` | `walk_forward_validation`, `aggregate_walk_forward_results` (advertencias) |
| **T029** | `tests/fixtures/` | Generar fixtures determinísticas (seed=42) para todas las funciones |
| **T030** | `tests/unit/test_*_parity.py` | Tests paridad numérica vs `boom_crash_quant_analyzer.py` original |

### Milestone M1D — FastAPI + WebSocket + Export

| Task | Archivos | Descripción |
|------|----------|-------------|
| **T031** | `dashboard/schemas.py` | Pydantic request/response models para API REST + WS |
| **T032** | `dashboard/app.py` | FastAPI app: lifespan, CORS, exception handlers, structlog middleware |
| **T033** | `dashboard/routes.py` | REST endpoints + WebSocket `/api/ws/{symbol}` (subscribe, push, ping/pong) |
| **T034** | `services/export_service.py` | `ExportService`: BytesIO JSON/CSV/ZIP → StreamingResponse |
| **T035** | `scripts/run.ps1` | Arranque único: `uv run python -m quantlab` |
| **T036** | `scripts/warm_cache.py` | Precarga caché opcional (símbolos, TFs, force) |
| **T037** | `scripts/verify_contracts.py` | Valida snapshots contra `contracts/snapshot.schema.json` |
| **T038** | `tests/contract/test_api_schema.py` | OpenAPI schema validation |
| **T039** | `tests/contract/test_ws_protocol.py` | WebSocket message schemas |
| **T040** | `tests/contract/test_forbidden_calls.py` | Escaneo estático de llamadas prohibidas |
| **T041** | `tests/performance/` | Rendimiento (startup, RAM, rate limit, recompute), mediana 5 |
| **T042** | Consolidar duplicados en código nuevo | `legacy/` intacto; equivalencia v1→v2 en `docs/migration.md` |
| **T043** | No portar `export_csv` | `ExportService` en memoria (`BytesIO`); `legacy/` intacto |

---

## Testing Strategy (Fase 1)

### Unit Tests (Paridad Numérica Obligatoria)
```python
# tests/unit/test_stats_parity.py
from boom_crash_quant_analyzer import wilson_score_interval as original
from quantlab.analytics.stats import wilson_score_interval as migrated

@pytest.mark.parametrize("successes,trials", [(10,100), (0,50), (5,5), (49,100)])
def test_wilson_parity(successes, trials):
    o = original(successes, trials)
    m = migrated(successes, trials)
    assert abs(o[0] - m[0]) < 1e-10
    assert abs(o[1] - m[1]) < 1e-10
```
**Regla:** 100% funciones públicas migradas tienen test de paridad (tol 1e-10).

### Integration Tests (Fixtures Determinísticas)
- Fixtures en `tests/fixtures/` generadas con `seed=42`
- Tests **sin red** (mock WS Deriv)
- `@pytest.mark.live` para tests reales con Deriv (opcionales, manuales)

### Contract Tests
- `jsonschema` validation contra `contracts/snapshot.schema.json`
- OpenAPI schema validation (request/response models)
- WebSocket message schema validation

### Performance Tests (Mediana 5 runs)
- `test_startup_time`: `uv run python -m quantlab` → health check < 3s
- `test_ram_usage`: 2 índices, 6 TF, live 10 min → RSS < 400 MB
- `test_rate_limit`: 5 min carga → ≤ 150 req/min

---

## Configuration (`.env.example`)

```bash
# Server
QUANTLAB_HOST=127.0.0.1
QUANTLAB_PORT=8050
QUANTLAB_DEBUG=0
QUANTLAB_LOG_LEVEL=INFO

# Deriv
QUANTLAB_DERIV_ENDPOINTS=wss://api.derivws.com/trading/v1/options/ws/public,wss://ws.binaryws.com/websockets/v3?app_id=1089
QUANTLAB_RATE_LIMIT_PER_MIN=150

# Cache & RAM
QUANTLAB_MAX_TICKS=50000
QUANTLAB_MAX_CANDLES_PER_TF=30000
QUANTLAB_MAX_DIRECTIONAL_RETURNS=10000
QUANTLAB_DOWNSAMPLE_MAX_POINTS=5000
QUANTLAB_FIGURE_CACHE_SIZE=20
QUANTLAB_RAM_WARNING_MB=380
QUANTLAB_RAM_CRITICAL_MB=420

# Refresh & Staleness
QUANTLAB_REFRESH_SECONDS=60
QUANTLAB_STALE_SECONDS=900

# Seeds
QUANTLAB_SEED=20261115
```

---

## Risks & Mitigations (Fase 1 Específicos)

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| `fit_evt_gpd` KS test falla en datos reales | Bloqueante gráfico #2 | Fallback: si KS p < 0.05, log warning pero retorna fit; test con serie sintética xi=0.2 |
| `detect_volatility_regimes` Markov no converge | Regímenes no disponibles | CUSUM fallback automático; `method: "cusum"` en respuesta; log warning |
| Rate limit Deriv en CI | Tests fallan | Tests unitarios **sin red**; `@pytest.mark.live` solo manual |
| RAM > 400 MB en i3-10100F | Crítico hardware | Monitor 30s + LRU evicción agresiva; `maxlen` conservadores; downsampling LTTB |
| Paridad numérica falla en bootstrap | Credibilidad | Usar `np.random.Generator(seed)` local; no global seed; tolerancia 1e-10 |
| WebSocket Deriv inestable en Windows | Datos incompletos | Reconexión exponencial + jitter; fallback endpoints; `dropped_ticks` visible |
| `statsmodels` versión sin MarkovRegression | Regímenes fallan | Verificar en `pyproject.toml`: `statsmodels>=0.14.0`; test import en CI |

---

## Milestones Timeline

| Milestone | Entregable | Criterio de Éxito |
|-----------|------------|-------------------|
| **M1A** (Semana 1) | Config + DerivClient + RAM Cache | `DerivClient` conecta, rate limiter funciona, buffers `deque` no crecen indefinidos |
| **M1B** (Semana 2) | SnapshotService + StreamManager | Snapshot inmutable + atomic swap; StreamManager recibe ticks, `_process_live_tick` exacto |
| **M1C** (Semana 3) | Analytics Core + Funciones Faltantes | **100% paridad numérica**; `fit_evt_gpd` y `detect_volatility_regimes` testeadas |
| **M1D** (Semana 4) | FastAPI + WS + Export | API responde, WS push ticks/events, export BytesIO, arranque < 3s, RAM < 400 MB |

---

## Definition of Done (Fase 1)

- [ ] Todos los tests unitarios pasan (paridad 1e-10)
- [ ] Tests integración pasan (fixtures determinísticas)
- [ ] Tests contrato pasan (OpenAPI, JSON schema, WS protocol)
- [ ] Tests performance pasan (mediana 5 runs: startup < 3s, RAM < 400 MB, rate limit ≤ 150/min)
- [ ] `scripts/spike_deriv.py` conecta y recibe ticks en < 5s
- [ ] `data/cache/` no existe o vacío (CERO DISCO verificado)
- [ ] Duplicados v1 eliminados (4 métodos)
- [ ] `export_csv` eliminada, sustituida por `ExportService`
- [ ] `fit_evt_gpd` y `detect_volatility_regimes` implementadas, documentadas, testeadas
- [ ] `/speckit.analyze` sin gaps críticos vs spec padre `001-crash-boom-dashboard`
- [ ] Documentación `README.md` actualizada con comandos Fase 1

---

## Próximos Pasos Post-Fase 1

1. **Fase 2** — Gráficos Plotly (backend): migrar 10 gráficos canónicos a `charts/` generando `fig.to_json()`
2. **Fase 3** — Downsampling LTTB + cache figuras por `config_hash`
3. **Fase 4** — Frontend Vanilla JS + Plotly.js local (servido por FastAPI; routing hash, WebSocket client, componentes)
4. **Fase 5** — Páginas Metodología/Sistema + Export UI
5. **Fase 6** — Convergencia (`/speckit.converge` hasta "Converged")