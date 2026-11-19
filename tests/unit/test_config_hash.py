"""Tests de ``config_hash`` y de la actualización parcial (T001)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from quantlab.config import QuantLabConfig, config_hash


def test_config_hash_format() -> None:
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", QuantLabConfig().hash()) is not None


def test_config_hash_is_idempotent_and_matches_function() -> None:
    config = QuantLabConfig()
    reference = config_hash(config.analyzer, config.model, config.gates, config.ram, config.version)
    assert config.hash() == reference
    assert config.hash() == config.hash()
    assert config.hash() == QuantLabConfig().hash()


def test_config_hash_changes_on_semantic_change() -> None:
    base = QuantLabConfig()
    changed = base.updated({"analyzer": {"candle_event_quantile": 0.995}})
    assert base.hash() != changed.hash()


def test_config_hash_changes_with_version() -> None:
    base = QuantLabConfig()
    assert base.hash() != base.updated({"version": "1.0.1"}).hash()


def test_config_hash_ignores_non_semantic_input_order() -> None:
    first = QuantLabConfig(model={"garch_p": 2, "garch_q": 1})
    second = QuantLabConfig(model={"garch_q": 1, "garch_p": 2})
    assert first.hash() == second.hash()


# --------------------------- actualización parcial --------------------------


def test_updated_merges_only_the_given_section() -> None:
    base = QuantLabConfig()
    patched = base.updated({"analyzer": {"candle_event_quantile": 0.995}})
    assert patched.analyzer.candle_event_quantile == 0.995
    assert patched.analyzer.event_lookback_bars == base.analyzer.event_lookback_bars
    assert patched.model == base.model
    assert base.analyzer.candle_event_quantile == 0.99  # el original no muta


def test_updated_with_empty_patch_is_a_noop() -> None:
    base = QuantLabConfig()
    assert base.updated({}).hash() == base.hash()


def test_updated_accepts_version() -> None:
    assert QuantLabConfig().updated({"version": "1.1.0"}).version == "1.1.0"


def test_updated_rejects_unknown_section() -> None:
    with pytest.raises(ValueError, match="no válidas"):
        QuantLabConfig().updated({"unknown": {}})


@pytest.mark.parametrize(
    "patch",
    [
        {"analyzer": {"nope": 1}},
        {"analyzer": {"days": 0}},
        {"version": "x"},
    ],
)
def test_updated_rejects_invalid_values(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        QuantLabConfig().updated(patch)


def test_updated_rejects_non_mapping_section() -> None:
    with pytest.raises(ValueError, match="debe ser un objeto"):
        QuantLabConfig().updated({"analyzer": [("days", 30)]})  # type: ignore[list-item]
