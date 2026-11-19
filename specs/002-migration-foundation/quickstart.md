# Quickstart: Migration Foundation — Crash/Boom Quant Lab Dashboard

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16

Guía para instalar, ejecutar y verificar la **Fase 1 (Backend + APIs)** en Windows 10/11. La aplicación es de **solo lectura**, **CERO DISCO**, sin autenticación.

> **Estado**: Este quickstart describe el **objetivo** de la Fase 1, que **todavía no está implementada**. Sólo existen `src/quantlab/utils.py` y sus tests; no hay app FastAPI, rutas, `scripts/`, `settings.py` ni cliente Deriv. Los comandos de esta guía fallarán hasta completar las tareas de `tasks.md`. El frontend (Vanilla JS + Plotly.js) es Fase 4.

---

## 1. Requisitos

| Requisito | Versión | Verificación |
|-----------|---------|--------------|
| Windows | 10 u 11 | `winver` |
| Python | 3.13.x | `py -3.13 --version` |
| uv | ≥ 0.5 | `uv --version` |
| Hardware | CPU x86-64 4 núcleos, 8 GB RAM (3.7 GB libres) | `Get-CimInstance Win32_ComputerSystem` |
| Disco | ≥ 500 MB libre (entorno + caché opcional) | `Get-PSDrive C` |
| Red | Acceso a `wss://api.derivws.com` | `Test-NetConnection api.derivws.com -Port 443` |

**Baseline hardware:** i3-10100F (4C/4T, 3.6–4.3 GHz), 8 GB RAM ~3.7 GB libres.

---

## 2. Instalación

```powershell
cd C:\Users\User\Documents\TRADING\proyectocolap\PROYECTOCOLAP
uv sync
Copy-Item .env.example .env
```

`uv sync` crea `.venv`, instala dependencias fijadas en `uv.lock` y el paquete `quantlab` en modo editable.

### Variables de entorno (`.env`)

Edita `.env` (no se versiona). Todas tienen valores por defecto seguros.

| Variable | Defecto | Descripción |
|----------|---------|-------------|
| `QUANTLAB_HOST` | `127.0.0.1` | Interfaz de escucha (no exponer sin auth) |
| `QUANTLAB_PORT` | `8050` | Puerto; si ocupado prueba siguiente |
| `QUANTLAB_DEBUG` | `0` | `1` = recarga auto + trazas Dash |
| `QUANTLAB_DERIV_ENDPOINTS` | ver abajo | Lista separada por comas, orden prioridad (pendiente de validar en T020) |
| `QUANTLAB_RATE_LIMIT_PER_MIN` | `150` | Token bucket propio (Deriv permite 220) |
| `QUANTLAB_MAX_TICKS` | `50000` | Max ticks por símbolo en RAM |
| `QUANTLAB_MAX_CANDLES_PER_TF` | `30000` | Max velas por TF en RAM |
| `QUANTLAB_RAM_WARNING_MB` | `380` | Inicia evicción LRU |
| `QUANTLAB_RAM_CRITICAL_MB` | `420` | Evicción agresiva + warning |
| `QUANTLAB_REFRESH_SECONDS` | `60` | Refresco métricas ligeras |
| `QUANTLAB_STALE_SECONDS` | `900` | Antigüedad para marcar datos desactualizados |
| `QUANTLAB_SEED` | `20261115` | Semilla bootstrap/simulaciones |
| `QUANTLAB_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

**Endpoints Deriv por defecto:**
```text
wss://api.derivws.com/trading/v1/options/ws/public,wss://ws.binaryws.com/websockets/v3?app_id=1089
```
El segundo es fallback solo si el primero falla a nivel de transporte (520, timeout, etc.).

---

## 3. Primera Ejecución

### 3.1 Modo Demo (Sin Red — Para Tests y Desarrollo)

```powershell
$env:QUANTLAB_DEMO="1"
.\scripts\run.ps1
```

- Usa fixtures determinísticas en `tests/fixtures/`
- No conecta a Deriv
- Health check: `http://127.0.0.1:8050/api/health`
- Snapshot demo: `http://127.0.0.1:8050/api/snapshot/BOOM1000`

### 3.2 Modo Real (Con Deriv)

```powershell
Remove-Item Env:QUANTLAB_DEMO -ErrorAction SilentlyContinue
.\scripts\run.ps1
```

**En el primer arranque:**
1. Resuelve `BOOM1000` y `CRASH500` contra `active_symbols`
2. Descarga velas para 6 temporalidades (paginado, rate limited 150/min)
3. Calcula snapshots iniciales (5min, 1h prioritarios)
4. Inicia WebSocket Deriv por instrumento (ticks en vivo)
5. Muestra URL local y progreso en consola

**Tiempos esperados (baseline i3-10100F):**
- Arranque (sin Deriv): **< 3 segundos**
- Snapshot inicial ambos índices: **< 3 minutos** (progreso visible por símbolo/TF)
- RAM operación normal: **< 400 MB**

### 3.3 Verificar Conexión Deriv (Spike Técnico)

```powershell
uv run python scripts/spike_deriv.py
```

Imprime:
- Endpoint activo + latencia handshake
- Símbolos activos encontrados
- 5 velas 5min + 5 ticks muestra
- Suscripción 30s con conteo ticks
- Errores clasificados

Si falla → revisa firewall/VPN, prueba fallback, no continúes hasta resolver.

---

## 4. API Endpoints (Fase 1)

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/health` | Health check + versión + instrumentos |
| GET | `/api/instruments` | Lista instrumentos registrados |
| GET | `/api/snapshot/{symbol}` | Snapshot completo |
| GET | `/api/snapshot/{symbol}?tf=5min` | Solo temporalidad 5min |
| GET | `/api/config/{symbol}` | Config efectiva + `config_hash` |
| POST | `/api/config/{symbol}` | Actualiza config (valida + recalcula + push WS) |
| POST | `/api/config/{symbol}/reset` | Restaura defaults + recalcula |
| GET | `/api/live/{symbol}/status` | Estado stream en vivo |
| GET | `/api/export/{symbol}?format=json` | Export JSON (BytesIO streaming) |
| GET | `/api/export/{symbol}?format=csv` | Export CSV (BytesIO streaming) |
| GET | `/api/export/{symbol}?format=zip` | Export ZIP (BytesIO streaming) |
| WS | `/api/ws/{symbol}` | WebSocket ticks/events/zones/config |

### Ejemplos de Uso

```powershell
# Health check
Invoke-RestMethod http://127.0.0.1:8050/api/health

# Snapshot Boom 1000 (5min only)
Invoke-RestMethod "http://127.0.0.1:8050/api/snapshot/BOOM1000?tf=5min"

# Config actual Crash 500
Invoke-RestMethod http://127.0.0.1:8050/api/config/CRASH500

# Cambiar quantile eventos vela (recalcula en background)
$body = @{ analyzer = @{ candle_event_quantile = 0.995 } } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8050/api/config/BOOM1000" -Body $body -ContentType "application/json"

# Restaurar defaults
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8050/api/config/BOOM1000/reset"

# Export JSON
Invoke-WebRequest "http://127.0.0.1:8050/api/export/BOOM1000?format=json" -OutFile boom_snapshot.json

# Estado live stream
Invoke-RestMethod http://127.0.0.1:8050/api/live/BOOM1000/status
```

### WebSocket Test (PowerShell 7+)

```powershell
# Requiere websocket-client: pip install websocket-client
python -c "
import asyncio, websockets, json
async def test():
    async with websockets.connect('ws://127.0.0.1:8050/api/ws/BOOM1000') as ws:
        await ws.send(json.dumps({'type': 'subscribe', 'channels': ['ticks', 'events']}))
        for _ in range(5):
            msg = await ws.recv()
            print(json.loads(msg))
asyncio.run(test())
"
```

---

## 5. Verificación por Historia de Usuario

### US1 — Arranque y Health Check
1. Ejecuta `.\scripts\run.ps1`
2. En < 3s: consola muestra `INFO: Uvicorn running on http://127.0.0.1:8050`
3. `GET /api/health` → `{status: "ok", version, uptime, instruments: [...]}`

### US2 — Configuración en Caliente
1. `GET /api/config/BOOM1000` → anota `config_hash`
2. `POST /api/config/BOOM1000` con `{"analyzer": {"candle_event_quantile": 0.995}}`
3. Respuesta: `{"config_hash": "sha256:nuevo...", "status": "updated", "recalculated_timeframes": ["5min", "1h", ...]}`
4. WebSocket push: `{"type": "config_updated", "config_hash": "...", "recalculated": [...]}`
5. `GET /api/config/BOOM1000` → nuevo `config_hash` coincide
6. `POST /api/config/BOOM1000/reset` → vuelve a hash original

### US3 — Cliente Deriv Persistente
1. `scripts/spike_deriv.py` → conecta, latencia < 100ms
2. `GET /api/live/BOOM1000/status` → `connected: true`, `latency_ms: <100`, `dropped_ticks: 0`
3. Desconecta red 30s → reconecta automático → `reconnect_count` incrementa
4. Rate limit: 150 req/min respetado (test carga 5 min)

### US4 — Buffers RAM + Cero Disco
1. `GET /api/live/BOOM1000/status` → `queue_depth`, `processed` incrementan
2. Verifica `data/cache/` **no existe** o está **vacío**
3. RAM monitor: consola log cada 30s `RAM: XXX MB` (debe ser < 400 MB)
4. Si `dropped_ticks > 0` → log warning visible

### US5 — Snapshot Inmutable + Atomic Swap
1. `GET /api/snapshot/BOOM1000` → `snapshot_version: N`
2. `POST /api/config/BOOM1000` (cambio real) → `recalculated_timeframes` no vacío
3. `GET /api/snapshot/BOOM1000` → `snapshot_version: N+1`, `config_hash` nuevo
4. Snapshot anterior inaccesible (referencia reemplazada atómicamente)

---

## 6. Pruebas y Calidad

```powershell
# Suite completa (unit + integration + contract)
uv run pytest

# Solo tests de paridad numérica (críticos)
uv run pytest tests/unit/test_*_parity.py -v

# Solo tests de rendimiento (mediana 5 runs)
uv run pytest tests/performance/ -v

# Cobertura
uv run pytest --cov=quantlab --cov-report=term-missing

# Lint y formato
uv run ruff check .
uv run ruff format --check .

# Validar contratos JSON
uv run python scripts/verify_contracts.py
```

### Criterios de Aceptación Fase 1

- [ ] 100% tests unitarios pasan (paridad 1e-10 vs `boom_crash_quant_analyzer.py`)
- [ ] Tests integración pasan (fixtures determinísticas, sin red)
- [ ] Tests contrato pasan (OpenAPI, JSON schema, WS protocol)
- [ ] Tests rendimiento pasan (mediana 5: startup < 3s, RAM < 400 MB, rate limit ≤ 150/min)
- [ ] `scripts/spike_deriv.py` conecta y recibe ticks en < 5s
- [ ] `data/cache/` no existe o vacío (CERO DISCO)
- [ ] `fit_evt_gpd` y `detect_volatility_regimes` implementadas y testeadas
- [ ] Duplicados v1 eliminados (4 métodos)
- [ ] `export_csv` eliminada, sustituida por `ExportService` en memoria

---

## 7. Estructura del Proyecto (Fase 1)

```text
PROYECTOCOLAP/
├── src/quantlab/
│   ├── config.py              # Pydantic configs + config_hash
│   ├── settings.py            # BaseSettings desde .env
│   ├── errors.py              # Taxonomía errores
│   ├── utils.py               # _safe_name, config_hash, downsample_lttb
│   ├── state.py               # InstrumentState (buffers deque + locks)
│   ├── domain/                # Instrument, Candle, Tick, EventCandidate, ReactionZone, Snapshot
│   ├── data/                  # DerivClient, RateLimiter, MarketDataService, RAMCache, Quality
│   ├── analytics/             # stats, evt, events, zones, volatility, survival, diagnostics, walkforward
│   ├── services/              # SnapshotStore, SnapshotService, FeatureRegistry, ExportService
│   ├── live/                  # StreamManager (WS Deriv → asyncio.Queue → consumer)
│   └── dashboard/             # FastAPI app, routes, schemas
├── tests/
│   ├── unit/                  # Paridad numérica + unitarios
│   ├── integration/           # Fixtures + mock WS
│   ├── contract/              # OpenAPI, JSON schema, WS protocol
│   ├── performance/           # RAM, startup, rate limit
│   └── fixtures/              # CSV/Parquet/JSON determinísticos (seed=42)
├── scripts/
│   ├── run.ps1                # Arranque único
│   ├── spike_deriv.py         # Test conexión Deriv
│   ├── warm_cache.py          # Precarga caché opcional
│   └── verify_contracts.py    # Valida schemas
├── specs/002-migration-foundation/
├── legacy/                    # boom_crash_quant_analyzer.py + notebook (referencia)
├── pyproject.toml
├── uv.lock
├── .env.example
└── .python-version            # 3.13
```

---

## 8. Comandos Frecuentes

| Tarea | Comando |
|-------|---------|
| Instalar/actualizar | `uv sync` |
| Ejecutar (modo real) | `.\scripts\run.ps1` |
| Ejecutar (modo demo) | `$env:QUANTLAB_DEMO="1"; .\scripts\run.ps1` |
| Ejecutar en otro puerto | `$env:QUANTLAB_PORT="8060"; .\scripts\run.ps1` |
| Verificar Deriv | `uv run python scripts/spike_deriv.py` |
| Precargar caché | `uv run python scripts/warm_cache.py --symbols BOOM1000 CRASH500 --timeframes 5min 1h` |
| Tests | `uv run pytest` |
| Tests paridad | `uv run pytest tests/unit/test_*_parity.py -v` |
| Lint | `uv run ruff check .` |
| Validar contratos | `uv run python scripts/verify_contracts.py` |
| Ver logs | Consola (structlog JSON) — no hay archivo de log |

---

## 9. Solución de Problemas

| Síntoma | Causa Probable | Solución |
|---------|----------------|----------|
| "No se pudo conectar con Deriv" | Endpoint bloqueado/520 | Ejecuta `spike_deriv.py`; prueba fallback; revisa firewall/VPN |
| "El símbolo no está activo" | Jurisdicción o símbolo renombrado | Revisa `active_symbols`; config override en `.env` |
| "Límite de Deriv alcanzado" | Ráfaga de peticiones | Espera; baja `QUANTLAB_RATE_LIMIT_PER_MIN`; usa modo demo |
| "Datos insuficientes" | Ventana corta / mercado cerrado | Cambia TF; revisa calidad; no fuerces resultado |
| RAM > 400 MB | Buffer creciendo / leak | Revisa logs `RAM_WARNING`; evicción LRU automática |
| La app no arranca | Puerto ocupado | Define `QUANTLAB_PORT`; `Get-NetTCPConnection -LocalPort 8050` |
| `uv` no se reconoce | No está en PATH | Reinicia terminal o reinstala uv |
| Python incorrecto | 3.14 u otra versión | `uv python install 3.13`; `uv sync` |
| Paridad numérica falla | Diferencia algoritmo | Revisa test `test_*_parity.py`; seed fija; tolerancia 1e-10 |

### Diagnóstico Rápido

```powershell
# ¿La app está escuchando?
Get-NetTCPConnection -LocalPort 8050 -State Listen

# ¿El endpoint Deriv responde?
Test-NetConnection api.derivws.com -Port 443

# ¿Qué versión de Python usa uv?
uv run python --version

# ¿RAM actual?
Get-Process -Name python | Select-Object WorkingSet64
```

---

## 10. Mantenimiento

- **Caché**: Solo RAM — se reconstruye desde Deriv al reiniciar. No hay `data/cache/`.
- **Snapshots**: Solo en memoria (`InstrumentState.snapshot`). Export manual bajo demanda.
- **Dependencias**: `uv lock --upgrade` → `uv sync` → ejecuta suite completa antes de commit.
- **Logs**: Solo consola (structlog JSON). No archivos. `QUANTLAB_LOG_LEVEL=DEBUG` si necesitas más detalle.
- **Backup**: Solo `pyproject.toml`, `uv.lock`, `src/`, `tests/`, `specs/`, `scripts/`, `.env.example`.

---

## 11. Qué NO Hace Esta Fase

- ❌ No tiene frontend/UI (Fase 4: Vanilla JS + Plotly.js)
- ❌ No ejecuta operaciones, no usa tokens, no accede a cuenta/balance
- ❌ No persiste nada en disco (ni ticks, ni velas, ni snapshots, ni logs)
- ❌ No garantiza spikes oficiales: muestra **candidatos estadísticos**
- ❌ No incluye páginas Metodología/Sistema (Fase 5)

---

## 12. Siguiente Paso

Cuando Fase 1 esté verificada:

1. Ejecuta `/speckit.analyze` para detectar gaps vs spec padre `001-crash-boom-dashboard`
2. Ejecuta `/speckit.converge` hasta "Converged"
3. Inicia **Fase 2**: Gráficos Plotly backend (`charts/` generando `fig.to_json()`)
4. **Fase 3**: Downsampling LTTB + cache figuras por `config_hash`
5. **Fase 4**: Frontend Vanilla JS + Plotly.js local (servido por FastAPI)
6. **Fase 5**: Páginas Metodología/Sistema + Export UI
7. **Fase 6**: Convergencia final + Demo