"""Tests de configuración tipada y de entorno (T001)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from quantlab.config import (
    TIMEFRAMES,
    AnalyzerConfig,
    GateConfig,
    ModelConfig,
    QuantLabConfig,
    RAMConfig,
)
from quantlab.settings import DEFAULT_DERIV_ENDPOINTS, Settings

# Defaults de `AnalyzerConfig` en legacy/boom_crash_quant_analyzer.py (v2.2.0).
LEGACY_ANALYZER_DEFAULTS = {
    "days": 20,
    "candle_event_quantile": 0.99,
    "event_lookback_bars": 200,
    "min_move_pct": 0.0,
    "tick_event_quantile": 0.999,
    "tick_event_lookback": 2000,
    "zone_tolerance_atr": 0.5,
    "pivot_window": 3,
    "min_zone_touches": 3,
    "touch_separation_bars": 3,
    "response_horizon_bars": 10,
    "favourable_atr": 1.0,
    "adverse_atr": 0.75,
}


# ----------------------------- AnalyzerConfig -----------------------------


def test_analyzer_defaults_match_legacy_v220() -> None:
    data = AnalyzerConfig().model_dump()
    assert data.pop("timeframe_overrides") == {}
    assert data == LEGACY_ANALYZER_DEFAULTS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("days", 0),
        ("candle_event_quantile", 0.5),
        ("candle_event_quantile", 1.0),
        ("event_lookback_bars", 9),
        ("min_move_pct", -0.1),
        ("tick_event_quantile", 0.5),
        ("tick_event_lookback", 299),
        ("zone_tolerance_atr", 0.0),
        ("pivot_window", 0),
        ("min_zone_touches", 0),
        ("touch_separation_bars", 0),
        ("response_horizon_bars", 0),
        ("favourable_atr", 0.0),
        ("adverse_atr", 0.0),
    ],
)
def test_analyzer_rejects_out_of_range_values(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        AnalyzerConfig(**{field: value})


def test_analyzer_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AnalyzerConfig(unknown_field=1)  # type: ignore[call-arg]


def test_analyzer_accepts_timeframe_overrides() -> None:
    config = AnalyzerConfig(timeframe_overrides={"5min": {"event_lookback_bars": 150}})
    assert config.timeframe_overrides["5min"]["event_lookback_bars"] == 150


def test_analyzer_rejects_unknown_timeframe_override() -> None:
    with pytest.raises(ValidationError, match="temporalidades no válidas"):
        AnalyzerConfig(timeframe_overrides={"2min": {"event_lookback_bars": 150}})


def test_analyzer_is_frozen() -> None:
    config = AnalyzerConfig()
    with pytest.raises(ValidationError):
        config.days = 30  # type: ignore[misc]


# ------------------------------ ModelConfig -------------------------------


@pytest.mark.parametrize("field", ["wf_train_days", "wf_test_days", "wf_step_days"])
def test_model_wf_windows_cover_all_timeframes(field: str) -> None:
    values = getattr(ModelConfig(), field)
    assert set(values) == set(TIMEFRAMES)
    assert all(value >= 1 for value in values.values())


def test_model_rejects_incomplete_wf_windows() -> None:
    partial = {tf: 10 for tf in TIMEFRAMES[:-1]}
    with pytest.raises(ValidationError, match="wf_train_days"):
        ModelConfig(wf_train_days=partial)


@pytest.mark.parametrize(
    ("field", "value"), [("garch_p", 0), ("garch_q", 4), ("rolling_window", 4)]
)
def test_model_rejects_out_of_range_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        ModelConfig(**{field: value})


def test_model_rejects_unknown_regime_method() -> None:
    with pytest.raises(ValidationError):
        ModelConfig(regime_method="hmm")  # type: ignore[arg-type]


# ------------------------------- GateConfig -------------------------------


def test_gates_have_twelve_documented_limits() -> None:
    data = GateConfig().model_dump()
    assert len(data) == 12
    assert set(data["wf_min_days"]) == set(TIMEFRAMES)


def test_gates_reject_incomplete_min_days() -> None:
    with pytest.raises(ValidationError, match="wf_min_days"):
        GateConfig(wf_min_days={"5min": 60, "1h": 180})


# -------------------------------- RAMConfig -------------------------------


def test_ram_defaults() -> None:
    ram = RAMConfig()
    assert ram.max_ticks_per_symbol == 50_000
    assert ram.max_candles_per_tf == 30_000
    assert ram.downsample_max_points == 5_000
    assert ram.ram_warning_mb < ram.ram_critical_mb


def test_ram_rejects_warning_above_critical() -> None:
    with pytest.raises(ValidationError, match="ram_warning_mb"):
        RAMConfig(ram_warning_mb=420, ram_critical_mb=420)


# ------------------------------ QuantLabConfig ----------------------------


def test_quantlab_config_composes_all_sections() -> None:
    config = QuantLabConfig()
    assert config.version == "1.0.0"
    assert config.analyzer.days == 20
    assert config.model.regime_method == "markov_switching"
    assert config.gates.wf_min_folds == 3
    assert config.ram.figure_cache_size == 20


def test_quantlab_config_rejects_bad_version() -> None:
    with pytest.raises(ValidationError):
        QuantLabConfig(version="1.0")


# --------------------------------- Settings -------------------------------


def test_settings_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.host == "127.0.0.1"
    assert settings.port == 8050
    assert settings.debug is False
    assert settings.deriv_endpoints == DEFAULT_DERIV_ENDPOINTS
    assert settings.rate_limit_per_min == 150
    assert settings.max_ticks == 50_000
    assert settings.log_level == "INFO"


def test_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTLAB_PORT", "8060")
    monkeypatch.setenv("QUANTLAB_SEED", "7")
    monkeypatch.setenv("QUANTLAB_DERIV_ENDPOINTS", "wss://a.example/ws, wss://b.example/ws")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.port == 8060
    assert settings.seed == 7
    assert settings.deriv_endpoints == ("wss://a.example/ws", "wss://b.example/ws")


def test_settings_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("QUANTLAB_PORT=8070\nQUANTLAB_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.port == 8070
    assert settings.log_level == "DEBUG"


def test_settings_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTLAB_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_rejects_non_ws_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTLAB_DERIV_ENDPOINTS", "https://example.com")
    with pytest.raises(ValidationError, match="WebSocket inválido"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_rejects_warning_above_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUANTLAB_RAM_WARNING_MB", "500")
    with pytest.raises(ValidationError, match="RAM_WARNING_MB"):
        Settings(_env_file=None)  # type: ignore[call-arg]
