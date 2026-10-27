"""Response schemas for the Caliper API.

Pydantic models only -- no I/O, just the shape of
``POST /api/v1/classify``'s success response.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.classification.models import ClassificationResult
from app.crosscheck.consistency import ConsistencyFlag


class ClassifyResponse(BaseModel):
    """Success response body for ``POST /api/v1/classify``.

    Exactly the three top-level keys the Module 4 brief specifies:
    ``classification``, ``consistency_flags``, and ``report``.
    """

    classification: ClassificationResult
    consistency_flags: list[ConsistencyFlag]
    report: str
