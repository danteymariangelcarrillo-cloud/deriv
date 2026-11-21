"""Tests del cliente Deriv asíncrono (T007) con WebSocket simulado."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from quantlab.data import deriv_client
from quantlab.data.deriv_client import DerivClient
from quantlab.errors import (
    DerivAPIError,
    DerivRateLimitError,
    DerivTimeoutError,
    DerivTransportError,
    SymbolNotActiveError,
)

_SENTINEL = object()

Symbols = list[dict[str, Any]]


class FakeWS:
    """WebSocket mínimo: registra envíos y permite empujar mensajes."""

    def __init__(self, url: str, responder: Any = None) -> None:
        self.url = url
        self.responder = responder
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        if self.responder is not None:
            self.responder(payload, self)

    async def close(self) -> None:
        self.closed = True
        self.incoming.put_nowait(_SENTINEL)

    def push(self, message: dict[str, Any]) -> None:
        self.incoming.put_nowait(message)

    def __aiter__(self) -> FakeWS:
        return self

    async def __anext__(self) -> str:
        item = await self.incoming.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return json.dumps(item)


class FakeServer:
    """Conector falso: una conexión por endpoint y errores programables."""

    def __init__(self) -> None:
        self.responder: Any = None
        self.connections: dict[str, FakeWS] = {}
        self.connect_calls: list[str] = []
        self.failures: dict[str, Exception] = {}

    async def connect(self, url: str, **_: Any) -> FakeWS:
        self.connect_calls.append(url)
        if url in self.failures:
            raise self.failures[url]
        ws = FakeWS(url, self.responder)
        self.connections[url] = ws
        return ws

    def sent_payloads(self) -> list[dict[str, Any]]:
        return [payload for ws in self.connections.values() for payload in ws.sent]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    fake = FakeServer()
    monkeypatch.setattr(deriv_client, "_WS_CONNECT", fake.connect)
    return fake


async def _wait_for(predicate: Any, *, attempts: int = 200) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condición no alcanzada a tiempo")


def _echo(field: str, msg_type: str) -> Any:
    def responder(payload: dict[str, Any], ws: FakeWS) -> None:
        ws.push({"msg_type": msg_type, field: "ok", "req_id": payload["req_id"]})

    return responder


async def test_request_round_trip_and_latency(server: FakeServer) -> None:
    server.responder = _echo("ping", "ping")
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)

    response = await client.request({"ping": 1}, ("ping",), "ping")

    assert response == {"msg_type": "ping", "ping": "ok", "req_id": "1"}
    assert server.connect_calls == ["wss://a"]
    state = client.connection_state
    assert state.connected is True
    assert state.active_endpoint == "wss://a"
    assert state.latency_ms_p50 is not None
    assert state.last_message_at is not None
    await client.close()


async def test_request_rejects_payloads_outside_allowlist(server: FakeServer) -> None:
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)

    with pytest.raises(DerivAPIError, match="allowlist|ALLOWED_REQUESTS"):
        await client.request({"ticks": "CRASH500"}, ("ticks",), "ticks")

    with pytest.raises(DerivAPIError):
        await client.request({"ping": 2}, ("ping",), "ping")

    with pytest.raises(DerivAPIError):
        await client.request(
            {
                "ticks_history": "CRASH500",
                "style": "ticks",
                "count": 5,
                "end": "latest",
                "subscribe": 1,
            },
            ("ticks",),
            "ticks",
        )

    assert server.connect_calls == []
    await client.close()


async def test_business_error_does_not_switch_endpoint(server: FakeServer) -> None:
    def responder(payload: dict[str, Any], ws: FakeWS) -> None:
        ws.push(
            {
                "error": {"code": "InvalidSymbol", "message": "símbolo inválido"},
                "req_id": payload["req_id"],
            }
        )

    server.responder = responder
    client = DerivClient(endpoints=["wss://a", "wss://b"], timeout=1.0)

    with pytest.raises(DerivAPIError, match="InvalidSymbol"):
        await client.request({"ping": 1}, ("ping",), "ping")

    assert server.connect_calls == ["wss://a"]
    await client.close()


async def test_deriv_rate_limit_error_is_classified(server: FakeServer) -> None:
    def responder(payload: dict[str, Any], ws: FakeWS) -> None:
        ws.push({"error": {"code": "RateLimit", "message": "lento"}, "req_id": payload["req_id"]})

    server.responder = responder
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)

    with pytest.raises(DerivRateLimitError):
        await client.request({"ping": 1}, ("ping",), "ping")
    await client.close()


async def test_fallback_on_transport_failure(server: FakeServer) -> None:
    server.failures["wss://a"] = OSError("handshake status 520")
    server.responder = _echo("time", "time")
    client = DerivClient(endpoints=["wss://a", "wss://b"], timeout=1.0, retries_per_endpoint=0)

    response = await client.request({"time": 1}, ("time",), "time")

    assert response["time"] == "ok"
    assert server.connect_calls == ["wss://a", "wss://b"]
    assert client.active_endpoint == "wss://b"
    assert client.connection_state.last_error is not None
    await client.close()


async def test_timeout_is_transport_error(server: FakeServer) -> None:
    client = DerivClient(endpoints=["wss://a"], timeout=0.05, retries_per_endpoint=0)

    with pytest.raises(DerivTransportError) as info:
        await client.request({"ping": 1}, ("ping",), "ping")

    assert any("intento 1" in line for line in info.value.context["failures"])
    await client.close()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (OSError("handshake status 520"), True),
        (OSError("HTTP 503"), True),
        (OSError("connection reset by peer"), True),
        (TimeoutError("timed out"), True),
        (DerivTimeoutError("sin respuesta"), True),
        (DerivAPIError("InvalidSymbol"), False),
        (ValueError("otra cosa"), False),
    ],
)
def test_is_transport_failure(exc: BaseException, expected: bool) -> None:
    assert DerivClient._is_transport_failure(exc) is expected


async def test_active_symbols_cache_ttl(server: FakeServer) -> None:
    symbols = [
        {"underlying_symbol": "CRASH500", "underlying_symbol_name": "Crash 500"},
        {"underlying_symbol": "BOOM1000", "underlying_symbol_name": "Boom 1000"},
    ]

    def responder(payload: dict[str, Any], ws: FakeWS) -> None:
        ws.push(
            {
                "msg_type": "active_symbols",
                "active_symbols": symbols,
                "req_id": payload["req_id"],
            }
        )

    server.responder = responder
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)

    assert await client.get_active_symbols() == symbols
    assert await client.get_active_symbols() == symbols
    assert len(server.sent_payloads()) == 1

    assert client._active_symbols_at is not None
    client._active_symbols_at -= 3601
    assert await client.get_active_symbols() == symbols
    assert len(server.sent_payloads()) == 2
    await client.close()


async def test_resolve_symbol_matches_legacy_logic(server: FakeServer) -> None:
    symbols = [
        {"underlying_symbol": "CRASH500", "underlying_symbol_name": "Crash 500"},
        {"underlying_symbol": "BOOM1000", "underlying_symbol_name": "Boom 1000"},
    ]

    def responder(payload: dict[str, Any], ws: FakeWS) -> None:
        ws.push(
            {
                "msg_type": "active_symbols",
                "active_symbols": symbols,
                "req_id": payload["req_id"],
            }
        )

    server.responder = responder
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)

    assert await client.resolve_symbol("crash500") == ("CRASH500", "Crash 500")
    assert await client.resolve_symbol("Boom") == ("BOOM1000", "Boom 1000")
    assert await client.resolve_symbol("Boom 1000", override="BOOM1000") == (
        "BOOM1000",
        "Boom 1000",
    )

    with pytest.raises(SymbolNotActiveError, match="no aparece activo"):
        await client.resolve_symbol("Boom 1000", override="NOPE")

    with pytest.raises(SymbolNotActiveError, match="No se encontró"):
        await client.resolve_symbol("Gold")
    await client.close()


async def test_rate_limiter_counts_requests(server: FakeServer) -> None:
    server.responder = _echo("ping", "ping")
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)

    await client.request({"ping": 1}, ("ping",), "ping")
    await client.request({"ping": 1}, ("ping",), "ping")

    assert client._rate_limiter.state.calls_last_minute == 2
    await client.close()


async def test_subscribe_stream_and_reconnect(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def responder(payload: dict[str, Any], ws: FakeWS) -> None:
        if "ticks" in payload:
            sub_id = f"S{len(server.connect_calls)}"
            ws.push(
                {
                    "msg_type": "ticks",
                    "tick": {"symbol": payload["ticks"], "quote": 0},
                    "subscription": {"id": sub_id},
                    "req_id": payload["req_id"],
                }
            )

    server.responder = responder
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)
    monkeypatch.setattr(client, "_backoff_seconds", lambda attempt: 0.0)

    stream = client.subscribe_ticks("CRASH500")
    first_read = asyncio.create_task(anext(stream))
    await _wait_for(lambda: bool(client.connection_state.subscriptions))

    ws = server.connections["wss://a"]
    ws.push(
        {
            "msg_type": "ticks",
            "tick": {"symbol": "CRASH500", "quote": 100.5},
            "subscription": {"id": "S1"},
        }
    )
    assert (await asyncio.wait_for(first_read, 1))["tick"]["quote"] == 100.5

    await ws.close()
    assert await asyncio.wait_for(anext(stream), 1) is None
    assert client.connection_state.connected is False

    second_read = asyncio.create_task(anext(stream))
    await _wait_for(lambda: bool(client.connection_state.subscriptions))
    assert server.connect_calls == ["wss://a", "wss://a"]
    assert client.connection_state.reconnect_count == 1

    ws2 = server.connections["wss://a"]
    ws2.push(
        {
            "msg_type": "ticks",
            "tick": {"symbol": "CRASH500", "quote": 101.0},
            "subscription": {"id": "S2"},
        }
    )
    assert (await asyncio.wait_for(second_read, 1))["tick"]["quote"] == 101.0

    await stream.aclose()
    await client.close()


async def test_close_is_idempotent_and_updates_state(server: FakeServer) -> None:
    server.responder = _echo("ping", "ping")
    client = DerivClient(endpoints=["wss://a"], timeout=1.0)
    await client.request({"ping": 1}, ("ping",), "ping")

    await client.close()
    await client.close()

    state = client.connection_state
    assert state.connected is False
    assert client.active_endpoint == "wss://a"

    with pytest.raises(DerivTransportError, match="cerr"):
        await client.request({"ping": 1}, ("ping",), "ping")
    assert server.connect_calls == ["wss://a"]
