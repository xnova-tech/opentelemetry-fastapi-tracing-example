"""Verify the running Compose stack through Gateway and Grafana APIs."""

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import httpx

GATEWAY_URL = "http://127.0.0.1:8000"
GRAFANA_URL = "http://127.0.0.1:3000"
GRAFANA_USER = "admin"
GRAFANA_PASSWORD = "grafana-demo"


@dataclass(frozen=True, slots=True)
class TraceSpan:
    """Relevant fields from Tempo's OTLP JSON response."""

    service: str
    name: str
    span_id: str
    parent_span_id: str
    duration_ms: float


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected a JSON array")
    return cast(list[object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a JSON string")
    return value


def fetch_json(client: httpx.Client, url: str) -> dict[str, object]:
    """Fetch one JSON object and require a successful response."""
    response = client.get(url)
    response.raise_for_status()
    return _mapping(response.json())


def wait_for(
    description: str,
    operation: Callable[[], dict[str, object]],
    predicate: Callable[[dict[str, object]], bool],
) -> dict[str, object]:
    """Retry a local observation while containers become ready."""
    last_error: Exception | None = None
    for _ in range(60):
        try:
            payload = operation()
            if predicate(payload):
                return payload
        except (httpx.HTTPError, TypeError, ValueError) as error:
            last_error = error
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {description}") from last_error


def parse_spans(payload: dict[str, object]) -> tuple[TraceSpan, ...]:
    """Read service, hierarchy and duration from Tempo's trace response."""
    parsed: list[TraceSpan] = []
    for batch_value in _list(payload["batches"]):
        batch = _mapping(batch_value)
        resource = _mapping(batch["resource"])
        service = "unknown"
        for attribute_value in _list(resource["attributes"]):
            attribute = _mapping(attribute_value)
            if attribute.get("key") == "service.name":
                service = _string(_mapping(attribute["value"])["stringValue"])
        for scope_value in _list(batch["scopeSpans"]):
            scope = _mapping(scope_value)
            for span_value in _list(scope["spans"]):
                span = _mapping(span_value)
                start = int(_string(span["startTimeUnixNano"]))
                end = int(_string(span["endTimeUnixNano"]))
                parsed.append(
                    TraceSpan(
                        service=service,
                        name=_string(span["name"]),
                        span_id=_string(span["spanId"]),
                        parent_span_id=_string(span.get("parentSpanId", "")),
                        duration_ms=(end - start) / 1_000_000,
                    )
                )
    return tuple(parsed)


def wait_for_trace(
    client: httpx.Client,
    trace_id: str,
    expected_span_count: int,
) -> tuple[TraceSpan, ...]:
    """Query Tempo through Grafana until the trace has the expected spans."""
    url = f"{GRAFANA_URL}/api/datasources/proxy/uid/tempo/api/traces/{trace_id}"
    payload = wait_for(
        f"trace {trace_id}",
        lambda: fetch_json(client, url),
        lambda candidate: len(parse_spans(candidate)) == expected_span_count,
    )
    return parse_spans(payload)


def request_trace(client: httpx.Client, *, delay_ms: int) -> str:
    """Call the gateway and return its validated trace ID."""
    payload = fetch_json(client, f"{GATEWAY_URL}/checkout?delay_ms={delay_ms}")
    trace_id = _string(payload["trace_id"])
    if len(trace_id) != 32:
        raise AssertionError(f"unexpected trace ID: {trace_id}")
    return trace_id


def assert_linked_trace(spans: tuple[TraceSpan, ...]) -> None:
    """Require the expected three-span hierarchy and service names."""
    by_name = {span.name: span for span in spans}
    if set(by_name) != {"gateway.request", "gateway.call-worker", "worker.reserve"}:
        raise AssertionError(f"unexpected spans: {sorted(by_name)}")
    root = by_name["gateway.request"]
    client = by_name["gateway.call-worker"]
    worker = by_name["worker.reserve"]
    if root.service != "gateway" or worker.service != "worker":
        raise AssertionError("unexpected service.name attributes")
    if client.parent_span_id != root.span_id or worker.parent_span_id != client.span_id:
        raise AssertionError("trace parent hierarchy is invalid")


def inspect_linked_stack(
    gateway_client: httpx.Client,
    grafana_client: httpx.Client,
) -> None:
    """Check readiness, normal tracing and an observable worker delay."""
    wait_for(
        "Grafana",
        lambda: fetch_json(grafana_client, f"{GRAFANA_URL}/api/health"),
        lambda payload: payload.get("database") == "ok",
    )
    wait_for(
        "Tempo data source",
        lambda: fetch_json(
            grafana_client,
            f"{GRAFANA_URL}/api/datasources/uid/tempo/health",
        ),
        lambda payload: payload.get("status") == "OK",
    )
    normal_spans = wait_for_trace(
        grafana_client,
        request_trace(gateway_client, delay_ms=0),
        3,
    )
    assert_linked_trace(normal_spans)
    slow_spans = wait_for_trace(
        grafana_client,
        request_trace(gateway_client, delay_ms=350),
        3,
    )
    assert_linked_trace(slow_spans)
    slow_worker = {span.name: span for span in slow_spans}["worker.reserve"]
    if slow_worker.duration_ms < 300:
        raise AssertionError(f"worker delay was too short: {slow_worker.duration_ms:.1f} ms")
    print("stack: gateway=ready grafana_tempo=ready")
    print("normal_trace: spans=3 parent_links_valid=true")
    print("slow_trace: spans=3 worker_delay_observed=true")
    print("grafana_tempo_query: ok")


def inspect_unlinked_stack(
    gateway_client: httpx.Client,
    grafana_client: httpx.Client,
) -> None:
    """Confirm that disabled propagation removes the worker from gateway's trace."""
    trace_id = request_trace(gateway_client, delay_ms=0)
    spans = wait_for_trace(grafana_client, trace_id, 2)
    names = {span.name for span in spans}
    if names != {"gateway.request", "gateway.call-worker"}:
        raise AssertionError(f"unexpected unlinked trace: {sorted(names)}")
    print("no_propagation_trace: gateway_spans=2 worker_unlinked=true")


def main() -> None:
    """Run either the normal E2E contract or its negative control."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-unlinked", action="store_true")
    args = parser.parse_args()
    with (
        httpx.Client(timeout=3.0) as gateway_client,
        httpx.Client(
            timeout=3.0,
            auth=(GRAFANA_USER, GRAFANA_PASSWORD),
        ) as grafana_client,
    ):
        if args.expect_unlinked:
            inspect_unlinked_stack(gateway_client, grafana_client)
        else:
            inspect_linked_stack(gateway_client, grafana_client)


if __name__ == "__main__":
    main()
