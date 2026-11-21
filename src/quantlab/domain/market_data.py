"""Modelos de entrada de mercado: ``Instrument``, ``Candle`` y ``Tick`` (T011).

``Instrument`` es configuración (no datos): declara símbolo, nombre visible,
dirección y ventanas de histórico. ``Candle`` y ``Tick`` son los datos ya
normalizados que consumen la caché RAM y la analítica.

Invariantes aplicadas aquí (``001-crash-boom-dashboard/data-model.md`` §3.1-3.3):

- ``INV-001``: la dirección es coherente con el símbolo (BOOM1000 → ``up``,
  CRASH500 → ``down``).
- ``INV-002``: ``history_days`` cubre exactamente las seis temporalidades.
- ``INV-010``: ``low <= open, close <= high``.
- ``INV-014``: los precios son finitos y positivos.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field, field_validator, model_serializer, model_validator

from quantlab.config import TIMEFRAMES
from quantlab.domain.types import Direction, DomainModel, Source, Timeframe
from quantlab.utils import ensure_utc_datetime

__all__ = [
    "DEFAULT_INSTRUMENTS",
    "Candle",
    "Instrument",
    "Tick",
    "get_instrument",
]

#: Dirección de cada índice conocido; ``INV-001`` la exige coherente.
_KNOWN_DIRECTIONS: dict[str, Direction] = {"BOOM1000": "up", "CRASH500": "down"}

#: Días de histórico por temporalidad tal como los fija data-model §6.1.
_DEFAULT_HISTORY_DAYS: dict[str, int] = {
    "1min": 30,
    "5min": 90,
    "15min": 180,
    "1h": 365,
    "4h": 730,
    "1d": 1825,
}

_SYMBOL_PATTERN = r"^[A-Z0-9]{3,20}$"
_HEX_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class Instrument(DomainModel):
    """Configuración de un índice operable analíticamente."""

    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    display_name: str = Field(min_length=1)
    direction: Direction
    enabled: bool = True
    history_days: dict[str, int] = Field(default_factory=lambda: dict(_DEFAULT_HISTORY_DAYS))
    tick_history_count: int = Field(default=5000, ge=2, le=5000)
    color: str | None = Field(default=None, pattern=_HEX_COLOR_PATTERN)
    notes: str | None = None

    @field_validator("history_days")
    @classmethod
    def _validate_history_days(cls, values: dict[str, int]) -> dict[str, int]:
        missing = sorted(set(TIMEFRAMES) - set(values))
        unknown = sorted(set(values) - set(TIMEFRAMES))
        if missing or unknown:
            raise ValueError(
                f"history_days debe cubrir exactamente {list(TIMEFRAMES)}; "
                f"faltan={missing}, sobran={unknown}"
            )
        if any(value < 1 for value in values.values()):
            raise ValueError("history_days sólo admite enteros >= 1")
        return values

    @model_serializer(mode="wrap")
    def _omit_empty_optional(self, handler):
        """Omite ``color``/``notes`` nulos: el schema no admite ``null``."""
        payload = handler(self)
        for key in ("color", "notes"):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload

    @model_validator(mode="after")
    def _direction_matches_symbol(self) -> Instrument:
        expected = _KNOWN_DIRECTIONS.get(self.symbol)
        if expected is not None and self.direction != expected:
            raise ValueError(f"dirección incoherente para {self.symbol}: se esperaba {expected!r}")
        return self


#: Registro por defecto de la Fase 1 (data-model §6.1).
DEFAULT_INSTRUMENTS: dict[str, Instrument] = {
    "BOOM1000": Instrument(
        symbol="BOOM1000",
        display_name="Boom 1000",
        direction="up",
        history_days=dict(_DEFAULT_HISTORY_DAYS),
    ),
    "CRASH500": Instrument(
        symbol="CRASH500",
        display_name="Crash 500",
        direction="down",
        history_days=dict(_DEFAULT_HISTORY_DAYS),
    ),
}


def get_instrument(symbol: str) -> Instrument:
    """Devuelve el ``Instrument`` por defecto del símbolo.

    Raises:
        KeyError: si el símbolo no pertenece al registro por defecto.
    """
    try:
        return DEFAULT_INSTRUMENTS[symbol]
    except KeyError as exc:
        raise KeyError(
            f"símbolo desconocido: {symbol!r}; disponibles={sorted(DEFAULT_INSTRUMENTS)}"
        ) from exc


class Candle(DomainModel):
    """Barra OHLC normalizada y validada."""

    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    timeframe: Timeframe
    epoch: int = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    source: Source
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ohlc_consistent(self) -> Candle:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(
                f"high={self.high} es menor que max(open, close, low) "
                f"({max(self.open, self.close, self.low)})"
            )
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(
                f"low={self.low} es mayor que min(open, close, high) "
                f"({min(self.open, self.close, self.high)})"
            )
        return self


class Tick(DomainModel):
    """Observación de precio en un instante; sólo existe en memoria."""

    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    epoch: int = Field(gt=0)
    price: float = Field(gt=0)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence: int = Field(ge=0)

    @field_validator("received_at")
    @classmethod
    def _normalise_received_at(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value)
