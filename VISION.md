# VISION — Migración del analizador Crash/Boom a aplicación web local

**Fecha**: 2026-11-16
**Estado**: Propuesta de visión (no modifica el código del motor matemático).
**Fuente auditada**: `boom_crash_quant_analyzer.py` (v2.2.0, 1836 líneas) y
`analizador_crash_boom_quantitativo.ipynb` (demo Colab, 16 KB).

> Principio rector: **preservar el 100 % del motor estadístico** con los mismos
> cálculos y resultados. Cualquier cambio de fórmula requiere autorización
> explícita del usuario (Directriz 2).

---

## 1. Objetivo

Migrar el analizador cuantitativo exploratorio de Deriv (índices **Crash 500** y
**Boom 1000**) a una aplicación web **local**, de solo lectura, con:

- Backend **Python** en **FastAPI** + **WebSockets**.
- Frontend **Plotly.js** (HTML + JS) servido por FastAPI.
- **Cero escritura en disco** en tiempo de ejecución (sin CSV, sin base de
  datos, sin archivos de caché ni exportación).
- Optimización para **Windows** con **8 GB de RAM (~3,7 GB libres)** y CPU
  **i3-10100F**; uso de RAM objetivo **< 400 MB**.
- Navegación por índice con estado independiente y panel de configuración por
  índice.

No se ejecutan operaciones de trading; solo se consumen datos públicos de
mercado (`active_symbols`, `ticks_history`, `ticks`).

---

## 2. Auditoría del prototipo (fuente de verdad)

### 2.1 `boom_crash_quant_analyzer.py` — inventario completo

**Dependencias**: `websocket-client`, `pandas`, `numpy`, `plotly`, `scipy`,
`statsmodels`, `arch`. Versión declarada: `MODULE_VERSION = "2.2.0"`.

**Endpoints**:
- Primario: `wss://api.derivws.com/trading/v1/options/ws/public`
- Fallback legacy: `wss://ws.binaryws.com/websockets/v3`

**Temporalidades** (`TIMEFRAMES`): 1min (60), 5min (300), 15min (900), 1h
(3600), 4h (14400), 1d (86400).

#### Funciones estadísticas puras (módulo)

| Función | Línea | Prueba estadística |
|---|---|---|
| `wilson_score_interval` | 72 | Intervalo de Wilson |
| `binomial_test_pvalue` | 86 | Test binomial exacto |
| `benjamini_hochberg_fdr` | 101 | Corrección Benjamini-Hochberg |
| `fit_gpd_mle` | 119 | Ajuste GPD por máxima verosimilitud |
| `gpd_return_level` | 164 | Niveles de retorno EVT |
| `bootstrap_ci` | 173 | Bootstrap percentil |

#### Cliente Deriv (`DerivMarketDataClient`)

`__init__` (215), `_is_transport_failure` (235), `_request_once` (247),
`request` (303), `get_active_symbols` (343), `_normalise_name` (356),
`resolve_symbol` (359), `get_candles` (412), `get_tick_history` (430).

#### Configuración (`AnalyzerConfig`, dataclass congelado, L451)

`days=20`, `candle_event_quantile=0.99`, `event_lookback_bars=200`,
`min_move_pct=0.0`, `tick_event_quantile=0.999`, `tick_event_lookback=2000`,
`zone_tolerance_atr=0.5`, `pivot_window=3`, `min_zone_touches=3`,
`touch_separation_bars=3`, `response_horizon_bars=10`, `favourable_atr=1.0`,
`adverse_atr=0.75`.

#### Analizador (`SyntheticIndexAnalyzer`) — métodos

| Método | Línea | Rol |
|---|---|---|
| `__init__` | 471 | Una instancia por índice (no mezclar estados) |
| `_ensure_symbol` | 509 | Resuelve símbolo contra `active_symbols` |
| `_clean_candles` | 516 | Normaliza/dedup/valida OHLC, UTC |
| `fetch_historical_data` | 540 | Descarga velas por temporalidad (paginación) |
| `fetch_tick_history` | 594 | Descarga hasta 5.000 ticks |
| `add_atr` | 617 | ATR(14) Wilder/rolling |
| `detect_candle_extremes` | 626 | Candidatos por velas (umbral previo) |
| `detect_tick_extremes` | 680 | Candidatos por ticks (umbral previo) |
| `_find_pivots` | 709 | Pivotes soporte/resistencia |
| `_cluster_levels` | 737 | Agrupación de niveles |
| `_evaluate_zone` | 753 | Evalúa zona: reacciones/fallos/ambiguos/pendientes |
| `detect_reaction_zones` | 870 | Zonas de reacción |
| `full_analysis` | 927 | Orquesta eventos + zonas por TF |
| `walk_forward_validation` | 944 | Validación fuera de muestra |
| `aggregate_walk_forward_results` | 1041 | Agregado OOS + BH-FDR + sign test |
| `conditional_event_table` | 1230 | Probabilidad condicional descriptiva |
| `residual_diagnostics` | 1554 | Ljung-Box, Jarque-Bera, ARCH-LM, ADF |
| `fit_garch` | 1598 | GARCH(p,q) con arch |
| `_consume_live_queue` | 1653 | Consumidor de cola en vivo |
| `_process_live_tick` | 1666 | Procesa tick en vivo |
| `start_live_ticks` | 1710 | Stream WS persistente con reconexión |
| `stop_live_ticks` | 1805 | Detiene el stream |
| `export_csv` | 1625 | **A ELIMINAR** (escribe CSV en disco) |
| `_safe_name` | 1649 | Nombre seguro para archivo (solo usa export_csv) |

### 2.2 Pruebas estadísticas presentes (se deben preservar al 100 %)

Wilson, Binomial exacto, Benjamini-Hochberg, GPD/EVT (`fit_gpd_mle`,
`gpd_return_level`), Bootstrap, Ljung-Box, Jarque-Bera, ARCH-LM, ADF, GARCH
(`arch`), Kaplan-Meier (supervivencia), Walk-Forward, y (pendiente)
Markov-Switching para regímenes.

### 2.3 Visualizaciones Plotly

Hay **11 funciones que producen figuras** (4 de ellas tienen una definición
duplicada y la segunda —la canónica— es la que prevalece en Python):

1. `plot_candles_with_zones` (velas + zonas + eventos) — L1070
2. `plot_return_distribution` (EVT/GPD 4 paneles) — L1329 (canónica)
3. `plot_volatility` (ATR + vol realizada + regímenes) — L1403 (canónica)
4. `plot_tick_heatmap` (precio-hora de ticks) — L1124
5. `plot_event_heatmap` (eventos por hora/precio) — L1140
6. `plot_interevent_survival` (log-log + KS) — L1437 (canónica)
7. `plot_autocorrelation` (ACF retornos y retornos²) — L1483 (canónica)
8. `plot_conditional_event_probability` — L1261
9. `plot_calibration` (herramienta de laboratorio, estática) — L1271
10. `plot_tick_spikes` (ticks con detección de spikes) — L1297
11. `plot_price_heatmap` (frecuencia precio-hora por velas) — L1521

> Nota de precisión: son **11** métodos de figura. Si `plot_calibration` (solo
> laboratorio, requiere predicciones externas de un modelo) se deja fuera de la
> UI, quedan **10 visualizaciones orientadas al dashboard**, que coincide con la
> directriz "10 visualizaciones". No se elimina `plot_calibration`: se preserva
> como utilidad.

### 2.4 Métodos duplicados (4) — a consolidar en una implementación canónica

| Método | Definición simple (muerta) | Definición canónica (la que vale) |
|---|---|---|
| `plot_return_distribution` | L1094 (solo histograma de ticks) | L1329 (EVT/GPD, Q-Q, mean excess) |
| `plot_volatility` | L1106 (precio + ATR + vol) | L1403 (+ regímenes) |
| `plot_interevent_survival` | L1177 (KM con censura) | L1437 (log-log + KS exponencial) |
| `plot_autocorrelation` | L1209 (pandas `autocorr`) | L1483 (statsmodels `acf`) |

La segunda definición reemplaza a la primera al interpretar el módulo; la
primera es código muerto. **Consolidación propuesta**: mantener la versión
canónica y documentar la equivalencia; no eliminar silenciosamente sin
autorización.

### 2.5 Funciones llamadas pero NO implementadas (2) — faltan

1. **`fit_evt_gpd`** — llamada en `plot_return_distribution` (L1363 y L1380):
   `self.fit_evt_gpd(timeframe=..., threshold_quantile=...)`. **No existe** en
   el archivo → `AttributeError` en runtime cuando `fit_evt=True` y hay ≥100
   excesos. Existe `fit_gpd_mle` (módulo) pero no el método envoltorio.
   También se llama en el notebook (celda L235) con
   `fit_evt_gpd(timeframe='ticks', threshold_quantile=0.99, direction='directional')`.

2. **`detect_volatility_regimes`** — llamada en `plot_volatility` (L1414):
   `self.detect_volatility_regimes(timeframe, n_regimes=2)`. **No existe** →
   `AttributeError` cuando `show_regimes=True` (valor por defecto). También se
   llama en el notebook (celda L253).

### 2.6 Problemas de memoria detectados (relevante para RAM < 400 MB)

- `live_ticks_records` (L498, L1681) y `live_events` (L497, L1694) son **listas
  sin límite**: crecen sin cota durante una sesión en vivo.
- `_live_queue` usa `queue.Queue(maxsize=100_000)` (L503) — acotada.
- `_directional_returns` usa `deque(maxlen=10_000)` (L507) — acotada.
- `self.data` guarda en memoria las 6 temporalidades + ticks + `events_*` +
  `zones_*` + `tick_events`.

**Sugerencia (Directriz 4)**: convertir `live_ticks_records` y `live_events` en
`deque(maxlen=...)` circulares para acotar RAM.

### 2.7 Honestidad estadística — hallazgos (solo documentados, no se modifican)

| Hallazgo | Ubicación | Sugerencia (requiere autorización) |
|---|---|---|
| Hipótesis nula fija `p_null = 0.5` en zonas | `_evaluate_zone` L831 | Usar `p0 = b/(a+b)` de geometría target/stop (~0,4286) |
| Hipótesis nula `0.5` en walk-forward | `walk_forward_validation` L1022 | Ídem |
| Sin BH-FDR sobre zonas in-sample | `detect_reaction_zones` | Aplicar BH por familia antes de reportar |
| `bootstrap_ci` usa `np.random.seed` global | L177 | Semilla explícita por llamada |
| `plot_tick_spikes` usa `fillna(method='bfill')` | L1306 | Evitar backfill (mira hacia el futuro) |
| Supervivencia canónica (L1437) sin censura explícita | L1459 | Usar KM con censura (como la versión L1177) |

> **Importante**: estos hallazgos NO se aplican en la migración por defecto. La
> Directriz 1 exige preservar los cálculos exactos. Cualquier corrección se
> implementa solo tras confirmación explícita del usuario.

### 2.8 Notebook (`.ipynb`)

Demo Colab (~16 KB) que: importa el módulo (con validación de versión y
`importlib.reload`), instancia `Boom 1000` (up) y `Crash 500` (down) como
instancias independientes, y ejercita **todas** las figuras + las dos funciones
faltantes + `export_csv` (empaquetado en ZIP para descarga). No aporta lógica
nueva: es el cliente de demostración. Se conserva como referencia histórica.

---

## 3. Directrices de migración

### Directriz 1 — Preservación total del motor estadístico

Se integra el **100 %** de:

- Funciones matemáticas y pruebas: Wilson, Binomial, Benjamini-Hochberg,
  GPD/EVT, Bootstrap, Ljung-Box, Jarque-Bera, ARCH-LM, ADF, GARCH,
  Kaplan-Meier y Walk-Forward.
- Las **11** funciones de visualización (10 orientadas al dashboard +
  `plot_calibration` de laboratorio).
- **Mismos cálculos y resultados**: el backend FastAPI ejecuta exactamente la
  misma matemática (mismos umbrales, mismos `shift(1)`/rolling, mismas
  tolerancias ATR, mismos pivotes) y expone los datos como JSON; el frontend
  Plotly.js solo renderiza esos datos. No se reimplementa la estadística en JS.

**Restricción**: las funciones que producen figuras Plotly en Python se portan a
"fase de datos" (cómputo en Python) + "fase de render" (Plotly.js en el
navegador). El cómputo numérico (histogramas, cuantiles, ACF, GPD, KM, etc.)
sigue en Python para garantizar resultados idénticos.

### Directriz 2 — Auditoría y sugerencias antes de modificar

- **No se altera ninguna fórmula sin autorización.** La migración replica los
  cálculos tal cual.
- **Métodos duplicados** (sección 2.4): se consolida cada uno en una sola
  implementación canónica, conservando el comportamiento de la versión canónica
  (L1329/L1403/L1437/L1483). Se documenta la equivalencia.
- **Funciones faltantes** (sección 2.5): se documentan sugerencias y se pide
  confirmación antes de implementarlas.

#### Sugerencia técnica para `fit_evt_gpd`

Firma sugerida (coherente con los usos L1363/L1380 y notebook L235):

```python
def fit_evt_gpd(self, timeframe="ticks", threshold_quantile=0.99, direction="directional"):
    # 1. retornos direccionales positivos (mismo pipeline que plot_return_distribution L1335-1341)
    # 2. umbral u = quantile(directional, threshold_quantile)
    # 3. excesos = directional[directional > u] - u
    # 4. xi, sigma, diag = fit_gpd_mle(excesos)          # reutiliza el núcleo existente
    # 5. return_levels = {periodo: gpd_return_level(xi, sigma, u, n_obs, n_excesos, periodo)}
    #    para periodos 10, 50, 100
    # 6. mean_excess_plot = {excesses, mean_excess} para una malla de umbrales
    # 7. ks_p = kstest de excesos contra GPD (si scipy disponible)
    # retorna dict: {threshold, threshold_quantile, n_excess, xi, sigma,
    #                xi_se, sigma_se, ks_p, converged, return_levels, mean_excess_plot, message}
```

Debe cumplir el contrato que ya espera `plot_return_distribution`: claves `xi`,
`sigma`, `threshold`, `return_levels` (mapa periodo→nivel) y `mean_excess_plot`
(con `excesses` y `mean_excess`).

#### Sugerencia técnica para `detect_volatility_regimes`

Firma sugerida (coherente con L1414 y notebook L253):

```python
def detect_volatility_regimes(self, timeframe, n_regimes=2):
    # 1. retornos de cierre (o vol realizada) de la temporalidad
    # 2. primario: modelo Markov-switching de 2 estados (statsmodels
    #    MarkovRegression/MarkovAutoregression) ordenado por varianza
    #    (estado 1 = alta volatilidad)
    # 3. fallback: CUSUM sobre volatilidad rodante si el modelo no converge
    # retorna dict: {markov_switching: {regime_probs: [[p0, p1], ...],
    #                params, log_likelihood}, cusum: {...}}
```

Debe cumplir el contrato que espera `plot_volatility` L1414-1416:
`regime_result["markov_switching"]["regime_probs"]` = lista de pares
`[p_baja, p_alta]`, tomando `[i][1]` como probabilidad del régimen de alta
volatilidad.

### Directriz 3 — Navegación y menú por índice

- **Barra superior**: botones **Boom 1000** / **Crash 500** para alternar
  índice, mostrando **estado de conexión en tiempo real** y **latencia**.
- **Selector de temporalidad**: 1min, 5min, 15min, 1h, 4h, 1d.
- **Menú interno por índice** (6 secciones):
  1. **Tiempo Real y Velas/Zonas** — ticks en vivo + spikes, velas OHLC con
     zonas técnicas y tabla de estadísticas.
  2. **Distribución y Colas EVT** — histograma, Q-Q, ajuste GPD, mean excess.
  3. **Volatilidad y GARCH** — volatilidad móvil, regímenes Markov-Switching,
     parámetros GARCH.
  4. **Supervivencia y Autocorrelación** — Kaplan-Meier, ACF de retornos,
     probabilidad condicionada.
  5. **Heatmaps, Diagnósticos y Walk-Forward** — heatmaps precio-hora/eventos,
     pruebas residuales, validación fuera de muestra.
  6. **Configuración del Índice** — panel interactivo por índice (ver abajo).
- **Estado independiente por índice**: cada índice es una instancia
  `SyntheticIndexAnalyzer` separada (como ya exige el diseño original); ningún
  parámetro ni resultado se mezcla entre Boom y Crash.

#### Panel "Configuración del Índice" (exclusivo por índice)

Expone y ajusta en vivo, sin mezclar estados:

- Parámetros de `AnalyzerConfig`: cuantil de eventos de vela, lookback de
  eventos, `min_move_pct`, cuantil de eventos de tick, lookback de tick,
  tolerancia ATR de zona, ventana de pivotes, toques mínimos, separación de
  toques, horizonte de respuesta, ATR favorable/adverso.
- Parámetros lógicos de modelos: ventana móvil de volatilidad, orden GARCH
  `p/q`, umbral EVT, ventanas de walk-forward (train/test/step) y **límites de
  visualización en RAM** (máximo de barras/puntos renderizados).
- Al **guardar**, se aplican y **recalculan en memoria RAM** las métricas y
  gráficas al instante (sin recarga de página, sin escritura en disco).
- Botón **"Restaurar valores por defecto"**.

### Directriz 4 — Tiempo real sin pérdida de datos y cero disco

- Una **conexión WebSocket persistente por índice** hacia Deriv, con
  **reconexión automática** (backoff) y heartbeat.
- **Búferes circulares (`deque`) en RAM** para ticks en vivo, retornos
  direccionales, registros de ticks y eventos (sustituyen a las listas sin
  límite actuales, sección 2.6).
- **Prohibido** en tiempo de ejecución: escribir CSV, bases de datos, archivos
  de caché o cualquier archivo. Se **elimina `export_csv`** (L1625);
  `_safe_name` se migra a `utils.py`.
- **Renderizado solo de la vista activa**: el navegador recibe únicamente los
  datos de la sección/temporalidad visible; el backend mantiene el resto en
  memoria. Objetivo de RAM **< 400 MB** en operación normal.

---

## 4. Stack técnico y restricciones de hardware

| Ámbito | Decisión |
|---|---|
| Backend | Python 3.13 + FastAPI + Uvicorn |
| Tiempo real | WebSocket servidor (FastAPI) + cliente WS persistente a Deriv |
| Frontend | HTML + JS + **Plotly.js** (sin Dash; sin `plotly` de Python para render) |
| Núcleo analítico | `quantlab` (Python puro, sin UI) — el motor estadístico preservado |
| Almacenamiento | Solo RAM (`deque` circulares); **cero disco** en runtime |
| Red | Local, `127.0.0.1` |
| Hardware objetivo | Windows 10/11, 8 GB RAM (~3,7 GB libres), i3-10100F |
| Presupuesto RAM | < 400 MB en operación normal |
| Datos | Públicos de Deriv; solo lectura; sin tokens |

---

## 5. Decisiones pendientes de confirmación (no se aplican sin autorización)

> **Estado (2026-11-16)**: confirmadas en §13.

> **Estado (2026-11-16)**: confirmadas en §13.

> **Estado (2026-11-16)**: confirmadas en §13.

1. **Preservación exacta vs correcciones estadísticas** (sección 2.7): ¿la
   migración replica los cálculos tal cual (recomendado, Directriz 1), o se
   aplican las correcciones (p_null geométrico, FDR in-sample, semilla de
   bootstrap, sin backfill, KM con censura)? **Por defecto: replicar exacto y
   listar las correcciones como tareas futuras optativas.**
2. **Implementar las dos funciones faltantes** (`fit_evt_gpd`,
   `detect_volatility_regimes`): ¿se implementan ahora con las firmas sugeridas
   (sección Directriz 2), o se dejan marcadas como pendientes? El dashboard
   necesita ambas para las secciones EVT y Regímenes.
3. **Consolidación de los 4 duplicados**: ¿se consolida en la versión canónica
   (recomendado) o se conservan ambas definiciones?
4. **Plotly.js**: ¿CDN o copia local? (CDN requiere internet en el navegador;
   copia local es un activo estático servido por FastAPI, no escritura en disco
   en runtime).
5. **Exportación**: confirmado — `export_csv` no se porta; se ofrece export en
   memoria (`BytesIO`: JSON/CSV/ZIP), sin tocar disco.

---

## 6. Inventario → destino (resumen de migración)

| Componente del prototipo | Destino en la app |
|---|---|
| 6 funciones estadísticas puras | `quantlab/analytics/stats.py` (sin cambios) |
| `DerivMarketDataClient` | `quantlab/data/deriv_client.py` (WS persistente + rate limit) |
| `AnalyzerConfig` | `quantlab/config.py` (mismo esquema + límites de render) |
| `SyntheticIndexAnalyzer` (una por índice) | `quantlab/analytics/analyzer.py` (sin cambios de fórmula) |
| Detección eventos/zonas/ATR | `quantlab/analytics/` (portar 1:1) |
| Walk-forward / BH / survival / GARCH / regímenes | `quantlab/analytics/` (portar 1:1) |
| 11 figuras Plotly | datos en Python + render en Plotly.js (frontend) |
| `export_csv` | **Eliminado** (no se porta) |
| `_safe_name` | Migrado a `quantlab/utils.py` |
| Stream en vivo (cola + deque) | `quantlab/live/stream_manager.py` (deques acotados) |
| Notebook Colab | `legacy/` (referencia histórica) |

---

## 7. Fuera de alcance (v1)

Trading/señales/entradas, autenticación, multiusuario, persistencia en disco de
cualquier tipo, base de datos, redistribución de datos crudos de Deriv.

---

## 13. Decisiones autorizadas (Fase 1 — `002-migration-foundation`)

1. **`fit_evt_gpd`**: implementación completa — firma
   `fit_evt_gpd(returns, threshold_quantile=0.95, direction="directional", min_excesses=30)`
   (MLE + KS + Mean Excess + Return Levels).
2. **`detect_volatility_regimes`**: Markov-Switching + fallback CUSUM — firma
   `detect_volatility_regimes(returns, n_regimes=2, method="markov_switching")`.
3. **Duplicados**: consolidar v1 en v2 canónicas **solo en el código nuevo**;
   `legacy/` permanece intacto como referencia de paridad.
4. **Frontend**: Vanilla JS + **Plotly.js local** (servido por FastAPI; sin CDN);
   Fase 4.
5. **Auth**: single-user local (sin login).
6. **Persistencia**: solo RAM + **export manual en memoria (`BytesIO`)**;
   cero escritura en disco.
7. **Rate limit Deriv**: 150/min (token bucket, margen 30 %).
8. **Downsampling**: LTTB (presupuesto ≤ 5.000 puntos por traza).

**Política estadística (Fase 1)**: se **preserva el cálculo exacto** del prototipo
(`p_null = 0.5`; BH solo en walk-forward). Las correcciones (null geométrico, BH
in-sample) son **optativas** y se activan solo con autorización explícita, con
tests de valores esperados (no de paridad 1e-10).
