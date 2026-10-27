# Caliper

Caliper is a pre-assessment copilot for Indian medical device risk classification under the Medical Devices Rules, 2017 (MDR 2017). From a device photo or short video plus a plain-language intended-use description, it estimates the device's risk class (A–D), the applicable licensing authority and CDSCO form, and whether clinical investigation is likely required — grounded in MDR 2017's First Schedule criteria rather than left to a model's guess.

Caliper does not replace the Drugs Controller General of India's classification authority, which is non-appealable once made. Every output is explicitly a pre-assessment estimate, not a determination, and states that in the output itself. See `architecture.md` at the repository root for the full design, data model, and build sequence.

## Status: Module 1

This repository currently contains Module 1 — the repository scaffold, CI/CD, the deterministic classification engine, and the non-Meta-API infrastructure (Valkey, secrets layer, audit logging). It's buildable and testable independently of any external model API. Modules 2 onward (vision, intake parsing, report generation, orchestration) are not yet wired in.

## Layout

- `architecture.md`, `LICENSE.md`, `CONTRIBUTING.md` — read these first.
- `backend/app/classification/` — the pure, network-free classification engine: data contracts (`models.py`), structured rule data (`rules_data.py`), and the engine itself (`engine.py`).
- `backend/app/security/` — the secrets-loading layer and the append-only audit log.
- `backend/app/main.py` — the FastAPI skeleton (health check only, for now).
- `backend/tests/` — the pytest suite; `test_classification_engine.py` covers every risk class and every error path.
- `alembic/` — database migrations.
- `docker-compose.yml`, `Caddyfile`, `Dockerfile` — the local/self-hosted deployment stack.

## Getting started

```bash
uv sync --all-groups
uv run pytest backend/tests
uv run ruff check .
uv run mypy backend/app
```

To run the full stack (API, PostgreSQL, Valkey, Caddy):

```bash
cp .env.example .env
docker compose up -d
docker compose exec api sh -c "cd /srv/caliper && uv run alembic upgrade head"
curl -k https://localhost/health
```

See `CONTRIBUTING.md` for development conventions, including why the classification rules in `rules_data.py` are marked `needs_verification: true` and how to help fix that.
