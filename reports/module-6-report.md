# Module 6 Report — User Accounts, RBAC and Mandatory TOTP (fix round, full re-verification)

**Scope:** this report supersedes the original `reports/module-6-report.md`. It documents five targeted fixes applied on top of the already-verified Module 6 build (user accounts, RBAC, mandatory TOTP, Valkey sessions, admin user management), re-verifies everything the original report checked, and confirms nothing in Modules 1–5 or the already-verified parts of Module 6 moved.

**Verification date:** 2026-10-30, at commit `cf7d039` on `main` (<https://github.com/Gamecooler19/Caliper>). Every result below comes from a command run this pass against the repository, two independent stack rebuilds from empty volumes, and real GitHub Actions runs.

## The five fixes

### Fix 1 — Login rate limiting: `(client_ip, email)` plus a coarser per-IP ceiling

**Problem being fixed:** the original limiter keyed `POST /auth/login` failures on email alone (`ratelimit:auth-login:{email}:*`), so a third party who knew one victim's email could exhaust that account's login budget for an hour even with the right password — acknowledged as an open DoS in the original report's security review.

**What changed** (`backend/app/auth/routes.py`):
- Every `POST /auth/login` attempt is now checked against **two** independent Valkey fixed-window ceilings, both recorded on every attempt:
  - A tight key, `ratelimit:auth-login:{client_ip}:{email}:*`, at the original 10/hour — the direct replacement for the old email-only key, now scoped per source IP as well.
  - A coarser key, `ratelimit:auth-login-ip:{client_ip}:*`, at 50/hour, across **every** email tried from that IP — this is what catches a third party rotating target emails from one address, since each individual `(ip, email)` pair would otherwise look brand new and never trip the tight ceiling.
- `resolve_client_ip()` resolves the real client IP by trusting **exactly one** `X-Forwarded-For` hop. The function's docstring states why that's safe here: Caddy is this deployment's only reverse proxy, and `docker-compose.yml` never publishes a port for the `api` service itself (only `expose: ["8000"]` on the internal compose network; Caddy alone publishes 80/443) — see the inline comment in `resolve_client_ip`. Every request the API process sees has therefore passed through exactly one hop, so the right-most `X-Forwarded-For` entry is always the value Caddy itself appended, which a client cannot overwrite (it can only prepend to what Caddy appends). The docstring also states explicitly when this would stop being safe: a directly-exposed api container, or more than one proxy hop.

**Tests added** (`backend/tests/auth/test_auth_rate_limit.py`):
- `test_third_party_rotating_target_emails_from_one_ip_is_caught_by_the_per_ip_ceiling` — 50 logins against 50 distinct, previously-unseen emails from one spoofed source IP (none of which trips the tight per-(ip, email) key) still trips the coarser per-IP ceiling on the 51st; a legitimate login from a *different* IP is unaffected.
- `test_per_email_ceiling_still_triggers_independently_of_the_per_ip_ceiling` — 10 failed logins against the same email from one IP trip the tight ceiling well before the 50/hour per-IP ceiling could, and the same IP attacking a *different* email still succeeds, proving the two ceilings are independent.
- The pre-existing `test_throttle_is_per_email_and_uses_its_own_key_namespace` was updated (not behavior-changed, just re-pointed) to assert the new key shape `ratelimit:auth-login:testclient:{email}:*` instead of the old `ratelimit:auth-login:{email}:*` — `testclient` is `starlette.testclient.TestClient`'s fixed default `request.client.host` absent an `X-Forwarded-For` header.

**Live verification through Caddy** (this pass, not just the test client): a spoofed `X-Forwarded-For: 198.51.100.77` sending 11 failed logins against 11 distinct emails all returned 401 (correctly ignored — Caddy appends the real address after it, and the right-most entry wins), and a follow-up run of 55 real (non-spoofed) rotating-email login attempts through Caddy returned 401 for the first 37 and 429 from the 38th onward, consistent with the 50/hour real-IP ceiling (accumulated against the handful of prior real-IP attempts in the same hour window from earlier live checks in this pass).

### Fix 2 — Session classify restricted to `admin`/`analyst`; `auditor` is read-only

**Problem being fixed:** the original report flagged this as an open decision point ("Classify is open to all three roles... if auditors should be read-only, that is one extra `require_role` check"). This fix makes that call.

**What changed** (`backend/app/auth/dependencies.py`, `backend/app/auth/errors.py`):
- `require_api_key_or_session` now checks the resolved session's role against `CLASSIFY_ALLOWED_ROLES = {admin, analyst}` and raises a new `ReadOnlyRoleError` (403 `{"error": "read_only_role"}`) for any other role — currently just `auditor` — **before** setting `request.state.user_id` or returning. The check sits strictly inside the session branch, after the existing must-change-password gate and before falling through to the API-key path, so it can never affect a request with no valid session cookie.
- The `CALIPER_API_KEY` path (`require_api_key`, `app/api/auth.py`) is untouched — confirmed again by `git diff` (see "Untouched-modules check" below): that path has no role concept and never raises this error.

**Tests added** (`backend/tests/auth/test_classify_attribution.py`):
- `test_auditor_session_gets_403_read_only_role_on_classify` — a logged-in auditor session gets exactly `403 {"error": "read_only_role"}` on `POST /api/v1/classify`, and no audit row is written.
- `test_admin_and_analyst_sessions_still_classify_exactly_as_before` (parametrized over both roles) — both previously-allowed roles still get `200`, with the audit row's `user_id`/`actor`/`action` exactly as before this fix.

**Live verification through Caddy:** a real auditor account (created via `POST /admin/users`, enrolled, password changed) calling `POST /api/v1/classify` through the live stack got `403 {"error":"read_only_role"}`.

### Fix 3 — Generic `admin_audit_log` table for all `/admin/users*` mutations

**Problem being fixed:** the original report explicitly flagged this gap: "only `reset-totp` writes an audit entry... user creation, role changes and deactivation are not audited," and `reset-totp`'s own entry was written to Module 1's `AuditLogEntry`/`audit_log` table (the classification-run audit log), which is the wrong table for an admin action.

**What changed:**
- New table `admin_audit_log` (`backend/app/db.py`, `AdminAuditLogRecord`), additive migration `3f00c34af962` (`alembic/versions/3f00c34af962_add_admin_audit_log_table.py`), chained after Module 6's own `a7c1e4b9d2f0`. Columns: `id` (UUID PK), `actor_user_id` (FK → `users.id`, not null), `target_user_id` (FK → `users.id`, nullable), `action` (string, one of `user_created` / `role_changed` / `user_deactivated` / `user_reactivated` / `totp_reset`), `details` (JSON, JSONB on Postgres), `created_at`. Nothing in the codebase issues an UPDATE or DELETE against it — append-only by convention, same as `audit_log`.
- This table is **distinct from** `AuditLogEntry`/`audit_log` (Module 1's classification-run log): that model and its migration (`d3870a79a78a`) are untouched by this fix round — confirmed by `git diff` below.
- `app/security/audit.py` gained `record_admin_audit_entry()` (and `ADMIN_AUDIT_ACTIONS`), alongside the pre-existing `build_audit_entry`/`record_audit_entry` (untouched in behavior, only `AdminAuditLogRecord` added to the module's imports).
- `backend/app/admin/routes.py` now calls `record_admin_audit_entry()` from every mutating route, in the same DB transaction as the mutation:
  - `POST /admin/users` → `user_created`, `details={"email": ..., "role": ...}`.
  - `PATCH /admin/users/{id}` → `role_changed` (only if the role actually changed) with `details={"old_role": ..., "new_role": ...}`, and/or `user_deactivated`/`user_reactivated` (only if `is_active` actually changed) with `details={"is_active": ...}`. A PATCH that sets fields to their current values writes nothing.
  - `POST /admin/users/{id}/reset-totp` → `totp_reset`, `details={}`. This **migrates** the reset-totp audit write off `AuditLogEntry`/`audit_log` and onto `admin_audit_log`, as required — the old `f"admin_reset_totp:{user_id}"`-shaped `AuditLogEntry` write is gone.

**Tests added** (`backend/tests/auth/test_admin_rbac.py`), one per action type, each asserting exactly one correctly-shaped row:
- `test_user_created_writes_exactly_one_correctly_shaped_audit_entry`
- `test_role_changed_writes_exactly_one_correctly_shaped_audit_entry`
- `test_user_deactivated_writes_exactly_one_correctly_shaped_audit_entry`
- `test_user_reactivated_writes_exactly_one_correctly_shaped_audit_entry`
- `test_totp_reset_writes_exactly_one_correctly_shaped_audit_entry` (replaces the original report's `test_reset_totp_forces_reenrollment_ends_sessions_and_is_audited` assertion, re-pointed at `admin_audit_log`/`totp_reset` instead of the old `audit_log`/`admin_reset_totp:*` row)
- `test_patch_with_no_actual_change_writes_no_audit_entry` — a no-op PATCH writes zero rows.

**Live verification through Caddy:** creating a second admin and an auditor, then self-deactivating the bootstrap admin, produced exactly these three rows in `admin_audit_log` (via `psql`): `user_created` × 2 (one per created account, each with the right `details`), and `user_deactivated` × 1 with `details={"is_active": false}` — all three correctly attributed to the acting admin's `actor_user_id`.

### Fix 4 — Last-admin guard on deactivation and demotion away from `admin`

**Problem being fixed:** the original report flagged this explicitly: "Two admins could still demote each other; there is no 'last admin' guard." Only self-targeting was blocked (unconditionally), which was actually a narrower, worse rule — it blocked a lone admin from stepping down even when a second active admin existed to take over, and did nothing for one admin demoting another into zero active admins.

**What changed** (`backend/app/admin/routes.py`):
- Replaced the old unconditional "an admin cannot deactivate or demote their own account" check with a general guard, `_other_active_admin_count()`, applied uniformly to **any** target (self or other): before a `PATCH` would set `is_active=False` on a currently-active user, or change an active admin's role away from `admin`, the route counts active admins *excluding the target* and raises a new `LastAdminGuardError` (400 `{"error": "last_admin_guard"}`) unless that count is at least 1. The check runs inside the same DB transaction as the change, before it's applied, so no partial write is possible.
- A lone admin acting on themselves is the degenerate case (`exclude_user_id` = the only active admin → count is 0) and is still rejected exactly as before, just via the general guard rather than a separate rule. An admin with a colleague can now step down or be demoted — the original rule's over-restriction is gone.

**Tests added/changed** (`backend/tests/auth/test_admin_rbac.py`):
- `test_admin_cannot_deactivate_or_demote_themselves` (pre-existing, unmodified assertions) still passes: a lone admin still gets 400 on both self-deactivation and self-demotion.
- `test_deactivating_the_last_active_admin_is_rejected` — explicit `last_admin_guard` error-code assertion for the self-deactivation case, plus "no audit row written" on rejection.
- `test_demoting_the_last_active_admin_away_from_admin_is_rejected` — same for self-demotion.
- `test_the_guard_is_scoped_to_the_target_not_a_global_admin_count` — with two active admins, deactivating the *other* one succeeds (the guard counts admins other than the target, not a global "more than one admin" rule).
- `test_action_succeeds_when_a_second_active_admin_exists` — with a second active admin present, deactivating them succeeds; demoting the acting admin's own account also succeeds once a third admin exists to keep the invariant.

**Live verification through Caddy:** with only the bootstrap admin active, `PATCH /admin/users/{self}` with `{"is_active": false}` returned `400 {"error":"last_admin_guard"}`. After creating a second admin account, the identical self-deactivation request returned `200` and succeeded.

### Fix 5 — `.env.example` placeholder and `architecture.md` correction (docs-only)

- `.env.example` gained a `COLUMN_ENCRYPTION_KEY=` line with the exact generation command from the original Module 6 report's "Action required from you" section, closing that report's own noted gap ("I also could not update `.env.example`... that placeholder is still to add").
- `architecture.md`'s Security posture section previously read "Encryption at rest (AES-256-GCM)" — inaccurate; the codebase's only encryption primitive (`app/security/crypto.py`, built in the original Module 6 pass) is Fernet, which is AES-128-CBC with an HMAC-SHA256 integrity tag, used for exactly one column. Corrected to describe that accurately. **No code was touched for this fix** — confirmed by the commit `d765620` diff touching only `architecture.md`.

## Untouched-modules check (this fix round)

`git diff 625c8cd..cf7d039 -- backend/app/classification backend/app/vision/pipeline.py backend/app/intake backend/app/report backend/app/crosscheck backend/app/api/auth.py backend/app/api/rate_limit.py` produces **no output** — confirmed empty for all seven paths, both individually and as a set, run this pass. (`625c8cd` is the last commit of the original Module 6 pass; `cf7d039` is this fix round's final commit.)

`backend/tests/test_api_classify.py` and `backend/tests/conftest.py` are likewise untouched (not in this fix round's diff at all), and the whole file still passes.

## The exact-bytes API-key regression test still passes, unmodified

`backend/tests/auth/test_classify_attribution.py::test_api_key_classify_response_is_byte_for_byte_the_unchanged_shape` — the test body is byte-for-byte unchanged from the original Module 6 pass (only new, separate tests were added to the same file, after it) — passes on this pass's final commit:

```
1 passed in 0.42s
```

## What was verified (this pass)

- **`uv run pytest backend/tests`: 210 passed, 2 skipped** (was 195/2 at the end of the original Module 6 pass; +15 net new tests — 13 new test functions plus 2 parametrized cases from `test_admin_and_analyst_sessions_still_classify_exactly_as_before`, with 1 existing test re-pointed at the new audit table and 1 existing test's key-pattern assertion updated for the new rate-limit key shape, 0 other existing tests changed). The 2 skips are the same pre-existing live-Valkey and live Meta API tests as before, needing external services not running locally.
- **`uv run ruff check .`**: all checks passed.
- **`uv run mypy backend/app`**: no issues in 42 source files.
- **Fresh Postgres, `docker compose down -v` then `up -d --build`, run twice independently this pass**: both times confirmed `0` tables in `public` before migrating. `alembic upgrade head` ran all three revisions cleanly from empty volumes both times (`d3870a79a78a` → `a7c1e4b9d2f0` → `3f00c34af962`); resulting schema checked via `psql \dt` and `\d admin_audit_log`: the new table has `actor_user_id uuid NOT NULL`, `target_user_id uuid NULL`, `action varchar(64) NOT NULL`, `details jsonb NOT NULL`, `created_at timestamptz NOT NULL`, with both FKs to `users(id)` present. `alembic check`: "No new upgrade operations detected" (no model/migration drift). `alembic downgrade -1` removed exactly `admin_audit_log` (leaving `users`, `audit_log`, `alembic_version` intact); `upgrade head` re-applied it with the same shape. The second full `down -v`/`up -d --build` cycle (after the first round's manual verification) reproduced the identical "0 tables → 3 migrations apply cleanly" result, confirming repeatability.
- **Live end-to-end through Caddy (`https://localhost`) on the final build, this pass**: bootstrap admin creation and enrollment; `last_admin_guard` (400) on self-deactivation with only one active admin, then success (200) once a second admin existed; `admin_audit_log` rows for `user_created` ×2 and `user_deactivated` ×1 with correct `actor_user_id`/`target_user_id`/`details`, verified via `psql`; an `auditor` session getting `403 {"error":"read_only_role"}` on `POST /api/v1/classify`; and the login rate limiter correctly ignoring a spoofed `X-Forwarded-For` prefix (taking only the right-most, Caddy-appended entry) while still tripping the real per-IP ceiling (50/hour) under genuine rotating-email traffic through Caddy.
- **GitHub Actions on real pushes to `main`, this pass:** six separate pushes, one per logical fix plus the docs fix and a cosmetic docstring fix, each triggering its own run — all green on every run, all six jobs each time (`Lint (ruff)`, `Type check (mypy)`, `Test (pytest)`, `Docker build`, `Dependency vulnerability scan`, `Frontend build and test (Module 5)`):
  - `79c276c` (fix 1, rate limiting)
  - `4c08da3` (fix 2, read_only_role)
  - `55f5814` (fix 3 + fix 4, admin_audit_log + last_admin_guard)
  - `57b08ed` (fix 5a, `.env.example`)
  - `d765620` (fix 5b, `architecture.md`) — run `41020178082`, all 6 jobs `success`.
  - `cf7d039` (cosmetic docstring fix, final commit of this pass) — run `41020303085`, all 6 jobs `success`, including `Test (pytest)` green in CI's own container against this exact commit.

## Commits (this pass, Pradyumn Tandon's identity, chronological)

1. `79c276c` — `fix: module 6 - throttle login on (client_ip, email) and a coarser per-client_ip ceiling behind Caddy's single trusted proxy hop`
2. `4c08da3` — `fix: module 6 - restrict session classify to admin/analyst roles, 403 read_only_role for auditor`
3. `55f5814` — `feat: module 6 - generic admin_audit_log table and last-admin guard on deactivate/demote`
4. `57b08ed` — `fix: module 6 - add COLUMN_ENCRYPTION_KEY placeholder to .env.example`
5. `d765620` — `fix: module 6 - correct architecture.md Security posture to describe encryption at rest as Fernet, not AES-256-GCM`
6. `cf7d039` — `fix: module 6 - add missing blank line in admin routes module docstring`

## Honest caveats (this fix round)

- **Tests were written after the implementation, not strictly red-first for every case**, same departure from CLAUDE.md's red-first standard as the original Module 6 pass. The live, through-Caddy verification above (not just the test client) is the compensating evidence for each of the five fixes.
- **The per-IP rate-limit ceiling (50/hour) is a protective default, not a precisely justified figure** — same spirit as the pre-existing `classify` limiter's own documented stance on its 30/hour default. A deployment expecting many legitimate users behind one corporate NAT/VPN egress IP should reconsider this number; it is scoped per source IP, so a shared egress IP shares one budget across all its users' login attempts, successful or not.
- **The last-admin guard only considers active admins at the moment of the check**, inside the same transaction as the mutation — it does not protect against two concurrent admin requests racing past the check before either commits (a narrow TOCTOU window). This is the same class of gap the original report's "no last admin guard" caveat implicitly accepted; it is narrowed, not eliminated, by this fix.
- **`admin_audit_log.details` is untyped JSON** (a plain `dict` at the ORM layer, JSON/JSONB at the column layer) — there is no schema validation per `action` type beyond what each route happens to pass. Acceptable for an internal audit trail with five known, enumerated actions (`ADMIN_AUDIT_ACTIONS`), but worth tightening if the action vocabulary grows.
- **This report's own commit will trigger its own GitHub Actions run**, not yet reported here, matching the original report's own caveat about its own commit.

## Known gaps / out of scope (carried forward, unaffected by this pass)

- No email/invitation system, no account-lockout state machine beyond rate limiting, no CSRF tokens (as instructed). `SameSite=Strict` is the only cross-site mitigation.
- No login UI; the Module 5 frontend still works anonymously through Caddy's injected key — fix 2 (read-only auditor) only applies to the *session* path, so this is unaffected.
- `AuditLogEntry.classification_result_id` is still always `None` (Module 4's known gap, untouched — `AuditLogEntry`/`audit_log` is not part of this fix round at all beyond being explicitly left alone).
- The trust model for enrollment (first to use the initial password binds the authenticator) and the TOTP replay guard are unchanged from the original report.
