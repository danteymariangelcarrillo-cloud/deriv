# Feature Specification: Crash/Boom Quant Lab Dashboard

**Feature Branch**: `001-crash-boom-dashboard`

**Created**: 2026-11-15

**Updated**: 2026-11-16 (migración a FastAPI + WebSockets + Plotly.js, cero disco)

**Status**: Approved

**Input**: Migrar `boom_crash_quant_analyzer.py` a una aplicación web local de solo
lectura (FastAPI + WebSockets + Plotly.js) para los índices Crash 500 y Boom 1000
de Deriv, con menú por índice, tiempo real, cero escritura en disco y RAM < 400 MB
en Windows (8 GB / i3-10100F), preservando el 100 % del motor estadístico.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Comparativa y estado de ambos índices (Priority: P1)

Como analista, abro la aplicación y veo en la barra superior el selector Boom 1000
/ Crash 500 con **estado de conexión en tiempo real** y **latencia**, y en la
vista de inicio el estado de ambos índices: último precio, variación, candidatos de
spike detectados, cobertura de datos y hora UTC de la última actualización.

**Why this priority**: Es la primera prueba de que la ingesta, el núcleo
estadístico y la UI funcionan de extremo a extremo sin tocar disco.

**Independent Test**: Con datos de ambos símbolos cargados en memoria, la barra
superior muestra el estado de conexión y la latencia, y la vista de inicio muestra
los valores de ambos índices coincidentes con los datos en memoria.

**Acceptance Scenarios**:

1. **Given** datos en memoria de `CRASH500` y `BOOM1000`, **When** abro la app,
   **Then** la barra superior muestra estado de conexión (conectando/activo/error)
   y latencia, y puedo alternar entre índices.
2. **Given** que un índice no está activo o su descarga falló, **When** abro la
   app, **Then** el otro índice se muestra normalmente y el fallido presenta un
   mensaje accionable sin bloquear la página.
3. **Given** que los datos superan la antigüedad máxima, **When** abro la app,
   **Then** aparece un indicador visible de "datos desactualizados" con la hora de
   la última actualización.

---

### User Story 2 - Análisis por índice: velas, zonas y tiempo real (Priority: P1)

Como analista, selecciono Boom 1000 o Crash 500, elijo temporalidad (1min, 5min,
15min, 1h, 4h, 1d) y en la sección "Tiempo Real y Velas/Zonas" veo ticks en vivo
con detección de spikes, velas OHLC con zonas técnicas y una tabla de estadísticas
de zonas (toques, evaluados, tasa de reacción, intervalo de confianza y valor p).

**Why this priority**: Es el núcleo analítico del producto y el mínimo demostrable.

**Independent Test**: Con fixtures de 5min y 1h, el gráfico contiene las trazas de
velas, zonas y eventos esperadas; la tabla coincide con los resultados calculados;
las zonas sin muestra suficiente muestran "N/A".

**Acceptance Scenarios**:

1. **Given** un fixture de Boom 1000 en 5min, **When** abro su página, **Then** veo
   velas OHLC, zonas de soporte/resistencia y marcadores de eventos candidatos.
2. **Given** una zona con `evaluated_touches = 0`, **When** se muestra la tabla,
   **Then** la tasa y el valor p aparecen como "N/A" con explicación.
3. **Given** una temporalidad con menos barras que el mínimo, **When** la
   selecciono, **Then** veo "datos insuficientes" con el mínimo necesario.
4. **Given** un stream de ticks en vivo activo, **When** llegan ticks, **Then** el
   gráfico en vivo se actualiza y los spikes se marcan sin recargar la página.

---

### User Story 3 - Distribución, volatilidad y supervivencia (Priority: P2)

Como analista, profundizo en el comportamiento del índice: distribución de retornos
con colas extremas (EVT/GPD), Q-Q, mean excess, GARCH(1,1), regímenes de
volatilidad, supervivencia entre eventos, autocorrelación de retornos y de
retornos al cuadrado, y probabilidad condicional de eventos.

**Why this priority**: Aporta la profundidad cuantitativa diferencial. Depende de
la historia P1 y de datos suficientes.

**Independent Test**: Con series sintéticas de parámetros conocidos, los resultados
de EVT, GARCH y supervivencia coinciden con los valores esperados dentro de
tolerancia; con muestras pequeñas cada panel muestra "datos insuficientes".

**Acceptance Scenarios**:

1. **Given** al menos 100 excesos direccionales, **When** abro la sección EVT,
   **Then** veo histograma, Q-Q, ajuste GPD y mean excess plot con `xi`, `sigma` y
   umbral.
2. **Given** menos de 30 excesos, **When** abro EVT, **Then** veo "datos
   insuficientes" con el mínimo requerido.
3. **Given** retornos de velas, **When** abro volatilidad, **Then** veo GARCH(1,1)
   con parámetros, p-valores, AIC/BIC y pronóstico, y regímenes de volatilidad.
4. **Given** eventos candidatos, **When** abro supervivencia, **Then** veo la curva
   Kaplan-Meier con `n` y modo (índice de evento o tiempo).

---

### User Story 4 - Walk-forward y honestidad estadística (Priority: P2)

Como analista, verifico si las zonas de reacción mantienen su comportamiento fuera
de muestra mediante walk-forward, y veo tasas OOS, intervalos de confianza,
p-valores ajustados por FDR y el número de folds.

**Why this priority**: Es la defensa contra el sesgo retrospectivo y una de las
mayores fortalezas del prototipo actual.

**Independent Test**: Con una serie sintética con zonas plantadas, el walk-forward
produce los folds esperados; con 5min y menos de 60 días, muestra "historia
insuficiente" y sugiere 15min o 1h.

**Acceptance Scenarios**:

1. **Given** datos de 1h con historia suficiente, **When** ejecuto walk-forward,
   **Then** veo folds, tasa OOS, IC y p-valor ajustado.
2. **Given** datos de 5min con menos de 60 días, **When** abro walk-forward,
   **Then** veo "historia insuficiente" y una recomendación de temporalidad.

---

### User Story 5 - Diagnósticos, configuración y cero disco (Priority: P2/P3)

Como analista o evaluador, consulto los heatmaps precio-hora/eventos, las pruebas
residuales (Ljung-Box, Jarque-Bera, ARCH-LM, ADF), y en la sección "Configuración
del Índice" ajusto los parámetros de cada índice por separado; al guardar, las
métricas y gráficas se recalculan en RAM al instante, con botón de restauración.

**Why this priority**: Da trazabilidad y control. La configuración por índice sin
mezclar estados es un requisito explícito.

**Independent Test**: Un test verifica que cambiar un parámetro en Boom 1000 no
afecta a Crash 500, que al guardar se recalculan las métricas en RAM sin escribir
en disco, y que el botón "Restaurar" devuelve los valores por defecto.

**Acceptance Scenarios**:

1. **Given** la sección Configuración de Boom 1000, **When** cambio un parámetro y
   guardo, **Then** las métricas/gráficas de Boom 1000 se recalculan y los de Crash
   500 no cambian.
2. **Given** una configuración modificada, **When** pulso "Restaurar valores por
   defecto", **Then** se restablecen los valores por defecto y se recalcula.
3. **Given** el código de producción, **When** se ejecuta el test de llamadas
   prohibidas, **Then** no se encuentra ningún endpoint de trading/cuenta/ejecución.
4. **Given** una sesión completa, **When** reviso el sistema de archivos, **Then**
   no se ha escrito ningún CSV, base de datos, caché ni archivo de exportación.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema MUST ser de solo lectura y MUST NOT llamar a ningún
  endpoint de trading, cuenta o ejecución (`buy`, `sell`, `cancel`, `proposal`,
  `portfolio`, `balance`, `statement`).
- **FR-002**: El sistema MUST mantener un registro de instrumentos con `CRASH500`
  (dirección down) y `BOOM1000` (dirección up), extensible por configuración.
- **FR-003**: El sistema MUST resolver cada símbolo contra `active_symbols` y
  MUST fallar con un diagnóstico claro si no está activo.
- **FR-004**: El sistema MUST descargar velas OHLC para 1min, 5min, 15min, 1h, 4h
  y 1d con paginación, deduplicación, validación OHLC y timestamps UTC.
- **FR-005**: El sistema MUST descargar historial de ticks y mantener una
  suscripción en vivo con reconexión y contador de descartes.
- **FR-006**: El sistema MUST calcular y mostrar calidad de datos: barras
  esperadas vs recibidas, cobertura, huecos, duplicados y antigüedad.
- **FR-007**: El sistema MUST detectar candidatos de spike por velas y por ticks
  con umbrales calculados solo con información anterior.
- **FR-008**: El sistema MUST detectar zonas de reacción por pivotes y calcular
  toques, evaluados, reacciones, fallos, ambiguos, pendientes, tasa, IC y p-valor.
- **FR-009**: El sistema MUST calcular el valor nulo de las zonas; por defecto
  replica el prototipo (p_null = 0.5). El cálculo geométrico `p0 = b/(a+b)` es una
  corrección optativa documentada, pendiente de autorización.
- **FR-010**: El sistema MUST aplicar Benjamini-Hochberg a las comparaciones
  múltiples del walk-forward (como en el prototipo); la corrección BH in-sample es
  optativa, pendiente de autorización.
- **FR-011**: El sistema MUST ajustar una GPD a los excesos direccionales y
  reportar `xi`, `sigma`, umbral, número de excesos, niveles de retorno y gráfico
  de exceso medio (implementando la función faltante `fit_evt_gpd`).
- **FR-012**: El sistema MUST ajustar GARCH(1,1) sobre retornos de barras
  temporales y reportar parámetros, p-valores, AIC/BIC y pronóstico.
- **FR-013**: El sistema MUST detectar regímenes de volatilidad (Markov switching
  con fallback CUSUM) implementando la función faltante `detect_volatility_regimes`.
- **FR-014**: El sistema MUST calcular curvas de supervivencia entre eventos en
  modo índice de evento y modo tiempo.
- **FR-015**: El sistema MUST calcular autocorrelación de retornos y de retornos al
  cuadrado, y diagnósticos Ljung-Box, Jarque-Bera, ARCH-LM y ADF.
- **FR-016**: El sistema MUST calcular la probabilidad condicional descriptiva de un
  evento en los siguientes H pasos.
- **FR-017**: El sistema MUST ejecutar validación walk-forward con ventanas de
  entrenamiento, prueba y paso configurables, reportando métricas OOS.
- **FR-018**: El sistema MUST mostrar una barra superior con botones Boom 1000 /
  Crash 500, estado de conexión en tiempo real y latencia.
- **FR-019**: El sistema MUST ofrecer un selector de temporalidad (1min, 5min,
  15min, 1h, 4h, 1d) y un menú interno con 6 secciones por índice: (1) Tiempo Real
  y Velas/Zonas, (2) Distribución y Colas EVT, (3) Volatilidad y GARCH,
  (4) Supervivencia y Autocorrelación, (5) Heatmaps, Diagnósticos y Walk-Forward,
  (6) Configuración del Índice.
- **FR-020**: El sistema MUST mantener el estado de cada índice independiente
  (una instancia de analizador por índice); ningún parámetro ni resultado se
  mezcla entre Boom y Crash.
- **FR-021**: Toda métrica inferencial MUST mostrar `n`, intervalo de confianza
  cuando aplique y etiqueta in-sample/OOS.
- **FR-022**: El sistema MUST aplicar puertas de muestra mínima y mostrar "datos
  insuficientes" en lugar de resultados no confiables.
- **FR-023**: El sistema MUST usar la terminología "candidato de spike" y mostrar
  el aviso de que no es un spike oficial ni una señal de trading.
- **FR-024**: El sistema MUST permitir ajustar en la sección Configuración todos
  los parámetros de `AnalyzerConfig` (cuantiles, lookbacks, tolerancia ATR,
  pivotes, horizontes) y parámetros de modelo (ventana móvil, orden GARCH p/q,
  umbral EVT, ventanas walk-forward, límites de visualización en RAM).
- **FR-025**: Al guardar la configuración, el sistema MUST recalcular en RAM las
  métricas y gráficas al instante, y ofrecer un botón "Restaurar valores por
  defecto".
- **FR-026**: El sistema MUST mantener una conexión WebSocket persistente por
  índice con reconexión automática y búferes circulares (`deque`) en RAM.
- **FR-027**: El sistema MUST renderizar en el navegador solo la vista activa,
  manteniendo el uso de RAM < 400 MB en operación normal.
- **FR-028**: El sistema MUST NOT persistir datos en disco en tiempo de ejecución:
  prohibido CSV, bases de datos, caché y archivos. El export manual en memoria
  (`BytesIO`; JSON/CSV/ZIP) está permitido y nunca toca disco; `export_csv` del
  prototipo se sustituye por `ExportService` en memoria.
- **FR-029**: El sistema MUST respetar el límite de Deriv con token bucket y una
  sola conexión persistente para histórico.
- **FR-030**: El sistema MUST aislar fallos por instrumento: un índice caído no
  impide ver el otro.
- **FR-031**: El sistema MUST registrar solicitudes, latencias, errores, reintentos
  y descartes de ticks para diagnóstico.
- **FR-032**: El sistema MUST ser reproducible: versiones fijadas, semillas
  explícitas y fixtures deterministas.
- **FR-033**: El sistema MUST incluir tests del núcleo estadístico, integración con
  fixtures, smoke tests de la UI y un test de llamadas prohibidas.
- **FR-034**: La UI y la documentación MUST estar en español; los identificadores y
  nombres de archivo en inglés.
- **FR-035**: La UI MUST ser responsive y usable en un navegador moderno de
  escritorio, sin plugins.

### Non-Functional Requirements

- **NFR-001**: Con datos en memoria, la vista de inicio MUST responder en menos de
  2 segundos en el baseline de rendimiento.
- **NFR-002**: La construcción inicial de datos para ambos índices MUST completarse
  en menos de 3 minutos mostrando progreso por símbolo y temporalidad.
- **NFR-003**: El proceso MUST usar menos de 400 MB de RAM en operación normal
  (baseline de rendimiento).
- **NFR-004**: La app MUST arrancar con un solo comando en Windows 10/11.
- **NFR-005**: La app MUST funcionar sin base de datos y sin servicios externos
  obligatorios además de Deriv.
- **NFR-006**: No MUST haber secretos, tokens ni credenciales en el repositorio.
- **NFR-007**: El sistema MUST respetar el presupuesto de 220 llamadas/minuto de
  Deriv, con margen de seguridad.
- **NFR-008**: Los módulos críticos de estadística e ingesta MUST tener tests
  deterministas con fixtures.
- **NFR-009**: Los snapshots en memoria MUST ser reproducibles: mismo fixture,
  misma configuración y misma semilla producen los mismos valores (salvo
  timestamps).
- **NFR-010**: La UI MUST mostrar estados de carga, error, vacío y datos
  insuficientes; no MUST dejar paneles en blanco sin explicación.
- **NFR-011**: La app MUST funcionar en UTC y MOSTRAR la zona horaria de forma
  explícita.

**Baseline de rendimiento** (aplica a NFR-001…NFR-003, SC-001 y SC-007): Windows
10/11, CPU **i3-10100F**, **8 GB de RAM (~3,7 GB libres)** y Python 3.13.
*Operación normal* significa 2 símbolos, 6 temporalidades en memoria y refresco de
60 s. *Carga en caliente* significa datos ya en memoria, sin descargas. Los tests
de rendimiento usan la mediana de 5 ejecuciones.

### Key Entities

- **Instrument**: símbolo (`CRASH500`, `BOOM1000`), nombre visible, dirección
  (`up`/`down`), ventanas de historia por temporalidad, habilitado.
- **Candle**: `epoch`, `open`, `high`, `low`, `close`, temporalidad, símbolo.
- **Tick**: `epoch`, `price`, `received_at`, símbolo, índice de secuencia.
- **EventCandidate**: tipo (vela/tick), dirección, tiempo, precio, magnitud,
  umbral previo, símbolo, temporalidad.
- **ReactionZone**: tipo, centro, límites, tolerancia, toques, evaluados,
  reacciones, fallos, ambiguos, pendientes, tasa, IC, p-valor.
- **DataQualityReport**: cobertura, barras esperadas/recibidas, huecos,
  duplicados, antigüedad, ticks descartados.
- **InstrumentSnapshot** (en memoria): instrumento, `generated_at` UTC, versión de
  configuración, datos, resultados, calidad y estado por análisis.
- **WalkForwardFold**: rangos train/test, zona, métricas OOS, p-valor ajustado FDR.
- **AnalyzerConfig** (por índice): cuantiles, lookbacks, tolerancia ATR, pivotes,
  horizontes, y límites de render en RAM.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un usuario puede abrir la app y ver ambos índices con datos en menos
  de 2 segundos en el baseline de rendimiento.
- **SC-002**: El 100 % de las funciones del prototipo está mapeado en la matriz de
  migración y tiene tarea, test o razón documentada de no aplicación.
- **SC-003**: El test de llamadas prohibidas pasa y no encuentra ningún endpoint
  de trading, cuenta o ejecución.
- **SC-004**: El 100 % de las tasas y probabilidades visibles muestra `n`, IC o
  "N/A", y etiqueta in-sample/OOS.
- **SC-005**: El walk-forward se ejecuta con la configuración por defecto en al
  menos 15min y 1h, o muestra "historia insuficiente".
- **SC-006**: La app no escribe ningún archivo en disco durante su ejecución
  normal (verificado por un test de "cero disco").
- **SC-007**: La construcción inicial de ambos snapshots tarda menos de 3 minutos
  y muestra progreso por símbolo y temporalidad.
- **SC-008**: Un usuario puede llegar a cualquier índice y sección en dos clics o
  menos desde el menú.
- **SC-009**: Toda la UI y la documentación están en español, salvo términos
  técnicos y nombres de campos.
- **SC-010**: La app arranca con un comando en Windows y muestra la URL local.
- **SC-011**: Los tests de estadística, ingesta, contratos y UI pasan en CI local;
  los módulos críticos tienen cobertura verificable.
- **SC-012**: Si Deriv no responde, la app sigue cargando y muestra errores
  accionables; no hay excepciones no controladas.
- **SC-013**: La RAM del proceso se mantiene por debajo de 400 MB en operación
  normal (medición con `tracemalloc` o monitor).

## Assumptions

- Los datos públicos de Deriv están disponibles sin autenticación en
  `wss://api.derivws.com/trading/v1/options/ws/public` y los símbolos
  `CRASH500`/`BOOM1000` están activos en la jurisdicción del usuario.
- El proyecto es de un solo usuario local; no se requiere login ni multiusuario.
- Python 3.13 y `uv` están disponibles; las dependencias científicas tienen ruedas
  compatibles.
- Plotly.js se sirve como activo estático local (sin CDN); no es una escritura en
  disco en tiempo de ejecución.
- El prototipo (`boom_crash_quant_analyzer.py` y el notebook) se conserva en
  `legacy/` como referencia histórica, sin eliminar funcionalidad.

## Out of Scope (v1)

Trading, señales, entradas al mercado, predicciones garantizadas, autenticación,
multiusuario, base de datos, persistencia en disco de cualquier tipo y
redistribución de datos crudos de Deriv.
