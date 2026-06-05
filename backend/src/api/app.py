"""FastAPI application assembly."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.assistant_transport.body_limit import AssistantBodySizeLimitMiddleware
from api.assistant_transport.routes import router as assistant_router
from api.budget.middleware import BudgetMiddleware
from api.budget.store import init_budgets
from api.observability import init_observability
from api.routes import citations, health, pricing
from app_config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="cloud-finops-decision-agent", version="0.0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    # BudgetMiddleware after CORS so preflight passes; budget checks fire on the
    # real /assistant request only. Inits below open the SQLite store and install
    # the OTel processor; both are idempotent under uvicorn --reload.
    app.add_middleware(BudgetMiddleware)
    app.add_middleware(AssistantBodySizeLimitMiddleware)
    init_observability(app)
    init_budgets()
    app.include_router(health.router)
    app.include_router(pricing.router)
    app.include_router(citations.router)
    app.include_router(assistant_router)
    return app
