"""Tests for the vision pipeline: app.vision.client and app.vision.pipeline.

Covers, per the Module 2 brief:

- A successful end-to-end mocked extraction through
  ``extract_vision_flags`` (SAM segmentation + Muse Spark structured
  output, both mocked via respx).
- Each of the three named client exception cases
  (:class:`MetaModelAPIError`, :class:`MetaModelAPIResponseError`,
  :class:`MetaModelAPITimeoutError`) propagating out of
  ``extract_vision_flags`` uncaught.
- A regression run of Module 1's zero-I/O AST check against the
  classification package, confirming this module didn't weaken it.

Every test in this file runs against mocked HTTP responses via respx
(see ``conftest.py``'s autouse ``no_real_network_calls`` fixture); none
of them reach api.meta.ai or object storage over the real network.
"""

from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image

from app.classification.models import VisionFlags
from app.vision.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
    segment_concept,
    understand_image_structured,
    upload_file,
)
from app.vision.pipeline import extract_vision_flags

RESPONSES_URL = "https://api.meta.ai/v1/responses"
FILES_URL = "https://api.meta.ai/v1/files"


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


def _responses_body(output_text: str) -> dict:
    """Build a representative non-streaming Responses API body, per the
    quickstart's documented response shape.
    """

    return {
        "id": "resp_test123",
        "object": "response",
        "created_at": 1792177394,
        "status": "completed",
        "model": "sam-3.1",
        "output": [
            {
                "id": "msg_test123",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": output_text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
    }


def _error_envelope(message: str, error_type: str, code: str | None = None) -> dict:
    """Build the documented Meta Model API error envelope."""

    return {"error": {"message": message, "type": error_type, "param": None, "code": code}}


# ---------------------------------------------------------------------------
# segment_concept
# ---------------------------------------------------------------------------


async def test_segment_concept_parses_box_from_output_text(respx_router):
    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(
            200, json=_responses_body(_sam_output_text((10, 20, 110, 120, 200, 150)))
        )
    )

    boxes = await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")

    assert len(boxes) == 1
    assert boxes[0].x1 == 10
    assert boxes[0].y1 == 20
    assert boxes[0].x2 == 110
    assert boxes[0].y2 == 120
    assert boxes[0].w == 200
    assert boxes[0].h == 150


async def test_segment_concept_zero_matches_returns_empty_list(respx_router):
    """Per the SAM docs, zero matches is a valid outcome (empty output_text),
    not an error.
    """

    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_responses_body(""))
    )

    boxes = await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")

    assert boxes == []


# ---------------------------------------------------------------------------
# understand_image_structured
# ---------------------------------------------------------------------------


async def test_understand_image_structured_parses_json_output(respx_router):
    payload = {
        "visual_evidence_implantable": True,
        "visual_evidence_duration_conflict": False,
    }
    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(
            200, json=_responses_body(json.dumps(payload))
        )
    )

    result = await understand_image_structured(
        _sample_image_bytes(),
        "prompt",
        {"type": "object", "properties": {}},
        "vision_flags",
        model="muse-spark-1.3",
    )

    assert result == payload


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------


async def test_upload_file_returns_parsed_response(respx_router):
    respx_router.post(FILES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "file-842549258569145",
                "object": "file",
                "bytes": 245832,
                "created_at": 1714502400,
                "filename": "photo.png",
                "purpose": "user_data",
                "status": "uploaded",
            },
        )
    )

    result = await upload_file(_sample_image_bytes(), "photo.png")

    assert result["id"] == "file-842549258569145"
    assert result["status"] == "uploaded"


# ---------------------------------------------------------------------------
# Named exception cases (client layer)
# ---------------------------------------------------------------------------


async def test_segment_concept_raises_api_error_on_error_response(respx_router):
    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(
            401,
            json=_error_envelope(
                "Incorrect API key provided.", "invalid_request_error", "invalid_api_key"
            ),
        )
    )

    with pytest.raises(MetaModelAPIError) as exc_info:
        await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_code == "invalid_api_key"


async def test_segment_concept_raises_response_error_on_malformed_body(respx_router):
    """A 2xx response missing the documented 'output' field is malformed,
    not a guessable default.
    """

    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json={"id": "resp_test123", "status": "completed"})
    )

    with pytest.raises(MetaModelAPIResponseError):
        await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")


async def test_segment_concept_raises_timeout_error(respx_router):
    respx_router.post(RESPONSES_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    with pytest.raises(MetaModelAPITimeoutError):
        await segment_concept(_sample_image_bytes(), "medical device", model="sam-3.1")


async def test_understand_image_structured_raises_response_error_on_invalid_json(
    respx_router,
):
    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_responses_body("not valid json {"))
    )

    with pytest.raises(MetaModelAPIResponseError):
        await understand_image_structured(
            _sample_image_bytes(),
            "prompt",
            {"type": "object", "properties": {}},
            "vision_flags",
            model="muse-spark-1.3",
        )


# ---------------------------------------------------------------------------
# extract_vision_flags: end-to-end success and exception propagation
# ---------------------------------------------------------------------------


async def test_extract_vision_flags_end_to_end_success(respx_router):
    """SAM finds a box, Muse Spark returns structured flags on the crop."""

    sam_route = respx_router.post(RESPONSES_URL)
    sam_route.mock(
        side_effect=[
            httpx.Response(
                200,
                json=_responses_body(
                    _sam_output_text((10, 10, 100, 100, 200, 150))
                ),
            ),
            httpx.Response(
                200,
                json=_responses_body(
                    json.dumps(
                        {
                            "visual_evidence_implantable": False,
                            "visual_evidence_duration_conflict": None,
                        }
                    )
                ),
            ),
        ]
    )

    result = await extract_vision_flags(_sample_image_bytes())

    assert isinstance(result, VisionFlags)
    assert result.visual_evidence_implantable is False
    assert result.visual_evidence_duration_conflict is None
    assert sam_route.call_count == 2


async def test_extract_vision_flags_no_segmentation_match_still_calls_muse_spark(
    respx_router,
):
    """Zero SAM matches is not an error: the pipeline falls back to the
    full image for the Muse Spark call rather than failing.
    """

    route = respx_router.post(RESPONSES_URL)
    route.mock(
        side_effect=[
            httpx.Response(200, json=_responses_body("")),
            httpx.Response(
                200,
                json=_responses_body(
                    json.dumps(
                        {
                            "visual_evidence_implantable": None,
                            "visual_evidence_duration_conflict": None,
                        }
                    )
                ),
            ),
        ]
    )

    result = await extract_vision_flags(_sample_image_bytes())

    assert result.visual_evidence_implantable is None
    assert result.visual_evidence_duration_conflict is None
    assert route.call_count == 2


async def test_extract_vision_flags_propagates_api_error_from_sam_step(respx_router):
    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(
            500, json=_error_envelope("Internal server error.", "server_error")
        )
    )

    with pytest.raises(MetaModelAPIError):
        await extract_vision_flags(_sample_image_bytes())


async def test_extract_vision_flags_propagates_response_error_from_muse_spark_step(
    respx_router,
):
    route = respx_router.post(RESPONSES_URL)
    route.mock(
        side_effect=[
            httpx.Response(200, json=_responses_body("")),
            httpx.Response(200, json=_responses_body("not valid json {")),
        ]
    )

    with pytest.raises(MetaModelAPIResponseError):
        await extract_vision_flags(_sample_image_bytes())


async def test_extract_vision_flags_propagates_timeout_error(respx_router):
    respx_router.post(RESPONSES_URL).mock(side_effect=httpx.TimeoutException("timed out"))

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
