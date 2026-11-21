"""Estado mutable en RAM por instrumento (T004).

Implementa ``InstrumentState`` y sus registros de buffer tal como los define
``specs/002-migration-foundation/data-model.md`` §3.1–3.2. Todo el estado vive
en memoria: no hay caché en disco ni persistencia de ningún tipo.

Los ``maxlen`` de los buffers se toman de ``RAMConfig`` en ``__post_init__``,
de modo que la configuración efectiva (y su ``config_hash``) gobierna el uso de
RAM en vez de duplicar constantes.

``InstrumentSnapshot`` pertenece a ``quantlab.domain.snapshot`` (T012); aquí se
importa solo para tipado (``TYPE_CHECKING``), así que este módulo no depende de
un paquete que todavía no existe.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import pandas as pd

from quantlab.config import (
    TIMEFRAMES,
    AnalyzerConfig,
    GateConfig,
    ModelConfig,
    RAMConfig,
)

if TYPE_CHECKING:  # pragma: no cover - solo tipado, T012 crea el módulo real
    from quantlab.domain.snapshot import InstrumentSnapshot

__all__ = ["CandleRecord", "InstrumentState", "TickRecord"]


@dataclass(slots=True)
class TickRecord:
    """Tick crudo mantenido en RAM (data-model §3.2)."""

    epoch: int
    price: float
    received_at: float  # time.monotonic() en el momento de recepción
    sequence: int  # monotónico por sesión


@dataclass(slots=True)
class CandleRecord:
    """Vela normalizada mantenida en RAM (data-model §3.2)."""

    epoch: int
    open: float
    high: float
    low: float
    close: float
    timeframe: str


@dataclass(slots=True)
class InstrumentState:
    """Estado mutable de un índice sintético (Boom/Crash) en RAM.

    Los buffers circulares evictan por FIFO al llenar y nunca superan el
    ``maxlen`` derivado de ``ram_config``.
    """

    symbol: str
    display_name: str
    direction: Literal["up", "down"]

    # Configuración efectiva y su hash canónico (T001).
    analyzer_config: AnalyzerConfig
    model_config: ModelConfig
    gate_config: GateConfig
    ram_config: RAMConfig
    config_hash: str

    # Buffers circulares (RAM only).
    historical_ticks: deque[TickRecord] = field(default_factory=deque)
    live_ticks: deque[TickRecord] = field(default_factory=deque)
    candles_by_tf: dict[str, deque[CandleRecord]] = field(default_factory=dict)
    directional_returns: deque[float] = field(default_factory=deque)

    # Estado calculado (inmutable, referencia compartida).
    snapshot: InstrumentSnapshot | None = None
    snapshot_version: int = 0

    # Live stream.
    ws_task: asyncio.Task[None] | None = None
    live_queue: asyncio.Queue[TickRecord] = field(init=False)
    dropped_ticks: int = 0
    last_latency_ms: float = 0.0
    connected: bool = False
    active_endpoint: str = ""

    # Concurrencia: serializa los recálculos de snapshot del instrumento.
    recompute_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol no puede estar vacío")
        if self.direction not in ("up", "down"):
            raise ValueError(f"direction inválida: {self.direction!r}")
        if not self.config_hash:
            raise ValueError("config_hash no puede estar vacío")

        max_ticks = self.ram_config.max_ticks_per_symbol
        max_candles = self.ram_config.max_candles_per_tf

        # Se recrean para aplicar el maxlen configurado, no una constante.
        self.historical_ticks = deque(self.historical_ticks, maxlen=max_ticks)
        self.live_ticks = deque(self.live_ticks, maxlen=max_ticks)
        self.directional_returns = deque(
            self.directional_returns, maxlen=self.ram_config.max_directional_returns
        )
        self.candles_by_tf = {
            tf: deque(self.candles_by_tf.get(tf, ()), maxlen=max_candles) for tf in TIMEFRAMES
        }
        self.live_queue = asyncio.Queue(maxsize=self.ram_config.max_live_queue)

    # ----------------------------- live stream -----------------------------

    def append_live_tick(self, tick: TickRecord) -> bool:
        """Encola un tick en vivo y lo registra en ``live_ticks``.

        Devuelve ``False`` y suma a ``dropped_ticks`` únicamente si la cola está
        llena (INV-M05); en ese caso el tick no se registra.
        """
        try:
            self.live_queue.put_nowait(tick)
        except asyncio.QueueFull:
            self.dropped_ticks += 1
            return False
        self.live_ticks.append(tick)
        return True

    # ------------------------------ dataframes -----------------------------

    def get_ticks_df(self) -> pd.DataFrame:
        """Devuelve los ticks históricos + en vivo ordenados por ``epoch``."""
        columns = ["epoch", "price", "received_at", "sequence"]
        records = [
            (tick.epoch, tick.price, tick.received_at, tick.sequence)
            for tick in (*self.historical_ticks, *self.live_ticks)
        ]
        frame = pd.DataFrame.from_records(records, columns=columns)
        if frame.empty:
            return frame
        return frame.sort_values("epoch", kind="stable").reset_index(drop=True)

    def get_candles_df(self, tf: str) -> pd.DataFrame:
        """Devuelve las velas de una temporalidad ordenadas por ``epoch``."""
        candles = self.candles_by_tf.get(tf)
        if candles is None:
            raise ValueError(f"temporalidad no válida: {tf!r}; use {list(TIMEFRAMES)}")

        columns = ["epoch", "open", "high", "low", "close"]
        records = [(c.epoch, c.open, c.high, c.low, c.close) for c in candles]
        frame = pd.DataFrame.from_records(records, columns=columns)
        if frame.empty:
            return frame
        return frame.sort_values("epoch", kind="stable").reset_index(drop=True)
