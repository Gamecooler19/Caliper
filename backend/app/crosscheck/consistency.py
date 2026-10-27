"""Vision-versus-stated-profile consistency checking: the one function
this module owns, ``check_consistency``.

Semantic analysis of VisionFlags (required before writing this
comparison; see the Module 4 brief and reports/module-4-report.md for
the same analysis recorded in the completion report)
----------------------------------------------------------------------
``extract_vision_flags()`` (``backend/app/vision/pipeline.py``) never
receives a stated :class:`IntendedUseProfile` -- it only looks at the
device image. So neither VisionFlags field was ever computed "against"
the stated profile; each measures something about the image alone,
per the exact prompt text sent to Muse Spark in
``backend/app/vision/pipeline.py``'s ``_VISION_FLAGS_PROMPT``:

    "Look closely at this image of a medical device. Answer two
    questions about what is visually evident, not what you assume from
    context: (1) Does the device's visible form factor show clear
    evidence it is designed to be implanted in the body? (2) Does the
    device's visible form factor conflict with being used only
    transiently or briefly? For each question, answer true, false, or
    null if the image does not give you reliable visual evidence
    either way. Do not guess."

Field 1, ``visual_evidence_implantable``, measures: does the image show
clear visual evidence the device is designed to be implanted? This is
directly, symmetrically comparable to ``IntendedUseProfile.
invasiveness_tier == InvasivenessTier.IMPLANTABLE`` in both directions:
True-but-not-stated-implantable and False-but-stated-implantable are
both sound discrepancies, because the question asked is exactly about
implantability, the same concept ``invasiveness_tier`` encodes.

Field 2, ``visual_evidence_duration_conflict``, measures something
narrower than its own schema description in ``pipeline.py`` might
suggest at a glance: the real question put to the model is specifically
"conflict with being used only transiently or briefly" -- not "conflict
with whatever duration_tier was stated." A `True` answer means the
device visually looks like a long-term/permanent device; it says
nothing about whether that visual impression conflicts with a stated
``short_term`` or ``long_term`` duration, because the model was never
asked to compare against those. A `False` answer means the image is
consistent with transient use; it likewise says nothing about
consistency with ``short_term``/``long_term``, for the same reason.
Therefore this field only supports *one* sound comparison: a `True`
value is a genuine discrepancy *only* when the stated
``duration_tier`` is ``transient`` -- the one case the question was
actually asked against. Any other combination (``False``, or a stated
``short_term``/``long_term`` duration) cannot be read as agreement or
disagreement, because the model's answer doesn't bear on that
comparison at all, and this function does not flag it.

This is exactly why ``check_consistency()`` below checks
``visual_evidence_implantable`` in both directions but
``visual_evidence_duration_conflict`` in only the one direction the
question's actual wording supports.

Zero I/O, zero network imports. Only imports from
``app.classification.models`` -- the plain data contracts Module 1
defines for exactly this purpose -- never from
``app.classification.engine`` or ``rules_data``, and nothing here is
read back by ``classify()``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.classification.models import (
    DurationTier,
    IntendedUseProfile,
    InvasivenessTier,
    VisionFlags,
)


class ConsistencyFlag(BaseModel):
    """One discrepancy between a stated profile field and the vision
    signal for it.

    ``message`` always frames the discrepancy as something needing
    human review -- it never asserts a regulatory conclusion or implies
    that any classification rule was applied because of it. Nothing in
    this module, and nothing that consumes a ``ConsistencyFlag``, feeds
    back into ``app.classification.engine.classify()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_name: str
    stated_value: str
    vision_signal: bool
    message: str


def check_consistency(
    profile: IntendedUseProfile, vision: VisionFlags
) -> list[ConsistencyFlag]:
    """Compare ``profile`` against ``vision`` and return one
    :class:`ConsistencyFlag` per discrepancy found, or an empty list if
    nothing disagrees.

    Pure function: zero I/O, zero network imports, no side effects.
    Only flags discrepancies the underlying vision signals can actually
    support, per the semantic analysis in this module's docstring --
    it does not invent a comparison the vision model was never asked
    to make.
    """

    flags: list[ConsistencyFlag] = []

    # --- invasiveness_tier vs. visual_evidence_implantable ----------------
    # Sound in both directions: the vision question is directly about
    # implantability, the same concept invasiveness_tier encodes.
    if (
        vision.visual_evidence_implantable is True
        and profile.invasiveness_tier != InvasivenessTier.IMPLANTABLE
    ):
        flags.append(
            ConsistencyFlag(
                field_name="invasiveness_tier",
                stated_value=profile.invasiveness_tier.value,
                vision_signal=True,
                message=(
                    "Vision analysis found visual evidence consistent with "
                    "an implantable device, but the stated profile's "
                    f"invasiveness_tier is '{profile.invasiveness_tier.value}', "
                    "not implantable. This discrepancy needs human review "
                    "before either input is relied on."
                ),
            )
        )
    elif (
        vision.visual_evidence_implantable is False
        and profile.invasiveness_tier == InvasivenessTier.IMPLANTABLE
    ):
        flags.append(
            ConsistencyFlag(
                field_name="invasiveness_tier",
                stated_value=profile.invasiveness_tier.value,
                vision_signal=False,
                message=(
                    "The stated profile says the device is implantable, "
                    "but vision analysis found clear visual evidence it is "
                    "not. This discrepancy needs human review before "
                    "either input is relied on."
                ),
            )
        )

    # --- duration_tier vs. visual_evidence_duration_conflict --------------
    # Only sound in one direction: the vision question asks specifically
    # about conflict with *transient* use, so a True answer is only a
    # genuine discrepancy when the stated duration_tier is transient.
    # A False answer, or a stated short_term/long_term duration, is not
    # evidence either way for this field -- see this module's docstring.
    if (
        vision.visual_evidence_duration_conflict is True
        and profile.duration_tier == DurationTier.TRANSIENT
    ):
        flags.append(
            ConsistencyFlag(
                field_name="duration_tier",
                stated_value=profile.duration_tier.value,
                vision_signal=True,
                message=(
                    "Vision analysis found the device's visual form "
                    "factor conflicts with transient or brief use, but "
                    "the stated profile's duration_tier is 'transient'. "
                    "This discrepancy needs human review before either "
                    "input is relied on."
                ),
            )
        )

    return flags
