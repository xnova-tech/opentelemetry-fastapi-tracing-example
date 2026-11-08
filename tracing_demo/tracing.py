"""OpenTelemetry SDK assembly for the demo services."""

from dataclasses import dataclass

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Tracer

from tracing_demo.config import TracingSettings


@dataclass(frozen=True, slots=True)
class Tracing:
    """Tracer and provider with an explicit lifecycle."""

    tracer: Tracer
    provider: TracerProvider

    def shutdown(self) -> None:
        """Flush pending spans and stop exporter workers."""
        self.provider.shutdown()


def create_tracing(
    service_name: str,
    settings: TracingSettings | None = None,
    *,
    exporter: SpanExporter | None = None,
) -> Tracing:
    """Create isolated tracing state for one service.

    Tests may provide an in-memory exporter. Runtime services use the
    configured OTLP/HTTP exporter and a batch processor.
    """
    if not service_name:
        raise ValueError("service_name must not be empty")

    tracing_settings = settings or TracingSettings.from_env()
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is None:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=tracing_settings.endpoint),
                schedule_delay_millis=tracing_settings.batch_schedule_delay_ms,
            )
        )
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return Tracing(
        tracer=provider.get_tracer(service_name),
        provider=provider,
    )
