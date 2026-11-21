"""Esquemas Pydantic de la API REST y del protocolo WebSocket (T031).

Los modelos de configuración y de snapshot se reutilizan tal cual: aquí sólo se
definen las envolturas de respuesta y los mensajes del WebSocket. Los mensajes
del servidor son una unión discriminada por ``type`` para que un cliente pueda
validarlos con ``TypeAdapter(WSServerMessage)``.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from quantlab.config import (
    AnalyzerConfig,
    GateConfig,
    ModelConfig,
    QuantLabConfig,
    RAMConfig,
)
from quantlab.domain.types import OverallStatus, Timeframe
from quantlab.services.snapshot_service import (  # reexportados para la API
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)

__all__ = [
    "CHANNELS",
    "EXPORT_FORMATS",
    "ChartData",
    "ChartRequest",
    "ConfigResponse",
    "ConfigUpdateRequest",
    "ConfigUpdateResponse",
    "ConnectionStateData",
    "ErrorData",
    "EventData",
    "ExportFormat",
    "HealthResponse",
    "InstrumentInfo",
    "PingMessage",
    "PongData",
    "SetTimeframeMessage",
    "StreamState",
    "SubscribeMessage",
    "TickData",
    "UnsubscribeMessage",
    "WSClientMessage",
    "WSServerMessage",
    "ZoneUpdateData",
    "parse_client_message",
    "parse_server_message",
]

CHANNELS: tuple[str, ...] = ("ticks", "events", "zones", "config")
Channel = Literal["ticks", "events", "zones", "config"]


class _Schema(BaseModel):
    """Base estricta de los esquemas de la API."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------- REST: salud -------------------------------


class InstrumentInfo(_Schema):
    """Instrumento registrado y su estado operativo."""

    symbol: str
    display_name: str
    direction: Literal["up", "down"]
    enabled: bool = True
    snapshot_version: int = Field(default=0, ge=0)
    overall_status: OverallStatus = "error"
    connected: bool = False
    latency_ms: float | None = None


class HealthResponse(_Schema):
    """Respuesta de ``GET /api/health``."""

    status: Literal["ok", "degraded", "error"]
    version: str
    uptime_seconds: float = Field(ge=0.0)
    instruments: list[InstrumentInfo] = Field(default_factory=list)


# ------------------------------ REST: config -------------------------------


class ConfigResponse(_Schema):
    """Configuración efectiva de un instrumento (``GET /api/config``)."""

    analyzer: AnalyzerConfig
    model: ModelConfig
    gates: GateConfig
    ram: RAMConfig
    config_hash: str
    version: str

    @classmethod
    def from_config(cls, config: QuantLabConfig) -> ConfigResponse:
        return cls(
            analyzer=config.analyzer,
            model=config.model,
            gates=config.gates,
            ram=config.ram,
            config_hash=config.hash(),
            version=config.version,
        )


class StreamState(_Schema):
    """Estado del stream en vivo (``GET /api/live/{symbol}/status``)."""

    symbol: str
    running: bool
    connected: bool
    received: int = Field(ge=0)
    processed: int = Field(ge=0)
    dropped_ticks: int = Field(ge=0)
    queue_depth: int = Field(ge=0)
    last_tick_epoch: int | None = None
    warmup_complete: bool
    live_events: int = Field(ge=0)
    latency_ms: float | None = None
    active_endpoint: str | None = None


class ExportFormat(StrEnum):
    """Formatos de exportación en memoria (T034)."""

    json = "json"
    csv = "csv"
    zip = "zip"


EXPORT_FORMATS: tuple[str, ...] = tuple(fmt.value for fmt in ExportFormat)


# ------------------------------ WebSocket: cliente ------------------------------


class SubscribeMessage(_Schema):
    type: Literal["subscribe"] = "subscribe"
    channels: list[Channel] = Field(min_length=1)


class UnsubscribeMessage(_Schema):
    type: Literal["unsubscribe"] = "unsubscribe"
    channels: list[Channel] = Field(min_length=1)


class SetTimeframeMessage(_Schema):
    type: Literal["set_timeframe"] = "set_timeframe"
    timeframe: Timeframe


class ChartRequest(_Schema):
    type: Literal["get_chart"] = "get_chart"
    chart_id: str = Field(min_length=1)
    max_bars: int = Field(default=500, ge=1, le=5000)


class PingMessage(_Schema):
    type: Literal["ping"] = "ping"


WSClientMessage = Annotated[
    SubscribeMessage | UnsubscribeMessage | SetTimeframeMessage | ChartRequest | PingMessage,
    Field(discriminator="type"),
]

_CLIENT_ADAPTER: TypeAdapter[WSClientMessage] = TypeAdapter(WSClientMessage)


def parse_client_message(payload: Any) -> WSClientMessage:
    """Valida un mensaje de cliente; lanza ``pydantic.ValidationError``."""
    return _CLIENT_ADAPTER.validate_python(payload)


# ------------------------------ WebSocket: servidor ------------------------------


class TickData(_Schema):
    epoch: int = Field(ge=0)
    price: float = Field(gt=0)
    is_event: bool = False
    seq: int = Field(ge=0)


class EventData(_Schema):
    epoch: int = Field(ge=0)
    price: float = Field(gt=0)
    magnitude_pct: float = Field(ge=0.0)
    kind: Literal["candle", "tick"]


class ZoneUpdateData(_Schema):
    """Resumen de una zona para el push de WebSocket."""

    zone_id: str
    center: float
    zone_type: Literal["support", "resistance"]
    reaction_rate_pct: float | None = None


class ConfigUpdatedData(_Schema):
    config_hash: str
    recalculated: list[str] = Field(default_factory=list)


class ConnectionStateData(_Schema):
    connected: bool
    latency_ms: float | None = None
    endpoint: str | None = None


class ChartData(_Schema):
    chart_id: str
    figure_json: dict[str, Any]


class PongData(_Schema):
    ts: float


class ErrorData(_Schema):
    code: str
    message: str


def _timestamp() -> float:
    return time.time()


class TickMessage(_Schema):
    type: Literal["tick"] = "tick"
    data: TickData
    timestamp: float = Field(default_factory=_timestamp)


class EventMessage(_Schema):
    type: Literal["event"] = "event"
    data: EventData
    timestamp: float = Field(default_factory=_timestamp)


class ZoneUpdateMessage(_Schema):
    type: Literal["zone_update"] = "zone_update"
    data: list[ZoneUpdateData]
    timestamp: float = Field(default_factory=_timestamp)


class ConfigUpdatedMessage(_Schema):
    type: Literal["config_updated"] = "config_updated"
    data: ConfigUpdatedData
    timestamp: float = Field(default_factory=_timestamp)


class ConnectionStateMessage(_Schema):
    type: Literal["connection_state"] = "connection_state"
    data: ConnectionStateData
    timestamp: float = Field(default_factory=_timestamp)


class ChartDataMessage(_Schema):
    type: Literal["chart_data"] = "chart_data"
    data: ChartData
    timestamp: float = Field(default_factory=_timestamp)


class PongMessage(_Schema):
    type: Literal["pong"] = "pong"
    data: PongData
    timestamp: float = Field(default_factory=_timestamp)


class ErrorMessage(_Schema):
    type: Literal["error"] = "error"
    data: ErrorData
    timestamp: float = Field(default_factory=_timestamp)


WSServerMessage = Annotated[
    TickMessage
    | EventMessage
    | ZoneUpdateMessage
    | ConfigUpdatedMessage
    | ConnectionStateMessage
    | ChartDataMessage
    | PongMessage
    | ErrorMessage,
    Field(discriminator="type"),
]

_SERVER_ADAPTER: TypeAdapter[WSServerMessage] = TypeAdapter(WSServerMessage)


def parse_server_message(payload: Any) -> WSServerMessage:
    """Valida un mensaje de servidor; lanza ``pydantic.ValidationError``."""
    return _SERVER_ADAPTER.validate_python(payload)
