from __future__ import annotations

from pathlib import Path

from ..logging_setup import logger
from ..models import MatchResult
from ..utils import save_json


def export_match_result(result: MatchResult, path: Path | None = None) -> Path:
    """Export a single match result to JSON."""
    if path is None:
        path = Path("./data/exports") / f"match_result_job_{result.job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(
        path,
        {
            "job_id": result.job_id,
            "match_score": result.match_score,
            "top_reasons": result.top_reasons,
            "missing_requirements": result.missing_requirements,
            "suggested_resume_bullets": result.suggested_resume_bullets,
            "summary_markdown_path": result.summary_markdown_path,
            "raw_json_path": result.raw_json_path,
        },
    )
    logger.info("Exported match result for job {} to {}", result.job_id, path)
    return path

