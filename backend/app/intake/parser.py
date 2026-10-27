"""Intake parsing: free-text device descriptions -> IntendedUseProfile.

Owns ``parse_intended_use``, the one function this module is
responsible for. It calls Muse Spark (via the shared
``app.meta_api.client.create_response``) with a JSON Schema matching
:class:`~app.classification.models.IntendedUseProfile`'s four required
fields (``invasiveness_tier``, ``duration_tier``, ``device_category``,
``body_system_contacted``) plus the nullable ``energy_source``.

The single most important behavior of this function: it raises
:class:`IntakeParsingError` -- never guesses or defaults a value --
whenever Muse Spark's output does not confidently resolve one of the
four required fields to a valid enum value, or leaves a required field
ambiguous or null. This mirrors the "raise rather than guess"
discipline Module 1's classification engine (``ClassificationError``
on missing/contradictory input) and Module 2's vision pipeline (``None``
rather than a guessed boolean) already established.

This module does not implement the vision-versus-profile cross-check
from architecture.md's data flow step 4 -- that decision belongs in
Module 4, where both VisionFlags and IntendedUseProfile exist in the
same request for the first time. See reports/module-3-report.md for why
that's explicitly left out here rather than half-built.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.classification.models import (
    DeviceCategory,
    DurationTier,
    IntendedUseProfile,
    InvasivenessTier,
)
from app.meta_api.client import MetaModelAPIResponseError, create_response
from app.security.secrets import load_muse_spark_model_id

#: Required-field enum values, read directly from the classification
#: package's own enums so this schema can never drift from
#: IntendedUseProfile's actual accepted values.
_INVASIVENESS_TIER_VALUES = [member.value for member in InvasivenessTier]
_DURATION_TIER_VALUES = [member.value for member in DurationTier]
_DEVICE_CATEGORY_VALUES = [member.value for member in DeviceCategory]

#: JSON Schema for the Muse Spark structured-output call. Every
#: required field is nullable in the schema itself (``"null"`` is in
#: each type union) so the model can honestly report "not stated" or
#: "ambiguous" via null rather than being forced to emit some enum
#: value. parse_intended_use() treats a null required field as a
#: parsing failure, not a default.
_INTENDED_USE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "invasiveness_tier": {
            "type": ["string", "null"],
            "enum": [*_INVASIVENESS_TIER_VALUES, None],
            "description": (
                "How far the device crosses the body's surface. One of: "
                + ", ".join(_INVASIVENESS_TIER_VALUES)
                + ". Null if the description does not clearly state or "
                "imply one of these values."
            ),
        },
        "duration_tier": {
            "type": ["string", "null"],
            "enum": [*_DURATION_TIER_VALUES, None],
            "description": (
                "How long the device maintains contact. One of: "
                + ", ".join(_DURATION_TIER_VALUES)
                + ". Null if the description does not clearly state or "
                "imply one of these values."
            ),
        },
        "device_category": {
            "type": ["string", "null"],
            "enum": [*_DEVICE_CATEGORY_VALUES, None],
            "description": (
                "Whether the device relies on a power/energy source other "
                "than the body. One of: "
                + ", ".join(_DEVICE_CATEGORY_VALUES)
                + ". Null if the description does not clearly state or "
                "imply one of these values."
            ),
        },
        "body_system_contacted": {
            "type": ["string", "null"],
            "description": (
                "The body system, organ, or tissue the device contacts "
                "(e.g. 'skin', 'heart', 'urinary tract'). Null if the "
                "description does not state which body system is "
                "contacted."
            ),
        },
        "energy_source": {
            "type": ["string", "null"],
            "description": (
                "The device's energy source (e.g. 'battery', 'mains'), if "
                "any. Null if the device has no energy source or none is "
                "mentioned -- this field is allowed to stay null."
            ),
        },
    },
    "required": [
        "invasiveness_tier",
        "duration_tier",
        "device_category",
        "body_system_contacted",
        "energy_source",
    ],
}

_INTENDED_USE_PROFILE_PROMPT = (
    "Read this plain-language description of a medical device's intended "
    "use and extract four required facts, plus one optional fact, as "
    "structured data. Only state a value you are confident about from the "
    "text; if the text does not clearly state or strongly imply a value "
    "for invasiveness_tier, duration_tier, device_category, or "
    "body_system_contacted, return null for that field rather than "
    "guessing. Do not infer a value from what is typical for a device "
    "category if the text itself does not say so.\n\n"
    "Description: {description}"
)


class IntakeParsingError(ValueError):
    """Raised when Muse Spark's output does not confidently resolve a
    required :class:`IntendedUseProfile` field, or the model's response
    could not be parsed at all.

    Never caught internally to fall back to a guessed or default
    profile -- callers decide how to surface this.
    """


async def parse_intended_use(description: str) -> IntendedUseProfile:
    """Parse a free-text intended-use description into a structured
    :class:`IntendedUseProfile`.

    Calls Muse Spark with a JSON Schema matching ``IntendedUseProfile``'s
    four required fields plus the nullable ``energy_source``. Raises
    :class:`IntakeParsingError` if any required field comes back null,
    missing, or outside the field's valid enum values -- this function
    never guesses or defaults a value for a required field.

    Propagates :class:`~app.meta_api.client.MetaModelAPIError`,
    :class:`~app.meta_api.client.MetaModelAPIResponseError`, or
    :class:`~app.meta_api.client.MetaModelAPITimeoutError` from the
    underlying model call, unchanged.
    """

    if not description or not description.strip():
        raise IntakeParsingError(
            "description is required and cannot be blank; the parser will "
            "not guess an intended-use profile from no input."
        )

    model = load_muse_spark_model_id()

    output_text = await create_response(
        model,
        [
            {
                "type": "input_text",
                "text": _INTENDED_USE_PROFILE_PROMPT.format(description=description),
            }
        ],
        text_format={
            "type": "json_schema",
            "name": "intended_use_profile",
            "schema": _INTENDED_USE_PROFILE_SCHEMA,
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

    required_fields = (
        "invasiveness_tier",
        "duration_tier",
        "device_category",
        "body_system_contacted",
    )
    for field_name in required_fields:
        value = parsed.get(field_name)
        if value is None:
            raise IntakeParsingError(
                f"Could not confidently resolve required field "
                f"'{field_name}' from the description; Muse Spark "
                f"returned null (ambiguous or not stated), and this "
                f"parser will not guess a value. Description: "
                f"{description!r}"
            )
        if not isinstance(value, str) or not value.strip():
            raise IntakeParsingError(
                f"Required field '{field_name}' resolved to a non-string "
                f"or blank value ({value!r}); refusing to guess."
            )

    energy_source = parsed.get("energy_source")
    if energy_source is not None and not isinstance(energy_source, str):
        raise IntakeParsingError(
            f"Optional field 'energy_source' resolved to a non-string, "
            f"non-null value ({energy_source!r})."
        )

    try:
        return IntendedUseProfile(
            invasiveness_tier=parsed["invasiveness_tier"],
            duration_tier=parsed["duration_tier"],
            device_category=parsed["device_category"],
            body_system_contacted=parsed["body_system_contacted"],
            energy_source=energy_source,
        )
    except ValidationError as exc:
        # A required field resolved to a string, but not one of the
        # enum's valid values (e.g. the model returned a value outside
        # the schema's enum constraint). Still a parsing failure, never
        # a guess at a substitute valid value.
        raise IntakeParsingError(
            f"Muse Spark returned a value that is not a valid "
            f"IntendedUseProfile field: {exc}"
        ) from exc
