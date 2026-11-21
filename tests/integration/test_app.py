"""Integración de la app FastAPI: lifespan, REST y WebSocket (T032/T033)."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quantlab.dashboard.app import build_services, create_app
from quantlab.data.quality import DataQualityReport
from quantlab.settings import Settings

BARS = 800


class FakeMarketData:
    """Velas deterministas en memoria; no hay red ni disco."""

    def __init__(self, bars: int = BARS) -> None:
        self.bars = bars

    async def get_candles(
        self, symbol: str, timeframe: str, start_epoch: int, end_epoch: int
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        rng = np.random.default_rng(31)
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, self.bars)))
        open_ = np.concatenate([[1000.0], close[:-1]])
        wick = np.abs(rng.normal(0.0, 0.002, self.bars))
        step = max(1, (end_epoch - start_epoch) // self.bars)
        epochs = [end_epoch - (self.bars - index) * step for index in range(self.bars)]
        frame = pd.DataFrame(
            {
                "epoch": epochs,
                "open": open_,
                "high": np.maximum(open_, close) * (1.0 + wick),
                "low": np.minimum(open_, close) * (1.0 - wick),
                "close": close,
            }
        )
        report = DataQualityReport(
            status="ok",
            expected_bars=self.bars,
            received_bars=self.bars,
            coverage_pct=100.0,
            missing_bars=0,
            gaps_count=0,
            duplicates_removed=0,
            invalid_ohlc=0,
            first_epoch=epochs[0],
            last_epoch=epochs[-1],
        )
        return frame, report


@pytest.fixture(scope="module")
def services():
    settings = Settings()
    runtime = build_services(settings, market=FakeMarketData())  # type: ignore[arg-type]
    asyncio.run(runtime.service.build_snapshot("BOOM1000", timeframes=["5min"]))
    return runtime


@pytest.fixture(scope="module")
def client(services):
    with TestClient(create_app(services, settings=services.settings)) as test_client:
        yield test_client


def test_lifespan_exposes_services_and_health(client) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["uptime_seconds"] >= 0.0
    assert {item["symbol"] for item in body["instruments"]} == {"BOOM1000", "CRASH500"}
    assert response.headers["x-request-id"]
    assert float(response.headers["x-latency-ms"]) >= 0.0


def test_health_responds_quickly(client) -> None:
    response = client.get("/api/health")

    assert float(response.headers["x-latency-ms"]) < 50.0


def test_instruments_and_snapshot(client) -> None:
    instruments = client.get("/api/instruments")
    snapshot = client.get("/api/snapshot/BOOM1000")
    filtered = client.get("/api/snapshot/BOOM1000", params={"tf": "5min"})

    assert instruments.status_code == 200
    assert len(instruments.json()) == 2
    assert snapshot.status_code == 200
    assert snapshot.json()["timeframes"]["5min"]["bars"] == BARS
    assert filtered.status_code == 200
    assert filtered.json()["timeframe"] == "5min"


def test_snapshot_errors(client) -> None:
    unknown = client.get("/api/snapshot/UNKNOWN")
    no_snapshot = client.get("/api/snapshot/CRASH500")
    bad_tf = client.get("/api/snapshot/BOOM1000", params={"tf": "2min"})
    missing_tf = client.get("/api/snapshot/BOOM1000", params={"tf": "1h"})

    assert unknown.status_code == 404
    assert no_snapshot.status_code == 404
    assert bad_tf.status_code == 422
    assert missing_tf.status_code == 404


def test_config_get_and_partial_update(client) -> None:
    before = client.get("/api/config/BOOM1000")
    updated = client.post(
        "/api/config/BOOM1000",
        json={"ram": {"max_ticks_per_symbol": 60000}},
    )
    after = client.get("/api/config/BOOM1000")

    assert before.status_code == 200
    assert before.json()["config_hash"] != after.json()["config_hash"]
    assert updated.status_code == 200
    assert updated.json()["status"] == "updated"
    assert updated.json()["recalculated_timeframes"] == []
    assert after.json()["ram"]["max_ticks_per_symbol"] == 60000


def test_config_validation_and_unknown_symbol(client) -> None:
    invalid = client.post("/api/config/BOOM1000", json={"analyzer": {"candle_event_quantile": 2.0}})
    unknown = client.post("/api/config/UNKNOWN", json={"ram": {}})

    assert invalid.status_code == 422
    assert unknown.status_code == 404


def test_live_status_shape(client) -> None:
    response = client.get("/api/live/BOOM1000/status")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BOOM1000"
    assert body["running"] is False
    assert body["queue_depth"] == 0
    assert body["warmup_complete"] is False


def test_export_json_csv_and_zip(client) -> None:
    json_response = client.get("/api/export/BOOM1000", params={"format": "json"})
    csv_response = client.get("/api/export/BOOM1000", params={"format": "csv"})
    zip_response = client.get("/api/export/BOOM1000", params={"format": "zip"})
    invalid = client.get("/api/export/BOOM1000", params={"format": "pdf"})

    assert json_response.status_code == 200
    assert json.loads(json_response.content)["instrument"]["symbol"] == "BOOM1000"
    assert "attachment" in json_response.headers["content-disposition"]

    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert b";" in csv_response.content

    assert zip_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        names = archive.namelist()
        assert "snapshot.json" in names
        assert "5min/features.csv" in names
        assert "5min/zones.csv" in names

    assert invalid.status_code == 422


def test_export_without_snapshot_is_404(client) -> None:
    assert client.get("/api/export/CRASH500").status_code == 404


def test_websocket_ping_pong_and_chart(client) -> None:
    with client.websocket_connect("/api/ws/BOOM1000") as websocket:
        websocket.send_json({"type": "ping"})
        pong = websocket.receive_json()
        assert pong["type"] == "pong"
        assert pong["data"]["ts"] >= 0.0

        websocket.send_json({"type": "subscribe", "channels": ["ticks", "events"]})
        websocket.send_json({"type": "get_chart", "chart_id": "candles_zones", "max_bars": 100})
        chart = websocket.receive_json()
        while chart["type"] not in {"chart_data", "error"}:
            chart = websocket.receive_json()
        assert chart["type"] == "chart_data"
        assert chart["data"]["chart_id"] == "candles_zones"
        assert "figure_json" in chart["data"]


def test_websocket_rejects_invalid_messages(client) -> None:
    with client.websocket_connect("/api/ws/BOOM1000") as websocket:
        websocket.send_text("{not json")
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["data"]["code"] == "INVALID_JSON"

        websocket.send_json({"type": "unknown"})
        error = websocket.receive_json()
        assert error["data"]["code"] == "INVALID_MESSAGE"


def test_websocket_rejects_unknown_symbol(client) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/ws/UNKNOWN"):
        pass
