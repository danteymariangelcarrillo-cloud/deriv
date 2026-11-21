# Tasks — Migration Foundation (Fase 1)

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

Desglose granular de tareas para la Fase 1. Cada tarea es una unidad de trabajo atómica, con dependencias explícitas y criterios de aceptación.

**Convención IDs**: `T{###}` — secuencial por milestone. **Prioridad**: P0 (bloqueante), P1 (crítico), P2 (importante), P3 (nice-to-have).

**Nota de secuenciación (2026-11-16)**: T000 (bootstrap) precede a T001. T017 se movió a M1C para romper el ciclo T017↔T021; T016 usa un `FakeAnalyticsEngine` hasta M1C. Los fixtures se generan en T029.

**Estado real (2026-11-20)**: **Fase 1 completa (T000-T043)**. M1A-M1D cerrados: config/estado/rate limit/allowlist/cliente Deriv/cache RAM/market data; domain + SnapshotStore + FeatureRegistry + SnapshotService + StreamManager; analytics con paridad legacy (EVT, eventos, zonas, volatilidad/regímenes, supervivencia, diagnósticos, walk-forward) y fachada `AnalyticsEngine`; app FastAPI con 7 rutas REST, WS `/api/ws/{symbol}`, export JSON/CSV/ZIP en memoria, scripts (`run.ps1`, `warm_cache.py`, `verify_contracts.py`) y benchmarks. Verificación: 620 tests recolectados (`uv run pytest`, 2 skip live), `ruff check` y `ruff format --check` limpios. T035 comprobado en PowerShell real: health OK en 2,1 s y fallback de puerto 8065→8066; T041 warm start real contra Deriv OK (<30 s).

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
- [x] `InstrumentState` dataclass con `slots=True`
- [x] Buffers `deque` con `maxlen`: historical_ticks, live_ticks, candles_by_tf, directional_returns
- [x] Configs: analyzer, model, gates, ram + config_hash
- [x] Snapshot: `snapshot: InstrumentSnapshot | None`, `snapshot_version: int`
- [x] Live stream: `ws_task`, `live_queue`, `dropped_ticks`, `latency_ms`, `connected`, `active_endpoint`
- [x] Locks: `recompute_lock: asyncio.Lock`
- [x] Métodos helper: `get_candles_df(tf)`, `get_ticks_df()`, `append_live_tick()`

**Notas de implementación (T004)**:
- Los `maxlen` de los cuatro buffers se derivan de `RAMConfig` en `__post_init__` (no de constantes duplicadas), de modo que `config_hash` refleje los límites efectivos.
- `candles_by_tf` se inicializa con las 6 temporalidades de `TIMEFRAMES`; `get_candles_df` rechaza cualquier otra.
- El campo de latencia se nombra `last_latency_ms`, como en `data-model.md §3.1` (la subtarea decía `latency_ms`).
- `snapshot` se tipa con `TYPE_CHECKING` hacia `quantlab.domain.snapshot` (T012); no se crea ese módulo aquí.
- `TickRecord` y `CandleRecord` (data-model §3.2) viven en `state.py`.

**Criterios de Aceptación**:
- Buffers nunca superan `maxlen` (verificar con test de estrés)
- `recompute_lock` previene recálculos concurrentes
- Tests: `test_state.py` — buffers, locks, serialización

---

### T005 — TokenBucket Rate Limiter
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `data/rate_limiter.py`

**Descripción**: Token bucket thread-safe para asyncio (capacity=30, refill=2.5/s = 150/min).

**Subtareas**:
- [x] `TokenBucket` class con `asyncio.Lock` interno
- [x] `acquire(tokens=1)`: bloquea hasta tener tokens
- [x] `try_acquire(tokens=1)`: no bloquea, retorna bool
- [x] `state` property → `RateLimitState` (tokens, capacity, refill, calls_last_minute, waited_ms, budget)
- [x] Integración con `DerivClient.request()` — auto-acquire antes de cada request

**Criterios de Aceptación**:
- 150 req/min sostenido, burst 30
- `waited_ms_last_request` mide latencia por rate limit

**Notas de implementación (T005)**:
- El bucket arranca lleno: `capacity=30` permite la ráfaga inicial; un token a `2.5/s` tarda 0.4 s (verificado en `test_rate_limit.py`).
- `RateLimitState` sigue `contracts/service-interfaces.md §2`: `refill_per_second` (no `refill`), `budget_per_minute` y `waited_ms_last_request: int`.
- `acquire` espera fuera del lock y re-comprueba; `try_acquire` no contiene `await`, por lo que su sección crítica es atómica en el event loop.
- `calls_last_minute` usa ventana móvil de 60 s sobre un `deque` de marcas de tiempo; el límite sostenido se verifica por construcción (capacidad + refill) y por el test de ráfaga/espera, no con 60 s reales.
- La integración con `DerivClient.request()` queda pendiente de T007 (el cliente no existe).
- Tests: `test_rate_limiter.py` — carga 5 min ≤ 150/min

---

### T006 — Deriv Allowlist
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `data/deriv_calls.py`

**Descripción**: Única fuente de truth para payloads permitidos a Deriv (allowlist estricta).

**Subtareas**:
- [x] `ALLOWED_REQUESTS` dict con 6 entradas: active_symbols, ticks_history_candles, ticks_history_ticks, subscribe_ticks, ping, time
- [x] `build_request(template: str, **params) -> dict`: valida template existe, interpola params
- [x] `validate_response(response: dict) -> bool`: verifica `msg_type` en allowlist
- [x] Test: `test_forbidden_calls.py` escanea código y falla si hay `msg_type` no permitido

**Criterios de Aceptación**:
- Cualquier request no construido desde `ALLOWED_REQUESTS` → error en test
- Payloads coinciden con Deriv API pública documentada
- Tests: `test_deriv_calls.py`

**Notas de implementación (T006)**:
- Las 6 plantillas replican literalmente `contracts/forbidden-calls.md §8`; el test comprueba igualdad exacta para detectar cualquier deriva futura.
- El contrato usa `{int}` en cuatro claves, por lo que el nombre del parámetro es la **clave del payload** (`ticks_history`, `count`, `granularity`, `start`, `end`, `ticks`); documentado en el docstring de `build_request`.
- Validación estricta: plantilla inexistente, parámetro faltante o sobrante y símbolo vacío → `ValueError`; tipo incorrecto → `TypeError`. Se rechaza `bool` como entero y no se permite colar `subscribe` en un histórico.
- `ALLOWED_RESPONSE_TYPES` es independiente de la allowlist de request: `active_symbols`, `candles`, `history`, `ticks`, `ping`, `time`. `msg_type == "error"` devuelve `False` (no es respuesta de datos).
- `build_request` devuelve dicts nuevos: mutar el resultado no altera `ALLOWED_REQUESTS`.
- El escaneo estático de llamadas prohibidas pertenece a T040 (`tests/contract/test_forbidden_calls.py`); aquí solo se dejó preparado el módulo (verificado con las regex de §7: 0 violaciones).

---

### T007 — DerivClient Async
**Prioridad**: P0 | **Dependencias**: T002, T005, T006 | **Archivos**: `data/deriv_client.py`

**Descripción**: Cliente WebSocket asíncrono persistente con rate limiting, fallback endpoints, reconexión exponencial.

**Subtareas**:
- [x] `__init__`: endpoints list, rate_limiter, timeout, retries
- [x] `request(payload, expected_types, expected_key)`: req_id, timeout, fallback solo en errores transporte
- [x] `subscribe_ticks(symbol)`: async generator que yield ticks; maneja reconexión interna
- [x] `get_active_symbols()`: cache TTL 1 hora
- [x] `resolve_symbol(target, override)`: migración exacta lógica original
- [x] `connection_state` property → `ConnectionState`
- [x] `_is_transport_failure()`: detección 520, 502, 503, 504, timeout, connection reset
- [x] Reconexión: backoff 1s, 2s, 4s, 8s, max 15s + jitter ±0.5s

**Criterios de Aceptación**:
- `scripts/spike_deriv.py` conecta y recibe ticks en < 5s
- Rate limiter respeta 150/min (test carga)
- Fallback funciona si primer endpoint falla con 520
- Reconexión automática tras corte 30s
- Tests: `test_deriv_client.py` (mock WS), `test_deriv_client_live.py` (@pytest.mark.live)

**Notas de implementación (T007)**:
- Una sola conexión WebSocket persistente multiplexa solicitudes por `req_id` y suscripciones por `subscription.id`; sirve a CRASH500 y BOOM1000 sin abrir una conexión por símbolo. `close()` cancela lector y pendientes.
- `request()` valida el payload contra `ALLOWED_REQUESTS` (claves exactas y valores literales) **antes** de pedir token al bucket; cada envío consume un token. El fallback solo ocurre ante errores de transporte y respeta `retries_per_endpoint`; un error de negocio no cambia de endpoint.
- `resolve_symbol` es **async** (desviación necesaria: el contrato lo declara síncrono, pero consulta `active_symbols` por WebSocket). El algoritmo se portó 1:1: normalización alfanumérica, override validado contra códigos activos, ranking exacto/prefijo/subcadena y error con nombres relacionados.
- `subscribe_ticks` devuelve `dict | None` (el contrato anota `dict` pero su descripción exige `None` al reconectar). El backoff es 1, 2, 4, 8, máx. 15 s ± 0.5 s; el jitter usa `random.Random(0)` para ser determinista en tests (evita RNG global sin semilla).
- Reintentos intra-endpoint sin `sleep` (solo cambia de endpoint de inmediato); el backoff con espera aplica a la reconexión del stream, como pide el contrato.
- `_WS_CONNECT` es un punto de indirección para tests; en producción resuelve perezosamente `websockets.asyncio.client.connect`. `last_error` conserva el último fallo como historial aunque la conexión se recupere.
- Pendiente de validación real: `scripts/spike_deriv.py` es T020 y no existe, así que el criterio "conecta y recibe ticks en <5s" no se puede verificar aún; `test_deriv_client_live.py` se omite por la misma razón (sin spike ni red en tests). Los 18 tests usan un WebSocket simulado.

---

### T008 — RAMCache
**Prioridad**: P0 | **Dependencias**: T004 | **Archivos**: `data/cache.py`

**Descripción**: Caché exclusiva en RAM con buffers circulares y evicción LRU global.

**Subtareas**:
- [x] `RAMCache` class con `ram_config: RAMConfig`
- [x] `append_ticks(symbol, ticks)` → retorna n_descartados
- [x] `append_live_tick(symbol, tick)` → retorna bool (descartado por maxlen)
- [x] `append_candles(symbol, tf, candles)` → retorna n_descartados
- [x] Getters: `get_ticks`, `get_live_ticks`, `get_candles` → list[Record]
- [x] Live queue: `enqueue_live`, `dequeue_live` → bool/Record
- [x] `check_ram_and_evict(rss_mb)`: si > warning → LRU evict velas TF menos usado; si > critical → agresivo
- [x] `get_stats(symbol)` → `CacheStats`
- [x] Monitor background task: cada 30s llama `check_ram_and_evict(psutil.Process().memory_info().rss / 1e6)`

**Criterios de Aceptación**:
- Buffers `deque` evicción automática FIFO
- `dropped_ticks` incrementa solo si `live_queue.full()`
- RAM monitor libera memoria bajo presión
- Tests: `test_ram_cache.py` — evicción, stats, monitor

**Notas de implementación (T008)**:
- Política LRU global sobre buffers de velas `(símbolo, timeframe)`: cada `append_candles`/`get_candles` marca acceso con una secuencia entera interna (no usa relojes). Warning: evicta **un** buffer no vacío (el menos reciente) por llamada; el monitor repite cada 30 s. Critical: vacía **todos** los buffers de velas y reduce `figure_cache_size` a la mitad con suelo 5 (data-model §8). Los ticks no se evictan por LRU para no romper analítica.
- El buffer evictado se **vacía**, no se elimina del dict: así `append_candles` puede volver a llenarlo (regresión cubierta por test).
- `check_ram_and_evict` devuelve MB enteros liberados estimados con `sys.getsizeof` de los registros (si libera <1 MB devuelve 0, por contrato `int`).
- `CacheStats.ram_usage_mb` es una **estimación por símbolo** (`sys.getsizeof` de buffers y registros); no incluye el contenido de `live_queue`, solo el objeto cola.
- `append_directional_return` es una extensión mínima: el contrato incluye `directional_returns` en `CacheStats` pero no expone mutador; T019 lo necesita para que el campo no sea siempre 0.
- `replace_candles` es otra extensión mínima, añadida durante T009: `warm_cache(force=True)` necesita sustituir un buffer sin duplicar velas y el contrato no ofrecía mutador de reemplazo.
- Monitor: `start_monitor(interval=30.0)` / `stop_monitor()` (las subtareas lo exigen aunque el contrato §4 no fije firma). Un fallo del monitor se propaga en `stop_monitor` en vez de quedar sin observar. `stop_monitor` es idempotente y `start_monitor` rechaza doble arranque.
- CERO DISCO: `cache.py` no importa `pathlib`/`os` ni abre archivos.

---

### T009 — MarketDataService
**Prioridad**: P0 | **Dependencias**: T002, T007, T008 | **Archivos**: `data/market_data_service.py`

**Descripción**: Descarga velas paginadas, normalización OHLC, reporte de calidad. **Sin persistencia**.

**Subtareas**:
- [x] `get_candles(symbol, tf, start, end, limit=1000)` → `(DataFrame, DataQualityReport)`
- [x] Paginación: loop con `page_end = oldest - 1`, max 1000/page, rate limited
- [x] `_clean_candles()`: migración exacta L516-L538 (validación OHLC, deduplicación, UTC)
- [x] `get_tick_history(symbol, count=5000, start=None)` → DataFrame
- [x] `warm_cache(symbol, timeframes, force=False)`: descarga y llena RAMCache
- [x] `DataQualityReport`: coverage, gaps, duplicates, invalid_ohlc, stale_seconds, dropped_ticks

**Criterios de Aceptación**:
- Velas normalizadas: columnas `datetime, epoch, Open, High, Low, Close`
- Quality report: `status` ok/warn/fail según umbrales
- **Cero archivos escritos** — verificado en test
- Tests: `test_market_data.py` (fixtures), `test_market_data_live.py` (@pytest.mark.live)

**Notas de implementación (T009)**:
- **Dependencia invertida con T010**: `DataQualityReport` se define en `data/quality.py` como modelo Pydantic **frozen** validado contra `snapshot.schema.json`; T009 solo adelanta el modelo porque su contrato lo retorna. `calculate_quality` y sus tests quedan íntegramente para T010 (no se duplica lógica).
- `limit` es el **tamaño de página** (1..1000), no el total: la paginación sigue hacia atrás hasta `start_epoch`, una página incompleta, fin de progreso o un tope derivado del rango (máx. 2000 páginas) que añade una nota de truncado.
- `_clean_candles` es copia 1:1 de L516-L538. `_audit_raw` replica el mismo orden (recorte → dedupe → validación OHLC) solo para contar `duplicates_removed` e `invalid_ohlc`, que el DataFrame limpio ya no permite reconstruir.
- `get_candles` **no** muta `RAMCache` (contrato: solo retorna DataFrame + reporte); la escritura en caché ocurre en `warm_cache`, que usa los días de histórico de data-model §6.1 (30/90/180/365/730/1825).
- `warm_cache(force=True)` reemplaza el buffer usando la extensión mínima `RAMCache.replace_candles` (añadida a T008) para no duplicar velas; `force=False` omite TFs ya cacheadas.
- Ticks: la allowlist §8 fija `end: latest` y no admite `start`, por lo que `start_epoch` se aplica como **filtro posterior** y nunca se añade al payload. `sequence` se regenera 0..n-1 salvo que Deriv lo entregue; se deduplica por epoch conservando el último y se ordena ascendente.
- Rate limit: el servicio adquiere un token por página además del bucket interno de `DerivClient` (el contrato exige el parámetro `rate_limiter`); el consumo efectivo sigue limitado por ambos buckets.
- Umbrales de `status`: `fail` si no hay velas o cobertura <90 %; `warn` si 90–98 % o hueco crítico (>2 % de barras esperadas en un solo hueco); `ok` si ≥98 % sin hueco crítico. `stale_seconds = max(0, now - last_epoch)`; `dropped_ticks` sale de `RAMCache.get_stats`.
- CERO DISCO verificado cambiando el cwd a `tmp_path` y comprobando que queda vacío tras `warm_cache` y `get_tick_history`. No se importan `pathlib`/`os` en el módulo de producción.
- Test live opt-in con `QUANTLAB_LIVE=1`, marcador `live` registrado en `pyproject.toml`; por defecto se omite (sin red).

---

### T010 — DataQualityReport
**Prioridad**: P1 | **Dependencias**: T009 | **Archivos**: `data/quality.py`

**Descripción**: Modelo Pydantic + lógica de cálculo de calidad de datos.

**Subtareas**:
- [x] `DataQualityReport` Pydantic (ver `data-model.md §3.6`)
- [x] `calculate_quality(df, tf, expected_bars)`: gaps > 1.5×granularidad, duplicados epoch, OHLC inválido
- [x] `stale_seconds = now - last_epoch` si hay datos
- [x] `status`: ok (≥98% coverage, sin gaps críticos), warn ([90,98%) o gaps menores), fail (<90% o sin datos)

**Criterios de Aceptación**:
- Cobertura calculada correctamente
- Status coherente con umbrales
- Tests: `test_quality.py`

**Notas de implementación (T010)**:
- El modelo `DataQualityReport` ya existía desde T009 (frozen, `extra="forbid"`, campos obligatorios del schema). T010 lo conserva y añade la función pura `calculate_quality`; no se recreó el modelo.
- `MarketDataService._build_quality_report` ahora **delega** en `calculate_quality`: existe una sola definición de cobertura, huecos y umbrales. `GRANULARITY_SECONDS` se movió a `quality.py` y T009 lo importa (sin duplicar la tabla).
- `calculate_quality` acepta columnas OHLC en minúsculas (payload crudo) o capitalizadas (salida de T009), ignora columnas extra, convierte a numérico, ordena por epoch y deduplica con `keep="last"` (misma semántica que la limpieza legacy L516-L538).
- `duplicates_removed` e `invalid_ohlc` son opcionales: si se informan se usan como conteos ya auditados sobre el payload crudo (ruta T009); si no, se detectan dentro del DataFrame. `received_bars` siempre cuenta las filas únicas y OHLC-válidas resultantes.
- Cobertura (INV-050): `100 * received / expected`, acotada a `[0, 100]`; `0` si `expected_bars == 0`; `missing_bars = max(0, expected - received)`.
- Huecos: salto entre barras consecutivas `> 1.5 × granularidad`; se cuenta cada hueco y las barras faltantes dentro de él. "Hueco crítico" no está definido en el contrato: se adopta operativamente `faltantes > max(3, 2 % de expected)` y se informa en `notes`; el contrato solo permite `warn`, nunca `fail`, por un hueco si la cobertura sigue ≥90 %.
- `status`: `fail` si no hay barras, cobertura <90 % o (opcionalmente) antigüedad > `max_stale_seconds`; `warn` si cobertura `[90, 98)`, hay cualquier hueco o hay OHLC inválido (INV-051/052); `ok` en el resto. Los duplicados por sí solos no bajan el estado si la cobertura final se mantiene.
- `stale_seconds = max(0, now - last_epoch)` y `null` sin datos. `now` es inyectable para tests; `max_stale_seconds` es opcional y por defecto `None`: T009 descarga ventanas históricas y no debe marcarlas "stale". T016 deberá pasar el `stale_seconds` de `RuntimeConfig` para cumplir INV-053 en snapshots.
- Se conservan `notes` y `errors` del llamador; se añaden `DATA_EMPTY` sin barras y `DATA_STALE` al superar la antigüedad máxima. `dropped_ticks` se transporta desde T009.
- Tests: 30 en `test_quality.py` (modelo frozen, columnas, duplicados, tres variantes de OHLC inválido, NaN/Inf, huecos y hueco crítico, umbrales 98/90, bordes, vacío, staleness, validaciones) y 17 en `test_market_data.py` (incluye integración de OHLC inválido con cobertura 100 % → warn).

---

## Milestone M1B — SnapshotService + Live StreamManager (Semana 2)

### T011 — Domain Models (Candle, Tick, EventCandidate)
**Prioridad**: P0 | **Dependencias**: T001, T003 | **Archivos**: `domain/market_data.py`, `domain/events.py`

**Descripción**: Modelos Pydantic para datos de entrada y eventos candidatos.

**Subtareas**:
- [x] `Candle`: epoch, open, high, low, close, tf, source, quality_flags
- [x] `Tick`: epoch, price, received_at, sequence
- [x] `EventCandidate`: todos los campos de `data-model.md §3.4` + UUID5 determinista
- [x] `Instrument` (config, §6.1) + registro por defecto BOOM1000/CRASH500, necesario para el snapshot

**Criterios de Aceptación**:
- Validación estricta (precios > 0, OHLC coherente)
- `event_id` determinista: `uuid5(namespace, f"{symbol}|{tf}|{kind}|{epoch}|{price}")`
- Tests: `test_domain.py`

**Notas de implementación (T011)**:
- `domain/types.py` centraliza los alias literales (`Direction`, `Timeframe`, `Source`, `EventKind`, `ZoneType`, `Stage`, `AnalysisStatus`, `OverallStatus`, `FeatureCategory`) y la base `DomainModel` (`extra="forbid"`, `frozen=True`, `allow_inf_nan=False`).
- `Instrument` aplica INV-001/002: dirección coherente con BOOM1000/CRASH500 y `history_days` con exactamente las seis temporalidades. `DEFAULT_INSTRUMENTS` y `get_instrument()` son el registro por defecto de §6.1.
- `Candle` valida INV-010 (`low <= open, close <= high`) y rechaza precios no finitos; `epoch > 0`. `Tick.received_at` se normaliza a UTC con `ensure_utc_datetime` (un ingenuo se interpreta como UTC).
- `event_id` se rellena automáticamente si no se informa; si se informa, debe ser un UUID canónico. El precio se serializa con `repr(float(...))` para que `123.4` y `123.40` coincidan.
- `EventSeries` exige `n_events >= len(events)`: la lista puede truncarse a 5000 (schema) y `n_events` conserva el total detectado.

---

### T012 — ReactionZone Model
**Prioridad**: P0 | **Dependencias**: T011 | **Archivos**: `domain/zones.py`

**Descripción**: Modelo Pydantic para zonas de reacción con null geométrico, FDR, bootstrap CI.

**Subtareas**:
- [x] `ReactionZone` Pydantic (ver `data-model.md §3.5`)
- [x] `p_null = 0.5` por defecto (réplica exacta); la geometría `b/(a+b)` ≈ 0.4286 es optativa
- [x] `sensitivity`: rate_ambiguous_as_failure_pct, rate_pending_as_failure_pct
- [x] `stage`: in_sample | oos

**Criterios de Aceptación**:
- Serialización JSON sin NaN/Inf (→ null + reason)
- Tests: `test_domain.py`

**Notas de implementación (T012)**:
- Se aplican INV-040/041/042/043 en `model_validator`: geometría (`upper - lower = 2*half_width` con `isclose`), `evaluated_touches = reactions + failures`, `episodes` = suma de las cuatro categorías, y nulos obligatorios cuando `evaluated_touches == 0`.
- `DEFAULT_P_NULL = 0.5` es el default del campo; la corrección geométrica se pasa de forma explícita (optativa) y no se calcula aquí.
- `zone_id` se rellena solo: UUID5 de `symbol|timeframe|zone_type|center_rounded|available_from_epoch` con el centro redondeado a 6 decimales (`compute_zone_id`).
- `ZoneSensitivity` acota las tasas a `[0, 100]` y usa la nota `"Excluidos del denominador principal"`; sus defaults a 0.0 reflejan el caso sin toques evaluados.
- `favourable_ci`/`adverse_ci` son tuplas de dos elementos ordenados; `NaN`/`Inf` se rechazan en la entrada para que el JSON no los arrastre.

---

### T013 — Snapshot Models (InstrumentSnapshot, FeatureStatus, TimeframeAnalysis)
**Prioridad**: P0 | **Dependencias**: T011, T012 | **Archivos**: `domain/snapshot.py`

**Descripción**: Modelos principales de snapshot inmutable (frozen=True).

**Subtareas**:
- [x] `FeatureStatus`: id, name_es, category, status, min_sample, sample_size, reason, ui_path, stage
- [x] `TimeframeAnalysis`: todos los campos `data-model.md §3.7` + `features: dict[analysis_id, FeatureStatus]`
- [x] `InstrumentSnapshot`: `frozen=True`, todos los campos `data-model.md §3.8`
- [x] `OverallStatus`: ready | partial | stale | error (lógica de agregación)

**Criterios de Aceptación**:
- `InstrumentSnapshot` inmutable: `model_config = ConfigDict(frozen=True)`
- `config_hash` y `data_version` incluidos
- Tests: `test_snapshot_models.py` — validación schema, inmutabilidad

**Notas de implementación (T013)**:
- `data-model.md` no tiene §3.7/§3.8; los campos se tomaron de `contracts/snapshot.schema.json`, que es el esquema normativo (§12). Los tests comparan `required`/`properties` de cada `$defs` contra `model_dump(mode="json")` en vez de usar `jsonschema` (no es dependencia del proyecto; T037/T038 podrán añadirlo).
- `domain/results.py` se adelanta para tipar los campos analíticos de `TimeframeAnalysis` (`EVTResult`, `GARCHResult`, `RegimeResult`, `SurvivalResult`, `ACFResult`, `DiagnosticsResult`, `ConditionalResult`, `WalkForwardResult`). T017/T021-T028 los reutilizan; no duplican lógica de cálculo.
- `SnapshotConfig` aplana la configuración del snapshot en `version` + `gates` (reutiliza `GateConfig` de T001) + `analysis` + `runtime`. `analysis.fdr_q = 0.05`, `garch_max_p/q = 1` y `runtime` (150 req/min, 420 MB, 30 s, 120 s) son defaults provisionales: T016 los derivará de `QuantLabConfig` y `RAMConfig`.
- `overall_status` se calcula si no se informa: sin TFs o todas en `error` → `error`; todas `ready` → `ready`; cualquier `stale` → `stale`; resto → `partial`.
- `snapshot_id` es UUID5 de `symbol|generated_at|config_hash` y se rellena solo; `generated_at` se serializa como ISO-8601 UTC con `Z`.
- Inmutabilidad real: las colecciones son tuplas (no listas) y `extra="forbid"`. `FeatureStatus` exige `reason` legible en estados `insufficient`/`error`.
- `app_version` por defecto `0.1.0` (versión del paquete); `schema_version` queda fijo en `1.0.0`.

---

### T014 — SnapshotStore (Atomic Swap)
**Prioridad**: P0 | **Dependencias**: T013 | **Archivos**: `services/snapshot_store.py`

**Descripción**: Almacén en memoria con swap atómico y versionado.

**Subtareas**:
- [x] `SnapshotStore` class con `dict[str, InstrumentSnapshot]` + `dict[str, int]` version
- [x] `get(symbol)` → snapshot (lectura atómica, sin lock por ser referencia inmutable)
- [x] `swap(symbol, snapshot)` → nuevo version (atomic: `self._snapshots[symbol] = snapshot; self._versions[symbol] += 1`)
- [x] `get_version(symbol)` → int
- [x] `clear(symbol)` → limpia entrada

**Criterios de Aceptación**:
- Lectura concurrente nunca ve estado parcial (referencia inmutable)
- Swap atómico: versión incrementa 1
- Tests: `test_snapshot_store.py` — concurrencia, versionado

**Notas de implementación (T014)**:
- Sin locks: la referencia al snapshot inmutable se publica antes que la versión, así que ninguna lectura observa un snapshot a medio construir (INV-M04). `get()` y `get_version()` son llamadas independientes; entre ambas puede ocurrir un swap.
- `swap` valida símbolo no vacío y tipo `InstrumentSnapshot`, y devuelve la nueva versión. `clear` reinicia la versión a 0 (el siguiente swap es 1).
- Extras mínimos: `symbols()` (orden alfabético), `__len__` y `__contains__`, útiles para `/api/health` (T032).
- CERO DISCO: sólo diccionarios en memoria.
- Test de concurrencia con 4 lectores y 300 swaps: ninguna lectura ve un snapshot incompleto; la versión final es 301.

---

### T015 — FeatureRegistry
**Prioridad**: P1 | **Dependencias**: T013 | **Archivos**: `services/feature_registry.py`

**Descripción**: Registro central de análisis + gates por temporalidad.

**Subtareas**:
- [x] `FeatureRegistry` class con `gate_config: GateConfig`
- [x] `register_all()`: crea `FeatureStatus` para 14 análisis base (evt.gpd, volatility.garch, volatility.regimes, zones.reaction, survival.kaplan_meier, diagnostics.residuals, walkforward.validation, events.candle, events.tick, events.conditional, autocorrelation, heatmap.price, heatmap.events, data.quality)
- [x] `check_gate(analysis_id, tf, sample_size)` → `FeatureStatus` con status ready/insufficient/not_applicable
- [x] `get_ui_path(analysis_id)` → ruta frontend (ej. `/i/BOOM1000/distribution`)

**Criterios de Aceptación**:
- Gates aplicados correctamente (ej. EVT insufficient si n_excess < 30)
- `FeatureStatus` incluye razón legible si insufficient
- Tests: `test_feature_registry.py`

**Notas de implementación (T015)**:
- El catálogo (14 `AnalysisSpec` con id, `name_es`, categoría, panel, stage y fuente del gate) es estático; los umbrales se leen de `GateConfig` en cada evaluación, de modo que `set_gate_config` aplica el hot-reload sin reconstruir el registro.
- `check_gate(..., symbol=...)` y `get_ui_path(..., symbol=...)` reciben el símbolo porque `ui_path` es una ruta real (`/i/BOOM1000/distribution`), no una plantilla.
- `register_all(symbol, tf)` devuelve los 14 `FeatureStatus` en estado `pending` con su `min_sample`; `SnapshotService` los sustituye por el resultado real de cada gate.
- `not_applicable` se expone con el flag `applicable=False` (por ejemplo `events.tick` sin ticks en memoria).
- Categorías: `heatmap.price`, `heatmap.events` y `data.quality` usan `data` porque el enum del schema no incluye `heatmap`.
- `walkforward.validation` se gatea por `wf_min_folds` (folds disponibles), no por `wf_min_days`; `wf_min_days` es una cota de historia que valida el cálculo walk-forward (M1C). Stage `oos`.
- Tests: 14 en `test_feature_registry.py` (catálogo, umbrales, hot-reload, bordes 29/30, razón legible, `not_applicable`, `register_all`, entradas inválidas).

---

### T016 — SnapshotService (Orquestador)
**Prioridad**: P0 | **Dependencias**: T013, T014, T015 | **Archivos**: `services/snapshot_service.py`

**Descripción**: Orquestador principal: config → gates → analytics → snapshot frozen → atomic swap. En M1B se testea con un `FakeAnalyticsEngine`; la integración real con T017 se valida en M1C.

**Subtareas**:
- [x] `build_snapshot(symbol, timeframes=None)`:
  1. Para cada TF: verifica gates via `FeatureRegistry`
  2. Llama `AnalyticsEngine` para cada análisis con gate ready
  3. Construye `TimeframeAnalysis` con resultados + `FeatureStatus`
  4. Construye `InstrumentSnapshot` frozen
  5. `SnapshotStore.swap(symbol, snapshot)`
- [x] `recompute_timeframe(symbol, tf)`: recalcula solo una TF (usa `InstrumentState.recompute_lock`)
- [x] `update_config(symbol, config_update)`:
  1. Merge + valida Pydantic
  2. Nuevo `config_hash`
  3. Si unchanged → 304
  4. Actualiza `InstrumentState.config` + `config_hash`
  6. `snapshot_version += 1` (invalida)
  7. Lanza `recompute_timeframe` background para TFs afectados (ThreadPoolExecutor)
  8. Push WS `config_updated` al terminar cada TF
- [x] `reset_config(symbol)`: restaura defaults + recalcula

**Criterios de Aceptación**:
- Gates evitan computar análisis sin muestra mínima
- Config hot-reload: respuesta inmediata, recálculo background
- WS push al terminar cada TF
- Tests: `test_snapshot_service.py` — build, recompute, config update

**Notas de implementación (T016)**:
- Protocolo del motor (provisional para M1B): `sample_size(analysis_id, candles=, ticks=)` y `compute(analysis_id, *, symbol, timeframe, candles, ticks, config)`. El gate se evalúa con el tamaño de muestra **antes** de computar; los tests verifican que `compute` no se llama cuando es `insufficient`.
- Los datos se piden a `MarketDataService.get_candles` con la ventana `Instrument.history_days[tf]`, se normalizan las columnas (`Open`→`open`) y se copian a `state.candles_by_tf` y a `RAMCache.replace_candles`, de modo que API, stream y snapshot ven los mismos buffers.
- Calidad: en la ruta normal se usa el `DataQualityReport` de T009; si la descarga falla se compone un reporte vacío y se registra `ErrorRecord(DATA_EMPTY)`. `TimeframeAnalysis.status` es `error` sin barras o con calidad `fail` (salvo datos rancios → `stale`), `ready` si algún análisis lo está, `insufficient` si ninguno y `error` si además alguno falló.
- Aislamiento de fallos: una excepción de gate o de cómputo sólo marca ese `FeatureStatus` como `error` y añade un `ErrorRecord(COMPUTE_FAILED)`; el resto del snapshot se publica igual.
- El servicio define `ConfigUpdateRequest`/`ConfigUpdateResponse` (T037 los reexportará). `update_config` valida con `QuantLabConfig.updated`, compara `config_hash` (idempotente → `unchanged`), actualiza estado + registro de gates e invalida con `snapshot_version += 1`. Un cambio sólo de `ram` no recalcula.
- Desviación del contrato: el recálculo background usa `asyncio.create_task` en vez de `ThreadPoolExecutor`, porque `recompute_lock` y el cliente Deriv están ligados al event loop. `wait_for_background()` permite a tests y al apagado esperar; el push WS se inyecta como `event_sink` opcional (`ConfigUpdatedEvent`), que T034 conectará al hub.
- Desviación del contrato: `__init__` acepta `states=` (keyword) con el registro de `InstrumentState`, que el stub no incluía, y `clock=` para tests deterministas. `cache` se usa para `cache_bytes` y para reflejar las velas descargadas.
- Tests: 15 en `test_snapshot_service.py` (build completo, gates, columnas capitalizadas, fallos aislados, datos vacíos, config efectiva, recompute aislado, update/reset, idempotencia, RAM-only, gates en caliente, errores de validación).

---

### T018 — Indicators (ATR, Returns)
**Prioridad**: P1 | **Dependencias**: T011 | **Archivos**: `analytics/indicators.py`

**Descripción**: Indicadores técnicos puros (migración exacta + vectorización).

**Subtareas**:
- [x] `add_atr(df, period=14)`: migración exacta L617-L624
- [x] `log_returns(prices)`: `np.log(prices).diff()`
- [x] `directional_returns(returns, direction)`: `returns.clip(lower=0)` si up, `(-returns).clip(lower=0)` si down
- [x] `rolling_quantile(series, window, quantile, min_periods)`: `series.shift(1).rolling(window, min_periods).quantile(quantile)`

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- Vectorizado (sin loops Python)
- Tests: `test_indicators_parity.py`

**Notas de implementación (T018)**:
- `add_atr` es copia línea a línea de `Analyzer.add_atr` (legacy L617-L624): `prev_close = Close.shift(1)`, `true_range = max(High-Low, |High-prev|, |Low-prev|)` con `skipna` por defecto y media simple `rolling(period, min_periods=period)`. Replica también el `NaN` inicial y los `period-1` primeros ATR nulos; no se muta el DataFrame (se copia).
- `log_returns` acepta `Series`, `Index` o `ndarray` y devuelve una `Series` con `np.log(...).diff()` (primer valor `NaN`). Es la definición que pide la tarea; el prototipo usaba `pct_change()*100` en los análisis originales, que T021-T028 migrarán según su propia especificación.
- `directional_returns` no muta la entrada y valida `direction`; coincide con la expresión del prototipo en `detect_tick_extremes` (`clip(lower=0)` para up, retorno invertido para down).
- `rolling_quantile` aplica `shift(1)` para excluir la barra actual (sin fuga temporal) y valida `window >= 1`, `1 <= min_periods <= window` y `quantile en (0, 1]`.
- Se creó el paquete `src/quantlab/analytics/` (vacío hasta ahora); `__init__.py` reexporta los cuatro indicadores y alojará el `AnalyticsEngine` de T017 en M1C.
- Tests: 22 en `test_indicators_parity.py` — ATR contra una implementación escalar independiente con `atol=1e-10` en varios periodos, no mutación/columnas, `log_returns` vs `np.log().diff()`, recorte direccional up/down vs expresión original, cuantiles sin fuga temporal y validación de parámetros.

---

### T019 — StreamManager (Live Ticks)
**Prioridad**: P0 | **Dependencias**: T007, T004, T008 | **Archivos**: `live/stream_manager.py`

**Descripción**: WebSocket Deriv → asyncio.Queue → consumer task → `_process_live_tick` (algoritmo **exacto** original).

**Subtareas**:
- [x] `StreamManager` class con `deriv_client`, `instrument_state`, `cache`
- [x] `start(symbol)`:
  1. Limpia `live_ticks`, `live_queue`, `dropped_ticks`, `directional_returns`
  2. Warm-up: llena `directional_returns` con histórico (si existe, maxlen=10k)
  3. Lanza `_consumer_loop` task
  4. Lanza `_ws_loop` task (reconexión interna)
- [x] `stop(symbol)`: cancela tasks, cierra WS, drena queue
- [x] `_consumer_loop`: `while not stop_event: tick = await queue.get(); _process_live_tick(tick); queue.task_done()`
- [x] `_process_live_tick(tick)`: **MIGRACIÓN EXACTA** de `boom_crash_quant_analyzer.py` L1666-L1708:
  - `price = float(tick["quote"])`, `epoch = int(tick["epoch"])`
  - `signed_return = (price / last_price - 1) * 100`
  - `directional = max(0, signed_return if up else -signed_return)`
  - `directional_returns.append(directional)`
  - Si `len(directional_returns) >= 300`: `threshold = quantile(directional_returns, tick_event_quantile)`
  - `is_event = directional > 0 and directional >= threshold`
  - Si evento: append a `live_events`, log, `ticks_since_event = 0`
  - Else: `ticks_since_event += 1`
- [x] `_ws_loop`: `websockets.connect` con reconexión exponencial, heartbeat ping/pong 20s
- [x] `get_status(symbol)` → `StreamState`

**Criterios de Aceptación**:
- Algoritmo `_process_live_tick` **idéntico** al original (paridad 1e-10 en test unitario)
- `dropped_ticks` incrementa solo si `live_queue.full()`
- Reconexión automática tras corte
- Tests: `test_live_stream.py` (mock WS + algoritmo), `test_live_stream_live.py` (@pytest.mark.live)

**Notas de implementación (T019)**:
- Desviación deliberada en `_ws_loop`: consume `DerivClient.subscribe_ticks` (T007) en lugar de abrir su propio `websockets.connect`. Esa corrutina ya implementa reconexión exponencial con jitter (1-15 s), heartbeat 20/20 s, allowlist de mensajes y rate limit; abrir un segundo socket las eludiría. El marcador `None` del cliente se traduce a `connected=False` y la sesión sigue con el siguiente tick. El cierre del cliente (`close()`) termina el `async for`.
- El constructor acepta un `InstrumentState` o un mapping símbolo→estado (el contrato tipaba uno solo pero `start/stop/get_status` son por símbolo); los tests cubren ambos. CERO DISCO y sin hilos.
- `InstrumentState` sigue siendo la fuente canónica: `state.append_live_tick` encola y sólo suma `dropped_ticks` si la cola está llena (INV-M05). `RAMCache` refleja los ticks aceptados y los retornos direccionales para `CacheStats`; no se duplica el contador de descartes.
- Warm-up: `pct_change()*100` sobre `historical_ticks` + `directional_returns` de T018, hasta `RAMConfig.max_directional_returns` (10 000 por defecto). Es la misma fórmula que usa `_process_live_tick`, para que el umbral sea comparable con el stream.
- Contadores: `received` = mensajes tick válidos recibidos del WS; `processed` = ticks aceptados por el algoritmo (`price` finito y > 0); `last_tick_epoch` = último epoch procesado; `latency_ms` = p50 de `DerivClient.connection_state` (sólo si conectado).
- `live_events` es un entero como exige `StreamState`; el evento se registra con `structlog` (precio, retorno, umbral previo, ticks desde el anterior). El payload detallado para el push WebSocket queda para T034.
- Si el stream termina solo (fallo o cierre del cliente), `_ws_loop` marca `running=False` y activa `stop_event` para que el consumidor no quede sondeando; un `start` posterior cancela tasks residuales y reinicia la sesión.
- Tests: 13 en `test_live_stream.py` (paridad 1e-10 del algoritmo con sesión sintética de 400 ticks, umbral sólo con retornos previos, dirección Crash, precios inválidos, warm-up, pipeline WS→cola→consumidor, mensajes incompletos/ilegibles, marcador de reconexión, cola llena con `dropped_ticks == 1`, idempotencia/reinicio, varios símbolos) y 1 live opt-in en `test_live_stream_live.py`.

---

### T020 — Spike Deriv Script
**Prioridad**: P1 | **Dependencias**: T007 | **Archivos**: `scripts/spike_deriv.py`

**Descripción**: Script de diagnóstico de conexión Deriv.

**Subtareas**:
- [x] Conecta a endpoint activo, mide latencia handshake
- [x] Lista símbolos activos, filtra BOOM1000/CRASH500
- [x] Descarga 5 velas 5min + 5 ticks muestra
- [x] Suscripción 30s: cuenta ticks recibidos, eventos detectados
- [x] Imprime errores clasificados (transport, rate limit, symbol not active)
- [x] Exit code 0 si OK, 1 si error

**Criterios de Aceptación**:
- Ejecutable standalone: `uv run python scripts/spike_deriv.py`
- Output legible con timestamps
- Tests: manual (requiere red)

**Notas de implementación (T020)**:
- `scripts/spike_deriv.py` ejecuta las cuatro etapas en orden: (1) `get_active_symbols` cronometrado como handshake + primera petición, con p50 de `connection_state`; (2) filtrado de `BOOM1000`/`CRASH500` sobre `underlying_symbol`/`symbol`; (3) 5 velas de 5 min vía `MarketDataService.get_candles` con su `DataQualityReport`, más 5 ticks de muestra; (4) 30 s de `StreamManager.start`, reportando `received`, `processed`, `live_events`, `dropped_ticks`, `queue_depth` y `warmup_complete`.
- Todas las líneas llevan timestamp UTC (`log()` con `flush=True`). No escribe en disco; sólo stdout.
- Errores clasificados con `classify()`: `timeout`, `rate_limit`, `symbol_not_active`, `deriv_api`, `transport`, `data`, `quantlab`, `unexpected`. Se comprueba `DerivTimeoutError` **antes** que `DerivTransportError` porque hereda de ella; sin ese orden la etiqueta `timeout` era inalcanzable (corregido durante la verificación).
- Exit code 0 sólo si todas las etapas terminan con datos; 1 ante cualquier error, incluido `KeyboardInterrupt`, ausencia de velas/ticks o una suscripción sin ticks. `client.close()` se ejecuta siempre en `finally`.
- Símbolo de suscripción: `BOOM1000` si está activo; si no, el primero de los objetivos presentes.
- Verificación en este entorno: `ruff check scripts` limpio, `py_compile` OK y comprobación de los helpers (`classify`, `active_codes`, `build_state`). La ejecución real **no pudo completarse aquí**: la red saliente del sandbox deja la conexión WebSocket bloqueada sin respuesta (ni siquiera se imprimió la primera etapa en 60-150 s); T020 especifica prueba manual con red, así que queda pendiente de ejecutar en un entorno con acceso a Deriv.
- No se añaden tests unitarios: la tarea indica explícitamente "Tests: manual (requiere red)".

---

## Milestone M1C — Analytics Core + Funciones Faltantes (Semana 3)

### T021 — Stats (Wilson, Binomial, BH-FDR, Bootstrap)
**Prioridad**: P0 | **Dependencias**: Ninguna | **Archivos**: `analytics/stats.py`

**Descripción**: Funciones estadísticas base con paridad 1e-10.

**Subtareas**:
- [x] `wilson_score_interval(successes, trials, alpha=0.05)` → migración exacta L72-L83
- [x] `binomial_test_pvalue(successes, trials, p_null, alternative='greater')` → migración exacta L86-L98 (compat SciPy ≥1.11 y <1.11)
- [x] `benjamini_hochberg_fdr(pvalues, alpha=0.05)` → migración exacta L101-L116
- [x] `bootstrap_ci(...)`: usa `np.random.Generator(seed)` local; la paridad con legacy se valida por cercanía estadística (RNG distinto)

**Criterios de Aceptación**:
- **Paridad 1e-10** vs original para todos los casos de test
- `bootstrap_ci` determinístico con seed
- Tests: `test_stats_parity.py` (parametrizado con casos edge)

**Notas de implementación (T021)**:
- `src/quantlab/analytics/stats.py` replica las cuatro funciones del prototipo (L72-L116 y L171-L183) como funciones puras; se exportan también desde `quantlab.analytics`.
- `wilson_score_interval`: fórmula Wilson idéntica (``z = norm.ppf(1 - alpha/2)``, centro y semi-ancho divididos por ``1 + z²/n``) con truncado a ``[0, 1]``; ``trials <= 0`` devuelve ``(nan, nan)``. Verificado contra `statsmodels.stats.proportion.proportion_confint(method="wilson")` a 1e-12 en 21 combinaciones de éxitos, ensayos y alpha.
- `binomial_test_pvalue`: usa `stats.binomtest` (SciPy ≥1.11) con caída a `stats.binom_test` (<1.11); una `alternative` desconocida cae a `"greater"`, igual que el prototipo. Contraste independiente con `scipy.stats.binom.sf/cdf` a 1e-12 para `greater`/`less`.
- `benjamini_hochberg_fdr`: paso a paso idéntico (ordenación, umbral `k*alpha/m`, mínimo acumulado inverso y ajuste a 1). Devuelve `(rejected bool, adjusted float)` en el orden de entrada; con `m == 0` devuelve arrays vacíos con esos dtypes. Verificado contra `statsmodels.stats.multitest.multipletests(method="fdr_bh")` con p-valores desordenados.
- `bootstrap_ci`: usa `np.random.default_rng(seed)` local (no `np.random.seed` global), remuestrea con `rng.choice`, descarta estadísticos no finitos y exige ≥10 réplicas válidas. Un test comprueba que el estado global del RNG no cambia; otro fija el IC de datos constantes (degenerado) y la reproducibilidad por seed.
- Tests: `tests/unit/test_stats_parity.py` con 56 casos (parametrizados incluidos); suite completa **441 passed, 2 skipped**; `ruff check` limpio.
- Sin acceso a disco/red ni mutación de argumentos en ninguna función.

---

### T022 — EVT (fit_gpd_mle, gpd_return_level, fit_evt_gpd COMPLETA)
**Prioridad**: P0 | **Dependencias**: T021 | **Archivos**: `analytics/evt.py`

**Descripción**: Ajuste GPD por MLE + **`fit_evt_gpd` completa** (MLE + KS + Mean Excess + Return Levels).

**Subtareas**:
- [x] `fit_gpd_mle(excesses)` → migración exacta L119-L161 (estabilizar SE con hessiano numérico)
- [x] `gpd_return_level(xi, sigma, threshold, n_obs, n_excesses, return_period)` → migración exacta L164-L170
- [x] **`fit_evt_gpd(returns, threshold_quantile=0.95, direction='directional', min_excesses=30)`**:
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
- [x] `detect_candle_extremes(df, config, direction)` → migración exacta L626-L678
- [x] `detect_tick_extremes(ticks_df, config, direction)` → migración exacta L680-L706
- [x] `conditional_event_table(ticks_df, events_df, horizon=100, bin_size=100, max_ticks=5000)` → migración exacta L1230-L1259
- [x] Gates: `candle_events_min_bars`, `tick_events_min_ticks`, `conditional_min_events`

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- Umbral rolling sin look-ahead (`shift(1)`)
- Tests: `test_events_parity.py`

---

### T024 — Zones (Pivots, Clustering, Evaluation)
**Prioridad**: P0 | **Dependencias**: T018, T021 | **Archivos**: `analytics/zones.py`

**Descripción**: Pivotes, clustering por tolerancia, evaluación con null geométrico, FDR, bootstrap CI.

**Subtareas**:
- [x] `find_pivots(df, side, window)` → migración exacta L709-L734
- [x] `cluster_levels(pivots, tolerance)` → migración exacta L737-L751
- [x] `evaluate_zone(df, centre, half_width, zone_type, available_from_idx, event_prices, config)` → migración exacta L753-L868 **CON**:
  - `p_null = 0.5` por defecto; geometría optativa
  - Wilson CI para reaction_rate
  - Binomial test vs p_null
  - Bootstrap CI para median_favourable/adverse_atr
- [x] `detect_reaction_zones(df, config, side, event_prices)` → migración exacta L870-L925
- [x] BH solo walk-forward por defecto; in-sample optativo en `detect_reaction_zones`

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
- [x] `fit_garch(returns, p=1, q=1)` → migración exacta L1598-L1623 (usa `arch_model`)
- [x] **`detect_volatility_regimes(returns, n_regimes=2, method='markov_switching')`**:
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
- [x] Gate: `regime_min_obs` (500)

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
- [x] `kaplan_meier(durations, censored)` → retorna `(times, survival, ci_lower, ci_upper, at_risk)`
- [x] Modo `event_index`: duraciones = diff(sorted event_indices); última censurada
- [x] Modo `time`: duraciones = diff(event_timestamps); última censurada
- [x] KS test vs Exponencial: `scipy.stats.kstest(durations, 'expon', args=(0, 1/lambda_mle))`
- [x] Gate: `survival_min_events` (20)

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
- [x] `residual_diagnostics(returns, max_lag=20)` → migración exacta L1554-L1596
- [x] Ljung-Box: `acorr_ljungbox(returns, lags=[max_lag])`
- [x] Jarque-Bera: `jarque_bera(returns)`
- [x] ARCH-LM: `het_arch(returns, maxlags=max_lag)`
- [x] ADF: `adfuller(returns, maxlag=max_lag, autolag='AIC')`
- [x] Gate: `acf_min_obs` (100)

**Criterios de Aceptación**:
- Paridad 1e-10 vs original
- Todos los p-values y estadísticos incluidos
- Tests: `test_diagnostics_parity.py`

---

### T028 — Walk-Forward Validation
**Prioridad**: P1 | **Dependencias**: T024, T021 | **Archivos**: `analytics/walkforward.py`

**Descripción**: Validación temporal anclada con advertencias de no-independencia.

**Subtareas**:
- [x] `walk_forward_validation(df, config, tf, train_days, test_days, step_days)` → migración exacta L944-L1039
- [x] `aggregate_walk_forward_results(wf_df)` → migración exacta L1041-L1063
- [x] **Advertencias obligatorias en output**:
  - `independent: false`
  - `warning: "Folds no independientes (zonas repetidas, solapamiento temporal). Sign test es descriptivo."`
  - `recommendation` si historia insuficiente
- [x] Gates: `wf_min_folds` (3), `wf_min_days` por TF

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
- [x] `AnalyticsEngine` class con `gate_config: GateConfig`
- [x] Métodos delegados a módulos: stats, evt, events, zones, volatility, survival, diagnostics, walkforward
- [x] Todos los métodos puros: mismos inputs → mismos outputs (seed fija para bootstrap)
- [x] Sin estado, sin I/O, sin side effects
- [x] Inyectar `AnalyticsEngine` real en `SnapshotService` (tests de integración; la composición de producción se cierra en T032)

**Criterios de Aceptación**:
- Determinístico: misma config + mismos datos → mismo resultado
- Tests: `test_analytics_engine.py` — determinismo, delegación; `test_snapshot_service_real_engine.py` — engine real

---


### T029 — Fixtures Determinísticas
**Prioridad**: P0 | **Dependencias**: T016, T017, T021-T028 | **Archivos**: `tests/fixtures/`, `scripts/generate_fixtures.py`

**Descripción**: Generar fixtures con seed=42 para todos los tests.

**Subtareas**:
- [x] `generate_fixtures.py`: usa `np.random.Generator(42)` para serie sintética
- [x] `boom1000_5min.csv`: 30 días, 25920 velas, OHLC válido
  - _Nota_: se usa CSV porque `pyarrow`/`fastparquet` no son dependencias del proyecto; el generador escribe `.parquet` automáticamente si están instalados.
- [x] `crash500_ticks.csv`: 5000 ticks, precios realistas
- [x] `synthetic_evt.csv`: GPD(xi=0.2, σ=1.5, threshold=0.005)
- [x] `synthetic_garch.csv`: GARCH(ω=1e-7, α=0.08, β=0.91)
- [x] `synthetic_regimes.csv`: 2 regímenes var=0.0004/0.0012, switch en medio
- [x] `snapshot_boom1000.json`: `build_snapshot()` con fixtures arriba
- [x] verificar `tests/fixtures/legacy_sha256.json` (creado en T000; solo lectura)

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
- [x] `test_stats_parity.py`: Wilson, Binomial, BH, Bootstrap (parametrizado)
- [x] `test_evt_parity.py`: fit_gpd_mle, gpd_return_level
- [x] `test_fit_evt_gpd.py`: fit_evt_gpd completa (xi, sigma, KS, Mean Excess, RL)
- [x] `test_events_parity.py`: candle/tick extremes, conditional
- [x] `test_zones_parity.py`: pivots, clustering, evaluation (modo default 0.5; optativo geo/FDR)
- [x] `test_volatility_parity.py`: GARCH
- [x] `test_regimes.py`: Markov + CUSUM (series sintéticas)
- [x] `test_survival_parity.py`: Kaplan-Meier (event_index + time)
- [x] `test_diagnostics_parity.py`: Ljung-Box, JB, ARCH-LM, ADF
- [x] `test_walkforward_parity.py`: validation + aggregation
- [x] `test_live_parity.py`: `_process_live_tick` algoritmo exacto

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
- [x] `HealthResponse`, `InstrumentInfo`
- [x] `ConfigResponse`, `ConfigUpdateRequest`, `ConfigUpdateResponse`
- [x] `StreamState`
- [x] `WSClientMessage`, `WSServerMessage` (union types por `type`)
- [x] `ExportFormat` enum: json, csv, zip

**Criterios de Aceptación**:
- Validación estricta en FastAPI
- OpenAPI generado automáticamente correcto
- Tests: `test_schemas.py`

---

### T032 — FastAPI App
**Prioridad**: P0 | **Dependencias**: T031, T033 | **Archivos**: `dashboard/app.py`, `tests/integration/test_app.py`

**Descripción**: Aplicación FastAPI con lifespan, middleware, exception handlers.

**Subtareas**:
- [x] `lifespan`: startup (inicializa DerivClient, StreamManager, SnapshotService) → shutdown (stop streams, close WS)
- [x] `CORSMiddleware` para localhost
- [x] `structlog` middleware: request_id, latency, level
- [x] Exception handlers: `QuantLabError` → JSON estructurado, 500 → genérico
- [x] `include_router(api_router, prefix="/api")`
- [x] `include_router(ws_router)` para WebSocket

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
- [x] REST: 8 endpoints (health, instruments, snapshot, config×3, live status, export)
- [x] WS: `/api/ws/{symbol}` con handler:
  - `subscribe`/`unsubscribe` channels
  - `set_timeframe`
  - `get_chart` (delegado a SnapshotService → figura JSON)
  - `ping`/`pong`
  - Push automático: tick, event, zone_update, config_updated, connection_state
- [x] Backpressure: queue max 10k msgs, close 1001 si overflow

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
- [x] `export_json(symbol)`: `snapshot.model_dump_json().encode()`
- [x] `export_csv_bytes(symbol)`: DataFrames → CSV UTF-8 BOM, separador `;` (Excel ES) en `BytesIO`
- [x] `export_zip(symbol)`: `zipfile.ZipFile(BytesIO)` con JSON + CSVs por TF
- [x] `get_media_type(format)`, `get_filename(symbol, format)`
- [x] Endpoint `/api/export/{symbol}?format=...` → `StreamingResponse`

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
- [x] `uv run python -m quantlab`
- [x] Carga `.env` si existe
- [x] Logs a consola (structlog JSON)
- [x] Maneja puerto ocupado (reintenta +1)
- [x] Muestra URL local al arrancar

**Criterios de Aceptación**:
- `.\scripts\run.ps1` arranca app en < 3s
- Funciona en PowerShell 5.1 y 7+
- Tests: manual

---

### T036 — Warm Cache Script
**Prioridad**: P2 | **Dependencias**: T009 | **Archivos**: `scripts/warm_cache.py`

**Descripción**: Precarga caché opcional para evitar espera primer arranque.

**Subtareas**:
- [x] Args: `--symbols`, `--timeframes`, `--force`
- [x] Usa `MarketDataService.warm_cache()`
- [x] Progreso por símbolo/TF en consola
- [x] `--force` ignora caché y re-descarga

**Criterios de Aceptación**:
- `uv run python scripts/warm_cache.py --symbols BOOM1000 CRASH500 --timeframes 5min 1h`
- Tests: manual

---

### T037 — Verify Contracts Script
**Prioridad**: P1 | **Dependencias**: T013 | **Archivos**: `scripts/verify_contracts.py`, `tests/contract/test_verify_contracts.py`

**Descripción**: Valida snapshots contra JSON schema.

**Subtareas**:
- [x] Carga `contracts/snapshot.schema.json`
- [x] Valida `contracts/snapshot.example.json`
- [x] Valida snapshots generados en `SnapshotStore` (si existen)
- [x] Exit code 0 si todo válido, 1 si errores

**Criterios de Aceptación**:
- `uv run python scripts/verify_contracts.py` pasa
- Tests: `test_verify_contracts.py`

---

### T038 — Contract Tests (API Schema)
**Prioridad**: P1 | **Dependencias**: T031 | **Archivos**: `tests/contract/test_api_schema.py`

**Descripción**: Validación de esquemas OpenAPI y JSON.

**Subtareas**:
- [x] OpenAPI schema generado coincide con modelos Pydantic
- [x] `snapshot.schema.json` valida `snapshot.example.json`
- [x] `snapshot.schema.json` valida snapshot generado por `build_snapshot()`
- [x] Request/response models compatibles con FastAPI

**Criterios de Aceptación**:
- `pytest tests/contract/test_api_schema.py` pasa
- Tests: automatizados en CI

---

### T039 — Contract Tests (WS Protocol)
**Prioridad**: P1 | **Dependencias**: T033 | **Archivos**: `tests/contract/test_ws_protocol.py`

**Descripción**: Validación de mensajes WebSocket.

**Subtareas**:
- [x] Client messages: subscribe, unsubscribe, set_timeframe, get_chart, ping → validados
- [x] Server messages: tick, event, zone_update, config_updated, connection_state, chart_data, pong, error → validados
- [x] Tipos union discriminados por `type` field

**Criterios de Aceptación**:
- `pytest tests/contract/test_ws_protocol.py` pasa
- Tests: automatizados

---

### T040 — Forbidden Calls Test
**Prioridad**: P0 | **Dependencias**: T006 | **Archivos**: `tests/contract/test_forbidden_calls.py`

**Descripción**: Escaneo estático de llamadas prohibidas.

**Subtareas**:
- [x] Implementa `test_no_forbidden_calls()` (ver `contracts/forbidden-calls.md`)
- [x] Patterns: trading, account, auth, disk, WS no allowlist
- [x] Allowlist: `tests/integration/test_live_deriv.py`, `scripts/spike_deriv.py`

**Criterios de Aceptación**:
- `pytest tests/contract/test_forbidden_calls.py` pasa (0 violaciones)
- Tests: automatizados en CI

**Nota para T040**: el regex de `contracts/forbidden-calls.md §7` produce falsos positivos con `asyncio.Task.cancel()` (aparece en `deriv_client.py` al cerrar el lector). El test debe marcar `cancel` solo como nombre de payload/`msg_type` de trading, no como método de `asyncio`; mismo criterio para `buy`/`sell` si aparecen como atributos. Ver también la tabla de falsos positivos conocidos de `001/contracts/forbidden-calls.md §3.1`.

---

### T041 — Performance Tests
**Prioridad**: P1 | **Dependencias**: T017, T019, T032, T033 | **Archivos**: `tests/performance/`

**Descripción**: Tests de rendimiento (mediana 5 runs).

**Subtareas**:
- [x] `test_startup_time.py`: `uv run python -m quantlab` → health check < 3s (mediana 5)
- [x] `test_ram_usage.py`: 2 índices, 6 TF, live 10 min → RSS < 400 MB (mediana 5)
- [x] `test_rate_limit.py`: carga 5 min → ≤ 150 req/min (mediana 5)
- [x] `test_recompute_time.py`: `POST /api/config` → recalculated_timeframes < 500ms/TF
- [x] `test_tick_latency.py`: tick→queue < 5 ms (deserialización + enqueue, mediana 5)
- [x] `test_warm_start.py`: arranque con Deriv warm < 30 s (mediana 5)

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
- [x] Implementar solo las versiones canónicas en `analytics/`/`charts/` (no portar v1).
- [x] Documentar la equivalencia v1→v2 en `docs/migration.md`.

**Criterios de Aceptación**:
- `legacy/boom_crash_quant_analyzer.py` permanece intacto (SHA-256 en `tests/fixtures/legacy_sha256.json`).
- El código nuevo no contiene definiciones duplicadas (chequeo AST).
- Tests: `test_no_duplicates.py` (AST).

---

### T043 — No Portar export_csv (ExportService en Memoria)
**Prioridad**: P0 | **Dependencias**: T034, T040 | **Archivos**: `services/export_service.py`, `legacy/` intacto

**Descripción**: `export_csv` no se porta; `ExportService` genera JSON/CSV/ZIP en `BytesIO`. `legacy/` no se modifica.

**Subtareas**:
- [x] Verificar que `ExportService` cubre JSON/CSV/ZIP en memoria.
- [x] No portar `export_csv` ni `_safe_name` (este último vive en `utils.py`).
- [x] No introducir `os`/`pathlib` de escritura en `analytics/`/`services/`.

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
| T004 | InstrumentState | P0 | ✅ Completado | — |
| T005 | RateLimiter | P0 | ✅ Completado | — |
| T006 | Deriv Allowlist | P0 | ✅ Completado | — |
| T007 | DerivClient | P0 | ✅ Completado | — |
| T008 | RAMCache | P0 | ✅ Completado | — |
| T009 | MarketDataService | P0 | ✅ Completado | — |
| T010 | QualityReport | P1 | ✅ Completado | — |
| T011 | Domain Models | P0 | ✅ Completado | — |
| T012 | ReactionZone | P0 | ✅ Completado | — |
| T013 | Snapshot Models | P0 | ✅ Completado | — |
| T014 | SnapshotStore | P0 | ✅ Completado | — |
| T015 | FeatureRegistry | P1 | ✅ Completado | — |
| T016 | SnapshotService | P0 | ✅ Completado | — |
| T017 | AnalyticsEngine | P0 | ✅ Completado | — |
| T018 | Indicators | P1 | ✅ Completado | — |
| T019 | StreamManager | P0 | ✅ Completado | — |
| T020 | Spike Script | P1 | ✅ Completado | — |
| T021 | Stats | P0 | ✅ Completado | — |
| T022 | EVT + fit_evt_gpd | P0 | ✅ Completado | — |
| T023 | Events | P0 | ✅ Completado | — |
| T024 | Zones | P0 | ✅ Completado | — |
| T025 | Volatility + Regimes | P0 | ✅ Completado | — |
| T026 | Survival | P1 | ✅ Completado | — |
| T027 | Diagnostics | P1 | ✅ Completado | — |
| T028 | WalkForward | P1 | ✅ Completado | — |
| T029 | Fixtures | P0 | ✅ Completado | — |
| T030 | Paridad Tests | P0 | ✅ Completado | — |
| T031 | API Schemas | P0 | ✅ Completado | — |
| T032 | FastAPI App | P0 | ✅ Completado | — |
| T033 | Routes + WS | P0 | ✅ Completado | — |
| T034 | ExportService | P1 | ✅ Completado | — |
| T035 | Run Script | P1 | ✅ Completado | — |
| T036 | Warm Cache | P2 | ✅ Completado (manual) | — |
| T037 | Verify Contracts | P1 | ✅ Completado | — |
| T038 | Contract Tests API | P1 | ✅ Completado | — |
| T039 | Contract Tests WS | P1 | ✅ Completado | — |
| T040 | Forbidden Calls | P0 | ✅ Completado | — |
| T041 | Performance Tests | P1 | ✅ Completado | — |
| T042 | Consolidar Duplicados (legacy intacto) | P0 | ✅ Completado | — |
| T043 | No portar export_csv | P0 | ✅ Completado | — |

---

## Próximos Pasos Post-Fase 1

1. **Ejecutar `/speckit.analyze`** para validar coherencia con spec padre `001-crash-boom-dashboard`
2. **Ejecutar `/speckit.converge`** hasta "Converged"
3. **Fase 2**: Gráficos Plotly backend (`charts/` generando `fig.to_json()`)
4. **Fase 3**: Downsampling LTTB + cache figuras por `config_hash`
5. **Fase 4**: Frontend Vanilla JS + Plotly.js local (servido por FastAPI)
6. **Fase 5**: Páginas Metodología/Sistema + Export UI
7. **Fase 6**: Convergencia final + Demo
