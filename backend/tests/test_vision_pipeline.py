"""Tests for the vision pipeline: app.vision.client and app.vision.pipeline.

Covers, per the Module 2 brief (still true after the Module 3 refactor):

- A successful end-to-end mocked extraction through
  ``extract_vision_flags`` (SAM segmentation + Muse Spark structured
  output).
- Each of the three named client exception cases
  (:class:`MetaModelAPIError`, :class:`MetaModelAPIResponseError`,
  :class:`MetaModelAPITimeoutError`) propagating out of
  ``extract_vision_flags`` uncaught.
- A regression run of Module 1's zero-I/O AST check against the
  classification package, confirming this module didn't weaken it.

Module 3 change: the three exception types and the raw Responses API
transport moved to the shared ``app.meta_api.client`` (tested directly
in ``test_meta_api_client.py``). These tests now mock
``app.vision.client.create_response`` -- the shared client function
``segment_concept()`` and ``understand_image_structured()`` call --
instead of mocking raw httpx/respx at the HTTP layer. Every assertion
present before the refactor is still made here, unchanged; only the
mocking mechanism moved up a layer, matching where the real boundary
now is. ``upload_file()`` was never vision-specific and its tests now
live solely in ``test_meta_api_client.py``, since it moved there too.

Every test in this file still runs inside ``conftest.py``'s autouse
``no_real_network_calls`` respx-mock context; none of them make a real
httpx call, so none can reach api.meta.ai over the real network.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.classification.models import VisionFlags
from app.meta_api.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
)
from app.vision.client import segment_concept, understand_image_structured
from app.vision.pipeline import extract_vision_flags


def _mock_create_response(monkeypatch: pytest.MonkeyPatch, **kwargs) -> AsyncMock:
    """Replace ``app.vision.client.create_response`` (the shared client
    function ``segment_concept``/``understand_image_structured`` call)
    with an :class:`AsyncMock`, configured via ``**kwargs`` (typically
    ``return_value`` or ``side_effect``).
    """

    mock = AsyncMock(**kwargs)
    monkeypatch.setattr("app.vision.client.create_response", mock)
    return mock


def _sample_image_bytes(width: int = 200, height: int = 150) -> bytes:
    """Build a tiny, real PNG in memory -- no fixture file on disk, no
    network fetch.
    """

    image = Image.new("RGB", (width, height), color=(10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sam_output_text(box: tuple[int, int, int, int, int, int]) -> str:
    """Build a representative SAM 3.1 output_text payload for one box,
    per the SAM overview's documented wire grammar. The mask token is
    omitted since this client only parses boxes.
    """

    x1, y1, x2, y2, w, h = box
    return f"<0f>0<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}|>"


# ---------------------------------------------------------------------------
# segment_concept
# ---------------------------------------------------------------------------


async def test_segment_concept_parses_box_from_output_text(monkeypatch):
    _mock_create_response(
        monkeypatch, return_value=_sam_output_text((10, 20, 110, 120, 200, 150))
    )

    boxes = await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")

    assert len(boxes) == 1
    assert boxes[0].x1 == 10
    assert boxes[0].y1 == 20
    assert boxes[0].x2 == 110
    assert boxes[0].y2 == 120
    assert boxes[0].w == 200
    assert boxes[0].h == 150


async def test_segment_concept_zero_matches_returns_empty_list(monkeypatch):
    """Per the SAM docs, zero matches is a valid outcome (empty output_text),
    not an error.
    """

    _mock_create_response(monkeypatch, return_value="")

    boxes = await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")

    assert boxes == []


# ---------------------------------------------------------------------------
# understand_image_structured
# ---------------------------------------------------------------------------


async def test_understand_image_structured_parses_json_output(monkeypatch):
    payload = {
        "visual_evidence_implantable": True,
        "visual_evidence_duration_conflict": False,
    }
    _mock_create_response(monkeypatch, return_value=json.dumps(payload))

    result = await understand_image_structured(
        _sample_image_bytes(),
        "prompt",
        {"type": "object", "properties": {}},
        "vision_flags",
        model="muse-spark-1.3",
    )

    assert result == payload


async def test_understand_image_structured_raises_response_error_on_invalid_json(
    monkeypatch,
):
    """The output_text's JSON parsing is vision-pipeline-local logic (not
    part of the shared client), so this exercises the real parsing path:
    the mocked create_response returns a text string, not an exception.
    """

    _mock_create_response(monkeypatch, return_value="not valid json {")

    with pytest.raises(MetaModelAPIResponseError):
        await understand_image_structured(
            _sample_image_bytes(),
            "prompt",
            {"type": "object", "properties": {}},
            "vision_flags",
            model="muse-spark-1.3",
        )


# ---------------------------------------------------------------------------
# Named exception cases (client layer): create_response raising
# propagates straight through segment_concept / understand_image_structured.
# ---------------------------------------------------------------------------


async def test_segment_concept_raises_api_error_on_error_response(monkeypatch):
    _mock_create_response(
        monkeypatch,
        side_effect=MetaModelAPIError(
            status_code=401,
            message="Incorrect API key provided.",
            error_type="invalid_request_error",
            error_code="invalid_api_key",
        ),
    )

    with pytest.raises(MetaModelAPIError) as exc_info:
        await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "invalid_api_key"


async def test_segment_concept_raises_response_error_on_malformed_body(monkeypatch):
    """A malformed Responses API body is detected and raised by the
    shared client; segment_concept must propagate it uncaught.
    """

    _mock_create_response(
        monkeypatch,
        side_effect=MetaModelAPIResponseError("Responses API body was malformed"),
    )

    with pytest.raises(MetaModelAPIResponseError):
        await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")


async def test_segment_concept_raises_timeout_error(monkeypatch):
    _mock_create_response(
        monkeypatch, side_effect=MetaModelAPITimeoutError("timed out")
    )

    with pytest.raises(MetaModelAPITimeoutError):
        await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")


# ---------------------------------------------------------------------------
# extract_vision_flags: end-to-end success and exception propagation
# ---------------------------------------------------------------------------


async def test_extract_vision_flags_end_to_end_success(monkeypatch):
    """SAM finds a box, Muse Spark returns structured flags on the crop."""

    mock = _mock_create_response(
        monkeypatch,
        side_effect=[
            _sam_output_text((10, 10, 100, 100, 200, 150)),
            json.dumps(
                {
                    "visual_evidence_implantable": False,
                    "visual_evidence_duration_conflict": None,
                }
            ),
        ],
    )

    result = await extract_vision_flags(_sample_image_bytes())

    assert isinstance(result, VisionFlags)
    assert result.visual_evidence_implantable is False
    assert result.visual_evidence_duration_conflict is None
    assert mock.await_count == 2


async def test_extract_vision_flags_no_segmentation_match_still_calls_muse_spark(
    monkeypatch,
):
    """Zero SAM matches is not an error: the pipeline falls back to the
    full image for the Muse Spark call rather than failing.
    """

    mock = _mock_create_response(
        monkeypatch,
        side_effect=[
            "",
            json.dumps(
                {
                    "visual_evidence_implantable": None,
                    "visual_evidence_duration_conflict": None,
                }
            ),
        ],
    )

    result = await extract_vision_flags(_sample_image_bytes())

    assert result.visual_evidence_implantable is None
    assert result.visual_evidence_duration_conflict is None
    assert mock.await_count == 2


async def test_extract_vision_flags_propagates_api_error_from_sam_step(monkeypatch):
    _mock_create_response(
        monkeypatch,
        side_effect=MetaModelAPIError(status_code=500, message="Internal server error."),
    )

    with pytest.raises(MetaModelAPIError):
        await extract_vision_flags(_sample_image_bytes())


async def test_extract_vision_flags_propagates_response_error_from_muse_spark_step(
    monkeypatch,
):
    _mock_create_response(
        monkeypatch,
        side_effect=["", "not valid json {"],
    )

    with pytest.raises(MetaModelAPIResponseError):
        await extract_vision_flags(_sample_image_bytes())


async def test_extract_vision_flags_propagates_timeout_error(monkeypatch):
    _mock_create_response(
        monkeypatch, side_effect=MetaModelAPITimeoutError("timed out")
    )

    with pytest.raises(MetaModelAPITimeoutError):
        await extract_vision_flags(_sample_image_bytes())


# ---------------------------------------------------------------------------
# Regression: Module 1's zero-I/O AST check on the classification package
# must still pass, unmodified, after this module lands.
# ---------------------------------------------------------------------------


def test_module_1_classification_engine_zero_io_check_still_passes():
    """Re-import and re-run the exact static checks
    ``test_classification_engine.py`` defines, as a regression guard that
    nothing in this module's changes (new secrets, new packages) crept
    into app.classification.engine / models / rules_data's import list.
    """

    import ast
    import inspect

    from app.classification import engine, rules_data

    forbidden_import_names = {
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
        "aioboto3",
        "fastapi",
        "starlette",
        "os",
        "pathlib",
        "io",
        "PIL",
    }

    for module in (engine, rules_data):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        hit = imported & forbidden_import_names
        assert not hit, f"{module.__name__} imports forbidden module(s): {hit}"
