"""Precarga opcional de la caché RAM desde Deriv (T036).

Uso::

    uv run python scripts/warm_cache.py --symbols BOOM1000 CRASH500 --timeframes 5min 1h
    uv run python scripts/warm_cache.py --symbols BOOM1000 --force

No escribe en disco: ``MarketDataService.warm_cache`` llena ``RAMCache`` y el
proceso termina, así que sólo tiene sentido como comprobación manual del cliente
Deriv o para calentar un proceso que se mantenga vivo.
"""

from __future__ import annotations

import argparse
import asyncio

import structlog

from quantlab.config import TIMEFRAMES, RAMConfig
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_client import DerivClient
from quantlab.data.market_data_service import MarketDataService
from quantlab.data.rate_limiter import TokenBucket
from quantlab.domain.market_data import DEFAULT_INSTRUMENTS
from quantlab.settings import Settings

log = structlog.get_logger("quantlab.warm_cache")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Precarga la caché RAM desde Deriv.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_INSTRUMENTS),
        choices=sorted(DEFAULT_INSTRUMENTS),
    )
    parser.add_argument("--timeframes", nargs="+", default=list(TIMEFRAMES), choices=TIMEFRAMES)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora la caché en memoria y vuelve a descargar",
    )
    return parser


async def warm(symbols: list[str], timeframes: list[str], force: bool) -> dict[str, dict[str, int]]:
    settings = Settings()
    client = DerivClient(
        endpoints=list(settings.deriv_endpoints),
        rate_limit_per_min=settings.rate_limit_per_min,
    )
    cache = RAMCache(
        RAMConfig(
            max_ticks_per_symbol=settings.max_ticks,
            max_candles_per_tf=settings.max_candles_per_tf,
            ram_warning_mb=settings.ram_warning_mb,
            ram_critical_mb=settings.ram_critical_mb,
        )
    )
    market = MarketDataService(
        client,
        cache,
        TokenBucket(capacity=30, refill_per_second=settings.rate_limit_per_min / 60.0),
    )
    result: dict[str, dict[str, int]] = {}
    try:
        for symbol in symbols:
            log.info("warm_cache_start", symbol=symbol, timeframes=timeframes, force=force)
            result[symbol] = await market.warm_cache(symbol, timeframes, force=force)
            log.info("warm_cache_done", symbol=symbol, candles=result[symbol])
    finally:
        await client.close()
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    asyncio.run(warm(args.symbols, args.timeframes, args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
