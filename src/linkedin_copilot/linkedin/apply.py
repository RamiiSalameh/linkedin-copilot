from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from browser_use import Agent

from ..browser import create_browser_agent
from ..config import get_settings
from ..db import update_job_status
from ..logging_setup import logger
from ..models import ApplicationState, JobRecord, JobStatus, ScreeningQuestion
from ..llm import get_llm
from ..utils import save_json, timestamped_filename
from .forms import collect_screening_questions
from .safety import guard_before_submit


async def easy_apply(job: JobRecord, profile: Dict[str, object]) -> ApplicationState:
    """
    Orchestrate an Easy Apply flow for a given job.

    This function assumes that the user is already logged in to LinkedIn.
    It will never auto-submit unless configuration explicitly allows it.
    """
    s = get_settings()
    application = ApplicationState(job_url=str(job.url), started_at=datetime.utcnow())
    logs_dir = Path(s.data.get("exports_dir", "./data/exports"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    task = (
        "Open the given LinkedIn job URL and start the Easy Apply process.\n"
        "Fill in obvious non-sensitive fields (name, email, phone, LinkedIn, etc.).\n"
        "Do NOT click final submit or send the application.\n"
        "Stop on the review or submit step and clearly display a summary.\n"
        f"URL: {job.url}\n"
    )
    agent: Agent = create_browser_agent(task)
    logger.info("Starting Easy Apply flow for job {} - {}", job.id, job.url)
    result = await agent.run()
    application.last_step = "agent_run_completed"

    # Attempt best-effort extraction of screening questions/summaries if exposed by agent
    questions: List[ScreeningQuestion] = []
    try:
        if hasattr(result, "extracted_content") and isinstance(result.extracted_content, dict):
            raw = result.extracted_content
            for q in raw.get("screening_questions", []):
                questions.append(ScreeningQuestion(question_text=str(q)))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse screening questions from agent result: {}", exc)

    # Draft answers via LLM for review
    drafted_answers: Dict[str, str] = {}
    profile_json = json.dumps(profile, ensure_ascii=False)
    resume_path = Path(s.env.default_resume_path)
    resume_text = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""
    llm = get_llm()

    for q in questions:
        answer = llm.generate_screening_answer(profile_json, resume_text, q.question_text)
        q.answer_draft = answer
        drafted_answers[q.question_text] = answer
        logger.info("Drafted screening answer\nQ: {}\nA: {}", q.question_text, answer)

    # Respect human-in-the-loop safety for any submit-like action
    allowed = await guard_before_submit(f"Potential final submission for job {job.id}")
    if not allowed:
        application.status = JobStatus.AWAITING_REVIEW
        update_job_status(job.id or 0, JobStatus.AWAITING_REVIEW)
    else:
        application.status = JobStatus.SUBMITTED_MANUAL
        update_job_status(job.id or 0, JobStatus.SUBMITTED_MANUAL)

    export_path = logs_dir / timestamped_filename(f"application_{job.id}", ".json")
    save_json(
        export_path,
        {
            "job_id": job.id,
            "job_url": str(job.url),
            "started_at": application.started_at.isoformat(),
            "status": application.status.value,
            "questions": [q.model_dump() for q in questions],
            "drafted_answers": drafted_answers,
        },
    )
    logger.info("Saved application attempt to {}", export_path)
    return application

