"""Tests for Pipeline Dashboard (GET /dashboard, GET /api/dashboard) and dashboard DB helpers."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from linkedin_copilot.models import JobRecord, JobStatus, MatchResult  # noqa: E402
from linkedin_copilot.db import (  # noqa: E402
    init_db,
    insert_job,
    save_match_result,
    create_scrape_run,
    insert_staging_job,
    get_matched_count,
    get_applied_count,
    get_pending_jobs_count,
    get_total_staging_jobs_count,
)


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test_dashboard.sqlite3"
    with patch("linkedin_copilot.db._get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestDashboardCounts:
    """Test get_matched_count and get_applied_count."""

    def test_matched_and_applied_counts_empty(self, test_db):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            assert get_matched_count() == 0
            assert get_applied_count() == 0

    def test_matched_and_applied_counts(self, test_db):
        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            for i, status in enumerate((JobStatus.MATCHED, JobStatus.MATCHED, JobStatus.APPLIED)):
                job = JobRecord(
                    title="Job",
                    company="Co",
                    location="X",
                    url=f"https://example.com/job/{i}",
                    linkedin_job_id=f"lid-{i}",
                    date_found=datetime.utcnow(),
                    status=status,
                )
                insert_job(job)
            assert get_matched_count() == 2
            assert get_applied_count() == 1


class TestDashboardAPI:
    """Test GET /api/dashboard."""

    def test_dashboard_api_structure(self, test_db):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            client = TestClient(app)
            resp = client.get("/api/dashboard")
            assert resp.status_code == 200
            data = resp.json()
            assert "pending_scrape" in data
            assert "pending_match" in data
            assert "jobs_pending" in data
            assert "review_pending" in data
            assert "matched_count" in data
            assert "applied_count" in data
            assert "suggested_action" in data
            assert "top_jobs" in data
            assert isinstance(data["top_jobs"], list)
            assert data["jobs_pending"] == 0
            assert data["review_pending"] == 0
            assert data["matched_count"] == 0
            assert data["applied_count"] == 0
            assert data["suggested_action"] is None

    def test_dashboard_api_suggested_action_when_pending(self, test_db):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            job = JobRecord(
                title="Pending",
                company="X",
                location="Y",
                url="https://example.com/job/1",
                linkedin_job_id="j1",
                date_found=datetime.utcnow(),
                status=JobStatus.PENDING_SCRAPE,
            )
            insert_job(job)
            client = TestClient(app)
            resp = client.get("/api/dashboard")
            assert resp.status_code == 200
            data = resp.json()
            assert data["jobs_pending"] == 1
            assert data["suggested_action"] is not None
            assert data["suggested_action"]["action"] == "process_pending"
            assert data["suggested_action"]["url"] == "/jobs"

    def test_dashboard_api_top_jobs_populated(self, test_db):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            job = JobRecord(
                title="High Match Job",
                company="TopCo",
                location="Remote",
                url="https://example.com/job/high",
                linkedin_job_id="high1",
                date_found=datetime.utcnow(),
                status=JobStatus.MATCHED,
            )
            inserted = insert_job(job)
            save_match_result(
                MatchResult(
                    job_id=inserted.id,
                    match_score=85,
                    top_reasons=["Strong fit"],
                    missing_requirements=[],
                    suggested_resume_bullets=[],
                )
            )
            client = TestClient(app)
            resp = client.get("/api/dashboard")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["top_jobs"]) >= 1
            first = data["top_jobs"][0]
            assert first["title"] == "High Match Job"
            assert first["company"] == "TopCo"
            assert first.get("match_score") == 85
            assert first.get("recommendation") == "Apply"


class TestDashboardPage:
    """Test GET / (dashboard is home) and GET /dashboard redirects."""

    def test_dashboard_page_returns_200_at_root(self, test_db):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            client = TestClient(app)
            resp = client.get("/")
            assert resp.status_code == 200
            assert "text/html" in resp.headers.get("content-type", "")

    def test_dashboard_page_contains_expected_content(self, test_db):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            client = TestClient(app)
            resp = client.get("/")
            assert resp.status_code == 200
            html = resp.text
            assert "Pipeline dashboard" in html or "Pipeline Dashboard" in html
            assert "Pending scrape" in html
            assert "Pending match" in html
            assert "Matched" in html
            assert "Applied" in html
            assert "View all jobs" in html
            assert "Top jobs to consider" in html or "Top jobs" in html

    def test_dashboard_redirects_to_root(self, test_db):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
            client = TestClient(app)
            resp = client.get("/dashboard", follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers.get("location") == "/"
