"""Taxonomía de errores estructurados (T002).

Cada error del sistema lleva un ``ErrorCode`` documentado en
``specs/002-migration-foundation/data-model.md`` (§10), con severidad,
retryable, origen y mensaje en español. ``ErrorRecord`` es la forma
serializable (JSON) que consumen la API, el WebSocket y los logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CODE_META",
    "AnalyticsError",
    "CacheError",
    "ConfigError",
    "DataError",
    "DerivAPIError",
    "DerivError",
    "DerivRateLimitError",
    "DerivTimeoutError",
    "DerivTransportError",
    "ErrorCode",
    "ErrorRecord",
    "ExportError",
    "InsufficientDataError",
    "QuantLabError",
    "Severity",
    "StreamError",
    "SymbolNotActiveError",
    "to_error_record",
]


class Severity(str, Enum):
    """Gravedad de un error o aviso."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCode(str, Enum):
    """Códigos estables de error (contrato de API y logs)."""

    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_HASH_UNCHANGED = "CONFIG_HASH_UNCHANGED"
    DERIV_TRANSPORT = "DERIV_TRANSPORT"
    DERIV_TIMEOUT = "DERIV_TIMEOUT"
    DERIV_RATE_LIMIT = "DERIV_RATE_LIMIT"
    DERIV_API_ERROR = "DERIV_API_ERROR"
    SYMBOL_NOT_ACTIVE = "SYMBOL_NOT_ACTIVE"
    DATA_EMPTY = "DATA_EMPTY"
    DATA_STALE = "DATA_STALE"
    CACHE_EVICTION = "CACHE_EVICTION"
    COMPUTE_INSUFFICIENT = "COMPUTE_INSUFFICIENT"
    COMPUTE_FAILED = "COMPUTE_FAILED"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    STREAM_DROPPED = "STREAM_DROPPED"
    EXPORT_FAILED = "EXPORT_FAILED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    RAM_WARNING = "RAM_WARNING"
    RAM_CRITICAL = "RAM_CRITICAL"


@dataclass(frozen=True)
class ErrorMeta:
    """Metadatos por código: severidad, retryable, origen y mensaje ES."""

    severity: Severity
    retryable: bool
    source: str
    message_es: str


CODE_META: dict[ErrorCode, ErrorMeta] = {
    ErrorCode.CONFIG_INVALID: ErrorMeta(
        Severity.CRITICAL, False, "config", "Configuración inválida: {detail}"
    ),
    ErrorCode.CONFIG_HASH_UNCHANGED: ErrorMeta(
        Severity.INFO, False, "config", "Sin cambios semánticos en configuración"
    ),
    ErrorCode.DERIV_TRANSPORT: ErrorMeta(
        Severity.ERROR, True, "deriv", "No se pudo conectar con Deriv; reintentando"
    ),
    ErrorCode.DERIV_TIMEOUT: ErrorMeta(
        Severity.ERROR, True, "deriv", "Deriv no respondió a tiempo"
    ),
    ErrorCode.DERIV_RATE_LIMIT: ErrorMeta(
        Severity.WARNING, True, "deriv", "Límite de Deriv alcanzado; esperando"
    ),
    ErrorCode.DERIV_API_ERROR: ErrorMeta(
        Severity.ERROR, False, "deriv", "Deriv rechazó la solicitud: {code}"
    ),
    ErrorCode.SYMBOL_NOT_ACTIVE: ErrorMeta(
        Severity.ERROR, False, "deriv", "El símbolo no está activo en tu jurisdicción"
    ),
    ErrorCode.DATA_EMPTY: ErrorMeta(
        Severity.WARNING, False, "data", "No se recibieron velas para esta temporalidad"
    ),
    ErrorCode.DATA_STALE: ErrorMeta(
        Severity.WARNING, False, "data", "Los datos están desactualizados (> {s}s)"
    ),
    ErrorCode.CACHE_EVICTION: ErrorMeta(
        Severity.INFO, False, "cache", "Se liberó espacio de caché (LRU)"
    ),
    ErrorCode.COMPUTE_INSUFFICIENT: ErrorMeta(
        Severity.INFO,
        False,
        "analytics",
        "Datos insuficientes para {analysis}: {min} req, {actual} disp",
    ),
    ErrorCode.COMPUTE_FAILED: ErrorMeta(
        Severity.ERROR, False, "analytics", "El análisis {analysis} falló: {detail}"
    ),
    ErrorCode.SNAPSHOT_STALE: ErrorMeta(
        Severity.WARNING, False, "services", "El snapshot está desactualizado"
    ),
    ErrorCode.STREAM_DROPPED: ErrorMeta(
        Severity.WARNING, False, "live", "Se descartaron {n} ticks por saturación de cola"
    ),
    ErrorCode.EXPORT_FAILED: ErrorMeta(
        Severity.ERROR, False, "services", "No se pudo generar el archivo de exportación"
    ),
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorMeta(
        Severity.WARNING, True, "rate_limiter", "Rate limit propio excedido; esperando token"
    ),
    ErrorCode.RAM_WARNING: ErrorMeta(
        Severity.WARNING, False, "cache", "RAM > {mb} MB; iniciando evicción LRU"
    ),
    ErrorCode.RAM_CRITICAL: ErrorMeta(
        Severity.ERROR, False, "cache", "RAM > {mb} MB; evicción agresiva activa"
    ),
}


class _SafeDict(dict[str, Any]):
    """Deja intactos los placeholders sin valor en vez de lanzar ``KeyError``."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render(template: str, context: dict[str, Any]) -> str:
    return template.format_map(_SafeDict(context))


class ErrorRecord(BaseModel):
    """Forma serializable de un error para API, WebSocket y logs."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    severity: Severity
    message_es: str
    detail: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    retryable: bool
    source: str
    context: dict[str, Any] = Field(default_factory=dict)


class QuantLabError(Exception):
    """Excepción base: código estable, severidad y mensaje en español."""

    default_code: ClassVar[ErrorCode] = ErrorCode.COMPUTE_FAILED

    def __init__(
        self,
        message_es: str | None = None,
        *,
        detail: str | None = None,
        context: dict[str, Any] | None = None,
        retryable: bool | None = None,
        severity: Severity | None = None,
        source: str | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        self.code = code or self.default_code
        meta = CODE_META[self.code]
        self.context: dict[str, Any] = dict(context or {})
        self.detail = detail
        self.severity = severity or meta.severity
        self.retryable = meta.retryable if retryable is None else retryable
        self.source = source or meta.source
        self.message_es = message_es or _render(meta.message_es, self.context)
        super().__init__(self.message_es)

    def to_error_record(self) -> ErrorRecord:
        """Convierte la excepción en ``ErrorRecord`` (timestamp UTC)."""
        return ErrorRecord(
            code=self.code,
            severity=self.severity,
            message_es=self.message_es,
            detail=self.detail,
            retryable=self.retryable,
            source=self.source,
            context=self.context,
        )


class ConfigError(QuantLabError):
    """Configuración inválida; no es retryable."""

    default_code = ErrorCode.CONFIG_INVALID


class DerivError(QuantLabError):
    """Error genérico del cliente Deriv (transporte por defecto)."""

    default_code = ErrorCode.DERIV_TRANSPORT


class DerivTransportError(DerivError):
    """Fallo de red, conexión cerrada o 5xx; retryable."""


class DerivTimeoutError(DerivTransportError):
    """Sin respuesta dentro del timeout; retryable."""

    default_code = ErrorCode.DERIV_TIMEOUT


class DerivRateLimitError(DerivError):
    """Límite de Deriv alcanzado; retryable tras ``retry_after``."""

    default_code = ErrorCode.DERIV_RATE_LIMIT


class DerivAPIError(DerivError):
    """Error de API (símbolo inválido, payload inválido); no retryable."""

    default_code = ErrorCode.DERIV_API_ERROR


class SymbolNotActiveError(DerivAPIError):
    """El símbolo no aparece en ``active_symbols``."""

    default_code = ErrorCode.SYMBOL_NOT_ACTIVE


class DataError(QuantLabError):
    """Problema de datos de mercado (vacío o desactualizado)."""

    default_code = ErrorCode.DATA_EMPTY


class CacheError(QuantLabError):
    """Aviso de caché (evicción LRU)."""

    default_code = ErrorCode.CACHE_EVICTION


class AnalyticsError(QuantLabError):
    """Fallo al calcular un análisis."""

    default_code = ErrorCode.COMPUTE_FAILED


class InsufficientDataError(AnalyticsError):
    """Muestra insuficiente para publicar un análisis (gate)."""

    default_code = ErrorCode.COMPUTE_INSUFFICIENT


class StreamError(QuantLabError):
    """Incidencia del stream en vivo (ticks descartados)."""

    default_code = ErrorCode.STREAM_DROPPED


class ExportError(QuantLabError):
    """No se pudo generar el export en memoria."""

    default_code = ErrorCode.EXPORT_FAILED


def to_error_record(error: QuantLabError) -> ErrorRecord:
    """Helper para logging estructurado: excepción → ``ErrorRecord``."""
    return error.to_error_record()
