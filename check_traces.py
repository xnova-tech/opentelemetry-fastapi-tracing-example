"""Deterministic in-process HTTP and trace relationship observation."""

import asyncio
from dataclasses import dataclass

import httpx
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tracing_demo import GatewaySettings, create_gateway_app, create_tracing, create_worker_app


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """HTTP response trace ID and all spans exported by the scenario."""

    trace_id: int
    spans: tuple[ReadableSpan, ...]

    def trace_spans(self) -> tuple[ReadableSpan, ...]:
        return tuple(span for span in self.spans if span.context and span.context.trace_id == self.trace_id)


async def run_scenario(*, delay_ms: int, propagation_enabled: bool) -> ScenarioResult:
    """Call the two ASGI applications over HTTPX and return exported spans."""
    exporter = InMemorySpanExporter()
    gateway_tracing = create_tracing("gateway", exporter=exporter)
    worker_tracing = create_tracing("worker", exporter=exporter)
    worker_app = create_worker_app(worker_tracing)
    worker_transport = httpx.ASGITransport(app=worker_app)
    gateway_app = create_gateway_app(
        GatewaySettings(
            worker_url="http://worker",
            propagation_enabled=propagation_enabled,
        ),
        gateway_tracing,
        worker_transport=worker_transport,
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_app),
            base_url="http://gateway",
        ) as client:
            response = await client.get("/checkout", params={"delay_ms": delay_ms})
        response.raise_for_status()
        trace_id = int(response.json()["trace_id"], 16)
        return ScenarioResult(trace_id, tuple(exporter.get_finished_spans()))
    finally:
        gateway_tracing.shutdown()
        worker_tracing.shutdown()


def spans_by_name(spans: tuple[ReadableSpan, ...]) -> dict[str, ReadableSpan]:
    """Index a known demo trace by span name."""
    return {span.name: span for span in spans}


def parent_links_are_valid(spans: tuple[ReadableSpan, ...]) -> bool:
    """Check the expected root → client → worker hierarchy."""
    by_name = spans_by_name(spans)
    if set(by_name) != {"gateway.request", "gateway.call-worker", "worker.reserve"}:
        return False
    root = by_name["gateway.request"]
    client = by_name["gateway.call-worker"]
    worker = by_name["worker.reserve"]
    return bool(
        root.context
        and client.context
        and worker.context
        and root.parent is None
        and client.parent
        and client.parent.span_id == root.context.span_id
        and worker.parent
        and worker.parent.span_id == client.context.span_id
    )


async def observe() -> None:
    """Run positive, negative and delayed trace scenarios."""
    propagated = await run_scenario(delay_ms=0, propagation_enabled=True)
    propagated_spans = propagated.trace_spans()
    if not parent_links_are_valid(propagated_spans):
        raise AssertionError("propagated trace hierarchy is invalid")
    print("propagation: spans=3 parent_links_valid=true")

    unlinked = await run_scenario(delay_ms=0, propagation_enabled=False)
    unlinked_names = {span.name for span in unlinked.trace_spans()}
    if unlinked_names != {"gateway.request", "gateway.call-worker"}:
        raise AssertionError(f"unexpected gateway trace spans: {sorted(unlinked_names)}")
    print("no_propagation: gateway_spans=2 worker_unlinked=true")

    delayed = await run_scenario(delay_ms=350, propagation_enabled=True)
    worker_span = spans_by_name(delayed.trace_spans())["worker.reserve"]
    duration_ms = (worker_span.end_time - worker_span.start_time) / 1_000_000
    if duration_ms < 300:
        raise AssertionError(f"worker delay was too short: {duration_ms:.1f} ms")
    print("delay: worker_delay_observed=true")


if __name__ == "__main__":
    asyncio.run(observe())
