"""Tests de la taxonomía de errores (T002)."""

from __future__ import annotations

import json
from datetime import UTC

import pytest

from quantlab.errors import (
    CODE_META,
    AnalyticsError,
    CacheError,
    ConfigError,
    DerivAPIError,
    DerivRateLimitError,
    DerivTimeoutError,
    DerivTransportError,
    ErrorCode,
    ErrorRecord,
    ExportError,
    InsufficientDataError,
    QuantLabError,
    Severity,
    StreamError,
    SymbolNotActiveError,
    to_error_record,
)


def test_every_code_has_metadata() -> None:
    assert set(CODE_META) == set(ErrorCode)
    for meta in CODE_META.values():
        assert meta.source
        assert meta.message_es
        assert isinstance(meta.severity, Severity)


def test_error_record_serializes_to_json() -> None:
    error = DerivTransportError(detail="connection reset", context={"attempt": 2})
    record = error.to_error_record()
    payload = json.loads(record.model_dump_json())
    assert payload["code"] == "DERIV_TRANSPORT"
    assert payload["severity"] == "error"
    assert payload["retryable"] is True
    assert payload["source"] == "deriv"
    assert payload["detail"] == "connection reset"
    assert payload["context"] == {"attempt": 2}
    assert payload["occurred_at"].endswith("Z") or "+00:00" in payload["occurred_at"]


def test_occurred_at_is_utc_aware() -> None:
    assert (
        ErrorRecord(
            code=ErrorCode.COMPUTE_FAILED,
            severity=Severity.ERROR,
            message_es="x",
            retryable=False,
            source="analytics",
        ).occurred_at.tzinfo
        is UTC
    )


def test_message_renders_context_placeholders() -> None:
    error = StreamError(context={"n": 42})
    assert error.message_es == "Se descartaron 42 ticks por saturación de cola"
    assert str(error) == error.message_es


def test_message_keeps_unknown_placeholders_visible() -> None:
    assert "{mb}" in QuantLabError(code=ErrorCode.RAM_WARNING).message_es


def test_explicit_message_overrides_template() -> None:
    error = ConfigError("El campo X está fuera de rango", detail="candle_event_quantile=1.5")
    assert error.message_es == "El campo X está fuera de rango"
    assert error.to_error_record().message_es == "El campo X está fuera de rango"


@pytest.mark.parametrize(
    ("error_class", "code", "retryable", "severity"),
    [
        (ConfigError, ErrorCode.CONFIG_INVALID, False, Severity.CRITICAL),
        (DerivTransportError, ErrorCode.DERIV_TRANSPORT, True, Severity.ERROR),
        (DerivTimeoutError, ErrorCode.DERIV_TIMEOUT, True, Severity.ERROR),
        (DerivRateLimitError, ErrorCode.DERIV_RATE_LIMIT, True, Severity.WARNING),
        (DerivAPIError, ErrorCode.DERIV_API_ERROR, False, Severity.ERROR),
        (SymbolNotActiveError, ErrorCode.SYMBOL_NOT_ACTIVE, False, Severity.ERROR),
        (AnalyticsError, ErrorCode.COMPUTE_FAILED, False, Severity.ERROR),
        (InsufficientDataError, ErrorCode.COMPUTE_INSUFFICIENT, False, Severity.INFO),
        (CacheError, ErrorCode.CACHE_EVICTION, False, Severity.INFO),
        (StreamError, ErrorCode.STREAM_DROPPED, False, Severity.WARNING),
        (ExportError, ErrorCode.EXPORT_FAILED, False, Severity.ERROR),
    ],
)
def test_subclasses_use_documented_defaults(
    error_class: type[QuantLabError],
    code: ErrorCode,
    retryable: bool,
    severity: Severity,
) -> None:
    error = error_class()
    assert error.code is code
    assert error.retryable is retryable
    assert error.severity is severity


def test_retryable_and_severity_can_be_overridden() -> None:
    error = DerivAPIError("rate limited", retryable=True, severity=Severity.WARNING)
    assert error.retryable is True
    assert error.severity is Severity.WARNING


def test_to_error_record_helper() -> None:
    record = to_error_record(ConfigError("boom"))
    assert isinstance(record, ErrorRecord)
    assert record.code is ErrorCode.CONFIG_INVALID
    assert record.source == "config"
