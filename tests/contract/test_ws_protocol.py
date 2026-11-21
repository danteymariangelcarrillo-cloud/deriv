"""Contrato del protocolo WebSocket (T039)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantlab.dashboard.schemas import (
    ChartRequest,
    PingMessage,
    SetTimeframeMessage,
    SubscribeMessage,
    UnsubscribeMessage,
    parse_client_message,
    parse_server_message,
)

CLIENT_MESSAGES = [
    ({"type": "subscribe", "channels": ["ticks", "events"]}, SubscribeMessage),
    ({"type": "unsubscribe", "channels": ["ticks"]}, UnsubscribeMessage),
    ({"type": "set_timeframe", "timeframe": "1h"}, SetTimeframeMessage),
    ({"type": "get_chart", "chart_id": "candles_zones", "max_bars": 500}, ChartRequest),
    ({"type": "ping"}, PingMessage),
]

SERVER_MESSAGES = [
    {"type": "tick", "data": {"epoch": 1, "price": 100.0, "is_event": False, "seq": 1}},
    {"type": "event", "data": {"epoch": 1, "price": 100.0, "magnitude_pct": 0.2, "kind": "tick"}},
    {
        "type": "zone_update",
        "data": [
            {
                "zone_id": "zone-1",
                "center": 100.0,
                "zone_type": "support",
                "reaction_rate_pct": 62.5,
            }
        ],
    },
    {"type": "config_updated", "data": {"config_hash": "sha256:x", "recalculated": ["5min"]}},
    {
        "type": "connection_state",
        "data": {"connected": True, "latency_ms": 42.0, "endpoint": "wss://x"},
    },
    {"type": "chart_data", "data": {"chart_id": "zones", "figure_json": {"data": []}}},
    {"type": "pong", "data": {"ts": 1.0}},
    {"type": "error", "data": {"code": "INSUFFICIENT_DATA", "message": "faltan datos"}},
]


@pytest.mark.parametrize(("payload", "expected"), CLIENT_MESSAGES)
def test_all_client_messages_are_valid(payload, expected) -> None:
    message = parse_client_message(payload)
    assert isinstance(message, expected)
    assert message.type == payload["type"]


@pytest.mark.parametrize("payload", SERVER_MESSAGES)
def test_all_server_messages_are_valid(payload) -> None:
    message = parse_server_message({**payload, "timestamp": 1700000000.123})
    assert message.type == payload["type"]
    assert message.timestamp == 1700000000.123


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "subscribe"},
        {"type": "subscribe", "channels": ["ticks", "unknown"]},
        {"type": "set_timeframe", "timeframe": "2min"},
        {"type": "get_chart", "chart_id": "", "max_bars": 0},
        {"type": "ping", "extra": True},
        {"type": "does_not_exist"},
    ],
)
def test_invalid_client_messages_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "tick", "data": {"epoch": 1, "price": -1.0, "seq": 1}},
        {
            "type": "event",
            "data": {"epoch": 1, "price": 1.0, "magnitude_pct": -0.1, "kind": "tick"},
        },
        {"type": "zone_update", "data": [{"zone_id": "x", "center": 1.0, "zone_type": "diagonal"}]},
        {"type": "config_updated", "data": {}},
        {"type": "connection_state", "data": {}},
        {"type": "chart_data", "data": {"chart_id": "x"}},
        {"type": "pong", "data": {}},
        {"type": "error", "data": {"code": "X"}},
        {"type": "unknown"},
    ],
)
def test_invalid_server_messages_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        parse_server_message({**payload, "timestamp": 1.0})


def test_discriminator_requires_type_field() -> None:
    with pytest.raises(ValidationError):
        parse_client_message({"channels": ["ticks"]})
