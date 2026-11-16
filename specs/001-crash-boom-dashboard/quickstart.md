# Quickstart: Crash/Boom Quant Lab Dashboard

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16

Guía para instalar, ejecutar y verificar el dashboard en Windows 10/11. La app es
**de solo lectura**, **no escribe nada en disco** en tiempo de ejecución y se
ejecuta en `127.0.0.1`.

## 1. Requisitos

| Requisito | Valor | Verificación |
|---|---|---|
| SO | Windows 10/11 | `winver` |
| Hardware | 8 GB RAM (~3,7 GB libres), CPU i3-10100F o superior | `Get-CimInstance Win32_ComputerSystem` |
| Python | 3.13 (gestionado con `uv`) | `uv python list` |
| `uv` | ≥ 0.13 | `uv --version` |
| Red | Acceso a Deriv WS (solo datos públicos) | spike técnico (T013–T018) |

## 2. Instalación

```powershell
cd C:\Users\User\Documents\TRADING\proyectocolap\PROYECTOCOLAP
uv python install 3.13
uv python pin 3.13
uv sync
```

Esto crea `.venv` con Python 3.13 e instala: `fastapi`, `uvicorn`,
`websocket-client`, `pandas`, `numpy`, `scipy`, `statsmodels`, `arch`, `pydantic`
(y dev: `pytest`, `ruff`). **No** se instala `plotly` de Python.

## 3. Ejecución

```powershell
.\scripts\run.ps1
```

`run.ps1` valida Python/uv, ejecuta `uv sync` si falta `.venv` y lanza:

```text
python -m quantlab
```

Abre `http://127.0.0.1:8000` en Chrome/Edge/Firefox.

### Modo demo (sin red)

Para probar sin conexión a Deriv, define en `.env`:

```text
QUANTLAB_DEMO=1
QUANTLAB_DEMO_FAIL=0
```

Con `QUANTLAB_DEMO_FAIL=1` se simula la caída de un índice para verificar el
aislamiento de fallos.

## 4. Variables de entorno (`.env.example`)

| Variable | Default | Descripción |
|---|---|---|
| `QUANTLAB_WS_URL` | `wss://api.derivws.com/trading/v1/options/ws/public` | Endpoint primario |
| `QUANTLAB_RATE_LIMIT_PER_MIN` | `150` | Token bucket (Deriv permite 220) |
| `QUANTLAB_MAX_RAM_MB` | `400` | Presupuesto de RAM objetivo |
| `QUANTLAB_RENDER_MAX_POINTS` | `5000` | Puntos máximos por traza |
| `QUANTLAB_STALE_SECONDS` | `900` | Antigüedad para "datos desactualizados" |
| `QUANTLAB_DEMO` | `0` | `1` usa fixtures en memoria (sin red) |
| `QUANTLAB_DEMO_FAIL` | `0` | `1` simula fallo de un símbolo |

## 5. Verificación

1. La **barra superior** muestra **Boom 1000** / **Crash 500**, estado de
   conexión (conectando/activo/error) y latencia.
2. El **selector de temporalidad** permite 1min, 5min, 15min, 1h, 4h, 1d.
3. Cada índice tiene 6 secciones: Tiempo Real y Velas/Zonas · Distribución y Colas
   EVT · Volatilidad y GARCH · Supervivencia y Autocorrelación · Heatmaps,
   Diagnósticos y Walk-Forward · Configuración del Índice.
4. En **Configuración del Índice** cambia un parámetro, pulsa **Guardar** y
   verifica que solo ese índice se recalcula; pulsa **Restaurar valores por
   defecto** para volver al inicio.
5. Verifica que cambiar la configuración de Boom 1000 **no** afecta a Crash 500.

## 6. Verificación de "cero disco"

```powershell
uv run pytest tests/contract/test_zero_disk.py -q
```

Este test ejecuta el pipeline y confirma que no se crea ningún archivo. No hay
CSV, caché, base de datos ni exportación.

## 7. Verificación completa

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

## 8. Solución de problemas

| Síntoma | Causa probable | Acción |
|---|---|---|
| Handshake 520 | Gateway/origen de Deriv | Reintenta; prueba desde otra red |
| Símbolo no activo | Jurisdicción | Revisa `active_symbols`; ajusta configuración |
| "Datos insuficientes" | Poca historia en 1d/4h | Usa 15min/1h o espera más datos |
| RAM > 400 MB | Búferes sin acotar | Revisa deques; reduce puntos renderizados |
| Puerto ocupado | Otro proceso en 8000 | `run.ps1` prueba 8001 |

## 9. Estructura (resumen)

```text
src/quantlab/
├── analytics/            # Motor estadístico preservado 1:1
├── charts/               # Cómputo de datos para trazas (sin Plotly de Python)
├── data/                 # Cliente Deriv, rate limiter, limpieza
├── live/                 # Stream en vivo (deques acotados)
├── dashboard/            # FastAPI (app, rutas, WS, schemas)
└── static/               # Frontend: index.html, app.js, plotly.min.js, styles.css
legacy/                   # Prototipo + notebook (referencia)
```

## 10. Notas

- **Cero disco**: la app no escribe CSV, base de datos, caché ni exportación.
- **Memoria**: los búferes en vivo usan `deque` circulares; RAM objetivo < 400 MB.
- **Idioma**: UI y documentación en español; código e identificadores en inglés.
- **Datos**: solo lectura, públicos de Deriv, con atribución; sin redistribuir.
