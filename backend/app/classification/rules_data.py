"""Structured rule data for the Caliper classification engine.

Every classification path is represented here as data -- a
:class:`RuleDefinition` (the public contract from architecture.md) paired
with a declarative :class:`RuleCondition` describing which structured input
values the rule matches. ``engine.py`` contains exactly one generic
evaluator that walks this table; it must never grow a per-rule branch of
its own. If a new rule is needed, it is added here, not as a new ``if`` in
the engine.

Sourcing and verification status
---------------------------------
The criteria below are *summaries* of the general classification logic
Indian medical device regulation (MDR 2017) is publicly known to follow --
an invasiveness/duration/active-status risk ladder culminating in
implantable and critical-body-system-contacting devices at the top.
They are deliberately written as plain-language descriptions, not as
verbatim numbered gazette rule text, because no verbatim First Schedule
text was sourced for this module. Every single rule below carries
``needs_verification=True`` and must stay that way until a maintainer
checks it against the actual notified First Schedule text and updates it
through the process described in CONTRIBUTING.md. Nothing in this file,
and nothing derived from it, may be presented as an authoritative
regulatory determination -- see LICENSE.md's "Not a Regulatory
Determination" clause and the disclaimer carried on every
ClassificationResult.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.classification.models import (
    ApplicableForm,
    DeviceCategory,
    DurationTier,
    InvasivenessTier,
    LicensingAuthority,
    RiskClass,
    RuleDefinition,
)

# ---------------------------------------------------------------------------
# Critical body system matching
# ---------------------------------------------------------------------------

#: Body systems whose involvement escalates an otherwise-invasive device to
#: Class D regardless of duration, mirroring the general pattern that
#: devices contacting the central circulatory system or central nervous
#: system are treated as highest-risk. This set is a conservative,
#: sourced-summary heuristic, not a verbatim gazette list -- it needs the
#: same verification pass as the rules that consume it.
CRITICAL_BODY_SYSTEMS: frozenset[str] = frozenset(
    {
        "heart",
        "central circulatory system",
        "central nervous system",
        "brain",
        "spinal cord",
    }
)


def is_critical_body_system(body_system_contacted: str) -> bool:
    """Pure, case/whitespace-insensitive membership check against
    :data:`CRITICAL_BODY_SYSTEMS`. No I/O, no network access.
    """

    return body_system_contacted.strip().casefold() in {
        name.casefold() for name in CRITICAL_BODY_SYSTEMS
    }


# ---------------------------------------------------------------------------
# Declarative match conditions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleCondition:
    """A declarative, structured match condition for one rule.

    Each field is either ``None`` (wildcard: any value matches) or a
    frozenset of acceptable values. ``critical_body_system`` is a tri-state:
    ``None`` means "don't care", ``True``/``False`` mean the rule only
    matches profiles whose body system does/doesn't fall in
    :data:`CRITICAL_BODY_SYSTEMS`. Evaluated generically by
    ``engine.matches_condition`` -- this dataclass carries no behavior.
    """

    invasiveness_tier: frozenset[InvasivenessTier] | None = None
    duration_tier: frozenset[DurationTier] | None = None
    device_category: frozenset[DeviceCategory] | None = None
    critical_body_system: bool | None = None


@dataclass(frozen=True)
class RuleRecord:
    """Pairs a public :class:`RuleDefinition` with its structured match
    condition and the additional structured outcome data
    (:class:`~app.classification.models.ClassificationResult` needs beyond
    ``resulting_class``). ``RuleDefinition`` itself is kept to exactly the
    fields specified in architecture.md; this wrapper is where the rest of
    the structured, sourced-but-unverified data lives.
    """

    definition: RuleDefinition
    condition: RuleCondition
    licensing_authority: LicensingAuthority
    applicable_form: ApplicableForm | None
    clinical_investigation_required: bool
    clinical_investigation_basis: str


# ---------------------------------------------------------------------------
# Clinical investigation basis text, reused across rules of the same class.
# Generic predicate-device reasoning, not a quotation of specific gazette
# text -- see module docstring.
# ---------------------------------------------------------------------------

_CI_BASIS_NOT_REQUIRED_LOW_RISK = (
    "Non-invasive, non-active devices are treated as having an adequately "
    "established predicate-device profile; no novel clinical evidence basis "
    "is triggered by this profile alone."
)
_CI_BASIS_NOT_REQUIRED_PRESUMED_EQUIVALENT = (
    "Low-risk invasive contact or simple active non-invasive use is "
    "presumed equivalent to an existing predicate device absent novel "
    "energy delivery or critical-body-system contact."
)
_CI_BASIS_REQUIRED_NO_PRESUMED_EQUIVALENCE = (
    "Surgically invasive or sustained-contact profiles are not presumed "
    "equivalent to an existing predicate device; a clinical investigation "
    "basis is required absent a cited predicate."
)
_CI_BASIS_REQUIRED_HIGH_RISK = (
    "Implantable devices and devices contacting the central circulatory or "
    "central nervous system carry long-term or permanent risk exposure; a "
    "clinical investigation basis is required regardless of predicate "
    "availability."
)

# ---------------------------------------------------------------------------
# The rule table, ordered from most specific / highest-escalating to most
# general. engine.classify() returns the FIRST matching record, so order is
# meaningful: implantable and critical-body-system escalation must be
# checked before the more general invasiveness/duration ladder.
# ---------------------------------------------------------------------------

RULES: tuple[RuleRecord, ...] = (
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-D-IMPLANTABLE",
            criteria=(
                "Implantable devices, intended to remain partly or wholly "
                "within the body for an extended or permanent period, "
                "regardless of duration tier or active/non-active status."
            ),
            resulting_class=RiskClass.D,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.IMPLANTABLE}),
        ),
        licensing_authority=LicensingAuthority.CENTRAL,
        applicable_form=ApplicableForm.MD_5,
        clinical_investigation_required=True,
        clinical_investigation_basis=_CI_BASIS_REQUIRED_HIGH_RISK,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-D-CRITICAL-SYSTEM",
            criteria=(
                "Invasive or surgically invasive devices that contact the "
                "central circulatory system or central nervous system, "
                "regardless of duration tier or active/non-active status."
            ),
            resulting_class=RiskClass.D,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset(
                {InvasivenessTier.INVASIVE, InvasivenessTier.SURGICALLY_INVASIVE}
            ),
            critical_body_system=True,
        ),
        licensing_authority=LicensingAuthority.CENTRAL,
        applicable_form=ApplicableForm.MD_5,
        clinical_investigation_required=True,
        clinical_investigation_basis=_CI_BASIS_REQUIRED_HIGH_RISK,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-C-SURGICALLY-INVASIVE",
            criteria=(
                "Surgically invasive devices not already escalated to Class D "
                "by critical-body-system contact, regardless of duration "
                "tier or active/non-active status."
            ),
            resulting_class=RiskClass.C,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.SURGICALLY_INVASIVE}),
            critical_body_system=False,
        ),
        licensing_authority=LicensingAuthority.CENTRAL,
        applicable_form=ApplicableForm.MD_5,
        clinical_investigation_required=True,
        clinical_investigation_basis=_CI_BASIS_REQUIRED_NO_PRESUMED_EQUIVALENCE,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-C-INVASIVE-SUSTAINED",
            criteria=(
                "Invasive devices (entering through a body orifice or "
                "surface, short of surgical invasion) used for short-term "
                "or long-term duration, not escalated by critical-body-"
                "system contact."
            ),
            resulting_class=RiskClass.C,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.INVASIVE}),
            duration_tier=frozenset({DurationTier.SHORT_TERM, DurationTier.LONG_TERM}),
            critical_body_system=False,
        ),
        licensing_authority=LicensingAuthority.CENTRAL,
        applicable_form=ApplicableForm.MD_5,
        clinical_investigation_required=True,
        clinical_investigation_basis=_CI_BASIS_REQUIRED_NO_PRESUMED_EQUIVALENCE,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-B-INVASIVE-TRANSIENT",
            criteria=(
                "Invasive devices used only transiently (momentary to "
                "short, uninterrupted use), not escalated by critical-"
                "body-system contact."
            ),
            resulting_class=RiskClass.B,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.INVASIVE}),
            duration_tier=frozenset({DurationTier.TRANSIENT}),
            critical_body_system=False,
        ),
        licensing_authority=LicensingAuthority.STATE,
        applicable_form=ApplicableForm.MD_3,
        clinical_investigation_required=False,
        clinical_investigation_basis=_CI_BASIS_NOT_REQUIRED_PRESUMED_EQUIVALENT,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-B-NONINVASIVE-ACTIVE-SUSTAINED",
            criteria=(
                "Non-invasive, active devices used for short-term or "
                "long-term duration (energy exchange or monitoring over a "
                "sustained period without crossing the body surface)."
            ),
            resulting_class=RiskClass.B,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.NON_INVASIVE}),
            duration_tier=frozenset({DurationTier.SHORT_TERM, DurationTier.LONG_TERM}),
            device_category=frozenset({DeviceCategory.ACTIVE}),
        ),
        licensing_authority=LicensingAuthority.STATE,
        applicable_form=ApplicableForm.MD_3,
        clinical_investigation_required=False,
        clinical_investigation_basis=_CI_BASIS_NOT_REQUIRED_PRESUMED_EQUIVALENT,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-A-NONINVASIVE-ACTIVE-TRANSIENT",
            criteria=(
                "Non-invasive, active devices used only transiently, e.g. "
                "simple energy transmission or measurement with no "
                "sustained contact."
            ),
            resulting_class=RiskClass.A,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.NON_INVASIVE}),
            duration_tier=frozenset({DurationTier.TRANSIENT}),
            device_category=frozenset({DeviceCategory.ACTIVE}),
        ),
        licensing_authority=LicensingAuthority.STATE,
        applicable_form=ApplicableForm.MD_3,
        clinical_investigation_required=False,
        clinical_investigation_basis=_CI_BASIS_NOT_REQUIRED_LOW_RISK,
    ),
    RuleRecord(
        definition=RuleDefinition(
            rule_id="FS-A-NONINVASIVE-NONACTIVE",
            criteria=(
                "Non-invasive, non-active devices, regardless of duration "
                "tier -- the baseline lowest-risk profile."
            ),
            resulting_class=RiskClass.A,
        ),
        condition=RuleCondition(
            invasiveness_tier=frozenset({InvasivenessTier.NON_INVASIVE}),
            device_category=frozenset({DeviceCategory.NON_ACTIVE}),
        ),
        licensing_authority=LicensingAuthority.STATE,
        applicable_form=None,
        clinical_investigation_required=False,
        clinical_investigation_basis=_CI_BASIS_NOT_REQUIRED_LOW_RISK,
    ),
)


def rule_definitions() -> tuple[RuleDefinition, ...]:
    """Return the public :class:`RuleDefinition` for every rule in the
    table, in table order. Pure convenience accessor for callers (audit
    display, tests, documentation) that only need the architecture.md
    contract, not the matching internals.
    """

    return tuple(record.definition for record in RULES)
