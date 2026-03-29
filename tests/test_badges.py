from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from linkedin_copilot.models import JobRecord, JobStatus  # type: ignore  # noqa: E402
from linkedin_copilot.db import (  # type: ignore  # noqa: E402
    init_db,
    insert_job,
    insert_staging_job,
    create_scrape_run,
    get_pending_jobs_count,
    get_pending_scrape_count,
    get_pending_match_count,
    get_total_staging_jobs_count,
)


@pytest.fixture
def test_db(tmp_path):
  """Create a temporary test database."""
  db_path = tmp_path / "test_badges.sqlite3"
  with patch("linkedin_copilot.db._get_db_path", return_value=db_path):
      init_db()
      yield db_path


class TestBadgeCounts:
  def test_pending_jobs_count(self, test_db):
      with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
          # No jobs yet
          assert get_pending_jobs_count() == 0

          # Insert jobs in various statuses
          pending_scrape = JobRecord(
              title="Pending Scrape",
              company="A",
              location="X",
              url="https://example.com/job/1",
              linkedin_job_id="1",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_SCRAPE,
          )
          pending_match = JobRecord(
              title="Pending Match",
              company="B",
              location="Y",
              url="https://example.com/job/2",
              linkedin_job_id="2",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_MATCH,
          )
          matched = JobRecord(
              title="Matched",
              company="C",
              location="Z",
              url="https://example.com/job/3",
              linkedin_job_id="3",
              date_found=datetime.utcnow(),
              status=JobStatus.MATCHED,
          )
          applied = JobRecord(
              title="Applied",
              company="D",
              location="W",
              url="https://example.com/job/4",
              linkedin_job_id="4",
              date_found=datetime.utcnow(),
              status=JobStatus.APPLIED,
          )

          insert_job(pending_scrape)
          insert_job(pending_match)
          insert_job(matched)
          insert_job(applied)

          # Only pending_scrape and pending_match should be counted
          assert get_pending_jobs_count() == 2
          assert get_pending_scrape_count() == 1
          assert get_pending_match_count() == 1

  def test_total_staging_jobs_count(self, test_db):
      with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
          from linkedin_copilot.models import JobSource  # imported here to avoid unused in other tests

          # No staging rows yet
          assert get_total_staging_jobs_count() == 0

          # Create a scrape run and insert staging jobs
          run_id = create_scrape_run(
              company_id=1,
              total_found=3,
              new_count=3,
              duplicates_count=0,
              errors=None,
          )

          for i in range(3):
              job = JobRecord(
                  title=f"Staging {i}",
                  company="Staging Co",
                  location="Remote",
                  url=f"https://example.com/staging/{i}",
                  external_job_id=f"stg-{i}",
                  date_found=datetime.utcnow(),
                  status=JobStatus.PENDING_SCRAPE,
                  source=JobSource.GREENHOUSE,
                  company_id=1,
              )
              insert_staging_job(run_id, job)

          assert get_total_staging_jobs_count() == 3


class TestBadgesAPI:
  def test_badges_endpoint(self, test_db):
      from fastapi.testclient import TestClient
      from linkedin_copilot.web import app

      with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
          client = TestClient(app)

          # Initially zero
          resp = client.get("/api/badges")
          assert resp.status_code == 200
          data = resp.json()
          assert data["jobs_pending"] == 0
          assert data["review_pending"] == 0

          # Insert one pending job and one staging job
          pending = JobRecord(
              title="Pending",
              company="X",
              location="Y",
              url="https://example.com/job/p",
              linkedin_job_id="pending-1",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_SCRAPE,
          )
          insert_job(pending)

          run_id = create_scrape_run(
              company_id=1,
              total_found=1,
              new_count=1,
              duplicates_count=0,
              errors=None,
          )
          from linkedin_copilot.models import JobSource

          staging_job = JobRecord(
              title="Staging",
              company="Y",
              location="Z",
              url="https://example.com/staging/p",
              external_job_id="stg-p",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_SCRAPE,
              source=JobSource.GREENHOUSE,
              company_id=1,
          )
          insert_staging_job(run_id, staging_job)

          resp2 = client.get("/api/badges")
          assert resp2.status_code == 200
          data2 = resp2.json()
          assert data2["jobs_pending"] == 1
          assert data2["review_pending"] == 1
          assert "pending_scrape" in data2
          assert "pending_match" in data2
          assert "suggested_action" in data2
          # Both non-zero: rule prefers review_pulled
          assert data2["suggested_action"] is not None
          assert data2["suggested_action"]["action"] == "review_pulled"
          assert data2["suggested_action"]["url"] == "/careers/review"


class TestSuggestedAction:
  """Test next-best-action rule: review_pulled preferred over process_pending when both non-zero."""

  def test_suggested_action_null_when_both_zero(self, test_db):
      from fastapi.testclient import TestClient
      from linkedin_copilot.web import app  # noqa: F401

      with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
          client = TestClient(app)
          resp = client.get("/api/badges")
          assert resp.status_code == 200
          data = resp.json()
          assert data["jobs_pending"] == 0
          assert data["review_pending"] == 0
          assert data["suggested_action"] is None

  def test_suggested_action_process_pending_when_only_jobs_pending(self, test_db):
      from fastapi.testclient import TestClient
      from linkedin_copilot.web import app  # noqa: F401

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
          resp = client.get("/api/badges")
          assert resp.status_code == 200
          data = resp.json()
          assert data["jobs_pending"] == 1
          assert data["review_pending"] == 0
          assert data["suggested_action"] is not None
          assert data["suggested_action"]["action"] == "process_pending"
          assert data["suggested_action"]["url"] == "/jobs"
          assert "message" in data["suggested_action"]
          assert "label" in data["suggested_action"]

  def test_suggested_action_review_pulled_when_only_review_pending(self, test_db):
      from fastapi.testclient import TestClient
      from linkedin_copilot.models import JobSource
      from linkedin_copilot.web import app  # noqa: F401

      with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
          run_id = create_scrape_run(
              company_id=1,
              total_found=1,
              new_count=1,
              duplicates_count=0,
              errors=None,
          )
          staging = JobRecord(
              title="Staging",
              company="Co",
              location="Remote",
              url="https://example.com/staging/1",
              external_job_id="stg-1",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_SCRAPE,
              source=JobSource.GREENHOUSE,
              company_id=1,
          )
          insert_staging_job(run_id, staging)
          client = TestClient(app)
          resp = client.get("/api/badges")
          assert resp.status_code == 200
          data = resp.json()
          assert data["jobs_pending"] == 0
          assert data["review_pending"] == 1
          assert data["suggested_action"] is not None
          assert data["suggested_action"]["action"] == "review_pulled"
          assert data["suggested_action"]["url"] == "/careers/review"

  def test_suggested_action_prefers_review_pulled_when_both_non_zero(self, test_db):
      from fastapi.testclient import TestClient
      from linkedin_copilot.models import JobSource
      from linkedin_copilot.web import app  # noqa: F401

      with patch("linkedin_copilot.db._get_db_path", return_value=test_db):
          insert_job(JobRecord(
              title="P",
              company="X",
              location="Y",
              url="https://example.com/job/1",
              linkedin_job_id="j1",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_SCRAPE,
          ))
          run_id = create_scrape_run(
              company_id=1,
              total_found=1,
              new_count=1,
              duplicates_count=0,
              errors=None,
          )
          insert_staging_job(run_id, JobRecord(
              title="S",
              company="Y",
              location="Z",
              url="https://example.com/staging/1",
              external_job_id="stg-1",
              date_found=datetime.utcnow(),
              status=JobStatus.PENDING_SCRAPE,
              source=JobSource.GREENHOUSE,
              company_id=1,
          ))
          client = TestClient(app)
          resp = client.get("/api/badges")
          assert resp.status_code == 200
          data = resp.json()
          assert data["jobs_pending"] == 1
          assert data["review_pending"] == 1
          assert data["suggested_action"]["action"] == "review_pulled"

