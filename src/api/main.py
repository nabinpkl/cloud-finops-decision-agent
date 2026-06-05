"""ASGI entry point for the FastAPI backend."""

from __future__ import annotations

from api.app import create_app

app = create_app()
