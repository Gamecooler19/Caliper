"""Intake parsing: free-text device descriptions -> IntendedUseProfile.

Owns ``parse_intended_use``, which calls Muse Spark (via the shared
``app.meta_api.client``) to resolve a plain-language intended-use
description into the four required IntendedUseProfile fields plus the
nullable energy_source. Raises rather than guesses whenever the model's
output doesn't confidently resolve a required field.
"""
