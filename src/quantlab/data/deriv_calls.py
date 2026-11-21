"""Allowlist estricta de payloads enviados a la API pública de Deriv (T006).

Única fuente de verdad para request de solo lectura. Cualquier mensaje hacia
Deriv debe construirse con :func:`build_request` a partir de
:data:`ALLOWED_REQUESTS`; no se permite componer payloads a mano en otras capas.

Plantillas (``contracts/forbidden-calls.md §8``): ``active_symbols``,
``ticks_history_candles``, ``ticks_history_ticks``, ``subscribe_ticks``,
``ping`` y ``time``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

__all__ = [
    "ALLOWED_REQUESTS",
    "ALLOWED_RESPONSE_TYPES",
    "build_request",
    "validate_response",
]

#: Plantillas permitidas. La clave es el nombre lógico usado por las capas
#: superiores; el valor es el payload con placeholders ``{symbol}`` y ``{int}``.
#: Tratar como inmutable: usar siempre :func:`build_request`.
ALLOWED_REQUESTS: Final[dict[str, dict[str, str | int]]] = {
    "active_symbols": {"active_symbols": "brief"},
    "ticks_history_candles": {
        "ticks_history": "{symbol}",
        "style": "candles",
        "granularity": "{int}",
        "count": "{int}",
        "start": "{int}",
        "end": "{int}",
    },
    "ticks_history_ticks": {
        "ticks_history": "{symbol}",
        "style": "ticks",
        "count": "{int}",
        "end": "latest",
    },
    "subscribe_ticks": {"ticks": "{symbol}", "subscribe": 1},
    "ping": {"ping": 1},
    "time": {"time": 1},
}

#: ``msg_type`` de respuesta que el cliente puede consumir. Deriv responde
#: ``candles``/``history`` para ``ticks_history`` y ``ping``/``time`` para sus
#: llamadas equivalentes. Un mensaje ``error`` no es una respuesta de datos y
#: por tanto no valida.
ALLOWED_RESPONSE_TYPES: Final[frozenset[str]] = frozenset(
    {"active_symbols", "candles", "history", "ticks", "ping", "time"}
)

_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"^\{([a-z_]+)\}$")


def _render_int(template: str, key: str, value: object) -> int:
    # bool es subclase de int: se rechaza explícitamente para no enviar True/False.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"parámetro {key!r} de {template!r} debe ser int, recibido {type(value).__name__}"
        )
    return value


def _render_symbol(template: str, key: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"parámetro {key!r} de {template!r} debe ser str, recibido {type(value).__name__}"
        )
    symbol = value.strip()
    if not symbol:
        raise ValueError(f"parámetro {key!r} de {template!r} no puede estar vacío")
    return symbol


_RENDERERS = {"int": _render_int, "symbol": _render_symbol}


def build_request(template: str, **params: object) -> dict[str, str | int]:
    """Construye un payload permitido a partir de una plantilla de la allowlist.

    Cada placeholder ``{int}``/``{symbol}`` se rellena con el parámetro cuyo
    nombre coincide con la clave del payload. Ejemplos::

        build_request("ping")
        build_request("subscribe_ticks", ticks="CRASH500")
        build_request("ticks_history_ticks", ticks_history="BOOM1000", count=5000)

    Args:
        template: clave de :data:`ALLOWED_REQUESTS`.
        **params: valor de cada clave con placeholder.

    Returns:
        Un dict nuevo, listo para serializar a JSON.

    Raises:
        ValueError: si la plantilla no existe, falta un parámetro, sobra un
            parámetro o el símbolo está vacío.
        TypeError: si un parámetro no respeta el tipo del placeholder.
    """
    try:
        spec = ALLOWED_REQUESTS[template]
    except KeyError:
        raise ValueError(
            f"plantilla no permitida: {template!r}; permitidas: {sorted(ALLOWED_REQUESTS)}"
        ) from None

    rendered: dict[str, str | int] = {}
    consumed: set[str] = set()

    for key, value in spec.items():
        match = _PLACEHOLDER.match(value) if isinstance(value, str) else None
        if match is None:
            rendered[key] = value
            continue

        kind = match.group(1)
        renderer = _RENDERERS.get(kind)
        if renderer is None:
            raise ValueError(f"placeholder desconocido {kind!r} en plantilla {template!r}")
        if key not in params:
            raise ValueError(f"falta el parámetro {key!r} para la plantilla {template!r}")

        consumed.add(key)
        rendered[key] = renderer(template, key, params[key])

    extra = sorted(set(params) - consumed)
    if extra:
        raise ValueError(f"parámetros no permitidos para {template!r}: {extra}")

    return rendered


def validate_response(response: Mapping[str, object]) -> bool:
    """Indica si la respuesta tiene un ``msg_type`` admitido por la allowlist.

    No lanza: un ``msg_type`` ausente, desconocido o un mensaje de error
    devuelven ``False``.
    """
    if not isinstance(response, Mapping):
        return False
    return response.get("msg_type") in ALLOWED_RESPONSE_TYPES
