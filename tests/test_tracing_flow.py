"""HTTP and OpenTelemetry regression tests for the two-service example."""

import asyncio

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from check_traces import parent_links_are_valid, run_scenario, spans_by_name
from tracing_demo import GatewaySettings, create_tracing, create_worker_app


def test_propagation_creates_one_three_span_trace() -> None:
    result = asyncio.run(run_scenario(delay_ms=0, propagation_enabled=True))

    assert len(result.trace_spans()) == 3
    assert parent_links_are_valid(result.trace_spans())


def test_disabled_propagation_starts_a_separate_worker_trace() -> None:
    result = asyncio.run(run_scenario(delay_ms=0, propagation_enabled=False))

    assert {span.name for span in result.trace_spans()} == {
        "gateway.request",
        "gateway.call-worker",
    }
    worker = spans_by_name(result.spans)["worker.reserve"]
    assert worker.context
    assert worker.context.trace_id != result.trace_id


def test_worker_delay_is_visible_in_its_span() -> None:
    result = asyncio.run(run_scenario(delay_ms=350, propagation_enabled=True))
    worker = spans_by_name(result.trace_spans())["worker.reserve"]

    assert (worker.end_time - worker.start_time) / 1_000_000 >= 300


def test_delay_is_bounded_by_fastapi_validation() -> None:
    asyncio.run(check_invalid_delay())


async def check_invalid_delay() -> None:
    exporter_tracing = create_tracing("worker-test", exporter=_discarding_exporter())
    try:
        app = create_worker_app(exporter_tracing)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://worker",
        ) as client:
            response = await client.get("/reserve", params={"delay_ms": 801})
        assert response.status_code == 422
    finally:
        exporter_tracing.shutdown()


def _discarding_exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.mark.parametrize("raw_value", ["maybe", "", "enabled"])
def test_gateway_settings_reject_invalid_propagation_value(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("PROPAGATION_ENABLED", raw_value)

    with pytest.raises(ValueError, match="PROPAGATION_ENABLED"):
        GatewaySettings.from_env()


def test_gateway_settings_use_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_URL", "http://example-worker:9000")
    monkeypatch.setenv("WORKER_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("PROPAGATION_ENABLED", "false")

    assert GatewaySettings.from_env() == GatewaySettings(
        worker_url="http://example-worker:9000",
        request_timeout_seconds=2.5,
        propagation_enabled=False,
    )
