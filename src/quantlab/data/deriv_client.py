"""Cliente asíncrono de solo lectura para la API pública de Deriv (T007).

Una única conexión WebSocket persistente multiplexa solicitudes (por ``req_id``)
y suscripciones de ticks. Cumple ``contracts/service-interfaces.md §1``:

- Rate limiting: token bucket de 150/min (capacidad 30, recarga 2.5/s).
- Fallback de endpoint solo ante errores de transporte (520, 502, 503, 504,
  timeout, connection reset).
- Reconexión exponencial con jitter (1, 2, 4, 8, máx. 15 s ± 0.5 s).

Los payloads se validan contra ``data.deriv_calls.ALLOWED_REQUESTS``: ninguna
otra forma de mensaje puede enviarse. Todo el estado vive en RAM.
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from typing import Any, Final

from websockets.exceptions import ConnectionClosed, WebSocketException

from quantlab.data.deriv_calls import ALLOWED_REQUESTS, build_request, validate_response
from quantlab.data.rate_limiter import TokenBucket
from quantlab.errors import (
    DerivAPIError,
    DerivRateLimitError,
    DerivTimeoutError,
    DerivTransportError,
    ErrorRecord,
    QuantLabError,
    SymbolNotActiveError,
)
from quantlab.settings import DEFAULT_DERIV_ENDPOINTS

__all__ = ["ConnectionState", "DerivClient"]

#: Capacidad del bucket exigida por el contrato (ráfaga inicial de 30).
_BUCKET_CAPACITY: Final[int] = 30
#: TTL de la caché de ``active_symbols``.
_SYMBOLS_TTL_SECONDS: Final[float] = 3600.0
#: Ventana de latencias para el p50.
_LATENCY_WINDOW: Final[int] = 100
#: Códigos de error de Deriv que representan rate limiting.
_RATE_LIMIT_CODES: Final[frozenset[str]] = frozenset(
    {"RateLimit", "RateLimitExceeded", "RateLimitExceededError"}
)


@dataclass(frozen=True, slots=True)
class ConnectionState:
    """Foto inmutable del estado de conexión (``connection_state``)."""

    active_endpoint: str
    connected: bool
    last_message_at: datetime | None
    reconnect_count: int
    last_error: ErrorRecord | None
    subscriptions: list[str]
    latency_ms_p50: float | None


@dataclass(slots=True)
class _Subscription:
    symbol: str
    queue: asyncio.Queue[dict[str, Any] | None]


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and value.startswith("{") and value.endswith("}")


_WS_CONNECT: Any = None  # sustituible en tests; se resuelve en _connect()


class DerivClient:
    """Cliente asíncrono para la API pública de Deriv (solo lectura)."""

    def __init__(
        self,
        endpoints: list[str] | None = None,
        rate_limit_per_min: int = 150,
        timeout: float = 20.0,
        retries_per_endpoint: int = 1,
    ) -> None:
        resolved = list(endpoints) if endpoints is not None else list(DEFAULT_DERIV_ENDPOINTS)
        if not resolved:
            raise ValueError("endpoints no puede estar vacío")
        if any(not url.startswith(("ws://", "wss://")) for url in resolved):
            raise ValueError("cada endpoint debe ser una URL ws:// o wss://")
        if rate_limit_per_min < 1:
            raise ValueError("rate_limit_per_min debe ser >= 1")
        if timeout <= 0:
            raise ValueError("timeout debe ser > 0")
        if retries_per_endpoint < 0:
            raise ValueError("retries_per_endpoint debe ser >= 0")

        self._endpoints: list[str] = resolved
        self._timeout = float(timeout)
        self._retries_per_endpoint = int(retries_per_endpoint)
        self._rate_limiter = TokenBucket(
            capacity=_BUCKET_CAPACITY,
            refill_per_second=rate_limit_per_min / 60.0,
        )

        self._ws: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._subscriptions: dict[str, _Subscription] = {}
        self._req_ids = count(1)
        self._latencies: deque[float] = deque(maxlen=_LATENCY_WINDOW)
        self._rng = random.Random(0)

        self._active_endpoint: str | None = None
        self._last_message_at: datetime | None = None
        self._last_error: ErrorRecord | None = None
        self._reconnect_count = 0
        self._has_connected = False

        self._active_symbols: list[dict[str, Any]] | None = None
        self._active_symbols_at: float | None = None
        self._closed = False

    # ------------------------------------------------------------------ API

    async def request(
        self,
        payload: dict[str, Any],
        expected_types: tuple[str, ...],
        expected_key: str,
    ) -> dict[str, Any]:
        """Solicita datos por WebSocket con ``req_id`` y fallback de endpoint.

        Solo reintenta ante errores de transporte; un error de negocio de Deriv
        se propaga de inmediato.

        Raises:
            DerivAPIError: payload fuera de la allowlist o rechazo de negocio.
            DerivRateLimitError: Deriv respondió con un código de rate limit.
            DerivTimeoutError: no hubo respuesta dentro de ``timeout``.
            DerivTransportError: se agotaron endpoints y reintentos.
        """
        self._assert_allowed_payload(payload)

        failures: list[str] = []
        for endpoint in self._endpoint_order():
            for attempt in range(self._retries_per_endpoint + 1):
                try:
                    return await self._request_once(endpoint, payload, expected_types, expected_key)
                except (DerivTransportError, TimeoutError, OSError) as exc:
                    if not self._is_transport_failure(exc):
                        raise
                    if self._closed:
                        raise DerivTransportError(
                            "El cliente Deriv se cerró durante la solicitud",
                            detail=str(exc),
                        ) from exc
                    failures.append(f"{endpoint} (intento {attempt + 1}): {exc}")
                    self._record_error(exc)
                    await self._drop_connection()
                    if attempt >= self._retries_per_endpoint:
                        break

        raise DerivTransportError(
            detail="No se pudo completar la solicitud con los endpoints probados: "
            + " | ".join(failures[-6:]),
            context={"failures": failures[-6:], "endpoints": list(self._endpoints)},
        )

    async def subscribe_ticks(self, symbol: str) -> AsyncIterator[dict[str, Any] | None]:
        """Genera ticks en vivo; ``None`` marca una reconexión en curso.

        Reconecta internamente con backoff exponencial y vuelve a suscribirse.
        El contrato anota el retorno como ``dict`` pero su propia descripción
        exige ``None`` como marcador de reconexión; se ensancha el tipo.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol no puede estar vacío")
        clean = symbol.strip()

        attempt = 0
        while True:
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            subscription_id: str | None = None
            try:
                subscription_id = await self._subscribe_once(clean, queue)
                attempt = 0
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item
            except DerivTransportError:
                if self._closed:
                    return
            finally:
                if subscription_id is not None:
                    self._subscriptions.pop(subscription_id, None)

            yield None

            await asyncio.sleep(self._backoff_seconds(attempt))
            attempt += 1

    async def get_active_symbols(self) -> list[dict[str, Any]]:
        """Símbolos activos, con caché en RAM de 1 hora."""
        now = time.monotonic()
        if (
            self._active_symbols is not None
            and self._active_symbols_at is not None
            and now - self._active_symbols_at < _SYMBOLS_TTL_SECONDS
        ):
            return list(self._active_symbols)

        response = await self.request(
            build_request("active_symbols"),
            expected_types=("active_symbols",),
            expected_key="active_symbols",
        )
        symbols = response.get("active_symbols")
        if not isinstance(symbols, list):
            raise DerivAPIError(
                "La respuesta de active_symbols no contiene una lista",
                detail="active_symbols debe ser una lista de objetos",
            )

        self._active_symbols = [item for item in symbols if isinstance(item, dict)]
        self._active_symbols_at = time.monotonic()
        return list(self._active_symbols)

    async def resolve_symbol(
        self, target_name: str, override: str | None = None
    ) -> tuple[str, str]:
        """Resuelve ``(código, nombre)`` con la lógica exacta del prototipo.

        Es ``async`` porque la resolución consulta ``active_symbols``; el resto
        del algoritmo (normalización, override y ranking) se conserva 1:1.
        """
        symbols = await self.get_active_symbols()

        if override:
            known_codes = {
                str(item.get("underlying_symbol") or item.get("symbol") or "") for item in symbols
            }
            if override not in known_codes:
                raise SymbolNotActiveError(
                    f"El símbolo configurado {override!r} no aparece activo en la respuesta "
                    "de Deriv.",
                    context={"symbol": override},
                )
            name = next(
                (
                    str(item.get("underlying_symbol_name") or item.get("display_name") or override)
                    for item in symbols
                    if str(item.get("underlying_symbol") or item.get("symbol") or "") == override
                ),
                override,
            )
            return override, name

        target = self._normalise_name(target_name)
        candidates: list[tuple[int, str, str]] = []
        for item in symbols:
            code = str(item.get("underlying_symbol") or item.get("symbol") or "")
            name = str(item.get("underlying_symbol_name") or item.get("display_name") or "")
            norm_name = self._normalise_name(name)
            if not code or not norm_name:
                continue
            if norm_name == target:
                candidates.append((0, code, name))
            elif norm_name.startswith(target):
                candidates.append((1, code, name))
            elif target in norm_name:
                candidates.append((2, code, name))

        if not candidates:
            token = "crash" if "crash" in target else "boom" if "boom" in target else target
            related = [
                str(item.get("underlying_symbol_name") or item.get("display_name") or "")
                for item in symbols
                if token
                in self._normalise_name(
                    item.get("underlying_symbol_name") or item.get("display_name") or ""
                )
            ]
            raise SymbolNotActiveError(
                f"No se encontró {target_name!r} entre los símbolos activos. "
                f"Nombres relacionados devueltos por Deriv: {related[:30]}",
                context={"target": target_name, "related": related[:30]},
            )

        candidates.sort(key=lambda candidate: (candidate[0], len(candidate[2])))
        return candidates[0][1], candidates[0][2]

    @property
    def active_endpoint(self) -> str:
        """Endpoint en uso (vacío antes de la primera conexión)."""
        return self._active_endpoint or ""

    @property
    def connection_state(self) -> ConnectionState:
        """Estado de conexión inmutable para la API y el dashboard."""
        p50: float | None = None
        if self._latencies:
            p50 = round(statistics.median(self._latencies), 3)
        return ConnectionState(
            active_endpoint=self.active_endpoint,
            connected=self._ws is not None,
            last_message_at=self._last_message_at,
            reconnect_count=self._reconnect_count,
            last_error=self._last_error,
            subscriptions=list(
                dict.fromkeys(f"ticks:{sub.symbol}" for sub in self._subscriptions.values())
            ),
            latency_ms_p50=p50,
        )

    async def close(self) -> None:
        """Cierra la conexión y cancela tareas internas; idempotente."""
        self._closed = True
        task = self._reader_task
        self._reader_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except (OSError, ConnectionClosed):
                pass

        self._fail_pending("Cliente cerrado")
        self._notify_subscribers()
        self._subscriptions.clear()

    # ------------------------------------------------------------ internals

    @staticmethod
    def _is_transport_failure(exc: BaseException) -> bool:
        """Detecta fallos de transporte (5xx de handshake, timeout, reset)."""
        if isinstance(
            exc,
            (DerivTransportError, TimeoutError, OSError, ConnectionClosed, WebSocketException),
        ):
            return True
        detail = str(exc).lower()
        markers = (
            "520",
            "502",
            "503",
            "504",
            "connection reset",
            "connection aborted",
            "timed out",
            "remote end closed",
            "websocket is already closed",
            "connection refused",
            "temporary failure in name resolution",
        )
        return any(marker in detail for marker in markers)

    @staticmethod
    def _normalise_name(value: Any) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

    def _endpoint_order(self) -> list[str]:
        endpoints = list(self._endpoints)
        if self._active_endpoint in endpoints:
            start = endpoints.index(self._active_endpoint)
            endpoints = endpoints[start:] + endpoints[:start]
        return endpoints

    def _assert_allowed_payload(self, payload: dict[str, Any]) -> None:
        """Exige que el payload sea una de las plantillas de la allowlist."""
        if not isinstance(payload, dict) or not payload:
            raise DerivAPIError(
                "El payload debe ser un dict no vacío",
                detail="Payload inválido para la API de Deriv",
            )
        for template in ALLOWED_REQUESTS.values():
            if set(payload) != set(template):
                continue
            if all(
                _is_placeholder(value) or payload[key] == value for key, value in template.items()
            ):
                return
        raise DerivAPIError(
            "Payload fuera de ALLOWED_REQUESTS",
            detail="Solo se permiten payloads construidos desde ALLOWED_REQUESTS",
            context={"keys": sorted(payload)},
        )

    def _record_error(self, exc: BaseException) -> None:
        if isinstance(exc, QuantLabError):
            self._last_error = exc.to_error_record()
        else:
            self._last_error = DerivTransportError(
                detail=f"{type(exc).__name__}: {exc}"
            ).to_error_record()

    def _backoff_seconds(self, attempt: int) -> float:
        """Backoff 1, 2, 4, 8, máx. 15 s, con jitter determinista de ±0.5 s."""
        base = min(15.0, 2.0 ** min(attempt, 5))
        return max(0.0, base + self._rng.uniform(-0.5, 0.5))

    async def _request_once(
        self,
        endpoint: str,
        payload: dict[str, Any],
        expected_types: tuple[str, ...],
        expected_key: str,
    ) -> dict[str, Any]:
        await self._connect(endpoint)
        ws = self._ws
        if ws is None:  # pragma: no cover - defensa ante carreras de cierre
            raise DerivTransportError(detail="Sin conexión WebSocket activa")

        await self._rate_limiter.acquire()

        request_id = str(next(self._req_ids))
        body = {**payload, "req_id": request_id}
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        started = time.perf_counter()

        try:
            await ws.send(json.dumps(body))
            response = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError as exc:
            raise DerivTimeoutError(
                detail=f"Sin respuesta para req_id={request_id} en {self._timeout}s",
                context={"endpoint": endpoint, "req_id": request_id},
            ) from exc
        except ConnectionClosed as exc:
            raise DerivTransportError(
                detail=f"Conexión cerrada durante la solicitud: {exc}",
                context={"endpoint": endpoint},
            ) from exc
        except OSError as exc:
            raise DerivTransportError(
                detail=f"Fallo de transporte WebSocket: {exc}",
                context={"endpoint": endpoint},
            ) from exc
        finally:
            self._pending.pop(request_id, None)

        self._raise_for_error(response, endpoint)

        if not validate_response(response):
            raise DerivAPIError(
                "Tipo de respuesta no permitido",
                detail=f"msg_type de respuesta no permitido: {response.get('msg_type')!r}",
                context={"endpoint": endpoint, "req_id": request_id},
            )

        if response.get("msg_type") in expected_types or expected_key in response:
            self._latencies.append((time.perf_counter() - started) * 1000.0)
            return response

        raise DerivAPIError(
            "Respuesta inesperada de Deriv",
            detail="La respuesta no coincide con expected_types ni expected_key",
            context={
                "endpoint": endpoint,
                "req_id": request_id,
                "expected_types": list(expected_types),
                "expected_key": expected_key,
                "msg_type": response.get("msg_type"),
            },
        )

    async def _subscribe_once(
        self,
        symbol: str,
        queue: asyncio.Queue[dict[str, Any] | None],
    ) -> str:
        response = await self.request(
            build_request("subscribe_ticks", ticks=symbol),
            expected_types=("ticks",),
            expected_key="subscription",
        )
        subscription = response.get("subscription")
        sub_id = (
            str(subscription.get("id"))
            if isinstance(subscription, dict) and subscription.get("id") is not None
            else None
        )
        if sub_id is None:
            raise DerivAPIError(
                "La suscripción de ticks no devolvió subscription.id",
                detail="Respuesta de suscripción sin identificador",
                context={"symbol": symbol},
            )
        self._subscriptions[sub_id] = _Subscription(symbol=symbol, queue=queue)
        return sub_id

    @staticmethod
    def _raise_for_error(response: dict[str, Any], endpoint: str) -> None:
        error = response.get("error")
        if not error:
            return
        if isinstance(error, dict):
            code = str(error.get("code") or "Error")
            message = str(error.get("message") or error)
        else:
            code = "Error"
            message = str(error)
        context = {"code": code, "endpoint": endpoint}
        if code in _RATE_LIMIT_CODES:
            raise DerivRateLimitError(detail=f"{code}: {message}", context=context)
        raise DerivAPIError(detail=f"{code}: {message}", context=context)

    async def _connect(self, endpoint: str) -> None:
        if self._closed:
            raise DerivTransportError(
                "El cliente Deriv está cerrado",
                detail="No se abre una conexión nueva después de close()",
            )
        if self._ws is not None:
            return
        async with self._connect_lock:
            if self._ws is not None:
                return
            try:
                ws = await _connector()(
                    endpoint,
                    open_timeout=self._timeout,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**23,
                )
            except (OSError, TimeoutError, WebSocketException) as exc:
                self._record_error(exc)
                raise DerivTransportError(
                    detail=f"No se pudo conectar a {endpoint}: {exc}",
                    context={"endpoint": endpoint},
                ) from exc

            self._ws = ws
            self._active_endpoint = endpoint
            if self._has_connected:
                self._reconnect_count += 1
            else:
                self._has_connected = True
            self._reader_task = asyncio.create_task(self._reader_loop(ws))

    async def _drop_connection(self) -> None:
        task = self._reader_task
        self._reader_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except (OSError, ConnectionClosed):
                pass

        self._fail_pending("Conexión WebSocket interrumpida")
        self._notify_subscribers()

    async def _reader_loop(self, ws: Any) -> None:
        failure: BaseException | None = None
        try:
            async for raw in ws:
                if not isinstance(raw, (str, bytes, bytearray)):
                    continue
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                self._last_message_at = datetime.now(UTC)
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except (OSError, ConnectionClosed, ValueError) as exc:
            failure = exc

        if self._ws is not ws:
            return  # otra conexión (o un cierre explícito) ya tomó el control
        self._ws = None
        self._reader_task = None
        failure = failure or DerivTransportError(detail="Fin del stream WebSocket")
        self._record_error(failure)
        self._fail_pending(f"Lector WebSocket detenido: {failure}")
        self._notify_subscribers()

    def _dispatch(self, message: dict[str, Any]) -> None:
        req_id = message.get("req_id")
        if req_id is not None:
            future = self._pending.get(str(req_id))
            if future is not None and not future.done():
                future.set_result(message)
                return

        if message.get("msg_type") != "ticks":
            return
        subscription = message.get("subscription")
        if not isinstance(subscription, dict) or subscription.get("id") is None:
            return
        entry = self._subscriptions.get(str(subscription["id"]))
        if entry is not None:
            entry.queue.put_nowait(message)

    def _fail_pending(self, reason: str) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(DerivTransportError(detail=reason))

    def _notify_subscribers(self) -> None:
        for entry in list(self._subscriptions.values()):
            entry.queue.put_nowait(None)


def _connector() -> Any:
    """Devuelve el conector WebSocket (indirección parcheable en tests)."""
    if _WS_CONNECT is not None:
        return _WS_CONNECT
    from websockets.asyncio.client import connect

    return connect
