"""Validación de los esquemas REST/WS (T031)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantlab.config import QuantLabConfig
from quantlab.dashboard.schemas import (
    CHANNELS,
    ConfigResponse,
    ConfigUpdateRequest,
    ErrorMessage,
    ExportFormat,
    HealthResponse,
    InstrumentInfo,
    PongMessage,
    StreamState,
    SubscribeMessage,
    TickMessage,
    parse_client_message,
    parse_server_message,
)


def test_health_response_roundtrip() -> None:
    response = HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_seconds=1.5,
        instruments=[InstrumentInfo(symbol="BOOM1000", display_name="Boom 1000", direction="up")],
    )

    assert response.model_dump()["instruments"][0]["symbol"] == "BOOM1000"
    assert set(response.instruments[0].model_dump()) >= {
        "symbol",
        "display_name",
        "direction",
        "enabled",
        "snapshot_version",
        "overall_status",
        "connected",
        "latency_ms",
    }


def test_health_response_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        HealthResponse(status="unknown", version="0.1.0", uptime_seconds=0.0)  # type: ignore[arg-type]


def test_config_response_from_config() -> None:
    config = QuantLabConfig()

    response = ConfigResponse.from_config(config)

    assert response.config_hash == config.hash()
    assert response.analyzer == config.analyzer
    assert response.version == "1.0.0"


def test_config_update_request_is_partial_and_strict() -> None:
    update = ConfigUpdateRequest.model_validate({"analyzer": {"candle_event_quantile": 0.95}})

    assert update.analyzer is not None
    assert update.analyzer.candle_event_quantile == 0.95
    assert update.model is None

    with pytest.raises(ValidationError):
        ConfigUpdateRequest.model_validate({"unknown": 1})
    with pytest.raises(ValidationError):
        ConfigUpdateRequest.model_validate({"analyzer": {"candle_event_quantile": 1.5}})


def test_stream_state_rejects_negative_counters() -> None:
    with pytest.raises(ValidationError):
        StreamState(
            symbol="BOOM1000",
            running=False,
            connected=False,
            received=-1,
            processed=0,
            dropped_ticks=0,
            queue_depth=0,
            warmup_complete=False,
            live_events=0,
        )


def test_export_format_enum() -> None:
    assert {fmt.value for fmt in ExportFormat} == {"json", "csv", "zip"}
    assert ExportFormat("zip") is ExportFormat.zip
    with pytest.raises(ValueError):
        ExportFormat("pdf")


def test_client_message_discriminator() -> None:
    message = parse_client_message({"type": "subscribe", "channels": ["ticks", "events"]})

    assert isinstance(message, SubscribeMessage)
    assert message.channels == ["ticks", "events"]
    assert set(message.channels) <= set(CHANNELS)

    with pytest.raises(ValidationError):
        parse_client_message({"type": "subscribe", "channels": []})
    with pytest.raises(ValidationError):
        parse_client_message({"type": "unknown"})
    with pytest.raises(ValidationError):
        parse_client_message({"type": "set_timeframe", "timeframe": "2min"})


def test_server_message_discriminator() -> None:
    tick = parse_server_message(
        {"type": "tick", "data": {"epoch": 1, "price": 100.0, "seq": 1}, "timestamp": 1.0}
    )
    error = parse_server_message(
        {"type": "error", "data": {"code": "X", "message": "boom"}, "timestamp": 1.0}
    )

    assert isinstance(tick, TickMessage)
    assert isinstance(error, ErrorMessage)
    with pytest.raises(ValidationError):
        parse_server_message({"type": "tick", "data": {"epoch": -1, "price": 0.0}})


def test_pong_and_extra_fields_are_rejected() -> None:
    pong = PongMessage.model_validate({"type": "pong", "data": {"ts": 1.0}, "timestamp": 1.0})
    assert pong.type == "pong"

    with pytest.raises(ValidationError):
        PongMessage.model_validate(
            {"type": "pong", "data": {"ts": 1.0}, "timestamp": 1.0, "extra": True}
        )
