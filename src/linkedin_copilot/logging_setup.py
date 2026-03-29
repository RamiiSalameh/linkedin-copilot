from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from .config import get_settings


def setup_logging() -> None:
    """Configure loguru logging with file and stderr sinks."""
    settings = get_settings()
    log_dir = Path(settings.logging.get("log_dir", "./data/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()  # remove default sink
    level = settings.env.log_level.upper()

    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )
    logger.add(
        log_dir / "linkedin_copilot.log",
        level=level,
        rotation="10 MB",
        retention="10 days",
        enqueue=True,
    )


__all__ = ["setup_logging", "logger"]

