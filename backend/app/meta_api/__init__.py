"""Shared Meta Model API transport.

This package owns the single, tested implementation of auth, error
handling, and Responses API response parsing that every Meta Model API
caller in this codebase (vision, intake parsing, report generation)
builds on, instead of each duplicating its own httpx calls.
"""
