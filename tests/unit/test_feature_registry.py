"""Tests del registro de análisis y sus puertas de muestra (T015)."""

from __future__ import annotations

import pytest

from quantlab.config import GateConfig
from quantlab.services.feature_registry import ANALYSIS_IDS, FeatureRegistry


@pytest.fixture
def registry() -> FeatureRegistry:
    return FeatureRegistry(GateConfig())


def test_catalog_has_the_fourteen_base_analyses(registry: FeatureRegistry) -> None:
    ids = registry.analysis_ids()

    assert ids == ANALYSIS_IDS
    assert len(ids) == 14
    assert len(set(ids)) == 14
    assert {spec.id for spec in registry.specs} == set(ids)


def test_every_analysis_builds_a_valid_feature_status(registry: FeatureRegistry) -> None:
    for analysis_id in registry.analysis_ids():
        feature = registry.check_gate(analysis_id, "5min", 10_000, symbol="BOOM1000")

        assert feature.id == analysis_id
        assert feature.name_es
        assert feature.ui_path.startswith("/i/BOOM1000/")
        assert feature.min_sample >= 1
        assert feature.sample_size == 10_000
        assert feature.status == "ready"
        assert feature.reason is None


def test_min_sample_reads_gate_config_per_analysis(registry: FeatureRegistry) -> None:
    assert registry.min_sample("evt.gpd", "5min") == 30
    assert registry.min_sample("volatility.garch", "5min") == 200
    assert registry.min_sample("volatility.regimes", "5min") == 500
    assert registry.min_sample("zones.reaction", "5min") == 10
    assert registry.min_sample("survival.kaplan_meier", "5min") == 20
    assert registry.min_sample("autocorrelation", "5min") == 100
    assert registry.min_sample("diagnostics.residuals", "5min") == 100
    assert registry.min_sample("events.candle", "5min") == 50
    assert registry.min_sample("events.tick", "5min") == 300
    assert registry.min_sample("events.conditional", "5min") == 30
    assert registry.min_sample("walkforward.validation", "5min") == 3
    assert registry.min_sample("heatmap.price", "5min") == 2
    assert registry.min_sample("heatmap.events", "5min") == 1
    assert registry.min_sample("data.quality", "5min") == 1


def test_gate_config_changes_are_hot_reloadable() -> None:
    registry = FeatureRegistry(GateConfig(evt_min_excess=10))
    assert registry.min_sample("evt.gpd", "5min") == 10
    assert registry.check_gate("evt.gpd", "5min", 10, symbol="BOOM1000").status == "ready"

    registry.set_gate_config(GateConfig(evt_min_excess=100))
    assert registry.min_sample("evt.gpd", "5min") == 100
    feature = registry.check_gate("evt.gpd", "5min", 10, symbol="BOOM1000")
    assert feature.status == "insufficient"
    assert "muestra insuficiente" in (feature.reason or "")


def test_walkforward_gate_uses_min_folds(registry: FeatureRegistry) -> None:
    registry.set_gate_config(GateConfig(wf_min_folds=7))

    assert registry.min_sample("walkforward.validation", "1h") == 7


def test_boundary_is_ready_at_exact_min_sample(registry: FeatureRegistry) -> None:
    assert registry.check_gate("evt.gpd", "5min", 29, symbol="BOOM1000").status == "insufficient"
    assert registry.check_gate("evt.gpd", "5min", 30, symbol="BOOM1000").status == "ready"


def test_insufficient_feature_has_readable_reason(registry: FeatureRegistry) -> None:
    feature = registry.check_gate("volatility.garch", "5min", 12, symbol="CRASH500")

    assert feature.status == "insufficient"
    assert feature.sample_size == 12
    assert feature.min_sample == 200
    assert feature.reason is not None
    assert "12" in feature.reason and "200" in feature.reason
    assert feature.ui_path == "/i/CRASH500/volatility"


def test_not_applicable_does_not_require_sample(registry: FeatureRegistry) -> None:
    feature = registry.check_gate("events.tick", "5min", 0, symbol="BOOM1000", applicable=False)

    assert feature.status == "not_applicable"
    assert feature.reason is not None


def test_register_all_creates_pending_catalog(registry: FeatureRegistry) -> None:
    catalog = registry.register_all("BOOM1000", "1h")

    assert tuple(catalog) == ANALYSIS_IDS
    assert all(feature.status == "pending" for feature in catalog.values())
    assert {feature.stage for feature in catalog.values()} == {"in_sample", "oos"}
    assert sum(feature.stage == "oos" for feature in catalog.values()) == 1
    assert catalog["walkforward.validation"].stage == "oos"
    assert catalog["evt.gpd"].min_sample == 30
    assert catalog["evt.gpd"].sample_size == 0


def test_unknown_analysis_and_bad_timeframe_are_rejected(registry: FeatureRegistry) -> None:
    with pytest.raises(KeyError, match="desconocido"):
        registry.check_gate("no.existe", "5min", 10, symbol="BOOM1000")
    with pytest.raises(ValueError, match="temporalidad"):
        registry.check_gate("evt.gpd", "2min", 10, symbol="BOOM1000")
    with pytest.raises(ValueError, match="temporalidad"):
        registry.register_all("BOOM1000", "2min")
    with pytest.raises(ValueError, match="negativo"):
        registry.check_gate("evt.gpd", "5min", -1, symbol="BOOM1000")
    with pytest.raises(ValueError, match="symbol"):
        registry.get_ui_path("evt.gpd", " ")
