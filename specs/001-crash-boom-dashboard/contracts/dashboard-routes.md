# Contracts: Rutas y Navegación (FastAPI + SPA)

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16

Contrato de navegación y estado de la SPA servida por FastAPI. La app es de solo
lectura y se ejecuta en `127.0.0.1`. El frontend (HTML + JS + Plotly.js) se sirve
desde `/static`.

## 1. Rutas HTTP (FastAPI)

| Ruta | Método | Contenido |
|---|---|---|
| `/` | GET | SPA (`index.html`) |
| `/static/*` | GET | `app.js`, `plotly.min.js`, `styles.css` |
| `/healthz` | GET | `{"status":"ok"}` |
| `/api/instruments` | GET | Registro de instrumentos: símbolo, nombre, dirección, estado de conexión, latencia |
| `/api/<symbol>/summary` | GET | Resumen por índice (precio, variación, spikes, cobertura, antigüedad) |
| `/api/<symbol>/<section>` | GET | Datos JSON de la sección para la temporalidad (`?tf=`) |
| `/api/<symbol>/config` | PUT | Aplica configuración del índice; recalcula en RAM; devuelve snapshot |
| `/api/<symbol>/config/reset` | POST | Restaura configuración por defecto |
| `/ws/<symbol>` | WS | Push de ticks en vivo y métricas al navegador |

## 2. Secciones por índice (menú interno)

Cada índice tiene exactamente 6 secciones, con estado independiente:

| id | Sección | Contenido |
|---|---|---|
| `realtime` | Tiempo Real y Velas/Zonas | Ticks en vivo + spikes, velas OHLC, zonas, tabla de estadísticas |
| `distribution` | Distribución y Colas EVT | Histograma, Q-Q, ajuste GPD, mean excess |
| `volatility` | Volatilidad y GARCH | Volatilidad móvil, regímenes, parámetros GARCH |
| `survival` | Supervivencia y Autocorrelación | Kaplan-Meier, ACF, probabilidad condicionada |
| `diagnostics` | Heatmaps, Diagnósticos y Walk-Forward | Heatmaps precio-hora/eventos, pruebas residuales, walk-forward |
| `config` | Configuración del Índice | Parámetros de `AnalyzerConfig` y de modelo; guardar y restaurar |

## 3. Barra superior

| Elemento | id | Descripción |
|---|---|---|
| Toggle índice | `instrument-toggle` | Botones Boom 1000 / Crash 500 |
| Estado conexión | `connection-status` | `connecting` / `active` / `error` |
| Latencia | `latency-badge` | ms del último handshake/mensaje |
| Selector TF | `tf-selector` | 1min, 5min, 15min, 1h, 4h, 1d |
| Menú secciones | `section-menu` | 6 secciones |

## 4. Componentes de la SPA

| id | Tipo | Contenido |
|---|---|---|
| `summary-cards` | div | Tarjetas por índice |
| `price-chart` | Plotly.js | Velas + zonas + eventos |
| `zones-table` | tabla | Zonas con `n`, IC, p-valor, etapa |
| `evt-panel` | Plotly.js | Histograma, Q-Q, GPD, mean excess |
| `volatility-panel` | Plotly.js | ATR, vol realizada, regímenes |
| `survival-panel` | Plotly.js | Kaplan-Meier |
| `acf-panel` | Plotly.js | ACF retornos y retornos² |
| `diagnostics-table` | tabla | Ljung-Box, JB, ARCH-LM, ADF |
| `walkforward-panel` | Plotly.js + tabla | Folds, tasa OOS, IC, p-adj |
| `config-form` | formulario | Parámetros + guardar + restaurar |

## 5. Estado en la URL

El estado de navegación se refleja en `query string` para poder compartir la
vista (sin recargar la app):

```text
/?symbol=BOOM1000&tf=5min&section=distribution
```

## 6. Estados de la UI

| Estado | Señal visual |
|---|---|
| Cargando | skeleton |
| Vacío | mensaje "sin datos" |
| Error | banner con código y acción |
| Datos insuficientes | panel de gate con mínimo requerido |
| Desactualizado | badge de staleness + hora UTC |
| Actualizando | indicador "actualizando…" |

## 7. Reglas de render

- El frontend solicita solo los datos de la sección/temporalidad visible.
- Máximo 5.000 puntos por traza; si hay más, se muestra la última ventana y se
  indica "mostrando 5.000 de N".
- No se renderiza ninguna sección que no esté activa (para acotar RAM).
