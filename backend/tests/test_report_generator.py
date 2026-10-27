"""Tests for report generation: app.report.generator.

Covers, per the Module 3 brief:

- One successful generation confirming the disclaimer is present
  verbatim even when the mocked model response omits it.
- A model-call failure propagating as one of the three named exceptions.

Every test mocks ``app.report.generator.create_response`` (the shared
client function this module calls) rather than making any real network
call; see ``conftest.py``'s autouse ``no_real_network_calls`` fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.classification.models import (
    PRE_ASSESSMENT_DISCLAIMER,
    ApplicableForm,
    ClassificationResult,
    LicensingAuthority,
    RiskClass,
)
from app.meta_api.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
)
from app.report.generator import generate_report


def _mock_create_response(monkeypatch: pytest.MonkeyPatch, **kwargs) -> AsyncMock:
    mock = AsyncMock(**kwargs)
    monkeypatch.setattr("app.report.generator.create_response", mock)
    return mock


def _sample_result(**overrides) -> ClassificationResult:
    fields = {
        "risk_class": RiskClass.D,
        "matched_rule_id": "FS-D-IMPLANTABLE",
        "applicable_licensing_authority": LicensingAuthority.CENTRAL,
        "applicable_form": ApplicableForm.MD_5,
        "clinical_investigation_required": True,
        "clinical_investigation_basis": (
            "Implantable devices carry long-term risk exposure; a "
            "clinical investigation basis is required."
        ),
    }
    fields.update(overrides)
    return ClassificationResult(**fields)


# ---------------------------------------------------------------------------
# Successful generation: disclaimer always present verbatim
# ---------------------------------------------------------------------------


async def test_generate_report_appends_disclaimer_verbatim_when_model_omits_it(
    monkeypatch,
):
    """The mocked model response deliberately contains no disclaimer-like
    text at all -- the function must still append the real disclaimer,
    byte-exact, every time.
    """

    _mock_create_response(
        monkeypatch,
        return_value=(
            "This implantable device is Class D, requiring Central "
            "licensing authority approval via Form MD-5 and a clinical "
            "investigation."
        ),
    )

    result = _sample_result()
    report = await generate_report(result)

    assert report.endswith(result.disclaimer)
    assert PRE_ASSESSMENT_DISCLAIMER in report
    # The disclaimer appears exactly once, exactly as the fixed string.
    assert report.count(PRE_ASSESSMENT_DISCLAIMER) == 1


async def test_generate_report_disclaimer_is_byte_exact_even_with_model_disclaimer_text(
    monkeypatch,
):
    """Even if the model's own prose includes paraphrased disclaimer-like
    language, the appended disclaimer is still the exact fixed string --
    this function never relies on or trusts the model's version.
    """

    _mock_create_response(
        monkeypatch,
        return_value=(
            "This is just an estimate, not an official determination, "
            "please consult a regulator."
        ),
    )

    result = _sample_result()
    report = await generate_report(result)

    # The real disclaimer string appears verbatim, appended after the
    # model's own (different) disclaimer-like text.
    assert report.endswith(result.disclaimer)
    assert result.disclaimer == PRE_ASSESSMENT_DISCLAIMER


async def test_generate_report_includes_model_prose_before_disclaimer(monkeypatch):
    _mock_create_response(monkeypatch, return_value="Prose about the result.")

    result = _sample_result()
    report = await generate_report(result)

    assert report.startswith("Prose about the result.")
    assert report == f"Prose about the result.\n\n{result.disclaimer}"


# ---------------------------------------------------------------------------
# Model-call failure propagates as one of the three named exceptions
# ---------------------------------------------------------------------------


async def test_generate_report_propagates_api_error(monkeypatch):
    _mock_create_response(
        monkeypatch,
        side_effect=MetaModelAPIError(status_code=500, message="Internal server error."),
    )

    with pytest.raises(MetaModelAPIError):
        await generate_report(_sample_result())


async def test_generate_report_propagates_response_error(monkeypatch):
    _mock_create_response(
        monkeypatch,
        side_effect=MetaModelAPIResponseError("Responses API body was malformed"),
    )

    with pytest.raises(MetaModelAPIResponseError):
        await generate_report(_sample_result())


async def test_generate_report_propagates_timeout_error(monkeypatch):
    _mock_create_response(monkeypatch, side_effect=MetaModelAPITimeoutError("timed out"))

    with pytest.raises(MetaModelAPITimeoutError):
        await generate_report(_sample_result())
