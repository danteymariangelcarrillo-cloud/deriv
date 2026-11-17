"""Utilidades compartidas del núcleo de QuantLab.

Este módulo es puro: sólo depende de la biblioteca estándar y de ``numpy``. No
realiza E/S de disco ni accesos de red, por lo que puede invocarse desde
cualquier capa del dashboard sin efectos secundarios.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = [
    "_safe_name",
    "config_hash",
    "downsample_lttb",
    "ensure_utc_datetime",
    "generate_deterministic_uuid5",
]


def _safe_name(name: str) -> str:
    """Convierte un nombre de instrumento en un identificador seguro.

    Migración byte a byte del helper legacy (``boom_crash_quant_analyzer.py``,
    L1649). Se usa ``str.isalnum()`` — consciente de Unicode, no plegado a
    ASCII — y se recortan los guiones bajos de los extremos.
    """
    if not isinstance(name, str):
        raise TypeError(f"name debe ser str, recibido {type(name).__name__}")
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")


def config_hash(payload: Mapping[str, Any] | BaseModel) -> str:
    """Devuelve el SHA-256 del JSON canónico de ``payload``.

    Acepta un mapping o un modelo pydantic v2 (se serializa con
    ``model_dump(mode="json")``). El JSON canónico ordena claves y elimina
    espacios, de modo que el mismo contenido lógico produce siempre el mismo
    hash: ``"sha256:" + 64 caracteres hexadecimales``.
    """
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        canonical: Any = model_dump(mode="json")
    elif isinstance(payload, Mapping):
        canonical = dict(payload)
    else:
        raise TypeError(
            "payload debe ser un Mapping[str, Any] o un modelo pydantic, "
            f"recibido {type(payload).__name__}"
        )

    canonical_json = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    json_bytes = canonical_json.encode("utf-8")
    return "sha256:" + hashlib.sha256(json_bytes).hexdigest()


def downsample_lttb(data: np.ndarray, max_points: int) -> np.ndarray:
    """Reduce ``data`` a ``max_points`` filas preservando la forma visual.

    Implementación propia de Largest-Triangle-Three-Buckets sobre numpy: no se
    añade la dependencia ``downsample``. La entrada debe ser un array
    ``(N, 2)`` con x monótona no decreciente en la columna 0 e y en la 1.

    Los buckets se calculan de forma vectorizada (cotas + medias con ``cumsum``)
    y sólo hay un bucle Python sobre los ``max_points`` de salida; el argmax del
    área del triángulo se resuelve con numpy. Se conservan siempre el primer y
    el último punto, y los picos sobreviven porque cada bucket aporta el punto
    de mayor desviación respecto a la tendencia local.
    """
    arr = np.asarray(data, dtype=float)
    if max_points < 3:
        raise ValueError(f"max_points debe ser >= 3, recibido {max_points}")
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"data debe tener forma (N, 2), recibida {arr.shape}")
    point_count = arr.shape[0]
    if point_count == 0:
        raise ValueError("data no puede estar vacío (N == 0)")
    if point_count <= max_points:
        return arr.copy()

    bucket_count = max_points - 2
    step = (point_count - 2) / bucket_count

    bucket_index = np.arange(bucket_count, dtype=np.float64)
    start_indexes = np.floor(bucket_index * step).astype(np.intp) + 1
    end_indexes = np.empty(bucket_count, dtype=np.intp)
    end_indexes[:-1] = start_indexes[1:]
    end_indexes[-1] = point_count - 1

    # Cada bucket de candidatos se compara contra la media del siguiente bucket.
    average_offsets = np.arange(1, bucket_count + 1, dtype=np.float64)
    average_start_indexes = np.floor(average_offsets * step).astype(np.intp) + 1
    average_end_indexes = np.minimum(
        np.floor((average_offsets + 1.0) * step).astype(np.intp) + 1,
        point_count,
    )

    x_values = np.ascontiguousarray(arr[:, 0])
    y_values = np.ascontiguousarray(arr[:, 1])

    average_counts = average_end_indexes - average_start_indexes
    safe_end_indexes = np.maximum(average_end_indexes, average_start_indexes)
    cumulative_x = np.concatenate(([0.0], np.cumsum(x_values)))
    cumulative_y = np.concatenate(([0.0], np.cumsum(y_values)))
    sums_x = cumulative_x[safe_end_indexes] - cumulative_x[average_start_indexes]
    sums_y = cumulative_y[safe_end_indexes] - cumulative_y[average_start_indexes]
    divisor = np.maximum(average_counts, 1)
    average_x = np.where(average_counts > 0, sums_x / divisor, x_values[-1])
    average_y = np.where(average_counts > 0, sums_y / divisor, y_values[-1])

    result = np.empty((max_points, 2), dtype=float)
    result[0] = arr[0]
    result[-1] = arr[-1]

    # Listas de Python para los escalares del bucle: evita empaquetar escalares
    # numpy en cada una de las ``max_points`` iteraciones.
    bucket_starts = start_indexes.tolist()
    bucket_ends = end_indexes.tolist()
    average_x_list = average_x.tolist()
    average_y_list = average_y.tolist()
    x_list = x_values.tolist()
    y_list = y_values.tolist()

    anchor = 0
    for bucket in range(bucket_count):
        bucket_start = bucket_starts[bucket]
        bucket_end = bucket_ends[bucket]
        if bucket_end <= bucket_start:
            chosen = bucket_start
        else:
            anchor_x = x_list[anchor]
            anchor_y = y_list[anchor]
            delta_x = anchor_x - average_x_list[bucket]
            delta_y = average_y_list[bucket] - anchor_y
            offset = delta_x * anchor_y + delta_y * anchor_x
            areas = np.abs(
                x_values[bucket_start:bucket_end] * delta_y
                + y_values[bucket_start:bucket_end] * delta_x
                - offset
            )
            chosen = bucket_start + int(areas.argmax())
        result[bucket + 1] = arr[chosen]
        anchor = chosen

    return result


def ensure_utc_datetime(value: datetime | float | str) -> datetime:
    """Normaliza ``value`` a un ``datetime`` consciente en UTC.

    Acepta ``datetime`` (ingenuo o con zona), epoch en segundos (``int`` o
    ``float``) y cadenas ISO-8601 con separador ``T`` o espacio y sufijo ``Z``
    opcional. El resultado siempre lleva ``tzinfo=timezone.utc``.
    """
    if isinstance(value, bool):
        raise TypeError("value no puede ser bool; se esperaba fecha, epoch o ISO-8601")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError(f"epoch fuera de rango: {value!r}") from exc
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("value no puede ser una cadena vacía")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"cadena ISO-8601 inválida: {value!r}") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    raise TypeError(
        "value debe ser datetime, int, float o str, "
        f"recibido {type(value).__name__}"
    )


def generate_deterministic_uuid5(namespace: str, *parts: str) -> str:
    """Genera un UUIDv5 estable para identificadores de zona/evento/snapshot.

    Contrato estable del namespace: se deriva con
    ``uuid.uuid5(uuid.NAMESPACE_DNS, f"quantlab:{namespace}")`` y el nombre es
    ``"|".join(str(part) for part in parts)``. Mismos argumentos → mismo UUID
    en cualquier proceso o ejecución; distinto namespace o distinto orden de
    partes → UUID distinto. Se devuelve la forma canónica en minúsculas.
    """
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("namespace no puede estar vacío ni ser sólo espacios")
    ns_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"quantlab:{namespace}")
    name = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(ns_uuid, name))
