"""Tests for the API orchestration layer: POST /api/v1/classify.

Covers, per the Module 4 brief:

- The full success path, with ``extract_vision_flags``,
  ``parse_intended_use``, ``classify``, and ``generate_report`` mocked
  at their module boundaries in ``app.api.routes`` (Modules 2/3's own
  test suites already cover their internals). ``upload_media`` is also
  mocked here, for the same "zero real network calls" reason every
  other test in this suite is mocked -- it talks to S3-compatible
  object storage over aioboto3/aiohttp, which the httpx-based
  ``no_real_network_calls`` respx fixture cannot intercept, so leaving
  it unmocked would make a real network attempt against the fake
  storage endpoint configured in ``conftest.py``.
- One test per documented error-mapping case.
- A missing-API-key case and a wrong-API-key case.
- A rate-limit test against a real Valkey-compatible backend (a
  standalone ``valkey/valkey:8-alpine`` container, started for this
  test run and reachable at ``redis://localhost:16379/0``), confirming
  the 31st request within an hour for the same key returns 429.

The app's own lifespan (``backend/app/main.py``) builds its database
engine and Valkey client via ``build_engine()``/``build_valkey_client()``
at startup; these tests monkeypatch those two factory functions (as
imported into ``app.main``) to point at a disposable SQLite file and a
fakeredis-backed client (or, for the rate-limit test, the real Valkey
container) before entering the ``TestClient`` context, so the real
app startup/shutdown path runs unmodified against test-scoped
dependencies.
"""

from __future__ import annotations

import asyncio
import io
from contextlib import contextmanager
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient
from PIL import Image

from app.classification.models import (
    ApplicableForm,
    ClassificationResult,
    DeviceCategory,
    DurationTier,
    IntendedUseProfile,
    InvasivenessTier,
    LicensingAuthority,
    RiskClass,
    VisionFlags,
)
from app.db import Base, build_engine
from app.intake.parser import IntakeParsingError
from app.meta_api.client import (
    MetaModelAPIError,
    MetaModelAPIResponseError,
    MetaModelAPITimeoutError,
)
from app.storage.media import MediaStorageError
from app.vision.client import SamBox

REAL_TEST_VALKEY_URL = "redis://localhost:16379/0"


def _sample_image_bytes() -> bytes:
    image = Image.new("RGB", (64, 48), color=(10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _sample_profile() -> IntendedUseProfile:
    return IntendedUseProfile(
        invasiveness_tier=InvasivenessTier.NON_INVASIVE,
        duration_tier=DurationTier.TRANSIENT,
        device_category=DeviceCategory.NON_ACTIVE,
        body_system_contacted="skin",
    )


def _sample_vision_flags() -> VisionFlags:
    return VisionFlags(
        visual_evidence_implantable=False,
        visual_evidence_duration_conflict=False,
    )


def _sample_classification_result() -> ClassificationResult:
    return ClassificationResult(
        risk_class=RiskClass.A,
        matched_rule_id="FS-A-NONINVASIVE-NONACTIVE",
        applicable_licensing_authority=LicensingAuthority.STATE,
        applicable_form=ApplicableForm.MD_3,
        clinical_investigation_required=False,
        clinical_investigation_basis="No novel evidence basis triggered.",
    )


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@contextmanager
def _test_client(monkeypatch: pytest.MonkeyPatch, tmp_path, valkey_client_factory):
    """Build a ``TestClient`` with the app's DB engine pointed at a
    disposable SQLite file and its Valkey client built by
    ``valkey_client_factory`` (called inside the app's lifespan, which
    runs on the TestClient's own event loop -- important for the real
    Valkey backend test, since a redis-py connection opened on one
    event loop cannot be reused on another), then run the app's real
    lifespan against those test-scoped dependencies.

    ``raise_server_exceptions=False`` so an unhandled exception that
    reaches the registered catch-all handler produces a real response
    this test can assert on, rather than being re-raised into the test
    for debugging (the TestClient's normal behavior).
    """

    db_path = tmp_path / "test_caliper.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    asyncio.run(_create_tables(engine))

    monkeypatch.setattr("app.main.build_engine", lambda: engine)
    monkeypatch.setattr("app.main.build_valkey_client", valkey_client_factory)

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def fake_valkey_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path, fake_valkey_client):
    with _test_client(monkeypatch, tmp_path, lambda: fake_valkey_client) as client:
        yield client


def _sample_display_boxes() -> list[SamBox]:
    return [SamBox(x1=5, y1=6, x2=55, y2=46, w=64, h=48)]


def _mock_pipeline_functions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    vision_flags=None,
    vision_side_effect=None,
    profile=None,
    intake_side_effect=None,
    classification_result=None,
    report="A short report.",
    display_boxes=None,
    display_box_side_effect=None,
):
    monkeypatch.setattr(
        "app.api.routes.upload_media", AsyncMock(return_value="media/fake-key.png")
    )
    monkeypatch.setattr(
        "app.api.routes.extract_vision_flags",
        AsyncMock(
            return_value=vision_flags if vision_flags is not None else _sample_vision_flags(),
            side_effect=vision_side_effect,
        ),
    )
    monkeypatch.setattr(
        "app.api.routes.parse_intended_use",
        AsyncMock(
            return_value=profile if profile is not None else _sample_profile(),
            side_effect=intake_side_effect,
        ),
    )
    monkeypatch.setattr(
        "app.api.routes.classify",
        lambda *_args, **_kwargs: (
            classification_result
            if classification_result is not None
            else _sample_classification_result()
        ),
    )
    monkeypatch.setattr("app.api.routes.generate_report", AsyncMock(return_value=report))
    monkeypatch.setattr(
        "app.api.routes.segment_concept",
        AsyncMock(
            return_value=(
                display_boxes if display_boxes is not None else _sample_display_boxes()
            ),
            side_effect=display_box_side_effect,
        ),
    )


def _multipart_request(client: TestClient, *, headers=None, files=None, data=None):
    default_headers = {"X-API-Key": "test-fake-caliper-api-key"}
    if headers is not None:
        default_headers.update(headers)

    default_files = {"image": ("device.png", _sample_image_bytes(), "image/png")}
    if files is not None:
        default_files = files

    default_data = {"description": "A simple non-invasive bandage."}
    if data is not None:
        default_data = data

    return client.post(
        "/api/v1/classify",
        headers=default_headers,
        files=default_files,
        data=default_data,
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_classify_success_path(api_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _mock_pipeline_functions(monkeypatch)

    response = _multipart_request(api_client)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "classification",
        "consistency_flags",
        "report",
        "disclaimer",
        "device_bounding_box",
    }
    assert body["classification"]["risk_class"] == "A"
    assert body["classification"]["matched_rule_id"] == "FS-A-NONINVASIVE-NONACTIVE"
    assert body["consistency_flags"] == []
    assert body["report"] == "A short report."
    assert body["device_bounding_box"] == {
        "x1": 5,
        "y1": 6,
        "x2": 55,
        "y2": 46,
        "image_width": 64,
        "image_height": 48,
    }


def test_classify_success_path_includes_consistency_flags_when_discrepant(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    discrepant_vision = VisionFlags(
        visual_evidence_implantable=True,
        visual_evidence_duration_conflict=None,
    )
    _mock_pipeline_functions(monkeypatch, vision_flags=discrepant_vision)

    response = _multipart_request(api_client)

    assert response.status_code == 200
    flags = response.json()["consistency_flags"]
    assert len(flags) == 1
    assert flags[0]["field_name"] == "invasiveness_tier"


# ---------------------------------------------------------------------------
# device_bounding_box: Module 5's display-only, independent SAM call
# ---------------------------------------------------------------------------


def test_classify_device_bounding_box_null_when_sam_finds_no_match(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Zero matches from the display-only segment_concept() call is a
    valid outcome, not an error: the response field is null, and the
    rest of the response is unaffected.
    """

    _mock_pipeline_functions(monkeypatch, display_boxes=[])

    response = _multipart_request(api_client)

    assert response.status_code == 200
    body = response.json()
    assert body["device_bounding_box"] is None
    assert body["classification"]["risk_class"] == "A"


def test_classify_device_bounding_box_picks_largest_match(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """When SAM returns multiple matches for the display-only call, the
    largest-by-area box is used -- the same tie-break
    app.vision.pipeline uses internally for its own, separate call.
    """

    small_box = SamBox(x1=0, y1=0, x2=10, y2=10, w=64, h=48)
    large_box = SamBox(x1=1, y1=1, x2=60, y2=45, w=64, h=48)
    _mock_pipeline_functions(monkeypatch, display_boxes=[small_box, large_box])

    response = _multipart_request(api_client)

    assert response.status_code == 200
    box = response.json()["device_bounding_box"]
    assert box == {"x1": 1, "y1": 1, "x2": 60, "y2": 45, "image_width": 64, "image_height": 48}


@pytest.mark.parametrize(
    "display_error",
    [
        MetaModelAPITimeoutError("timed out"),
        MetaModelAPIError(status_code=500, message="Internal server error."),
        MetaModelAPIResponseError("Malformed response body"),
        RuntimeError("anything else at all"),
    ],
    ids=["timeout", "api_error", "response_error", "unexpected"],
)
def test_classify_display_box_failure_resolves_to_null_box_and_leaves_response_unchanged(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, display_error: Exception
):
    """Any exception from the display-only second segment_concept() call
    is caught at that call site: the request still succeeds (200),
    device_bounding_box is null, and classification / consistency_flags /
    report / disclaimer are identical to a request where the call
    succeeded. It must never reach the registered exception handlers.
    """

    _mock_pipeline_functions(monkeypatch)
    baseline = _multipart_request(api_client)
    assert baseline.status_code == 200

    _mock_pipeline_functions(monkeypatch, display_box_side_effect=display_error)
    response = _multipart_request(api_client)

    assert response.status_code == 200
    body = response.json()
    assert body["device_bounding_box"] is None
    baseline_body = baseline.json()
    for field in ("classification", "consistency_flags", "report", "disclaimer"):
        assert body[field] == baseline_body[field]


def test_classify_display_box_failure_is_logged_with_context(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    _mock_pipeline_functions(
        monkeypatch, display_box_side_effect=MetaModelAPITimeoutError("timed out")
    )

    with caplog.at_level("ERROR", logger="app.api.routes"):
        response = _multipart_request(api_client)

    assert response.status_code == 200
    records = [r for r in caplog.records if r.name == "app.api.routes"]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert "display" in records[0].getMessage().lower()
    assert "image/png" in records[0].getMessage()


def test_classify_display_box_failure_does_not_mask_intake_failure(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The display call's swallow is independent of the correctness-
    critical gather: with both failing, the request still fails hard
    on parse_intended_use's error, exactly as it does today.
    """

    _mock_pipeline_functions(
        monkeypatch,
        intake_side_effect=IntakeParsingError("Could not resolve 'invasiveness_tier'."),
        display_box_side_effect=RuntimeError("display call also failed"),
    )

    response = _multipart_request(api_client)

    assert response.status_code == 422
    assert response.json()["error"] == "intake_parsing_failed"


def test_classify_disclaimer_field_equals_classification_result_disclaimer(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """``disclaimer`` is populated straight from result.disclaimer, not
    parsed out of the report: the mocked report here deliberately does
    not contain the disclaimer at all, so only a direct copy can pass.
    """

    result = _sample_classification_result().model_copy(
        update={"disclaimer": "Custom disclaimer text, distinct from the report."}
    )
    _mock_pipeline_functions(
        monkeypatch, classification_result=result, report="Report with no disclaimer."
    )

    response = _multipart_request(api_client)

    assert response.status_code == 200
    body = response.json()
    assert body["disclaimer"] == result.disclaimer
    assert body["report"] == "Report with no disclaimer."
    assert body["classification"]["disclaimer"] == result.disclaimer


# ---------------------------------------------------------------------------
# Error-mapping cases
# ---------------------------------------------------------------------------


def test_classify_missing_image_returns_400(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(monkeypatch)

    response = api_client.post(
        "/api/v1/classify",
        headers={"X-API-Key": "test-fake-caliper-api-key"},
        data={"description": "A description."},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_request"
    assert "message" in body


def test_classify_missing_description_returns_400(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(monkeypatch)

    response = api_client.post(
        "/api/v1/classify",
        headers={"X-API-Key": "test-fake-caliper-api-key"},
        files={"image": ("device.png", _sample_image_bytes(), "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_classify_empty_description_returns_400(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(monkeypatch)

    response = _multipart_request(api_client, data={"description": "   "})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_classify_intake_parsing_error_returns_422(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(
        monkeypatch,
        intake_side_effect=IntakeParsingError(
            "Could not confidently resolve required field 'invasiveness_tier'."
        ),
    )

    response = _multipart_request(api_client)

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "intake_parsing_failed"
    assert "message" in body


def test_classify_meta_model_api_timeout_returns_504(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(
        monkeypatch, vision_side_effect=MetaModelAPITimeoutError("timed out")
    )

    response = _multipart_request(api_client)

    assert response.status_code == 504
    assert response.json() == {"error": "upstream_model_api_timeout"}


def test_classify_meta_model_api_error_returns_502(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(
        monkeypatch,
        vision_side_effect=MetaModelAPIError(
            status_code=500, message="Internal server error."
        ),
    )

    response = _multipart_request(api_client)

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "upstream_model_api_error"
    assert "message" in body


def test_classify_meta_model_api_response_error_returns_502(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(
        monkeypatch,
        vision_side_effect=MetaModelAPIResponseError("Malformed response body"),
    )

    response = _multipart_request(api_client)

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_model_api_error"


def test_classify_media_storage_error_returns_502(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(monkeypatch)
    monkeypatch.setattr(
        "app.api.routes.upload_media",
        AsyncMock(side_effect=MediaStorageError("Failed to upload to bucket")),
    )

    response = _multipart_request(api_client)

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "storage_error"
    assert "message" in body


def test_classify_unhandled_exception_returns_500_without_leaking_details(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(
        monkeypatch, vision_side_effect=RuntimeError("some unexpected internal detail")
    )

    response = _multipart_request(api_client)

    assert response.status_code == 500
    body = response.json()
    assert body == {"error": "internal_error"}
    # The raw exception message must never appear in the response body.
    assert "some unexpected internal detail" not in response.text


# ---------------------------------------------------------------------------
# Auth: missing / wrong API key
# ---------------------------------------------------------------------------


def test_classify_missing_api_key_returns_401(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(monkeypatch)

    response = api_client.post(
        "/api/v1/classify",
        files={"image": ("device.png", _sample_image_bytes(), "image/png")},
        data={"description": "A description."},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_classify_wrong_api_key_returns_401(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _mock_pipeline_functions(monkeypatch)

    response = _multipart_request(api_client, headers={"X-API-Key": "totally-wrong-key"})

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


# ---------------------------------------------------------------------------
# Rate limiting against a real Valkey-compatible backend
# ---------------------------------------------------------------------------


@pytest.fixture
def real_valkey_available():
    """Flush the real, standalone Valkey test backend (started for this
    test run at ``redis://localhost:16379/0``) clean before the test,
    skipping the test if that backend isn't reachable, rather than
    pre-building a client the app's lifespan would then have to reuse
    across an event-loop boundary.
    """

    async def _flush():
        client = redis_asyncio.from_url(REAL_TEST_VALKEY_URL, decode_responses=True)
        try:
            await client.flushdb()
        finally:
            await client.aclose()

    try:
        asyncio.run(_flush())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"Real Valkey test backend at {REAL_TEST_VALKEY_URL} is not "
            f"reachable: {exc}"
        )


def test_classify_rate_limit_enforced_against_real_valkey_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path, real_valkey_available
):
    """Issues 31 real requests through the app against a real
    Valkey-compatible backend. The default limit is 30 requests per
    hour per key; the 31st request for the same key must return 429
    with a Retry-After header.
    """

    _mock_pipeline_functions(monkeypatch)

    valkey_factory = lambda: redis_asyncio.from_url(  # noqa: E731
        REAL_TEST_VALKEY_URL, decode_responses=True
    )

    with _test_client(monkeypatch, tmp_path, valkey_factory) as client:
        last_response = None
        for _ in range(31):
            last_response = _multipart_request(client)

        assert last_response is not None
        assert last_response.status_code == 429
        assert last_response.json() == {"error": "rate_limit_exceeded"}
        assert "Retry-After" in last_response.headers
        assert int(last_response.headers["Retry-After"]) > 0
