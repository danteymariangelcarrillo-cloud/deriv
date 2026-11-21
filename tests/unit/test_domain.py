"""Tests de modelos de dominio: entrada, eventos y zonas (T011-T012)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from quantlab.domain.events import EventCandidate, EventSeries, compute_event_id
from quantlab.domain.market_data import (
    DEFAULT_INSTRUMENTS,
    Candle,
    Instrument,
    Tick,
    get_instrument,
)
from quantlab.domain.zones import DEFAULT_P_NULL, ReactionZone, ZoneSensitivity, compute_zone_id

SHA = "sha256:" + "a" * 64


def _candle(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "BOOM1000",
        "timeframe": "5min",
        "epoch": 1_700_000_000,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "source": "deriv",
    }
    payload.update(overrides)
    return payload


def _event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "BOOM1000",
        "timeframe": "5min",
        "kind": "candle",
        "direction": "up",
        "event_epoch": 1_700_000_000,
        "event_price": 123.45,
        "magnitude_points": 1.2,
        "magnitude_pct": 0.98,
        "threshold_points": 1.0,
        "lookback": 200,
        "warmup_ok": True,
    }
    payload.update(overrides)
    return payload


def _zone(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "BOOM1000",
        "timeframe": "5min",
        "zone_type": "support",
        "center": 100.0,
        "lower": 99.5,
        "upper": 100.5,
        "half_width": 0.5,
        "touch_bars": 12,
        "episodes": 10,
        "evaluated_touches": 6,
        "reactions": 4,
        "failures": 2,
        "ambiguous_touches": 1,
        "pending_touches": 3,
        "reaction_rate": 4 / 6,
        "ci_lower": 0.3,
        "ci_upper": 0.9,
        "event_count_in_zone": 0,
        "available_from_epoch": 1_699_999_000,
        "stage": "in_sample",
        "status": "ready",
    }
    payload.update(overrides)
    return payload


# ------------------------------- Instrument -------------------------------


def test_default_instruments_cover_six_timeframes() -> None:
    assert set(DEFAULT_INSTRUMENTS) == {"BOOM1000", "CRASH500"}
    for instrument in DEFAULT_INSTRUMENTS.values():
        assert set(instrument.history_days) == {"1min", "5min", "15min", "1h", "4h", "1d"}
        assert instrument.tick_history_count == 5000
        assert instrument.enabled is True

    assert get_instrument("BOOM1000").direction == "up"
    assert get_instrument("CRASH500").direction == "down"


def test_instrument_rejects_incoherent_direction_and_bad_history() -> None:
    with pytest.raises(ValidationError, match="dirección incoherente"):
        Instrument(symbol="BOOM1000", display_name="Boom", direction="down")
    with pytest.raises(ValidationError, match="history_days"):
        Instrument(
            symbol="BOOM1000",
            display_name="Boom",
            direction="up",
            history_days={"5min": 90},
        )
    with pytest.raises(ValidationError, match="history_days"):
        Instrument(
            symbol="BOOM1000",
            display_name="Boom",
            direction="up",
            history_days={
                "1min": 30,
                "5min": 0,
                "15min": 180,
                "1h": 365,
                "4h": 730,
                "1d": 1825,
            },
        )
    with pytest.raises(ValidationError):
        Instrument(symbol="boom1000", display_name="Boom", direction="up")


def test_unknown_instrument_lookup_lists_available() -> None:
    with pytest.raises(KeyError, match="BOOM1000"):
        get_instrument("VOL75")


# --------------------------------- Candle ---------------------------------


def test_candle_accepts_valid_ohlc_and_is_frozen() -> None:
    candle = Candle(**_candle())

    assert candle.high == 102.0
    assert candle.quality_flags == ()
    with pytest.raises(ValidationError):
        candle.close = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 100.5},  # high < close
        {"low": 101.5},  # low > close
        {"open": 0.0},
        {"close": -1.0},
        {"epoch": 0},
        {"timeframe": "2min"},
        {"source": "otro"},
        {"extra_field": True},
    ],
)
def test_candle_rejects_invalid_payloads(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Candle(**_candle(**overrides))


def test_candle_rejects_nan_and_inf() -> None:
    with pytest.raises(ValidationError):
        Candle(**_candle(high=float("nan")))
    with pytest.raises(ValidationError):
        Candle(**_candle(close=float("inf")))


# ---------------------------------- Tick ----------------------------------


def test_tick_normalises_received_at_to_utc() -> None:
    naive = Tick(
        symbol="BOOM1000", epoch=1, price=100.0, sequence=0, received_at=datetime(2026, 1, 1)
    )
    aware = Tick(
        symbol="BOOM1000",
        epoch=1,
        price=100.0,
        sequence=0,
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert naive.received_at == aware.received_at
    assert naive.received_at.tzinfo is not None
    assert naive.received_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "overrides",
    [{"price": 0.0}, {"epoch": 0}, {"sequence": -1}, {"price": float("inf")}],
)
def test_tick_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    payload = {"symbol": "BOOM1000", "epoch": 1, "price": 100.0, "sequence": 0, **overrides}
    with pytest.raises(ValidationError):
        Tick(**payload)


# ------------------------------ EventCandidate -----------------------------


def test_event_id_is_deterministic_and_derived_from_fields() -> None:
    first = EventCandidate(**_event())
    second = EventCandidate(**_event(event_price=123.45))
    other_price = EventCandidate(**_event(event_price=123.46))
    other_kind = EventCandidate(**_event(kind="tick"))

    assert first.event_id == second.event_id
    assert first.event_id != other_price.event_id
    assert first.event_id != other_kind.event_id
    assert first.event_id == compute_event_id("BOOM1000", "5min", "candle", 1_700_000_000, 123.45)
    assert first.label == "candidato de spike"
    assert first.threshold_basis == "rolling_quantile"


def test_event_id_rejects_non_uuid_and_non_canonical_forms() -> None:
    with pytest.raises(ValidationError, match="UUID"):
        EventCandidate(**_event(event_id="no-es-uuid"))
    canonical = compute_event_id("BOOM1000", "5min", "candle", 1_700_000_000, 123.45)
    # UUID en mayúsculas no es la forma canónica que emite uuid5.
    with pytest.raises(ValidationError, match="canónica"):
        EventCandidate(**_event(event_id=canonical.upper()))


def test_event_normalises_computed_at_and_rejects_invalid_label() -> None:
    event = EventCandidate(**_event(computed_at=datetime(2026, 1, 1)))
    assert event.computed_at.utcoffset().total_seconds() == 0

    with pytest.raises(ValidationError):
        EventCandidate(**_event(label="señal confirmada"))
    with pytest.raises(ValidationError):
        EventCandidate(**_event(threshold_basis="otra"))
    with pytest.raises(ValidationError):
        EventCandidate(**_event(magnitude_pct=-0.1))


def test_event_series_requires_counts_to_cover_serialised_events() -> None:
    series = EventSeries(
        status="ready",
        n_events=1,
        threshold_quantile=0.99,
        lookback=200,
        warmup=50,
        events=(EventCandidate(**_event()),),
    )
    assert series.n_events == 1

    with pytest.raises(ValidationError, match="n_events"):
        EventSeries(
            status="ready",
            n_events=0,
            threshold_quantile=0.99,
            lookback=200,
            warmup=50,
            events=(EventCandidate(**_event()),),
        )


# ------------------------------- ReactionZone ------------------------------


def test_zone_defaults_and_deterministic_id() -> None:
    zone = ReactionZone(**_zone())
    same = ReactionZone(**_zone())
    moved = ReactionZone(**_zone(center=100.25, lower=99.75, upper=100.75))

    assert zone.p_null == DEFAULT_P_NULL == 0.5
    assert zone.reject is False
    assert zone.zone_id == same.zone_id
    assert zone.zone_id != moved.zone_id
    assert zone.zone_id == compute_zone_id("BOOM1000", "5min", "support", 100.0, 1_699_999_000)
    assert isinstance(zone.sensitivity, ZoneSensitivity)
    assert zone.sensitivity.note


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"evaluated_touches": 7}, "reactions \\+ failures"),
        ({"pending_touches": 4}, "episodes"),
        ({"lower": 100.5, "upper": 101.5}, "lower <= center <= upper"),
        ({"upper": 100.4}, "2 \\* half_width"),
        ({"half_width": 0.0}, "greater than 0"),
    ],
)
def test_zone_invariants(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        ReactionZone(**_zone(**overrides))


def test_zone_without_evaluated_touches_requires_null_statistics() -> None:
    no_data = _zone(
        episodes=4,
        evaluated_touches=0,
        reactions=0,
        failures=0,
        ambiguous_touches=1,
        pending_touches=3,
        reaction_rate=None,
        ci_lower=None,
        ci_upper=None,
    )
    zone = ReactionZone(**no_data)
    assert zone.reaction_rate is None

    with pytest.raises(ValidationError, match="deben ser null"):
        ReactionZone(**{**no_data, "reaction_rate": 0.0})


def test_zone_sensitivity_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ZoneSensitivity(rate_ambiguous_as_failure_pct=120.0, rate_pending_as_failure_pct=10.0)
    with pytest.raises(ValidationError):
        ReactionZone(
            **_zone(
                sensitivity={
                    "rate_ambiguous_as_failure_pct": 10.0,
                    "rate_pending_as_failure_pct": 20.0,
                    "nota": "x",
                }
            )
        )
