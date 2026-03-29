from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import ApplicationState


@dataclass
class SessionState:
    """In-memory session state for a single run."""

    session_id: str
    started_at: datetime
    applications: List[ApplicationState] = field(default_factory=list)

    def add_application(self, app: ApplicationState) -> None:
        self.applications.append(app)


def session_state_path() -> Path:
    return Path("./data/logs/session_state.json")

