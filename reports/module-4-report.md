# Module 4 Report — Caliper API Orchestration Layer

**Scope:** wire Modules 1-3 together behind one HTTP endpoint, `POST /api/v1/classify`, with shared-API-key auth and a Valkey-backed rate limiter, plus a new vision-versus-stated-profile consistency check — without modifying any of Modules 1-3's own logic.

**Verification date:** 2026-10-27, from a fresh check of the actual repository at commit `8121157` on `main` (<https://github.com/Gamecooler19/caliper>) and a live run of the stack — not from the task description.

## What `visual_evidence_implantable` and `visual_evidence_duration_conflict` actually measure

Read directly from `backend/app/vision/pipeline.py`'s `_VISION_FLAGS_PROMPT`, which is the literal text sent to Muse Spark and the only place these fields' real semantics are defined:

> "Look closely at this image of a medical device. Answer two questions about what is visually evident, not what you assume from context: (1) Does the device's visible form factor show clear evidence it is designed to be implanted in the body? (2) Does the device's visible form factor conflict with being used only transiently or briefly? For each question, answer true, false, or null if the image does not give you reliable visual evidence either way. Do not guess."

`extract_vision_flags()` never receives a stated `IntendedUseProfile` — it only ever sees the device image — so neither field was computed "against" anything stated. Each measures something about the image alone:

- **`visual_evidence_implantable`** measures whether the image shows clear evidence of implantable design. This is directly comparable to `invasiveness_tier == IMPLANTABLE` **in both directions**, because the question asked is exactly about implantability — the same concept that field encodes.
- **`visual_evidence_duration_conflict`** measures something narrower than it might look at a glance: the question asked is specifically "conflict with being used only transiently or briefly" — not "conflict with whatever duration was stated." A `True` answer means the device visually resembles a long-term device; it says nothing about whether that conflicts with a stated `short_term` or `long_term` duration, because the model was never asked that. A `False` answer means the image is consistent with transient use, which also says nothing about a stated `short_term`/`long_term` duration.

Because of this, `check_consistency()` only treats `visual_evidence_duration_conflict=True` as a discrepancy when the stated `duration_tier` is `transient` — the one case the question's wording actually supports. A `False` value, or any stated `short_term`/`long_term` duration, is correctly never flagged, regardless of the vision signal, because the model's answer doesn't bear on that comparison at all. This is exactly what `backend/app/crosscheck/consistency.py`'s module docstring documents before the comparison code, and what `test_crosscheck.py`'s "unsound direction" tests confirm by name.

## What was built

### Vision cross-check (`backend/app/crosscheck/`)
- `ConsistencyFlag` (pydantic, frozen): `field_name: str`, `stated_value: str`, `vision_signal: bool`, `message: str`.
- `check_consistency(profile, vision) -> list[ConsistencyFlag]`: zero I/O, zero network imports — confirmed by a fresh structural scan: only `__future__`, `pydantic`, and `app.classification.models` appear. Checks `invasiveness_tier` in both directions and `duration_tier` in the one sound direction, per the semantic analysis above. Every message explicitly asks for human review and never asserts a regulatory conclusion (`test_check_consistency_flags_vision_implantable_but_profile_says_non_invasive` and its sibling test directly assert neither "class" nor "licensing" appears in the message text).
- **Not read by `classify()`.** `backend/app/api/routes.py` calls `classify(profile)` with exactly one argument; `ConsistencyFlag` data is computed separately and only placed in the API response, never passed into the engine.

### API orchestration (`backend/app/api/`)
- `auth.py` — `require_api_key`, an `X-API-Key` dependency checked against the new `CALIPER_API_KEY` secret via `hmac.compare_digest`. Single shared key; no per-user identity, TOTP, or RBAC was built.
- `rate_limit.py` — `check_rate_limit`, a Valkey fixed-window counter (atomic `INCR`+`EXPIRE`) defaulting to 30 requests/hour/key, documented in its own module docstring as a protective default, not a precisely justified figure.
- `errors.py` — every documented error case as one registered FastAPI exception handler: `UnauthorizedError`→401, `RateLimitExceededError`→429 with `Retry-After`, `InvalidRequestError`→400, `IntakeParsingError`→422, `MetaModelAPITimeoutError`→504, `MetaModelAPIError`/`MetaModelAPIResponseError`→502, `MediaStorageError`→502, and a catch-all `Exception`→500 that logs server-side via `logging.exception` and returns only `{"error": "internal_error"}`.
- `routes.py` — `POST /api/v1/classify`: validates the multipart request, calls `upload_media()`, runs `extract_vision_flags()` and `parse_intended_use()` concurrently via `asyncio.gather`, runs `check_consistency()`, calls `classify(profile)`, calls `generate_report()`, writes an `AuditLogEntry` with `classification_result_id=None`, and returns `{"classification", "consistency_flags", "report"}`.
- `schemas.py` — `ClassifyResponse`, the three-key success body.
- `backend/app/valkey_client.py` — the one place that constructs a Valkey client, reading `VALKEY_URL` through the existing Module 1 secrets layer.
- `backend/app/main.py` — mounts the router, registers the exception handlers, and wires a lifespan that builds the DB engine and Valkey client once at startup and disposes both at shutdown. `/health` is unchanged.

### Secrets
- `CALIPER_API_KEY` added to `backend/app/security/secrets.py`'s existing `SecretSpec` pattern, `required=True`, no default. No other new secrets were needed — `MUSE_SPARK_MODEL_ID` and `META_MODEL_API_KEY` are read through the already-existing Module 2/3 loaders.

### Known gap, stated explicitly rather than silently built around
`AuditLogEntry.classification_result_id` is always written as `None` in `routes.py`. Full `ClassificationResult` persistence was not built in this module.

## What was verified (this pass, live)

- **`uv run pytest backend/tests`** — 96 passed, 1 skipped (the Module 2 live-API integration test, unaffected by this module). New coverage:
  - `test_crosscheck.py` (10 tests): a discrepancy case for each field, a full-agreement empty-list case, plus explicit tests that the "unsound" directions (`duration_tier=long_term`/`short_term` paired with `visual_evidence_duration_conflict=True`, and any duration paired with `False`) are correctly never flagged.
  - `test_api_classify.py` (14 tests): the full success path (with `extract_vision_flags`, `parse_intended_use`, `classify`, `generate_report`, and `upload_media` mocked at their `app.api.routes` import boundary — Modules 2/3's own suites already cover their internals); one test per documented error-mapping case; a missing-API-key case and a wrong-API-key case; and a rate-limit test that runs 31 real HTTP requests through the actual FastAPI app against a real, standalone `valkey/valkey:8-alpine` container, confirming the 31st request returns 429 with a `Retry-After` header.
- **Module 1's zero-I/O check, run fresh** — `git diff 758fce7 HEAD -- backend/app/classification backend/tests/test_classification_engine.py` is empty: neither the classification package nor its test file was touched by any Module 4 commit. `test_vision_pipeline.py`'s own regression copy of the AST check (carried over from Module 2) still passes.
- **`classify()` is unmodified and unaffected by consistency data** — confirmed by inspection: `backend/app/api/routes.py` calls `classify(profile)` with one positional argument; `backend/app/classification/engine.py` is byte-identical to its Module 1/2/3 state.
- **`uv run ruff check .`** — all checks passed.
- **`uv run mypy backend/app`** — Success: no issues found in 30 source files.
- **GitHub Actions, triggered by a real push to `main`** — run `40472581733` (<https://github.com/Gamecooler19/caliper/actions/runs/40472581733>): all five jobs (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)` — with a `valkey/valkey:8-alpine` service container added to this job specifically for the real-backend rate-limit test — `Docker build`, `Dependency vulnerability scan`) completed with status `success`.
- **`docker compose up -d --build`** from a fully fresh state (`docker compose down -v` first) — all four containers (`caliper-api-1`, `caliper-postgres-1`, `caliper-valkey-1`, `caliper-caddy-1`) reached `Up`/`healthy`; **no new service was added to `docker-compose.yml`**, confirmed by diff. `curl -k https://localhost/health` returned `{"status":"ok"}`. Against the same running stack: `POST /api/v1/classify` with no `X-API-Key` header returned `{"error":"unauthorized"}`; with a wrong key, the same; with the correct key and a missing `image` field, `{"error":"invalid_request","message":"An 'image' file field is required."}`.
- **Alembic migration** applied cleanly against the fresh Postgres container in this same run (`Running upgrade -> d3870a79a78a, create audit_log table`), consistent with Module 1.

## Known gaps / explicitly out of scope for Module 4

- `AuditLogEntry.classification_result_id` is always `None` — full `ClassificationResult` persistence is not built (see above).
- No TOTP MFA, RBAC, or multi-user account model — a single shared `CALIPER_API_KEY` is the entire auth model, per the brief.
- No frontend code of any kind — Module 5's scope.
- The rate limiter is a fixed-window counter, not a sliding window: it can allow a short burst across two adjacent hour boundaries (up to ~2x the configured limit in the worst case). This tradeoff is documented in `rate_limit.py`'s module docstring and accepted as consistent with "protective default, not a precision instrument."
- `check_consistency()` only implements the two comparisons the actual vision prompt text supports (implantability in both directions, transient-duration-conflict in one direction). No comparison against `device_category`, `body_system_contacted`, or `energy_source` was attempted, since `VisionFlags` carries no signal about any of those.
