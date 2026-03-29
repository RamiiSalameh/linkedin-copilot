"""
Tests for career site scrape runs and staging (review-and-approve flow).

Covers:
- create_scrape_run, insert_staging_job
- get_runs, get_staging_jobs, get_run_by_id
- approve_staging_jobs (partial, approve_all, dedupe)
- discard_run
- get_staging_count_by_company
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from linkedin_copilot.models import (
    Company,
    ATSType,
    JobRecord,
    JobSource,
    JobStatus,
)
from linkedin_copilot.db import (
    init_db,
    insert_company,
    insert_job,
    get_company_by_id,
    get_job_count_by_company,
    create_scrape_run,
    insert_staging_job,
    get_runs,
    get_staging_jobs,
    get_run_by_id,
    approve_staging_jobs,
    discard_run,
    get_staging_count_by_company,
    update_company_last_scraped,
)
from linkedin_copilot.careers.base import SearchResult


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database with schema."""
    db_path = tmp_path / "test_staging.sqlite3"
    with patch("linkedin_copilot.db._get_db_path", return_value=db_path):
        init_db()
        yield db_path


@pytest.fixture
def company(test_db):
    """Insert a test company."""
    with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
        c = Company(
            name="Test Co",
            careers_url="https://boards.greenhouse.io/testco",
            ats_type=ATSType.GREENHOUSE,
            board_token="testco",
            enabled=True,
        )
        return insert_company(c)


class TestCreateScrapeRunAndStaging:
    """Test creating runs and inserting staging jobs."""

    def test_create_scrape_run(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=10,
                new_count=3,
                duplicates_count=7,
                errors=["err1"],
            )
            assert run_id is not None
            assert run_id > 0

    def test_insert_staging_job(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            job = JobRecord(
                title="Engineer",
                company="Test Co",
                location="Remote",
                url="https://example.com/job/1",
                external_job_id="ext-1",
                date_found=datetime.utcnow(),
                source=JobSource.GREENHOUSE,
                company_id=company.id,
            )
            sid = insert_staging_job(run_id, job)
            assert sid is not None
            assert sid > 0


class TestGetRunsAndStagingJobs:
    """Test get_runs, get_staging_jobs, get_run_by_id."""

    def test_get_runs_empty(self, test_db):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            runs = get_runs(pending_only=True)
            assert runs == []

    def test_get_runs_with_pending(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=2,
                new_count=2,
                duplicates_count=0,
                errors=None,
            )
            for i in range(2):
                job = JobRecord(
                    title=f"Job {i}",
                    company="Test Co",
                    location="Remote",
                    url=f"https://example.com/job/{i}",
                    external_job_id=f"ext-{i}",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                )
                insert_staging_job(run_id, job)
            runs = get_runs(pending_only=True)
            assert len(runs) == 1
            assert runs[0].id == run_id
            assert runs[0].pending_count == 2
            assert runs[0].company_id == company.id

    def test_get_staging_jobs(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            job = JobRecord(
                title="Dev",
                company="Test Co",
                location="NYC",
                url="https://example.com/job/1",
                external_job_id="ext-1",
                date_found=datetime.utcnow(),
                source=JobSource.LEVER,
                company_id=company.id,
            )
            insert_staging_job(run_id, job)
            jobs = get_staging_jobs(run_id)
            assert len(jobs) == 1
            assert jobs[0]["title"] == "Dev"
            assert jobs[0]["company"] == "Test Co"
            assert jobs[0]["location"] == "NYC"
            assert jobs[0]["source"] == "lever"
            assert jobs[0]["run_id"] == run_id

    def test_get_run_by_id(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            insert_staging_job(
                run_id,
                JobRecord(
                    title="J",
                    company="C",
                    location="L",
                    url="https://x.com/1",
                    external_job_id="e1",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                ),
            )
            run = get_run_by_id(run_id)
            assert run is not None
            assert run.id == run_id
            assert run.pending_count == 1
            assert run.company_id == company.id

    def test_get_run_by_id_not_found(self, test_db):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            assert get_run_by_id(99999) is None


class TestApproveStagingJobs:
    """Test approve_staging_jobs: insert into jobs, remove from staging, dedupe."""

    def test_approve_all_adds_to_jobs(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=2,
                new_count=2,
                duplicates_count=0,
                errors=None,
            )
            ids = []
            for i in range(2):
                job = JobRecord(
                    title=f"Job {i}",
                    company="Test Co",
                    location="Remote",
                    url=f"https://example.com/job/{i}",
                    external_job_id=f"ext-{i}",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                )
                sid = insert_staging_job(run_id, job)
                ids.append(sid)
            approved, skipped = approve_staging_jobs(run_id, ids)
            assert approved == 2
            assert skipped == 0
            assert get_staging_jobs(run_id) == []
            assert get_job_count_by_company(company.id) == 2

    def test_approve_partial(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=3,
                new_count=3,
                duplicates_count=0,
                errors=None,
            )
            staging_ids = []
            for i in range(3):
                job = JobRecord(
                    title=f"Job {i}",
                    company="Test Co",
                    location="Remote",
                    url=f"https://example.com/job/{i}",
                    external_job_id=f"ext-{i}",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                )
                staging_ids.append(insert_staging_job(run_id, job))
            # Approve only first two
            approved, skipped = approve_staging_jobs(run_id, staging_ids[:2])
            assert approved == 2
            assert skipped == 0
            remaining = get_staging_jobs(run_id)
            assert len(remaining) == 1
            assert remaining[0]["title"] == "Job 2"
            assert get_job_count_by_company(company.id) == 2

    def test_approve_skips_duplicate_external_id(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            # Pre-insert a job with same external_id in jobs table
            existing = JobRecord(
                title="Existing",
                company="Test Co",
                location="Remote",
                url="https://example.com/job/dup",
                external_job_id="dup-id",
                date_found=datetime.utcnow(),
                source=JobSource.GREENHOUSE,
                company_id=company.id,
            )
            insert_job(existing)
            assert get_job_count_by_company(company.id) == 1

            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            sid = insert_staging_job(
                run_id,
                JobRecord(
                    title="Duplicate",
                    company="Test Co",
                    location="Remote",
                    url="https://example.com/job/dup2",
                    external_job_id="dup-id",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                ),
            )
            approved, skipped = approve_staging_jobs(run_id, [sid])
            assert approved == 0
            assert skipped == 1
            assert get_job_count_by_company(company.id) == 1
            assert get_staging_jobs(run_id) == []

    def test_approve_empty_ids(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=0,
                new_count=0,
                duplicates_count=0,
                errors=None,
            )
            approved, skipped = approve_staging_jobs(run_id, [])
            assert approved == 0
            assert skipped == 0


class TestDiscardRun:
    """Test discard_run."""

    def test_discard_run_removes_staging(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=2,
                new_count=2,
                duplicates_count=0,
                errors=None,
            )
            for i in range(2):
                insert_staging_job(
                    run_id,
                    JobRecord(
                        title=f"J{i}",
                        company="C",
                        location="L",
                        url=f"https://x.com/{i}",
                        external_job_id=f"e{i}",
                        date_found=datetime.utcnow(),
                        source=JobSource.GREENHOUSE,
                        company_id=company.id,
                    ),
                )
            assert len(get_staging_jobs(run_id)) == 2
            discard_run(run_id)
            assert get_staging_jobs(run_id) == []
            assert get_job_count_by_company(company.id) == 0


class TestGetStagingCountByCompany:
    """Test get_staging_count_by_company."""

    def test_staging_count_zero(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            assert get_staging_count_by_company(company.id) == 0

    def test_staging_count_after_insert(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=3,
                new_count=3,
                duplicates_count=0,
                errors=None,
            )
            for i in range(3):
                insert_staging_job(
                    run_id,
                    JobRecord(
                        title=f"J{i}",
                        company="C",
                        location="L",
                        url=f"https://x.com/{i}",
                        external_job_id=f"e{i}",
                        date_found=datetime.utcnow(),
                        source=JobSource.GREENHOUSE,
                        company_id=company.id,
                    ),
                )
            assert get_staging_count_by_company(company.id) == 3


class TestGetJobCountByCompanyUnchanged:
    """Ensure get_job_count_by_company still counts only jobs table."""

    def test_count_only_jobs_table(self, test_db, company):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            insert_staging_job(
                run_id,
                JobRecord(
                    title="Staging",
                    company="C",
                    location="L",
                    url="https://x.com/s",
                    external_job_id="staging-1",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                ),
            )
            assert get_staging_count_by_company(company.id) == 1
            assert get_job_count_by_company(company.id) == 0
            # After approve, count increases
            jobs = get_staging_jobs(run_id)
            approve_staging_jobs(run_id, [jobs[0]["id"]])
            assert get_job_count_by_company(company.id) == 1


def _patch_db_path(test_db):
    """Patch db path for both test code and web app (same process)."""
    return patch("linkedin_copilot.db._get_db_path", return_value=test_db)


class TestCareersRunsAPI:
    """API tests for GET /api/careers/runs and GET /api/careers/runs/{id}/jobs."""

    def test_get_runs_empty(self, test_db):
        with _patch_db_path(test_db):
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.get("/api/careers/runs")
            assert resp.status_code == 200
            data = resp.json()
            assert "runs" in data
            assert data["runs"] == []

    def test_get_runs_with_runs(self, test_db, company):
        with _patch_db_path(test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            insert_staging_job(
                run_id,
                JobRecord(
                    title="API Job",
                    company="Test Co",
                    location="Remote",
                    url="https://example.com/job/1",
                    external_job_id="api-1",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                ),
            )
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.get("/api/careers/runs")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["runs"]) == 1
            assert data["runs"][0]["id"] == run_id
            assert data["runs"][0]["pending_count"] == 1
            assert data["runs"][0]["company_name"] == "Test Co"

    def test_get_run_jobs_200(self, test_db, company):
        with _patch_db_path(test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            insert_staging_job(
                run_id,
                JobRecord(
                    title="Run Job",
                    company="Test Co",
                    location="NYC",
                    url="https://example.com/job/1",
                    external_job_id="run-1",
                    date_found=datetime.utcnow(),
                    source=JobSource.LEVER,
                    company_id=company.id,
                ),
            )
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.get(f"/api/careers/runs/{run_id}/jobs")
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == run_id
            assert data["company_name"] == "Test Co"
            assert len(data["jobs"]) == 1
            assert data["jobs"][0]["title"] == "Run Job"
            assert data["jobs"][0]["source"] == "lever"

    def test_get_run_jobs_404(self, test_db):
        with _patch_db_path(test_db):
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.get("/api/careers/runs/99999/jobs")
            assert resp.status_code == 404
            assert "error" in resp.json()


class TestCareersApproveDiscardAPI:
    """API tests for POST approve and POST discard."""

    def test_approve_with_job_ids(self, test_db, company):
        with _patch_db_path(test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=2,
                new_count=2,
                duplicates_count=0,
                errors=None,
            )
            ids = []
            for i in range(2):
                job = JobRecord(
                    title=f"Job {i}",
                    company="Test Co",
                    location="Remote",
                    url=f"https://example.com/job/{i}",
                    external_job_id=f"approve-{i}",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                )
                ids.append(insert_staging_job(run_id, job))
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.post(
                f"/api/careers/runs/{run_id}/approve",
                json={"job_ids": ids},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["approved"] == 2
            assert data["skipped_duplicates"] == 0
            assert get_staging_jobs(run_id) == []

    def test_approve_all(self, test_db, company):
        with _patch_db_path(test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            insert_staging_job(
                run_id,
                JobRecord(
                    title="All Job",
                    company="Test Co",
                    location="Remote",
                    url="https://example.com/job/all",
                    external_job_id="all-1",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                ),
            )
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.post(
                f"/api/careers/runs/{run_id}/approve",
                json={"approve_all": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["approved"] == 1
            assert get_staging_jobs(run_id) == []

    def test_approve_missing_body_400(self, test_db, company):
        with _patch_db_path(test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=0,
                new_count=0,
                duplicates_count=0,
                errors=None,
            )
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.post(
                f"/api/careers/runs/{run_id}/approve",
                json={},
            )
            assert resp.status_code == 400
            assert "error" in resp.json()

    def test_discard_run(self, test_db, company):
        with _patch_db_path(test_db):
            run_id = create_scrape_run(
                company_id=company.id,
                total_found=1,
                new_count=1,
                duplicates_count=0,
                errors=None,
            )
            insert_staging_job(
                run_id,
                JobRecord(
                    title="Discard Me",
                    company="Test Co",
                    location="Remote",
                    url="https://example.com/job/d",
                    external_job_id="discard-1",
                    date_found=datetime.utcnow(),
                    source=JobSource.GREENHOUSE,
                    company_id=company.id,
                ),
            )
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.post(f"/api/careers/runs/{run_id}/discard")
            assert resp.status_code == 200
            assert resp.json().get("success") is True
            assert get_staging_jobs(run_id) == []

    def test_discard_run_404(self, test_db):
        with _patch_db_path(test_db):
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            client = TestClient(app)
            resp = client.post("/api/careers/runs/99999/discard")
            assert resp.status_code == 404


class TestCareersScrapeDedupAcrossStaging:
    """Regression tests for scrape dedupe against pending staging."""

    def test_rescrape_does_not_restage_same_external_job(self, test_db, company):
        with _patch_db_path(test_db):
            from fastapi.testclient import TestClient
            from linkedin_copilot.web import app
            from linkedin_copilot import web as web_module

            client = TestClient(app)
            web_module._careers_scrape_status["running"] = False

            job = JobRecord(
                title="Same Role",
                company=company.name,
                location="Remote",
                url="https://boards.greenhouse.io/testco/jobs/123",
                external_job_id="same-ext-123",
                date_found=datetime.utcnow(),
                source=JobSource.GREENHOUSE,
                company_id=company.id,
            )

            async def _fake_fetch_jobs(*args, **kwargs):
                result = SearchResult()
                result.add_job(job)
                return result

            fake_scraper = AsyncMock()
            fake_scraper.fetch_jobs = AsyncMock(side_effect=_fake_fetch_jobs)
            fake_scraper.close = AsyncMock(return_value=None)

            with patch("linkedin_copilot.careers.registry.get_scraper_for_company", return_value=fake_scraper):
                first = client.post(f"/api/companies/{company.id}/scrape", json={})
                second = client.post(f"/api/companies/{company.id}/scrape", json={})

            assert first.status_code == 200
            assert second.status_code == 200

            first_data = first.json()
            second_data = second.json()
            assert first_data["new_jobs"] == 1
            assert second_data["new_jobs"] == 0
            assert second_data["duplicates"] >= 1
            assert get_staging_count_by_company(company.id) == 1
