"""SAM 3.1 segmentation and Muse Spark image-understanding calls.

This module owns the vision-specific request shaping (image encoding,
SAM output-text box parsing) on top of the shared Meta Model API
transport in ``app.meta_api.client``. The three HTTP-layer exception
types (:class:`MetaModelAPIError`, :class:`MetaModelAPIResponseError`,
:class:`MetaModelAPITimeoutError`) and the raw ``create_response`` /
``upload_file`` transport calls moved to ``app.meta_api.client`` in
Module 3 -- this module re-exports the three exception types (and
``upload_file``, which was never vision-specific) for backward
compatibility with existing imports, but no longer implements its own
HTTP calls.

``segment_concept()`` and ``understand_image_structured()`` keep their
exact Module 2 call signatures and external behavior; only their
internal transport now goes through the shared client. See
reports/module-3-report.md for confirmation nothing drifted during this
move.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from app.meta_api.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
    create_response,
    upload_file,
)

__all__ = [
    "MetaModelAPIError",
    "MetaModelAPIResponseError",
    "MetaModelAPITimeoutError",
    "SamBox",
    "segment_concept",
    "understand_image_structured",
    "upload_file",
]

# Grammar for one SAM 3.1 box record in the output_text special-token
# stream, per the SAM overview / segmenting-with-prompts guides:
#   <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>
_SAM_BOX_PATTERN = re.compile(
    r"<\|box;x1=(?P<x1>\d+);y1=(?P<y1>\d+);x2=(?P<x2>\d+);y2=(?P<y2>\d+);"
    r"w=(?P<w>\d+);h=(?P<h>\d+)\|>"
)


def _image_to_data_url(image_bytes: bytes, content_type: str = "image/png") -> str:
    """Encode raw image bytes as a base64 data URL, per the File handling
    guide's "Send it inline" path (``image_url`` as a base64 data URL).
    """

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


@dataclass(frozen=True)
class SamBox:
    """One bounding box from a SAM 3.1 segmentation match, in source
    pixel coordinates (``w``/``h`` are the source media's own pixel
    size, per the SAM segmenting-with-prompts guide).
    """

    x1: int
    y1: int
    x2: int
    y2: int
    w: int
    h: int


async def segment_concept(
    image_bytes: bytes,
    concept: str,
    *,
    model: str,
    content_type: str = "image/png",
) -> list[SamBox]:
    """Run SAM 3.1 concept segmentation on one image via the shared
    ``create_response()`` transport.

    ``concept`` is a short noun phrase per the "Write the concept prompt"
    guidance (e.g. "device"). Returns every matched box, in match order;
    an empty list means zero matches, which the SAM docs document as a
    valid, non-error outcome -- not every image contains the named
    concept.

    Raises :class:`MetaModelAPITimeoutError`, :class:`MetaModelAPIError`,
    or :class:`MetaModelAPIResponseError` on failure.
    """

    output_text = await create_response(
        model,
        [
            {"type": "input_text", "text": concept},
            {
                "type": "input_image",
                "image_url": _image_to_data_url(image_bytes, content_type),
            },
        ],
    )

    return [
        SamBox(
            x1=int(match["x1"]),
            y1=int(match["y1"]),
            x2=int(match["x2"]),
            y2=int(match["y2"]),
            w=int(match["w"]),
            h=int(match["h"]),
        )
        for match in _SAM_BOX_PATTERN.finditer(output_text)
    ]


async def understand_image_structured(
    image_bytes: bytes,
    prompt: str,
    json_schema: dict[str, Any],
    schema_name: str,
    *,
    model: str,
    content_type: str = "image/png",
) -> dict[str, Any]:
    """Run a Muse Spark multimodal call with structured JSON output via
    the shared ``create_response()`` transport.

    Per the Structured output guide, the Responses API takes the schema
    under ``text.format`` (``response_format`` is the Chat Completions
    equivalent; this client always uses the Responses API). Returns the
    parsed JSON object from the model's ``output_text``.

    Raises :class:`MetaModelAPITimeoutError`, :class:`MetaModelAPIError`,
    or :class:`MetaModelAPIResponseError` -- the last of these also
    covers the model's output_text not being valid JSON, since that is a
    malformed response from this client's point of view.
    """

    output_text = await create_response(
        model,
        [
            {"type": "input_text", "text": prompt},
            {
                "type": "input_image",
                "image_url": _image_to_data_url(image_bytes, content_type),
            },
        ],
        text_format={
            "type": "json_schema",
            "name": schema_name,
            "schema": json_schema,
        },
    )

    try:
        parsed = json.loads(output_text)
    except ValueError as exc:
        raise MetaModelAPIResponseError(
            f"Muse Spark structured-output text was not valid JSON: "
            f"{output_text[:200]!r}"
        ) from exc

    if not isinstance(parsed, dict):
        raise MetaModelAPIResponseError(
            f"Muse Spark structured-output JSON was not an object: {parsed!r}"
        )

    return parsed
