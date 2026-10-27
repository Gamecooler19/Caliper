"""Live Meta Model API integration test for the vision pipeline.

# This test calls the real Meta Model API and consumes real hackathon
# credit balance -- run it manually once Pradyumn has claimed and
# configured his Meta Model API key, not as part of the default suite.

Skipped by default. Set RUN_LIVE_META_API_TESTS=1 (and META_MODEL_API_KEY
plus the storage secrets, if exercising upload_media too) to run it:

    RUN_LIVE_META_API_TESTS=1 uv run pytest \
        backend/tests/integration/test_vision_pipeline_live.py -v

This file is intentionally excluded from the default `pytest` collection
path used by CI (backend/tests), and is additionally skip-guarded here so
even a direct `pytest backend/tests/integration` invocation without the
env var set is a no-op rather than a real network call.
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from app.vision.pipeline import extract_vision_flags

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_META_API_TESTS") != "1",
    reason=(
        "Live Meta Model API test. Set RUN_LIVE_META_API_TESTS=1 and a "
        "real META_MODEL_API_KEY to run -- consumes real API credit."
    ),
)


def _sample_device_photo_bytes() -> bytes:
    """A real (synthetic) image to send to the live API.

    Deliberately not a network fetch or a bundled binary fixture: built
    in-memory so this file has zero setup dependency beyond Pillow. For
    a more meaningful manual run, replace this with bytes read from an
    actual device photo on disk.
    """

    image = Image.new("RGB", (640, 480), color=(200, 200, 210))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_extract_vision_flags_against_live_meta_model_api():
    """End-to-end call against the real SAM 3.1 + Muse Spark endpoints.

    Asserts only that the call completes and returns a well-typed
    VisionFlags -- the actual boolean/null values depend on the real
    model's judgment of the sample image and aren't asserted here.
    """

    from app.classification.models import VisionFlags

    result = await extract_vision_flags(_sample_device_photo_bytes())

    assert isinstance(result, VisionFlags)
    assert result.visual_evidence_implantable in (True, False, None)
    assert result.visual_evidence_duration_conflict in (True, False, None)
