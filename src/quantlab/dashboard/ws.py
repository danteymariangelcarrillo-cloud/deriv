"""Hub de conexiones WebSocket y push de mensajes (T033).

Cada conexión tiene una cola acotada (10 000 mensajes). Si el cliente no
consume y la cola se llena, se cierra con código 1001 (backpressure), tal como
exige ``contracts/dashboard-routes.md`` §3.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import WebSocket

from quantlab.dashboard.schemas import (
    CHANNELS,
    ChartDataMessage,
    ConfigUpdatedData,
    ConfigUpdatedMessage,
    ConnectionStateData,
    ConnectionStateMessage,
    EventData,
    EventMessage,
    TickData,
    TickMessage,
    WSServerMessage,
    ZoneUpdateData,
    ZoneUpdateMessage,
)
from quantlab.domain.snapshot import InstrumentSnapshot

__all__ = ["MAX_QUEUE", "ConnectionHub", "WSClient"]

log = structlog.get_logger(__name__)

MAX_QUEUE = 10_000
OVERFLOW_CLOSE_CODE = 1001


@dataclass(slots=True, eq=False)
class WSClient:
    """Conexión suscrita a los canales de un símbolo."""

    websocket: WebSocket
    symbol: str
    timeframe: str = "5min"
    channels: set[str] = field(default_factory=lambda: set(CHANNELS))
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=MAX_QUEUE)
    )
    closed: bool = False


class ConnectionHub:
    """Registro de conexiones y publicación de mensajes por símbolo/canal."""

    def __init__(self) -> None:
        self._clients: set[WSClient] = set()

    @property
    def size(self) -> int:
        return len(self._clients)

    def register(self, websocket: WebSocket, symbol: str) -> WSClient:
        client = WSClient(websocket=websocket, symbol=symbol)
        self._clients.add(client)
        return client

    def unregister(self, client: WSClient) -> None:
        client.closed = True
        self._clients.discard(client)

    async def close_all(self) -> None:
        clients = tuple(self._clients)
        self._clients.clear()
        for client in clients:
            client.closed = True
            with contextlib.suppress(RuntimeError):
                await client.websocket.close()

    def publish(self, symbol: str, channel: str, message: WSServerMessage) -> int:
        """Encola ``message`` en las conexiones del símbolo suscritas al canal.

        Devuelve cuántas conexiones lo recibieron. Si una cola está llena, la
        conexión se cierra con 1001 y se elimina del hub.
        """
        payload = message.model_dump(mode="json")
        delivered = 0
        for client in tuple(self._clients):
            if client.symbol != symbol or client.closed or channel not in client.channels:
                continue
            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                self._overflow(client)
                continue
            delivered += 1
        return delivered

    def _overflow(self, client: WSClient) -> None:
        self.unregister(client)
        log.warning("ws_backpressure_close", symbol=client.symbol)
        asyncio.create_task(self._close(client.websocket, OVERFLOW_CLOSE_CODE))

    @staticmethod
    async def _close(websocket: WebSocket, code: int) -> None:
        with contextlib.suppress(RuntimeError, asyncio.CancelledError):
            await websocket.close(code=code)

    async def send_loop(self, client: WSClient) -> None:
        """Drena la cola de una conexión hasta que se cierre."""
        while not client.closed:
            payload = await client.queue.get()
            try:
                await client.websocket.send_json(payload)
            except (RuntimeError, asyncio.CancelledError):
                break
            except Exception:
                log.exception("ws_send_failed", symbol=client.symbol)
                break
        self.unregister(client)


# ---------------------------- constructores de push ----------------------------


def tick_message(epoch: int, price: float, seq: int, *, is_event: bool = False) -> TickMessage:
    return TickMessage(data=TickData(epoch=epoch, price=price, seq=seq, is_event=is_event))


def event_message(epoch: int, price: float, move_pct: float, kind: str = "tick") -> EventMessage:
    return EventMessage(
        data=EventData(
            epoch=epoch,
            price=price,
            magnitude_pct=max(0.0, abs(move_pct)),
            kind="tick" if kind == "tick" else "candle",
        )
    )


def connection_message(
    *, connected: bool, latency_ms: float | None, endpoint: str | None
) -> ConnectionStateMessage:
    return ConnectionStateMessage(
        data=ConnectionStateData(connected=connected, latency_ms=latency_ms, endpoint=endpoint)
    )


def config_message(config_hash: str, recalculated: list[str]) -> ConfigUpdatedMessage:
    return ConfigUpdatedMessage(
        data=ConfigUpdatedData(config_hash=config_hash, recalculated=recalculated)
    )


def chart_message(chart_id: str, figure_json: dict[str, Any]) -> ChartDataMessage:
    return ChartDataMessage(data={"chart_id": chart_id, "figure_json": figure_json})


def zones_message(snapshot: InstrumentSnapshot, timeframe: str) -> ZoneUpdateMessage:
    analysis = snapshot.timeframes.get(timeframe)
    zones = analysis.zones if analysis is not None else ()
    return ZoneUpdateMessage(
        data=[
            ZoneUpdateData(
                zone_id=zone.zone_id,
                center=zone.center,
                zone_type=zone.zone_type,
                reaction_rate_pct=(
                    None if zone.reaction_rate is None else round(zone.reaction_rate * 100.0, 4)
                ),
            )
            for zone in zones
        ]
    )
