"""Tests for intake parsing: app.intake.parser.

Covers, per the Module 3 brief:

- One successful parse.
- One case per required field (invasiveness_tier, duration_tier,
  device_category, body_system_contacted) where the mocked model output
  is ambiguous (null) or invalid, each causing :class:`IntakeParsingError`.
- A malformed-JSON model response propagating
  :class:`MetaModelAPIResponseError`.

Every test mocks ``app.intake.parser.create_response`` (the shared
client function this module calls) rather than making any real network
call; see ``conftest.py``'s autouse ``no_real_network_calls`` fixture.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.classification.models import (
    DeviceCategory,
    DurationTier,
    IntendedUseProfile,
    InvasivenessTier,
)
from app.intake.parser import IntakeParsingError, parse_intended_use
from app.meta_api.client import MetaModelAPIResponseError


def _mock_create_response(monkeypatch: pytest.MonkeyPatch, **kwargs) -> AsyncMock:
    mock = AsyncMock(**kwargs)
    monkeypatch.setattr("app.intake.parser.create_response", mock)
    return mock


def _full_fields(**overrides) -> dict:
    fields = {
        "invasiveness_tier": "non_invasive",
        "duration_tier": "transient",
        "device_category": "non_active",
        "body_system_contacted": "skin",
        "energy_source": None,
    }
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# Successful parse
# ---------------------------------------------------------------------------


async def test_parse_intended_use_success(monkeypatch):
    _mock_create_response(
        monkeypatch,
        return_value=json.dumps(
            _full_fields(
                invasiveness_tier="invasive",
                duration_tier="short_term",
                device_category="active",
                body_system_contacted="urinary tract",
                energy_source="battery",
            )
        ),
    )

    profile = await parse_intended_use(
        "A battery-powered catheter inserted into the urinary tract for a few days."
    )

    assert isinstance(profile, IntendedUseProfile)
    assert profile.invasiveness_tier == InvasivenessTier.INVASIVE
    assert profile.duration_tier == DurationTier.SHORT_TERM
    assert profile.device_category == DeviceCategory.ACTIVE
    assert profile.body_system_contacted == "urinary tract"
    assert profile.energy_source == "battery"


async def test_parse_intended_use_success_with_null_energy_source(monkeypatch):
    """energy_source is the one nullable field; null must be accepted,
    not treated as a parsing failure.
    """

    _mock_create_response(monkeypatch, return_value=json.dumps(_full_fields()))

    profile = await parse_intended_use("A simple elastic bandage.")

    assert profile.energy_source is None


# ---------------------------------------------------------------------------
# Blank input: raise, never guess
# ---------------------------------------------------------------------------


async def test_parse_intended_use_raises_on_blank_description(monkeypatch):
    mock = _mock_create_response(monkeypatch, return_value=json.dumps(_full_fields()))

    with pytest.raises(IntakeParsingError):
        await parse_intended_use("   ")

    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# One ambiguous/invalid case per required field -> IntakeParsingError
# ---------------------------------------------------------------------------


async def test_parse_intended_use_raises_when_invasiveness_tier_is_null(monkeypatch):
    _mock_create_response(
        monkeypatch, return_value=json.dumps(_full_fields(invasiveness_tier=None))
    )

    with pytest.raises(IntakeParsingError):
        await parse_intended_use("A device that does something to the body, somehow.")


async def test_parse_intended_use_raises_when_duration_tier_is_null(monkeypatch):
    _mock_create_response(
        monkeypatch, return_value=json.dumps(_full_fields(duration_tier=None))
    )

    with pytest.raises(IntakeParsingError):
        await parse_intended_use("A device used for an unspecified length of time.")


async def test_parse_intended_use_raises_when_device_category_is_null(monkeypatch):
    _mock_create_response(
        monkeypatch, return_value=json.dumps(_full_fields(device_category=None))
    )

    with pytest.raises(IntakeParsingError):
        await parse_intended_use("A device of unclear power status.")


async def test_parse_intended_use_raises_when_body_system_contacted_is_null(
    monkeypatch,
):
    _mock_create_response(
        monkeypatch, return_value=json.dumps(_full_fields(body_system_contacted=None))
    )

    with pytest.raises(IntakeParsingError):
        await parse_intended_use("A device that touches... something.")


async def test_parse_intended_use_raises_when_invasiveness_tier_is_invalid_enum_value(
    monkeypatch,
):
    """The model returning a string that isn't one of the valid enum
    values is still a parsing failure -- IntendedUseProfile's own
    validation rejects it, and that ValidationError becomes
    IntakeParsingError rather than a substituted default.
    """

    _mock_create_response(
        monkeypatch,
        return_value=json.dumps(_full_fields(invasiveness_tier="somewhat_invasive")),
    )

    with pytest.raises(IntakeParsingError):
        await parse_intended_use("A device that is sort of invasive, kind of.")


# ---------------------------------------------------------------------------
# Malformed JSON from the model -> MetaModelAPIResponseError propagates
# ---------------------------------------------------------------------------


async def test_parse_intended_use_propagates_response_error_on_malformed_json(
    monkeypatch,
):
    _mock_create_response(monkeypatch, return_value="not valid json {")

    with pytest.raises(MetaModelAPIResponseError):
        await parse_intended_use("A normal device description.")


async def test_parse_intended_use_propagates_response_error_on_non_object_json(
    monkeypatch,
):
    _mock_create_response(monkeypatch, return_value=json.dumps(["not", "an", "object"]))

    with pytest.raises(MetaModelAPIResponseError):
        await parse_intended_use("A normal device description.")
