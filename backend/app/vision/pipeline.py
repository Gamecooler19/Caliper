"""Vision pipeline: image bytes -> a populated VisionFlags record.

Owns the one function this module is responsible for,
``extract_vision_flags``. Internally it runs two Meta Model API calls in
sequence:

1. SAM 3.1 segmentation (via ``app.vision.client.segment_concept``) to
   locate the device in the image.
2. A Muse Spark multimodal structured-output call (via
   ``app.vision.client.understand_image_structured``) on the segmented
   region, to populate the two nullable boolean fields on
   :class:`~app.classification.models.VisionFlags`.

This module does not implement the Module 3 vision-versus-profile
cross-check from architecture.md's data flow step 4 -- that step needs
``IntendedUseProfile``, which doesn't exist as a populated input until
intake parsing (Module 3) lands. See reports/module-2-report.md for that
gap, explicitly noted rather than partially built here.

This module imports ``app.classification.models.VisionFlags`` (Module 1's
data contract) but Module 1's classification package does not import
anything from here, so the zero-I/O invariant on
``app.classification.engine`` / ``models`` / ``rules_data`` is untouched.
"""

from __future__ import annotations

import io

from PIL import Image

from app.classification.models import VisionFlags
from app.security.secrets import load_muse_spark_model_id, load_sam_model_id
from app.vision.client import SamBox, segment_concept, understand_image_structured

#: Concept prompt for the SAM 3.1 segmentation step. A short, concrete
#: noun phrase, per the SAM "Write the concept prompt" guidance.
_DEVICE_CONCEPT_PROMPT = "medical device"

#: JSON Schema for the Muse Spark structured-output call. Both fields are
#: nullable: a value of null means the model found no reliable visual
#: evidence either way, which this pipeline reports as None rather than
#: guessing a boolean.
_VISION_FLAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "visual_evidence_implantable": {
            "type": ["boolean", "null"],
            "description": (
                "True if the image shows clear visual evidence the device "
                "is designed to be implanted in the body (e.g. a sealed "
                "housing with suture tabs or leads, a form factor "
                "consistent with a pacemaker, stent, or similar). False if "
                "the image shows clear evidence it is not implantable. "
                "Null if the image gives no reliable evidence either way."
            ),
        },
        "visual_evidence_duration_conflict": {
            "type": ["boolean", "null"],
            "description": (
                "True if the device's visual form factor conflicts with a "
                "short/transient duration of use (e.g. it visually "
                "resembles a long-term or permanent device). False if the "
                "visual form factor is consistent with the described "
                "duration. Null if the image gives no reliable evidence "
                "either way, or duration cannot be assessed from the "
                "image alone."
            ),
        },
    },
    "required": [
        "visual_evidence_implantable",
        "visual_evidence_duration_conflict",
    ],
}

_VISION_FLAGS_PROMPT = (
    "Look closely at this image of a medical device. Answer two questions "
    "about what is visually evident, not what you assume from context: "
    "(1) Does the device's visible form factor show clear evidence it is "
    "designed to be implanted in the body? (2) Does the device's visible "
    "form factor conflict with being used only transiently or briefly? "
    "For each question, answer true, false, or null if the image does not "
    "give you reliable visual evidence either way. Do not guess."
)


def _crop_to_box(image_bytes: bytes, box: SamBox) -> bytes:
    """Crop ``image_bytes`` to the pixel region described by ``box``.

    ``box`` coordinates are in the source image's own pixel space, per
    the SAM segmenting-with-prompts guide ("Convert coordinates to media
    pixels"). Returns PNG-encoded bytes of the crop.
    """

    with Image.open(io.BytesIO(image_bytes)) as original:
        image = original.convert("RGB")
        cropped = image.crop((box.x1, box.y1, box.x2, box.y2))
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue()


def _largest_box(boxes: list[SamBox]) -> SamBox:
    """Pick the largest matched box by area, as the most likely primary
    device region when SAM returns multiple matches for the concept
    prompt.
    """

    return max(boxes, key=lambda box: (box.x2 - box.x1) * (box.y2 - box.y1))


async def extract_vision_flags(image_bytes: bytes) -> VisionFlags:
    """Turn a device image into a populated :class:`VisionFlags` record.

    Runs SAM 3.1 segmentation to locate the device, then a Muse Spark
    structured-output call on the segmented region (or the full image, if
    segmentation found no match) to populate
    ``visual_evidence_implantable`` and
    ``visual_evidence_duration_conflict``.

    Propagates :class:`~app.vision.client.MetaModelAPIError`,
    :class:`~app.vision.client.MetaModelAPIResponseError`, or
    :class:`~app.vision.client.MetaModelAPITimeoutError` from either
    underlying call -- this function never catches those to return a
    default or guessed :class:`VisionFlags`.
    """

    sam_model = load_sam_model_id()
    muse_spark_model = load_muse_spark_model_id()

    boxes = await segment_concept(
        image_bytes, _DEVICE_CONCEPT_PROMPT, model=sam_model
    )

    region_bytes = (
        _crop_to_box(image_bytes, _largest_box(boxes)) if boxes else image_bytes
    )

    structured = await understand_image_structured(
        region_bytes,
        _VISION_FLAGS_PROMPT,
        _VISION_FLAGS_SCHEMA,
        schema_name="vision_flags",
        model=muse_spark_model,
    )

    return VisionFlags(
        visual_evidence_implantable=structured.get("visual_evidence_implantable"),
        visual_evidence_duration_conflict=structured.get(
            "visual_evidence_duration_conflict"
        ),
    )
