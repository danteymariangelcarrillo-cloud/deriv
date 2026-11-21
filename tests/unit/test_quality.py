"""Tests de DataQualityReport y calculate_quality (T010)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from quantlab.data.quality import (
    DataQualityReport,
    calculate_quality,
)
from quantlab.errors import DataError, ErrorCode, ErrorRecord

T0 = 1_700_000_000


def _row(
    epoch: int,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
) -> dict[str, float | int]:
    return {"epoch": epoch, "open": open_, "high": high, "low": low, "close": close}


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _consecutive(count: int, step: int = 60) -> pd.DataFrame:
    return _frame([_row(T0 + i * step) for i in range(count)])


def _now_last(df: pd.DataFrame, offset: int = 10) -> int:
    return int(df["epoch"].iloc[-1]) + offset


def test_model_contract_fields_and_frozen() -> None:
    report = DataQualityReport(
        status="ok",
        expected_bars=1,
        received_bars=1,
        coverage_pct=100.0,
        missing_bars=0,
        gaps_count=0,
        duplicates_removed=0,
        invalid_ohlc=0,
        first_epoch=T0,
        last_epoch=T0,
        stale_seconds=0,
    )

    dumped = report.model_dump(mode="json")
    assert set(dumped) == {
        "status",
        "expected_bars",
        "received_bars",
        "coverage_pct",
        "missing_bars",
        "gaps_count",
        "duplicates_removed",
        "invalid_ohlc",
        "first_epoch",
        "last_epoch",
        "stale_seconds",
        "dropped_ticks",
        "errors",
        "notes",
    }
    with pytest.raises(ValidationError):
        report.status = "fail"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DataQualityReport(
            status="ok",
            expected_bars=1,
            received_bars=1,
            coverage_pct=100.0,
            missing_bars=0,
            gaps_count=0,
            duplicates_removed=0,
            invalid_ohlc=0,
            extra_field=True,  # type: ignore[call-arg]
        )


def test_perfect_series_is_ok_and_sorts_input() -> None:
    df = _frame([_row(T0 + 120), _row(T0), _row(T0 + 60)])
    now = T0 + 130

    report = calculate_quality(df, "1min", 3, now=now)

    assert report.status == "ok"
    assert report.received_bars == 3
    assert report.expected_bars == 3
    assert report.coverage_pct == 100.0
    assert report.missing_bars == 0
    assert (report.gaps_count, report.duplicates_removed, report.invalid_ohlc) == (0, 0, 0)
    assert (report.first_epoch, report.last_epoch) == (T0, T0 + 120)
    assert report.stale_seconds == 10
    assert report.errors == []
    assert report.notes == ["Cobertura completa, sin huecos ni duplicados."]


def test_accepts_capitalized_columns_and_extra_columns() -> None:
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime([T0, T0 + 60], unit="s", utc=True),
            "epoch": [T0, T0 + 60],
            "Open": [100.0, 100.0],
            "High": [101.0, 101.0],
            "Low": [99.0, 99.0],
            "Close": [100.5, 100.5],
        }
    )

    report = calculate_quality(df, "1min", 2, now=T0 + 70)

    assert report.status == "ok"
    assert report.received_bars == 2
    assert report.first_epoch == T0
    assert report.last_epoch == T0 + 60


def test_duplicates_keep_last_and_are_counted() -> None:
    df = _frame(
        [
            _row(T0, close=100.5),
            _row(T0 + 60, close=102.0, high=103.0),
            _row(T0 + 60, close=105.0, high=106.0),
        ]
    )

    report = calculate_quality(df, "1min", 2, now=T0 + 70)

    assert report.received_bars == 2
    assert report.duplicates_removed == 1
    assert report.coverage_pct == 100.0
    assert report.status == "ok"
    assert any("duplicadas" in note for note in report.notes)


@pytest.mark.parametrize(
    "bad_row",
    [
        _row(T0 + 30, high=98.0, low=99.0),
        _row(T0 + 30, high=100.0, close=101.0),
        _row(T0 + 30, low=101.0, open_=100.0, close=100.0),
    ],
)
def test_invalid_ohlc_downgrades_full_coverage_to_warn(bad_row: dict[str, Any]) -> None:
    df = _frame([_row(T0), bad_row, _row(T0 + 60)])

    report = calculate_quality(df, "1min", 2, now=T0 + 70)

    assert report.received_bars == 2
    assert report.invalid_ohlc == 1
    assert report.coverage_pct == 100.0
    assert report.status == "warn"
    assert any("OHLC inválido" in note for note in report.notes)


def test_gap_counts_and_missing_bars() -> None:
    epochs = [T0, T0 + 60, T0 + 300]
    df = _frame([_row(epoch) for epoch in epochs])

    report = calculate_quality(df, "1min", 6, now=T0 + 310)

    assert report.received_bars == 3
    assert report.missing_bars == 3
    assert report.gaps_count == 1
    assert report.coverage_pct == 50.0
    assert report.status == "fail"
    assert any("huecos" in note for note in report.notes)


def test_critical_gap_is_reported() -> None:
    epochs = [T0 + i * 60 for i in range(50)] + [T0 + (i + 60) * 60 for i in range(50)]
    df = _frame([_row(epoch) for epoch in epochs])

    report = calculate_quality(df, "1min", 100, now=epochs[-1] + 10)

    assert report.gaps_count == 1
    assert report.received_bars == 100
    assert report.missing_bars == 0
    assert report.status == "warn"  # coverage 100, pero el hueco se informa
    assert any("crítico" in note for note in report.notes)


def test_missing_bars_at_edges_do_not_create_gaps() -> None:
    df = _consecutive(98)

    report = calculate_quality(df, "1min", 100, now=_now_last(df))

    assert report.gaps_count == 0
    assert report.missing_bars == 2
    assert report.coverage_pct == 98.0
    assert report.status == "ok"


@pytest.mark.parametrize(
    ("received", "expected", "coverage", "status"),
    [
        (98, 100, 98.0, "ok"),
        (97, 100, 97.0, "warn"),
        (90, 100, 90.0, "warn"),
        (89, 100, 89.0, "fail"),
    ],
)
def test_coverage_thresholds(received: int, expected: int, coverage: float, status: str) -> None:
    df = _consecutive(received)

    report = calculate_quality(df, "1min", expected, now=_now_last(df))

    assert report.coverage_pct == coverage
    assert report.status == status


def test_coverage_is_capped_at_100_and_missing_never_negative() -> None:
    df = _consecutive(5)

    report = calculate_quality(df, "1min", 3, now=_now_last(df))

    assert report.coverage_pct == 100.0
    assert report.missing_bars == 0
    assert report.status == "ok"


def test_empty_series_fails_with_data_empty() -> None:
    report = calculate_quality(pd.DataFrame(), "5min", 12, now=T0, symbol="CRASH500")

    assert report.status == "fail"
    assert report.received_bars == 0
    assert report.expected_bars == 12
    assert report.coverage_pct == 0.0
    assert report.missing_bars == 12
    assert report.first_epoch is None
    assert report.last_epoch is None
    assert report.stale_seconds is None
    assert [error.code for error in report.errors] == [ErrorCode.DATA_EMPTY]
    assert report.errors[0].context["symbol"] == "CRASH500"
    assert report.errors[0].context["timeframe"] == "5min"
    assert any("No se recibieron" in note for note in report.notes)


def test_expected_zero_gives_zero_coverage() -> None:
    report = calculate_quality(pd.DataFrame(), "1min", 0, now=T0)

    assert report.coverage_pct == 0.0
    assert report.missing_bars == 0
    assert report.status == "fail"


def test_stale_seconds_and_optional_max_staleness() -> None:
    df = _consecutive(3)
    last = int(df["epoch"].iloc[-1])

    fresh = calculate_quality(df, "1min", 3, now=last + 900, max_stale_seconds=900)
    assert fresh.stale_seconds == 900
    assert fresh.status == "ok"

    stale = calculate_quality(
        df,
        "1min",
        3,
        now=last + 901,
        max_stale_seconds=900,
        symbol="BOOM1000",
    )
    assert stale.stale_seconds == 901
    assert stale.status == "fail"
    assert [error.code for error in stale.errors] == [ErrorCode.DATA_STALE]
    assert stale.errors[0].context["symbol"] == "BOOM1000"


def test_staleness_is_not_evaluated_without_limit() -> None:
    df = _consecutive(2)

    report = calculate_quality(df, "1min", 2, now=T0 + 10_000_000)

    assert report.stale_seconds == 10_000_000 - 60
    assert report.status == "ok"


def test_context_counts_notes_and_errors_are_preserved() -> None:
    df = _consecutive(2)
    previous = DataError("aviso previo", code=ErrorCode.DATA_STALE).to_error_record()

    report = calculate_quality(
        df,
        "1min",
        2,
        duplicates_removed=4,
        invalid_ohlc=2,
        dropped_ticks=7,
        notes=["paginación truncada"],
        errors=[previous],
        now=_now_last(df),
    )

    assert report.duplicates_removed == 4
    assert report.invalid_ohlc == 2
    assert report.dropped_ticks == 7
    assert report.notes[0] == "paginación truncada"
    assert report.errors == [previous]


def test_rows_with_infinite_ohlc_are_counted_invalid() -> None:
    df = _frame(
        [
            _row(T0),
            _row(T0 + 60, high=float("inf")),
        ]
    )

    report = calculate_quality(df, "1min", 2, now=T0 + 70)

    assert report.received_bars == 1
    assert report.invalid_ohlc == 1
    assert report.status == "fail"


def test_rows_with_nan_ohlc_are_dropped() -> None:
    df = _frame([_row(T0), {"epoch": T0 + 60, "open": None, "high": 1, "low": 1, "close": 1}])

    report = calculate_quality(df, "1min", 2, now=T0 + 70)

    assert report.received_bars == 1
    assert report.invalid_ohlc == 1
    assert report.coverage_pct == 50.0
    assert report.status == "fail"


@pytest.mark.parametrize(
    ("df", "timeframe", "expected", "match"),
    [
        (pd.DataFrame({"epoch": [T0], "open": [1]}), "1min", 1, "columnas"),
        (_consecutive(1), "2min", 1, "timeframe"),
        (_consecutive(1), "1min", -1, "expected_bars"),
        (_consecutive(1), "1min", True, "expected_bars"),
        (_consecutive(1), "1min", 1, "no aplica"),
    ],
)
def test_invalid_arguments_raise(
    df: pd.DataFrame, timeframe: str, expected: Any, match: str
) -> None:
    if match == "no aplica":
        with pytest.raises(ValueError, match="dropped_ticks"):
            calculate_quality(df, timeframe, expected, dropped_ticks=-1)
        return
    with pytest.raises(ValueError, match=match):
        calculate_quality(df, timeframe, expected)


def test_non_dataframe_raises_type_error() -> None:
    with pytest.raises(TypeError, match="DataFrame"):
        calculate_quality([{"epoch": T0}], "1min", 1)  # type: ignore[arg-type]


def test_duplicate_canonical_columns_raise() -> None:
    df = pd.DataFrame(
        {
            "epoch": [T0],
            "open": [100.0],
            "Open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
        }
    )
    with pytest.raises(ValueError, match="duplicada"):
        calculate_quality(df, "1min", 1)


def test_error_record_type_is_enforced() -> None:
    record = ErrorRecord.model_validate(
        {
            "code": "DATA_EMPTY",
            "severity": "warning",
            "message_es": "sin datos",
            "retryable": False,
            "source": "data",
        }
    )
    report = calculate_quality(
        _consecutive(1), "1min", 1, errors=[record], now=_now_last(_consecutive(1))
    )
    assert report.errors == [record]
