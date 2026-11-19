# Tasks — Migration Foundation (Fase 1)

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

Desglose granular de tareas para la Fase 1. Cada tarea es una unidad de trabajo atómica, con dependencias explícitas y criterios de aceptación.

**Convención IDs**: `T{###}` — secuencial por milestone. **Prioridad**: P0 (bloqueante), P1 (crítico), P2 (importante), P3 (nice-to-have).

**Nota de secuenciación (2026-11-16)**: T000 (bootstrap) precede a T001. T017 se movió a M1C para romper el ciclo T017↔T021; T016 usa un `FakeAnalyticsEngine` hasta M1C. Los fixtures se generan en T029.

**Estado real (2026-11-18)**: **T000, T001, T002 y T003 completadas**; 40 tareas pendientes. No hay app FastAPI, rutas, `scripts/` ni cliente Deriv: la Fase 1 **no** está implementada y `quickstart.md` describe el objetivo.

---

## Milestone M1A — Config + DerivClient + RAM Cache (Semana 1)

### T000 — Project Bootstrap (pyproject + uv + dirs)
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `pyproject.toml`, `.gitignore`, `.env.example`, `tests/`, `legacy/`, `boom_crash_quant_analyzer.py`, `analizador_crash_boom_quantitativo.ipynb`, `tests/fixtures/legacy_sha256.json`

**Descripción**: Crear el esqueleto ejecutable antes de T001.

**Subtareas**:
- [x] `pyproject.toml`: `requires-python = ">=3.13,<3.14"`; deps `fastapi`, `uvicorn`, `websockets`, `pydantic`, `pydantic-settings`, `pandas`, `numpy`, `scipy`, `statsmodels`, `arch`, `structlog`, `psutil`, `downsample` (si está disponible); dev `pytest`, `pytest-asyncio`, `httpx`, `ruff`
- [x] `uv python install 3.13` + `uv python pin 3.13` + `uv sync`
- [x] Crear `src/quantlab/` y `tests/{unit,integration,contract,performance}` con `__init__.py`
- [x] `.gitignore`, `.env.example` y `src/quantlab/__main__.py`
- [x] `git init` + commit inicial; mover prototipo+notebook a `legacy/` sin modificar; `tests/fixtures/legacy_sha256.json` con SHA-256

**Criterios de Aceptación**:
- `uv sync` y `uv run python -c "import quantlab"` funcionan
- `pytest` arranca (0 tests OK)
- `legacy/` intacto; SHA-256 fijado; commit inicial creado

---

### T001 — Config Pydantic Models
**Prioridad**: P0 | **Dependencias**: T003 | **Archivos**: `config.py`, `settings.py` (config_hash vía `utils.config_hash`)

**Descripción**: Implementar modelos Pydantic v2 para toda la configuración tipada con validación estricta.

**Subtareas**:
- [x] `AnalyzerConfig`: 13 campos base + `timeframe_overrides` (dict[str, dict])
- [x] `ModelConfig`: rolling_window, garch_p/q, evt_threshold_quantile, wf_train/test/step_days por TF, regime_method
- [x] `GateConfig`: 12 gates mínimos + wf_min_days por TF
- [x] `RAMConfig`: 8 límites de buffers + downsample + figure_cache + RAM warning/critical
- [x] `settings.py`: `BaseSettings` desde `.env` con prefijo `QUANTLAB_`
- [x] `config_hash()`: usa `utils.config_hash` (implementado en T003)
- [x] Validación: rangos estrictos (`Field(gt/ge/lt/le)`, `extra="forbid"`, `frozen`), partial update support (`QuantLabConfig.updated`)

**Criterios de Aceptación**:
- `AnalyzerConfig()` defaults coinciden con `boom_crash_quant_analyzer.py` v2.2.0
- `config_hash` idempotente: mismo input → mismo hash; cambio semántico → hash distinto
- `settings.py` carga `.env` y valida tipos
- Tests: `test_config.py` + `test_config_hash.py` + `test_settings.py` (los tests de entorno viven en `test_config.py`)
- Notas de desviación: `AnalyzerConfig` conserva los 13 campos del prototipo, incluido `days=20` (la spec lista 12); los mapas `wf_*` cubren las 6 temporalidades de `data-model.md`, no sólo 5min/1h como en `spec.md`

---

### T002 — Taxonomía de Errores
**Prioridad**: P0 | **Dependencias**: T001 | **Archivos**: `errors.py`

**Descripción**: Definir jerarquía de excepciones y códigos de error estructurados con mensajes en español.

**Subtareas**:
- [x] `ErrorCode` enum: 18 códigos documentados (CONFIG_INVALID, DERIV_TRANSPORT, COMPUTE_INSUFFICIENT, etc.)
- [x] `Severity` enum: info, warning, error, critical
- [x] `ErrorRecord` Pydantic: code, severity, message_es, detail, occurred_at, retryable, source, context
- [x] `QuantLabError` base exception + subclasses por categoría (ConfigError, DerivError, AnalyticsError, CacheError, StreamError)
- [x] `to_error_record()` helper para logging estructurado

**Criterios de Aceptación**:
- Todos los códigos de `data-model.md §10` cubiertos (18; el texto de T002 dice "20+" pero la tabla normativa define 18)
- Mensajes ES accionables (no técnicos crudos)
- Tests: `test_errors.py` — serialización JSON, retryable logic

---

### T003 — Utilidades Core
**Prioridad**: P1 | **Dependencias**: T000 | **Archivos**: `utils.py`

**Descripción**: Funciones auxiliares compartidas.

**Subtareas**:
- [x] `_safe_name()`: migración exacta de `boom_crash_quant_analyzer.py` L1649
- [x] `config_hash()`: implementación propia — SHA-256 de JSON canónico (sorted keys) → `sha256:...`
- [x] `downsample_lttb(data: np.ndarray, max_points: int) -> np.ndarray`: implementación interna con numpy (la librería C `downsample` no es dependencia del proyecto), preservando la semántica LTTB
- [x] `ensure_utc_datetime()`: normalización timestamps
- [x] `generate_deterministic_uuid5(namespace: str, *parts: str) -> str`: para zone_id, event_id, snapshot_id

**Criterios de Aceptación**:
- `downsample_lttb` reduce 50k → 5k puntos en ≤ 100 ms con la implementación interna numpy (medido ~33 ms de mediana en la máquina de referencia); el objetivo < 5 ms aplica solo al wrapper C `downsample`, que no es dependencia del proyecto
  - Desviación consciente (T003): LTTB exacto es secuencial — el ancla de cada bucket es el punto elegido en el anterior — por lo que la implementación pura Python/numpy no alcanza el objetivo del wrapper C
- UUID5 determinista: mismo input → mismo UUID
- `config_hash` idempotente, estable entre procesos y estricto: rechaza valores no JSON (`set`, `datetime`, `NaN`) en vez de convertirlos con `str`
- `downsample_lttb` rechaza x no monótona (el contrato la exige no decreciente)
- Tests: `test_utils.py`

---

### T004 — InstrumentState (Estado Mutable RAM)
**Prioridad**: P0 | **Dependencias**: T001, T003 | **Archivos**: `state.py`

**Descripción**: Dataclass principal que mantiene todo el estado en RAM por instrumento.

**Subtareas**:
- [ ] `InstrumentState` dataclass con `slots=True`
- [ ] Buffers `deque` con `maxlen`: historical_ticks, live_ticks, candles_by_tf, directional_returns
- [ ] Configs: analyzer, model, gates, ram + config_hash
- [ ] Snapshot: `snapshot: InstrumentSnapshot | None`, `snapshot_version: int`
- [ ] Live stream: `ws_task`, `live_queue`, `dropped_ticks`, `latency_ms`, `connected`, `active_endpoint`
- [ ] Locks: `recompute_lock: asyncio.Lock`
- [ ] Métodos helper: `get_candles_df(tf)`, `get_ticks_df()`, `append_live_tick()`

**Criterios de Aceptación**:
- Buffers nunca superan `maxlen` (verificar con test de estrés)
- `recompute_lock` previene recálculos concurrentes
- Tests: `test_state.py` — buffers, locks, serialización

---

### T005 — TokenBucket Rate Limiter
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `data/rate_limiter.py`

**Descripción**: Token bucket thread-safe para asyncio (capacity=30, refill=2.5/s = 150/min).

**Subtareas**:
- [ ] `TokenBucket` class con `asyncio.Lock` interno
- [ ] `acquire(tokens=1)`: bloquea hasta tener tokens
- [ ] `try_acquire(tokens=1)`: no bloquea, retorna bool
- [ ] `state` property → `RateLimitState` (tokens, capacity, refill, calls_last_minute, waited_ms, budget)
- [ ] Integración con `DerivClient.request()` — auto-acquire antes de cada request

**Criterios de Aceptación**:
- 150 req/min sostenido, burst 30
- `waited_ms_last_request` mide latencia por rate limit
- Tests: `test_rate_limiter.py` — carga 5 min ≤ 150/min

---

### T006 — Deriv Allowlist
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `data/deriv_calls.py`

**Descripción**: Única fuente de truth para payloads permitidos a Deriv (allowlist estricta).

**Subtareas**:
- [ ] `ALLOWED_REQUESTS` dict con 6 entradas: active_symbols, ticks_history_candles, ticks_history_ticks, subscribe_ticks, ping, time
- [ ] `build_request(template: str, **params) -> dict`: valida template existe, interpola params
- [ ] `validate_response(response: dict) -> bool`: verifica `msg_type` en allowlist
- [ ] Test: `test_forbidden_calls.py` escanea código y falla si hay `msg_type` no permitido

**Criterios de Aceptación**:
- Cualquier request no construido desde `ALLOWED_REQUESTS` → error en test
- Payloads coinciden con Deriv API pública documentada
- Tests: `test_deriv_calls.py`

---

### T007 — DerivClient Async
**Prioridad**: P0 | **Dependencias**: T002, T005, T006 | **Archivos**: `data/deriv_client.py`

**Descripción**: Cliente WebSocket asíncrono persistente con rate limiting, fallback endpoints, reconexión exponencial.

**Subtareas**:
- [ ] `__init__`: endpoints list, rate_limiter, timeout, retries
- [ ] `request(payload, expected_types, expected_key)`: req_id, timeout, fallback solo en errores transporte
- [ ] `subscribe_ticks(symbol)`: async generator que yield ticks; maneja reconexión interna
- [ ] `get_active_symbols()`: cache TTL 1 hora
- [ ] `resolve_symbol(target, override)`: migración exacta lógica original
- [ ] `connection_state` property → `ConnectionState`
- [ ] `_is_transport_failure()`: detección 520, 502, 503, 504, timeout, connection reset
- [ ] Reconexión: backoff 1s, 2s, 4s, 8s, max 15s + jitter ±0.5s

**Criterios de Aceptación**:
- `scripts/spike_deriv.py` conecta y recibe ticks en < 5s
- Rate limiter respeta 150/min (test carga)
- Fallback funciona si primer endpoint falla con 520
- Reconexión automática tras corte 30s
- Tests: `test_deriv_client.py` (mock WS), `test_deriv_client_live.py` (@pytest.mark.live)

---

### T008 — RAMCache
**Prioridad**: P0 | **Dependencias**: T004 | **Archivos**: `data/cache.py`

**Descripción**: Caché exclusiva en RAM con buffers circulares y evicción LRU global.

**Subtareas**:
- [ ] `RAMCache` class con `ram_config: RAMConfig`
- [ ] `append_ticks(symbol, ticks)` → retorna n_descartados
- [ ] `append_live_tick(symbol, tick)` → retorna bool (descartado por maxlen)
- [ ] `append_candles(symbol, tf, candles)` → retorna n_descartados
- [ ] Getters: `get_ticks`, `get_live_ticks`, `get_candles` → list[Record]
- [ ] Live queue: `enqueue_live`, `dequeue_live` → bool/Record
- [ ] `check_ram_and_evict(rss_mb)`: si > warning → LRU evict velas TF menos usado; si > critical → agresivo
- [ ] `get_stats(symbol)` → `CacheStats`
- [ ] Monitor background task: cada 30s llama `check_ram_and_evict(psutil.Process().memory_info().rss / 1e6)`

**Criterios de Aceptación**:
- Buffers `deque` evicción automática FIFO
- `dropped_ticks` incrementa solo si `live_queue.full()`
- RAM monitor libera memoria bajo presión
- Tests: `test_ram_cache.py` — evicción, stats, monitor

---

### T009 — MarketDataService
**Prioridad**: P0 | **Dependencias**: T002, T007, T008 | **Archivos**: `data/market_data_service.py`

**Descripción**: Descarga velas paginadas, normalización OHLC, reporte de calidad. **Sin persistencia**.

**Subtareas**:
- [ ] `get_candles(symbol, tf, start, end, limit=1000)` → `(DataFrame, DataQualityReport)`
- [ ] Paginación: loop con `page_end = oldest - 1`, max 1000/page, rate limited
- [ ] `_clean_candles()`: migración exacta L516-L538 (validación OHLC, deduplicación, UTC)
- [ ] `get_tick_history(symbol, count=5000, start=None)` → DataFrame
- [ ] `warm_cache(symbol, timeframes, force=False)`: descarga y llena RAMCache
- [ ] `DataQualityReport`: coverage, gaps, duplicates, invalid_ohlc, stale_seconds, dropped_ticks

**Criterios de Aceptación**:
- Velas normalizadas: columnas `datetime, epoch, Open, High, Low, Close`
- Quality report: `status` ok/warn/fail según umbrales
- **Cero archivos escritos** — verificado en test
- Tests: `test_market_data.py` (fixtures), `test_market_data_live.py` (@pytest.mark.live)

---

### T010 — DataQualityReport
**Prioridad**: P1 | **Dependencias**: T009 | **Archivos**: `data/quality.py`

**Descripción**: Modelo Pydantic + lógica de cálculo de calidad de datos.

**Subtareas**:
- [ ] `DataQualityReport` Pydantic (ver `data-model.md §3.6`)
- [ ] `calculate_quality(df, tf, expected_bars)`: gaps > 1.5×granularidad, duplicados epoch, OHLC inválido
- [ ] `stale_seconds = now - last_epoch` si hay datos
- [ ] `status`: ok (≥98% coverage, sin gaps críticos), warn ([90,98%) o gaps menores), fail (<90% o sin datos)

**Criterios de Aceptación**:
- Cobertura calculada correctamente
- Status coherente con umbrales
- Tests: `test_quality.py`

---

## Milestone M1B — SnapshotService + Live StreamManager (Semana 2)

### T011 — Domain Models (Candle, Tick, EventCandidate)
**Prioridad**: P0 | **Dependencias**: T001, T003 | **Archivos**: `domain/market_data.py`, `domain/events.py`

**Descripción**: Modelos Pydantic para datos de entrada y eventos candidatos.

**Subtareas**:
- [ ] `Candle`: epoch, open, high, low, close, tf, source, quality_flags
- [ ] `Tick`: epoch, price, received_at, sequence
- [ ] `EventCandidate`: todos los campos de `data-model.md §3.4` + UUID5 determinista

**Criterios de Aceptación**:
- Validación estricta (precios > 0, OHLC coherente)
- `event_id` determinista: `uuid5(namespace, f"{symbol}|{tf}|{kind}|{epoch}|{price}")`
- Tests: `test_domain.py`

---

### T012 — ReactionZone Model
**Prioridad**: P0 | **Dependencias**: T011 | **Archivos**: `domain/zones.py`

**Descripción**: Modelo Pydantic para zonas de reacción con null geométrico, FDR, bootstrap CI.

**Subtareas**:
- [ ] `ReactionZone` Pydantic (ver `data-model.md §3.5`)
- [ ] `p_null = 0.5` por defecto (réplica exacta); la geometría `b/(a+b)` ≈ 0.4286 es optativa
- [ ] `sensitivity`: rate_ambiguous_as_failure_pct, rate_pending_as_failure_pct
- [ ] `stage`: in_sample | oos

**Criterios de Aceptación**:
- Serialización JSON sin NaN/Inf (→ null + reason)
- Tests: `test_domain.py`

---

### T013 — Snapshot Models (InstrumentSnapshot, FeatureStatus, TimeframeAnalysis)
**Prioridad**: P0 | **Dependencias**: T011, T012 | **Archivos**: `domain/snapshot.py`

**Descripción**: Modelos principales de snapshot inmutable (frozen=True).

**Subtareas**:
- [ ] `FeatureStatus`: id, name_es, category, status, min_sample, sample_size, reason, ui_path, stage
- [ ] `TimeframeAnalysis`: todos los campos `data-model.md §3.7` + `features: dict[analysis_id, FeatureStatus]`
- [ ] `InstrumentSnapshot`: `frozen=True`, todos los campos `data-model.md §3.8`
- [ ] `OverallStatus`: ready | partial | stale | error (lógica de agregación)

**Criterios de Aceptación**:
- `InstrumentSnapshot` inmutable: `model_config = ConfigDict(frozen=True)`
- `config_hash` y `data_version` incluidos
- Tests: `test_snapshot_models.py` — validación schema, inmutabilidad

---

### T014 — SnapshotStore (Atomic Swap)
**Prioridad**: P0 | **Dependencias**: T013 | **Archivos**: `services/snapshot_store.py`

**Descripción**: Almacén en memoria con swap atómico y versionado.

**Subtareas**:
- [ ] `SnapshotStore` class con `dict[str, InstrumentSnapshot]` + `dict[str, int]` version
- [ ] `get(symbol)` → snapshot (lectura atómica, sin lock por ser referencia inmutable)
- [ ] `swap(symbol, snapshot)` → nuevo version (atomic: `self._snapshots[symbol] = snapshot; self._versions[symbol] += 1`)
- [ ] `get_version(symbol)` → int
- [ ] `clear(symbol)` → limpia entrada

**Criterios de Aceptación**:
- Lectura concurrente nunca ve estado parcial (referencia inmutable)
- Swap atómico: versión incrementa 1
- Tests: `test_snapshot_store.py` — concurrencia, versionado

---

### T015 — FeatureRegistry
**Prioridad**: P1 | **Dependencias**: T013 | **Archivos**: `services/feature_registry.py`

**Descripción**: Registro central de análisis + gates por temporalidad.

**Subtareas**:
- [ ] `FeatureRegistry` class con `gate_config: GateConfig`
- [ ] `register_all()`: crea `FeatureStatus` para 14 análisis base (evt.gpd, volatility.garch, volatility.regimes, zones.reaction, survival.kaplan_meier, diagnostics.residuals, walkforward.validation, events.candle, events.tick, events.conditional, autocorrelation, heatmap.price, heatmap.events, data.quality)
- [ ] `check_gate(analysis_id, tf, sample_size)` → `FeatureStatus` con status ready/insufficient/not_applicable
- [ ] `get_ui_path(analysis_id)` → ruta frontend (ej. `/i/BOOM1000/distribution`)

**Criterios de Aceptación**:
- Gates aplicados correctamente (ej. EVT insufficient si n_excess < 30)
- `FeatureStatus` incluye razón legible si insufficient
- Tests: `test_feature_registry.py`

---

### T016 — SnapshotService (Orquestador)
**Prioridad**: P0 | **Dependencias**: T013, T014, T015 | **Archivos**: `services/snapshot_service.py`

**Descripción**: Orquestador principal: config → gates → analytics → snapshot frozen → atomic swap. En M1B se testea con un `FakeAnalyticsEngine`; la integración real con T017 se valida en M1C.

**Subtareas**:
- [ ] `build_snapshot(symbol, timeframes=None)`:
  1. Para cada TF: verifica gates via `FeatureRegistry`
  2. Llama `AnalyticsEngine` para cada análisis con gate ready
  3. Construye `TimeframeAnalysis` con resultados + `FeatureStatus`
  4. Construye `InstrumentSnapshot` frozen
  5. `SnapshotStore.swap(symbol, snapshot)`
- [ ] `recompute_timeframe(symbol, tf)`: recalcula solo una TF (usa `InstrumentState.recompute_lock`)
- [ ] `update_config(symbol, config_update)`:
  1. Merge + valida Pydantic
  2. Nuevo `config_hash`
  3. Si unchanged → 304
  4. Actualiza `InstrumentState.config` + `config_hash`
  6. `snapshot_version += 1` (invalida)
  7. Lanza `recompute_timeframe` background para TFs afectados (ThreadPoolExecutor)
  8. Push WS `config_updated` al terminar cada TF
- [ ] `reset_config(symbol)`: restaura defaults + recalcula

**Criterios de Aceptación**:
- Gates evitan computar análisis sin muestra mínima
- Config hot-reload: respuesta inmediata, recálculo background
- WS push al terminar cada TF
- Tests: `test_snapshot_service.py` — build, recompute, config update

---

### T018 — Indicators (ATR, Returns)
**Prioridad**: P1 | **Dependencias**: T011 | **Archivos**: `analytics/indicators.py`

**Descripción**: Indicadores técnicos puros (migración exacta + vectorización).

**Subtareas**:
- [ ] `add_atr(df, period=14)`: migración exacta L617-L624
- [ ] `log_returns(prices)`: `np.log(prices).diff()`
- [ ] `directional_returns(returns, direction)`: `returns.clip(lower=0)` si up, `(-returns).clip(lower=0)` si down
- [ ] `rolling_quantile(series, window, quantile, min_periods)`: `series.shift(1).rolling(window, min_periods).quantile(quantile)`

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- Vectorizado (sin loops Python)
- Tests: `test_indicators_parity.py`

---

### T019 — StreamManager (Live Ticks)
**Prioridad**: P0 | **Dependencias**: T007, T004, T008 | **Archivos**: `live/stream_manager.py`

**Descripción**: WebSocket Deriv → asyncio.Queue → consumer task → `_process_live_tick` (algoritmo **exacto** original).

**Subtareas**:
- [ ] `StreamManager` class con `deriv_client`, `instrument_state`, `cache`
- [ ] `start(symbol)`:
  1. Limpia `live_ticks`, `live_queue`, `dropped_ticks`, `directional_returns`
  2. Warm-up: llena `directional_returns` con histórico (si existe, maxlen=10k)
  3. Lanza `_consumer_loop` task
  4. Lanza `_ws_loop` task (reconexión interna)
- [ ] `stop(symbol)`: cancela tasks, cierra WS, drena queue
- [ ] `_consumer_loop`: `while not stop_event: tick = await queue.get(); _process_live_tick(tick); queue.task_done()`
- [ ] `_process_live_tick(tick)`: **MIGRACIÓN EXACTA** de `boom_crash_quant_analyzer.py` L1666-L1708:
  - `price = float(tick["quote"])`, `epoch = int(tick["epoch"])`
  - `signed_return = (price / last_price - 1) * 100`
  - `directional = max(0, signed_return if up else -signed_return)`
  - `directional_returns.append(directional)`
  - Si `len(directional_returns) >= 300`: `threshold = quantile(directional_returns, tick_event_quantile)`
  - `is_event = directional > 0 and directional >= threshold`
  - Si evento: append a `live_events`, log, `ticks_since_event = 0`
  - Else: `ticks_since_event += 1`
- [ ] `_ws_loop`: `websockets.connect` con reconexión exponencial, heartbeat ping/pong 20s
- [ ] `get_status(symbol)` → `StreamState`

**Criterios de Aceptación**:
- Algoritmo `_process_live_tick` **idéntico** al original (paridad 1e-10 en test unitario)
- `dropped_ticks` incrementa solo si `live_queue.full()`
- Reconexión automática tras corte
- Tests: `test_live_stream.py` (mock WS + algoritmo), `test_live_stream_live.py` (@pytest.mark.live)

---

### T020 — Spike Deriv Script
**Prioridad**: P1 | **Dependencias**: T007 | **Archivos**: `scripts/spike_deriv.py`

**Descripción**: Script de diagnóstico de conexión Deriv.

**Subtareas**:
- [ ] Conecta a endpoint activo, mide latencia handshake
- [ ] Lista símbolos activos, filtra BOOM1000/CRASH500
- [ ] Descarga 5 velas 5min + 5 ticks muestra
- [ ] Suscripción 30s: cuenta ticks recibidos, eventos detectados
- [ ] Imprime errores clasificados (transport, rate limit, symbol not active)
- [ ] Exit code 0 si OK, 1 si error

**Criterios de Aceptación**:
- Ejecutable standalone: `uv run python scripts/spike_deriv.py`
- Output legible con timestamps
- Tests: manual (requiere red)

---

## Milestone M1C — Analytics Core + Funciones Faltantes (Semana 3)

### T021 — Stats (Wilson, Binomial, BH-FDR, Bootstrap)
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `analytics/stats.py`

**Descripción**: Funciones estadísticas base con paridad 1e-10.

**Subtareas**:
- [ ] `wilson_score_interval(successes, trials, alpha=0.05)` → migración exacta L72-L83
- [ ] `binomial_test_pvalue(successes, trials, p_null, alternative='greater')` → migración exacta L86-L98 (compat SciPy ≥1.11 y <1.11)
- [ ] `benjamini_hochberg_fdr(pvalues, alpha=0.05)` → migración exacta L101-L116
- [ ] `bootstrap_ci(...)`: usa `np.random.Generator(seed)` local; la paridad con legacy se valida por cercanía estadística (RNG distinto)

**Criterios de Aceptación**:
- **Paridad 1e-10** vs original para todos los casos de test
- `bootstrap_ci` determinístico con seed
- Tests: `test_stats_parity.py` (parametrizado con casos edge)

---

### T022 — EVT (fit_gpd_mle, gpd_return_level, fit_evt_gpd COMPLETA)
**Prioridad**: P0 | **Dependencias**: T021 | **Archivos**: `analytics/evt.py`

**Descripción**: Ajuste GPD por MLE + **`fit_evt_gpd` completa** (MLE + KS + Mean Excess + Return Levels).

**Subtareas**:
- [ ] `fit_gpd_mle(excesses)` → migración exacta L119-L161 (estabilizar SE con hessiano numérico)
- [ ] `gpd_return_level(xi, sigma, threshold, n_obs, n_excesses, return_period)` → migración exacta L164-L170
- [ ] **`fit_evt_gpd(returns, threshold_quantile=0.95, direction='directional', min_excesses=30)`**:
  1. `directional = returns.clip(lower=0)` si up, `(-returns).clip(lower=0)` si down
  2. `threshold = np.quantile(directional[directional>0], threshold_quantile)`
  3. `excesses = directional[directional > threshold] - threshold`
  4. Si `len(excesses) < min_excesses`: retorna `{"status": "insufficient", "n_excesses": len(excesses), "min_required": min_excesses}`
  5. `xi, sigma, diagnostics = fit_gpd_mle(excesses)`
  6. **KS test**: `scipy.stats.kstest(excesses, 'genpareto', args=(xi, 0, sigma))`
  7. **Mean Excess Plot**: para 20 umbrales entre P90-P99.5, calcula media excesos
  8. **Return Levels**: 10, 50, 100, 250 períodos
  9. Retorna dict completo con status, xi, sigma, se, ks_stat, ks_p, return_levels, mean_excess_plot

**Criterios de Aceptación**:
- `fit_gpd_mle` paridad 1e-10
- `fit_evt_gpd` retorna todos los campos que `plot_return_distribution` (v2) espera
- KS test p > 0.05 → fit aceptable
- Mean Excess Plot: 20 puntos, lineal si xi > 0
- Tests: `test_evt_parity.py` (fit_gpd_mle), `test_fit_evt_gpd.py` (función completa con serie sintética xi=0.2, σ=1.5)

---

### T023 — Events (Candle/Tick Extremes, Conditional)
**Prioridad**: P0 | **Dependencias**: T018, T021 | **Archivos**: `analytics/events.py`

**Descripción**: Detección de eventos candidatos por velas y ticks + tabla condicional.

**Subtareas**:
- [ ] `detect_candle_extremes(df, config, direction)` → migración exacta L626-L678
- [ ] `detect_tick_extremes(ticks_df, config, direction)` → migración exacta L680-L706
- [ ] `conditional_event_table(ticks_df, events_df, horizon=100, bin_size=100, max_ticks=5000)` → migración exacta L1230-L1259
- [ ] Gates: `candle_events_min_bars`, `tick_events_min_ticks`, `conditional_min_events`

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- Umbral rolling sin look-ahead (`shift(1)`)
- Tests: `test_events_parity.py`

---

### T024 — Zones (Pivots, Clustering, Evaluation)
**Prioridad**: P0 | **Dependencias**: T018, T021 | **Archivos**: `analytics/zones.py`

**Descripción**: Pivotes, clustering por tolerancia, evaluación con null geométrico, FDR, bootstrap CI.

**Subtareas**:
- [ ] `find_pivots(df, side, window)` → migración exacta L709-L734
- [ ] `cluster_levels(pivots, tolerance)` → migración exacta L737-L751
- [ ] `evaluate_zone(df, centre, half_width, zone_type, available_from_idx, event_prices, config)` → migración exacta L753-L868 **CON**:
  - `p_null = 0.5` por defecto; geometría optativa
  - Wilson CI para reaction_rate
  - Binomial test vs p_null
  - Bootstrap CI para median_favourable/adverse_atr
- [ ] `detect_reaction_zones(df, config, side, event_prices)` → migración exacta L870-L925
- [ ] BH solo walk-forward por defecto; in-sample optativo en `detect_reaction_zones`

**Criterios de Aceptación**:
- Paridad 1e-10 en modo default (`p_null=0.5`); modo optativo con valores esperados
- `p_null ≈ 0.4286` solo si se autoriza la corrección
- `p_value_adj` calculado con BH
- Bootstrap CI con 500 replicas
- Tests: `test_zones_parity.py`

---

### T025 — Volatility (GARCH + Regimes Markov/CUSUM)
**Prioridad**: P0 | **Dependencias**: T018, T021 | **Archivos**: `analytics/volatility.py`

**Descripción**: GARCH(1,1) + **`detect_volatility_regimes` Markov-Switching + CUSUM fallback**.

**Subtareas**:
- [ ] `fit_garch(returns, p=1, q=1)` → migración exacta L1598-L1623 (usa `arch_model`)
- [ ] **`detect_volatility_regimes(returns, n_regimes=2, method='markov_switching')`**:
  1. `realized_vol = returns.rolling(20).std().dropna()`
  2. Si `method == 'markov_switching'` y `len(realized_vol) >= 500`:
     - `MarkovRegression(realized_vol, k_regimes=2, switching_variance=True).fit()`
     - Si converge: `regime_probs = fitted_model.regime_probs` (n_obs × 2)
     - Ordenar regímenes por varianza: 0=low, 1=high
     - `high_vol_prob_current = regime_probs[-1, 1]`
     - Segmentos: detectar cambios en `regime_probs.argmax(axis=1)`
  3. Else (CUSUM fallback):
     - Target = mediana realized_vol
     - `S_t = max(0, S_{t-1} + (realized_vol[t] - target) / std)`
     - Umbral `h = 5 * std`
     - Regimen 0 si `S_t < h/2`, 1 si `S_t >= h`
     - Suavizado: `prob_high = sigmoid(S_t - h/2)`
  4. Downsample `series` a ≤ 5000 puntos (LTTB)
  5. Retorna dict completo con method, series, segments, high_vol_prob_current, converged
- [ ] Gate: `regime_min_obs` (500)

**Criterios de Aceptación**:
- GARCH paridad 1e-10
- Markov: converge en serie sintética 2 regímenes; fallback CUSUM si no
- `series` downsampled ≤ 5000
- Tests: `test_volatility_parity.py` (GARCH), `test_regimes.py` (Markov + CUSUM con series sintéticas)

---

### T026 — Survival (Kaplan-Meier)
**Prioridad**: P1 | **Dependencias**: T021 | **Archivos**: `analytics/survival.py`

**Descripción**: Curva Kaplan-Meier modo event_index + time, censura, KS vs Exponencial.

**Subtareas**:
- [ ] `kaplan_meier(durations, censored)` → retorna `(times, survival, ci_lower, ci_upper, at_risk)`
- [ ] Modo `event_index`: duraciones = diff(sorted event_indices); última censurada
- [ ] Modo `time`: duraciones = diff(event_timestamps); última censurada
- [ ] KS test vs Exponencial: `scipy.stats.kstest(durations, 'expon', args=(0, 1/lambda_mle))`
- [ ] Gate: `survival_min_events` (20)

**Criterios de Aceptación**:
- Paridad 1e-10 vs `plot_interevent_survival` v2
- Censura manejada correctamente (última observación)
- KS stat + p-value incluidos
- Tests: `test_survival_parity.py`

---

### T027 — Diagnostics (Ljung-Box, JB, ARCH-LM, ADF)
**Prioridad**: P1 | **Dependencias**: T018 | **Archivos**: `analytics/diagnostics.py`

**Descripción**: Tests estadísticos de residuos.

**Subtareas**:
- [ ] `residual_diagnostics(returns, max_lag=20)` → migración exacta L1554-L1596
- [ ] Ljung-Box: `acorr_ljungbox(returns, lags=[max_lag])`
- [ ] Jarque-Bera: `jarque_bera(returns)`
- [ ] ARCH-LM: `het_arch(returns, maxlags=max_lag)`
- [ ] ADF: `adfuller(returns, maxlag=max_lag, autolag='AIC')`
- [ ] Gate: `acf_min_obs` (100)

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- Todos los p-values y estadísticos incluidos
- Tests: `test_diagnostics_parity.py`

---

### T028 — Walk-Forward Validation
**Prioridad**: P1 | **Dependencias**: T024, T021 | **Archivos**: `analytics/walkforward.py`

**Descripción**: Validación temporal anclada con advertencias de no-independencia.

**Subtareas**:
- [ ] `walk_forward_validation(df, config, tf, train_days, test_days, step_days)` → migración exacta L944-L1039
- [ ] `aggregate_walk_forward_results(wf_df)` → migración exacta L1041-L1063
- [ ] **Advertencias obligatorias en output**:
  - `independent: false`
  - `warning: "Folds no independientes (zonas repetidas, solapamiento temporal). Sign test es descriptivo."`
  - `recommendation` si historia insuficiente
- [ ] Gates: `wf_min_folds` (3), `wf_min_days` por TF

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- `independent: false` siempre presente
- Sign test etiquetado descriptivo
- Tests: `test_walkforward_parity.py`

---

### T017 — AnalyticsEngine (Fachada)
**Prioridad**: P0 | **Dependencias**: T016, T018, T021-T028 | **Archivos**: `analytics/__init__.py`, `services/snapshot_service.py`

**Descripción**: Fachada única para todo el motor analítico (stateless, determinístico).

**Subtareas**:
- [ ] `AnalyticsEngine` class con `gate_config: GateConfig`
- [ ] Métodos delegados a módulos: stats, evt, events, zones, volatility, survival, diagnostics, walkforward
- [ ] Todos los métodos puros: mismos inputs → mismos outputs (seed fija para bootstrap)
- [ ] Sin estado, sin I/O, sin side effects
- [ ] Inyectar `AnalyticsEngine` real en `SnapshotService` (reemplaza `FakeAnalyticsEngine` como default de producción)

**Criterios de Aceptación**:
- Determinístico: misma config + mismos datos → mismo resultado
- Tests: `test_analytics_engine.py` — determinismo, delegación; `test_snapshot_service_real_engine.py` — engine real

---


### T029 — Fixtures Determinísticas
**Prioridad**: P0 | **Dependencias**: T016, T017, T021-T028 | **Archivos**: `tests/fixtures/`, `scripts/generate_fixtures.py`

**Descripción**: Generar fixtures con seed=42 para todos los tests.

**Subtareas**:
- [ ] `generate_fixtures.py`: usa `np.random.Generator(42)` para serie sintética
- [ ] `boom1000_5min.parquet`: 30 días, 25920 velas, OHLC válido
- [ ] `crash500_ticks.csv`: 5000 ticks, precios realistas
- [ ] `synthetic_evt.csv`: GPD(xi=0.2, σ=1.5, threshold=0.005)
- [ ] `synthetic_garch.csv`: GARCH(ω=1e-7, α=0.08, β=0.91)
- [ ] `synthetic_regimes.csv`: 2 regímenes var=0.0004/0.0012, switch en medio
- [ ] `snapshot_boom1000.json`: `build_snapshot()` con fixtures arriba
- [ ] verificar `tests/fixtures/legacy_sha256.json` (creado en T000; solo lectura)

**Criterios de Aceptación**:
- Fixtures determinísticos: mismo seed → mismos archivos (byte-a-byte)
- Los fixtures son artefactos de test en `tests/fixtures/` (no runtime; no se escribe en `data/cache/`).
- Usados en TODOS los tests unitarios/integración
- Tests: `test_fixtures_deterministic.py`

---

### T030 — Tests Paridad Numérica
**Prioridad**: P0 | **Dependencias**: T021-T029 | **Archivos**: `tests/unit/test_*_parity.py`

**Descripción**: Tests de paridad 1e-10 para TODAS las funciones migradas.

**Subtareas**:
- [ ] `test_stats_parity.py`: Wilson, Binomial, BH, Bootstrap (parametrizado)
- [ ] `test_evt_parity.py`: fit_gpd_mle, gpd_return_level
- [ ] `test_fit_evt_gpd.py`: fit_evt_gpd completa (xi, sigma, KS, Mean Excess, RL)
- [ ] `test_events_parity.py`: candle/tick extremes, conditional
- [ ] `test_zones_parity.py`: pivots, clustering, evaluation (modo default 0.5; optativo geo/FDR)
- [ ] `test_volatility_parity.py`: GARCH
- [ ] `test_regimes.py`: Markov + CUSUM (series sintéticas)
- [ ] `test_survival_parity.py`: Kaplan-Meier (event_index + time)
- [ ] `test_diagnostics_parity.py`: Ljung-Box, JB, ARCH-LM, ADF
- [ ] `test_walkforward_parity.py`: validation + aggregation
- [ ] `test_live_parity.py`: `_process_live_tick` algoritmo exacto

**Criterios de Aceptación**:
- **100% funciones públicas migradas** tienen test de paridad
- Tolerancia: `abs(original - migrated) < 1e-10` en modo default; bootstrap/optativo con tolerancia estadística
- Seeds fijas en todos los tests
- Tests pasan en CI sin red

---

## Milestone M1D — FastAPI + WebSocket + Export (Semana 4)

### T031 — API Schemas (Pydantic Request/Response)
**Prioridad**: P0 | **Dependencias**: T013, T001 | **Archivos**: `dashboard/schemas.py`

**Descripción**: Modelos Pydantic para validación de API REST + WS.

**Subtareas**:
- [ ] `HealthResponse`, `InstrumentInfo`
- [ ] `ConfigResponse`, `ConfigUpdateRequest`, `ConfigUpdateResponse`
- [ ] `StreamState`
- [ ] `WSClientMessage`, `WSServerMessage` (union types por `type`)
- [ ] `ExportFormat` enum: json, csv, zip

**Criterios de Aceptación**:
- Validación estricta en FastAPI
- OpenAPI generado automáticamente correcto
- Tests: `test_schemas.py`

---

### T032 — FastAPI App
**Prioridad**: P0 | **Dependencias**: T031, T033 | **Archivos**: `dashboard/app.py`, `tests/integration/test_app.py`

**Descripción**: Aplicación FastAPI con lifespan, middleware, exception handlers.

**Subtareas**:
- [ ] `lifespan`: startup (inicializa DerivClient, StreamManager, SnapshotService) → shutdown (stop streams, close WS)
- [ ] `CORSMiddleware` para localhost
- [ ] `structlog` middleware: request_id, latency, level
- [ ] Exception handlers: `QuantLabError` → JSON estructurado, 500 → genérico
- [ ] `include_router(api_router, prefix="/api")`
- [ ] `include_router(ws_router)` para WebSocket

**Criterios de Aceptación**:
- Arranque < 3s (sin Deriv)
- Logs JSON en consola
- Graceful shutdown (stop streams)
- Tests: `test_app.py` — startup, shutdown, middleware

---

### T033 — REST + WebSocket Routes
**Prioridad**: P0 | **Dependencias**: T031, T016, T019, T034 | **Archivos**: `dashboard/routes.py`

**Descripción**: Endpoints REST + WebSocket `/api/ws/{symbol}`.

**Subtareas**:
- [ ] REST: 8 endpoints (health, instruments, snapshot, config×3, live status, export)
- [ ] WS: `/api/ws/{symbol}` con handler:
  - `subscribe`/`unsubscribe` channels
  - `set_timeframe`
  - `get_chart` (delegado a SnapshotService → figura JSON)
  - `ping`/`pong`
  - Push automático: tick, event, zone_update, config_updated, connection_state
- [ ] Backpressure: queue max 10k msgs, close 1001 si overflow

**Criterios de Aceptación**:
- `GET /api/health` responde en < 10ms
- `POST /api/config` valida, recalcula background, push WS
- WS: suscripción, ping/pong, push automático funciona
- Tests: `test_api.py`, `test_ws.py` (mock WS client)

---

### T034 — ExportService (RAM Only)
**Prioridad**: P1 | **Dependencias**: T013 | **Archivos**: `services/export_service.py`

**Descripción**: Exportación en memoria (BytesIO) → StreamingResponse.

**Subtareas**:
- [ ] `export_json(symbol)`: `snapshot.model_dump_json().encode()`
- [ ] `export_csv_bytes(symbol)`: DataFrames → CSV UTF-8 BOM, separador `;` (Excel ES) en `BytesIO`
- [ ] `export_zip(symbol)`: `zipfile.ZipFile(BytesIO)` con JSON + CSVs por TF
- [ ] `get_media_type(format)`, `get_filename(symbol, format)`
- [ ] Endpoint `/api/export/{symbol}?format=...` → `StreamingResponse`

**Criterios de Aceptación**:
- **Cero archivos en disco** — todo `BytesIO`
- CSV abre correctamente en Excel ES (BOM + `;`)
- ZIP contiene estructura organizada
- Tests: `test_export.py` — bytes válidos, sin archivos temporales

---

### T035 — Run Script (Arranque Único)
**Prioridad**: P1 | **Dependencias**: T032 | **Archivos**: `scripts/run.ps1`

**Descripción**: Script PowerShell para arranque en Windows.

**Subtareas**:
- [ ] `uv run python -m quantlab`
- [ ] Carga `.env` si existe
- [ ] Logs a consola (structlog JSON)
- [ ] Maneja puerto ocupado (reintenta +1)
- [ ] Muestra URL local al arrancar

**Criterios de Aceptación**:
- `.\scripts\run.ps1` arranca app en < 3s
- Funciona en PowerShell 5.1 y 7+
- Tests: manual

---

### T036 — Warm Cache Script
**Prioridad**: P2 | **Dependencias**: T009 | **Archivos**: `scripts/warm_cache.py`

**Descripción**: Precarga caché opcional para evitar espera primer arranque.

**Subtareas**:
- [ ] Args: `--symbols`, `--timeframes`, `--force`
- [ ] Usa `MarketDataService.warm_cache()`
- [ ] Progreso por símbolo/TF en consola
- [ ] `--force` ignora caché y re-descarga

**Criterios de Aceptación**:
- `uv run python scripts/warm_cache.py --symbols BOOM1000 CRASH500 --timeframes 5min 1h`
- Tests: manual

---

### T037 — Verify Contracts Script
**Prioridad**: P1 | **Dependencias**: T013 | **Archivos**: `scripts/verify_contracts.py`, `tests/contract/test_verify_contracts.py`

**Descripción**: Valida snapshots contra JSON schema.

**Subtareas**:
- [ ] Carga `contracts/snapshot.schema.json`
- [ ] Valida `contracts/snapshot.example.json`
- [ ] Valida snapshots generados en `SnapshotStore` (si existen)
- [ ] Exit code 0 si todo válido, 1 si errores

**Criterios de Aceptación**:
- `uv run python scripts/verify_contracts.py` pasa
- Tests: `test_verify_contracts.py`

---

### T038 — Contract Tests (API Schema)
**Prioridad**: P1 | **Dependencias**: T031 | **Archivos**: `tests/contract/test_api_schema.py`

**Descripción**: Validación de esquemas OpenAPI y JSON.

**Subtareas**:
- [ ] OpenAPI schema generado coincide con modelos Pydantic
- [ ] `snapshot.schema.json` valida `snapshot.example.json`
- [ ] `snapshot.schema.json` valida snapshot generado por `build_snapshot()`
- [ ] Request/response models compatibles con FastAPI

**Criterios de Aceptación**:
- `pytest tests/contract/test_api_schema.py` pasa
- Tests: automatizados en CI

---

### T039 — Contract Tests (WS Protocol)
**Prioridad**: P1 | **Dependencias**: T033 | **Archivos**: `tests/contract/test_ws_protocol.py`

**Descripción**: Validación de mensajes WebSocket.

**Subtareas**:
- [ ] Client messages: subscribe, unsubscribe, set_timeframe, get_chart, ping → validados
- [ ] Server messages: tick, event, zone_update, config_updated, connection_state, chart_data, pong, error → validados
- [ ] Tipos union discriminados por `type` field

**Criterios de Aceptación**:
- `pytest tests/contract/test_ws_protocol.py` pasa
- Tests: automatizados

---

### T040 — Forbidden Calls Test
**Prioridad**: P0 | **Dependencias**: T006 | **Archivos**: `tests/contract/test_forbidden_calls.py`

**Descripción**: Escaneo estático de llamadas prohibidas.

**Subtareas**:
- [ ] Implementa `test_no_forbidden_calls()` (ver `contracts/forbidden-calls.md`)
- [ ] Patterns: trading, account, auth, disk, WS no allowlist
- [ ] Allowlist: `tests/integration/test_live_deriv.py`, `scripts/spike_deriv.py`

**Criterios de Aceptación**:
- `pytest tests/contract/test_forbidden_calls.py` pasa (0 violaciones)
- Tests: automatizados en CI

---

### T041 — Performance Tests
**Prioridad**: P1 | **Dependencias**: T017, T019, T032, T033 | **Archivos**: `tests/performance/`

**Descripción**: Tests de rendimiento (mediana 5 runs).

**Subtareas**:
- [ ] `test_startup_time.py`: `uv run python -m quantlab` → health check < 3s (mediana 5)
- [ ] `test_ram_usage.py`: 2 índices, 6 TF, live 10 min → RSS < 400 MB (mediana 5)
- [ ] `test_rate_limit.py`: carga 5 min → ≤ 150 req/min (mediana 5)
- [ ] `test_recompute_time.py`: `POST /api/config` → recalculated_timeframes < 500ms/TF
- [ ] `test_tick_latency.py`: tick→queue < 5 ms (deserialización + enqueue, mediana 5)
- [ ] `test_warm_start.py`: arranque con Deriv warm < 30 s (mediana 5)

**Criterios de Aceptación**:
- Mediana 5 runs dentro de targets
- Latencia tick→queue < 5 ms
- Tests marcados `@pytest.mark.performance` (excluidos de CI rápido)
- Tests: manual / nightly

---

### T042 — Consolidar Duplicados en Código Nuevo (legacy intacto)
**Prioridad**: P0 | **Dependencias**: T017, T021, T022, T023, T024, T025, T026, T027, T028 | **Archivos**: `analytics/` (nuevo), `docs/migration.md`, `tests/unit/test_no_duplicates.py`

**Descripción**: La app nueva solo implementa las funciones canónicas v2; `legacy/` no se modifica.

**Subtareas**:
- [ ] Implementar solo las versiones canónicas en `analytics/`/`charts/` (no portar v1).
- [ ] Documentar la equivalencia v1→v2 en `docs/migration.md`.

**Criterios de Aceptación**:
- `legacy/boom_crash_quant_analyzer.py` permanece intacto (SHA-256 en `tests/fixtures/legacy_sha256.json`).
- El código nuevo no contiene definiciones duplicadas (chequeo AST).
- Tests: `test_no_duplicates.py` (AST).

---

### T043 — No Portar export_csv (ExportService en Memoria)
**Prioridad**: P0 | **Dependencias**: T034, T040 | **Archivos**: `services/export_service.py`, `legacy/` intacto

**Descripción**: `export_csv` no se porta; `ExportService` genera JSON/CSV/ZIP en `BytesIO`. `legacy/` no se modifica.

**Subtareas**:
- [ ] Verificar que `ExportService` cubre JSON/CSV/ZIP en memoria.
- [ ] No portar `export_csv` ni `_safe_name` (este último vive en `utils.py`).
- [ ] No introducir `os`/`pathlib` de escritura en `analytics/`/`services/`.

**Criterios de Aceptación**:
- `grep -n "def export_csv(" src/quantlab/` → 0 resultados (la variante en memoria es `export_csv_bytes`).
- `legacy/boom_crash_quant_analyzer.py` intacto (SHA-256 en `tests/fixtures/legacy_sha256.json`).
- `grep "to_csv|open.*w|Path.write" src/quantlab/` → 0 (excepto tests).
- Tests: `test_forbidden_calls.py` (patrón disco).


## Resumen de Dependencias Críticas

```
T001 → T002, T003, T004, T006, T011, T031
T004 → T008, T019
T005 → T007
T006 → T007, T040
T007 → T009, T019, T020
T008 → T009, T019
T009 → T010, T020, T036
T011 → T012, T013
T012 → T013
T013 → T014, T015, T016, T031, T034, T037
T014 → T016
T015 → T016
T016 → T033
T017 → T018, T021, T022, T023, T024, T025, T026, T027, T028
T018 → T023, T024, T025, T027, T028
T019 → T033
T021 → T022, T023, T024, T026, T027
T022 → T030
T023 → T030
T024 → T028, T030
T025 → T030
T026 → T030
T027 → T030
T028 → T030
T029 → T030
T031 → T032, T033, T038
T032 → T033, T035, T041
T033 → T039
T034 → T043
```

---

## Estado de Tareas

| ID | Descripción | Prioridad | Estado | Encargado |
|----|-------------|-----------|--------|-----------|
| T000 | Project Bootstrap | P0 | ✅ Completado | — |
| T001 | Config Pydantic | P0 | ✅ Completado | — |
| T002 | Errores | P0 | ✅ Completado | — |
| T003 | Utils | P1 | ✅ Completado | — |
| T004 | InstrumentState | P0 | ⬜ Pendiente | — |
| T005 | RateLimiter | P0 | ⬜ Pendiente | — |
| T006 | Deriv Allowlist | P0 | ⬜ Pendiente | — |
| T007 | DerivClient | P0 | ⬜ Pendiente | — |
| T008 | RAMCache | P0 | ⬜ Pendiente | — |
| T009 | MarketDataService | P0 | ⬜ Pendiente | — |
| T010 | QualityReport | P1 | ⬜ Pendiente | — |
| T011 | Domain Models | P0 | ⬜ Pendiente | — |
| T012 | ReactionZone | P0 | ⬜ Pendiente | — |
| T013 | Snapshot Models | P0 | ⬜ Pendiente | — |
| T014 | SnapshotStore | P0 | ⬜ Pendiente | — |
| T015 | FeatureRegistry | P1 | ⬜ Pendiente | — |
| T016 | SnapshotService | P0 | ⬜ Pendiente | — |
| T017 | AnalyticsEngine | P0 | ⬜ Pendiente | — |
| T018 | Indicators | P1 | ⬜ Pendiente | — |
| T019 | StreamManager | P0 | ⬜ Pendiente | — |
| T020 | Spike Script | P1 | ⬜ Pendiente | — |
| T021 | Stats | P0 | ⬜ Pendiente | — |
| T022 | EVT + fit_evt_gpd | P0 | ⬜ Pendiente | — |
| T023 | Events | P0 | ⬜ Pendiente | — |
| T024 | Zones | P0 | ⬜ Pendiente | — |
| T025 | Volatility + Regimes | P0 | ⬜ Pendiente | — |
| T026 | Survival | P1 | ⬜ Pendiente | — |
| T027 | Diagnostics | P1 | ⬜ Pendiente | — |
| T028 | WalkForward | P1 | ⬜ Pendiente | — |
| T029 | Fixtures | P0 | ⬜ Pendiente | — |
| T030 | Paridad Tests | P0 | ⬜ Pendiente | — |
| T031 | API Schemas | P0 | ⬜ Pendiente | — |
| T032 | FastAPI App | P0 | ⬜ Pendiente | — |
| T033 | Routes + WS | P0 | ⬜ Pendiente | — |
| T034 | ExportService | P1 | ⬜ Pendiente | — |
| T035 | Run Script | P1 | ⬜ Pendiente | — |
| T036 | Warm Cache | P2 | ⬜ Pendiente | — |
| T037 | Verify Contracts | P1 | ⬜ Pendiente | — |
| T038 | Contract Tests API | P1 | ⬜ Pendiente | — |
| T039 | Contract Tests WS | P1 | ⬜ Pendiente | — |
| T040 | Forbidden Calls | P0 | ⬜ Pendiente | — |
| T041 | Performance Tests | P1 | ⬜ Pendiente | — |
| T042 | Consolidar Duplicados (legacy intacto) | P0 | ⬜ Pendiente | — |
| T043 | No portar export_csv | P0 | ⬜ Pendiente | — |

---

## Próximos Pasos Post-Fase 1

1. **Ejecutar `/speckit.analyze`** para validar coherencia con spec padre `001-crash-boom-dashboard`
2. **Ejecutar `/speckit.converge`** hasta "Converged"
3. **Fase 2**: Gráficos Plotly backend (`charts/` generando `fig.to_json()`)
4. **Fase 3**: Downsampling LTTB + cache figuras por `config_hash`
5. **Fase 4**: Frontend Vanilla JS + Plotly.js local (servido por FastAPI)
6. **Fase 5**: Páginas Metodología/Sistema + Export UI
7. **Fase 6**: Convergencia final + Demo