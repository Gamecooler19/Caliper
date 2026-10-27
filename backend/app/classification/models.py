"""Data contracts for the Caliper classification engine.

These are pure data definitions. Nothing in this module performs I/O,
network calls, or any form of inference -- it only describes the shape
of the structured facts the engine consumes and produces, as specified
in /architecture.md's "Data model" section.

All models are immutable (``frozen=True``) so that a profile or result,
once constructed, cannot be mutated in place -- any transformation must
produce a new object. This makes the engine's pure-function contract
enforceable by the type system, not just by convention.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Fixed, literal strings from architecture.md. These are not placeholders --
# they are copied verbatim from the data model / security posture sections
# and must not drift from that document.
# ---------------------------------------------------------------------------

FIRST_SCHEDULE_CITATION: str = "First Schedule, Medical Devices Rules 2017"
"""Fixed source_citation value for every RuleDefinition."""

PRE_ASSESSMENT_DISCLAIMER: str = (
    "This is a pre-assessment estimate, not a regulatory determination. "
    "Caliper does not replace the Drugs Controller General of India's "
    "classification authority: under the Medical Devices Rules, 2017, device "
    "classification is non-appealable once made. This output must not be "
    "represented to any regulator, customer, or third party as an official "
    "classification."
)
"""Fixed disclaimer language, grounded in architecture.md's Overview section
("Every output Caliper produces is explicitly a pre-assessment estimate, not
a determination, and says so in the output itself.") and LICENSE.md's
"Not a Regulatory Determination" clause. Every ClassificationResult carries
this exact string.
"""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InvasivenessTier(StrEnum):
    """How far the device crosses the body's surface, per IntendedUseProfile."""

    NON_INVASIVE = "non_invasive"
    INVASIVE = "invasive"
    SURGICALLY_INVASIVE = "surgically_invasive"
    IMPLANTABLE = "implantable"


class DurationTier(StrEnum):
    """How long the device maintains contact, per IntendedUseProfile."""

    TRANSIENT = "transient"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class DeviceCategory(StrEnum):
    """Whether the device relies on a power/energy source other than the body."""

    ACTIVE = "active"
    NON_ACTIVE = "non_active"


class RiskClass(StrEnum):
    """Medical Devices Rules 2017 risk classes, lowest to highest risk."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class LicensingAuthority(StrEnum):
    """Which CDSCO-designated authority licenses a given risk class."""

    STATE = "State"
    CENTRAL = "Central"


class ApplicableForm(StrEnum):
    """CDSCO forms this engine is scoped to reason about.

    The field this backs is nullable: ``None`` means no form applies in the
    current scope, which is itself a meaningful, structured answer, not a
    missing one.
    """

    MD_3 = "MD-3"
    MD_5 = "MD-5"


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


class IntendedUseProfile(BaseModel):
    """Structured intended-use facts about a device, as described in
    architecture.md's Data model section. This is the primary input to the
    classification engine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invasiveness_tier: InvasivenessTier
    duration_tier: DurationTier
    device_category: DeviceCategory
    body_system_contacted: str
    energy_source: str | None = None


class VisionFlags(BaseModel):
    """Cross-check flags produced by the (not-yet-built) vision pipeline.

    Unused by the engine's current rule set but present in the contract per
    architecture.md's Data flow step 4 ("Cross-check"). Both fields are
    nullable booleans: ``None`` means "no vision evidence available", which
    is distinct from ``False`` ("vision evidence contradicts this flag").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    visual_evidence_implantable: bool | None = None
    visual_evidence_duration_conflict: bool | None = None


class RuleDefinition(BaseModel):
    """A single structured classification rule.

    Deliberately data, not code: the engine must never encode a risk-class
    decision as inline Python conditionals divorced from a RuleDefinition
    record. Every classification path maps to exactly one of these, defined
    in rules_data.py, with ``needs_verification`` defaulting to True until a
    maintainer confirms the criteria text against the verbatim gazette (see
    CONTRIBUTING.md).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    source_citation: str = Field(default=FIRST_SCHEDULE_CITATION)
    criteria: str
    resulting_class: RiskClass
    needs_verification: bool = True


class AuditLogEntry(BaseModel):
    """One append-only audit record, written on every classification run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str
    action: str
    timestamp: str
    classification_result_id: str | None = None


class ClassificationResult(BaseModel):
    """The deterministic engine's output for one classification run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_class: RiskClass
    matched_rule_id: str
    applicable_licensing_authority: LicensingAuthority
    applicable_form: ApplicableForm | None = None
    clinical_investigation_required: bool
    clinical_investigation_basis: str
    disclaimer: str = Field(default=PRE_ASSESSMENT_DISCLAIMER)
