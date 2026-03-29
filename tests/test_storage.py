from pathlib import Path

from linkedin_copilot.db import delete_jobs, get_jobs_by_ids, get_jobs_paginated, init_db, insert_job, job_exists
from linkedin_copilot.models import JobRecord, JobStatus
from linkedin_copilot.storage.files import export_jobs_csv


def test_export_jobs_csv(tmp_path: Path, monkeypatch) -> None:
    # Point DB to temp directory
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    init_db()

    job = JobRecord(
        title="Test Role",
        company="TestCo",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/999",
        date_found=__import__("datetime").datetime.utcnow(),
        easy_apply=True,
        status=JobStatus.DISCOVERED,
    )
    insert_job(job)

    out = export_jobs_csv(tmp_path / "jobs.csv")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Test Role" in content


def test_delete_jobs_soft_delete_keeps_dedupe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    init_db()

    url = "https://www.linkedin.com/jobs/view/888"
    job = JobRecord(
        title="Backend Engineer",
        company="SoftDeleteCo",
        location="Remote",
        url=url,
        date_found=__import__("datetime").datetime.utcnow(),
        easy_apply=False,
        status=JobStatus.PENDING_SCRAPE,
    )
    job_id = insert_job(job)

    updated = delete_jobs([job_id])
    assert updated == 1

    # Row remains in DB as soft-deleted.
    rows = get_jobs_by_ids([job_id])
    assert len(rows) == 1
    assert rows[0].status == JobStatus.DELETED

    # Dedup still works because job row still exists.
    assert job_exists(url) is True

    # Soft-deleted jobs are hidden from normal paginated UI listing.
    jobs, total, _ = get_jobs_paginated(page=1, per_page=50)
    assert total == 0
    assert jobs == []

