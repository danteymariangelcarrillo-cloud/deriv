"""Stream de ticks en vivo por símbolo (T019).

Pipeline: suscripción Deriv → ``asyncio.Queue`` → task consumidora →
``_process_live_tick``. El algoritmo de detección de eventos es una migración
**exacta** de ``boom_crash_quant_analyzer.py`` L1666-L1708: el umbral se calcula
con los retornos *previos* (cuantil móvil sin la observación actual) y sólo se
publica un candidato si el retorno direccional supera ese umbral.

Reutiliza las piezas ya cerradas:

- ``DerivClient.subscribe_ticks`` (T007) aporta la reconexión exponencial con
  jitter, el heartbeat de 20 s y el allowlist de mensajes. ``None`` marca una
  reconexión en curso; por eso ``_ws_loop`` no abre su propio WebSocket (sería
  una segunda conexión sin allowlist ni rate limit).
- ``InstrumentState`` (T004) aporta la cola acotada, ``live_ticks``,
  ``dropped_ticks`` y ``directional_returns`` (maxlen de ``RAMConfig``).
- ``RAMCache`` refleja los ticks en vivo y los retornos direccionales para las
  estadísticas de salud.
- ``directional_returns`` de T018 para el recorte direccional del warm-up.

CERO DISCO y sin hilos: todo ocurre en el event loop.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import structlog

from quantlab.analytics.indicators import directional_returns
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_client import DerivClient
from quantlab.state import InstrumentState, TickRecord

__all__ = ["LiveStreamState", "StreamManager", "StreamState"]

#: Mínimo de retornos previos para publicar umbral (legacy L1689).
WARMUP_RETURNS = 300
#: Timeout del ``get`` de la cola: permite comprobar ``stop_event`` sin bloquear.
_QUEUE_POLL_SECONDS = 0.2

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class LiveStreamState:
    """Foto del stream de un símbolo (contracts §7)."""

    symbol: str
    running: bool
    connected: bool
    received: int
    processed: int
    dropped_ticks: int
    queue_depth: int
    last_tick_epoch: int | None
    warmup_complete: bool
    live_events: int
    latency_ms: float | None
    active_endpoint: str | None


@dataclass(slots=True)
class _Runtime:
    """Estado de sesión del stream (se reinicia en cada ``start``)."""

    received: int = 0
    processed: int = 0
    live_events: int = 0
    ticks_since_event: int = 0
    sequence: int = 0
    last_price: float | None = None
    last_epoch: int | None = None
    running: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    consumer_task: asyncio.Task[None] | None = None
    ws_task: asyncio.Task[None] | None = None
    stream: AsyncIterator[dict[str, Any] | None] | None = None


#: Alias retrocompatible (T042): el DTO HTTP se llama también ``StreamState``.
StreamState = LiveStreamState


class StreamManager:
    """Gestiona el stream en vivo de uno o varios símbolos."""

    def __init__(
        self,
        deriv_client: DerivClient,
        instrument_state: InstrumentState | Mapping[str, InstrumentState],
        cache: RAMCache,
        *,
        event_sink: Callable[[str, int, float, float, float], None] | None = None,
    ) -> None:
        self._client = deriv_client
        self._cache = cache
        #: Callback opcional para el push WebSocket de eventos (T033).
        self._event_sink = event_sink
        if isinstance(instrument_state, InstrumentState):
            self._states: dict[str, InstrumentState] = {instrument_state.symbol: instrument_state}
        else:
            self._states = dict(instrument_state)
        self._runtimes: dict[str, _Runtime] = {}

    # -------------------------------- ciclo de vida --------------------------------

    async def start(self, symbol: str) -> None:
        """Inicia (o reinicia) el stream del símbolo.

        Limpia buffers en vivo, hace warm-up de ``directional_returns`` con los
        ticks históricos y lanza la task consumidora y la del WebSocket. Si ya
        está corriendo, no hace nada (idempotente).
        """
        state = self._resolve(symbol)
        runtime = self._runtimes.setdefault(symbol, _Runtime())
        if runtime.running:
            return

        stale = [
            task
            for task in (runtime.consumer_task, runtime.ws_task)
            if task is not None and not task.done()
        ]
        for task in stale:
            task.cancel()
        if stale:
            await asyncio.gather(*stale, return_exceptions=True)

        self._reset_session(state, runtime)
        self._warm_up(state, symbol)

        runtime.running = True
        runtime.stop_event = asyncio.Event()
        runtime.consumer_task = asyncio.create_task(
            self._consumer_loop(symbol), name=f"quantlab-consumer:{symbol}"
        )
        runtime.ws_task = asyncio.create_task(self._ws_loop(symbol), name=f"quantlab-ws:{symbol}")
        state.ws_task = runtime.ws_task
        log.info(
            "stream_started",
            symbol=symbol,
            direction=state.direction,
            warmup_returns=len(state.directional_returns),
        )

    async def stop(self, symbol: str) -> None:
        """Detiene el stream, cancela las tasks, cierra la suscripción y drena."""
        state = self._resolve(symbol)
        runtime = self._runtimes.get(symbol)
        if runtime is None:
            return

        runtime.running = False
        runtime.stop_event.set()
        tasks = [task for task in (runtime.consumer_task, runtime.ws_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        stream = runtime.stream
        runtime.stream = None
        if stream is not None:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except RuntimeError:  # ya cerrado por la cancelación
                    pass

        while True:
            try:
                state.live_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        runtime.consumer_task = None
        runtime.ws_task = None
        state.ws_task = None
        state.connected = False
        state.active_endpoint = ""
        log.info("stream_stopped", symbol=symbol)

    async def get_status(self, symbol: str) -> StreamState:
        """Estado coherente del stream (contadores, cola y warm-up)."""
        state = self._resolve(symbol)
        runtime = self._runtimes.get(symbol, _Runtime())
        connection = getattr(self._client, "connection_state", None)
        latency = getattr(connection, "latency_ms_p50", None)
        return StreamState(
            symbol=symbol,
            running=runtime.running,
            connected=state.connected,
            received=runtime.received,
            processed=runtime.processed,
            dropped_ticks=state.dropped_ticks,
            queue_depth=state.live_queue.qsize(),
            last_tick_epoch=runtime.last_epoch,
            warmup_complete=len(state.directional_returns) >= WARMUP_RETURNS,
            live_events=runtime.live_events,
            latency_ms=latency if state.connected else None,
            active_endpoint=state.active_endpoint or None,
        )

    # --------------------------------- internals ---------------------------------

    def _resolve(self, symbol: str) -> InstrumentState:
        state = self._states.get(symbol)
        if state is None:
            raise KeyError(f"instrumento desconocido: {symbol!r}")
        return state

    @staticmethod
    def _reset_session(state: InstrumentState, runtime: _Runtime) -> None:
        """Limpia el estado en vivo antes de una sesión nueva (legacy L1723-L1730)."""
        state.live_ticks.clear()
        state.live_queue = asyncio.Queue(maxsize=state.ram_config.max_live_queue)
        state.dropped_ticks = 0
        state.directional_returns.clear()
        state.connected = False
        state.active_endpoint = ""
        runtime.received = 0
        runtime.processed = 0
        runtime.live_events = 0
        runtime.ticks_since_event = 0
        runtime.sequence = 0
        runtime.last_price = None
        runtime.last_epoch = None

    def _warm_up(self, state: InstrumentState, symbol: str) -> None:
        """Inicializa ``directional_returns`` con retornos históricos.

        Misma fórmula que el prototipo: ``pct_change()*100`` y recorte
        direccional, sólo con valores finitos y hasta el ``maxlen`` del buffer.
        """
        if len(state.historical_ticks) < 2:
            return
        prices = pd.Series([tick.price for tick in state.historical_ticks], dtype="float64")
        returns = prices.pct_change().mul(100.0).replace([np.inf, -np.inf], np.nan).dropna()
        directional = directional_returns(returns, state.direction)
        limit = state.directional_returns.maxlen
        if limit is not None:
            directional = directional.tail(limit)
        for value in directional.to_numpy(dtype=float):
            if not math.isfinite(value):
                continue
            state.directional_returns.append(float(value))
            self._cache.append_directional_return(symbol, float(value))

    async def _consumer_loop(self, symbol: str) -> None:
        """Consume la cola y aplica ``_process_live_tick`` tick a tick."""
        state = self._resolve(symbol)
        runtime = self._runtimes[symbol]
        while not runtime.stop_event.is_set():
            try:
                record = await asyncio.wait_for(state.live_queue.get(), timeout=_QUEUE_POLL_SECONDS)
            except TimeoutError:
                continue
            try:
                await self._process_live_tick(
                    symbol, {"quote": record.price, "epoch": record.epoch}
                )
            except Exception:
                log.exception("live_tick_failed", symbol=symbol)
            finally:
                state.live_queue.task_done()

    async def _ws_loop(self, symbol: str) -> None:
        """Consume ``DerivClient.subscribe_ticks`` y encola los ticks válidos.

        ``None`` indica reconexión en curso; la reconexión con backoff y el
        heartbeat los gestiona ``DerivClient`` (T007).
        """
        state = self._resolve(symbol)
        runtime = self._runtimes[symbol]
        stream = self._client.subscribe_ticks(symbol)
        runtime.stream = stream
        try:
            async for payload in stream:
                if runtime.stop_event.is_set():
                    break
                if payload is None:
                    state.connected = False
                    continue
                tick = payload.get("tick") if isinstance(payload, dict) else None
                if not isinstance(tick, dict):
                    continue
                if "quote" not in tick or "epoch" not in tick:
                    log.warning("live_tick_incomplete", symbol=symbol)
                    continue
                try:
                    price = float(tick["quote"])
                    epoch = int(tick["epoch"])
                except (TypeError, ValueError):
                    log.warning("live_tick_unparseable", symbol=symbol)
                    continue
                if not math.isfinite(price) or price <= 0:
                    continue

                runtime.received += 1
                runtime.sequence += 1
                state.connected = True
                endpoint = getattr(self._client, "active_endpoint", "") or ""
                if endpoint:
                    state.active_endpoint = endpoint
                self._enqueue(
                    state,
                    TickRecord(
                        epoch=epoch,
                        price=price,
                        received_at=time.monotonic(),
                        sequence=runtime.sequence,
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("ws_loop_failed", symbol=symbol)
            runtime.running = False
        finally:
            state.connected = False
            runtime.stream = None
            # Si el stream terminó solo (fallo o cierre del cliente), el
            # consumidor también debe parar para no quedar sondeando la cola.
            if runtime.running and not runtime.stop_event.is_set():
                runtime.running = False
                runtime.stop_event.set()

    def _enqueue(self, state: InstrumentState, record: TickRecord) -> bool:
        """Encola el tick; ``dropped_ticks`` sólo sube si la cola está llena."""
        accepted = state.append_live_tick(record)
        if accepted:
            self._cache.append_live_tick(state.symbol, record)
        return accepted

    async def _process_live_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        """Migración exacta del algoritmo del prototipo (L1666-L1708)."""
        state = self._resolve(symbol)
        runtime = self._runtimes[symbol]
        price = float(tick["quote"])
        epoch = int(tick["epoch"])
        if not np.isfinite(price) or price <= 0:
            return

        runtime.processed += 1
        is_event = False
        move_pct = float("nan")
        threshold_pct = float("nan")
        directional = 0.0
        if runtime.last_price is not None and runtime.last_price > 0:
            signed_return = (price / runtime.last_price - 1.0) * 100.0
            directional = max(0.0, signed_return if state.direction == "up" else -signed_return)
            move_pct = signed_return
            if len(state.directional_returns) >= WARMUP_RETURNS:
                threshold_pct = float(
                    np.quantile(
                        np.asarray(state.directional_returns),
                        state.analyzer_config.tick_event_quantile,
                    )
                )
                is_event = directional > 0 and directional >= threshold_pct
            state.directional_returns.append(directional)
            self._cache.append_directional_return(symbol, directional)

        if is_event:
            runtime.live_events += 1
            log.info(
                "live_event",
                symbol=symbol,
                price=price,
                return_pct=move_pct,
                directional_move_pct=directional,
                threshold_pct_prior_only=threshold_pct,
                ticks_before_event=runtime.ticks_since_event,
            )
            runtime.ticks_since_event = 0
            if self._event_sink is not None:
                self._event_sink(symbol, epoch, price, move_pct, directional)
        else:
            runtime.ticks_since_event += 1

        runtime.last_price = price
        runtime.last_epoch = epoch
