# Contracts: Service Interfaces

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16

Contratos internos entre el núcleo `quantlab` y el servidor FastAPI. Son
protocolos de Python (typing estructural); no prescriben una implementación
concreta. La UI (frontend JS) solo consume endpoints JSON/WS; nunca llama a Deriv
directamente. **Cero escritura en disco.**

## 1. Tipos comunes

```python
from __future__ import annotations

import pandas as pd

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TypeAlias

Timeframe: TypeAlias = Literal["1min", "5min", "15min", "1h", "4h", "1d"]
Direction: TypeAlias = Literal["up", "down"]
AnalysisStatus: TypeAlias = Literal[
    "pending", "computing", "ready", "insufficient",
    "not_applicable", "error", "stale",
]
Stage: TypeAlias = Literal["in_sample", "oos"]
```

- Todos los `datetime` son timezone-aware y UTC.
- Todos los métodos públicos son thread-safe salvo que se indique lo contrario.
- Ningún método del núcleo escribe en disco.
- El núcleo no importa FastAPI ni Plotly.js.

## 2. Errores

```python
class QuantLabError(Exception):
    """Base de todos los errores controlados."""

class ConfigError(QuantLabError):
    """Configuración inválida; no es retryable."""

class DerivError(QuantLabError):
    """Base de errores de Deriv."""

class DerivTransportError(DerivError):
    """Fallo de red, conexión cerrada o 5xx. Retryable."""

class DerivTimeoutError(DerivTransportError):
    """Sin respuesta dentro del timeout. Retryable."""

class DerivRateLimitError(DerivError):
    """Límite alcanzado. Retryable tras retry_after."""
    retry_after: float

class DerivAPIError(DerivError):
    """Error de API (símbolo inválido, payload inválido). No retryable."""
    code: str
    req_id: int | None

class SymbolNotActiveError(DerivAPIError):
    """El símbolo no está en active_symbols."""

class DataValidationError(QuantLabError):
    """La serie no cumple OHLC, epoch o ventana."""

class RAMBudgetError(QuantLabError):
    """El proceso supera el presupuesto de RAM objetivo."""
```

**Reglas**

- `DerivTransportError` y `DerivTimeoutError` se reintentan con backoff.
- `DerivRateLimitError` se reintenta después de `retry_after`.
- `DerivAPIError` no se reintenta; se traduce a `ErrorRecord` y se muestra.
- Los errores de análisis por muestra insuficiente **no** se lanzan: se
  representan con `status="insufficient"` y `FeatureStatus.reason`.

## 3. Cliente Deriv

```python
@dataclass(frozen=True)
class SubscriptionHandle:
    subscription_id: str
    symbol: str
    started_at: datetime

class DerivClientProtocol(Protocol):
    def request(
        self,
        payload: Mapping[str, Any],
        *,
        expected_types: Sequence[str],
        expected_key: str,
        timeout_s: float = 20.0,
    ) -> dict[str, Any]:
        """Envía una petición y devuelve el mensaje validado."""

    def subscribe_ticks(
        self,
        symbol: str,
        callback: Callable[[Tick], None],
    ) -> SubscriptionHandle:
        """Suscribe ticks y reenvía cada Tick al callback.

        La suscripción se reestablece tras una reconexión.
        """

    def unsubscribe_ticks(self, symbol: str) -> None: ...

    @property
    def state(self) -> ConnectionState:
        """Estado inmutable de la conexión (incluye latencia)."""

    def close(self) -> None:
        """Cierra la conexión (idempotente)."""
```

**Invariantes**

- `request` valida `expected_key` y `msg_type`; si no, lanza `DerivAPIError`.
- El `req_id` lo asigna el cliente; el llamador nunca lo proporciona.
- Una petición pendiente se resuelve como máximo una vez.
- El cliente no conoce pandas ni entidades de dominio; devuelve dicts crudos.

## 4. Rate limiter

```python
class RateLimiterProtocol(Protocol):
    def acquire(self, tokens: int = 1, timeout_s: float | None = None) -> float:
        """Bloquea hasta tener tokens. Devuelve segundos esperados."""

    @property
    def state(self) -> RateLimitState: ...
```

- Capacidad 30, recarga 2,5 tokens/s (150/min).
- `acquire` es thread-safe y FIFO.

## 5. Market data service (memoria)

```python
@dataclass(frozen=True)
class CandleSeries:
    symbol: str
    timeframe: Timeframe
    frame: pd.DataFrame  # columnas: epoch, open, high, low, close
    source: Literal["deriv", "fixture"]
    data_version: str
    start_epoch: int | None
    end_epoch: int | None
    quality: DataQualityReport

@dataclass(frozen=True)
class TickSeries:
    symbol: str
    frame: pd.DataFrame  # columnas: epoch, price, received_at, sequence
    source: Literal["deriv", "fixture"]
    start_epoch: int | None
    end_epoch: int | None
    dropped: int = 0

class MarketDataServiceProtocol(Protocol):
    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: int,
        end: int,
    ) -> CandleSeries:
        """Devuelve velas normalizadas en memoria (de Deriv o fixture).

        - Recorta a [start, end], deduplica, valida OHLC y ordena.
        - Nunca devuelve barras futuras. No escribe en disco.
        """

    def get_ticks(
        self,
        symbol: str,
        count: int = 5000,
        *,
        start: int | None = None,
    ) -> TickSeries:
        """Histórico de ticks en memoria; no se persiste."""

    def quality(self, series: CandleSeries) -> DataQualityReport: ...
```

**Invariantes**

- `frame` usa columnas snake_case en minúsculas y `epoch` como `int64`.
- `data_version` cambia si cambia cualquier barra del rango.

## 6. Analytics engine

```python
@dataclass(frozen=True)
class AnalysisRequest:
    symbol: str
    timeframe: Timeframe
    candles: CandleSeries
    ticks: TickSeries | None
    config: AnalyzerConfig
    stage: Stage = "in_sample"

class AnalyticsEngineProtocol(Protocol):
    def compute(self, request: AnalysisRequest) -> TimeframeAnalysis:
        """Calcula todos los análisis de una temporalidad.

        - Aplica gates y devuelve `insufficient` en vez de lanzar.
        - No muta `request` ni los DataFrames de entrada.
        - Es determinista para la misma entrada y semilla.
        - No importa FastAPI ni Plotly.js.
        """

    def compute_zones(
        self,
        candles: CandleSeries,
        *,
        side: Literal["both", "support", "resistance"] = "both",
        stage: Stage = "in_sample",
    ) -> list[ReactionZone]: ...

    def compute_evt(self, candles: CandleSeries) -> EVTResult: ...
    def compute_volatility(self, candles: CandleSeries) -> GARCHResult: ...
    def compute_regimes(self, candles: CandleSeries) -> RegimeResult: ...
    def compute_survival(self, events: Sequence[EventCandidate]) -> SurvivalResult: ...
    def compute_walkforward(self, candles: CandleSeries) -> WalkForwardResult: ...
```

- `compute` orquesta; los métodos específicos permiten tests y reutilización.
- Los datos de trazas (para Plotly.js) se producen en `charts/`, no aquí.

## 7. Snapshot service (en memoria)

```python
class SnapshotServiceProtocol(Protocol):
    def build(
        self,
        symbol: str,
        timeframes: Iterable[Timeframe] = ("5min", "1h"),
    ) -> InstrumentSnapshot:
        """Construye un snapshot inmutable en RAM."""

    def refresh(self, symbol: str) -> InstrumentSnapshot: ...
    def ensure(self, symbol: str, timeframe: Timeframe) -> TimeframeAnalysis: ...

class SnapshotStoreProtocol(Protocol):
    def get(self, symbol: str) -> InstrumentSnapshot | None: ...
    def latest(self) -> Mapping[str, InstrumentSnapshot]: ...
    def swap(self, snapshot: InstrumentSnapshot) -> None:
        """Reemplaza la referencia de forma atómica (en RAM)."""
    def invalidate(self, symbol: str) -> None: ...
```

**Invariantes**

- `swap` publica un objeto ya construido; si falta validación, no publica.
- Dos `build` concurrentes para el mismo símbolo se coalescen.
- El snapshot vive solo en RAM; no hay archivo ni persistencia.

## 8. Stream manager (deques acotados)

```python
class StreamManagerProtocol(Protocol):
    def start(self, symbol: str) -> None:
        """Inicia la suscripción y el consumidor. Idempotente."""
    def stop(self, symbol: str) -> None:
        """Detiene la suscripción y vacía la cola. Idempotente."""
    @property
    def state(self, symbol: str) -> StreamState: ...
    def recent_ticks(self, symbol: str, limit: int = 5000) -> TickSeries: ...
    def recent_events(self, symbol: str, limit: int = 5000) -> list[EventCandidate]: ...
```

- Los búferes en vivo usan `deque(maxlen=…)`: ticks (50.000), eventos (5.000),
  retornos direccionales (10.000) y cola (100.000).
- Los ticks viven solo en memoria; se descartan al llenar el deque con contador
  de descartes.

## 9. Endpoints del servidor FastAPI

| Recurso | Productor | Consumidor | Sincronización |
|---|---|---|---|
| Conexión WS (Deriv) | Hilo escritor | Hilo lector | Lock + `Future` por `req_id` |
| Snapshots | Worker de análisis | Endpoints FastAPI | `swap` atómico; lectura sin lock |
| Búferes en vivo | Hilo lector WS | Hilo consumidor | `queue.Queue` acotada |
| Push al navegador | Hilo consumidor | Frontend JS | WebSocket `/ws/<symbol>` |

- Ningún endpoint FastAPI espera una petición WS de Deriv directamente; usa el
  snapshot o lanza un job en background.
- El hilo lector nunca ejecuta análisis pesados; solo despacha y encola.
- Un lock nunca se mantiene mientras se serializa a JSON.

## 10. Dobles de prueba

`tests/fixtures/fakes.py` debe proporcionar:

- `FakeDerivClient`: respuestas pregrabadas por `req_id` y `msg_type`, errores
  inyectables, suscripción simulada.
- `InMemoryBuffer`: `deque` acotado para simular el stream sin red.
- `FixedClock`: reloj determinista para tests de staleness y UTC.
- `FakeSnapshotStore`: publica y devuelve snapshots de fixture.
