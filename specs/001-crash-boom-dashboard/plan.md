# Implementation Plan: Crash/Boom Quant Lab Dashboard

**Branch**: `001-crash-boom-dashboard` | **Date**: 2026-11-16 | **Spec**: [spec.md](./spec.md) | **Vision**: [VISION.md](../../VISION.md)

**Input**: Feature specification from `/specs/001-crash-boom-dashboard/spec.md`

## Summary

Migrar `boom_crash_quant_analyzer.py` a una aplicación web **local** de solo
lectura con backend **FastAPI + WebSockets** y frontend **Plotly.js** servido como
activo estático. El motor estadístico se preserva **al 100 %** en un paquete
Python puro `quantlab` (sin fórmulas alteradas); las dos funciones faltantes
(`fit_evt_gpd`, `detect_volatility_regimes`) se implementan; los 4 métodos de
figura duplicados se consolidan en su versión canónica. **Cero escritura en disco**
en runtime: búferes circulares (`deque`) en RAM, sin CSV/DB/caché/export. Objetivo
**RAM < 400 MB** en Windows (8 GB, i3-10100F).

## Technical Context

**Language/Version**: Python 3.13 (gestionado con `uv`; evitar 3.14 por
compatibilidad de `arch`/`statsmodels`).

**Primary Dependencies**: fastapi, uvicorn, websocket-client, pandas, numpy,
scipy, statsmodels, arch, pydantic. **Sin `plotly` de Python** (el render es
Plotly.js en el navegador).

**Frontend**: HTML + CSS + JS + **Plotly.js** (copia local servida por FastAPI en
`src/quantlab/static/`). Sin framework de SPA; sin Dash.

**Storage**: solo RAM. Búferes circulares (`deque(maxlen=…)`) para ticks en vivo,
registros de ticks y eventos. Los datos históricos por temporalidad viven en
`self.data` (DataFrames) durante el proceso. **Sin base de datos, sin Parquet, sin
CSV, sin `export_csv`.**

**Testing**: pytest para unidad/integración con fixtures; test estático de llamadas
prohibidas; test de "cero disco" (sin escrituras); smoke tests del servidor
FastAPI (TestClient).

**Target Platform**: Windows 10/11 local; navegador moderno (Chrome/Edge/Firefox).
Hardware: 8 GB RAM (~3,7 GB libres), i3-10100F.

**Performance Goals**: vista de inicio < 2 s con datos en memoria; snapshot inicial
de ambos índices < 3 min; RAM < 400 MB; gráficos con ≤ 5.000 puntos (downsampling).

**Constraints**: 220 llamadas/minuto de Deriv (token bucket a 150/min con margen);
sin ticks crudos ni datos en disco; sin tokens; sin endpoints de trading; UTC;
UI en español, código en inglés.

**Scale/Scope**: 2 instrumentos, 6 temporalidades, ~100.000 velas por instrumento
en memoria con la configuración por defecto, un usuario local.

## Constitution Check

| Principio | Cumplimiento | Evidencia en el plan |
|---|---|---|
| I. Solo lectura | PASS | Allowlist de llamadas; test de llamadas prohibidas; sin tokens |
| II. Honestidad estadística | PASS | `n`, IC, etiquetas in-sample/OOS; correcciones optativas documentadas |
| III. Sin persistencia masiva | PASS | Solo RAM (`deque`); cero disco |
| IV. Reproducibilidad | PASS | `uv.lock`, semillas, fixtures, UTC |
| V. Núcleo testeable separado de la UI | PASS | `quantlab` sin FastAPI; UI consume JSON |
| VI. Transparencia | PASS | Estado de conexión/latencia, calidad, parámetros por índice |
| VII. Rendimiento y límites | PASS | WS persistente, token bucket, render de vista activa |
| VIII. Alcance acotado | PASS | Registro de instrumentos; 2 índices iniciales |

## Project Structure

```text
src/quantlab/
├── __init__.py
├── __main__.py                # `python -m quantlab`
├── config.py                  # Pydantic: AnalyzerConfig, ModelConfig, GateConfig, RAMConfig + config_hash
├── settings.py                # BaseSettings desde .env (QUANTLAB_*)
├── errors.py                  # Taxonomía de errores (mensajes ES)
├── utils.py                   # _safe_name, config_hash, downsample_lttb, uuid5, UTC
├── state.py                   # InstrumentState (deques + locks)
├── instruments.py             # Registro CRASH500/BOOM1000
├── telemetry.py               # Contadores/latencias (sin archivo)
├── domain/
│   ├── instrument.py          # Instrument
│   ├── market_data.py         # Candle, Tick (Pydantic)
│   ├── events.py              # EventCandidate
│   ├── zones.py               # ReactionZone
│   └── snapshot.py            # InstrumentSnapshot (frozen), FeatureStatus, TimeframeAnalysis
├── data/
│   ├── deriv_client.py        # WS async persistente, req_id, fallback, reconexión
│   ├── deriv_calls.py         # Allowlist de llamadas Deriv
│   ├── rate_limiter.py        # TokenBucket (30 / 2,5 s)
│   ├── market_data_service.py # Velas/ticks, limpieza, calidad (sin persistencia)
│   ├── cache.py               # RAMCache: deques + LRU + monitor 30 s
│   └── quality.py             # DataQualityReport
├── analytics/
│   ├── stats.py               # Wilson, binomial, BH, bootstrap, GPD MLE, return level
│   ├── indicators.py          # ATR, retornos
│   ├── events.py              # detect_candle/tick_extremes, condicional
│   ├── zones.py               # Pivotes, clustering, evaluación
│   ├── evt.py                 # fit_evt_gpd (completa)
│   ├── volatility.py          # GARCH + detect_volatility_regimes
│   ├── survival.py            # Kaplan-Meier
│   ├── diagnostics.py         # Ljung-Box, JB, ARCH-LM, ADF
│   └── walkforward.py         # Validación OOS
├── charts/                    # Fase 3: datos para trazas (sin figuras Python)
├── live/
│   └── stream_manager.py      # WS + deques + heartbeat + reconexión
├── services/
│   ├── snapshot_store.py      # Swap atómico en RAM
│   ├── snapshot_service.py    # Orquestador: gates → analytics → snapshot
│   ├── feature_registry.py    # Registro de análisis y gates
│   └── export_service.py      # Export en memoria (BytesIO: JSON/CSV/ZIP)
├── dashboard/
│   ├── app.py                 # FastAPI (sirve /api y /static)
│   ├── routes.py              # REST por índice/sección
│   ├── ws.py                  # WebSocket servidor
│   ├── schemas.py             # Modelos Pydantic de API
│   └── __main__.py            # `python -m quantlab`
└── static/                    # Frontend (Fase 4)
    ├── index.html
    ├── app.js
    ├── plotly.min.js
    └── styles.css

tests/
├── unit/
├── integration/
├── contract/
├── ui/
└── performance/

legacy/                        # Prototipo + notebook (referencia histórica)
docs/                          # Arquitectura, migración, operación
scripts/                       # Spike de Deriv, run.ps1, check.ps1
```

## Phase 0: Research (ver `research.md`)

1. **Endpoint**: `wss://api.derivws.com/trading/v1/options/ws/public` primario;
   `wss://ws.binaryws.com/websockets/v3` fallback.
2. **Ventanas por temporalidad**: 1min 30d, 5min 90d, 15min 180d, 1h 1a, 4h 2a,
   1d 5a. Con token bucket 150/min y caché en memoria solo se piden huecos.
3. **Almacenamiento**: solo RAM (`deque` circulares). Sin Parquet/CSV/JSON en
   disco. Presupuesto RAM < 400 MB.
4. **Estadística**: replicar exacto; correcciones (null geométrico, FDR in-sample,
   semilla bootstrap, sin backfill, KM con censura) documentadas como optativas.
5. **Gates de muestra**: EVT ≥ 100 excesos (mínimo 30), zonas ≥ 10 evaluados,
   supervivencia ≥ 20 eventos, walk-forward ≥ 3 folds, GARCH ≥ 200 obs.
6. **Rendimiento**: downsampling ≤ 5.000 puntos; render solo de vista activa.

## Data flow

```text
Deriv WS público (1 conexión persistente por índice)
   ↓ (req_id, token bucket, fallback, heartbeat)
DerivClient / StreamManager
   ↓
MarketDataService (velas/ticks en memoria)
   ↓
AnalyticsEngine (quantlab, sin fórmulas alteradas) → InstrumentSnapshot (RAM)
   ↓
FastAPI: REST /api/... (JSON) + WebSocket /ws (push de ticks en vivo)
   ↓
Frontend Plotly.js (render solo de la vista activa)
```

## Service interfaces

- `DerivClient.request(payload, expected_types, expected_key)` → respuesta
  validada; reintentos/fallback solo en errores de transporte.
- `DerivClient.subscribe_ticks(symbol)` → stream con callback.
- `MarketDataService.get_candles(symbol, timeframe, start, end)` → DataFrame
  normalizado + `DataQualityReport`.
- `MarketDataService.get_ticks(symbol, count)` → DataFrame.
- `AnalyticsEngine.compute(snapshot_input)` → `InstrumentSnapshot` (memoria).
- `StreamManager` → cola acotada + deques; expone estado, ticks procesados y
  descartes.

## UI (rutas y secciones)

| Ruta | Contenido |
|---|---|
| `/` | SPA: barra superior (Boom/Crash + conexión + latencia) + inicio |
| `/api/instruments` | Registro de instrumentos y estado |
| `/api/<symbol>/summary` | Resumen por índice |
| `/api/<symbol>/<section>` | Datos JSON por sección y temporalidad (`?tf=`) |
| `/ws/<symbol>` | Push de ticks en vivo y métricas |
| `/static/*` | Frontend + Plotly.js local |

**Secciones por índice** (menú interno, estado independiente):
1. Tiempo Real y Velas/Zonas · 2. Distribución y Colas EVT · 3. Volatilidad y
GARCH · 4. Supervivencia y Autocorrelación · 5. Heatmaps, Diagnósticos y
Walk-Forward · 6. Configuración del Índice.

## Migration Matrix (100 % del prototipo)

| Componente del prototipo | Estado | Acción | Destino |
|---|---|---|---|
| `wilson_score_interval` | Implementado | Portar 1:1 | `analytics/stats.py` |
| `binomial_test_pvalue` | Implementado | Portar 1:1 | `analytics/stats.py` |
| `benjamini_hochberg_fdr` | Implementado | Portar 1:1 | `analytics/stats.py` |
| `fit_gpd_mle` | Implementado | Portar 1:1 | `analytics/stats.py` |
| `gpd_return_level` | Implementado | Portar 1:1 | `analytics/stats.py` |
| `bootstrap_ci` | Implementado | Portar 1:1 | `analytics/stats.py` |
| `DerivAPIError` | Implementado | Portar; taxonomía | `errors.py` |
| `DerivMarketDataClient` | Implementado | Reescribir persistente + rate limit | `data/deriv_client.py` |
| `resolve_symbol` / `get_active_symbols` | Implementado | Portar | `data/deriv_client.py` |
| `get_candles` / `get_tick_history` | Implementado | Portar | `data/deriv_client.py` |
| `AnalyzerConfig` | Implementado | Portar + límites de render | `config.py` |
| `_clean_candles` | Implementado | Portar | `data/market_data_service.py` |
| `fetch_historical_data` | Implementado | Portar; ventanas por TF | `data/market_data_service.py` |
| `fetch_tick_history` | Implementado | Portar | `data/market_data_service.py` |
| `add_atr` | Implementado | Portar 1:1 | `analytics/indicators.py` |
| `detect_candle_extremes` | Implementado | Portar 1:1 | `analytics/events.py` |
| `detect_tick_extremes` | Implementado | Portar 1:1 | `analytics/events.py` |
| `_find_pivots` / `_cluster_levels` | Implementado | Portar 1:1 | `analytics/zones.py` |
| `_evaluate_zone` | Implementado | Portar 1:1 | `analytics/zones.py` |
| `detect_reaction_zones` | Implementado | Portar 1:1 | `analytics/zones.py` |
| `full_analysis` | Implementado | Reemplazar por orquestador | `analytics/` + `dashboard/routes.py` |
| `walk_forward_validation` | Implementado | Portar 1:1 | `analytics/walkforward.py` |
| `aggregate_walk_forward_results` | Implementado | Portar 1:1 | `analytics/walkforward.py` |
| `conditional_event_table` | Implementado | Portar 1:1 | `analytics/events.py` |
| `residual_diagnostics` | Implementado | Portar 1:1 | `analytics/diagnostics.py` |
| `fit_garch` | Implementado | Portar 1:1 | `analytics/volatility.py` |
| `plot_candles_with_zones` | Implementado | Datos Python + render JS | `charts/price.py` + frontend |
| `plot_return_distribution` | Duplicado (L1094/L1329) | Consolidar canónica (L1329) | `charts/distribution.py` + frontend |
| `plot_volatility` | Duplicado (L1106/L1403) | Consolidar canónica (L1403) | `charts/volatility.py` + frontend |
| `plot_interevent_survival` | Duplicado (L1177/L1437) | Consolidar canónica (L1437) | `charts/survival.py` + frontend |
| `plot_autocorrelation` | Duplicado (L1209/L1483) | Consolidar canónica (L1483) | `charts/timeseries.py` + frontend |
| `plot_tick_heatmap` / `plot_event_heatmap` / `plot_price_heatmap` | Implementado | Datos + render JS | `charts/heatmaps.py` + frontend |
| `plot_conditional_event_probability` | Implementado | Datos + render JS | `charts/timeseries.py` + frontend |
| `plot_calibration` | Implementado (lab) | Preservar utilidad | `charts/validation.py` |
| `plot_tick_spikes` | Implementado | Datos + render JS | `charts/price.py` + frontend |
| `fit_evt_gpd` | **Falta** | Implementar (firma en VISION.md) | `analytics/evt.py` |
| `detect_volatility_regimes` | **Falta** | Implementar (firma en VISION.md) | `analytics/volatility.py` |
| `export_csv` / `_safe_name` | Implementado | **Eliminar** (cero disco) | — |
| `_consume_live_queue` / `_process_live_tick` | Implementado | Portar; deques acotados | `live/stream_manager.py` |
| `start_live_ticks` / `stop_live_ticks` | Implementado | Portar; heartbeat/reconexión | `live/stream_manager.py` |
| Notebook Colab | Demo | Mover a `legacy/` | `legacy/` |

**Regla de completitud**: ninguna función del prototipo se elimina salvo
`export_csv`/`_safe_name` (Directriz 4). Las duplicadas se consolidan en la
canónica y se documenta la equivalencia. Las faltantes se implementan.

## Statistical corrections (optativas — pendientes de autorización)

Se replican los cálculos exactos. Las siguientes correcciones están documentadas
en VISION.md §2.7 y solo se aplican con autorización explícita:

1. Null geométrico `p0 = b/(a+b)` (≈0,4286) en zonas y walk-forward.
2. BH-FDR in-sample sobre todas las zonas evaluadas.
3. Semilla explícita en `bootstrap_ci` (evitar `np.random.seed` global).
4. Eliminar `fillna(method='bfill')` en `plot_tick_spikes` (fuga de futuro).
5. Kaplan-Meier con censura en la curva canónica.
6. Deques acotados para `live_ticks_records` y `live_events`.

## Milestones

### Milestone A — Fundación y rebanada vertical (P1)
- Spike de Deriv, cliente persistente + rate limiter, limpieza y calidad.
- Núcleo `quantlab` con estadística portada 1:1 y las 2 funciones faltantes.
- FastAPI: `/api/instruments`, `/api/<symbol>/summary`, servidor WS.
- Frontend: barra superior, selector de temporalidad, sección Velas/Zonas.
- Test de llamadas prohibidas y de cero disco.
- Criterio: ambos índices visibles, datos reales/fixtures, sin trading, sin disco.

### Milestone B — Análisis avanzado (P2)
- EVT/GPD, GARCH, regímenes, supervivencia, autocorrelación, heatmaps,
  probabilidad condicional.
- Stream en vivo con deques acotados, heartbeat y descartes.
- Walk-forward con ventanas por TF.
- Criterio: cada panel tiene gate de muestra y etiqueta in-sample/OOS.

### Milestone C — Configuración, operación y convergencia (P3)
- Sección Configuración del Índice (estado independiente, restaurar por defecto).
- Rendimiento RAM < 400 MB, responsive, logs, `run.ps1`, README.
- Auditoría de migración (100 %), `/speckit.analyze` y `/speckit.converge`.
- Criterio: un evaluador puede auditar fuente, método, configuración y resultados.

## Risks & Mitigations

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Endpoint 520 / cambios de Deriv | Alto | Spike temprano, fallback, diagnóstico, errores claros |
| Símbolos no activos en la jurisdicción | Alto | Resolución contra `active_symbols`, mensaje accionable |
| Límite 220/min | Medio | Token bucket 150/min, una conexión persistente |
| Historia insuficiente en 1d/4h | Medio | Ventanas por TF, gates, estados explícitos |
| RAM > 400 MB con 8 GB / i3 | Medio | Deques acotados, downsampling, render de vista activa |
| Sesgo in-sample | Alto | Walk-forward, etiquetas, correcciones optativas |
| GARCH/regímenes no convergen | Medio | Gates, captura de error, estado "no aplicable" |
| Python 3.14 incompatible | Bajo | Fijar 3.13 con `uv` |
| Términos de Deriv | Medio | Atribución; no redistribuir crudos |

## Resolved Questions

1. **Persistencia en disco**: v1 es **cero disco** (solo RAM, deques circulares).
   No hay caché, CSV ni base de datos; el export manual es en memoria (`BytesIO`) y no toca disco.
2. **Local o publicado**: v1 local en `127.0.0.1`.
3. **Idioma**: UI y docs en español; código y nombres de archivo en inglés.
4. **Funciones faltantes**: se implementan `fit_evt_gpd` y
   `detect_volatility_regimes` con las firmas sugeridas en VISION.md.
5. **Duplicados**: se consolidan los 4 métodos en su versión canónica.
6. **Plotly.js**: copia local servida por FastAPI (sin CDN).
