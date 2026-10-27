# Contributing to Caliper

Caliper is a pre-assessment copilot for medical device risk classification under India's Medical Devices Rules, 2017. It's licensed for noncommercial, healthcare, and NGO use under the terms in `LICENSE.md` — read that first if you're unsure whether your intended use is covered.

## The most valuable contribution you can make

The classification logic lives in `backend/app/classification/rules_data.py`, sourced against publicly available summaries of the MDR 2017 First Schedule and flagged `needs_verification: true` until each rule is checked against the verbatim gazette text. If you have access to the official notified text and can confirm, correct, or cite a specific rule, that's the single highest-value contribution this project can take. Open an issue tagged `rule-verification` with the rule ID, the gazette citation, and the exact text — not a paraphrase.

## Development setup

- Python 3.12, dependencies managed with `uv` (not `uvx`, not pip directly, not npm).
- `docker compose up` brings up the full stack: API, PostgreSQL, Valkey, Caddy.
- `pytest` runs the unit and integration suite; `backend/tests/test_classification_engine.py` is the one that matters most — it has to pass against every rule path before anything else is reviewed.
- `playwright test` runs the end-to-end flow once it exists (Module 4 onward).

## Commit conventions

Commits are typed `feat:`, `fix:`, or `iteration:`, chosen by what the commit actually did — not what was intended. During initial build-out, commits are scoped to a module: `feat: module 2 - vision pipeline ingest`. After v1 ships, ordinary scoped commits are fine: `fix: classification engine duration-tier edge case`.

## Pull requests

- One logical change per PR. A rule-verification fix and a feature addition are two PRs, not one.
- Tests pass before review, not after.
- If your change touches `classification-engine`, it has zero network calls and zero I/O — that's an architectural invariant, not a style preference (see `architecture.md`). A PR that breaks it will be asked to fix that before anything else.
- If your change touches the regulatory logic or output text, say in the PR description what in MDR 2017 supports it.

## Code of conduct

Be precise, be direct, and assume good faith. Disagreements about the actual regulation get resolved by citing the gazette text, not by argument.

## Security issues

Do not open a public issue for a security vulnerability. Email pradyumn@kanopus.org directly.
