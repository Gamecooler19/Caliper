"""Raw Meta Model API HTTP calls.

This module owns every direct HTTP call to the Meta Model API
(``https://api.meta.ai/v1``) made by the vision pipeline: SAM 3.1
segmentation and Muse Spark multimodal understanding, both over
``POST /v1/responses``, plus ``POST /v1/files`` for the Files API.

Implemented against Meta's live documentation at ai.developer.meta.com
(redirecting to dev.meta.ai) as of 2026-10-27:
  - Quickstart / "Build with Meta Model API" overview
  - Media segmentation guide and SAM overview/segmenting-with-prompts guide
  - Image understanding guide
  - File handling guide (inline files and the Files API upload response)
  - Structured output guide (``text.format`` for the Responses API)
  - Error handling guide (the ``{"error": {...}}`` envelope)

See reports/module-2-report.md for the exact request/response shapes this
module was built against, recorded there so the schema is on record
rather than only living in Meta's docs.

Uses httpx as the async HTTP client, per the existing async stack. Every
public function raises one of three named exceptions on failure --
:class:`MetaModelAPIError` (non-2xx error response), a malformed/
unexpected response shape (:class:`MetaModelAPIResponseError`), or a
request timeout (:class:`MetaModelAPITimeoutError`) -- never crashes
unhandled and never silently returns a default value.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.security.secrets import load_meta_model_api_key

BASE_URL = "https://api.meta.ai/v1"
RESPONSES_PATH = "/responses"
FILES_PATH = "/files"

DEFAULT_TIMEOUT_SECONDS = 30.0

# Grammar for one SAM 3.1 box record in the output_text special-token
# stream, per the SAM overview / segmenting-with-prompts guides:
#   <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>
_SAM_BOX_PATTERN = re.compile(
    r"<\|box;x1=(?P<x1>\d+);y1=(?P<y1>\d+);x2=(?P<x2>\d+);y2=(?P<y2>\d+);"
    r"w=(?P<w>\d+);h=(?P<h>\d+)\|>"
)


class MetaModelAPIError(RuntimeError):
    """The Meta Model API returned a non-2xx error response.

    Carries the HTTP status code and, when present, the error envelope's
    ``message``, ``type``, and ``code`` fields, per the Error handling
    guide's documented ``{"error": {"message", "type", "param", "code"}}``
    shape.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        error_type: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.error_code = error_code
        super().__init__(
            f"Meta Model API returned HTTP {status_code}: {message} "
            f"(type={error_type!r}, code={error_code!r})"
        )


class MetaModelAPIResponseError(RuntimeError):
    """The Meta Model API returned 2xx but the response body was malformed
    or did not match the shape this client expects (missing fields,
    unparsable structured-output JSON, no message item, etc.).
    """


class MetaModelAPITimeoutError(RuntimeError):
    """The request to the Meta Model API timed out."""


def _image_to_data_url(image_bytes: bytes, content_type: str = "image/png") -> str:
    """Encode raw image bytes as a base64 data URL, per the File handling
    guide's "Send it inline" path (``image_url`` as a base64 data URL).
    """

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _raise_for_error_envelope(response: httpx.Response) -> None:
    """Raise :class:`MetaModelAPIError` if ``response`` is a non-2xx error,
    parsing the documented ``{"error": {...}}`` envelope when present.
    """

    if response.status_code // 100 == 2:
        return

    message = response.text
    error_type: str | None = None
    error_code: str | None = None
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            message = error.get("message", message)
            error_type = error.get("type")
            error_code = error.get("code")
    except ValueError:
        pass  # body wasn't JSON; fall back to raw text as the message

    raise MetaModelAPIError(
        status_code=response.status_code,
        message=message,
        error_type=error_type,
        error_code=error_code,
    )


async def _post_json(
    path: str, payload: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """POST a JSON body to the Meta Model API and return the parsed JSON
    response body.

    Raises :class:`MetaModelAPITimeoutError` on timeout,
    :class:`MetaModelAPIError` on a non-2xx response, and
    :class:`MetaModelAPIResponseError` if the 2xx body isn't valid JSON.
    """

    api_key = load_meta_model_api_key()

    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as client:
            response = await client.post(
                path,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.TimeoutException as exc:
        raise MetaModelAPITimeoutError(
            f"Request to {BASE_URL}{path} timed out after {timeout}s"
        ) from exc

    _raise_for_error_envelope(response)

    try:
        return response.json()
    except ValueError as exc:
        raise MetaModelAPIResponseError(
            f"Meta Model API returned a 2xx response from {path} with a "
            f"non-JSON body: {response.text[:200]!r}"
        ) from exc


async def upload_file(
    data: bytes, filename: str, purpose: str = "user_data"
) -> dict[str, Any]:
    """Upload a file through the Files API (``POST /v1/files``).

    Returns the parsed response body, documented by the File handling
    guide as::

        {
          "id": "file-842549258569145",
          "object": "file",
          "bytes": 245832,
          "created_at": 1714502400,
          "filename": "document.pdf",
          "purpose": "user_data",
          "status": "uploaded"
        }

    Raises :class:`MetaModelAPITimeoutError`, :class:`MetaModelAPIError`,
    or :class:`MetaModelAPIResponseError` on failure.
    """

    api_key = load_meta_model_api_key()

    try:
        async with httpx.AsyncClient(
            base_url=BASE_URL, timeout=DEFAULT_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                FILES_PATH,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"purpose": purpose},
                files={"file": (filename, data)},
            )
    except httpx.TimeoutException as exc:
        raise MetaModelAPITimeoutError(
            f"Request to {BASE_URL}{FILES_PATH} timed out after "
            f"{DEFAULT_TIMEOUT_SECONDS}s"
        ) from exc

    _raise_for_error_envelope(response)

    try:
        body = response.json()
    except ValueError as exc:
        raise MetaModelAPIResponseError(
            f"Meta Model API returned a 2xx response from {FILES_PATH} with "
            f"a non-JSON body: {response.text[:200]!r}"
        ) from exc

    if not isinstance(body, dict) or "id" not in body:
        raise MetaModelAPIResponseError(
            f"Meta Model API file upload response was missing the expected "
            f"'id' field: {body!r}"
        )

    return body


def _first_message_output_text(response_body: dict[str, Any]) -> str:
    """Extract the ``output_text`` string from a Responses API response
    body's first assistant message item.

    Per the quickstart's documented response shape, ``output`` is a list
    of items; the message item has ``type: "message"`` and its
    ``content`` list carries an ``output_text`` part with a ``text``
    field. Raises :class:`MetaModelAPIResponseError` if that shape isn't
    present.
    """

    output = response_body.get("output")
    if not isinstance(output, list):
        raise MetaModelAPIResponseError(
            f"Responses API body was missing a list 'output' field: "
            f"{response_body!r}"
        )

    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    return text

    raise MetaModelAPIResponseError(
        f"Responses API body had no message item with an output_text part: "
        f"{response_body!r}"
    )


@dataclass(frozen=True)
class SamBox:
    """One bounding box from a SAM 3.1 segmentation match, in source
    pixel coordinates (``w``/``h`` are the source media's own pixel
    size, per the SAM segmenting-with-prompts guide).
    """

    x1: int
    y1: int
    x2: int
    y2: int
    w: int
    h: int


async def segment_concept(
    image_bytes: bytes,
    concept: str,
    *,
    model: str,
    content_type: str = "image/png",
) -> list[SamBox]:
    """Run SAM 3.1 concept segmentation on one image over
    ``POST /v1/responses``.

    ``concept`` is a short noun phrase per the "Write the concept prompt"
    guidance (e.g. "device"). Returns every matched box, in match order;
    an empty list means zero matches, which the SAM docs document as a
    valid, non-error outcome -- not every image contains the named
    concept.

    Raises :class:`MetaModelAPITimeoutError`, :class:`MetaModelAPIError`,
    or :class:`MetaModelAPIResponseError` on failure.
    """

    payload = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": concept},
                    {
                        "type": "input_image",
                        "image_url": _image_to_data_url(image_bytes, content_type),
                    },
                ],
            }
        ],
    }

    body = await _post_json(RESPONSES_PATH, payload)
    output_text = _first_message_output_text(body)

    return [
        SamBox(
            x1=int(match["x1"]),
            y1=int(match["y1"]),
            x2=int(match["x2"]),
            y2=int(match["y2"]),
            w=int(match["w"]),
            h=int(match["h"]),
        )
        for match in _SAM_BOX_PATTERN.finditer(output_text)
    ]


async def understand_image_structured(
    image_bytes: bytes,
    prompt: str,
    json_schema: dict[str, Any],
    schema_name: str,
    *,
    model: str,
    content_type: str = "image/png",
) -> dict[str, Any]:
    """Run a Muse Spark multimodal call with structured JSON output over
    ``POST /v1/responses``.

    Per the Structured output guide, the Responses API takes the schema
    under ``text.format`` (``response_format`` is the Chat Completions
    equivalent; this client always uses the Responses API). Returns the
    parsed JSON object from the model's ``output_text``.

    Raises :class:`MetaModelAPITimeoutError`, :class:`MetaModelAPIError`,
    or :class:`MetaModelAPIResponseError` -- the last of these also
    covers the model's output_text not being valid JSON, since that is a
    malformed response from this client's point of view.
    """

    payload = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": _image_to_data_url(image_bytes, content_type),
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": json_schema,
            }
        },
    }

    body = await _post_json(RESPONSES_PATH, payload)
    output_text = _first_message_output_text(body)

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

    return parsed
