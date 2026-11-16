# Tasks: Crash/Boom Quant Lab Dashboard

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md),
[VISION.md](../../VISION.md)

**Tests**: obligatorios (FR-033). Cada historia incluye tests unitarios, de
integración o de UI antes de cerrar su checkpoint.

**Organización**: tareas agrupadas por fase e historia. Rutas relativas a la raíz
del repositorio.

## Formato

- `[P]`: puede ejecutarse en paralelo (archivos distintos, sin dependencias).
- `[US1]`…`[US5]`: historia de usuario a la que pertenece la tarea.
- Cada tarea indica el archivo o directorio exacto.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Inicializar el proyecto, dependencias y estructura base.

- [ ] T001 Crear repositorio Git local, `.gitignore` (`.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `legacy/`) y `README.md` mínimo en la raíz.
- [ ] T002 Crear `pyproject.toml` con metadata, layout `src/`, `requires-python = ">=3.13,<3.14"`; instalar y fijar Python 3.13 con `uv python install 3.13` y `uv python pin 3.13`.
- [ ] T003 Añadir dependencias en `pyproject.toml` (`fastapi`, `uvicorn`, `websocket-client`, `pandas`, `numpy`, `scipy`, `statsmodels`, `arch`, `pydantic`, `pytest`, `pytest-cov`, `ruff`, `httpx`) y generar `uv.lock`. Sin `plotly` de Python. Ver T000 de 002 para el set definitivo (pydantic-settings, structlog, psutil, websockets, pytest-asyncio). Ver T000 de 002 para el set definitivo (pydantic-settings, structlog, psutil, websockets, pytest-asyncio).
- [ ] T004 Configurar `ruff` y `pytest` en `pyproject.toml` (line length 100, target py313, `testpaths=["tests"]`).
- [ ] T005 Crear esqueleto `src/quantlab/` con `__init__.py` en `domain/`, `data/`, `analytics/`, `charts/`, `live/`, `services/`, `dashboard/` y `dashboard/static/`.
- [ ] T006 Crear `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/ui/`, `tests/fixtures/` y `tests/performance/` con `__init__.py`.
- [ ] T007 Crear `.env.example` y `src/quantlab/settings.py` con variables `QUANTLAB_*` (endpoint, límites de RAM, rate limit); no leer secretos.
- [ ] T008 Crear `src/quantlab/logging_config.py` con logs a consola (UTF-8), sin tokens ni payloads completos.
- [ ] T009 Crear `scripts/run.ps1` que valide Python/uv, ejecute `uv sync` si falta `.venv` y lance `python -m quantlab`; mostrar la URL local.
- [ ] T010 Mover `boom_crash_quant_analyzer.py` y `analizador_crash_boom_quantitativo.ipynb` a `legacy/` sin modificar contenido; añadir `legacy/README.md` explicando que es referencia histórica.
- [ ] T011 Crear `docs/architecture.md` y `docs/migration.md` con el diagrama de capas y la matriz de migración de `plan.md`/`VISION.md`.
- [ ] T012 Crear `scripts/check.ps1` que ejecute `ruff check .`, `ruff format --check .` y `pytest`.

**Checkpoint**: `uv sync`, `ruff` y `pytest` funcionan; el prototipo está en `legacy/`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infraestructura que bloquea todas las historias: spike de Deriv,
dominio, cliente, config, estadística básica portada 1:1 y contratos.

### Spike de Deriv

- [ ] T013 [P] Crear `scripts/spike_deriv.py`: conectar al endpoint primario con `websocket-client`, medir handshake y mostrar el primer mensaje; timeout 15 s.
- [ ] T014 Extender `scripts/spike_deriv.py` para consultar `active_symbols` y verificar `CRASH500`/`BOOM1000`; documentar sinónimos si no están activos.
- [ ] T015 Extender `scripts/spike_deriv.py` para descargar 5.000 velas de 5min con `ticks_history`/`style=candles`, validar OHLC y medir latencia.
- [ ] T016 Extender `scripts/spike_deriv.py` para descargar 5.000 ticks y abrir una suscripción de 30 s; contar ticks y cerrar limpiamente.
- [ ] T017 Extender `scripts/spike_deriv.py` para provocar y clasificar errores de transporte, API y rate limit; medir 20 peticiones secuenciales.
- [ ] T018 Escribir `docs/spike-deriv.md` con resultados, endpoint elegido, ventanas efectivas y presupuesto de rate limit.

### Dominio y configuración

- [ ] T019 [P] Implementar `src/quantlab/domain/instrument.py` y `src/quantlab/instruments.py` con `Instrument`, dirección, ventanas por temporalidad y registro de `CRASH500`/`BOOM1000`.
- [ ] T020 [P] Implementar `src/quantlab/domain/market_data.py` con `Candle`, `Tick`, `CandleSeries`, `TickSeries`.
- [ ] T021 [P] Implementar `src/quantlab/domain/events.py` (`EventCandidate`, `EventSeries`, `EventSummary`) y `src/quantlab/domain/zones.py` (`ReactionZone`).
- [ ] T022 [P] Implementar `src/quantlab/domain/snapshot.py` (`InstrumentSnapshot`, `TimeframeAnalysis`, `FeatureStatus`, enums) según `data-model.md`.
- [ ] T023 Implementar `src/quantlab/config.py` con `AnalyzerConfig` (13 parámetros del prototipo), `RuntimeConfig` y `RenderLimits`; `config_hash`.
- [ ] T024 Implementar `src/quantlab/errors.py` con la taxonomía de `contracts/service-interfaces.md` y `ErrorRecord`; mensajes en español.
- [ ] T024b Implementar `src/quantlab/telemetry.py` con contadores de peticiones, latencias, errores, reintentos y descartes; exponer resumen para `/api/instruments` y la sección Sistema.

### Datos y Deriv

- [ ] T025 [P] Implementar `src/quantlab/data/rate_limiter.py` (token bucket 30/2,5 por segundo, FIFO, thread-safe) y tests.
- [ ] T026 Implementar `src/quantlab/data/deriv_client.py`: `_request_once`, `_is_transport_failure`, `request`, mapa `req_id`, timeout, reconexión y heartbeat.
- [ ] T027 Extender `deriv_client.py` con `get_active_symbols`, `_normalise_name`, `resolve_symbol` y `_ensure_symbol`; fallar con `SymbolNotActiveError`.
- [ ] T028 Extender `deriv_client.py` con `get_candles` y `get_tick_history`; validar `style`, `granularity`, `count` y arrays `prices/times`.
- [ ] T029 Implementar `src/quantlab/data/market_data_service.py` con `_clean_candles`: normalización, deduplicación, validación OHLC y UTC.
- [ ] T030 Implementar `fetch_historical_data` en `market_data_service.py` con ventanas por temporalidad, paginación hacia atrás y detección de falta de progreso (sin escribir en disco).
- [ ] T031 Implementar `fetch_tick_history` en `market_data_service.py`; preservar empates epoch/precio con `sequence` (sin persistir).
- [ ] T032 [P] Implementar `src/quantlab/data/quality.py` con `DataQualityReport`: cobertura, huecos, duplicados, OHLC inválidos, antigüedad.

### Estadística básica (portar 1:1)

- [ ] T033 Implementar `wilson_score_interval` en `src/quantlab/analytics/stats.py` (1:1, casos límite 0/0, 0/n, n/n).
- [ ] T034 Implementar `binomial_test_pvalue` en `stats.py` (1:1, compatibilidad SciPy <1.11 y ≥1.11).
- [ ] T035 Implementar `benjamini_hochberg_fdr` en `stats.py` (1:1, orden estable, n < 2).
- [ ] T036 Implementar `bootstrap_ci` en `stats.py` (1:1, `Generator` local con semilla explícita como mejora no invasiva).
- [ ] T037 Implementar `fit_gpd_mle` en `stats.py` (1:1, MLE L-BFGS-B, Hessiana por diferencias finitas).
- [ ] T038 Implementar `gpd_return_level` en `stats.py` (1:1, casos `xi == 0` y `xi != 0`).

### Contratos y fixtures

- [ ] T039 [P] Crear fixtures deterministas en `tests/fixtures/`: velas de 5min/1h para ambos símbolos, ticks y series sintéticas de EVT/GARCH.
- [ ] T040 [P] Crear `tests/fixtures/fakes.py` con `FakeDerivClient`, `FixedClock` y `InMemoryBuffer` que cumplan los protocolos.
- [ ] T041 Implementar `src/quantlab/data/deriv_calls.py` con la allowlist y `tests/contract/test_forbidden_calls.py` (tests 4.1–4.4 de `contracts/forbidden-calls.md`).
- [ ] T042 Implementar `tests/contract/test_zero_disk.py` que ejecute el pipeline y verifique que no se crea ningún archivo (sin CSV/DB/caché/export).

**Checkpoint**: pipeline de datos con fixtures y spike; contratos de allowlist y cero disco pasan.

---

## Phase 3: Analytics Core (Portar 1:1 + funciones faltantes)

**Purpose**: Eventos, zonas, walk-forward, EVT/GPD, GARCH, regímenes, supervivencia,
ACF, diagnósticos y datos de gráficas. Cálculos idénticos al prototipo.

### Indicadores y eventos

- [ ] T043 [US2] Implementar `add_atr` en `src/quantlab/analytics/indicators.py` (1:1) y tests de valores conocidos.
- [ ] T044 [US2] Implementar `detect_candle_extremes` en `analytics/events.py` (1:1, umbral `shift(1)`, `lookback`, `min_move_pct`, `warmup_ok`).
- [ ] T045 [US2] Implementar `detect_tick_extremes` en `analytics/events.py` (1:1, ventana rolling, warm-up 300 ticks).
- [ ] T046 [US2] Implementar el mapeo a `EventCandidate` y la etiqueta "candidato de spike" en `analytics/events.py`.

### Zonas

- [ ] T047 [US2] Implementar `_find_pivots` en `analytics/zones.py` (1:1, ventana y confirmación).
- [ ] T048 [US2] Implementar `_cluster_levels` en `analytics/zones.py` (1:1, tolerancia ATR, centro mediano).
- [ ] T049 [US2] Implementar `_evaluate_zone` en `analytics/zones.py` (1:1): episodios, reacciones, fallos, ambiguos, pendientes, Wilson, binomial, bootstrap.
- [ ] T050 [US2] Implementar `detect_reaction_zones` en `analytics/zones.py` (1:1, `side`, `min_touches`, `available_from`).

### Walk-forward

- [ ] T051 [US4] Implementar `walk_forward_validation` en `analytics/walkforward.py` (1:1, ventanas por temporalidad, mínimo 3 folds, sin fuga temporal).
- [ ] T052 [US4] Implementar `aggregate_walk_forward_results` en `analytics/walkforward.py` (1:1, tasa OOS, Wilson, sign test, BH-FDR).

### EVT/GPD (incluye función faltante)

- [ ] T053 [US3] Implementar `fit_evt_gpd` en `src/quantlab/analytics/evt.py` según la firma de VISION.md §Directriz 2 (umbral por cuantil, excesos, `fit_gpd_mle`, niveles de retorno, mean excess, KS). Reutiliza `stats.py` 1:1.
- [ ] T054 [US3] Implementar la selección de umbral (95→90 si <100 excesos) y datos de exceso medio/niveles de retorno en `analytics/evt.py`.

### Volatilidad y GARCH (incluye función faltante)

- [ ] T055 [US3] Implementar `fit_garch` en `analytics/volatility.py` (1:1, `arch`, parámetros, p-valores, AIC/BIC, pronóstico a 10 pasos).
- [ ] T056 [US3] Implementar `detect_volatility_regimes` en `analytics/volatility.py` según la firma de VISION.md (Markov switching 2 estados ordenado por varianza, fallback CUSUM).

### Supervivencia, autocorrelación y diagnósticos

- [ ] T057 [US3] Implementar el cálculo canónico de supervivencia en `analytics/survival.py` (Kaplan-Meier, modos índice/tiempo, censura).
- [ ] T058 [US3] Implementar `residual_diagnostics` en `analytics/diagnostics.py` (1:1, Ljung-Box, Jarque-Bera, ARCH-LM, ADF).
- [ ] T059 [US3] Implementar `conditional_event_table` en `analytics/events.py` (1:1, bins, horizonte, gate de 30 eventos).

### Datos de gráficas (charts → JSON, sin Plotly de Python)

- [ ] T060 [US2] Implementar `charts/price.py` con el cómputo de datos para velas + zonas + eventos (sin construir figuras).
- [ ] T061 [US3] Implementar `charts/distribution.py` con datos para histograma, Q-Q, GPD y mean excess (consolida el duplicado `plot_return_distribution`).
- [ ] T062 [US3] Implementar `charts/volatility.py` con datos para ATR, volatilidad realizada y regímenes (consolida el duplicado `plot_volatility`).
- [ ] T063 [US3] Implementar `charts/survival.py` con datos para Kaplan-Meier + KS (consolida el duplicado `plot_interevent_survival`).
- [ ] T064 [US3] Implementar `charts/timeseries.py` con datos para ACF y probabilidad condicional (consolida el duplicado `plot_autocorrelation`).
- [ ] T065 [US5] Implementar `charts/heatmaps.py` con datos para heatmaps precio-hora y eventos.
- [ ] T066 [US5] Implementar `charts/validation.py` con datos para `plot_calibration` (herramienta de laboratorio, preservada).

**Checkpoint**: todas las funciones del prototipo están portadas 1:1; las 2
faltantes implementadas; los 4 duplicados consolidados en la canónica.

---

## Phase 4: Live Stream (RAM, deques)

- [ ] T067 [US1] Implementar `src/quantlab/live/stream_manager.py` con `_consume_live_queue` (cola acotada) y `_process_live_tick` (warm-up, umbral rolling).
- [ ] T068 [US1] Implementar `start_live_ticks`/`stop_live_ticks` en `stream_manager.py` usando el endpoint activo, heartbeat y reconexión (backoff).
- [ ] T069 [US1] Sustituir las listas sin límite por `deque(maxlen=…)` para `live_ticks_records` y `live_events` (acota RAM).
- [ ] T070 [US1] Integrar `StreamManager` con el estado por índice y exponer ticks procesados, descartes y últimos eventos.

**Checkpoint**: stream en vivo con reconexión y búferes circulares acotados.

---

## Phase 5: Server (FastAPI + WebSocket)

- [ ] T071 [US1] Implementar `src/quantlab/dashboard/app.py` con la app FastAPI, montaje de `/static` y manejador global de errores.
- [ ] T072 [US1] Implementar `src/quantlab/dashboard/routes.py` con `/api/instruments` (registro, conexión, latencia).
- [ ] T073 [US1] Implementar `/api/<symbol>/summary` y `/api/<symbol>/<section>` (`?tf=`) devolviendo JSON del snapshot en memoria.
- [ ] T074 [US1] Implementar `src/quantlab/dashboard/ws.py` con WebSocket `/ws/<symbol>` que hace push de ticks en vivo y métricas al navegador.
- [ ] T075 [US5] Implementar `src/quantlab/dashboard/schemas.py` con modelos Pydantic (config, snapshot, sección).
- [ ] T076 [US5] Implementar `PUT /api/<symbol>/config` que valida y aplica la configuración del índice, recalcula en RAM y devuelve el snapshot actualizado.
- [ ] T077 [US5] Implementar `POST /api/<symbol>/config/reset` para restaurar valores por defecto.
- [ ] T078 [US1] Añadir `/healthz` y estado de conexión por símbolo en `/api/instruments`.

**Checkpoint**: FastAPI sirve la SPA, endpoints JSON por índice/sección y WS en vivo.

---

## Phase 6: Frontend (Plotly.js, barra, secciones, config)

- [ ] T079 [US1] Crear `src/quantlab/static/index.html` con la estructura de la SPA (barra superior, selector de temporalidad, menú de secciones, contenedor).
- [ ] T080 [US1] Crear `src/quantlab/static/styles.css` (responsive, contraste, estados de carga/error).
- [ ] T081 [US1] Implementar `src/quantlab/static/app.js` con la carga de Plotly.js local y el cliente de la API.
- [ ] T082 [US1] Implementar la barra superior: botones Boom 1000 / Crash 500, estado de conexión en tiempo real y latencia.
- [ ] T083 [US1] Implementar el selector de temporalidad (1min, 5min, 15min, 1h, 4h, 1d).
- [ ] T084 [US2] Implementar la sección "Tiempo Real y Velas/Zonas" (velas, zonas, eventos, tabla de estadísticas, ticks en vivo).
- [ ] T085 [US3] Implementar la sección "Distribución y Colas EVT" (histograma, Q-Q, GPD, mean excess).
- [ ] T086 [US3] Implementar la sección "Volatilidad y GARCH" (volatilidad móvil, regímenes, parámetros GARCH).
- [ ] T087 [US3] Implementar la sección "Supervivencia y Autocorrelación" (Kaplan-Meier, ACF, probabilidad condicionada).
- [ ] T088 [US5] Implementar la sección "Heatmaps, Diagnósticos y Walk-Forward".
- [ ] T089 [US5] Implementar la sección "Configuración del Índice" con todos los parámetros de `AnalyzerConfig` y de modelo, botón guardar y botón "Restaurar valores por defecto".
- [ ] T090 [US5] Implementar el render solo de la vista activa (solicitar datos solo de la sección/temporalidad visible).
- [ ] T091 [US2] Implementar downsampling LTTB / recorte a ≤ 5.000 puntos y aviso "mostrando N de M".

**Checkpoint**: SPA completa con navegación por índice, secciones y configuración.

---

## Phase 7: Tests

- [ ] T092 [US2] Escribir `tests/unit/test_stats.py` (Wilson, binomial, BH, bootstrap, GPD, return level).
- [ ] T093 [US2] Escribir `tests/unit/test_indicators_events.py` (ATR, umbral sin look-ahead, warm-up).
- [ ] T094 [US2] Escribir `tests/unit/test_zones.py` (pivotes, clustering, evaluación, ambiguos, pendientes).
- [ ] T095 [US3] Escribir `tests/unit/test_evt.py` (GPD sintética, tolerancia, gates 30/100).
- [ ] T096 [US3] Escribir `tests/unit/test_volatility.py` (GARCH simulado, regímenes con fallback).
- [ ] T097 [US3] Escribir `tests/unit/test_survival.py` (censura conocida, mediana).
- [ ] T098 [US3] Escribir `tests/unit/test_diagnostics.py` (Ljung-Box, JB, ARCH-LM, ADF).
- [ ] T099 [US4] Escribir `tests/unit/test_walkforward.py` (zonas plantadas, folds, sin fuga temporal) y verificar 15min/1h con ventanas por defecto (folds o "historia insuficiente").
- [ ] T100 [US1] Escribir `tests/integration/test_data_pipeline.py` (fake client → limpieza → calidad).
- [ ] T101 [US1] Escribir `tests/integration/test_stream_manager.py` (fake WS, descartes, reconexión).
- [ ] T102 [US1] Escribir `tests/integration/test_snapshot_both.py` (ambos símbolos, aislamiento de fallos).
- [ ] T103 [US5] Escribir `tests/contract/test_snapshot_schema.py` (jsonschema contra `contracts/snapshot.schema.json` y el ejemplo) y `tests/contract/test_snapshot_reproducibility.py` (misma fixture/config/semilla → mismos valores salvo timestamps).
- [ ] T104 [US5] Escribir `tests/contract/test_no_auth_headers.py` y `test_no_trading_routes.py`.
- [ ] T105 [US1] Escribir `tests/ui/test_routes.py` con TestClient (rutas, JSON, healthz, 404).
- [ ] T106 [US5] Escribir `tests/ui/test_config_isolation.py` (cambiar config de Boom no afecta Crash).
- [ ] T107 [US1] Escribir `tests/performance/test_ram_budget.py` (RAM < 400 MB con 2 símbolos, 6 TF en memoria).

**Checkpoint**: suite de tests (unidad, integración, contrato, UI, rendimiento) en verde.

---

## Phase 8: Polish & Cross-Cutting

- [ ] T108 [P] Test de rendimiento de inicio con datos en memoria < 2 s, mediana de 5 ejecuciones.
- [ ] T109 [P] Test de construcción inicial de ambos índices < 3 min con progreso por símbolo/TF.
- [ ] T110 [P] Medir y documentar RAM < 400 MB en operación normal con `tracemalloc` o monitor.
- [ ] T111 [P] Revisar responsive, contraste, foco y textos alternativos en `static/` y componentes.
- [ ] T111b [P] Verificar que la UI y los docs están en español y los identificadores en inglés; añadir test de textos.
- [ ] T112 [P] Ejecutar escaneo de secretos y revisar `.env.example`, `README.md`, `docs/` y `src/`.
- [ ] T113 Auditar la matriz de migración: cada función de `legacy/boom_crash_quant_analyzer.py` tiene tarea, test o razón documentada; `export_csv`/`_safe_name` no se portan a la app (el prototipo en `legacy/` se conserva intacto).
- [ ] T114 Documentar la equivalencia de los 4 métodos duplicados consolidados en `docs/migration.md`.
- [ ] T115 Validar `quickstart.md` paso a paso en Windows (modo demo, modo real, solución de problemas).
- [ ] T116 Ejecutar `/speckit.analyze` y corregir gaps críticos entre spec, plan, research, data-model, contratos y tareas.
- [ ] T117 Ejecutar `/speckit.converge` hasta "Converged"; repetir tras cada corrección.
- [ ] T118 Preparar demo final con fixtures, capturas y guion de 5 minutos en `docs/demo.md`.
- [ ] T119 Documentar limitaciones, términos de Deriv y no-asesoría financiera en `docs/limits.md` y `README.md`.
- [ ] T120 Crear tag/release local y backup de `src/`, `tests/`, `specs/`, `scripts/`, `pyproject.toml`, `uv.lock`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)**: sin dependencias.
- **Foundational (2)**: depende de Setup; bloquea todas las historias.
- **Analytics (3)**: depende de Foundational.
- **Live (4)**: depende de Foundational.
- **Server (5)**: depende de Analytics + Live.
- **Frontend (6)**: depende de Server.
- **Tests (7)**: acompaña a cada fase; consolidación final.
- **Polish (8)**: depende de todas.

### User Story Dependencies

| Historia | Depende de | Puede probarse sola |
|---|---|---|
| US1 | Foundational + Live + Server | Sí, con fixtures |
| US2 | Foundational + Analytics (eventos/zonas) | Sí, con fixtures |
| US3 | US2 (eventos/zonas), Analytics | Sí, con series sintéticas |
| US4 | US2 (zonas), Analytics | Sí, con series sintéticas |
| US5 | US1–US4 | Sí, con snapshot de fixture |

### Parallel Opportunities

- T019–T022 (dominio) son paralelas.
- T025, T032, T039, T040 pueden ir en paralelo dentro de Foundational.
- T033–T038 (stats) son paralelas entre sí.
- T047–T050 comparten `zones.py`: no paralelizar entre sí.
- T053–T056 comparten `evt.py`/`volatility.py`: no paralelizar entre sí.
- T060–T066 comparten `charts/`: coordinar.
- Tests de historias distintas pueden ir en paralelo.

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Completar Phase 1 y Phase 2.
2. Completar US1 (barra + estado + inicio) y validar con fixtures.
3. Completar US2 (velas, zonas, tiempo real) y validar.
4. **STOP and VALIDATE**: demo con datos reales o fixtures; sin llamadas de trading;
   sin escritura en disco.
5. Continuar con US3, US4 y US5.

### Regla de completitud

Ninguna función del prototipo se elimina (salvo `export_csv`/`_safe_name`). Las
duplicadas se consolidan en la canónica; las faltantes se implementan. T113 audita
la matriz y T114 documenta las equivalencias. El test de cero disco y el de
llamadas prohibidas bloquean la entrega.

## Notes

- `[P]` significa archivos distintos y sin dependencia.
- Cada tarea deja el repositorio ejecutable y con tests pasando.
- No se avanza de fase sin cerrar el checkpoint y sin actualizar `tasks.md`.
- Los datos reales no se usan en tests; los fixtures son deterministas.
- No se persiste nada en disco en runtime; el test de contrato lo verifica.
- La UI y los docs van en español; el código y los nombres de archivo en inglés.
- Un fallo del test de llamadas prohibidas o de cero disco bloquea la entrega.
