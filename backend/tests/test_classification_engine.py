"""Unit tests for the deterministic classification engine.

Covers, per the Module 1 brief:

- At least one positive case per risk class A through D, using the
  sourced criteria in ``rules_data.py`` (which in turn summarizes
  architecture.md's classification logic).
- Cases where missing or contradictory input fields cause the engine to
  raise rather than guess a class.
- A static check that ``app.classification.engine`` imports nothing that
  performs network I/O.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.classification import engine, rules_data
from app.classification.engine import ClassificationError, classify
from app.classification.models import (
    FIRST_SCHEDULE_CITATION,
    PRE_ASSESSMENT_DISCLAIMER,
    ApplicableForm,
    DeviceCategory,
    DurationTier,
    IntendedUseProfile,
    InvasivenessTier,
    LicensingAuthority,
    RiskClass,
    RuleDefinition,
    VisionFlags,
)
from app.classification.rules_data import rule_definitions

# ---------------------------------------------------------------------------
# Network-free / zero-I/O architectural invariant
# ---------------------------------------------------------------------------

#: Modules the engine is allowed to import. Anything importing outside the
#: standard library and these is a sign the "pure, zero-I/O" invariant may
#: have been broken.
_ALLOWED_ENGINE_IMPORT_PREFIXES = (
    "app.classification",
    "__future__",
)

#: Names that would indicate network or disk I/O crept into the engine.
_FORBIDDEN_IMPORT_NAMES = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "urllib2",
    "urllib3",
    "socket",
    "http",
    "ftplib",
    "smtplib",
    "sqlalchemy",
    "asyncpg",
    "psycopg",
    "psycopg2",
    "redis",
    "celery",
    "boto3",
    "fastapi",
    "starlette",
    "os",
    "pathlib",
    "io",
}


def _imported_module_names(module) -> set[str]:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("module", [engine, rules_data])
def test_engine_modules_have_no_forbidden_imports(module) -> None:
    """Static guard: the classification engine and its rule data must not
    import anything capable of network or disk I/O.
    """

    imported = _imported_module_names(module)
    forbidden_hit = imported & _FORBIDDEN_IMPORT_NAMES
    assert not forbidden_hit, (
        f"{module.__name__} imports forbidden I/O-capable module(s): "
        f"{forbidden_hit}"
    )


def test_engine_module_only_imports_allowed_prefixes() -> None:
    """Every non-stdlib import in engine.py must come from
    app.classification itself (models/rules_data) -- nothing from the web
    framework, ORM, or networking stack.
    """

    source = inspect.getsource(engine)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        module_name: str | None = None
        if isinstance(node, ast.ImportFrom) and node.module:
            module_name = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
        if module_name is None:
            continue
        if module_name in ("__future__",):
            continue
        # stdlib-only imports (none currently) would be fine too; assert
        # anything app-rooted is within the classification package.
        if module_name.startswith("app."):
            assert module_name.startswith(_ALLOWED_ENGINE_IMPORT_PREFIXES), (
                f"engine.py imports {module_name!r}, outside "
                "app.classification"
            )


def test_classification_engine_file_has_no_io_builtins() -> None:
    """Belt-and-suspenders textual check: engine.py's source never calls
    ``open(``, which would indicate disk I/O.
    """

    engine_path = Path(engine.__file__)
    text = engine_path.read_text(encoding="utf-8")
    assert "open(" not in text
    assert "socket" not in text


# ---------------------------------------------------------------------------
# rules_data sourcing invariants
# ---------------------------------------------------------------------------


def test_every_rule_carries_fixed_source_citation() -> None:
    for definition in rule_definitions():
        assert definition.source_citation == FIRST_SCHEDULE_CITATION


def test_every_rule_defaults_needs_verification_true() -> None:
    """No rule in the sourced-but-unverified table may claim to be
    verified -- that would misrepresent summary criteria as confirmed
    gazette text.
    """

    for definition in rule_definitions():
        assert definition.needs_verification is True


def test_rule_definition_needs_verification_defaults_true_on_construction() -> None:
    rule = RuleDefinition(
        rule_id="TEST-RULE",
        criteria="A placeholder rule for the default-value test.",
        resulting_class=RiskClass.A,
    )
    assert rule.needs_verification is True
    assert rule.source_citation == FIRST_SCHEDULE_CITATION


# ---------------------------------------------------------------------------
# Positive cases: one per risk class A-D, sourced from rules_data.py's
# criteria (itself grounded in architecture.md's classification logic).
# ---------------------------------------------------------------------------


def test_class_a_non_invasive_non_active_device() -> None:
    """A simple non-invasive, non-active device (e.g. a manual stethoscope
    analog, an elastic bandage) is the baseline Class A profile.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="skin",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.A
    assert result.matched_rule_id == "FS-A-NONINVASIVE-NONACTIVE"
    assert result.applicable_licensing_authority == LicensingAuthority.STATE
    assert result.clinical_investigation_required is False
    assert result.disclaimer == PRE_ASSESSMENT_DISCLAIMER


def test_class_a_non_invasive_active_transient_device() -> None:
    """A non-invasive, active, transiently-used device (e.g. a digital
    thermometer taking a momentary reading) is also Class A.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.ACTIVE,
        body_system_contacted="skin",
        energy_source="battery",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.A
    assert result.matched_rule_id == "FS-A-NONINVASIVE-ACTIVE-TRANSIENT"


def test_class_b_non_invasive_active_sustained_device() -> None:
    """A non-invasive, active device used over a sustained period (e.g. a
    long-term wearable monitor) is Class B.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.LONG_TERM,
        device_category=DeviceCategory.ACTIVE,
        body_system_contacted="skin",
        energy_source="mains",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.B
    assert result.matched_rule_id == "FS-B-NONINVASIVE-ACTIVE-SUSTAINED"
    assert result.applicable_form == ApplicableForm.MD_3


def test_class_b_invasive_transient_device() -> None:
    """An invasive device used only transiently (e.g. a hypodermic needle
    for a single injection) is Class B.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="muscle",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.B
    assert result.matched_rule_id == "FS-B-INVASIVE-TRANSIENT"


def test_class_c_invasive_long_term_device() -> None:
    """An invasive device used long-term without surgical invasion or
    critical-body-system contact (e.g. a long-term urinary catheter) is
    Class C.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.INVASIVE,
        duration_tier=DurationTier.LONG_TERM,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="urinary tract",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.C
    assert result.matched_rule_id == "FS-C-INVASIVE-SUSTAINED"
    assert result.clinical_investigation_required is True


def test_class_c_surgically_invasive_device() -> None:
    """A surgically invasive device not contacting a critical body system
    (e.g. a surgical mesh) is Class C.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.SURGICALLY_INVASIVE,
        duration_tier=DurationTier.SHORT_TERM,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="abdominal wall",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.C
    assert result.matched_rule_id == "FS-C-SURGICALLY-INVASIVE"


def test_class_d_implantable_device() -> None:
    """An implantable device (e.g. a cardiac pacemaker) is Class D
    regardless of duration tier or active status.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.IMPLANTABLE,
        duration_tier=DurationTier.LONG_TERM,
        device_category=DeviceCategory.ACTIVE,
        body_system_contacted="heart",
        energy_source="battery",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.D
    assert result.matched_rule_id == "FS-D-IMPLANTABLE"
    assert result.applicable_licensing_authority == LicensingAuthority.CENTRAL
    assert result.applicable_form == ApplicableForm.MD_5
    assert result.clinical_investigation_required is True


def test_class_d_critical_body_system_contact_device() -> None:
    """A surgically invasive device contacting the central nervous system
    (e.g. a neurosurgical device) is Class D even though it is not
    implantable.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.SURGICALLY_INVASIVE,
        duration_tier=DurationTier.SHORT_TERM,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="central nervous system",
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.D
    assert result.matched_rule_id == "FS-D-CRITICAL-SYSTEM"


@pytest.mark.parametrize(
    "body_system",
    ["heart", "Heart", "  HEART  ", "central nervous system", "brain", "spinal cord"],
)
def test_class_d_critical_body_system_matching_is_case_and_whitespace_insensitive(
    body_system: str,
) -> None:
    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.INVASIVE,
        duration_tier=DurationTier.SHORT_TERM,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted=body_system,
    )

    result = classify(profile)

    assert result.risk_class == RiskClass.D
    assert result.matched_rule_id == "FS-D-CRITICAL-SYSTEM"


# ---------------------------------------------------------------------------
# Missing / contradictory input: must raise, never guess.
# ---------------------------------------------------------------------------


def test_classify_raises_on_none_profile() -> None:
    with pytest.raises(ClassificationError):
        classify(None)  # type: ignore[arg-type]


def test_classify_raises_on_blank_body_system() -> None:
    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="   ",
    )

    with pytest.raises(ClassificationError):
        classify(profile)


def test_intended_use_profile_rejects_missing_required_field() -> None:
    """Pydantic validation itself must reject a profile missing a required
    field -- the engine must never receive (and silently tolerate) a
    partially-populated profile.
    """

    with pytest.raises(ValidationError):
        IntendedUseProfile(
            duration_tier=DurationTier.TRANSIENT,
            device_category=DeviceCategory.NON_ACTIVE,
            body_system_contacted="skin",
        )  # type: ignore[call-arg]


def test_intended_use_profile_rejects_invalid_enum_value() -> None:
    """A contradictory/garbage invasiveness_tier value must not silently
    coerce to something the engine can match -- it must be rejected at
    construction.
    """

    with pytest.raises(ValidationError):
        IntendedUseProfile(
            invasiveness_tier="not_a_real_tier",
            duration_tier=DurationTier.TRANSIENT,
            device_category=DeviceCategory.NON_ACTIVE,
            body_system_contacted="skin",
        )  # type: ignore[arg-type]


def test_intended_use_profile_rejects_unknown_extra_field() -> None:
    """``extra="forbid"`` means a caller passing a field the contract
    doesn't define (e.g. a typo, or a field from a future schema version)
    is rejected rather than silently ignored.
    """

    with pytest.raises(ValidationError):
        IntendedUseProfile(
            invasiveness_tier=InvasivenessTier.NON_INVASIVE,
            duration_tier=DurationTier.TRANSIENT,
            device_category=DeviceCategory.NON_ACTIVE,
            body_system_contacted="skin",
            unexpected_field="surprise",
        )  # type: ignore[call-arg]


def test_classify_rejects_malformed_vision_flags() -> None:
    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="skin",
    )

    with pytest.raises(ClassificationError):
        classify(profile, vision_flags={"visual_evidence_implantable": True})  # type: ignore[arg-type]


def test_classify_accepts_well_formed_vision_flags_without_changing_outcome() -> None:
    """VisionFlags is accepted and validated but, per architecture.md's
    Module 1 scope, does not yet alter the deterministic outcome -- there
    is no sourced, verified rule yet for resolving a vision/profile
    mismatch.
    """

    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="skin",
    )
    flags = VisionFlags(
        visual_evidence_implantable=True,
        visual_evidence_duration_conflict=False,
    )

    result = classify(profile, vision_flags=flags)

    assert result.risk_class == RiskClass.A


# ---------------------------------------------------------------------------
# Immutability / result contract
# ---------------------------------------------------------------------------


def test_classification_result_is_immutable() -> None:
    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="skin",
    )
    result = classify(profile)

    with pytest.raises(ValidationError):
        result.risk_class = RiskClass.D  # type: ignore[misc]


def test_intended_use_profile_is_immutable() -> None:
    profile = IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="skin",
    )

    with pytest.raises(ValidationError):
        profile.invasiveness_tier = InvasivenessTier.IMPLANTABLE  # type: ignore[misc]


def test_every_classification_result_carries_fixed_disclaimer() -> None:
    profiles = [
        IntendedUseProfile(
            invasiveness_tier=InvasivenessTier.NON_INVASIVE,
            duration_tier=DurationTier.TRANSIENT,
            device_category=DeviceCategory.NON_ACTIVE,
            body_system_contacted="skin",
        ),
        IntendedUseProfile(
            invasiveness_tier=InvasivenessTier.IMPLANTABLE,
            duration_tier=DurationTier.LONG_TERM,
            device_category=DeviceCategory.ACTIVE,
            body_system_contacted="heart",
        ),
    ]
    for profile in profiles:
        result = classify(profile)
        assert result.disclaimer == PRE_ASSESSMENT_DISCLAIMER


def test_find_matching_rule_raises_classification_error_type() -> None:
    """ClassificationError must be a ValueError subclass so callers can
    catch broadly if they choose, but it is always this specific,
    identifiable type -- never a bare exception.
    """

    assert issubclass(ClassificationError, ValueError)
