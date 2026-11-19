"""Configuración de entorno (T001).

``Settings`` lee las variables ``QUANTLAB_*`` del entorno y, si existe, del
archivo ``.env`` (nunca versionado). No escribe nada en disco y no abre
conexiones: los endpoints se validan como URL WebSocket, pero no se usan aquí.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

__all__ = ["DEFAULT_DERIV_ENDPOINTS", "Settings"]

DEFAULT_DERIV_ENDPOINTS: tuple[str, ...] = (
    "wss://api.derivws.com/trading/v1/options/ws/public",
    "wss://ws.binaryws.com/websockets/v3?app_id=1089",
)


class Settings(BaseSettings):
    """Variables de entorno validadas con prefijo ``QUANTLAB_``."""

    model_config = SettingsConfigDict(
        env_prefix="QUANTLAB_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8050, ge=1, le=65_535)
    debug: bool = False
    deriv_endpoints: Annotated[tuple[str, ...], NoDecode] = DEFAULT_DERIV_ENDPOINTS
    rate_limit_per_min: int = Field(default=150, ge=1, le=220)
    max_ticks: int = Field(default=50_000, ge=1_000)
    max_candles_per_tf: int = Field(default=30_000, ge=1_000)
    ram_warning_mb: int = Field(default=380, ge=100)
    ram_critical_mb: int = Field(default=420, ge=200)
    refresh_seconds: int = Field(default=60, ge=1)
    stale_seconds: int = Field(default=900, ge=60)
    seed: int = 20261115
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("deriv_endpoints", mode="before")
    @classmethod
    def _split_endpoints(cls, value: object) -> object:
        """Acepta ``"url1,url2"`` (formato de ``.env``) o una secuencia."""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, Sequence):
            return tuple(str(part).strip() for part in value if str(part).strip())
        return value

    @field_validator("deriv_endpoints")
    @classmethod
    def _validate_endpoints(cls, endpoints: tuple[str, ...]) -> tuple[str, ...]:
        if not endpoints:
            raise ValueError("deriv_endpoints no puede estar vacío")
        for endpoint in endpoints:
            if not endpoint.startswith(("ws://", "wss://")):
                raise ValueError(f"endpoint WebSocket inválido: {endpoint!r}")
        return endpoints

    @model_validator(mode="after")
    def _warning_below_critical(self) -> Settings:
        if self.ram_warning_mb >= self.ram_critical_mb:
            raise ValueError("QUANTLAB_RAM_WARNING_MB debe ser menor que RAM_CRITICAL_MB")
        return self
