"""Contratos OpenAPI y JSON del snapshot (T038)."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pandas as pd

from quantlab.dashboard.app import build_services, create_app
from quantlab.data.quality import DataQualityReport
from scripts.verify_contracts import FIXTURE_PATH, SCHEMA_PATH, load_json, validate

BARS = 400


class FakeMarket:
    def __init__(self) -> None:
        rng = np.random.default_rng(41)
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.003, BARS)))
        self.candles = pd.DataFrame(
            {
                "epoch": [1_700_000_000 + index * 300 for index in range(BARS)],
                "open": close,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
            }
        )

    async def get_candles(self, symbol, timeframe, start_epoch, end_epoch):
        report = DataQualityReport(
            status="ok",
            expected_bars=BARS,
            received_bars=BARS,
            coverage_pct=100.0,
            missing_bars=0,
            gaps_count=0,
            duplicates_removed=0,
            invalid_ohlc=0,
            first_epoch=int(self.candles["epoch"].iloc[0]),
            last_epoch=int(self.candles["epoch"].iloc[-1]),
        )
        return self.candles.copy(), report


def test_openapi_contains_documented_routes_and_schemas() -> None:
    app = create_app(build_services(market=FakeMarket()))  # type: ignore[arg-type]
    schema = app.openapi()

    assert set(schema["paths"]) == {
        "/api/health",
        "/api/instruments",
        "/api/snapshot/{symbol}",
        "/api/config/{symbol}",
        "/api/config/{symbol}/reset",
        "/api/live/{symbol}/status",
        "/api/export/{symbol}",
    }
    components = schema["components"]["schemas"]
    for name in ("HealthResponse", "InstrumentInfo", "ConfigResponse", "StreamState"):
        assert name in components
    assert "ConfigUpdateRequest" in components
    assert "ConfigUpdateResponse" in components


def test_openapi_request_response_models_are_compatible() -> None:
    app = create_app(build_services(market=FakeMarket()))  # type: ignore[arg-type]
    paths = app.openapi()["paths"]

    config_post = paths["/api/config/{symbol}"]["post"]
    assert config_post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "ConfigUpdateRequest"
    )
    assert config_post["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("ConfigUpdateResponse")
    export_params = {param["name"] for param in paths["/api/export/{symbol}"]["get"]["parameters"]}
    assert "format" in export_params


def test_fixture_and_generated_snapshot_validate_against_json_schema() -> None:
    schema = load_json(SCHEMA_PATH)
    fixture = load_json(FIXTURE_PATH)
    assert validate(fixture, schema) == []

    services = build_services(market=FakeMarket())  # type: ignore[arg-type]
    snapshot = asyncio.run(services.service.build_snapshot("BOOM1000", timeframes=["5min"]))
    payload = json.loads(snapshot.model_dump_json())

    assert validate(payload, schema) == []
