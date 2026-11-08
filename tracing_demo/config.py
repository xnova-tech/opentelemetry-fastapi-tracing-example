"""Runtime settings for the gateway and trace exporter."""

import os
from dataclasses import dataclass


def _parse_bool(raw_value: str, *, variable: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{variable} must be true or false")


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """Settings used when the gateway calls the worker."""

    worker_url: str = "http://worker:8000"
    request_timeout_seconds: float = 5.0
    propagation_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.worker_url.startswith(("http://", "https://")):
            raise ValueError("worker_url must be an HTTP URL")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        """Read gateway settings from environment variables."""
        timeout = float(os.getenv("WORKER_TIMEOUT_SECONDS", "5"))
        propagation_enabled = _parse_bool(
            os.getenv("PROPAGATION_ENABLED", "true"),
            variable="PROPAGATION_ENABLED",
        )
        return cls(
            worker_url=os.getenv("WORKER_URL", "http://worker:8000"),
            request_timeout_seconds=timeout,
            propagation_enabled=propagation_enabled,
        )


@dataclass(frozen=True, slots=True)
class TracingSettings:
    """OTLP exporter settings shared by both services."""

    endpoint: str = "http://collector:4318/v1/traces"
    batch_schedule_delay_ms: int = 200

    def __post_init__(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("endpoint must be an HTTP URL")
        if self.batch_schedule_delay_ms <= 0:
            raise ValueError("batch_schedule_delay_ms must be positive")

    @classmethod
    def from_env(cls) -> "TracingSettings":
        """Read exporter settings from standard OpenTelemetry variables."""
        return cls(
            endpoint=os.getenv(
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                "http://collector:4318/v1/traces",
            ),
            batch_schedule_delay_ms=int(os.getenv("OTEL_BSP_SCHEDULE_DELAY", "200")),
        )
