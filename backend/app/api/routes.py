"""POST /api/v1/classify: the one HTTP route this module owns.

Orchestrates Modules 1-3 for one request:

1. Validate the multipart request (image + description present).
2. ``upload_media()`` to store the image.
3. ``extract_vision_flags()`` and ``parse_intended_use()`` concurrently
   via ``asyncio.gather`` -- neither depends on the other's output.
4. ``check_consistency()`` on the vision/profile pair.
5. ``classify()`` using the parsed profile.
6. ``generate_report()`` using the classification result.
7. Write an ``AuditLogEntry`` (``classification_result_id`` left
   ``None`` -- see module docstring in ``backend/app/main.py`` and
   reports/module-4-report.md for why that's an explicit known gap,
   not silently-built partial persistence).

Every failure case is mapped by raising the exception type
``backend/app/api/errors.py`` has a registered handler for; this route
has no try/except of its own for those cases. The one case this route
does check directly is the "missing or empty image/description field"
400, since that's a request-shape validation this route owns, not a
failure any lower-level module reports.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from app.api.auth import require_api_key
from app.api.errors import InvalidRequestError, RateLimitExceededError
from app.api.rate_limit import check_rate_limit
from app.api.schemas import ClassifyResponse
from app.classification.engine import classify
from app.crosscheck.consistency import check_consistency
from app.intake.parser import parse_intended_use
from app.report.generator import generate_report
from app.security.audit import build_audit_entry, record_audit_entry
from app.storage.media import upload_media
from app.vision.pipeline import extract_vision_flags

router = APIRouter(prefix="/api/v1")

_DEFAULT_IMAGE_CONTENT_TYPE = "image/png"


async def _read_and_validate_request(
    image: UploadFile | None, description: str | None
) -> tuple[bytes, str, str]:
    """Validate the two required multipart fields and return the image
    bytes, its content type, and the description text.

    Raises :class:`InvalidRequestError` (mapped to HTTP 400) if either
    field is missing or empty -- this is request-shape validation this
    route owns, distinct from the business-logic validation Modules 2/3
    already perform on well-formed input.
    """

    if image is None or not image.filename:
        raise InvalidRequestError("An 'image' file field is required.")

    if description is None or not description.strip():
        raise InvalidRequestError("A non-empty 'description' field is required.")

    image_bytes = await image.read()
    if not image_bytes:
        raise InvalidRequestError("The 'image' file field cannot be empty.")

    content_type = image.content_type or _DEFAULT_IMAGE_CONTENT_TYPE

    return image_bytes, content_type, description


@router.post(
    "/classify",
    response_model=ClassifyResponse,
    status_code=status.HTTP_200_OK,
)
async def classify_device(
    request: Request,
    image: UploadFile | None = File(default=None),  # noqa: B008
    description: str | None = Form(default=None),  # noqa: B008
    actor: str = Depends(require_api_key),  # noqa: B008
) -> ClassifyResponse:
    """Classify a device from a photo and a free-text description.

    See this module's docstring for the full orchestration sequence.
    """

    image_bytes, content_type, description_text = await _read_and_validate_request(
        image, description
    )

    valkey_client = request.app.state.valkey_client
    rate_limit_result = await check_rate_limit(valkey_client, actor)
    if not rate_limit_result.allowed:
        raise RateLimitExceededError(rate_limit_result.retry_after_seconds)

    await upload_media(image_bytes, content_type)

    vision_flags, profile = await asyncio.gather(
        extract_vision_flags(image_bytes),
        parse_intended_use(description_text),
    )

    consistency_flags = check_consistency(profile, vision_flags)

    classification_result = classify(profile)

    report = await generate_report(classification_result)

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        entry = build_audit_entry(
            actor=actor,
            action="classify",
            classification_result_id=None,
        )
        await record_audit_entry(session, entry)
        await session.commit()

    return ClassifyResponse(
        classification=classification_result,
        consistency_flags=consistency_flags,
        report=report,
    )
