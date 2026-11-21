"""Token bucket para respetar el límite de Deriv (T005).

Contrato: ``specs/002-migration-foundation/contracts/service-interfaces.md`` §2.
Con ``capacity=30`` y ``refill_per_second=2.5`` se sostienen 150 req/min con
ráfagas de 30, por debajo del límite de Deriv (220/min) con margen del 30 %.

``try_acquire`` es síncrono por contrato: su sección crítica no contiene
``await``, por lo que es atómica respecto de otras tareas del mismo event loop.
``acquire`` espera fuera del lock y vuelve a comprobar, de modo que nunca se
bloquea el loop ni se concede de más.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

__all__ = ["RateLimitState", "TokenBucket"]

#: Ventana móvil usada para contar llamadas por minuto.
_WINDOW_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class RateLimitState:
    """Instantánea del estado del bucket (contrato §2)."""

    tokens: float
    capacity: int
    refill_per_second: float
    calls_last_minute: int
    waited_ms_last_request: int
    budget_per_minute: int


class TokenBucket:
    """Token bucket thread-safe para asyncio.

    ``capacity=30`` y ``refill_per_second=2.5`` equivalen a 150 req/min
    sostenidas con ráfaga inicial de 30 (INV-M06).
    """

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        if capacity < 1:
            raise ValueError("capacity debe ser >= 1")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second debe ser > 0")

        self._capacity = float(capacity)
        self._refill_per_second = float(refill_per_second)
        self._tokens = float(capacity)  # arranca lleno: permite la ráfaga
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._call_timestamps: deque[float] = deque()
        self._waited_ms_last_request = 0

    # ------------------------------- propiedades -------------------------------

    @property
    def available_tokens(self) -> float:
        """Tokens disponibles ahora (aplica el refill pendiente)."""
        self._refill(time.monotonic())
        return self._tokens

    @property
    def state(self) -> RateLimitState:
        """Instantánea consistente para mostrar en la UI/health."""
        now = time.monotonic()
        self._refill(now)
        self._prune_calls(now)
        return RateLimitState(
            tokens=self._tokens,
            capacity=int(self._capacity),
            refill_per_second=self._refill_per_second,
            calls_last_minute=len(self._call_timestamps),
            waited_ms_last_request=self._waited_ms_last_request,
            budget_per_minute=round(self._refill_per_second * 60),
        )

    # -------------------------------- adquisición ------------------------------

    async def acquire(self, tokens: int = 1) -> None:
        """Bloquea hasta tener ``tokens`` disponibles y los consume."""
        self._validate_tokens(tokens)
        started = time.monotonic()

        while True:
            async with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self._record_call(now)
                    self._waited_ms_last_request = round((now - started) * 1000)
                    return
                deficit = tokens - self._tokens
                delay = deficit / self._refill_per_second

            # Se espera fuera del lock: otros consumidores pueden entrar.
            await asyncio.sleep(delay)

    def try_acquire(self, tokens: int = 1) -> bool:
        """Intenta consumir sin bloquear; ``True`` solo si había tokens."""
        self._validate_tokens(tokens)
        now = time.monotonic()
        self._refill(now)
        if self._tokens < tokens:
            return False

        self._tokens -= tokens
        self._record_call(now)
        self._waited_ms_last_request = 0
        return True

    # --------------------------------- internos --------------------------------

    def _validate_tokens(self, tokens: int) -> None:
        if tokens < 1:
            raise ValueError("tokens debe ser >= 1")
        if tokens > self._capacity:
            raise ValueError(
                f"tokens={tokens} supera la capacidad del bucket ({int(self._capacity)})"
            )

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._last_refill = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)

    def _record_call(self, now: float) -> None:
        self._call_timestamps.append(now)
        self._prune_calls(now)

    def _prune_calls(self, now: float) -> None:
        while self._call_timestamps and now - self._call_timestamps[0] >= _WINDOW_SECONDS:
            self._call_timestamps.popleft()
