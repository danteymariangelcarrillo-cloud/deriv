"""Rutas REST y WebSocket del dashboard (T033).

Leen y escriben sólo en RAM. El push de ticks/eventos/zonas se hace por el
``ConnectionHub``; aquí se construyen los payloads y se validan los mensajes del
cliente con los esquemas de T031.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from quantlab.dashboard.schemas import (
    CHANNELS,
    ChartRequest,
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    ConnectionStateData,
    ConnectionStateMessage,
    ErrorData,
    ErrorMessage,
    ExportFormat,
    HealthResponse,
    InstrumentInfo,
    PingMessage,
    PongData,
    PongMessage,
    SetTimeframeMessage,
    StreamState,
    SubscribeMessage,
    UnsubscribeMessage,
    WSServerMessage,
    ZoneUpdateMessage,
    parse_client_message,
)
from quantlab.dashboard.ws import WSClient, chart_message, tick_message, zones_message
from quantlab.domain.market_data import DEFAULT_INSTRUMENTS
from quantlab.domain.snapshot import InstrumentSnapshot, TimeframeAnalysis

if TYPE_CHECKING:  # evita el ciclo app <-> routes en tiempo de importación
    from quantlab.dashboard.app import AppServices

__all__ = ["api_router", "ws_router"]

log = structlog.get_logger(__name__)

api_router = APIRouter(tags=["api"])
ws_router = APIRouter(tags=["ws"])

_TICK_POLL_SECONDS = 0.05
_WS_TIMEFRAMES = {"1min", "5min", "15min", "1h", "4h", "1d"}


def _services(request: Request) -> AppServices:
    return request.app.state.services


def _state(services: AppServices, symbol: str) -> Any:
    state = services.states.get(symbol)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Instrument {symbol} not found")
    return state


def _snapshot(services: AppServices, symbol: str) -> InstrumentSnapshot:
    _state(services, symbol)
    snapshot = services.store.get(symbol)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Snapshot for {symbol} not found")
    return snapshot


def _instrument_info(services: AppServices, symbol: str, instrument: Any) -> InstrumentInfo:
    snapshot = services.store.get(symbol)
    connection = services.client.connection_state
    return InstrumentInfo(
        symbol=symbol,
        display_name=instrument.display_name,
        direction=instrument.direction,
        enabled=instrument.enabled,
        snapshot_version=services.store.get_version(symbol),
        overall_status=(snapshot.overall_status if snapshot is not None else "error"),
        connected=connection.connected,
        latency_ms=connection.latency_ms_p50,
    )


# ----------------------------------- REST -----------------------------------


@api_router.get("/health", response_model=HealthResponse)
def health_check(request: Request) -> HealthResponse:
    services = _services(request)
    instruments = [
        _instrument_info(services, symbol, instrument)
        for symbol, instrument in DEFAULT_INSTRUMENTS.items()
    ]
    status = "ok" if all(item.enabled for item in instruments) else "degraded"
    return HealthResponse(
        status=status,
        version=request.app.version,
        uptime_seconds=services.uptime_seconds,
        instruments=instruments,
    )


@api_router.get("/instruments", response_model=list[InstrumentInfo])
def list_instruments(request: Request) -> list[InstrumentInfo]:
    services = _services(request)
    return [
        _instrument_info(services, symbol, instrument)
        for symbol, instrument in DEFAULT_INSTRUMENTS.items()
    ]


@api_router.get("/snapshot/{symbol}", response_model=InstrumentSnapshot | TimeframeAnalysis)
def get_snapshot(
    request: Request,
    symbol: str,
    tf: str | None = Query(default=None),
) -> InstrumentSnapshot | TimeframeAnalysis:
    services = _services(request)
    snapshot = _snapshot(services, symbol)
    if tf is None:
        return snapshot
    if tf not in _WS_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"Timeframe {tf} not valid")
    analysis = snapshot.timeframes.get(tf)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Snapshot for {symbol} has no timeframe {tf}")
    return analysis


@api_router.get("/config/{symbol}", response_model=ConfigResponse)
def get_config(request: Request, symbol: str) -> ConfigResponse:
    services = _services(request)
    _state(services, symbol)
    return ConfigResponse.from_config(services.config_for(symbol))


@api_router.post("/config/{symbol}", response_model=ConfigUpdateResponse)
async def update_config(
    request: Request, symbol: str, payload: ConfigUpdateRequest
) -> ConfigUpdateResponse:
    services = _services(request)
    _state(services, symbol)
    response = await services.service.update_config(symbol, payload)
    if response.recalculated_timeframes:
        asyncio.create_task(
            _publish_zones_later(services, symbol, response.recalculated_timeframes)
        )
    return response


@api_router.post("/config/{symbol}/reset", response_model=ConfigUpdateResponse)
async def reset_config(request: Request, symbol: str) -> ConfigUpdateResponse:
    services = _services(request)
    _state(services, symbol)
    response = await services.service.reset_config(symbol)
    if response.recalculated_timeframes:
        asyncio.create_task(
            _publish_zones_later(services, symbol, response.recalculated_timeframes)
        )
    return response


@api_router.get("/live/{symbol}/status", response_model=StreamState)
async def get_stream_status(request: Request, symbol: str) -> StreamState:
    services = _services(request)
    _state(services, symbol)
    status = await services.stream.get_status(symbol)
    return StreamState(**asdict(status))


@api_router.get("/export/{symbol}")
def export_snapshot(
    request: Request,
    symbol: str,
    format: str = Query(default="json"),
) -> StreamingResponse:
    services = _services(request)
    _snapshot(services, symbol)
    try:
        selected = ExportFormat(format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Export format {format} not valid") from exc
    payload = services.export.export(symbol, selected.value)
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=services.export.get_media_type(selected.value),
        headers={
            "content-disposition": (
                f'attachment; filename="{services.export.get_filename(symbol, selected.value)}"'
            )
        },
    )


# --------------------------------- helpers ---------------------------------


async def _publish_zones_later(services: AppServices, symbol: str, timeframes: list[str]) -> None:
    """Espera al recálculo en background y publica las zonas nuevas."""
    with contextlib.suppress(Exception):
        await services.service.wait_for_background()
        snapshot = services.store.get(symbol)
        if snapshot is None:
            return
        for timeframe in timeframes:
            services.hub.publish(symbol, "zones", zones_message(snapshot, timeframe))


def _enqueue(client: WSClient, message: WSServerMessage) -> bool:
    if client.closed:
        return False
    try:
        client.queue.put_nowait(message.model_dump(mode="json"))
    except asyncio.QueueFull:
        client.closed = True
        asyncio.create_task(client.websocket.close(code=1001))
        return False
    return True


def _error(code: str, message: str) -> ErrorMessage:
    return ErrorMessage(data=ErrorData(code=code, message=message))


def _figure(snapshot: InstrumentSnapshot, timeframe: str, chart_id: str) -> dict[str, Any]:
    """Figura JSON mínima con zonas y eventos del snapshot (sin Plotly)."""
    analysis = snapshot.timeframes.get(timeframe)
    traces: list[dict[str, Any]] = []
    if analysis is not None:
        if chart_id in {"candles_zones", "zones"}:
            traces.append(
                {
                    "type": "scatter",
                    "mode": "markers",
                    "name": "zonas",
                    "x": [zone.center for zone in analysis.zones],
                    "y": [100.0 * (zone.reaction_rate or 0.0) for zone in analysis.zones],
                    "text": [zone.zone_type for zone in analysis.zones],
                }
            )
        if chart_id in {"candles_zones", "events"}:
            events = analysis.events.candle.events
            traces.append(
                {
                    "type": "scatter",
                    "mode": "markers",
                    "name": "eventos",
                    "x": [event.event_epoch for event in events],
                    "y": [event.event_price for event in events],
                }
            )
    return {
        "data": traces,
        "layout": {
            "title": f"{snapshot.instrument.symbol} {timeframe} {chart_id}",
            "xaxis": {"title": "precio / epoch"},
            "yaxis": {"title": "tasa de reacción %"},
            "template": "plotly_white",
        },
    }


async def _tick_pump(services: AppServices, client: WSClient) -> None:
    """Empuja ticks nuevos y cambios de conexión del stream al cliente."""
    state = services.states[client.symbol]
    last_sequence = state.live_ticks[-1].sequence if state.live_ticks else 0
    last_connected = state.connected
    while not client.closed:
        if state.live_ticks and state.live_ticks[-1].sequence > last_sequence:
            new_ticks = [tick for tick in state.live_ticks if tick.sequence > last_sequence]
            last_sequence = new_ticks[-1].sequence
            if "ticks" in client.channels:
                for tick in new_ticks:
                    _enqueue(
                        client,
                        tick_message(tick.epoch, tick.price, tick.sequence),
                    )
        if state.connected != last_connected:
            last_connected = state.connected
            connection = services.client.connection_state
            _enqueue(
                client,
                ConnectionStateMessage(
                    data=ConnectionStateData(
                        connected=last_connected,
                        latency_ms=connection.latency_ms_p50,
                        endpoint=state.active_endpoint or None,
                    )
                ),
            )
        await asyncio.sleep(_TICK_POLL_SECONDS)


# ---------------------------------- WebSocket ----------------------------------


@ws_router.websocket("/api/ws/{symbol}")
async def websocket_endpoint(websocket: WebSocket, symbol: str) -> None:
    services: AppServices = websocket.app.state.services  # type: ignore[name-defined]
    state = services.states.get(symbol)
    if state is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    client = services.hub.register(websocket, symbol)
    sender = asyncio.create_task(services.hub.send_loop(client))
    pump = asyncio.create_task(_tick_pump(services, client))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                _enqueue(client, _error("INVALID_JSON", "El mensaje no es JSON válido"))
                continue
            try:
                message = parse_client_message(payload)
            except ValidationError as exc:
                _enqueue(client, _error("INVALID_MESSAGE", exc.errors()[0]["msg"]))
                continue

            if isinstance(message, PingMessage):
                _enqueue(client, PongMessage(data=PongData(ts=asyncio.get_running_loop().time())))
            elif isinstance(message, SubscribeMessage):
                client.channels.update(
                    channel for channel in message.channels if channel in CHANNELS
                )
            elif isinstance(message, UnsubscribeMessage):
                client.channels.difference_update(message.channels)
            elif isinstance(message, SetTimeframeMessage):
                client.timeframe = message.timeframe
            elif isinstance(message, ChartRequest):
                snapshot = services.store.get(symbol)
                if snapshot is None:
                    _enqueue(client, _error("NO_SNAPSHOT", f"No hay snapshot para {symbol}"))
                else:
                    _enqueue(
                        client,
                        chart_message(
                            message.chart_id, _figure(snapshot, client.timeframe, message.chart_id)
                        ),
                    )
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws_connection_failed", symbol=symbol)
    finally:
        client.closed = True
        for task in (pump, sender):
            task.cancel()
        await asyncio.gather(pump, sender, return_exceptions=True)
        services.hub.unregister(client)


def zones_push(services: AppServices, symbol: str, timeframe: str) -> ZoneUpdateMessage | None:
    """Construye el push de zonas si existe snapshot (usado por tests)."""
    snapshot = services.store.get(symbol)
    if snapshot is None:
        return None
    return zones_message(snapshot, timeframe)
