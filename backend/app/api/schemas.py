"""Response schemas for the Caliper API.

Pydantic models only -- no I/O, just the shape of
``POST /api/v1/classify``'s success response.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.classification.models import ClassificationResult
from app.crosscheck.consistency import ConsistencyFlag


class DeviceBoundingBox(BaseModel):
    """Module 5 addition: the device's SAM 3.1 bounding box, in the
    source image's own pixel space, plus that source image's pixel
    dimensions -- everything the frontend needs to draw a
    proportionally-positioned overlay on its own rendered copy of the
    uploaded photo without ever needing the backend to know the
    browser's display size.

    Populated from a second, independent ``segment_concept()`` call
    made purely for display purposes (see ``backend/app/api/routes.py``);
    it never feeds into ``classify()`` or any other Module 1-4 logic.
    ``image_width``/``image_height`` come from the same SAM match's
    ``w``/``h`` fields (the source media's own pixel size, per the SAM
    segmenting-with-prompts guide), not a second decode of the image.
    """

    x1: int
    y1: int
    x2: int
    y2: int
    image_width: int
    image_height: int


class ClassifyResponse(BaseModel):
    """Success response body for ``POST /api/v1/classify``.

    The three top-level keys the Module 4 brief specifies --
    ``classification``, ``consistency_flags``, and ``report`` -- plus
    one Module 5 addition: ``device_bounding_box``, nullable, present
    only for display purposes (see :class:`DeviceBoundingBox`). ``None``
    when the second, independent SAM call finds no match -- the same
    "zero matches is a valid outcome" semantics ``segment_concept()``
    already documents, not an error.
    """

    classification: ClassificationResult
    consistency_flags: list[ConsistencyFlag]
    report: str
    device_bounding_box: DeviceBoundingBox | None = None
