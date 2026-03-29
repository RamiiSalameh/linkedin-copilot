from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import get_settings
from .db import (
    claim_next_pipeline_task,
    get_job_by_id,
    get_job_full_description,
    mark_pipeline_task_cancelled,
    mark_pipeline_task_failed,
    mark_pipeline_task_succeeded,
    update_job_description,
    update_job_status,
)
from .logging_setup import logger
from .models import JobSource, JobStatus, PipelineTaskType
from .linkedin.extract import scrape_job_description
from .scoring.matcher import load_profile


@dataclass(frozen=True)
class WorkerHandle:
    stop_event: asyncio.Event
    task: asyncio.Task[None]


async def _execute_scrape_job_description(job_id: int) -> None:
    job = get_job_by_id(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    # Idempotency: skip if description already exists
    if get_job_full_description(job_id):
        if job.status == JobStatus.PENDING_SCRAPE:
            update_job_status(job_id, JobStatus.PENDING_MATCH)
        return

    logger.info("Worker: scraping description for job {} ({})", job_id, job.title)
    if job.source == JobSource.WORKDAY:
        from .careers.workday import WorkdayScraper

        scraper = WorkdayScraper()
        description = await scraper.fetch_job_details(job)
        if not description or len(description.strip()) < 50:
            raise RuntimeError("Workday description extraction returned empty/short result")
        update_job_description(job_id, description)
        update_job_status(job_id, JobStatus.PENDING_MATCH)
        return

    await scrape_job_description(job)
    update_job_status(job_id, JobStatus.PENDING_MATCH)


async def _execute_match_job(job_id: int, *, llm_semaphore: asyncio.Semaphore) -> None:
    job = get_job_by_id(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    # Idempotency: only match if not already matched
    if job.status == JobStatus.MATCHED:
        return

    description = get_job_full_description(job_id) or job.description_snippet or ""
    if not description or len(description) < 50:
        # If it isn't ready to match yet, treat as a transient condition
        raise RuntimeError("No usable description available yet for matching")

    profile = load_profile()
    async with llm_semaphore:
        # Blocking LLM call runs in thread pool; import locally to avoid circulars.
        from .web import _match_single_job_sync  # pylint: disable=import-outside-toplevel

        ok = await asyncio.to_thread(_match_single_job_sync, job, profile)
        if not ok:
            raise RuntimeError("LLM match failed")
        update_job_status(job_id, JobStatus.MATCHED)


async def _worker_loop(*, worker_id: str, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    poll_sleep = float(settings.env.worker_poll_interval_seconds)
    llm_semaphore = asyncio.Semaphore(int(settings.env.llm_max_concurrent))

    logger.info("Pipeline worker started: worker_id={}", worker_id)
    while not stop_event.is_set():
        task = claim_next_pipeline_task(worker_id=worker_id)
        if task is None:
            await asyncio.sleep(poll_sleep)
            continue

        task_id = int(task["id"])
        task_type = task["task_type"]
        payload: Dict[str, Any] = task.get("payload") or {}
        job_id = payload.get("job_id")

        try:
            if task.get("cancel_requested"):
                mark_pipeline_task_cancelled(task_id)
                continue

            if task_type == PipelineTaskType.SCRAPE_JOB_DESCRIPTION.value:
                if not isinstance(job_id, int):
                    raise ValueError("Missing/invalid job_id in payload")
                await _execute_scrape_job_description(job_id)
                mark_pipeline_task_succeeded(task_id)
                continue

            if task_type == PipelineTaskType.MATCH_JOB.value:
                if not isinstance(job_id, int):
                    raise ValueError("Missing/invalid job_id in payload")
                await _execute_match_job(job_id, llm_semaphore=llm_semaphore)
                mark_pipeline_task_succeeded(task_id)
                continue

            raise ValueError(f"Unsupported task_type: {task_type}")
        except asyncio.CancelledError:
            logger.warning("Pipeline worker cancelled mid-task {}", task_id)
            mark_pipeline_task_cancelled(task_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Task {} failed ({}): {}", task_id, task_type, exc)
            mark_pipeline_task_failed(task_id, str(exc))

    logger.info("Pipeline worker stopped: worker_id={}", worker_id)


def start_pipeline_workers() -> WorkerHandle:
    """
    Start background pipeline worker(s) in the current event loop.

    Note: FastAPI/uvicorn runs a single event loop per process; for dev reload,
    this will restart cleanly.
    """
    settings = get_settings()
    concurrency = max(1, int(settings.env.worker_concurrency))

    stop_event = asyncio.Event()

    async def supervisor() -> None:
        workers = [
            asyncio.create_task(_worker_loop(worker_id=f"worker-{i+1}", stop_event=stop_event))
            for i in range(concurrency)
        ]
        try:
            await asyncio.gather(*workers)
        finally:
            for w in workers:
                if not w.done():
                    w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    task = asyncio.create_task(supervisor())
    return WorkerHandle(stop_event=stop_event, task=task)


async def stop_pipeline_workers(handle: Optional[WorkerHandle]) -> None:
    if handle is None:
        return
    handle.stop_event.set()
    if not handle.task.done():
        handle.task.cancel()
    await asyncio.gather(handle.task, return_exceptions=True)
