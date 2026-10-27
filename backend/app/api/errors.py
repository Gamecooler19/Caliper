"""Exception types and exception-handler registration for the Caliper API.

Every error-mapping case is implemented here as one FastAPI exception
handler, each returning the exact JSON body documented for that case.
Registered once, from ``backend/app/main.py``, via
:func:`register_exception_handlers` -- the route handlers in
``routes.py`` do not need their own try/except blocks for these cases;
raising the right exception type is enough, and FastAPI/Starlette's
exception middleware (which walks each exception's MRO against the
registered handler map) finds the exact match.

The catch-all ``Exception`` handler is the "caught at the route
boundary" the brief requires: it logs the full exception server-side
via ``logging.exception`` and returns only
``{"error": "internal_error"}`` -- never a raw traceback or internal
exception message.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.intake.parser import IntakeParsingError
from app.meta_api.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
)
from app.storage.media import MediaStorageError

logger = logging.getLogger(__name__)


class CaliperAPIError(Exception):
    """Base for this module's own API-level errors (auth, rate limiting,
    request validation) -- distinct from the business-logic exceptions
    Modules 2/3 already define, which are handled by their own
    registered handlers below.
    """


class UnauthorizedError(CaliperAPIError):
    """Raised by :func:`app.api.auth.require_api_key` when the
    ``X-API-Key`` header is missing or doesn't match the configured key.
    """


class RateLimitExceededError(CaliperAPIError):
    """Raised when a request would exceed the per-key rate limit on
    ``POST /api/v1/classify``.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded; retry after {retry_after_seconds}s")


class InvalidRequestError(CaliperAPIError):
    """Raised when a request to ``POST /api/v1/classify`` is missing a
    required field (image or description) or supplies it blank/empty.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


async def _unauthorized_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "unauthorized"})


async def _rate_limit_handler(
    request: Request, exc: RateLimitExceededError
) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limit_exceeded"},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


async def _invalid_request_handler(
    request: Request, exc: InvalidRequestError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_request", "message": exc.message},
    )


async def _intake_parsing_handler(
    request: Request, exc: IntakeParsingError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "intake_parsing_failed", "message": str(exc)},
    )


async def _upstream_timeout_handler(
    request: Request, exc: MetaModelAPITimeoutError
) -> JSONResponse:
    return JSONResponse(
        status_code=504,
        content={"error": "upstream_model_api_timeout"},
    )


async def _upstream_api_error_handler(
    request: Request, exc: MetaModelAPIError | MetaModelAPIResponseError
) -> JSONResponse:
    message = exc.message if isinstance(exc, MetaModelAPIError) else str(exc)
    return JSONResponse(
        status_code=502,
        content={"error": "upstream_model_api_error", "message": message},
    )


async def _storage_error_handler(request: Request, exc: MediaStorageError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"error": "storage_error", "message": str(exc)},
    )


async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception while processing %s %s", request.method, request.url.path,
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"error": "internal_error"})


def register_exception_handlers(app: FastAPI) -> None:
    """Register every error-mapping handler on ``app``.

    Order of registration doesn't affect dispatch: Starlette's exception
    middleware walks each raised exception's MRO and picks the most
    specific registered handler, so the broad ``Exception`` catch-all
    registered here only ever fires for exceptions none of the other
    handlers match.

    ``add_exception_handler``'s own type hints are deliberately wider
    than any single handler's signature (it accepts handlers for any
    ``Exception`` subclass), which is why each call below is annotated
    with a local ``# type: ignore[arg-type]`` -- the runtime behavior is
    exactly what Starlette's dispatch documents and every case here is
    covered by ``test_api_classify.py``.
    """

    app.add_exception_handler(UnauthorizedError, _unauthorized_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceededError, _rate_limit_handler)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidRequestError, _invalid_request_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntakeParsingError, _intake_parsing_handler)  # type: ignore[arg-type]
    app.add_exception_handler(MetaModelAPITimeoutError, _upstream_timeout_handler)  # type: ignore[arg-type]
    app.add_exception_handler(MetaModelAPIError, _upstream_api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(MetaModelAPIResponseError, _upstream_api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(MediaStorageError, _storage_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _internal_error_handler)
