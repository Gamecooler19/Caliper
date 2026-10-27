"""FastAPI application skeleton.

Per the brief for this module, the HTTP surface is intentionally minimal:
a health-check endpoint only. Request/response orchestration for the
classification engine, auth, and rate limiting belongs to the
``api-gateway`` module boundary described in architecture.md and is out
of scope until Module 4 ("API orchestration and the demo-facing UI").

This module performs I/O by nature (it's a web server) and must stay
separate from ``app.classification.engine``, which must not.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Caliper",
    description=(
        "Pre-assessment copilot for Indian medical device risk "
        "classification under the Medical Devices Rules, 2017. See "
        "architecture.md at the repository root."
    ),
    version="0.1.0",
)


class HealthResponse(BaseModel):
    """Response body for the health-check endpoint."""

    status: str = "ok"


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness/readiness probe. No dependency checks (DB, Valkey) are
    performed here yet -- this is intentionally the minimal Module 1
    skeleton, not a full readiness probe.
    """

    return HealthResponse(status="ok")
