# Module 3 Report — Caliper Intake Parsing, Report Generation, and Shared Meta API Client

**Scope:** extract the Responses API transport Module 2 built into `backend/app/vision/client.py` into a new shared `backend/app/meta_api/client.py`; refactor `backend/app/vision/client.py` to call it; build intake parsing (`parse_intended_use`) and report generation (`generate_report`) on top of the shared client.

**Verification date:** 2026-10-27, from a fresh check of the actual repository at commit `878acee` on `main` (<https://github.com/Gamecooler19/caliper>) and a live run of the stack — not from the task description.

## What was built

### Shared transport extraction (`backend/app/meta_api/client.py`)

`MetaModelAPIError`, `MetaModelAPIResponseError`, and `MetaModelAPITimeoutError` moved here verbatim from `backend/app/vision/client.py` (same fields, same `__init__`, same docstrings). The generic `async create_response(model: str, input_content: list, text_format: dict | None = None)` function implements exactly the request/response shapes recorded in `reports/module-2-report.md`:

- It builds `{"model": ..., "input": [{"type": "message", "role": "user", "content": input_content}]}`, adding `"text": {"format": text_format}` only when `text_format` is given — the same structure Module 2's `segment_concept()`/`understand_image_structured()` sent inline.
- It extracts `output_text` from the response's first `type: "message"` item via `_first_message_output_text()`, byte-identical logic to Module 2's private helper of the same name (confirmed by diff against the pre-refactor file before deletion).
- `upload_file()` moved here verbatim too — it builds the same multipart `POST /v1/files` request and returns the same parsed `{id, object, bytes, created_at, filename, purpose, status}` body, unchanged from Module 2.

**Explicit schema-fidelity confirmation:** nothing about the request or response shape changed during this move. The only change is *where* the code lives and *who calls it*; `create_response()`'s request-building and `_first_message_output_text()`'s response-parsing are the same logic Module 2 wrote, just generalized to accept `input_content` and `text_format` as parameters instead of being inlined per-caller.

### Vision client refactor (`backend/app/vision/client.py`)

`segment_concept()` and `understand_image_structured()` keep their exact Module 2 call signatures (`segment_concept(image_bytes, concept, *, model, content_type="image/png")`, `understand_image_structured(image_bytes, prompt, json_schema, schema_name, *, model, content_type="image/png")`) and external behavior. Internally, each now builds its `input_content` list and calls the shared `create_response()` instead of constructing its own httpx request. The module re-exports the three exception types and `upload_file` for any code still importing them from `app.vision.client`. `backend/app/vision/pipeline.py` was not touched at all — confirmed by `git diff 76f8372 HEAD -- backend/app/vision/pipeline.py`, which is empty.

### Intake parsing (`backend/app/intake/parser.py`)

`async parse_intended_use(description: str) -> IntendedUseProfile` calls the shared `create_response()` with Muse Spark and a JSON Schema whose five properties are read directly from `IntendedUseProfile`'s own enums (`InvasivenessTier`, `DurationTier`, `DeviceCategory`), so the schema can never drift from the real accepted values. Every field in the schema, including the four required ones, is typed `["string", "null"]` so the model can honestly report "not stated" rather than being forced into an enum value.

**The single most important behavior, verified:** `parse_intended_use()` raises `IntakeParsingError` whenever any of `invasiveness_tier`, `duration_tier`, `device_category`, or `body_system_contacted` comes back `null`, blank, or a string outside the field's valid enum values — it never substitutes a default or guesses. A blank input description is rejected before any model call is made. `energy_source` is the one field genuinely allowed to be `null`.

### Report generation (`backend/app/report/generator.py`)

`async generate_report(result: ClassificationResult) -> str` calls the shared `create_response()` with Muse Spark for free-text prose, then returns `f"{prose.strip()}\n\n{result.disclaimer}"`. **The disclaimer is never requested from or trusted to the model** — the prompt explicitly tells Muse Spark not to add one, and regardless of what the model actually returns (including paraphrased disclaimer-like text, as exercised by `test_generate_report_disclaimer_is_byte_exact_even_with_model_disclaimer_text`), `result.disclaimer` (the fixed `PRE_ASSESSMENT_DISCLAIMER` string from `app.classification.models`) is appended programmatically and is byte-exact in the output every time.

### Explicitly out of scope (per the brief, not silently half-built)

- **The vision-versus-profile cross-check from architecture.md's data flow step 4** — not implemented anywhere in this module. `app.intake.parser` and `app.report.generator` each operate on one side of the pipeline only; neither constructs or inspects a request containing both `VisionFlags` and `IntendedUseProfile` at once. That decision — what a mismatch does — is explicitly deferred to Module 4, where both will exist in the same request for the first time.
- **No public HTTP endpoints.** `backend/app/main.py` is unchanged (confirmed via `git diff 76f8372 HEAD -- backend/app/main.py`, empty) — still health-check only. `parse_intended_use` and `generate_report` are internal service functions, not routes.
- **No new secrets.** `MUSE_SPARK_MODEL_ID` and `META_MODEL_API_KEY` are read through the existing Module 2 secrets layer (`load_muse_spark_model_id`, implicitly via `create_response`'s call to `load_meta_model_api_key`); no new `SecretSpec` entries were added.

## What was verified (this pass, live)

- **`uv run pytest backend/tests`** — 72 passed, 1 skipped (the live-API integration test, by design). Breakdown of new coverage:
  - `test_meta_api_client.py` (11 tests): the documented request shape and auth header sent by `create_response()`; `upload_file()`'s success and malformed-id cases; and, per the brief, each of the three named exception types constructed from a mocked non-2xx response (`MetaModelAPIError`), a malformed 2xx response (`MetaModelAPIResponseError`), and a timeout (`MetaModelAPITimeoutError`) — for both `create_response()` and `upload_file()`.
  - `test_intake_parser.py` (10 tests): one successful parse, one with a null `energy_source` (the allowed nullable field), a blank-description rejection before any model call, one ambiguous/null case per each of the four required fields causing `IntakeParsingError`, an invalid-enum-value case also causing `IntakeParsingError`, and malformed/non-object JSON propagating `MetaModelAPIResponseError`.
  - `test_report_generator.py` (6 tests): a successful generation with the disclaimer appended verbatim even when the mocked model omits it entirely; a second case where the model's own output contains different, paraphrased disclaimer-like text, confirming the appended string is still the exact fixed `PRE_ASSESSMENT_DISCLAIMER`; and each of the three named exceptions propagating from a failed model call.
  - `test_vision_pipeline.py` (13 tests, rewritten per the brief): every assertion from Module 2 is still present, now mocking `app.vision.client.create_response` via `unittest.mock.AsyncMock` instead of mocking raw httpx/respx — `upload_file`'s tests moved to `test_meta_api_client.py` since it was never vision-specific, per the brief.
  - `test_classification_engine.py` (32 tests, Module 1): unchanged, all passing.
- **Module 1's zero-I/O AST check, run twice independently** — once as `test_module_1_classification_engine_zero_io_check_still_passes` inside `test_vision_pipeline.py` (regression guard from Module 2, still present and passing), and once as a fresh, ad hoc `agentgrep` structural scan of every import statement in `backend/app/classification/engine.py`, `models.py`, and `rules_data.py` for this report: only `__future__`, `enum`, `dataclasses`, `pydantic`, and the package's own modules appear. `git diff 76f8372 HEAD -- backend/app/classification backend/tests/test_classification_engine.py` is empty — neither the package nor its test file was touched by any Module 3 commit.
- **`uv run ruff check .`** — all checks passed, repository-wide.
- **`uv run mypy backend/app`** — Success: no issues found in 21 source files.
- **GitHub Actions, triggered by a real push to `main`** — run `40466178397` (<https://github.com/Gamecooler19/caliper/actions/runs/40466178397>): all five jobs (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`) completed with status `success`.
- **`docker compose up -d --build`** from a fully fresh state (`docker compose down -v` first) — all four containers (`caliper-api-1`, `caliper-postgres-1`, `caliper-valkey-1`, `caliper-caddy-1`) reached `Up`/`healthy`, and `curl -k https://localhost/health` returned `{"status":"ok"}` through Caddy's TLS termination.

## Confirmation that the shared client matches the Module 2 schemas exactly

This was checked directly, not assumed:

- **Request shape** — `test_create_response_sends_documented_request_shape_and_returns_output_text` asserts the exact JSON body `create_response()` sends equals `{"model": "muse-spark-1.3", "input": [{"type": "message", "role": "user", "content": [...]}]}`, matching the shape recorded in `reports/module-2-report.md`'s "Muse Spark structured-output request" and "SAM 3.1 segmentation request" sections. `test_create_response_includes_text_format_when_given` asserts the `text.format` object is nested exactly as `{"format": schema}`, matching the Structured output guide's `text.format` placement.
- **Auth** — `test_create_response_sends_bearer_auth_header` asserts the `Authorization` header is `Bearer <key>`, matching the documented `Bearer token (MODEL_API_KEY)` auth scheme.
- **Response parsing** — `_first_message_output_text()` in the new module is the same function Module 2 wrote (confirmed by inspection before and after the move); it is exercised by every test in `test_meta_api_client.py` that supplies a response body in the exact shape recorded in Module 2's report (`output: [{type: "message", content: [{type: "output_text", text: ...}]}]`).
- **Error envelope** — `test_create_response_raises_api_error_on_non_2xx_response` supplies the exact `{"error": {"message", "type", "code"}}` envelope recorded in Module 2's report and asserts all three fields land on the raised `MetaModelAPIError`.
- **Files API response** — `test_upload_file_returns_parsed_response` supplies the exact `{id, object, bytes, created_at, filename, purpose, status}` shape recorded in Module 2's report and asserts the `id` and `status` fields pass through unchanged.

No drift was found or introduced during the move.

## Known gaps / explicitly out of scope for Module 3

- The vision-versus-profile cross-check (architecture.md data flow step 4) is not implemented — deferred to Module 4, as instructed.
- No public upload/classify/intake/report HTTP endpoints exist yet; these remain internal service functions pending Module 4's orchestration layer.
- `IntendedUseProfile.energy_source` validation only checks type (string or null); it does not attempt to normalize or further validate the string's content, matching Module 1's model (which places no further constraint on that field either).
- The live-API integration test (`backend/tests/integration/test_vision_pipeline_live.py`, from Module 2) was not re-run against the real Meta Model API in this pass — no claimed key in this environment; it is unaffected by this module's changes and continues to collect correctly as a skip in the default suite.
