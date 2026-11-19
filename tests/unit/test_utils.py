"""Tests unitarios de las utilidades del núcleo (T003).

Sin red, sin ficheros y sin esperas: la suite completa debe correr en < 1 s.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pytest
from pydantic import BaseModel

from quantlab.utils import (
    _safe_name,
    config_hash,
    downsample_lttb,
    ensure_utc_datetime,
    generate_deterministic_uuid5,
)


class _SampleConfig(BaseModel):
    """Configuración mínima para probar la entrada de tipo modelo pydantic."""

    alpha: int = 1
    beta: str = "x"


def _make_series(point_count: int) -> np.ndarray:
    """Serie sintética (x monótona, y ondulada) del tamaño pedido."""
    x_values = np.arange(point_count, dtype=float)
    y_values = np.sin(x_values / 50.0) * 10.0 + 100.0
    return np.column_stack((x_values, y_values))


# ----------------------------- _safe_name -----------------------------


def test_safe_name_migrates_legacy_behaviour() -> None:
    assert _safe_name("Volatility 75 (1s) Index") == "volatility_75__1s__index"


def test_safe_name_strips_surrounding_underscores() -> None:
    assert _safe_name("___abc___") == "abc"


def test_safe_name_handles_empty_string() -> None:
    assert _safe_name("") == ""


def test_safe_name_keeps_unicode_letters() -> None:
    # isalnum() es Unicode-aware: los acentos se preservan, no se pliegan a ASCII.
    assert _safe_name("Café BOOM Índice") == "café_boom_índice"


def test_safe_name_keeps_digits() -> None:
    assert _safe_name("BOOM1000") == "boom1000"


def test_safe_name_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        _safe_name(75)  # type: ignore[arg-type]


# ----------------------------- config_hash -----------------------------


def test_config_hash_format() -> None:
    digest = config_hash({"a": 1})
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None


def test_config_hash_is_key_order_invariant() -> None:
    first = config_hash({"a": 1, "b": {"c": 2, "d": [3, 4]}})
    second = config_hash({"b": {"d": [3, 4], "c": 2}, "a": 1})
    assert first == second


def test_config_hash_is_idempotent() -> None:
    payload = {"symbol": "BOOM1000", "windows": [10, 20, 50], "enabled": True}
    assert config_hash(payload) == config_hash(payload)


def test_config_hash_changes_on_semantic_change() -> None:
    assert config_hash({"a": 1}) != config_hash({"a": 2})
    assert config_hash({"a": 1}) != config_hash({"a": 1, "b": None})
    assert config_hash({"a": [1, 2]}) != config_hash({"a": [2, 1]})


def test_config_hash_handles_nested_payload() -> None:
    first = config_hash({"outer": {"inner": [{"z": 1, "a": 2}]}})
    second = config_hash({"outer": {"inner": [{"a": 2, "z": 1}]}})
    assert first == second
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first) is not None


def test_config_hash_accepts_pydantic_model() -> None:
    model = _SampleConfig()
    digest = config_hash(model)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None
    assert digest == config_hash(model.model_dump(mode="json"))
    assert digest == config_hash({"alpha": 1, "beta": "x"})
    assert digest != config_hash(_SampleConfig(alpha=2))


@pytest.mark.parametrize("payload", [42, "texto", [1, 2], None])
def test_config_hash_rejects_non_mapping(payload: object) -> None:
    with pytest.raises(TypeError):
        config_hash(payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [{"a", "b"}, datetime(2024, 1, 1, tzinfo=UTC), b"bytes", np.int64(5)],
)
def test_config_hash_rejects_non_json_values(value: object) -> None:
    """Un ``set`` no puede convertirse con ``str``: su orden varía entre procesos."""
    with pytest.raises(TypeError, match="no serializable como JSON"):
        config_hash({"key": value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_config_hash_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(ValueError):
        config_hash({"key": value})


# ----------------------------- downsample_lttb -----------------------------


def test_downsample_lttb_shape_and_endpoints() -> None:
    data = _make_series(50_000)
    reduced = downsample_lttb(data, 5_000)
    assert reduced.shape == (5_000, 2)
    assert reduced.dtype == np.float64
    np.testing.assert_array_equal(reduced[0], data[0])
    np.testing.assert_array_equal(reduced[-1], data[-1])


def test_downsample_lttb_keeps_x_monotonic() -> None:
    reduced = downsample_lttb(_make_series(50_000), 5_000)
    assert np.all(np.diff(reduced[:, 0]) >= 0.0)


def test_downsample_lttb_preserves_spike() -> None:
    """El pico sobrevive aunque el muestreo por paso fijo lo perdería.

    El índice 5003 no cae en la rejilla del muestreo ingenuo (``stride = 10``),
    así que un ``data[::stride]`` no contendría el pico: sólo la selección por
    área de triángulo de LTTB lo conserva.
    """
    data = _make_series(10_000)
    spike_index = 5_003
    data[spike_index, 1] = 500.0
    reduced = downsample_lttb(data, 1_000)
    assert reduced[:, 1].max() == 500.0
    assert np.any(reduced[:, 0] == spike_index)
    stride = data.shape[0] // 1_000
    naive_stride_sample = data[::stride]
    assert not np.any(naive_stride_sample[:, 0] == spike_index)


def test_downsample_lttb_pins_triangle_area_selection() -> None:
    """Fija la regla de selección LTTB en un fixture calculable a mano.

    Con ``max_points=3`` hay un único bucket que cubre los índices 1..5, la
    media del bucket siguiente es ``(6, 0)`` y el ancla es ``(0, 0)``, de modo
    que el área vale ``6 * |y|`` y el candidato elegido es el pico del índice 1.
    Un muestreo por paso fijo (``data[:: 2]``) devolvería el índice 2 y no
    contiene el valor ``1.0`` en x, así que este test lo discrimina.
    """
    data = np.array([[0, 0], [1, 10], [2, 0], [3, 0], [4, 0], [5, 0], [6, 0]], dtype=float)
    expected = np.array([[0.0, 0.0], [1.0, 10.0], [6.0, 0.0]])
    naive_stride_sample = data[:: data.shape[0] // 3]
    assert not np.any(naive_stride_sample[:, 0] == 1.0)
    np.testing.assert_array_equal(downsample_lttb(data, 3), expected)


def test_downsample_lttb_returns_small_input_unchanged() -> None:
    data = _make_series(10)
    result = downsample_lttb(data, 10)
    np.testing.assert_array_equal(result, data)
    assert not np.shares_memory(result, data)


def test_downsample_lttb_returns_minimal_input_unchanged() -> None:
    data = _make_series(4)
    np.testing.assert_array_equal(downsample_lttb(data, 5), data)


@pytest.mark.parametrize("max_points", [0, 1, 2, -5])
def test_downsample_lttb_rejects_too_few_points(max_points: int) -> None:
    with pytest.raises(ValueError):
        downsample_lttb(_make_series(100), max_points)


@pytest.mark.parametrize("max_points", [5.0, "5", True])
def test_downsample_lttb_rejects_non_integer_max_points(max_points: object) -> None:
    with pytest.raises(TypeError):
        downsample_lttb(_make_series(100), max_points)  # type: ignore[arg-type]


def test_downsample_lttb_accepts_numpy_integer_max_points() -> None:
    data = _make_series(100)
    np.testing.assert_array_equal(
        downsample_lttb(data, np.int64(10)),  # type: ignore[arg-type]
        downsample_lttb(data, 10),
    )


@pytest.mark.parametrize(
    "bad_data",
    [
        np.arange(100, dtype=float),
        np.zeros((100, 3)),
        np.zeros((50, 2))[None, ...],
    ],
)
def test_downsample_lttb_rejects_wrong_shape(bad_data: np.ndarray) -> None:
    with pytest.raises(ValueError):
        downsample_lttb(bad_data, 10)


def test_downsample_lttb_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        downsample_lttb(np.empty((0, 2)), 10)


def test_downsample_lttb_rejects_non_monotonic_x() -> None:
    data = _make_series(100)
    data[40, 0] = 10.0  # rompe la monotonía: 10 < 39
    with pytest.raises(ValueError, match="monótona"):
        downsample_lttb(data, 10)


def test_downsample_lttb_accepts_repeated_x() -> None:
    data = np.array([[0, 0], [0, 5], [1, 1], [2, 2]], dtype=float)
    reduced = downsample_lttb(data, 3)
    assert reduced.shape == (3, 2)
    np.testing.assert_array_equal(reduced[0], data[0])
    np.testing.assert_array_equal(reduced[-1], data[-1])


def test_downsample_lttb_performance() -> None:
    """La implementación numpy interna debe reducir 50k → 5k puntos en <= 100 ms.

    El criterio de la spec está re-baselizado: ``<= 100 ms`` para la
    implementación numpy propia (medido ~33 ms de mediana y ~42 ms de máximo en
    la máquina de referencia) y el objetivo ``< 5 ms`` aplica sólo al wrapper C
    opcional de la librería ``downsample``, que no es dependencia del proyecto.
    El margen de 100 ms es el límite de CI: detecta regresiones a un bucle
    Python por punto sin depender de la carga del runner.
    """
    data = _make_series(50_000)
    downsample_lttb(data, 5_000)  # calentamiento
    started = time.perf_counter()
    result = downsample_lttb(data, 5_000)
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    assert result.shape == (5_000, 2)
    assert elapsed_ms < 100.0, f"LTTB tardó {elapsed_ms:.2f} ms (límite CI 100 ms)"


# --------------------------- ensure_utc_datetime ---------------------------


def test_ensure_utc_naive_datetime() -> None:
    result = ensure_utc_datetime(datetime(2024, 1, 1, 0, 0, 0))
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, tzinfo=UTC)


def test_ensure_utc_aware_non_utc_datetime() -> None:
    aware = datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    result = ensure_utc_datetime(aware)
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, tzinfo=UTC)


def test_ensure_utc_epoch_int() -> None:
    result = ensure_utc_datetime(1_704_067_200)
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, tzinfo=UTC)


def test_ensure_utc_epoch_float() -> None:
    result = ensure_utc_datetime(1_704_067_200.5)
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, 0, 0, 0, 500_000, tzinfo=UTC)


def test_ensure_utc_numpy_integer_epoch() -> None:
    result = ensure_utc_datetime(np.int64(1_704_067_200))
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, tzinfo=UTC)
    assert result == ensure_utc_datetime(1_704_067_200)


def test_ensure_utc_numpy_float_epoch() -> None:
    result = ensure_utc_datetime(np.float64(1_704_067_200.5))
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, 0, 0, 0, 500_000, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    ["2024-01-01T00:00:00Z", "2024-01-01 00:00:00"],
)
def test_ensure_utc_iso_string(text: str) -> None:
    result = ensure_utc_datetime(text)
    assert result.tzinfo is UTC
    assert result == datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("value", [True, False, object(), None])
def test_ensure_utc_rejects_wrong_type(value: object) -> None:
    with pytest.raises(TypeError):
        ensure_utc_datetime(value)  # type: ignore[arg-type]


def test_ensure_utc_rejects_bad_string() -> None:
    with pytest.raises(ValueError):
        ensure_utc_datetime("no-es-una-fecha")


# ---------------------- generate_deterministic_uuid5 ----------------------


def test_uuid5_is_deterministic() -> None:
    first = generate_deterministic_uuid5("event", "BOOM1000", "1min", "spike", "1704", "1000.5")
    second = generate_deterministic_uuid5("event", "BOOM1000", "1min", "spike", "1704", "1000.5")
    assert first == second


def test_uuid5_changes_with_namespace() -> None:
    assert generate_deterministic_uuid5("zone", "BOOM1000") != generate_deterministic_uuid5(
        "event", "BOOM1000"
    )


def test_uuid5_changes_with_part_order() -> None:
    assert generate_deterministic_uuid5("event", "a", "b") != generate_deterministic_uuid5(
        "event", "b", "a"
    )


@pytest.mark.parametrize("namespace", ["", "   ", "\t"])
def test_uuid5_rejects_blank_namespace(namespace: str) -> None:
    with pytest.raises(ValueError):
        generate_deterministic_uuid5(namespace, "BOOM1000")


@pytest.mark.parametrize("namespace", [5, None, 5.0])
def test_uuid5_rejects_non_str_namespace(namespace: object) -> None:
    with pytest.raises(TypeError):
        generate_deterministic_uuid5(namespace, "BOOM1000")  # type: ignore[arg-type]


def test_uuid5_is_version_five() -> None:
    raw = generate_deterministic_uuid5("snapshot", "BOOM1000", "42")
    parsed = uuid.UUID(raw)
    assert parsed.version == 5
    assert raw == raw.lower()
