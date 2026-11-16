# Dashboard Routes — Migration Foundation

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16

Definición de rutas API REST y WebSocket para la Fase 1 (Backend only). El frontend (Fase 4) consumirá estos endpoints.

---

## 1. REST API Routes

| Método | Ruta | Handler | Request | Response | Descripción |
|--------|------|---------|---------|----------|-------------|
| GET | `/api/health` | `health_check` | — | `HealthResponse` | Health check + versión + instrumentos |
| GET | `/api/instruments` | `list_instruments` | — | `list[InstrumentInfo]` | Lista instrumentos registrados |
| GET | `/api/snapshot/{symbol}` | `get_snapshot` | `tf?: str` | `InstrumentSnapshot` \| `TimeframeAnalysis` | Snapshot completo o filtrado por TF |
| GET | `/api/config/{symbol}` | `get_config` | — | `ConfigResponse` | Config efectiva + `config_hash` |
| POST | `/api/config/{symbol}` | `update_config` | `ConfigUpdateRequest` | `ConfigUpdateResponse` | Actualiza config (valida + recalcula + push WS) |
| POST | `/api/config/{symbol}/reset` | `reset_config` | — | `ConfigUpdateResponse` | Restaura defaults + recalcula |
| GET | `/api/live/{symbol}/status` | `get_stream_status` | — | `StreamState` | Estado stream en vivo |
| GET | `/api/export/{symbol}` | `export_snapshot` | `format?: "json"\|"csv"\|"zip"` | `StreamingResponse` | Export en memoria (BytesIO) |

### Parámetros de Query

| Ruta | Parámetro | Tipo | Default | Descripción |
|------|-----------|------|---------|-------------|
| `/api/snapshot/{symbol}` | `tf` | `str` | `None` | Filtrar por temporalidad: `1min`, `5min`, `15min`, `1h`, `4h`, `1d` |
| `/api/export/{symbol}` | `format` | `str` | `json` | Formato export: `json`, `csv`, `zip` |

---

## 2. Response Models (Pydantic)

### HealthResponse
```python
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    version: str
    uptime_seconds: float
    instruments: list[InstrumentInfo]
```

### InstrumentInfo
```python
class InstrumentInfo(BaseModel):
    symbol: str
    display_name: str
    direction: Literal["up", "down"]
    enabled: bool
    snapshot_version: int
    overall_status: Literal["ready", "partial", "stale", "error"]
    connected: bool
    latency_ms: float | None
```

### ConfigResponse
```python
class ConfigResponse(BaseModel):
    analyzer: AnalyzerConfig
    model: ModelConfig
    gates: GateConfig
    ram: RAMConfig
    config_hash: str
    version: str
```

### ConfigUpdateRequest (Partial Update)
```python
class ConfigUpdateRequest(BaseModel):
    analyzer: AnalyzerConfig | None = None
    model: ModelConfig | None = None
    gates: GateConfig | None = None
    ram: RAMConfig | None = None
```

### ConfigUpdateResponse
```python
class ConfigUpdateResponse(BaseModel):
    config_hash: str
    status: Literal["updated", "unchanged", "error"]
    recalculated_timeframes: list[str]
    message: str | None
```

### StreamState
```python
class StreamState(BaseModel):
    symbol: str
    running: bool
    connected: bool
    received: int
    processed: int
    dropped_ticks: int
    queue_depth: int
    last_tick_epoch: int | None
    warmup_complete: bool
    live_events: int
    latency_ms: float | None
    active_endpoint: str | None
```

---

## 3. WebSocket Route

### Endpoint
```
WS /api/ws/{symbol}
```

### Parámetros de Ruta
| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `symbol` | `str` | `BOOM1000` o `CRASH500` |

### Handshake
- Sin autenticación (single-user local)
- Headers: `Upgrade: websocket`, `Connection: Upgrade`
- Protocolo: `ws` (localhost) — en producción detrás de TLS → `wss`

### Mensajes Cliente → Servidor

```json
{ "type": "subscribe", "channels": ["ticks", "events", "zones", "config"] }
{ "type": "unsubscribe", "channels": ["ticks"] }
{ "type": "set_timeframe", "timeframe": "5min" }
{ "type": "get_chart", "chart_id": "candles_zones", "max_bars": 500 }
{ "type": "ping" }
```

#### Tipos de Suscripción
| Canal | Descripción | Frecuencia |
|-------|-------------|------------|
| `ticks` | Cada tick recibido (con `is_event` flag) | ~10-100 Hz |
| `events` | Solo cuando se detecta candidato extremo | Event-driven |
| `zones` | Actualización de zonas tras recálculo | Event-driven |
| `config` | Notificación de cambio de config | Event-driven |

### Mensajes Servidor → Cliente

```json
{ "type": "tick", "data": {"epoch": 1700000000, "price": 1234.56, "is_event": false, "seq": 1}, "timestamp": 1700000000.123 }
{ "type": "event", "data": {"epoch": 1700000000, "price": 1234.56, "magnitude_pct": 0.12, "kind": "tick"}, "timestamp": 1700000000.123 }
{ "type": "zone_update", "data": [{"zone_id": "uuid...", "center": 1234.5, "reaction_rate_pct": 65.2, ...}], "timestamp": 1700000000.123 }
{ "type": "config_updated", "data": {"config_hash": "sha256:...", "recalculated": ["5min", "1h"]}, "timestamp": 1700000000.123 }
{ "type": "connection_state", "data": {"connected": true, "latency_ms": 42, "endpoint": "wss://..."}, "timestamp": 1700000000.123 }
{ "type": "chart_data", "data": {"chart_id": "candles_zones", "figure_json": {...}}, "timestamp": 1700000000.123 }
{ "type": "pong", "data": {"ts": 1700000000.123}, "timestamp": 1700000000.123 }
{ "type": "error", "data": {"code": "INSUFFICIENT_DATA", "message": "Datos insuficientes para EVT"}, "timestamp": 1700000000.123 }
```

### Lógica de Push Automático

| Evento Trigger | Mensaje Push | Condición |
|----------------|--------------|-----------|
| Tick recibido | `tick` | Siempre (si suscrito a `ticks`) |
| Evento detectado | `event` | Siempre (si suscrito a `events`) |
| Zonas recalculadas | `zone_update` | Tras `POST /api/config` o recompute programado |
| Config cambiada | `config_updated` | Tras `POST /api/config` exitoso |
| Reconexión WS | `connection_state` | `connected: true/false` + latencia |
| Cliente pide chart | `chart_data` | Tras `get_chart` request |

### Heartbeat
- Servidor envía `ping` cada 20s (configurable)
- Cliente debe responder `pong` en < 10s
- Si no hay `pong` → servidor cierra conexión

### Backpressure
- Si cliente no consume → queue interna crece
- Si queue > 10.000 mensajes → servidor cierra conexión con código 1001
- Cliente debe reconectar y re-suscribirse

---

## 4. Códigos de Error HTTP

| Código | Cuándo | Response Body |
|--------|--------|---------------|
| 200 | Éxito | Modelo correspondiente |
| 304 | `POST /api/config` sin cambios semánticos | `ConfigUpdateResponse` con `status: "unchanged"` |
| 400 | Validación Pydantic falla | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` |
| 404 | Símbolo no registrado / snapshot no existe | `{"detail": "Instrument CRASH500 not found"}` |
| 409 | Recalculo en progreso (lock) | `{"detail": "Recompute in progress, try again"}` |
| 422 | Config inválida (rangos, dependencias) | `{"detail": "candle_event_quantile must be < 1.0"}` |
| 500 | Error interno no controlado | `{"detail": "Internal server error"}` |
| 503 | Deriv no disponible / stream caído | `{"detail": "Deriv connection unavailable"}` |

---

## 5. Rate Limiting (Propio)

- **API REST**: Sin rate limiting propio (single-user local)
- **Deriv Client**: Token bucket 150/min aplicado en `DerivClient.request()`
- **WebSocket**: Sin rate limiting (push server-driven)

---

## 6. CORS (Development)

```python
# dashboard/app.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8050", "http://localhost:8050"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
```

En producción (Fase 6): restringir a origen real, habilitar TLS.

---

## 7. OpenAPI / Swagger UI

- Disponible en `/docs` (Swagger UI) y `/redoc` (ReDoc)
- Solo en `QUANTLAB_DEBUG=1` (por seguridad)
- Generado automáticamente desde modelos Pydantic

---

## 8. Versioning

- API version: `v1` (implícito en rutas `/api/...`)
- Cambios breaking → nueva versión `v2` en ruta `/api/v2/...`
- Fase 1: solo `v1`