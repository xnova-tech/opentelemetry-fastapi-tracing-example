"""FastAPI application factories for the gateway and worker."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Query, Request
from opentelemetry import propagate
from opentelemetry.context import Context

from tracing_demo.config import GatewaySettings
from tracing_demo.tracing import Tracing, create_tracing

MAX_DELAY_MS = 800


def create_gateway_app(
    settings: GatewaySettings | None = None,
    tracing: Tracing | None = None,
    *,
    worker_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create the gateway with injectable transport and tracing state."""
    gateway_settings = settings or GatewaySettings.from_env()
    app_tracing = tracing or create_tracing("gateway")
    owns_tracing = tracing is None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_tracing:
            app_tracing.shutdown()

    application = FastAPI(
        title="OpenTelemetry gateway demo",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/checkout")
    async def checkout(
        delay_ms: int = Query(default=0, ge=0, le=MAX_DELAY_MS),
    ) -> dict[str, str]:
        with app_tracing.tracer.start_as_current_span("gateway.request") as root_span:
            with app_tracing.tracer.start_as_current_span("gateway.call-worker"):
                headers: dict[str, str] = {}
                if gateway_settings.propagation_enabled:
                    propagate.inject(headers)
                async with httpx.AsyncClient(
                    base_url=gateway_settings.worker_url,
                    timeout=gateway_settings.request_timeout_seconds,
                    transport=worker_transport,
                ) as client:
                    response = await client.get(
                        "/reserve",
                        params={"delay_ms": delay_ms},
                        headers=headers,
                    )
                response.raise_for_status()
            trace_id = root_span.get_span_context().trace_id
        return {"status": "ok", "trace_id": f"{trace_id:032x}"}

    return application


def create_worker_app(tracing: Tracing | None = None) -> FastAPI:
    """Create the worker that continues an incoming W3C trace context."""
    app_tracing = tracing or create_tracing("worker")
    owns_tracing = tracing is None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_tracing:
            app_tracing.shutdown()

    application = FastAPI(
        title="OpenTelemetry worker demo",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/reserve")
    def reserve(
        request: Request,
        delay_ms: int = Query(default=0, ge=0, le=MAX_DELAY_MS),
    ) -> dict[str, bool]:
        parent_context = propagate.extract(dict(request.headers), context=Context())
        with app_tracing.tracer.start_as_current_span(
            "worker.reserve",
            context=parent_context,
        ):
            if delay_ms:
                time.sleep(delay_ms / 1000)
        return {"reserved": True}

    return application
