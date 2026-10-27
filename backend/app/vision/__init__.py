"""Vision pipeline: turns device media into a populated VisionFlags record.

This package owns the Meta Model API HTTP calls (SAM 3.1 segmentation,
Muse Spark multimodal understanding). It is a consumer of
``app.classification.models.VisionFlags`` from Module 1, but Module 1's
classification package itself remains zero-I/O and does not import
anything from here.
"""
