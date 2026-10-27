"""Report generation: ClassificationResult -> readable report text.

Owns ``generate_report``, which calls Muse Spark (via the shared
``app.meta_api.client``) to write plain-language prose summarizing a
ClassificationResult, then appends the result's fixed disclaimer
verbatim and programmatically -- the model is never trusted to
reproduce that string itself.
"""
