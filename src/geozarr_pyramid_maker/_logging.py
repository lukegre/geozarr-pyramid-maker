"""Opt-in logging setup. Library code must never touch loguru handlers (D-08)."""

import os
import sys

from loguru import logger


def configure_logging(level: str | None = None) -> None:
    """Replace all loguru handlers with a single stderr sink.

    Level precedence: an explicit ``level`` argument wins; otherwise the ``LOGURU_LEVEL``
    environment variable; otherwise ``"INFO"``. This is the only place the library touches
    loguru handlers, and it only runs when the caller opts in (D-08).
    """
    if level is None:
        level = os.environ.get("LOGURU_LEVEL", "INFO")
    logger.remove()
    logger.add(sys.stderr, level=level.upper())
