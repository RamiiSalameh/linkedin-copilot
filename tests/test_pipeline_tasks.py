from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from linkedin_copilot.db import (  # type: ignore
    claim_next_pipeline_task,
    create_task_group_id,
    enqueue_process_pending_tasks,
    get_task_group_summary,
    init_db,
    insert_job,
    mark_pipeline_task_succeeded,
    request_cancel_task_group,
)
from linkedin_copilot.models import JobRecord, JobStatus  # type: ignore


@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_pipeline_tasks.sqlite3"
    with patch("linkedin_copilot.db._get_db_path", return_value=db_path):
        init_db()
        yield db_path


def _seed_job(i: int) -> JobRecord:
    return JobRecord(
        title=f"Job {i}",
        company="Acme",
        location="Remote",
        url=f"https://example.com/job/{i}",
        linkedin_job_id=str(i),
        date_found=datetime.utcnow(),
        status=JobStatus.PENDING_SCRAPE,
    )


class TestPipelineTaskQueue:
    def test_enqueue_and_claim(self, test_db):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            # Seed jobs so worker actions would have something to reference
            j1 = insert_job(_seed_job(1))
            j2 = insert_job(_seed_job(2))

            group_id = create_task_group_id()
            enqueue_process_pending_tasks(group_id, [j1.id, j2.id])  # type: ignore[arg-type]

            summary = get_task_group_summary(group_id)
            assert summary["total"] == 4  # 2 scrape + 2 match
            assert summary["completed"] == 0
            assert summary["running"] is True

            t = claim_next_pipeline_task(worker_id="w1")
            assert t is not None
            assert t["status"] == "running"
            assert t["task_group_id"] == group_id

            # Mark succeeded and verify group completed increments
            mark_pipeline_task_succeeded(int(t["id"]))
            summary2 = get_task_group_summary(group_id)
            assert summary2["completed"] == 1

    def test_cancel_group_cancels_queued(self, test_db):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            j1 = insert_job(_seed_job(10))
            group_id = create_task_group_id()
            enqueue_process_pending_tasks(group_id, [j1.id])  # type: ignore[arg-type]

            request_cancel_task_group(group_id)
            summary = get_task_group_summary(group_id)
            # All tasks should be cancelled (none running yet)
            assert summary["counts"].get("cancelled", 0) == summary["total"]
