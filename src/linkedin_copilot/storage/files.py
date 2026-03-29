from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pandas as pd

from ..db import db_connection
from ..logging_setup import logger
from ..models import JobStatus


def export_jobs_csv(path: Path | None = None, statuses: Iterable[JobStatus] | None = None) -> Path:
    """Export job records to a CSV file."""
    if path is None:
        path = Path("./data/exports/jobs.csv")
    path.parent.mkdir(parents=True, exist_ok=True)

    query = "SELECT * FROM jobs"
    params: List[str] = []
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        query += f" WHERE status IN ({placeholders})"
        params = [s.value for s in statuses]

    with db_connection() as conn:
        df = pd.read_sql_query(query, conn, params=params or None)

    df.to_csv(path, index=False)
    logger.info("Exported {} jobs to {}", len(df), path)
    return path

