from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from ..logging_setup import logger


def load_selectors(path: Path | None = None) -> Dict[str, Any]:
    """Load LinkedIn selectors from YAML, with basic validation."""
    if path is None:
        path = Path("config/selectors.yaml")
    if not path.exists():
        raise FileNotFoundError(f"selectors.yaml not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}
    logger.debug("Loaded selectors from {}", path)
    return data.get("linkedin", {})


SELECTORS = load_selectors()

