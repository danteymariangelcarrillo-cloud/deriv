#!/usr/bin/env python
"""Spike de diagnóstico de la conexión Deriv (T020).

Ejecutable standalone::

    uv run python scripts/spike_deriv.py

Comprueba, en orden, y con timestamps UTC:

1. Endpoint activo y latencia del handshake (primera petición real).
2. Símbolos activos y presencia de ``BOOM1000`` / ``CRASH500``.
3. Descarga de 5 velas de 5 min y 5 ticks de muestra.
4. Suscripción de 30 s: ticks recibidos, procesados y eventos detectados.
5. Errores clasificados (transporte, rate limit, timeout, símbolo inactivo,
   API de Deriv o error de datos).

Sale con código 0 si todo funciona y 1 ante cualquier error. No escribe en
disco: sólo imprime por stdout. Requiere red y no forma parte de la suite.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime

from quantlab.config import QuantLabConfig
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_client import DerivClient
from quantlab.data.market_data_service import MarketDataService
from quantlab.data.rate_limiter import TokenBucket
from quantlab.domain.market_data import DEFAULT_INSTRUMENTS
from quantlab.errors import (
    DataError,
    DerivAPIError,
    DerivRateLimitError,
    DerivTimeoutError,
    DerivTransportError,
    QuantLabError,
    SymbolNotActiveError,
)
from quantlab.live.stream_manager import StreamManager
from quantlab.state import InstrumentState

#: Duración de la suscripción en vivo exigida por la tarea.
SUBSCRIPTION_SECONDS = 30
#: Símbolos objetivo del spike.
TARGET_SYMBOLS = ("BOOM1000", "CRASH500")
#: Temporalidad y número de velas de muestra.
CANDLE_TIMEFRAME = "5min"
CANDLE_GRANULARITY_SECONDS = 300
SAMPLE_ROWS = 5


def log(message: str) -> None:
    """Imprime una línea con timestamp UTC legible."""
    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def classify(exc: BaseException) -> str:
    """Etiqueta estable del error para el resumen final."""
    # ``DerivTimeoutError`` hereda de ``DerivTransportError``: comprobar el
    # timeout primero, o la etiqueta "timeout" nunca se emitiría.
    if isinstance(exc, DerivTimeoutError):
        return "timeout"
    if isinstance(exc, DerivRateLimitError):
        return "rate_limit"
    if isinstance(exc, SymbolNotActiveError):
        return "symbol_not_active"
    if isinstance(exc, DerivAPIError):
        return "deriv_api"
    if isinstance(exc, DerivTransportError):
        return "transport"
    if isinstance(exc, DataError):
        return "data"
    if isinstance(exc, QuantLabError):
        return "quantlab"
    return "unexpected"


def build_state(symbol: str, config: QuantLabConfig) -> InstrumentState:
    """``InstrumentState`` con la configuración efectiva por defecto."""
    instrument = DEFAULT_INSTRUMENTS[symbol]
    return InstrumentState(
        symbol=instrument.symbol,
        display_name=instrument.display_name,
        direction=instrument.direction,
        analyzer_config=config.analyzer,
        model_config=config.model,
        gate_config=config.gates,
        ram_config=config.ram,
        config_hash=config.hash(),
    )


def active_codes(symbols: list[dict[str, object]]) -> set[str]:
    """Códigos de los símbolos activos (Deriv usa dos nombres posibles)."""
    codes: set[str] = set()
    for item in symbols:
        code = item.get("underlying_symbol") or item.get("symbol")
        if isinstance(code, str) and code:
            codes.add(code)
    return codes


async def run() -> int:
    config = QuantLabConfig()
    cache = RAMCache(config.ram)
    client = DerivClient()
    market = MarketDataService(
        client,
        cache,
        TokenBucket(capacity=30, refill_per_second=150 / 60.0),
    )

    try:
        # 1. Handshake -------------------------------------------------------
        started = time.perf_counter()
        symbols = await client.get_active_symbols()
        handshake_ms = (time.perf_counter() - started) * 1000.0
        log(f"Endpoint activo: {client.active_endpoint or '(sin conexión)'}")
        log(f"Handshake + active_symbols: {handshake_ms:.1f} ms")
        p50 = client.connection_state.latency_ms_p50
        log(f"Latencia p50 de peticiones: {'n/d' if p50 is None else f'{p50:.3f} ms'}")

        # 2. Símbolos --------------------------------------------------------
        codes = active_codes(symbols)
        matched = [symbol for symbol in TARGET_SYMBOLS if symbol in codes]
        log(f"Símbolos activos devueltos: {len(symbols)}")
        log(f"Objetivo presentes: {matched or 'ninguno'}")
        if not matched:
            raise SymbolNotActiveError(
                "Ni BOOM1000 ni CRASH500 aparecen activos en Deriv.",
                context={"targets": list(TARGET_SYMBOLS), "active": len(symbols)},
            )

        symbol = "BOOM1000" if "BOOM1000" in matched else matched[0]

        # 3. Velas y ticks de muestra ---------------------------------------
        end_epoch = int(datetime.now(UTC).timestamp())
        start_epoch = end_epoch - SAMPLE_ROWS * CANDLE_GRANULARITY_SECONDS
        candles, quality = await market.get_candles(
            symbol, CANDLE_TIMEFRAME, start_epoch, end_epoch
        )
        frame = candles.rename(columns={column: str(column).lower() for column in candles.columns})
        log(
            f"Velas {CANDLE_TIMEFRAME} de {symbol}: {len(frame)} "
            f"(calidad={quality.status}, cobertura={quality.coverage_pct:.1f}%)"
        )
        if frame.empty:
            raise DataError(
                f"Deriv no devolvió velas {CANDLE_TIMEFRAME} para {symbol}.",
                context={"symbol": symbol},
            )
        for row in frame.tail(SAMPLE_ROWS).itertuples(index=False):
            log(f"  vela epoch={int(row.epoch)} close={float(row.close):.4f}")

        ticks = await market.get_tick_history(symbol, count=SAMPLE_ROWS)
        log(f"Ticks de muestra de {symbol}: {len(ticks)}")
        if ticks.empty:
            raise DataError(f"Deriv no devolvió ticks para {symbol}.", context={"symbol": symbol})
        for row in ticks.tail(SAMPLE_ROWS).itertuples(index=False):
            log(f"  tick epoch={int(row.epoch)} price={float(row.price):.4f}")

        # 4. Suscripción -----------------------------------------------------
        state = build_state(symbol, config)
        manager = StreamManager(client, state, cache)
        await manager.start(symbol)
        log(f"Suscrito a {symbol}; capturando {SUBSCRIPTION_SECONDS}s de ticks...")
        try:
            await asyncio.sleep(SUBSCRIPTION_SECONDS)
            status = await manager.get_status(symbol)
        finally:
            await manager.stop(symbol)

        log(
            f"Ticks recibidos={status.received} procesados={status.processed} "
            f"eventos={status.live_events} dropped={status.dropped_ticks} "
            f"cola={status.queue_depth} warmup={'sí' if status.warmup_complete else 'no'} "
            f"endpoint={status.active_endpoint or '(sin conexión)'}"
        )
        if status.connected:
            log(
                f"Latencia p50 final: {status.latency_ms if status.latency_ms is not None else 'n/d'}"
            )
        if status.received == 0:
            raise DataError(
                "La suscripción de 30 s no recibió ningún tick.",
                context={"symbol": symbol},
            )

        log("SPIKE OK")
        return 0
    except Exception as exc:  # noqa: BLE001 - el script debe clasificar cualquier fallo
        log(f"ERROR [{classify(exc)}] {type(exc).__name__}: {exc}")
        detail = getattr(exc, "detail", None)
        if detail:
            log(f"  detalle: {detail}")
        return 1
    finally:
        await client.close()


def main() -> int:
    """Punto de entrada standalone; 0 si todo va bien, 1 si algo falla."""
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        log("Interrumpido por el usuario")
        return 1


if __name__ == "__main__":
    sys.exit(main())
