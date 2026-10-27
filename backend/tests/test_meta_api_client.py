"""Tests for the shared Meta Model API transport: app.meta_api.client.

Covers, per the Module 3 brief:

- The three named exception types (:class:`MetaModelAPIError`,
  :class:`MetaModelAPIResponseError`, :class:`MetaModelAPITimeoutError`),
  each correctly constructed from a mocked non-2xx response, a malformed
  2xx response, and a timeout, respectively.
- ``create_response()`` building exactly the request shape recorded in
  reports/module-2-report.md and correctly extracting ``output_text``.
- ``upload_file()`` building the documented multipart request and
  returning the parsed Files API response.

This is the one place in the test suite that mocks at the raw HTTP
layer (via respx against ``https://api.meta.ai/v1/...``); higher-level
callers (``app.vision.client``, intake parsing, report generation) mock
this already-tested shared client directly instead of re-mocking HTTP.

Every test in this file runs against mocked HTTP responses via respx
(see ``conftest.py``'s autouse ``no_real_network_calls`` fixture); none
of them reach api.meta.ai over the real network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.meta_api.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
    create_response,
    upload_file,
)

RESPONSES_URL = "https://api.meta.ai/v1/responses"
FILES_URL = "https://api.meta.ai/v1/files"


def _responses_body(output_text: str, model: str = "muse-spark-1.3") -> dict:
    """Build a representative non-streaming Responses API body, per the
    quickstart's documented response shape, recorded in
    reports/module-2-report.md.
    """

    return {
        "id": "resp_test123",
        "object": "response",
        "created_at": 1792177394,
        "status": "completed",
        "model": model,
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
# create_response: request shape and successful parsing
# ---------------------------------------------------------------------------


async def test_create_response_sends_documented_request_shape_and_returns_output_text(
    respx_router,
):
    route = respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_responses_body("hello from muse spark"))
    )

    result = await create_response(
        "muse-spark-1.3",
        [{"type": "input_text", "text": "What is in this image?"}],
    )

    assert result == "hello from muse spark"
    sent_body = route.calls.last.request.content
    payload = json.loads(sent_body)
    assert payload == {
        "model": "muse-spark-1.3",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "What is in this image?"}],
            }
        ],
    }


async def test_create_response_includes_text_format_when_given(respx_router):
    route = respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_responses_body("{}"))
    )

    schema = {"type": "json_schema", "name": "thing", "schema": {"type": "object"}}
    await create_response(
        "muse-spark-1.3",
        [{"type": "input_text", "text": "prompt"}],
        text_format=schema,
    )

    payload = json.loads(route.calls.last.request.content)
    assert payload["text"] == {"format": schema}


async def test_create_response_sends_bearer_auth_header(respx_router):
    route = respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_responses_body("ok"))
    )

    await create_response("muse-spark-1.3", [{"type": "input_text", "text": "hi"}])

    auth_header = route.calls.last.request.headers["Authorization"]
    assert auth_header.startswith("Bearer ")


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

    result = await upload_file(b"fake image bytes", "photo.png")

    assert result["id"] == "file-842549258569145"
    assert result["status"] == "uploaded"


async def test_upload_file_raises_response_error_when_id_missing(respx_router):
    respx_router.post(FILES_URL).mock(
        return_value=httpx.Response(200, json={"object": "file", "status": "uploaded"})
    )

    with pytest.raises(MetaModelAPIResponseError):
        await upload_file(b"fake image bytes", "photo.png")


# ---------------------------------------------------------------------------
# The three named exception types -- each constructed from a mocked
# non-2xx response, a malformed 2xx response, and a timeout respectively.
# ---------------------------------------------------------------------------


async def test_create_response_raises_api_error_on_non_2xx_response(respx_router):
    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(
            401,
            json=_error_envelope(
                "Incorrect API key provided.",
                "invalid_request_error",
                "invalid_api_key",
            ),
        )
    )

    with pytest.raises(MetaModelAPIError) as exc_info:
        await create_response("muse-spark-1.3", [{"type": "input_text", "text": "hi"}])

    assert exc_info.value.status_code == 401
    assert exc_info.value.message == "Incorrect API key provided."
    assert exc_info.value.error_type == "invalid_request_error"
    assert exc_info.value.error_code == "invalid_api_key"


async def test_create_response_raises_response_error_on_malformed_2xx_response(
    respx_router,
):
    """A 2xx response missing the documented 'output' field is malformed,
    not a guessable default.
    """

    respx_router.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, json={"id": "resp_test123", "status": "completed"})
    )

    with pytest.raises(MetaModelAPIResponseError):
        await create_response("muse-spark-1.3", [{"type": "input_text", "text": "hi"}])


async def test_create_response_raises_timeout_error_on_timeout(respx_router):
    respx_router.post(RESPONSES_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    with pytest.raises(MetaModelAPITimeoutError):
        await create_response("muse-spark-1.3", [{"type": "input_text", "text": "hi"}])


async def test_upload_file_raises_api_error_on_non_2xx_response(respx_router):
    respx_router.post(FILES_URL).mock(
        return_value=httpx.Response(
            400, json=_error_envelope("File too large.", "invalid_request_error")
        )
    )

    with pytest.raises(MetaModelAPIError) as exc_info:
        await upload_file(b"fake image bytes", "photo.png")

    assert exc_info.value.status_code == 400


async def test_upload_file_raises_response_error_on_malformed_2xx_response(respx_router):
    respx_router.post(FILES_URL).mock(return_value=httpx.Response(200, content=b"not json"))

    with pytest.raises(MetaModelAPIResponseError):
        await upload_file(b"fake image bytes", "photo.png")


async def test_upload_file_raises_timeout_error_on_timeout(respx_router):
    respx_router.post(FILES_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    with pytest.raises(MetaModelAPITimeoutError):
        await upload_file(b"fake image bytes", "photo.png")
