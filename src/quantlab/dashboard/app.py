"""Aplicación FastAPI del dashboard (T032).

La composición de dependencias vive en :class:`AppServices`. ``create_app``
acepta servicios ya construidos para que los tests usen dobles sin red; si no se
inyectan, el ``lifespan`` los crea al arrancar (DerivClient perezoso, sin abrir
conexión hasta la primera petición).

Nada se escribe en disco: logs JSON a consola, caché y snapshots en RAM.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quantlab.analytics.engine import AnalyticsEngine
from quantlab.config import QuantLabConfig, RAMConfig
from quantlab.dashboard import routes
from quantlab.dashboard.ws import (
    ConnectionHub,
    config_message,
    event_message,
)
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_client import DerivClient
from quantlab.data.market_data_service import MarketDataService
from quantlab.data.rate_limiter import TokenBucket
from quantlab.domain.market_data import DEFAULT_INSTRUMENTS
from quantlab.errors import QuantLabError
from quantlab.live.stream_manager import StreamManager
from quantlab.services.export_service import ExportService
from quantlab.services.feature_registry import FeatureRegistry
from quantlab.services.snapshot_service import ConfigUpdatedEvent, SnapshotService
from quantlab.services.snapshot_store import SnapshotStore
from quantlab.settings import Settings
from quantlab.state import InstrumentState

__all__ = ["AppServices", "app", "build_services", "create_app", "main"]

log = structlog.get_logger(__name__)

DEFAULT_SYMBOLS: tuple[str, ...] = tuple(DEFAULT_INSTRUMENTS)


@dataclass
class AppServices:
    """Dependencias compartidas por la API, el WebSocket y el orquestador."""

    settings: Settings
    config: QuantLabConfig
    states: dict[str, InstrumentState]
    store: SnapshotStore
    registry: FeatureRegistry
    cache: RAMCache
    client: DerivClient
    market: MarketDataService
    stream: StreamManager
    service: SnapshotService
    export: ExportService
    hub: ConnectionHub
    started_at: float = field(default_factory=time.perf_counter)

    @property
    def uptime_seconds(self) -> float:
        return max(0.0, time.perf_counter() - self.started_at)

    def config_for(self, symbol: str) -> QuantLabConfig:
        state = self.states.get(symbol)
        if state is None:
            raise KeyError(symbol)
        return QuantLabConfig(
            version=self.config.version,
            analyzer=state.analyzer_config,
            model=state.model_config,
            gates=state.gate_config,
            ram=state.ram_config,
        )

    async def shutdown(self) -> None:
        for symbol in self.states:
            await self.stream.stop(symbol)
        await self.client.close()
        await self.hub.close_all()


def build_services(
    settings: Settings | None = None,
    *,
    config: QuantLabConfig | None = None,
    market: MarketDataService | None = None,
    client: DerivClient | None = None,
) -> AppServices:
    """Compone la aplicación completa sin abrir conexiones de red."""
    settings = settings or Settings()
    config = config or QuantLabConfig(
        ram=RAMConfig(
            max_ticks_per_symbol=settings.max_ticks,
            max_candles_per_tf=settings.max_candles_per_tf,
            ram_warning_mb=settings.ram_warning_mb,
            ram_critical_mb=settings.ram_critical_mb,
        )
    )
    client = client or DerivClient(
        endpoints=list(settings.deriv_endpoints),
        rate_limit_per_min=settings.rate_limit_per_min,
    )
    cache = RAMCache(config.ram)
    market = market or MarketDataService(
        client,
        cache,
        TokenBucket(
            capacity=30,
            refill_per_second=settings.rate_limit_per_min / 60.0,
        ),
    )
    states = {
        symbol: InstrumentState(
            symbol=symbol,
            display_name=instrument.display_name,
            direction=instrument.direction,
            analyzer_config=config.analyzer,
            model_config=config.model,
            gate_config=config.gates,
            ram_config=config.ram,
            config_hash=config.hash(),
        )
        for symbol, instrument in DEFAULT_INSTRUMENTS.items()
    }
    registry = FeatureRegistry(config.gates)
    store = SnapshotStore()
    hub = ConnectionHub()

    def _on_config_updated(event: ConfigUpdatedEvent) -> None:
        hub.publish(
            event.symbol,
            "config",
            config_message(event.config_hash, list(event.recalculated)),
        )

    stream = StreamManager(
        client,
        states,
        cache,
        event_sink=lambda symbol, epoch, price, move_pct, _directional: hub.publish(
            symbol, "events", event_message(epoch, price, move_pct, "tick")
        ),
    )
    service = SnapshotService(
        store,
        registry,
        market,
        AnalyticsEngine(config.gates),
        cache,
        states=states,
        event_sink=_on_config_updated,
    )
    return AppServices(
        settings=settings,
        config=config,
        states=states,
        store=store,
        registry=registry,
        cache=cache,
        client=client,
        market=market,
        stream=stream,
        service=service,
        export=ExportService(store),
        hub=hub,
    )


def _configure_logging(settings: Settings) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
    )


def create_app(
    services: AppServices | None = None,
    *,
    settings: Settings | None = None,
) -> FastAPI:
    """Crea la app; ``services`` permite inyectar dobles en tests."""
    settings = settings or (services.settings if services is not None else Settings())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime = services or build_services(settings)
        application.state.services = runtime
        _configure_logging(runtime.settings)
        log.info(
            "dashboard_started",
            host=runtime.settings.host,
            port=runtime.settings.port,
            instruments=list(runtime.states),
        )
        try:
            yield
        finally:
            await runtime.shutdown()
            log.info("dashboard_stopped")

    app = FastAPI(
        title="QuantLab Dashboard API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8050", "http://localhost:8050"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _observability(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        response.headers["x-request-id"] = request_id
        response.headers["x-latency-ms"] = str(latency_ms)
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )
        return response

    @app.exception_handler(QuantLabError)
    async def _quantlab_error(_request: Request, exc: QuantLabError) -> JSONResponse:
        log.warning("quantlab_error", code=exc.code.value, message=str(exc))
        return JSONResponse(
            status_code=503,
            content={
                "detail": str(exc),
                "code": exc.code.value,
                "retryable": exc.retryable,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error=type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(routes.api_router, prefix="/api")
    app.include_router(routes.ws_router)
    return app


app = create_app()


def main() -> None:
    """Arranca uvicorn con la configuración de entorno."""
    settings = Settings()
    _configure_logging(settings)
    app = create_app(settings=settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
