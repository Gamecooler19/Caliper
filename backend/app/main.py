"""FastAPI application: health check (Module 1) plus the Module 4 API
orchestration layer mounted alongside it.

Startup wires up the two I/O-performing dependencies the API route
needs -- an async SQLAlchemy session factory (for audit logging) and a
Valkey client (for rate limiting) -- and stores them on
``app.state`` so ``backend/app/api/routes.py`` can reach them without
constructing its own engine or client per request. Shutdown disposes
both cleanly.

Known gap, stated explicitly rather than silently built around: this
module does not persist full ``ClassificationResult`` objects to the
database. ``AuditLogEntry.classification_result_id`` is always written
as ``None`` here -- see ``backend/app/api/routes.py`` and
reports/module-4-report.md.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from app.admin.routes import router as admin_router
from app.api.errors import register_exception_handlers
from app.api.routes import router as api_v1_router
from app.auth.errors import register_auth_exception_handlers
from app.auth.routes import router as auth_router
from app.db import build_engine, build_session_factory
from app.valkey_client import build_valkey_client


@asynccontextmanager
async def _lifespan(app: FastAPI):
    engine = build_engine()
    app.state.db_engine = engine
    app.state.db_session_factory = build_session_factory(engine)
    app.state.valkey_client = build_valkey_client()

    try:
        yield
    finally:
        await app.state.valkey_client.aclose()
        await engine.dispose()


app = FastAPI(
    title="Caliper",
    description=(
        "Pre-assessment copilot for Indian medical device risk "
        "classification under the Medical Devices Rules, 2017. See "
        "architecture.md at the repository root."
    ),
    version="0.1.0",
    lifespan=_lifespan,
)

register_exception_handlers(app)
register_auth_exception_handlers(app)
app.include_router(api_v1_router)
app.include_router(auth_router)
app.include_router(admin_router)


class HealthResponse(BaseModel):
    """Response body for the health-check endpoint."""

    status: str = "ok"


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness/readiness probe. No dependency checks (DB, Valkey) are
    performed here -- this stays the minimal Module 1 skeleton, not a
    full readiness probe, even with the API routes now mounted.
    """

    return HealthResponse(status="ok")
