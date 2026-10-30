# Module 6 Report — User Accounts, RBAC and Mandatory TOTP

**Scope:** a multi-user account model for Caliper's backend (roles, argon2 passwords, mandatory TOTP, Valkey sessions, admin user management) so an interactive classification is attributable to a person, with every existing `CALIPER_API_KEY` caller unchanged. Backend only: there is no login UI in this module.

**Verification date:** 2026-10-30, at commit `ff463b6` on `main` (<https://github.com/Gamecooler19/Caliper>). Every result below comes from a command run this pass against the repository, a stack rebuilt from empty volumes, or a real GitHub Actions run.

## Action required from you

- **Set `COLUMN_ENCRYPTION_KEY`** in your `.env` (or wherever you inject secrets). It is a new required secret: a Fernet key, generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. `docker-compose.yml` passes it through with an empty default, so until it is set, TOTP enrollment and login return 500. I did not touch `.env`; for verification I exported a throwaway key in the shell for the compose run only. I also could not update `.env.example` (reading it was blocked by a permission rule), so that placeholder is still to add.
- **Run migrations on deploy:** nothing runs `alembic upgrade head` automatically (same as before this module). Run it once, then bootstrap the first admin (see below).

## What was built

### Dependencies (`pyproject.toml`, `uv.lock`)
`argon2-cffi` 25.1.0, `pyotp` 2.10.0, and `cryptography` 50.0.2 (for the encryption utility below).

### Column-level encryption: a new, minimal utility, added deliberately
Module 1's `app/security/secrets.py` only ever loaded **environment-sourced application secrets**; nothing in the codebase encrypted database column values. So I **did add a new primitive**: `backend/app/security/crypto.py` (`encrypt_text` / `decrypt_text`, ~40 lines over `cryptography`'s Fernet: AES-128-CBC with an HMAC-SHA256 tag), keyed by a new `COLUMN_ENCRYPTION_KEY` secret that is declared and loaded through the existing secrets layer (`SecretSpec` + `load_column_encryption_key()`). It is the only encryption primitive in the codebase and is used for exactly one column, `users.totp_secret_encrypted`. It never logs plaintext or ciphertext. Tampered ciphertext or a wrong key raises `DecryptionError`.

### `backend/app/auth/`
- `models.py` — `User` exactly as specified: `id` UUID PK, `email` unique + indexed + lowercased (stored lowercased by code **and** enforced by a `CHECK (email = lower(email))` constraint), `password_hash`, `role` enum (`admin`/`analyst`/`auditor`), `totp_secret_encrypted` (nullable), `totp_enabled` (default false), `must_change_password` (default true), `is_active` (default true), `created_at`, `last_login_at` (nullable).
- `passwords.py` — argon2id hash/verify; a dummy hash verified against when a login names an unknown email, so unknown and known accounts cost the same.
- `totp.py` — secret generation, encrypted storage, 6-digit code verification with a ±1 step window, plus a replay guard (`consume_code_once`: a code accepted once is rejected for 90 s).
- `sessions.py` — Valkey sessions: JSON `{user_id, role, created_at, last_seen_at}` under `session:{session_id}`, **12-hour sliding TTL refreshed on every authenticated request**; create/read/refresh/destroy; a per-user session set so an admin action can end all of a user's sessions. Also the short-lived login tokens (below).
- `dependencies.py` — `require_session`, `require_role(role)`, and `require_api_key_or_session` for `POST /api/v1/classify`, which sets `request.state.user_id` (the user's id, or `None` for the API key).
- `routes.py` — `POST /auth/login`, `POST /auth/login/totp`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password`, plus the enrollment endpoints the login flow needs (`POST /auth/totp/enroll/begin`, `.../confirm`).
- `errors.py` — auth error types and handlers (registered from `main.py`, so `app/api/errors.py` is untouched).
- `create_admin.py` — a small bootstrap CLI (see "Additions beyond the brief").

### `backend/app/admin/routes.py`
`POST /admin/users` (generates a random initial password, returns it **once** in the response, stores only the hash; nothing is emailed), `GET /admin/users`, `PATCH /admin/users/{id}` (role and/or `is_active`), `POST /admin/users/{id}/reset-totp` (clears the enrollment, ends the user's sessions, writes its own audit entry). Every route is behind `require_role("admin")`.

### Login flow
1. `POST /auth/login` (email + password) returns **never a session**: either `totp_required` + a 5-minute single-use challenge token, or `totp_enrollment_required` + a 15-minute enrollment token.
2. `POST /auth/login/totp` (challenge token + code) sets the session cookie only on success: `HttpOnly; Secure; SameSite=Strict`, with no `Max-Age` (a browser-session cookie; the Valkey sliding TTL is the source of truth).
3. Unenrolled accounts: `enroll/begin` generates and stores the secret encrypted (`totp_enabled` stays false), `enroll/confirm` verifies a code from it, enables TOTP and starts the session.
4. `must_change_password` is a gate: until `POST /auth/change-password` succeeds, every authenticated route except `/auth/me`, `/auth/logout` and `/auth/change-password` returns 403 `{"error":"password_change_required"}` (including `/admin/*` and `/classify` with a session).
5. Failures: wrong/unknown email, wrong password, inactive account and invalid tokens all return the same 401 `invalid_credentials`; a bad code returns 401 `invalid_totp_code`.

### Audit attribution (additive migration `a7c1e4b9d2f0`)
`users` table plus a nullable `audit_log.user_id` FK. `AuditLogEntry` lives in `app/classification/models.py` (Module 1, off limits), so `user_id` is a new optional parameter on `record_audit_entry(session, entry, user_id=None)` rather than a field on that model. Session classify calls write the real user id; API-key calls write `NULL`.

### Rate limiting: `rate_limit.py` not modified
`app/api/rate_limit.py` is byte-identical. The same `check_rate_limit` is called with its existing `route` / `key_identifier` parameters under new keys: `ratelimit:auth-login:{email}` (10/hour), `ratelimit:auth-totp:{user_id}` (10/hour, covers login/totp and enrollment), `ratelimit:auth-change-password:{user_id}`, and `ratelimit:classify-session:{user_id}` (30/hour, the same default as classify). The API-key call is the original `check_rate_limit(valkey_client, actor)`.

## The API-key path was not altered (explicit confirmation)

- `backend/app/api/auth.py` (`require_api_key`) and `backend/app/api/rate_limit.py`: `git diff 3ebb7cf HEAD` is **empty** for both.
- `backend/tests/test_api_classify.py` and `backend/tests/conftest.py` (the pre-existing classify suite and fixtures): `git diff 3ebb7cf HEAD` is **empty**, and that whole file still passes: it is the "passes unmodified" regression evidence, run against the new code.
- The only change to `api/routes.py`: the dependency is now `require_api_key_or_session`, and the rate-limit/audit lines branch on `request.state.user_id`. For a request with no session cookie, the dependency calls `require_api_key` directly, the limiter call is the original line, and the audit write passes `user_id=None`.
- New tests in `backend/tests/auth/test_classify_attribution.py` pin this: API-key responses equal the exact expected bytes (`response.content == ` the compact JSON of the unchanged response model) with no `Set-Cookie`; missing/wrong/empty key return the exact `{"error":"unauthorized"}` bytes and write no audit row; an API-key call writes `user_id` NULL with actor `api-client` and uses the original `ratelimit:classify:api-client:*` key.

**One behavior to know about:** Caddy injects `X-API-Key` on every `/api/v1/*` request, so a logged-in browser sends both a cookie and the key. `require_api_key_or_session` therefore resolves a **valid session first**, so that browser is attributed to the person. An invalid/expired cookie falls through to the API-key check (so the existing key-injecting deployment keeps working). Consequence: **the demo frontend remains anonymous**: with Caddy still injecting the key, nothing forces a login before classifying. Requiring login in the UI, and removing key injection for browser traffic, is a Module 7 decision, not made here.

## Untouched-modules check

`git diff 3ebb7cf HEAD -- backend/app/classification backend/app/vision/pipeline.py backend/app/intake backend/app/report backend/app/crosscheck` produces **no output** (3ebb7cf is the end of Module 5). Backend files changed under `backend/app/` since then: `admin/`, `auth/`, `api/routes.py`, `db.py`, `main.py`, `security/audit.py`, `security/crypto.py`, `security/secrets.py`.

## What was verified (this pass)

- **`uv run pytest backend/tests`: 195 passed, 2 skipped** (was 104 / 2; +91 new, 0 existing tests changed). The 2 skips are the live-Valkey rate-limit and live Meta API tests, which need external services not running locally; the Valkey one runs in CI against its service container.
- **`uv run ruff check .`**: all checks passed. **`uv run mypy backend/app`**: no issues in 42 source files.
- **Fresh Postgres, `docker compose down -v` then `up -d --build`**: confirmed `0` tables in `public` before migrating. `alembic upgrade head` ran both revisions cleanly; resulting schema checked in `psql`: `users` columns/nullability as specified, `ck_users_email_lowercase`, unique index `ix_users_email`, FK `fk_audit_log_user_id_users`, `audit_log.user_id uuid NULL`, enum `user_role = {admin,analyst,auditor}`. `alembic check`: "No new upgrade operations detected" (no model/migration drift). `alembic downgrade -1` removed `users`, the FK column and the enum type; `upgrade head` re-applied. I repeated the whole wipe-and-rebuild a second time on the final commit with the same result.
- **Live end-to-end through Caddy (`https://localhost`) on the final build, 25/25 checks passed**: bootstrap admin, enrollment, cookie flags (`HttpOnly`, `Secure`, `SameSite=Strict`) in the real `Set-Cookie`, the password-change gate (403 on `/admin/users` until changed), admin creating a user, an analyst getting 403 on all four admin endpoints, `reset-totp` ending the target's session and writing an audit row naming the admin, the 11th failed login returning 429, logout, and the no-cookie classify path still reaching request validation via Caddy's injected key (400 "image required", the pre-Module-6 behavior).
- **Secrets at rest and in logs (live):** the TOTP secret in `users.totp_secret_encrypted` is a Fernet token that does not contain the plaintext secret; `password_hash` is `$argon2id$...`; a full `pg_dump --data-only` and the API container logs contain none of the passwords or TOTP secrets used in the run.
- **GitHub Actions on real pushes to `main`:** run `41013040077` (commit `7048484`) and run `41013363243` (commit `ff463b6`, the final code commit): all six jobs `success` (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`, `Frontend build and test (Module 5)`). The commit that adds this report triggers its own run, not yet reported here.

### Tests written (all in `backend/tests/auth/`, new package with its own conftest)
Password hash/verify round trips and malformed-hash handling; the encryption primitive; TOTP generation, encrypted storage, valid/invalid/malformed/out-of-window codes, replay; the full login happy path with `Set-Cookie` attributes asserted; rejection for wrong password, wrong TOTP code, inactive account, unenrolled account (and unknown email); enrollment end to end; session JSON shape and the sliding TTL; the must-change-password gate; 403 for **both** non-admin roles on **each** of the four admin endpoints (8 cases) plus 401 unauthenticated and "API key is not an admin credential"; admin create/patch/deactivate/reset-totp behavior including audit and session termination; login and TOTP throttling under their own key namespace, with classify's namespace untouched; session classify writing the real user id vs. API key writing NULL; the exact-bytes API-key regression; the bootstrap CLI.

## Honest caveats

- **Tests were written after the implementation, not before.** That departs from the red-first standard in your CLAUDE.md. To compensate, I mutation-checked the suite: 13 deliberate breakages (cookie `Secure`/`HttpOnly`/`SameSite` removed, RBAC check removed, must-change gate removed, inactive check removed, throttle sharing classify's key, `user_id` not written, session classify using the classify key, TOTP code not verified, session never used for classify, reset-totp not audited, TTL not slid) were each **caught by at least one test**, and the files were restored (status clean). The one later fix (below) was done red-first.
- **A real classification through a session was not exercised live.** A live `POST /classify` needs the Meta Model API and object storage. Session-classify attribution (`audit_log.user_id`) is verified by the mocked-pipeline tests, not on the live stack.
- **Live checks ran with TLS verification off** against Caddy's `tls internal` certificate (local dev CA), via `httpx(verify=False)`. That was only for talking to localhost; do not copy it elsewhere.
- **The live dev database now contains the test accounts** (`e2e-admin@example.com`, `e2e-analyst@example.com`) from the verification run, in local Docker volumes only.

## Security review of the committed code

An automated review of the pushed commits raised two findings:

1. **Sessions survived a password change: fixed** (`ff463b6`). `change-password` now ends the user's other sessions and keeps the current one (red-first test: `test_changing_password_ends_the_users_other_sessions_but_not_the_current_one`). Outstanding login tokens are left to expire: a challenge token still needs a valid TOTP code and lives 5 minutes.
2. **Login throttle keyed on email lets a third party exhaust a victim's login budget (DoS): acknowledged, not changed.** It is real: someone who knows an email can send 10 bad logins and block that account for the rest of the hour, even with the right password. This follows from the brief's design (rate-limit login attempts, no separate lockout state). The suggested fix, an IP-based key, is not reliable here yet: behind Caddy the backend sees the proxy's IP, so a correct per-IP limit needs a trusted-proxy / `X-Forwarded-For` configuration decision first. Counting only failed attempts would not remove the problem, since the attacker's attempts are the failed ones. Recommended follow-up once the proxy setup is settled: key on `(client IP, email)` plus a separate per-IP ceiling.

## Additions beyond the brief (small, flagged)

- **Enrollment endpoints** (`enroll/begin`, `enroll/confirm`): the brief requires an "enrollment-required" login result and tests TOTP enrollment but names no endpoint to enroll; these are the minimum.
- **`python -m app.auth.create_admin <email>`**: with no registration route and every admin route behind an admin session, the first admin must exist out of band. Run: `docker compose exec -e PYTHONPATH=backend api python -m app.auth.create_admin admin@example.com`. It prints a random initial password once; only the hash is stored.
- **Per-user session index and `destroy_all_sessions_for_user`**, so deactivation and `reset-totp` actually end live sessions. The user row (not the role copied into the session JSON) is authoritative for `is_active`, `role` and `must_change_password` on every request, so demotion and deactivation take effect immediately.
- **Self-protection:** an admin cannot deactivate or demote their own account (400). Two admins could still demote each other; there is no "last admin" guard.
- **TOTP replay guard** and **minimum 12-character password** on change.

## Decisions for you

- **Trust model of enrollment:** the initial password an admin hands over is the bootstrap credential. Whoever holds it and reaches enrollment first binds their authenticator to the account; `reset-totp` re-opens the same window. Acceptable for a no-email design, but it means the initial password must be delivered over a trusted channel.
- **Classify is open to all three roles.** The brief does not say which roles may classify, so any active, password-changed user can. If auditors should be read-only, that is one extra `require_role` check; but no audit-read endpoints exist yet either.
- **Admin audit coverage:** only `reset-totp` writes an audit entry, as specified. User creation, role changes and deactivation are not audited.

## Known gaps / out of scope

- No email/invitation system, no account-lockout state machine beyond rate limiting, no CSRF tokens (as instructed). `SameSite=Strict` is the only cross-site mitigation.
- No login UI; the Module 5 frontend still works anonymously through Caddy's injected key.
- Stale prose: the `CALIPER_API_KEY` description in `secrets.py` and the docstring of `api/auth.py` still say TOTP/RBAC are out of scope. I left `api/auth.py` untouched on purpose; the `secrets.py` text is cosmetic.
- `AuditLogEntry.classification_result_id` is still always `None` (Module 4's known gap).
