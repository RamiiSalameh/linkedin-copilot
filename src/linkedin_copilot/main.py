from __future__ import annotations

import asyncio

from .config import get_settings
from .db import init_db
from .logging_setup import setup_logging, logger
from .utils import ensure_data_dirs
from .cli import app


def bootstrap() -> None:
    """Common bootstrap for CLI entrypoint."""
    ensure_data_dirs()
    setup_logging()
    get_settings()
    init_db()


def main() -> None:
    """Run the Typer CLI."""
    bootstrap()
    app()


if __name__ == "__main__":
    main()

