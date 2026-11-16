# Research: Crash/Boom Quant Lab Dashboard

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16 | **Plan**: [plan.md](./plan.md)

Este documento resuelve las incógnitas técnicas de la Fase 0. Cada decisión
incluye la alternativa descartada y la forma de verificarla. Las cifras son
presupuestos de diseño; el spike técnico (T013–T018 en `tasks.md`) las confirma
contra la API real antes de construir el resto.

## Resumen de decisiones

| # | Tema | Decisión |
|---|---|---|
| D1 | Endpoint | WebSocket público persistente, endpoint nuevo como primario y legacy como fallback |
| D2 | Historia | Ventanas por temporalidad; paginación hacia atrás; solo memoria |
| D3 | Almacenamiento | Solo RAM (búferes circulares `deque`); cero disco; sin base de datos |
| D4 | Snapshots | Inmutables, en memoria, por instrumento y temporalidad; cómputo en background |
| D5 | Estadística | Replicar exacto; correcciones (null geométrico, FDR, censura) optativas |
| D6 | Puertas de muestra | Gates explícitos por análisis, visibles en la UI |
| D7 | UI | FastAPI + WebSockets + Plotly.js (estático local); barra superior por índice; español |
| D8 | Runtime | Python 3.13 con `uv`, Windows 10/11 (8 GB / i3-10100F), un solo comando |
| D9 | Pruebas | pytest por capas, fixtures deterministas, allowlist y cero disco |
| D10 | Persistencia | Cero disco en runtime (sin CSV, DB, caché, export); `export_csv` eliminado |

## D1. Endpoint y estrategia de conexión

### Decisión

Una sola conexión WebSocket persistente por proceso, con multiplexación por
`req_id` y una suscripción de ticks sobre la misma conexión.

| Prioridad | Endpoint | Uso |
|---|---|---|
| 1 | `wss://api.derivws.com/trading/v1/options/ws/public` | Primario, sin token |
| 2 | `wss://ws.binaryws.com/websockets/v3?app_id=1089` | Fallback si el primario falla a nivel de transporte |
| 3 | `wss://ws.derivws.com/websockets/v3?app_id=1089` | Último recurso, solo si el spike lo confirma |

`app_id=1089` es el identificador público de demostración usado por la
documentación y ejemplos; no es un secreto. El spike debe confirmar si el
endpoint primario lo requiere.

### Rationale

- El prototipo abre una conexión por petición y espera `time.sleep(0.25)` entre
  temporalidades; con ~200 peticiones eso añade decenas de segundos y expone el
  handshake a fallos repetidos.
- Una conexión persistente permite correlacionar respuestas por `req_id`,
  reutilizar TLS, mantener la suscripción de ticks y detectar mensajes
  huérfanos.
- La conexión única también simplifica el rate limiting: un solo productor de
  llamadas y un solo lugar donde registrar latencias.

### Diseño de la conexión

- Hilo lector dedicado que despacha mensajes a un mapa `req_id -> Future`.
- Hilo escritor con lock; la cola de envío respeta el token bucket.
- `req_id` monotónico por proceso; nunca se reutiliza.
- Timeout por petición de 20 s; timeout de conexión de 15 s.
- Heartbeat `ping` cada 30 s; si no llega ningún mensaje en 90 s, se considera
  conexión muerta y se reconecta.
- Reintentos solo para fallos de transporte (timeout, conexión cerrada, 5xx,
  `Disconnected`). Los errores de API (`invalid_symbol`, `rate_limit`,
  `input_validation_failed`) se devuelven al llamador y no se reintentan salvo
  `rate_limit`, que espera `retry_after`.
- Backoff exponencial con jitter: 1, 2, 4, 8, 16, 30 s; máximo 6 intentos.
- Al reconectar se reenvía `active_symbols`, la suscripción de ticks y se
  invalidan las peticiones pendientes con `DerivTransportError`.
- Un solo `threading.Lock` para el estado de conexión; los `Future` se resuelven
  fuera del lock para evitar bloqueos.

### Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Una conexión por petición (prototipo) | Latencia, handshakes, difícil de limitar y de mantener viva |
| REST/HTTP | Deriv no ofrece un REST público equivalente para velas y ticks |
| Librería de terceros | Dependencia adicional y menos control sobre `req_id`, reintentos y tests |
| Dos conexiones (histórico + vivo) | Duplica estado y rate limiting; no es necesario para 2 símbolos |

### Verificación (spike)

1. Conectar al endpoint primario desde Windows y medir handshake.
2. Confirmar `active_symbols` con `CRASH500` y `BOOM1000`.
3. Descargar 5.000 velas de 1min y comprobar `epoch`, OHLC y granularidad.
4. Descargar 5.000 ticks y comprobar `prices`/`times`.
5. Provocar un `req_id` inválido y un símbolo inexistente para capturar la
   taxonomía de errores.
6. Medir el tiempo de 20 peticiones y comparar con el presupuesto de 220/min.
7. Repetir con el endpoint de fallback.

Si el primario devuelve HTTP 520 de forma consistente, se invierte la prioridad
sin cambiar el resto del diseño.

## D2. Ventanas de historia y paginación

### Decisión

| Temporalidad | Granularidad (s) | Ventana | Barras esperadas | Páginas a 1.000 | Páginas a 5.000 |
|---|---:|---:|---:|---:|---:|
| 1min | 60 | 30 días | 43.200 | 44 | 9 |
| 5min | 300 | 90 días | 25.920 | 26 | 6 |
| 15min | 900 | 180 días | 17.280 | 18 | 4 |
| 1h | 3.600 | 365 días | 8.760 | 9 | 2 |
| 4h | 14.400 | 730 días | 4.380 | 5 | 1 |
| 1d | 86.400 | 1.825 días | 1.825 | 2 | 1 |
| **Total por índice** | | | **101.365** | **104** | **23** |
| **Total ambos** | | | **202.730** | **208** | **46** |

- Se solicita `count=5000` primero. Si la respuesta viene truncada o el endpoint
  limita a 1.000, se usa `count=1000` y se mantiene la paginación.
- La paginación va hacia atrás: `end = oldest_epoch - 1`, manteniendo `start`
  fijo. Se detiene cuando el lote llega a `start`, cuando no hay progreso
  temporal o cuando se alcanza `max_pages_per_timeframe=120`.
- Cada página se valida antes de acumular; si un lote no trae `epoch` válidos se
  registra el error y se detiene esa temporalidad, sin contaminar las demás.
- La ventana efectiva se recorta a lo que Deriv devuelva. Si es menor que la
  ventana objetivo, la UI muestra "ventana efectiva" y la calidad lo refleja.
- Si ya hay datos en memoria para el rango, solo se solicitan las colas que
  faltan (más antigua y más reciente); si no, se descarga la ventana completa.

### Rationale

- 1d necesita al menos 60 barras para el lookback de eventos y 5 años para que
  el walk-forward y la cola de 1d tengan muestra. Con 20 días fijos, el
  prototipo no puede analizar 1d ni ejecutar walk-forward.
- 4h con 2 años cubre el lookback y deja historia para regímenes.
- 1min con 30 días es el compromiso entre tamaño (~43k barras) y utilidad
  intradía; un año de 1min dispara el almacenamiento a decenas de MB y no aporta
  al caso de uso principal.
- A 150 peticiones/minuto, 208 peticiones tardan ~1,4 min; 46 tardan ~0,3 min.
  Ambos caben en el objetivo de 3 min para el snapshot inicial.

### Presupuesto de rate limit

- Límite documentado: 220 llamadas/minuto por conexión/IP.
- Token bucket propio: capacidad 30, recarga 2,5 tokens/s (150/min).
- Separación mínima entre peticiones de 0,2 s.
- Si llega `rate_limit`, se respeta `retry_after` y se descuenta del presupuesto.
- El estado (`tokens`, `calls_last_minute`, `waited_ms`) se muestra en `/system`.

### Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| 20 días fijos (prototipo) | Insuficiente para 1d, 4h, regímenes y walk-forward |
| 1 año de 1min | ~525k barras por índice; no aporta al caso de uso y crece la memoria |
| Descargar todo cada arranque | Tarda minutos y desperdicia el límite de Deriv |
| Solo `count` sin `start/end` | No permite paginar ni recortar ventanas |

## D3. Almacenamiento (solo RAM, cero disco)

### Decisión

- **Cero escritura en disco en runtime.** No hay caché Parquet, JSON, CSV, base
  de datos ni exportación. `export_csv` del prototipo se elimina.
- Los datos históricos (velas por temporalidad + ticks) viven en `self.data`
  (DataFrames) en RAM durante el proceso.
- Los búferes en vivo usan `deque(maxlen=…)` circulares:
  - `_directional_returns`: máx 10.000 retornos direccionales por símbolo.
  - `live_ticks_records`: `deque(maxlen=50.000)` (en vez de la lista sin límite).
  - `live_events`: `deque(maxlen=5.000)`.
  - `_live_queue`: cola acotada (100.000).
- El `InstrumentSnapshot` es inmutable y vive en RAM (sin archivo).

### Tamaño estimado (RAM)

| Concepto | Cálculo | Tamaño |
|---|---:|---|
| Velas por defecto, ambos índices | ~202.730 filas × ~40 B en pandas | ~8–12 MB |
| Ticks históricos (5.000 × 2) | ~40 B/tick | < 1 MB |
| Búferes en vivo (50k × 2 + 10k × 2) | ~100 B/registro | ~12 MB |
| Snapshot en memoria (por índice) | ~300–800 KB | < 2 MB |
| **Total típico** | | **< 40 MB** |

Objetivo: **< 400 MB** en operación normal (2 símbolos, 6 temporalidades),
dejando margen para la UI y picos de cómputo.

### Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Parquet/CSV/JSON en disco | Directriz 4: cero escritura en disco |
| Base de datos (SQLite/PostgreSQL) | Añade persistencia; 200k filas no lo justifican; viola cero disco |
| DuckDB | Añade dependencia y almacenamiento |
| Listas sin límite para ticks en vivo | Crecen sin cota y rompen el objetivo de RAM < 400 MB |

## D4. Snapshots y estrategia de cómputo

### Decisión

- `InstrumentSnapshot` es inmutable y contiene un mapa de `TimeframeAnalysis`.
- Al arrancar se precalculan `5min` y `1h` para ambos índices. Las demás
  temporalidades se calculan a demanda y se guardan en el mismo snapshot.
- El cálculo pesado (EVT, GARCH, regímenes, supervivencia, walk-forward) corre
  en un worker de background con una cola por instrumento. La UI nunca bloquea
  el event loop de FastAPI.
- El snapshot se publica con `SnapshotStore.swap(snapshot)`: se construye una
  copia completa y se reemplaza la referencia con un lock; los lectores siempre
  ven una versión consistente.
- Cada snapshot tiene `snapshot_id` (UUID5 de símbolo + `generated_at` +
  `config_hash`) y `data_version`. Las figuras se cachean por
  `(snapshot_id, section, theme)` con LRU de 32 entradas.
- Un refresco concurrente no muta el snapshot visible; si ya hay uno en curso
  para el mismo instrumento, el segundo se coalesce.

### Por qué no calcular todo al inicio

- EVT, GARCH, regímenes y walk-forward sobre 6 temporalidades × 2 índices
  multiplican el tiempo de arranque y el uso de memoria.
- El usuario solo necesita una temporalidad a la vez; precalcular 5min y 1h
  cubre la vista por defecto y la comparativa.
- Los análisis pesados se pueden ejecutar "a demanda" con un botón y mostrar
  progreso, sin bloquear la navegación.

### Formato y tamaño

- JSON con `schema_version` y `config_hash`; sin `NaN`/`Infinity`.
- Un snapshot completo ronda 300–800 KB; el límite práctico es 2 MB.
- Los DataFrames de velas no se serializan dentro del snapshot; se referencian
  por `candles_ref` y la UI los lee de la caché en memoria.

## D5. Correcciones estadísticas (optativas)

> **Estado**: estas correcciones **NO se aplican por defecto**. La migración
> replica exactamente el prototipo (`p_null = 0.5`; BH solo en walk-forward). Se
> aplican solo con autorización explícita (ver `VISION.md` §5).

### D5.1 Null de zonas por geometría

El prototipo usa `p_null = 0.5` para el test binomial. Si se autoriza la
corrección, para un paseo aleatorio sin deriva la probabilidad de tocar el target
antes que el stop, partiendo del centro de la zona, es:

```text
p0 = b / (a + b)
```

donde `a` es la distancia al target en ATR y `b` la distancia al stop en ATR.
Con `a = 1,0` y `b = 0,75`, `p0 = 0,75 / 1,75 = 0,4286`. Con la corrección
autorizada, el test binomial usaría `p0` como hipótesis nula y alternativa
`greater`; por defecto se mantiene `0.5`.

- `a` y `b` se calculan con el ATR de la barra anterior al primer toque del
  episodio y con el precio de entrada (cierre de la barra de toque o centro de
  la zona si el cierre está dentro).
- Si la entrada real está en el borde de la zona, `a` y `b` se ajustan a la
  distancia desde ese precio; no se usa el centro ciego.
- Sensibilidad: se repite con una muestra placebo de entradas aleatorias en las
  mismas fechas y mismos multiplicadores; si el `p0` empírico difiere del
  geométrico en más de 0,05, se reporta la diferencia y se usa el geométrico
  como primario.
- El `p0` se muestra en la tabla y en Metodología; nunca se oculta.

### D5.2 Múltiples comparaciones (FDR)

- Por defecto se replica el prototipo: BH solo en walk-forward. Si se autoriza la
  corrección, BH se aplica por familia `(símbolo, temporalidad, etapa)` donde
  etapa es `in_sample` u `oos`.
- Se reportan `p_raw`, `p_adj` (q-value) y `reject` con `q = 0,10`.
- Las zonas no se ordenan solo por tasa; la tabla muestra `n`, IC, `p_adj` y
  etiqueta de etapa.
- Si no hay suficientes zonas para BH (n < 2), `p_adj = p_raw` y se advierte.

### D5.3 Toques ambiguos y pendientes

- **Ambiguo**: target y stop se tocan en la misma barra (o en el mismo tick, si
  se evalúa tick a tick). Se excluye del denominador principal y se muestra su
  conteo.
- **Pendiente**: no se tocó ni target ni stop dentro del horizonte. Se excluye
  del denominador y se muestra como censurado a la derecha.
- **Sensibilidad**: se muestra la tasa si los ambiguos se contaran como fallo y
  si los pendientes se contaran como fallo. Esto evita cherry-picking.
- `evaluated_touches = reactions + failures`. `rate = reactions / evaluated`.

### D5.4 EVT/GPD

- Excesos direccionales: para Boom, `close_t - close_{t-1}` positivo; para
  Crash, el negativo en valor absoluto. Se trabaja con excesos sobre un umbral
  `u`.
- Umbral: percentil 95 de los excesos; si deja menos de 100 excesos, se baja al
  percentil 90; si aun así hay menos de 30, se declara insuficiente.
- Ajuste MLE de GPD(`xi`, `sigma`) con `scipy.optimize.minimize` (L-BFGS-B) y
  matriz de información observada para los errores estándar. IC al 95 % por
  Wald; si `xi` está cerca de 0, se usa el límite exponencial.
- Diagnóstico: KS sobre la CDF ajustada, gráfico de exceso medio y
  estabilidad del umbral (2–3 umbrales alternativos).
- Niveles de retorno:

```text
x_m = u + (sigma / xi) * ((m / n_u)^xi - 1)   si xi != 0
x_m = u + sigma * ln(m / n_u)                  si xi == 0
```

donde `m` es el periodo de retorno en barras y `n_u` el número de excesos.
- `fit_evt_gpd(excesses, threshold)` es el envoltorio que faltaba; devuelve
  `xi`, `sigma`, `se_xi`, `se_sigma`, IC, `n_excess`, `ks_p`, `converged` y
  `message`. No se implementa dos veces: `fit_gpd_mle` sigue siendo el núcleo
  numérico y `fit_evt_gpd` añade validación, diagnóstico y metadatos.

### D5.5 GARCH y regímenes

- GARCH(1,1) sobre retornos logarítmicos de velas (1min o 5min), multiplicados
  por 100 para estabilidad numérica; media constante; distribución normal o t
  según AIC.
- Se reportan `omega`, `alpha[1]`, `beta[1]`, p-valores, AIC/BIC, persistencia,
  pronóstico a 10 pasos con IC y diagnósticos de residuos estandarizados
  (Ljung-Box).
- Gate: ≥ 200 observaciones; preferido ≥ 500. No se ajusta GARCH sobre ticks
  irregulares.
- Regímenes: Markov switching de 2 estados sobre retornos (varianza
  dependiente del régimen) con `statsmodels`; si no converge, CUSUM sobre
  retornos al cuadrado como fallback. Los estados se ordenan por varianza para
  evitar label switching. Se reporta la probabilidad suavizada del régimen de
  alta volatilidad y la fecha del último cambio.
- Gate: ≥ 500 observaciones. La UI advierte que el régimen es descriptivo y no
  una señal de entrada.

### D5.6 Supervivencia

- **Modo índice de evento**: distancia en barras entre eventos consecutivos.
- **Modo tiempo**: segundos entre eventos.
- Kaplan-Meier con censura a la derecha: el último intervalo se censura si el
  periodo termina antes del siguiente evento.
- Se reportan `n`, eventos observados, censurados, mediana, IC y la curva.
- Gate: ≥ 20 eventos. La UI indica el modo y la censura.
- No se usa el p-valor de log-rank con folds solapados; si se muestra, es
  descriptivo.

### D5.7 Autocorrelación y diagnósticos

- ACF de retornos y de retornos al cuadrado, `max_lag = 50`, banda
  `±1,96/√n`.
- Ljung-Box en lags 10 y 20; Jarque-Bera; ARCH-LM con 10 lags; ADF con
  regresión constante.
- Gate: ≥ 100 barras. Se calcula sobre barras temporales, no sobre ticks.
- La UI muestra `n`, lags y la advertencia de que la significancia no implica
  previsibilidad económica.

### D5.8 Walk-forward

- Ventana expansiva (anchored): el entrenamiento empieza siempre en la primera
  barra y crece; el test es la siguiente ventana.
- Configuración por defecto: `train = max(40 % de la serie, 500 barras)`,
  `test = max(15 % de la serie, 100 barras)`, `step = test`.
- Mínimo 3 folds válidos. Si no se alcanzan, se declara "historia insuficiente"
  y se recomienda una temporalidad mayor.
- Aplicable a 5min, 15min, 1h, 4h y 1d. 1min se marca "no aplicable" porque su
  ventana de 30 días no permite un walk-forward honesto.
- Las zonas se detectan **solo** con el tramo de entrenamiento; se evalúan en
  el tramo de test. No se reutilizan zonas del test.
- Agregado OOS: suma de reacciones y evaluados, tasa, IC Wilson, sign test
  descriptivo y BH entre zonas.
- Advertencia fija: los folds comparten historia y las zonas pueden repetirse;
  los folds no son independientes. El p-valor del sign test es descriptivo.
- Gate de historia: 5min ≥ 60 días, 15min ≥ 90 días, 1h ≥ 180 días,
  4h ≥ 365 días, 1d ≥ 730 días.

### D5.9 Probabilidad condicional

- Se calcula `P(evento en los próximos H pasos | pasos desde el evento anterior
  en el bin b)`.
- Base histórica: barras para el modo histórico; ticks para el modo vivo si hay
  ≥ 300 ticks de warm-up y ≥ 30 eventos.
- IC Wilson por bin; si un bin tiene < 30 eventos, se agrupa con el siguiente o
  se marca insuficiente.
- Es descriptiva e in-sample; no se presenta como probabilidad predictiva.
- `conditional_event_table` del prototipo se porta con esta semántica y se
  añade la variante por barras para el histórico.

### D5.10 Eventos de vela

- Para Boom: `(high_t - close_{t-1}) / close_{t-1}`; para Crash:
  `(close_{t-1} - low_t) / close_{t-1}`.
- El umbral es el cuantil móvil de las `lookback` barras **anteriores**
  (`shift(1)`), nunca incluye la barra evaluada.
- Se reporta también la reversión cierre→extremo para no confundir una mecha
  con un movimiento sostenido.
- Se etiqueta "candidato de spike"; una vela OHLC no prueba el recorrido tick a
  tick.

## D6. Puertas de muestra

| Análisis | Mínimo | Preferido | Comportamiento si no se alcanza |
|---|---:|---:|---|
| Eventos de vela | 50 barras | 200 | "datos insuficientes" con mínimo |
| Eventos de tick | 300 ticks | 2.000 | "warm-up insuficiente" |
| Zonas (por zona) | 10 evaluados | 30 | "N/A" en tasa y p |
| Zonas (global) | 5 zonas | 20 | BH con advertencia |
| EVT/GPD | 30 excesos | 100 | "datos insuficientes" |
| GARCH(1,1) | 200 barras | 500 | "datos insuficientes" |
| Regímenes | 500 barras | 1.000 | "datos insuficientes" |
| Supervivencia | 20 eventos | 50 | "datos insuficientes" |
| ACF/diagnósticos | 100 barras | 300 | "datos insuficientes" |
| Probabilidad condicional | 30 eventos/bin | 100 | bin agrupado o "N/A" |
| Walk-forward | 3 folds | 5 | "historia insuficiente" + recomendación |

Las puertas viven en `GateConfig` y se serializan en el snapshot para que la UI
y Metodología muestren exactamente el mismo valor.

## D7. UI y interacción

### Decisión

- SPA servida por FastAPI en `/static`; frontend HTML + CSS + JS + **Plotly.js**
  (copia local, sin CDN). Sin Dash; sin `plotly` de Python.
- Barra superior: botones **Boom 1000** / **Crash 500**, estado de conexión en
  tiempo real y latencia.
- Selector de temporalidad: 1min, 5min, 15min, 1h, 4h, 1d.
- Menú interno por índice con 6 secciones: (1) Tiempo Real y Velas/Zonas,
  (2) Distribución y Colas EVT, (3) Volatilidad y GARCH, (4) Supervivencia y
  Autocorrelación, (5) Heatmaps, Diagnósticos y Walk-Forward, (6) Configuración
  del Índice.
- Estado independiente por índice: una instancia de analizador por símbolo; nada
  se mezcla entre Boom y Crash.
- La sección Configuración expone todos los parámetros de `AnalyzerConfig` y de
  modelo, con guardar (recalcula en RAM) y restaurar por defecto.
- Todos los textos en español; los IDs y campos en inglés.

### Rendimiento

- Máximo 5.000 puntos por traza; si hay más, se muestra la última ventana y se
  indica "mostrando 5.000 de N barras".
- Downsampling LTTB para líneas largas; las velas se recortan, no se agregan.
- El frontend solicita solo los datos de la sección/temporalidad visible (render
  de vista activa) para acotar RAM.
- La vista de inicio usa solo el resumen, no las 6 temporalidades.

### Accesibilidad y estados

- Colores con contraste AA; no se depende solo del color para indicar estado.
- Estados: cargando, vacío, error, datos insuficientes, desactualizado,
  actualizando.
- Nunca un panel en blanco: siempre un mensaje y una acción sugerida.

## D8. Runtime, dependencias y Windows

- Python 3.13 gestionado con `uv`; se evita 3.14 por compatibilidad de
  `arch`/`statsmodels`.
- Dependencias directas: `fastapi`, `uvicorn`, `websocket-client`, `pandas`,
  `numpy`, `scipy`, `statsmodels`, `arch`, `pydantic`.
- Desarrollo: `pytest`, `pytest-cov`, `ruff`, `httpx`. Sin `plotly` de Python.
- Frontend: `plotly.min.js` como activo estático en `src/quantlab/static/`.
- `uv.lock` versionado; `pyproject.toml` con `requires-python = ">=3.13,<3.14"`.
- Arranque: `scripts/run.ps1` valida Python/uv, ejecuta `uv sync` si falta
  `.venv` y lanza `python -m quantlab`.
- Variables de entorno en `.env` (no versionado) y `.env.example` (versionado).
- Puerto por defecto 8000; si está ocupado, se prueba 8001 y se muestra la URL.
- Hardware objetivo: Windows 10/11, 8 GB RAM (~3,7 GB libres), i3-10100F.
- Logs a consola (UTF-8); sin archivos en disco en runtime.

## D9. Pruebas y reproducibilidad

| Capa | Qué cubre | Herramienta |
|---|---|---|
| Unidad | Wilson, binomial, BH, GPD, GARCH, zonas, ATR, eventos | pytest |
| Integración | Cliente con WS simulado, búferes en memoria, snapshots, calidad | pytest + fixtures |
| Contrato | Esquema JSON del snapshot, allowlist de llamadas | jsonschema, AST |
| UI | Layout, rutas, estados | FastAPI TestClient |
| Rendimiento | Inicio < 2 s, snapshot < 3 min, RAM < 400 MB | pytest-benchmark opcional |

- Fixtures deterministas en `tests/fixtures/`: velas y ticks en memoria, snapshot esperado.
- Semilla global `QUANTLAB_SEED=20261115`; `numpy.random.default_rng(seed)` en
  cada función que aleatoriza (bootstrap).
- Las pruebas de GPD usan una muestra sintética con `xi=0,2`, `sigma=1,0` y
  tolerancia ±0,05; las de GARCH usan una serie simulada con parámetros
  conocidos.
- El test de llamadas prohibidas escanea el AST de `src/` y busca las cadenas
  `buy`, `sell`, `cancel`, `proposal`, `portfolio`, `balance`, `statement` en
  payloads y nombres de métodos. La allowlist está en
  `contracts/forbidden-calls.md`.
- No se usan datos reales en tests; el WS se simula con un servidor local o un
  fake client.

## D10. Ticks: alcance y persistencia

- El histórico de ticks se limita a 5.000 por petición; se usa para detección de
  eventos y probabilidad condicional en vivo.
- El stream vivo mantiene un `deque` de 50.000 ticks y una cola de 20.000 con
  descarte contabilizado. Si la cola se llena, se descarta el tick más antiguo y
  se incrementa `dropped_ticks`.
- No se persisten ticks crudos ni eventos de tick en v1. Los eventos de tick se
  recalculan al inicio con el histórico de 5.000 ticks y se muestran como
  "sesión actual".
- Si más adelante se necesita histórico de spikes de tick, se persistirán solo
  eventos agregados (no ticks crudos) con un límite explícito y una nueva
  decisión de diseño.

Esto resuelve la pregunta abierta #1 del plan de forma conservadora: no se
añade almacenamiento de ticks en v1.

## D11. Decisiones sobre las preguntas abiertas

| Pregunta | Decisión |
|---|---|
| ¿Persistir eventos de tick? | No en v1; solo sesión en memoria. Reevaluar con evidencia de uso |
| ¿Local o publicado? | Local en `127.0.0.1`; si se publica, añadir auth, TLS y revisión de términos |
| ¿Entregables en inglés? | UI y docs en español; código y nombres de archivo en inglés; traducción posterior si se pide |

## D12. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Endpoint primario inestable (520) | Media | Alto | Fallback, spike temprano, errores accionables |
| Símbolos no activos en la jurisdicción | Media | Alto | Resolución contra `active_symbols`, mensaje claro |
| Límite de Deriv más estricto en `ticks_history` | Media | Medio | Token bucket 150/min, backoff, memoria, medir en spike |
| Historia insuficiente en 1d/4h | Baja | Medio | Ventanas por TF, ventana efectiva, gates |
| 5.000 ticks insuficientes para condicional | Media | Bajo | Gate, modo barras, etiqueta descriptiva |
| GARCH no converge | Media | Bajo | Gate, captura de error, estado "no aplicable" |
| Markov switching no converge | Media | Bajo | Fallback CUSUM, estado explícito |
| Caché corrupta | Baja | Bajo | Checksum, validación, reconstrucción |
| Scope creep | Alta | Alto | MVP vertical, milestones, feature registry |
| Python 3.14 rompe `arch` | Baja | Medio | Fijar 3.13 en `pyproject.toml` y `uv` |
| Términos de Deriv | Media | Medio | Atribución, no redistribuir crudos, revisar antes de publicar |

## D13. Presupuesto consolidado

| Recurso | Presupuesto | Verificación |
|---|---|---|
| Peticiones iniciales | 208 (1000/página) o 46 (5000/página) | Log de rate limit y duración |
| Tiempo de snapshot inicial | < 3 min ambos índices | Cronómetro en spike y test de integración |
| Inicio con datos en caliente | < 2 s | Test de rendimiento |
| RAM | < 400 MB | `tracemalloc`/monitor en ejecución |
| Caché | ≤ 1 GiB, típico < 20 MB | Manifiesto y test de evicción |
| Puntos por gráfico | ≤ 5.000 | Inspección de figuras |
| Snapshot JSON | < 2 MB | Validación de esquema y tamaño |

## D14. Spike técnico: criterios de salida

El spike se considera exitoso si:

1. El endpoint primario responde `active_symbols` en < 5 s desde Windows.
2. `CRASH500` y `BOOM1000` aparecen activos (o se documenta el símbolo
   alternativo y el mensaje de error).
3. Se descargan 5.000 velas de 5min con OHLC válido y timestamps UTC.
4. Se descargan 5.000 ticks con `prices`/`times` alineados.
5. Se completa una suscripción de 30 s con ≥ 10 ticks y se cierra limpiamente.
6. Se capturan y clasifican al menos 3 errores (transporte, API, rate limit).
7. El tiempo de 20 peticiones secuenciales es < 30 s con el token bucket.

Si falla el punto 1 o 2, se invierte el orden de endpoints y se documenta antes
de continuar. Si falla el punto 3 o 4, se ajustan las ventanas y se recalcula el
presupuesto.
