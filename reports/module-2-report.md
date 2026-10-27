# Module 2 Report — Caliper Vision Pipeline

**Scope:** vision pipeline against the confirmed hackathon-eligible toolset (SAM 3.1 + Muse Spark via the Meta Model API, never SAM 3D), extending the secrets layer, adding an object-storage access layer, and wiring both into docker compose, per the Module 2 brief.

**Verification date:** 2026-10-27, from a fresh check of the actual repository at commit `d0e4745` on `main` (<https://github.com/Gamecooler19/caliper>) and a live run of the stack — not from the task description.

## What was built

### Documentation read before writing client code

Before any client code was written, Meta's live documentation at `ai.developer.meta.com` (which serves content from `dev.meta.ai`) was read directly: the "Build with Meta Model API and Muse Code" overview, the Media segmentation guide, the SAM overview and "Segmenting with prompts" guide, the Image understanding guide, the File handling guide, the Structured output guide, and the Error handling guide. SAM 3.1 (`sam-3.1`) and Muse Spark 1.3 (`muse-spark-1.3`) were confirmed as the current models exposed on Meta Model API; **SAM 3D does not appear anywhere in that live documentation's model list or capability table**, and this module does not call it, matching the constraint in the task.

### Secrets layer (`backend/app/security/secrets.py`)
- `SecretSpec` gained an optional `default` field, resolved after the environment variable and before the required/missing check, so a spec with a default is always resolvable.
- `SAM_MODEL_ID` (default `"sam-3.1"`) and `MUSE_SPARK_MODEL_ID` (default `"muse-spark-1.3"`) were added. Nothing in the vision code hardcodes either model ID as an inline string literal at a call site — both are read through `load_sam_model_id()` / `load_muse_spark_model_id()`.
- `META_MODEL_API_KEY` was promoted from `required=False` to `required=True`: this is the first module that actually calls the Meta Model API, so a missing key now raises `MissingSecretError` the first time `load_meta_model_api_key()` is called (inside the vision client, at request time), rather than failing silently mid-request. It does **not** block `docker compose up` or the `/health` endpoint, since neither of those code paths touches the secrets layer's Meta Model API accessor.
- `STORAGE_ENDPOINT_URL`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, `STORAGE_BUCKET` were added as required secrets for the object storage layer.

### Object storage (`backend/app/storage/media.py`)
- `async upload_media(data: bytes, content_type: str) -> str` and `async fetch_media(key: str) -> bytes`, built on `aioboto3`, reading all configuration through the secrets layer. Both wrap any underlying failure in `MediaStorageError`.

### Vision client (`backend/app/vision/client.py`)
Owns every raw HTTP call to the Meta Model API, using `httpx.AsyncClient`. Three named exception types, one for each required failure mode:
- `MetaModelAPIError` — a non-2xx response, carrying `status_code`, `message`, `error_type`, `error_code`, parsed from the documented `{"error": {"message", "type", "param", "code"}}` envelope.
- `MetaModelAPIResponseError` — a 2xx response that doesn't match the documented shape (missing `output`, no message item, invalid JSON from a structured-output call, a file-upload response missing `id`).
- `MetaModelAPITimeoutError` — `httpx.TimeoutException` caught and re-raised as this named type.

None of the three is ever caught internally to fall back to a default `VisionFlags` — they propagate to the caller.

Three functions: `segment_concept()` (SAM 3.1, `POST /v1/responses`, parses `<|box;...|>` tokens out of `output_text` via a regex matching the documented wire grammar), `understand_image_structured()` (Muse Spark, `POST /v1/responses` with `text.format.type = "json_schema"`), and `upload_file()` (`POST /v1/files`, multipart, returns the parsed `{id, object, bytes, created_at, filename, purpose, status}` body).

### Vision pipeline (`backend/app/vision/pipeline.py`)
`async extract_vision_flags(image_bytes: bytes) -> VisionFlags`: runs SAM 3.1 segmentation for the concept `"medical device"`, crops to the largest matched box with Pillow (falling back to the full image on zero matches, which the SAM docs document as a valid non-error outcome), then runs a Muse Spark structured-output call on that region with a JSON Schema whose two properties are `["boolean", "null"]`-typed, matching `VisionFlags.visual_evidence_implantable` / `visual_evidence_duration_conflict` exactly. A `null` from the model is passed straight through as `None` rather than coerced to a guessed boolean.

### Zero-I/O invariant on the classification package
**Not touched.** `git diff 22ea297 HEAD -- backend/app/classification` is empty — no file under `backend/app/classification/` was modified, added, or removed by any Module 2 commit. `backend/tests/test_classification_engine.py` is likewise untouched (`git log --follow` on it shows only the Module 1 commit). `test_vision_pipeline.py` additionally carries its own copy of the AST-based zero-I/O check as a regression guard (`test_module_1_classification_engine_zero_io_check_still_passes`), confirmed passing below.

### Explicitly out of scope (per the brief, not silently half-built)
- **Free-text intake parsing into `IntendedUseProfile`** — not implemented. That's Module 3.
- **Report generation** — not implemented. That's Module 3.
- **The vision-versus-profile cross-check from architecture.md's data flow step 4** — not implemented. That step compares vision-extracted features against a submitted `IntendedUseProfile`, which does not exist as a populated input until intake parsing (Module 3) lands. `extract_vision_flags()` populates `VisionFlags` only; nothing in this module reads or constructs an `IntendedUseProfile`.
- **The public upload API endpoint** — not built. `backend/app/main.py` is unchanged from Module 1 (health check only); `upload_media`/`fetch_media`/`extract_vision_flags` are internal service functions this module owns, not HTTP routes. Wiring them to a route is Module 4's orchestration layer.

## What was verified (this pass, live)

- **`uv run pytest backend/tests`** — 46 passed, 1 skipped. The skip is the live-API integration test (by design; see below). Coverage includes: a successful end-to-end mocked `extract_vision_flags` call (SAM match → crop → Muse Spark structured output); a zero-SAM-match path that still calls Muse Spark on the full image; each of the three named client exceptions (`MetaModelAPIError`, `MetaModelAPIResponseError`, `MetaModelAPITimeoutError`) propagating uncaught out of `extract_vision_flags`; and the Module 1 zero-I/O regression check.
- **Zero real network calls in the default suite** — `backend/tests/conftest.py`'s autouse `no_real_network_calls` fixture wraps every non-integration test in a `respx.mock()` router with no default-pass-through. Verified directly: an unmocked `httpx` call made inside that context raises `respx.AllMockedAssertionError` instead of reaching the network (checked with a standalone script before this was relied on in the suite).
- **`uv run ruff check .`** — all checks passed, repository-wide.
- **`uv run mypy backend/app`** — Success: no issues found in 15 source files.
- **Dependency vulnerability scan** — the first CI run on this module's code (`40460654087`) failed: `pip-audit` flagged Pillow 11.3.0 with multiple CVEs/PYSEC advisories (PSD/FITS/font decompression-bomb and heap-overflow issues). Pillow was bumped to `>=13.0,<14.0` (resolved to `13.0.0`); a local `pip-audit` run against the regenerated lock file afterward reported **no known vulnerabilities found**.
- **GitHub Actions, triggered by real pushes to `main`** — run `40461140206` (<https://github.com/Gamecooler19/caliper/actions/runs/40461140206>), the push containing the Pillow fix: all five jobs (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`) completed with status `success`.
- **`docker compose up -d --build`** from a fully fresh state (`docker compose down -v` first) — all four containers (`caliper-api-1`, `caliper-postgres-1`, `caliper-valkey-1`, `caliper-caddy-1`) reached `Up`/`healthy`, and `curl -k https://localhost/health` returned `{"status":"ok"}` through Caddy's TLS termination. `META_MODEL_API_KEY` being required did not block this — nothing on the `/health` path touches the Meta Model API accessor.
- **Live-API integration test structure** — `backend/tests/integration/test_vision_pipeline_live.py` exists, is `pytest.mark.skipif`-gated on `RUN_LIVE_META_API_TESTS == "1"`, builds its own in-memory sample image (no bundled binary fixture, no network fetch for the fixture itself), and calls the real `extract_vision_flags()` end to end. It was **not run against the real API** in this pass (no real `META_MODEL_API_KEY` was available in this environment); its structure was confirmed by inspection and by collection (`pytest` collects and correctly reports it as skipped in the default run above) rather than by execution, per the task's explicit allowance.

## Exact request/response schemas implemented against

Recorded here per the task, so the schema is on record rather than only living in Meta's docs:

**SAM 3.1 segmentation request** (`POST https://api.meta.ai/v1/responses`):
```json
{
  "model": "sam-3.1",
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {"type": "input_text", "text": "medical device"},
        {"type": "input_image", "image_url": "data:image/png;base64,<...>"}
      ]
    }
  ]
}
```

**Muse Spark structured-output request** (`POST https://api.meta.ai/v1/responses`):
```json
{
  "model": "muse-spark-1.3",
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {"type": "input_text", "text": "<prompt>"},
        {"type": "input_image", "image_url": "data:image/png;base64,<...>"}
      ]
    }
  ],
  "text": {
    "format": {"type": "json_schema", "name": "vision_flags", "schema": {"...": "..."}}
  }
}
```

**Non-streaming Responses API response** (both calls above use this shape; this client does not stream):
```json
{
  "id": "resp_abc123",
  "object": "response",
  "created_at": 1792177394,
  "status": "completed",
  "model": "muse-spark-1.3",
  "output": [
    {"id": "rs_abc123", "type": "reasoning", "summary": [], "status": "completed"},
    {
      "id": "msg_abc123",
      "type": "message",
      "role": "assistant",
      "status": "completed",
      "content": [{"type": "output_text", "text": "<model output>", "annotations": []}]
    }
  ],
  "usage": {"input_tokens": 15, "output_tokens": 258, "total_tokens": 273}
}
```
This client reads `output_text` from the first `type: "message"` item's `content` list. For SAM, that text is the special-token grammar `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;...|>`, of which only the `box` token is parsed (masks are not used by this pipeline). For Muse Spark structured output, that text is a JSON string matching the request's `text.format.schema`.

**Files API upload** (`POST https://api.meta.ai/v1/files`, multipart with a `purpose` field and a `file` part):
```json
{
  "id": "file-842549258569145",
  "object": "file",
  "bytes": 245832,
  "created_at": 1714502400,
  "filename": "document.pdf",
  "purpose": "user_data",
  "status": "uploaded"
}
```

**Error envelope** (any non-2xx response):
```json
{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": "invalid_api_key"}}
```

## Known gaps / explicitly out of scope for Module 2

- Free-text intake parsing, report generation, and the vision-versus-profile cross-check are not implemented — see above.
- No public upload HTTP endpoint exists; `upload_media`, `fetch_media`, and `extract_vision_flags` are internal service functions only.
- The live-API integration test was not run against the real Meta Model API in this pass (no claimed key in this environment); its runnability was confirmed by structure and collection, not execution.
- `understand_image_structured()` always targets the Responses API (`text.format`), never Chat Completions (`response_format`) — both exist on Meta Model API, but only one was implemented, per the Responses-API-first pattern used throughout the client.
- SAM 3.1 streaming mode exists on Meta Model API and is the documented recommended path for video; this client always sends non-streaming requests (`stream` omitted), since the pipeline only processes a single image today. Video segmentation and the `POST /v1/files` upload-and-reference-by-ID path (also documented and used by `upload_file()`, but not yet wired into `extract_vision_flags()`) are unexercised by the current pipeline.
