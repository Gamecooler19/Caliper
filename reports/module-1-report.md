# Module 1 Report — Caliper

**Scope:** repository scaffold, CI/CD, the deterministic classification engine, and non-Meta-API infrastructure (secrets layer, audit logging, Postgres/Valkey/Caddy stack), per `architecture.md`'s Build sequence.

**Verification date:** 2026-10-27, from a fresh check of the actual repository at commit `0652d28` on `main` (<https://github.com/Gamecooler19/caliper>) and a live run of the stack — not from the task description.

## What was built

### Repository hygiene
- `architecture.md`, `LICENSE.md`, `CONTRIBUTING.md` are present at the repository root. Diffed against the first commit (`git diff HEAD~4 -- architecture.md LICENSE.md CONTRIBUTING.md`): zero changes. They were committed verbatim in the first commit and have not been touched since.
- `.gitignore` excludes build artifacts, virtual environments, `.env`, databases, and Docker volume mount points. `.env.example` documents every environment variable without real values.
- Five commits on `main`, all authored as `Pradyumn Tandon <pradyumn@kanopus.org>` (verified via `git log --format="%an <%ae>"`), each typed `feat: module 1 - <description>` matching what that commit actually added.

### Classification engine (`backend/app/classification/`)
- `models.py` defines `IntendedUseProfile`, `VisionFlags`, `RuleDefinition`, `AuditLogEntry`, and `ClassificationResult` as frozen Pydantic models, matching the field-level contract in `architecture.md`'s Data model section exactly (enum members, nullability, the fixed `source_citation` and `disclaimer` strings).
- `rules_data.py` stores every classification path as a `RuleRecord` (a `RuleDefinition` plus a declarative `RuleCondition`) — never as inline `if`/`elif` logic. Every rule's `needs_verification` defaults to, and currently is, `True`; every rule's `source_citation` is the fixed literal `"First Schedule, Medical Devices Rules 2017"`. No specific First Schedule rule-number text was written or guessed — the criteria strings are plain-language summaries of the general invasiveness/duration/active-status/critical-body-system risk ladder, explicitly flagged as unverified in the module docstring and in `CONTRIBUTING.md`.
- `engine.py` contains one generic matcher (`_matches_condition`) that walks the rule table; it has no per-rule branches. `classify()` raises `ClassificationError` on a `None` profile, a blank `body_system_contacted`, a malformed `vision_flags` argument, or (defensively) no matching rule — it never falls through to a guessed class.
- **Zero-I/O check, done fresh for this report:** a structural search (`agentgrep`) of `engine.py`, `models.py`, and `rules_data.py` shows their only imports are `__future__`, `enum`, `dataclasses`, `pydantic`, and `app.classification.*` itself. No `requests`, `httpx`, `sqlalchemy`, `fastapi`, `os`, `socket`, or any other I/O-capable module appears anywhere in the classification package. `test_classification_engine.py` also enforces this with an AST-based static check that runs as part of the normal test suite, so a future PR that adds a network import to the engine will fail CI, not just this one-time review.

### Security layer (`backend/app/security/`)
- `secrets.py` reads every credential (`DATABASE_URL`, `VALKEY_URL`, `META_MODEL_API_KEY`) from `os.environ` only, through a single `SecretSpec`/`load_secret` indirection — nothing is hardcoded or read from a committed file. `META_MODEL_API_KEY` is modeled as present-but-optional, since no code in this module calls the Meta Model API.
- `audit.py` builds and persists `AuditLogEntry` records to an append-only `audit_log` table via async SQLAlchemy. No code path updates or deletes an existing row.

### App skeleton and data layer
- `backend/app/main.py` is a FastAPI app with exactly one route, `GET /health`, returning `{"status": "ok"}`. No other HTTP surface exists yet, matching the Module 1 scope.
- `backend/app/db.py` defines the async SQLAlchemy engine/session factory and the one ORM model (`AuditLogRecord`), reading its connection string through the secrets layer.
- `alembic/` is configured for async migrations (`alembic/env.py` imports `app.db.Base` and prefers the secrets-layer `DATABASE_URL` over the static `alembic.ini` value). One migration exists: `d3870a79a78a_create_audit_log_table.py`.

### Dependency management and tooling
- `pyproject.toml` pins Python to `>=3.12,<3.13`, lists FastAPI, SQLAlchemy 2 (async), Alembic, asyncpg, Celery+Redis client, and Pydantic as runtime dependencies, with `ruff`, `mypy`, `pytest`, and `pytest-asyncio` as dev dependencies. `uv.lock` is committed.
- `ruff` is configured with `E, F, I, UP, B, SIM` rule sets; `alembic/versions` (tool-generated migration bodies) is excluded from linting, everything else is linted.
- `mypy` is configured against `backend/app`.

### Docker Compose stack
- `docker-compose.yml` defines four services: `api` (built from `Dockerfile`, FastAPI/uvicorn), `postgres` (official `postgres:16-alpine`), `valkey` (official `valkey/valkey:8-alpine`), and `caddy` (official `caddy:2-alpine`) fronting the API over TLS.
- `Dockerfile` is a `uv`-based, cache-layered build: installs dependencies first, then copies `backend/`, `alembic/`, and `alembic.ini`, running on `python:3.12-slim`.
- `Caddyfile` terminates TLS (via Caddy's internal CA by default, ACME in production) and reverse-proxies to the API; plain HTTP is redirected to HTTPS, never served directly.

## What was verified (this pass, live)

- **`uv run pytest backend/tests`** — 32 passed, 0 failed. Coverage includes one positive case per risk class A, B, and C (two scenarios each) and D (two scenarios, implantable and critical-body-system), plus explicit raise-not-guess cases for a `None` profile, a blank `body_system_contacted`, a missing required field, an invalid enum value, an unknown/extra field, and a malformed `vision_flags` argument.
- **`uv run ruff check .`** — all checks passed, repository-wide.
- **`uv run mypy backend/app`** — Success: no issues found in 10 source files.
- **GitHub Actions, triggered by a real push to `main`** (run `40457875664`, <https://github.com/Gamecooler19/caliper/actions/runs/40457875664>) — all five jobs (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`) completed with status `success`.
- **`docker compose up -d --build`** from a fully fresh state (`docker compose down -v` first, so Postgres, Valkey, and Caddy volumes were recreated from nothing) — all four containers (`caliper-api-1`, `caliper-postgres-1`, `caliper-valkey-1`, `caliper-caddy-1`) reached `Up`/`healthy`.
- **`curl -k https://localhost/health`** — returned `{"status":"ok"}` through Caddy's TLS termination; Caddy's logs confirm it obtained a certificate for `localhost` via its internal CA (`"msg":"certificate obtained successfully"`).
- **`docker compose exec api sh -c "cd /srv/caliper && uv run alembic upgrade head"`** against that same fresh Postgres container — applied `d3870a79a78a` cleanly (`Running upgrade -> d3870a79a78a, create audit_log table`). `psql \dt` confirms both `alembic_version` and `audit_log` tables exist afterward.
- **Zero-network-import check on the classification engine** — a structural scan of every import statement in `engine.py`, `models.py`, and `rules_data.py` (done fresh for this report, not reused from earlier in the build) found no imports outside `__future__`, `enum`, `dataclasses`, `pydantic`, and the package's own modules.

## Known gaps / explicitly out of scope for Module 1

- The First Schedule rule criteria in `rules_data.py` are sourced summaries, not verbatim gazette text, and every rule is correctly flagged `needs_verification: true`. Verifying them against the actual notified text is called out in `CONTRIBUTING.md` as the highest-value open contribution.
- `VisionFlags` is accepted and schema-validated by `classify()` but does not yet influence the outcome — there is no sourced, verified rule for resolving a vision/profile mismatch. This matches architecture.md's Module 2 gating.
- No API route exists yet beyond `/health` — request orchestration, auth, and rate limiting are Module 4 scope per `architecture.md`'s Build sequence.
- TOTP MFA and RBAC (mentioned in `architecture.md`'s Security posture as the long-run target) are not implemented; there is no user/account model yet in Module 1.
- The `.env` file used for local verification was derived from `.env.example` and is git-ignored; it was not committed.
