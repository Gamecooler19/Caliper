"""POST /api/v1/classify: the one HTTP route this module owns.

Orchestrates Modules 1-3 for one request:

1. Validate the multipart request (image + description present).
2. ``upload_media()`` to store the image.
3. ``extract_vision_flags()``, ``parse_intended_use()``, and (Module 5
   addition) a second, independent ``segment_concept()`` call made
   purely for display purposes, all concurrently via ``asyncio.gather``
   -- none of the three depends on either other's output.
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

Module 5 addition -- display-only bounding box
------------------------------------------------
``extract_vision_flags()`` (Module 2) already runs its own internal
SAM 3.1 segmentation call inside ``app.vision.pipeline``, but that
module never returns the box coordinates out of its own function
boundary -- it only returns the two cross-check booleans, by design
(see that module's docstring). Rather than reach into Module 2's
internals to extract a value it deliberately doesn't expose, this
route makes its own second, independent call to the same
already-tested ``segment_concept()`` (Module 2's client function, not
``extract_vision_flags`` itself) purely so the frontend has a box to
draw over the user's own photo. This call:

- Never feeds its result into ``classify()``, ``check_consistency()``,
  or any other Module 1-4 logic -- its result is placed on the
  response only, as ``device_bounding_box``.
- Uses the same ``SAM_MODEL_ID`` secret and the same concept-prompt
  style Module 2 already uses, via the same ``load_sam_model_id()``
  loader -- no new secret, no new model ID.
- Resolves to ``None`` on the response when SAM returns zero matches,
  per ``segment_concept()``'s own documented "zero matches is a valid,
  non-error outcome" semantics -- never raises just because the
  concept wasn't found in the frame.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from app.api.auth import require_api_key
from app.api.errors import InvalidRequestError, RateLimitExceededError
from app.api.rate_limit import check_rate_limit
from app.api.schemas import ClassifyResponse, DeviceBoundingBox
from app.classification.engine import classify
from app.crosscheck.consistency import check_consistency
from app.intake.parser import parse_intended_use
from app.report.generator import generate_report
from app.security.audit import build_audit_entry, record_audit_entry
from app.security.secrets import load_sam_model_id
from app.storage.media import upload_media
from app.vision.client import SamBox, segment_concept
from app.vision.pipeline import extract_vision_flags

router = APIRouter(prefix="/api/v1")

_DEFAULT_IMAGE_CONTENT_TYPE = "image/png"

#: Concept prompt for this route's own, display-only SAM 3.1 call.
#: Deliberately the same short noun-phrase style Module 2 already uses
#: in ``app.vision.pipeline``'s internal call -- this route does not
#: import that module's private prompt constant, since the two calls
#: are independent by design, but there's no reason for the wording to
#: differ.
_DISPLAY_BOX_CONCEPT_PROMPT = "medical device"


def _largest_box_for_display(boxes: list[SamBox]) -> DeviceBoundingBox:
    """Pick the largest matched box by area (the same "most likely
    primary device region" tie-break ``app.vision.pipeline`` uses
    internally) and shape it into the API's :class:`DeviceBoundingBox`.

    ``boxes`` is assumed non-empty; callers only invoke this when
    ``segment_concept()`` returned at least one match.
    """

    box = max(boxes, key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1))
    return DeviceBoundingBox(
        x1=box.x1,
        y1=box.y1,
        x2=box.x2,
        y2=box.y2,
        image_width=box.w,
        image_height=box.h,
    )


async def _locate_device_for_display(
    image_bytes: bytes, content_type: str
) -> DeviceBoundingBox | None:
    """Run the second, independent, display-only SAM 3.1 call and
    return a :class:`DeviceBoundingBox`, or ``None`` if SAM found no
    match for the concept prompt -- the documented, valid "zero
    matches" outcome, not an error.
    """

    sam_model = load_sam_model_id()
    boxes = await segment_concept(
        image_bytes,
        _DISPLAY_BOX_CONCEPT_PROMPT,
        model=sam_model,
        content_type=content_type,
    )
    return _largest_box_for_display(boxes) if boxes else None


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

    vision_flags, profile, device_bounding_box = await asyncio.gather(
        extract_vision_flags(image_bytes),
        parse_intended_use(description_text),
        _locate_device_for_display(image_bytes, content_type),
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
        device_bounding_box=device_bounding_box,
    )
