"""Tests de los modelos de snapshot (T013) y su correspondencia con el schema."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from quantlab.data.quality import DataQualityReport
from quantlab.domain.events import EventCandidate, EventSeries, EventSummary
from quantlab.domain.market_data import DEFAULT_INSTRUMENTS
from quantlab.domain.snapshot import (
    AnalysisConfig,
    DataRange,
    FeatureStatus,
    InstrumentSnapshot,
    SnapshotConfig,
    TimeframeAnalysis,
    aggregate_overall_status,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "002-migration-foundation"
    / "contracts"
    / "snapshot.schema.json"
)
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

CONFIG_HASH = "sha256:" + "a" * 64
DATA_VERSION = "sha256:" + "b" * 64


def _assert_matches_def(payload: dict[str, Any], def_name: str) -> None:
    definition = SCHEMA["$defs"][def_name]
    dumped = set(payload)
    assert set(definition["required"]) <= dumped, f"{def_name}: faltan campos obligatorios"
    assert dumped <= set(definition["properties"]), f"{def_name}: campos fuera del schema"


def _quality() -> DataQualityReport:
    return DataQualityReport(
        status="ok",
        expected_bars=0,
        received_bars=0,
        coverage_pct=0.0,
        missing_bars=0,
        gaps_count=0,
        duplicates_removed=0,
        invalid_ohlc=0,
    )


def _events() -> EventSummary:
    empty = {
        "status": "insufficient",
        "n_events": 0,
        "threshold_quantile": 0.99,
        "lookback": 200,
        "warmup": 50,
    }
    return EventSummary(
        candle=EventSeries(**empty, note="sin velas"),
        tick=EventSeries(**{**empty, "threshold_quantile": 0.999}, note="sin ticks"),
    )


def _feature(**overrides: Any) -> FeatureStatus:
    payload: dict[str, Any] = {
        "id": "evt.gpd",
        "name_es": "Ajuste EVT/GPD",
        "category": "evt",
        "status": "ready",
        "min_sample": 30,
        "sample_size": 120,
        "ui_path": "/i/BOOM1000/distribution",
    }
    payload.update(overrides)
    return FeatureStatus(**payload)


def _analysis(**overrides: Any) -> TimeframeAnalysis:
    payload: dict[str, Any] = {
        "timeframe": "5min",
        "status": "ready",
        "generated_at": datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        "data_range": DataRange(
            requested_start_epoch=1_699_000_000,
            requested_end_epoch=1_700_000_000,
            start_epoch=1_699_000_060,
            end_epoch=1_699_999_980,
        ),
        "bars": 25920,
        "candles_ref": "BOOM1000:5min",
        "quality": _quality(),
        "events": _events(),
        "features": {"evt.gpd": _feature()},
    }
    payload.update(overrides)
    return TimeframeAnalysis(**payload)


def _snapshot(**overrides: Any) -> InstrumentSnapshot:
    payload: dict[str, Any] = {
        "generated_at": datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        "config_hash": CONFIG_HASH,
        "data_version": DATA_VERSION,
        "instrument": DEFAULT_INSTRUMENTS["BOOM1000"],
        "source": "deriv",
        "timeframes": {"5min": _analysis()},
    }
    payload.update(overrides)
    return InstrumentSnapshot(**payload)


# --------------------------- schema contract keys ---------------------------


def test_snapshot_payload_matches_schema_required_fields() -> None:
    dumped = _snapshot().model_dump(mode="json")

    assert set(SCHEMA["required"]) <= set(dumped)
    assert set(dumped) <= set(SCHEMA["properties"])
    assert dumped["schema_version"] == "1.0.0"
    assert set(dumped["config"]) <= set(SCHEMA["$defs"]["Config"]["properties"])
    assert set(dumped["config"]) == set(SCHEMA["$defs"]["Config"]["required"])


def test_timeframe_analysis_matches_schema() -> None:
    dumped = _analysis().model_dump(mode="json")

    _assert_matches_def(dumped, "TimeframeAnalysis")
    assert set(dumped) == set(SCHEMA["$defs"]["TimeframeAnalysis"]["required"])


def test_nested_defs_match_schema() -> None:
    _assert_matches_def(
        EventCandidate(
            symbol="BOOM1000",
            timeframe="5min",
            kind="candle",
            direction="up",
            event_epoch=1_700_000_000,
            event_price=100.0,
            magnitude_points=0.5,
            magnitude_pct=0.5,
            threshold_points=0.4,
            lookback=200,
            warmup_ok=True,
        ).model_dump(mode="json"),
        "EventCandidate",
    )
    _assert_matches_def(_feature().model_dump(mode="json"), "FeatureStatus")
    _assert_matches_def(DEFAULT_INSTRUMENTS["BOOM1000"].model_dump(mode="json"), "Instrument")

    from quantlab.domain.zones import ReactionZone

    zone = ReactionZone(
        symbol="BOOM1000",
        timeframe="5min",
        zone_type="support",
        center=100.0,
        lower=99.5,
        upper=100.5,
        half_width=0.5,
        touch_bars=1,
        episodes=0,
        evaluated_touches=0,
        reactions=0,
        failures=0,
        ambiguous_touches=0,
        pending_touches=0,
        event_count_in_zone=0,
        available_from_epoch=1_699_000_000,
        stage="in_sample",
        status="insufficient",
        reason="sin toques evaluados",
    )
    _assert_matches_def(zone.model_dump(mode="json"), "ReactionZone")


def test_config_defaults_are_serialisable() -> None:
    dumped = SnapshotConfig().model_dump(mode="json")

    assert dumped["version"] == "1.0.0"
    assert set(dumped["analysis"]) == set(SCHEMA["$defs"]["AnalysisConfig"]["required"])
    assert set(dumped["runtime"]) == set(SCHEMA["$defs"]["RuntimeConfig"]["required"])
    assert set(dumped["gates"]) == set(SCHEMA["$defs"]["GateConfig"]["required"])
    assert AnalysisConfig().fdr_q == 0.05


# ------------------------------- immutability -------------------------------


def test_snapshot_is_frozen_and_collections_are_tuples() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError):
        snapshot.config_hash = DATA_VERSION  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.notes = ("cambiado",)  # type: ignore[misc]
    assert isinstance(snapshot.features, tuple)
    assert isinstance(snapshot.timeframes["5min"].features["evt.gpd"], FeatureStatus)
    assert isinstance(snapshot.timeframes["5min"].quality, DataQualityReport)


def test_snapshot_rejects_extra_fields_and_bad_hashes() -> None:
    with pytest.raises(ValidationError):
        _snapshot(extra="no permitido")
    with pytest.raises(ValidationError, match="config_hash"):
        _snapshot(config_hash="a" * 64)  # falta el prefijo sha256:
    with pytest.raises(ValidationError, match="data_version"):
        _snapshot(data_version="sha256:zzz")


def test_generated_at_serialises_with_z() -> None:
    dumped = _snapshot().model_dump(mode="json")

    assert dumped["generated_at"] == "2026-11-20T12:00:00Z"
    assert dumped["timeframes"]["5min"]["generated_at"] == "2026-11-20T12:00:00Z"


# ----------------------------- snapshot identity -----------------------------


def test_snapshot_id_is_deterministic_and_reacts_to_config_hash() -> None:
    first = _snapshot()
    second = _snapshot()
    other = _snapshot(config_hash="sha256:" + "c" * 64)

    assert first.snapshot_id == second.snapshot_id
    assert first.snapshot_id != other.snapshot_id
    assert str(uuid.UUID(first.snapshot_id)) == first.snapshot_id


def test_snapshot_accepts_canonical_explicit_id_and_rejects_others() -> None:
    good = _snapshot()
    explicit = _snapshot(snapshot_id=good.snapshot_id)
    assert explicit.snapshot_id == good.snapshot_id

    with pytest.raises(ValidationError, match="canónica"):
        _snapshot(snapshot_id="A" * 8 + "-0000-0000-0000-000000000000")
    with pytest.raises(ValidationError, match="UUID"):
        _snapshot(snapshot_id="no-es-uuid")


def test_timeframes_must_match_their_keys() -> None:
    with pytest.raises(ValidationError, match="no coincide"):
        _snapshot(timeframes={"15min": _analysis()})
    with pytest.raises(ValidationError, match="no válidas"):
        _snapshot(timeframes={"2min": _analysis(timeframe="5min")})


# ------------------------------ overall status ------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "error"),
        (["error", "error"], "error"),
        (["ready"], "ready"),
        (["ready", "ready"], "ready"),
        (["ready", "insufficient"], "partial"),
        (["ready", "pending"], "partial"),
        (["ready", "error"], "partial"),
        (["ready", "stale"], "stale"),
        (["stale", "insufficient"], "stale"),
    ],
)
def test_aggregate_overall_status(statuses: list[str], expected: str) -> None:
    assert aggregate_overall_status(statuses) == expected


def test_snapshot_computes_overall_status_from_timeframes() -> None:
    assert _snapshot().overall_status == "ready"

    partial = _snapshot(
        timeframes={
            "5min": _analysis(),
            "15min": _analysis(timeframe="15min", status="insufficient"),
        }
    )
    assert partial.overall_status == "partial"

    explicit = _snapshot(overall_status="partial")
    assert explicit.overall_status == "partial"


def test_feature_status_requires_reason_when_insufficient() -> None:
    with pytest.raises(ValidationError, match="razón"):
        _feature(status="insufficient")
    assert _feature(status="insufficient", reason="faltan excesos").reason
