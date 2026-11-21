"""Tests de la allowlist de llamadas a Deriv (T006)."""

from __future__ import annotations

import pytest

from quantlab.data.deriv_calls import (
    ALLOWED_REQUESTS,
    ALLOWED_RESPONSE_TYPES,
    build_request,
    validate_response,
)


def test_allowlist_has_exactly_the_six_templates() -> None:
    assert set(ALLOWED_REQUESTS) == {
        "active_symbols",
        "ticks_history_candles",
        "ticks_history_ticks",
        "subscribe_ticks",
        "ping",
        "time",
    }


def test_templates_match_contract_payloads() -> None:
    assert ALLOWED_REQUESTS["active_symbols"] == {"active_symbols": "brief"}
    assert ALLOWED_REQUESTS["ticks_history_candles"] == {
        "ticks_history": "{symbol}",
        "style": "candles",
        "granularity": "{int}",
        "count": "{int}",
        "start": "{int}",
        "end": "{int}",
    }
    assert ALLOWED_REQUESTS["ticks_history_ticks"] == {
        "ticks_history": "{symbol}",
        "style": "ticks",
        "count": "{int}",
        "end": "latest",
    }
    assert ALLOWED_REQUESTS["subscribe_ticks"] == {"ticks": "{symbol}", "subscribe": 1}
    assert ALLOWED_REQUESTS["ping"] == {"ping": 1}
    assert ALLOWED_REQUESTS["time"] == {"time": 1}


def test_build_active_symbols_ignores_no_params() -> None:
    assert build_request("active_symbols") == {"active_symbols": "brief"}


def test_build_candles_interpolates_symbol_and_ints() -> None:
    payload = build_request(
        "ticks_history_candles",
        ticks_history="CRASH500",
        granularity=300,
        count=5000,
        start=1_700_000_000,
        end=1_700_100_000,
    )

    assert payload == {
        "ticks_history": "CRASH500",
        "style": "candles",
        "granularity": 300,
        "count": 5000,
        "start": 1_700_000_000,
        "end": 1_700_100_000,
    }
    assert isinstance(payload["count"], int)
    assert isinstance(payload["granularity"], int)


def test_build_ticks_history_uses_latest_end() -> None:
    payload = build_request("ticks_history_ticks", ticks_history="BOOM1000", count=5000)

    assert payload == {
        "ticks_history": "BOOM1000",
        "style": "ticks",
        "count": 5000,
        "end": "latest",
    }


def test_build_subscribe_ticks_sets_subscribe_flag() -> None:
    assert build_request("subscribe_ticks", ticks="CRASH500") == {
        "ticks": "CRASH500",
        "subscribe": 1,
    }


def test_build_ping_and_time() -> None:
    assert build_request("ping") == {"ping": 1}
    assert build_request("time") == {"time": 1}


def test_build_rejects_unknown_template() -> None:
    with pytest.raises(ValueError, match="plantilla no permitida"):
        build_request("proposal")


def test_build_rejects_missing_parameter() -> None:
    with pytest.raises(ValueError, match="falta el parámetro 'ticks'"):
        build_request("subscribe_ticks")

    with pytest.raises(ValueError, match="falta el parámetro 'start'"):
        build_request(
            "ticks_history_candles",
            ticks_history="CRASH500",
            granularity=300,
            count=10,
            end=1_700_000_000,
        )


def test_build_rejects_extra_parameters() -> None:
    # Impide colar campos fuera de la plantilla (p. ej. suscripción en un
    # histórico).
    with pytest.raises(ValueError, match="parámetros no permitidos"):
        build_request("ticks_history_ticks", ticks_history="CRASH500", count=10, subscribe=1)


def test_build_rejects_wrong_types() -> None:
    with pytest.raises(TypeError, match="debe ser int"):
        build_request("ticks_history_ticks", ticks_history="CRASH500", count="5000")

    with pytest.raises(TypeError, match="debe ser int"):
        build_request("ticks_history_ticks", ticks_history="CRASH500", count=True)

    with pytest.raises(TypeError, match="debe ser int"):
        build_request("ticks_history_ticks", ticks_history="CRASH500", count=5000.0)

    with pytest.raises(TypeError, match="debe ser str"):
        build_request("ticks_history_ticks", ticks_history=500, count=5000)


def test_build_rejects_blank_symbol() -> None:
    with pytest.raises(ValueError, match="no puede estar vacío"):
        build_request("subscribe_ticks", ticks="   ")


def test_build_strips_symbol_whitespace() -> None:
    assert build_request("subscribe_ticks", ticks="  BOOM1000 ") == {
        "ticks": "BOOM1000",
        "subscribe": 1,
    }


def test_build_returns_independent_payload() -> None:
    first = build_request("active_symbols")
    first["active_symbols"] = "full"

    assert build_request("active_symbols") == {"active_symbols": "brief"}
    assert ALLOWED_REQUESTS["active_symbols"] == {"active_symbols": "brief"}


def test_build_never_emits_allowlist_keys_outside_contract() -> None:
    payload = build_request("subscribe_ticks", ticks="CRASH500")

    assert set(payload) == {"ticks", "subscribe"}
    assert "msg_type" not in payload


@pytest.mark.parametrize("msg_type", sorted(ALLOWED_RESPONSE_TYPES))
def test_validate_response_accepts_allowlisted_types(msg_type: str) -> None:
    assert validate_response({"msg_type": msg_type}) is True


def test_validate_response_rejects_everything_else() -> None:
    assert validate_response({"msg_type": "error"}) is False
    assert validate_response({"msg_type": "proposal"}) is False
    assert validate_response({}) is False
    assert validate_response({"msg_type": None}) is False
    assert validate_response([]) is False  # type: ignore[arg-type]
