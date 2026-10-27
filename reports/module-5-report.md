# Module 5 Report — Caliper Demo Frontend

**Scope:** build Caliper's demo-facing frontend (idle/submitting/result/error upload-to-classification UI) and the one small additive backend change it depends on (`device_bounding_box`), wire the frontend into the existing Docker Compose stack behind Caddy with server-side API-key injection, add a Playwright suite, and add a CI job — without modifying any Module 1-4 logic.

**Verification date:** 2026-10-27, from a fresh check of the actual repository at commit `416a7a6` on `main` (<https://github.com/Gamecooler19/Caliper>) and a live run of the stack, run against the actual running containers and a real GitHub Actions run triggered by a real push — not from what the task prompt asked for.

## What was built

### Backend: one additive field (`backend/app/api/routes.py`, `backend/app/api/schemas.py`)

- `DeviceBoundingBox` (new pydantic model in `schemas.py`): `x1`, `y1`, `x2`, `y2`, `image_width`, `image_height` — the box in source-pixel space plus the source image's own pixel dimensions, for display only.
- `ClassifyResponse.device_bounding_box: DeviceBoundingBox | None = None` — the one new field, nullable, additive (no existing field, type, or default changed).
- `routes.py`'s `classify_device()` handler makes a **second, independent call** to the already-tested `segment_concept()` (from `app.vision.client`, Module 2), using the same `SAM_MODEL_ID` secret via the existing `load_sam_model_id()` loader. This call runs concurrently with `extract_vision_flags()` and `parse_intended_use()` inside the same `asyncio.gather()`. Its result:
  - never feeds into `classify()`, `check_consistency()`, or any other Module 1-4 function — it is placed on the response only;
  - resolves to `None` when SAM finds zero matches, matching `segment_concept()`'s own documented "zero matches is valid" semantics — not treated as an error;
  - on a genuine upstream failure (timeout/API error), propagates uncaught through the same registered exception handlers every other upstream call already uses (confirmed by a dedicated test, `test_classify_device_bounding_box_failure_does_not_affect_other_error_cases`).
- `extract_vision_flags()`, `app/vision/pipeline.py`'s own internal SAM call, and every Module 1-4 function signature are untouched — the new box data is sourced independently, never extracted from Module 2's existing (deliberately private) call.

**Diff confirmation, run fresh this pass:**
```
git diff 3ed14f7 HEAD -- backend/app/classification backend/app/vision/pipeline.py backend/app/intake backend/app/report backend/app/crosscheck
```
produces **zero output** — none of those paths changed across any of the three Module 5 commits (`eb4a4fe`, `f9c4b79`, `416a7a6`).

**Tests added** (`backend/tests/test_api_classify.py`): the existing success-path test now asserts the new field's shape; three new tests cover the null-on-zero-matches case, the largest-box tie-break on multiple matches, and the upstream-failure propagation case. `app.api.routes.segment_concept` is mocked at the same module-boundary pattern every other pipeline call already uses in this file.

### Caddy: server-side API-key injection (`Caddyfile`, `docker-compose.yml`, `Dockerfile.caddy`)

- `Caddyfile` now serves the built frontend as static files (`root * /srv/frontend`, `file_server`, with `try_files {path} /index.html` as the SPA fallback for the client-side state machine — no URL-based routing of its own, so every path falls through to the same `index.html`).
- `handle /api/v1/* { reverse_proxy api:8000 { header_up X-API-Key {$CALIPER_API_KEY} } }` — every request Caddy proxies to `/api/v1/*` gets `X-API-Key` injected from the `CALIPER_API_KEY` environment secret. The browser/frontend never receives, stores, or sends this header itself.
- `handle /health { reverse_proxy api:8000 }` kept as its own explicit block (no API key) so the Module 1 liveness probe keeps working unchanged now that Caddy also serves static files for everything else.
- `docker-compose.yml`'s `caddy` service now builds from `Dockerfile.caddy` (a multi-stage build: `node:22-alpine` builds the Vite production bundle, then `caddy:2-alpine` copies `dist/` in as `/srv/frontend`) and receives `CALIPER_API_KEY`/`CALIPER_DOMAIN` as environment variables. **No new service, container, or exposed port was added** — the `caddy` service still publishes exactly `${HTTPS_PORT:-443}:443` and `${HTTP_PORT:-80}:80`, confirmed by diff against the Module 4 compose file.
- `.dockerignore` added so the build context excludes `.env`, `node_modules`, caches, etc.

### Frontend (`frontend/`)

React 19, TypeScript, Vite 6, Tailwind CSS 3, Mantine 7 (provider + hooks), shadcn/ui-style primitives (Button/Textarea/Card built on `class-variance-authority` + `tailwind-merge`, re-themed onto this project's own tokens), TanStack Query v5 (provider wired in `App.tsx`; the single request itself goes through a plain `fetch`-based client since there's one endpoint and no caching/invalidation need, but the provider is present per the brief's stack requirement).

**Color token system** (`src/styles/tokens.css` + `tailwind.config.ts`), defined before any component code: every semantic color (`bg`, `surface`, `border`, `text`, `accent`, `danger`, `success`, `amber`) is a CSS custom property with both a light and a dark value, mapped into Tailwind's palette via `rgb(var(--x) / <alpha-value>)` — never a hardcoded single-mode hex in component code. Anchors: graphite `#14181D` (dark-mode base / light-mode text), teal `#14B8A6` (primary action + risk-readout emphasis), red `#DC2626` (error state only), green `#16A34A` (explicit no-discrepancy confirmation), amber `#D97706` (consistency-flags callout, visually distinct from danger).

**Typography split**, established in `tailwind.config.ts` before component code: `font-mono` (JetBrains Mono, falling back to IBM Plex Mono) for the risk-class letter and other instrument-readout numerics; `font-sans` (Inter, falling back to IBM Plex Sans) for body/prose text.

**State machine** (`src/lib/useClassifyMachine.ts`): exactly four states — `idle`, `submitting`, `result`, `error` — as a discriminated union driving a `useReducer`, with no URL-based routing anywhere in the app (`App.tsx` is a flat switch over `state.status`).

- **Idle** (`IdleScreen.tsx`): `ImageDropzone` + description `Textarea`, with the one primary "Measure" button (`variant="primary"`, full-width) disabled until both fields are non-empty. The only other visible control, the theme toggle, is `ghost`-variant and icon-only.
- **Submitting** (`SubmittingScreen.tsx`): one spinner and exactly one line of real status text ("Measuring your device against MDR 2017 criteria…") — no fabricated step list, since the backend is one request with no streaming.
- **Result** (`ResultScreen.tsx`): `AnnotatedImage` renders the uploaded photo with the SAM box as an SVG overlay using `viewBox="0 0 image_width image_height"` + `preserveAspectRatio="none"`, so the box always scales to the image's actual *rendered* size, never to raw pixel coordinates copied onto a differently-sized displayed image. The risk-class letter renders at `text-6xl`/`text-7xl` in `font-mono`; licensing authority, applicable form, and the clinical-investigation flag are shown alongside it. `consistency_flags` renders the amber "review suggested" callout only when non-empty, or an explicit green no-discrepancy confirmation when empty — both tested. The full report text is rendered with its fixed disclaimer sentence (always the suffix after `report`'s last `\n\n`, per `report/generator.py`'s own contract) split into its own bordered block. One primary "New Measurement" button returns to idle.
- **Error** (`ErrorScreen.tsx` + `src/lib/errorMessages.ts`): `presentError()` is the single place mapping a backend `error` code to copy — `intake_parsing_failed` → add-detail message + "Edit description" (preserves the user's image/description); `upstream_model_api_error`/`upstream_model_api_timeout` → "temporarily unavailable" + "Retry" (resubmits the same input); `rate_limit_exceeded` → "wait and try again"; everything else (including a stray `unauthorized`) → a generic "try again shortly" message. `src/lib/api.ts`'s `classifyDevice()` only ever surfaces a parsed error *code* to calling code (`ClassifyRequestError.code`) — the raw response body/message is read only to extract that code and is never passed further, so the UI cannot accidentally render it.

**No client-side API-key logic**: `src/lib/api.ts` calls `fetch("/api/v1/classify", ...)` as a same-origin relative request with no `Authorization`/`X-API-Key` header of any kind. Confirmed by a dedicated search (see "What was verified" below).

### Tests (`frontend/tests/`, Playwright)

All 22 tests pass (2 projects × 11 specs; `chromium` + `mobile-chromium`/Pixel 7 viewport), every network call mocked via `page.route`:

- `happy-path.spec.ts` — full idle→submitting→result flow with a populated `device_bounding_box` (overlay `viewBox` asserted) and a non-empty `consistency_flags` (amber callout asserted), plus a second case with an empty array asserting the green success treatment renders instead.
- `error-mapping.spec.ts` — one test per documented code: `intake_parsing_failed` (message + "Edit description" + input preserved on going back), `upstream_model_api_error`, `upstream_model_api_timeout` (both → "Retry"), `rate_limit_exceeded`, a stray `unauthorized`, and `internal_error` as the "anything else" case — each also asserts the raw backend code/message string never appears in the rendered page body.
- `mobile.spec.ts` — idle and result screens at a 390×844 mobile viewport, asserting the primary action is visible and dominant and there's no horizontal overflow.
- `theme-toggle.spec.ts` — asserts clicking the toggle changes both the `.dark` class and the actual resolved `--color-bg` custom-property value (not just a class name), and that toggling back restores the original value.

### CI (`.github/workflows/ci.yml`)

New `frontend` job: `npm ci` → `npm run typecheck` (tsc, strict) → `npm run build` (Vite production build) → Playwright browser install → `npx playwright test` → upload the HTML report as an artifact (`if-no-files-found: ignore`, since the list reporter is used locally in CI and no HTML report is generated unless a test fails) → a dedicated step that `grep -R`s the built `frontend/dist` for `CALIPER_API_KEY` and `X-API-Key`, failing the job if either string appears in client-shipped output. Every existing job (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`) is otherwise unchanged.

## What was verified (this pass, live)

- **`uv run pytest backend/tests`** (fresh, this pass) — **98 passed, 2 skipped** (the two gated live-Meta-API / live-Valkey integration tests, unaffected by this module). Module 1's zero-I/O AST check, re-run as part of `test_vision_pipeline.py::test_module_1_classification_engine_zero_io_check_still_passes`, passed unmodified.
- **`git diff 3ed14f7 HEAD -- backend/app/classification backend/app/vision/pipeline.py backend/app/intake backend/app/report backend/app/crosscheck`** — **empty**, confirming none of those paths changed across any Module 5 commit.
- **`uv run ruff check .`** — all checks passed. **`uv run mypy backend/app`** — no issues found in 30 source files.
- **`npm run typecheck`** (strict TS, `frontend/`) — no errors. **`npm run build`** — succeeds, producing `frontend/dist`.
- **`npx playwright test`** — **22 passed** across `chromium` and `mobile-chromium` projects.
- **`docker compose down -v && docker compose up -d --build`** from a fully fresh state — all four containers (`caliper-api-1`, `caliper-postgres-1`, `caliper-valkey-1`, `caliper-caddy-1`) reached `Up`/`healthy`. Verified against the live stack:
  - `curl -k https://localhost/health` → `{"status":"ok"}`.
  - `curl -k -o nul -w "%{http_code}" https://localhost/` → `200` (the built frontend's `index.html`, served by Caddy).
  - `curl -k -X POST https://localhost/api/v1/classify -F "description=test"` (no `X-API-Key` header sent by curl) → `{"error":"invalid_request","message":"An 'image' file field is required."}` — i.e. the request reached the backend's *request-shape validation*, not its auth check, which is only possible if Caddy's `header_up X-API-Key {$CALIPER_API_KEY}` actually attached a valid key before proxying. This is the direct, live confirmation that the injection works end to end.
  - `docker-compose.yml` diff against the Module 4 version confirms no new service and no new exposed port — `caddy` still only publishes `443`/`80`.
- **Repo-wide secret search, run fresh this pass**: `CALIPER_API_KEY` appears only in `Caddyfile` (the injection point), `backend/app/security/secrets.py` + `backend/app/api/auth.py` (the existing backend secrets layer), `backend/tests/conftest.py` (a mocked test fixture value), `docker-compose.yml` (env passthrough), `.env.example` (an empty documentation placeholder), and `reports/module-4-report.md` (narrative). **Zero matches under `frontend/src` or `frontend/tests`.** A second search for `X-API-Key`/`apiKey`/`api_key`/`secret` under `frontend/` turns up only two comment lines in `src/lib/api.ts` explaining that the frontend deliberately has no such logic — no executable code matched.
- **Built-bundle check**: `findstr /s /m` over `frontend/dist/*` for the literal `.env` API-key value, the string `CALIPER_API_KEY`, and the string `X-API-Key` all return zero matches — confirming the compiled JS/CSS the browser actually receives contains none of them. The same check now runs automatically in CI (`Frontend build and test (Module 5)` job's final step).
- **GitHub Actions, triggered by a real push to `main`**, run `40480510970` (<https://github.com/Gamecooler19/Caliper/actions/runs/40480510970>): all six jobs — `Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`, and the new `Frontend build and test (Module 5)` (22/22 Playwright tests) — completed with status `success`.

## Known gaps / explicitly out of scope for Module 5

- No OpenAPI codegen: `frontend/src/lib/types.ts` is hand-maintained to mirror `backend/app/api/schemas.py` and `backend/app/classification/models.py`. A schema drift between the two is not mechanically caught; it would surface as a runtime shape mismatch, not a build failure.
- The Playwright suite mocks `/api/v1/classify` at the browser network layer in every test — it is a UI/contract test suite, not an end-to-end integration test against a live Caliper backend with real Meta Model API calls. The live Docker Compose verification above is the integration-level check instead.
- `AuditLogEntry.classification_result_id` is still always `None` (Module 4's known gap, unchanged by this module).
- The dev-server Vite proxy (`frontend/vite.config.ts`) forwards `/api/v1` to `localhost:8000` with no API key, for local `npm run dev` convenience against a backend with auth temporarily relaxed — it is not part of any deployed path and carries no secret.
