"""Structured logging configuration."""
from __future__ import annotations

import logging
import sys

from app.core.config import Settings, get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(settings: Settings | None = None) -> logging.Logger:
    """Configure root and application loggers consistently."""
    active_settings = settings or get_settings()
    level = getattr(logging, active_settings.log_level, logging.INFO)

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    # Reduce noise from third-party libraries unless debugging
    if level > logging.DEBUG:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    logger = logging.getLogger("app")
    logger.setLevel(level)
    return logger
