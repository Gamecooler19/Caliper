"""The deterministic classification engine.

This module is the architectural core described in architecture.md: "a
deterministic, unit-tested rules engine [that] makes the decision itself,
auditable as code rather than as a prompt." It contains exactly one code
path from input to output, driven entirely by the data table in
``rules_data.py``.

Hard invariant: zero I/O, zero network calls, zero imports of anything
that performs I/O (no ``requests``, ``httpx``, file reads, database
clients, or framework imports). Every function here is a pure function of
its arguments: same input, same output, no side effects, nothing observed
or mutated outside the call. This invariant is enforced by
``test_classification_engine.py``'s import-time static check and is why
this module imports nothing beyond ``app.classification.models`` and
``app.classification.rules_data``.

On missing or contradictory input, this engine raises rather than guessing
a class. A medical device risk classification that silently defaults is
worse than one that refuses to run -- see architecture.md's rejection of
single-model classification for the same reason.
"""

from __future__ import annotations

from app.classification.models import (
    ClassificationResult,
    IntendedUseProfile,
    VisionFlags,
)
from app.classification.rules_data import (
    RULES,
    RuleCondition,
    RuleRecord,
    is_critical_body_system,
)


class ClassificationError(ValueError):
    """Raised when a profile cannot be classified deterministically.

    Covers both malformed/contradictory input and the (should-not-happen,
    but checked) case where no rule in the table matches at all. Never
    caught internally to fall back to a guessed class -- callers decide
    how to surface this.
    """


def _validate_profile(profile: IntendedUseProfile) -> None:
    """Reject contradictory structured input before any rule is evaluated.

    ``IntendedUseProfile`` already enforces type and enum membership at
    construction time (see models.py). This function enforces the
    cross-field consistency rules that a single field's type can't
    express on its own.
    """

    if not profile.body_system_contacted or not profile.body_system_contacted.strip():
        raise ClassificationError(
            "body_system_contacted is required and cannot be blank; the "
            "engine will not guess which body system is contacted."
        )


def _matches_condition(
    profile: IntendedUseProfile, condition: RuleCondition
) -> bool:
    """Pure structural match: does this profile satisfy this condition?

    Every field on ``RuleCondition`` is either a wildcard (``None``) or a
    constraint the profile must satisfy. This is the one generic matcher
    every rule in the table is evaluated through -- no per-rule branches.
    """

    if (
        condition.invasiveness_tier is not None
        and profile.invasiveness_tier not in condition.invasiveness_tier
    ):
        return False

    if (
        condition.duration_tier is not None
        and profile.duration_tier not in condition.duration_tier
    ):
        return False

    if (
        condition.device_category is not None
        and profile.device_category not in condition.device_category
    ):
        return False

    if condition.critical_body_system is not None:
        actual = is_critical_body_system(profile.body_system_contacted)
        if actual != condition.critical_body_system:
            return False

    return True


def find_matching_rule(profile: IntendedUseProfile) -> RuleRecord:
    """Return the first :class:`RuleRecord` in table order whose condition
    matches ``profile``.

    Raises :class:`ClassificationError` if no rule matches -- this engine
    never falls through to an implicit default class.
    """

    for record in RULES:
        if _matches_condition(profile, record.condition):
            return record

    raise ClassificationError(
        "No classification rule matched this IntendedUseProfile "
        f"(invasiveness_tier={profile.invasiveness_tier!r}, "
        f"duration_tier={profile.duration_tier!r}, "
        f"device_category={profile.device_category!r}, "
        f"body_system_contacted={profile.body_system_contacted!r}). "
        "Refusing to guess a risk class."
    )


def classify(
    profile: IntendedUseProfile,
    vision_flags: VisionFlags | None = None,
) -> ClassificationResult:
    """Classify a device's intended-use profile into a
    :class:`ClassificationResult`.

    This is the engine's single public entry point. ``vision_flags`` is
    accepted for forward compatibility with the Module 2 cross-check step
    described in architecture.md's Data flow, but is schema-validated and
    otherwise currently unused: this engine does not yet have a sourced,
    verified rule for resolving a vision/profile mismatch, so matching
    always proceeds on ``profile`` alone. ``vision_flags`` is still
    type-validated via :class:`VisionFlags` if provided, so a caller can't
    silently pass a malformed cross-check payload.

    Raises :class:`ClassificationError` on missing/contradictory input or
    if, in principle, no rule matches. Never returns a guessed class.
    """

    if profile is None:  # defensive: callers must supply a profile
        raise ClassificationError("IntendedUseProfile is required.")

    if vision_flags is not None and not isinstance(vision_flags, VisionFlags):
        raise ClassificationError(
            "vision_flags, if provided, must be a VisionFlags instance."
        )

    _validate_profile(profile)

    matched = find_matching_rule(profile)

    return ClassificationResult(
        risk_class=matched.definition.resulting_class,
        matched_rule_id=matched.definition.rule_id,
        applicable_licensing_authority=matched.licensing_authority,
        applicable_form=matched.applicable_form,
        clinical_investigation_required=matched.clinical_investigation_required,
        clinical_investigation_basis=matched.clinical_investigation_basis,
    )
