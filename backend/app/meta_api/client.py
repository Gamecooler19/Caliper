"""Shared Meta Model API transport.

This module owns the single implementation of auth, error handling, and
Responses API response parsing used by every Meta Model API caller in
this codebase. It was extracted from ``app.vision.client`` (Module 2)
so that intake parsing and report generation (Module 3) call the same
tested code instead of each duplicating it.

Implemented against Meta's live documentation at ai.developer.meta.com
(redirecting to dev.meta.ai), as recorded verbatim in
reports/module-2-report.md:
  - Quickstart / "Build with Meta Model API" overview (Responses API
    request/response shape)
  - File handling guide (the Files API upload response shape)
  - Structured output guide (``text.format`` for the Responses API)
  - Error handling guide (the ``{"error": {...}}`` envelope)

This refactor (Module 3) does not change any request or response shape
this client sends or expects -- ``create_response()`` builds exactly the
``{"model", "input": [{"type": "message", "role": "user", "content":
[...]}], "text": {"format": {...}}}`` request shape, and parses the
response the same way ``_first_message_output_text`` already did in
Module 2. See reports/module-3-report.md for the explicit before/after
confirmation that nothing drifted during the move.

Uses httpx as the async HTTP client. Every public function raises one of
three named exceptions on failure -- :class:`MetaModelAPIError` (non-2xx
error response), a malformed/unexpected response shape
(:class:`MetaModelAPIResponseError`), or a request timeout
(:class:`MetaModelAPITimeoutError`) -- never crashes unhandled and never
silently returns a default value.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.security.secrets import load_meta_model_api_key

BASE_URL = "https://api.meta.ai/v1"
RESPONSES_PATH = "/responses"
FILES_PATH = "/files"

DEFAULT_TIMEOUT_SECONDS = 30.0


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


async def create_response(
    model: str,
    input_content: list[dict[str, Any]],
    text_format: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Create a Responses API response (``POST /v1/responses``) and return
    its ``output_text``.

    Builds exactly the request shape recorded in
    reports/module-2-report.md::

        {
          "model": "<model>",
          "input": [
            {"type": "message", "role": "user", "content": <input_content>}
          ],
          "text": {"format": <text_format>}   # only when text_format is given
        }

    ``input_content`` is the list of Responses API content parts for the
    single user message (e.g. ``input_text`` / ``input_image`` /
    ``input_file`` parts) -- callers build these the same way whether
    they're segmenting an image, asking Muse Spark a structured-output
    question, or (in future callers) sending text-only input.

    ``text_format`` is the Responses API ``text.format`` object (e.g.
    ``{"type": "json_schema", "name": ..., "schema": ...}``) for
    structured output. Omitted entirely from the request when ``None``,
    matching the plain free-text call shape.

    Returns the ``output_text`` string extracted from the first
    ``type: "message"`` item in the response's ``output`` list, per the
    same parsing Module 2 implemented.

    Raises :class:`MetaModelAPITimeoutError` on timeout,
    :class:`MetaModelAPIError` on a non-2xx response, and
    :class:`MetaModelAPIResponseError` if the 2xx response body doesn't
    match the documented shape.
    """

    payload: dict[str, Any] = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": input_content,
            }
        ],
    }
    if text_format is not None:
        payload["text"] = {"format": text_format}

    body = await _post_json(RESPONSES_PATH, payload, timeout=timeout)
    return _first_message_output_text(body)


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
