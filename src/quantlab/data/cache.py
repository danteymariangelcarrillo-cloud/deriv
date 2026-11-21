"""Caché exclusiva en RAM por instrumento (T008).

Implementa ``RAMCache`` tal como lo define
``specs/002-migration-foundation/contracts/service-interfaces.md §4``:

- Buffers circulares ``deque`` con ``maxlen`` tomado de ``RAMConfig``.
- Evicción FIFO automática al llenar cada buffer.
- Evicción LRU **global** de buffers de velas por ``(símbolo, timeframe)``.
- ``live_queue`` acotada con contador de descartes.
- Monitor en background que cada 30 s lee el RSS y aplica la política.

CERO DISCO: este módulo no importa ``pathlib``/``os`` ni abre archivos.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING

import psutil

from quantlab.config import TIMEFRAMES, RAMConfig
from quantlab.state import CandleRecord, TickRecord

if TYPE_CHECKING:  # pragma: no cover - solo tipado
    from collections.abc import Sequence

__all__ = ["CacheStats", "RAMCache"]

_MB = 1024 * 1024
_MIN_FIGURE_CACHE_SIZE = 5


@dataclass(slots=True)
class CacheStats:
    """Foto del uso de RAM de un símbolo (contracts §4)."""

    symbol: str
    historical_ticks: int
    live_ticks: int
    candles_by_tf: dict[str, int]
    directional_returns: int
    live_queue_depth: int
    dropped_ticks: int
    ram_usage_mb: float


@dataclass(slots=True)
class _SymbolCache:
    """Buffers de un instrumento; todos acotados por ``RAMConfig``."""

    historical_ticks: deque[TickRecord]
    live_ticks: deque[TickRecord]
    candles_by_tf: dict[str, deque[CandleRecord]]
    directional_returns: deque[float]
    live_queue: asyncio.Queue[TickRecord]
    dropped_ticks: int = 0


class RAMCache:
    """Caché en memoria con buffers circulares y LRU global de velas.

    No es thread-safe: está pensada para el event loop (streams) y para
    lecturas desde workers que solo copian listas. No crea archivos ni
    directorios.
    """

    def __init__(self, ram_config: RAMConfig) -> None:
        self._ram_config = ram_config
        self._symbols: dict[str, _SymbolCache] = {}
        # Secuencia monotónica de accesos a buffers de velas: LRU global.
        self._candle_access: dict[tuple[str, str], int] = {}
        self._access_seq = 0
        self._figure_cache_size = ram_config.figure_cache_size
        self._monitor_task: asyncio.Task[None] | None = None

    @property
    def figure_cache_size(self) -> int:
        """Tamaño efectivo de la caché de figuras (se reduce bajo presión)."""
        return self._figure_cache_size

    # ------------------------------- buffers -------------------------------

    def append_ticks(self, symbol: str, ticks: Sequence[TickRecord]) -> int:
        """Añade ticks históricos; retorna cuántos se descartaron por ``maxlen``."""
        cache = self._ensure_symbol(symbol)
        dropped = self._dropped_on_append(cache.historical_ticks, len(ticks))
        cache.historical_ticks.extend(ticks)
        return dropped

    def append_live_tick(self, symbol: str, tick: TickRecord) -> bool:
        """Añade un tick a ``live_ticks``; ``True`` si desplazó uno por ``maxlen``."""
        cache = self._ensure_symbol(symbol)
        dropped = self._is_full(cache.live_ticks)
        cache.live_ticks.append(tick)
        return dropped

    def append_candles(self, symbol: str, timeframe: str, candles: Sequence[CandleRecord]) -> int:
        """Añade velas al buffer de ``timeframe``; retorna n descartadas."""
        self._validate_timeframe(timeframe)
        cache = self._ensure_symbol(symbol)
        buffer = cache.candles_by_tf[timeframe]
        dropped = self._dropped_on_append(buffer, len(candles))
        buffer.extend(candles)
        self._touch_candles(symbol, timeframe)
        return dropped

    def replace_candles(self, symbol: str, timeframe: str, candles: Sequence[CandleRecord]) -> int:
        """Reemplaza el buffer de velas de ``timeframe``; retorna n descartadas.

        Extensión mínima sobre el contrato: ``MarketDataService.warm_cache(
        force=True)`` necesita sustituir el histórico existente sin duplicar
        velas, y la API pública no ofrecía un mutador de reemplazo. El buffer
        se vacía antes de anexar y se marca como accedido (LRU).
        """
        self._validate_timeframe(timeframe)
        cache = self._ensure_symbol(symbol)
        buffer = cache.candles_by_tf[timeframe]
        dropped = self._dropped_on_append(buffer, len(candles))
        buffer.clear()
        buffer.extend(candles)
        self._touch_candles(symbol, timeframe)
        return dropped

    def append_directional_return(self, symbol: str, value: float) -> bool:
        """Añade un retorno direccional; ``True`` si desplazó uno por ``maxlen``.

        Extensión mínima sobre el contrato: ``CacheStats.directional_returns``
        necesita un mutador para ser distinto de cero (T019 lo usará).
        """
        cache = self._ensure_symbol(symbol)
        dropped = self._is_full(cache.directional_returns)
        cache.directional_returns.append(value)
        return dropped

    # -------------------------------- getters ------------------------------

    def get_ticks(self, symbol: str, max_n: int | None = None) -> list[TickRecord]:
        """Ticks históricos en orden de inserción (últimos ``max_n``)."""
        cache = self._symbols.get(symbol)
        if cache is None:
            return []
        return self._tail(cache.historical_ticks, max_n)

    def get_live_ticks(self, symbol: str, max_n: int | None = None) -> list[TickRecord]:
        """Ticks en vivo en orden de inserción (últimos ``max_n``)."""
        cache = self._symbols.get(symbol)
        if cache is None:
            return []
        return self._tail(cache.live_ticks, max_n)

    def get_candles(
        self, symbol: str, timeframe: str, max_n: int | None = None
    ) -> list[CandleRecord]:
        """Velas de ``timeframe`` (últimas ``max_n``); [] si el símbolo no existe."""
        self._validate_timeframe(timeframe)
        cache = self._symbols.get(symbol)
        if cache is None:
            return []
        buffer = cache.candles_by_tf.get(timeframe)
        if buffer is None:
            return []
        if buffer:
            self._touch_candles(symbol, timeframe)
        return self._tail(buffer, max_n)

    # ------------------------------ live queue -----------------------------

    def enqueue_live(self, symbol: str, tick: TickRecord) -> bool:
        """Encola sin bloquear; ``True`` si la cola estaba llena (descartado)."""
        cache = self._ensure_symbol(symbol)
        try:
            cache.live_queue.put_nowait(tick)
        except asyncio.QueueFull:
            cache.dropped_ticks += 1
            return True
        return False

    def dequeue_live(self, symbol: str) -> TickRecord | None:
        """Extrae el siguiente tick; ``None`` si la cola está vacía."""
        cache = self._symbols.get(symbol)
        if cache is None:
            return None
        try:
            return cache.live_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # ----------------------------- evicción RAM ----------------------------

    def check_ram_and_evict(self, process_rss_mb: float) -> int:
        """Aplica la política de evicción y retorna MB liberados (aprox.).

        - ``rss <= ram_warning_mb``: no hace nada.
        - ``rss > ram_warning_mb``: evicta el buffer de velas LRU no vacío.
        - ``rss > ram_critical_mb``: evicta todos los buffers de velas y
          reduce ``figure_cache_size`` a la mitad (mínimo 5).

        El RSS es la foto recibida; no se vuelve a medir dentro del método.
        """
        warning = self._ram_config.ram_warning_mb
        critical = self._ram_config.ram_critical_mb
        if process_rss_mb <= warning:
            return 0
        if process_rss_mb <= critical:
            return self._evict_lru_candles(limit=1)
        freed = self._evict_lru_candles(limit=None)
        self._figure_cache_size = max(_MIN_FIGURE_CACHE_SIZE, self._figure_cache_size // 2)
        return freed

    # -------------------------------- stats --------------------------------

    def get_stats(self, symbol: str) -> CacheStats:
        """Uso estimado de un símbolo (ceros si aún no tiene buffers).

        ``ram_usage_mb`` suma ``sys.getsizeof`` de buffers y registros; no
        incluye el contenido de ``live_queue`` (solo su objeto cola).
        """
        cache = self._symbols.get(symbol)
        if cache is None:
            return CacheStats(
                symbol=symbol,
                historical_ticks=0,
                live_ticks=0,
                candles_by_tf={},
                directional_returns=0,
                live_queue_depth=0,
                dropped_ticks=0,
                ram_usage_mb=0.0,
            )
        return CacheStats(
            symbol=symbol,
            historical_ticks=len(cache.historical_ticks),
            live_ticks=len(cache.live_ticks),
            candles_by_tf={
                timeframe: len(buffer) for timeframe, buffer in cache.candles_by_tf.items()
            },
            directional_returns=len(cache.directional_returns),
            live_queue_depth=cache.live_queue.qsize(),
            dropped_ticks=cache.dropped_ticks,
            ram_usage_mb=round(self._symbol_bytes(cache) / _MB, 3),
        )

    # ------------------------------- monitor -------------------------------

    def start_monitor(self, interval: float = 30.0) -> None:
        """Lanza el monitor de RSS; requiere un event loop en ejecución."""
        if interval <= 0:
            raise ValueError("interval debe ser positivo")
        if self._monitor_task is not None and not self._monitor_task.done():
            raise RuntimeError("el monitor de RAM ya está activo")
        loop = asyncio.get_running_loop()
        self._monitor_task = loop.create_task(self._monitor_loop(interval))

    async def stop_monitor(self) -> None:
        """Detiene el monitor; idempotente."""
        task = self._monitor_task
        self._monitor_task = None
        if task is None:
            return
        if task.done():
            # Propaga un fallo del monitor en vez de dejarlo sin observar.
            with contextlib.suppress(asyncio.CancelledError):
                task.result()
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _monitor_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            self.check_ram_and_evict(self._rss_mb())

    @staticmethod
    def _rss_mb() -> float:
        """RSS del proceso en MB (``psutil``), redondeado a 3 decimales."""
        return round(psutil.Process().memory_info().rss / 1e6, 3)

    # ------------------------------- internos ------------------------------

    def _ensure_symbol(self, symbol: str) -> _SymbolCache:
        if not isinstance(symbol, str):
            raise TypeError("symbol debe ser str")
        if not symbol:
            raise ValueError("symbol no puede estar vacío")
        cache = self._symbols.get(symbol)
        if cache is not None:
            return cache

        max_ticks = self._ram_config.max_ticks_per_symbol
        max_candles = self._ram_config.max_candles_per_tf
        cache = _SymbolCache(
            historical_ticks=deque(maxlen=max_ticks),
            live_ticks=deque(maxlen=max_ticks),
            candles_by_tf={timeframe: deque(maxlen=max_candles) for timeframe in TIMEFRAMES},
            directional_returns=deque(maxlen=self._ram_config.max_directional_returns),
            live_queue=asyncio.Queue(maxsize=self._ram_config.max_live_queue),
        )
        self._symbols[symbol] = cache
        return cache

    @staticmethod
    def _validate_timeframe(timeframe: str) -> None:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"temporalidad no válida: {timeframe!r}; use {list(TIMEFRAMES)}")

    @staticmethod
    def _is_full(buffer: deque[object]) -> bool:
        maxlen = buffer.maxlen
        return maxlen is not None and len(buffer) >= maxlen

    @staticmethod
    def _dropped_on_append(buffer: deque[object], incoming: int) -> int:
        maxlen = buffer.maxlen
        if maxlen is None:
            return 0
        return max(0, len(buffer) + incoming - maxlen)

    @staticmethod
    def _tail(buffer: deque[TickRecord] | deque[CandleRecord], max_n: int | None) -> list:
        if max_n is None:
            return list(buffer)
        if max_n < 0:
            raise ValueError("max_n no puede ser negativo")
        if max_n == 0 or not buffer:
            return []
        if max_n >= len(buffer):
            return list(buffer)
        return list(islice(buffer, len(buffer) - max_n, None))

    def _touch_candles(self, symbol: str, timeframe: str) -> None:
        self._access_seq += 1
        self._candle_access[(symbol, timeframe)] = self._access_seq

    def _evict_lru_candles(self, limit: int | None) -> int:
        candidates = sorted(
            (
                (seq, key)
                for key, seq in self._candle_access.items()
                if key[0] in self._symbols
                and key[1] in self._symbols[key[0]].candles_by_tf
                and self._symbols[key[0]].candles_by_tf[key[1]]
            ),
            key=lambda item: item[0],
        )
        if not candidates:
            return 0

        selected = candidates if limit is None else candidates[:limit]
        freed_bytes = 0
        for _, (symbol, timeframe) in selected:
            # Se vacía, no se elimina la clave: append_candles debe poder
            # volver a llenar el buffer después de una evicción.
            buffer = self._symbols[symbol].candles_by_tf[timeframe]
            # Solo los registros: el deque vacío permanece en la caché.
            freed_bytes += sum(sys.getsizeof(candle) for candle in buffer)
            buffer.clear()
            self._candle_access.pop((symbol, timeframe), None)
        return int(freed_bytes / _MB)

    @staticmethod
    def _symbol_bytes(cache: _SymbolCache) -> int:
        total = (
            sys.getsizeof(cache.historical_ticks)
            + sys.getsizeof(cache.live_ticks)
            + sys.getsizeof(cache.directional_returns)
            + sys.getsizeof(cache.candles_by_tf)
            + sys.getsizeof(cache.live_queue)
        )
        for buffer in (cache.historical_ticks, cache.live_ticks):
            total += sum(sys.getsizeof(record) for record in buffer)
        for buffer in cache.candles_by_tf.values():
            total += sys.getsizeof(buffer) + sum(sys.getsizeof(candle) for candle in buffer)
        total += sum(sys.getsizeof(value) for value in cache.directional_returns)
        return total
