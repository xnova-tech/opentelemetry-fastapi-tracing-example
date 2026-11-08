"""Uvicorn entry point for the worker service."""

from tracing_demo.apps import create_worker_app

app = create_worker_app()
