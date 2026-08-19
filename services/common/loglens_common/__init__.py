"""Shared building blocks for the LogLens observability platform.

Everything here is intentionally dependency-light so the exact same code can be
imported by the log generator, the Spark driver/executors, the FastAPI service,
the background worker and the offline ML training pipeline.
"""

from .config import Settings, settings  # noqa: F401

__version__ = "1.0.0"
