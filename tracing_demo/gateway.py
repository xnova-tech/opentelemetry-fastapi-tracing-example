"""Uvicorn entry point for the gateway service."""

from tracing_demo.apps import create_gateway_app

app = create_gateway_app()
