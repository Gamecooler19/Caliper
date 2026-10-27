"""Vision-versus-stated-profile consistency checking.

Owns ``check_consistency``, a pure function with zero I/O and zero
network imports that compares a device's stated :class:`IntendedUseProfile`
against its vision-derived :class:`VisionFlags` and reports discrepancies
for human review. See module docstring in ``consistency.py`` for the
explicit semantic analysis of what each VisionFlags field actually
measures and why the comparison is only sound in the directions
implemented.

This package never feeds into or is read by
``app.classification.engine``; ``classify()`` is untouched by Module 4
and does not accept or get influenced by ConsistencyFlag data.
"""
