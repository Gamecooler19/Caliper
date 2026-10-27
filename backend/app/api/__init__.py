"""API orchestration layer: the one HTTP surface that wires Modules 1-3
together behind auth and rate limiting.

Owns ``POST /api/v1/classify``, the shared-API-key auth dependency, and
the Valkey-backed rate limiter. Mounted into ``backend/app/main.py``
alongside the existing health check; nothing here is imported by, or
changes the behavior of, the classification engine, the vision
pipeline, intake parsing, or report generation.
"""
