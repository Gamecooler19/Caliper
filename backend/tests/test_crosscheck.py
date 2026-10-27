"""Tests for vision-versus-stated-profile consistency checking:
app.crosscheck.consistency.

Covers, per the Module 4 brief:

- At least one discrepancy-detected case per field
  (``invasiveness_tier``/``visual_evidence_implantable`` and
  ``duration_tier``/``visual_evidence_duration_conflict``).
- One full-agreement empty-list case.

Also covers the directions the semantic analysis in
``consistency.py``'s module docstring says are *not* sound (a stated
``short_term``/``long_term`` duration paired with
``visual_evidence_duration_conflict=True``, and a ``False`` value for
that field), confirming ``check_consistency`` does not invent a
comparison the vision model was never asked to make.

Pure-function tests: no mocking needed, no I/O, no network calls.
"""

from __future__ import annotations

from app.classification.models import (
    DeviceCategory,
    DurationTier,
    IntendedUseProfile,
    InvasivenessTier,
    VisionFlags,
)
from app.crosscheck.consistency import ConsistencyFlag, check_consistency


def _profile(**overrides) -> IntendedUseProfile:
    fields = {
        "invasiveness_tier": InvasivenessTier.NON_INVASIVE,
        "duration_tier": DurationTier.TRANSIENT,
        "device_category": DeviceCategory.NON_ACTIVE,
        "body_system_contacted": "skin",
    }
    fields.update(overrides)
    return IntendedUseProfile(**fields)


def _vision(**overrides) -> VisionFlags:
    fields = {
        "visual_evidence_implantable": None,
        "visual_evidence_duration_conflict": None,
    }
    fields.update(overrides)
    return VisionFlags(**fields)


# ---------------------------------------------------------------------------
# Full agreement: empty list
# ---------------------------------------------------------------------------


def test_check_consistency_returns_empty_list_when_nothing_disagrees():
    profile = _profile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
    )
    vision = _vision(
        visual_evidence_implantable=False,
        visual_evidence_duration_conflict=False,
    )

    assert check_consistency(profile, vision) == []


def test_check_consistency_returns_empty_list_when_vision_gives_no_evidence():
    """Both VisionFlags fields null (no reliable evidence either way)
    must never be treated as a discrepancy.
    """

    profile = _profile(invasiveness_tier=InvasivenessTier.IMPLANTABLE)
    vision = _vision()

    assert check_consistency(profile, vision) == []


def test_check_consistency_implantable_stated_and_implantable_vision_agree():
    profile = _profile(invasiveness_tier=InvasivenessTier.IMPLANTABLE)
    vision = _vision(visual_evidence_implantable=True)

    assert check_consistency(profile, vision) == []


# ---------------------------------------------------------------------------
# Discrepancy case: invasiveness_tier / visual_evidence_implantable
# ---------------------------------------------------------------------------


def test_check_consistency_flags_vision_implantable_but_profile_says_non_invasive():
    profile = _profile(invasiveness_tier=InvasivenessTier.NON_INVASIVE)
    vision = _vision(visual_evidence_implantable=True)

    flags = check_consistency(profile, vision)

    assert len(flags) == 1
    flag = flags[0]
    assert isinstance(flag, ConsistencyFlag)
    assert flag.field_name == "invasiveness_tier"
    assert flag.stated_value == "non_invasive"
    assert flag.vision_signal is True
    assert "review" in flag.message.lower()
    # The message must not assert a regulatory conclusion or imply a
    # rule was applied.
    assert "class" not in flag.message.lower()
    assert "licensing" not in flag.message.lower()


def test_check_consistency_flags_profile_says_implantable_but_vision_says_not():
    profile = _profile(invasiveness_tier=InvasivenessTier.IMPLANTABLE)
    vision = _vision(visual_evidence_implantable=False)

    flags = check_consistency(profile, vision)

    assert len(flags) == 1
    flag = flags[0]
    assert flag.field_name == "invasiveness_tier"
    assert flag.stated_value == "implantable"
    assert flag.vision_signal is False
    assert "review" in flag.message.lower()


# ---------------------------------------------------------------------------
# Discrepancy case: duration_tier / visual_evidence_duration_conflict
# ---------------------------------------------------------------------------


def test_check_consistency_flags_duration_conflict_when_stated_transient():
    """The one sound direction: vision says the device looks long-term,
    but the profile states transient use.
    """

    profile = _profile(duration_tier=DurationTier.TRANSIENT)
    vision = _vision(visual_evidence_duration_conflict=True)

    flags = check_consistency(profile, vision)

    assert len(flags) == 1
    flag = flags[0]
    assert flag.field_name == "duration_tier"
    assert flag.stated_value == "transient"
    assert flag.vision_signal is True
    assert "review" in flag.message.lower()
    assert "class" not in flag.message.lower()


# ---------------------------------------------------------------------------
# Unsound directions: must NOT be flagged, per the semantic analysis in
# consistency.py's module docstring.
# ---------------------------------------------------------------------------


def test_check_consistency_does_not_flag_duration_conflict_when_stated_long_term():
    """visual_evidence_duration_conflict=True only answers "does this
    look inconsistent with *transient* use" -- it says nothing about a
    stated long_term duration, so this must not be flagged.
    """

    profile = _profile(duration_tier=DurationTier.LONG_TERM)
    vision = _vision(visual_evidence_duration_conflict=True)

    assert check_consistency(profile, vision) == []


def test_check_consistency_does_not_flag_duration_conflict_when_stated_short_term():
    profile = _profile(duration_tier=DurationTier.SHORT_TERM)
    vision = _vision(visual_evidence_duration_conflict=True)

    assert check_consistency(profile, vision) == []


def test_check_consistency_does_not_flag_duration_false_against_any_stated_duration():
    """A False duration-conflict answer means "consistent with
    transient use"; it is not evidence of (dis)agreement with a stated
    short_term/long_term duration either.
    """

    for duration in (DurationTier.TRANSIENT, DurationTier.SHORT_TERM, DurationTier.LONG_TERM):
        profile = _profile(duration_tier=duration)
        vision = _vision(visual_evidence_duration_conflict=False)
        assert check_consistency(profile, vision) == []


# ---------------------------------------------------------------------------
# Multiple discrepancies at once
# ---------------------------------------------------------------------------


def test_check_consistency_returns_both_flags_when_both_fields_disagree():
    profile = _profile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
    )
    vision = _vision(
        visual_evidence_implantable=True,
        visual_evidence_duration_conflict=True,
    )

    flags = check_consistency(profile, vision)

    field_names = {flag.field_name for flag in flags}
    assert field_names == {"invasiveness_tier", "duration_tier"}
    assert len(flags) == 2
