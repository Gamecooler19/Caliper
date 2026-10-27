# Caliper — Architecture

## Overview

Caliper is a pre-assessment copilot for Indian medical device risk classification under the Medical Devices Rules, 2017 (MDR 2017). Given a photo or short video of a device plus a plain-language description of its intended use, Caliper estimates the device's risk class (A–D), the licensing authority and CDSCO form that applies, and whether clinical investigation is likely required — grounded in the actual classification criteria in MDR 2017's First Schedule, not a model's guess.

Caliper does not replace DCGI's classification authority. Under MDR 2017, device classification is decided by the Drugs Controller General of India and is non-appealable once made — manufacturers and importers don't have the self-classification liberty some other jurisdictions allow. Every output Caliper produces is explicitly a pre-assessment estimate, not a determination, and says so in the output itself.

## Why this exists

Manufacturers and importers currently find out their device's classification — and the regulatory pathway that follows from it — only after formal submission to CDSCO. Caliper gives them a fast, evidence-grounded estimate beforehand, so they can scope licensing authority, required forms, and clinical investigation exposure while they're still preparing a submission, not after.

## Core architecture decision: deterministic classification, LLM-assisted input and output

The regulatory determination itself is computed by a pure, deterministic rules engine — not inferred by a language model. Two alternatives were considered and rejected:

- **A single end-to-end LLM call** reasoning over uploaded media and a free-text description. Rejected: it makes the actual classification language-model-guessable, which is the one failure mode this product can't carry.
- **A multi-agent pipeline**, one model call per stage, chained. Rejected for v1: more failure points and latency per request than the problem justifies, without making the classification itself more correct.

Vision and language models handle everything on either side of the decision — extracting structured facts going in, writing a readable report coming out — while a deterministic, unit-tested rules engine makes the decision itself, auditable as code rather than as a prompt.

## Data flow

1. **Ingest** — user uploads device photo/video and a free-text intended-use description.
2. **Vision** — SAM 3 segments and tracks the device and its relevant sub-components; SAM 3D reconstructs it to extract physical features (dimensions, surface contact, invasive elements) relevant to classification.
3. **Intake parsing** — Muse Spark (Meta Model API) converts the free-text description into structured fields: invasiveness tier, duration of contact, active/non-active, body system contacted.
4. **Cross-check** — vision-extracted features are compared against the stated profile; mismatches are flagged, not silently resolved in either direction.
5. **Classification** — the deterministic rules engine maps the structured profile to a risk class, matched rule, licensing authority, applicable form, and clinical investigation flag.
6. **Report** — Muse Spark turns the structured result into a readable report. The pre-assessment disclaimer is part of the default output, not an afterthought.

## Data model

- **IntendedUseProfile** — `invasiveness_tier` (non_invasive | invasive | surgically_invasive | implantable), `duration_tier` (transient | short_term | long_term), `device_category` (active | non_active), `body_system_contacted` (string), `energy_source` (nullable string)
- **VisionFlags** — `visual_evidence_implantable` (nullable bool), `visual_evidence_duration_conflict` (nullable bool)
- **RuleDefinition** — `rule_id`, `source_citation` (fixed: "First Schedule, Medical Devices Rules 2017"), `criteria` (description), `resulting_class` (A | B | C | D), `needs_verification` (bool — true until the verbatim gazette text replaces the sourced summary criteria currently in use)
- **ClassificationResult** — `risk_class` (A | B | C | D), `matched_rule_id`, `applicable_licensing_authority` (State | Central), `applicable_form` (MD-3 | MD-5 | None), `clinical_investigation_required` (bool), `clinical_investigation_basis` (string — predicate-device reasoning), `disclaimer` (fixed pre-assessment language)

## Module boundaries

| Module | Owns | Never touches |
|---|---|---|
| media-ingest | Upload handling, object storage | Classification logic |
| vision-pipeline | SAM 3 / SAM 3D calls, feature extraction into VisionFlags | MDR rules, UI |
| intake-parser | Muse Spark call, free text → IntendedUseProfile | Classification reasoning |
| classification-engine | Pure functions, zero network calls, zero I/O: IntendedUseProfile + VisionFlags → ClassificationResult | Any model call |
| report-generator | Muse Spark call, ClassificationResult → readable report | The classification itself — read-only consumer |
| api-gateway | FastAPI orchestration, request/response schemas, auth, rate limiting | Business logic |

## Deployment model

Self-hosted, single-tenant. One deployment per organization, own database, own Meta Model API key (bring-your-own-key — Caliper never ships with or defaults to a shared key). No cross-tenant data model and no row-level security scheme, because there's no cross-tenant data to isolate. The organization's device and classification data never leaves infrastructure it controls.

## Tech stack

Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, `uv` for dependency management. PostgreSQL 16 as the only persistent datastore. Valkey as the Celery broker for the async vision-processing job queue, as the API rate limiter's backing store, and for response caching. S3-compatible object storage for uploaded media. Docker Compose with Caddy as reverse proxy and TLS termination. pytest for unit and integration coverage, Playwright for the end-to-end upload-to-report flow.

## Security posture

Encryption at rest (AES-256-GCM) and in transit (TLS, enforced end to end via Caddy). Secrets — including each deployment's Meta Model API key — held in a dedicated secrets layer, never in environment files committed to the repo or hardcoded. RBAC and TOTP MFA on every account. An append-only audit log recording who ran a classification, what was submitted, and what was returned. Dependency and container vulnerability scanning runs in CI on every change, not as a one-time check.

Caliper's security controls are built to the standard an auditor would check for SOC 2 Type II or ISO 27001 — encryption, access control, audit logging, change management. Neither certification is claimed anywhere in this project, because both are earned through an external audit observing controls operate correctly over months, not something a codebase has on day one. The architecture is built so that audit isn't blocked later; it isn't a substitute for one.

## Regulatory data scope

Caliper processes device specifications and classification output — not patient data. Unless an uploaded photo or video incidentally captures an identifiable person, no personal data under India's Digital Personal Data Protection Act, 2023 is expected to enter the system in normal use. This is a scoping statement, not a blanket compliance claim: a deployment that does capture personal data takes on DPDP Act obligations like any other data fiduciary, and that responsibility sits with the deploying organization, consistent with the self-hosted model.

## Build sequence

- **Module 1** — repository scaffold, CI/CD, the deterministic classification engine, and all non-Meta-API infrastructure (Valkey, secrets layer, audit logging). Buildable before Meta's hackathon API access opens.
- **Module 2** — vision pipeline (SAM 3, SAM 3D). Gated on kickoff.
- **Module 3** — intake parsing and report generation (Muse Spark). Gated on kickoff.
- **Module 4** — API orchestration and the demo-facing UI.
- **Module 5** (stretch) — interactive correction loop: user confirms or overrides extracted fields before final classification.
