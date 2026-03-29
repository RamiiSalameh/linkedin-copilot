from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from rich.console import Console

console = Console()


def ensure_data_dirs() -> None:
    """Create required data directories."""
    for sub in [
        "data",
        "data/resumes",
        "data/profiles",
        "data/exports",
        "data/screenshots",
        "data/raw_jobs",
        "data/logs",
    ]:
        Path(sub).mkdir(parents=True, exist_ok=True)


def iso_now() -> str:
    return datetime.utcnow().isoformat()


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def prompt_yes_no(message: str) -> bool:
    """Prompt user in terminal for yes/no confirmation."""
    console.print(f"[bold yellow]{message}[/bold yellow] [y/N]: ", end="")
    sys.stdout.flush()
    answer = input().strip().lower()
    return answer in {"y", "yes"}


def timestamped_filename(prefix: str, suffix: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{ts}{suffix}"

