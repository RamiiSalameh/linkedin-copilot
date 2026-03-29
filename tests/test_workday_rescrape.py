"""
Tests for Workday job description rescrape flow.

When rescraping descriptions for jobs (e.g. via Process All Pending), Workday jobs
must use WorkdayScraper.fetch_job_details instead of the LinkedIn extract logic,
since Workday pages use a different DOM.
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from linkedin_copilot.models import JobRecord, JobSource, JobStatus


@pytest.fixture
def workday_job():
    """A Workday job without a description (needs rescrape)."""
    return JobRecord(
        id=513,
        title="AI Developer",
        company="Philips",
        location="Ramat Gan",
        url="https://philips.wd3.myworkdayjobs.com/en-US/jobs-and-careers/job/Ramat-Gan/AI-Developer_567174-1",
        external_job_id="Ramat-Gan/AI-Developer_567174-1",
        date_found=datetime.utcnow(),
        source=JobSource.WORKDAY,
        company_id=1,
        status=JobStatus.PENDING_SCRAPE,
    )


@pytest.fixture
def linkedin_job():
    """A LinkedIn job (should use generic scrape_job_description)."""
    return JobRecord(
        id=100,
        title="Software Engineer",
        company="Acme",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/123",
        date_found=datetime.utcnow(),
        source=JobSource.LINKEDIN,
        status=JobStatus.PENDING_SCRAPE,
    )


@pytest.mark.asyncio
async def test_scrape_descriptions_uses_workday_scraper_for_workday_jobs(workday_job):
    """When job.source is WORKDAY, _scrape_descriptions_for_jobs uses WorkdayScraper.fetch_job_details and update_job_description."""
    from linkedin_copilot.web import _scrape_descriptions_for_jobs

    fake_description = "Workday job description content. " * 50
    mock_fetch = AsyncMock(return_value=fake_description)
    mock_scraper_instance = MagicMock()
    mock_scraper_instance.fetch_job_details = mock_fetch
    mock_scraper_class = MagicMock(return_value=mock_scraper_instance)
    with patch("linkedin_copilot.web.get_job_full_description", return_value=None):
        with patch("linkedin_copilot.web._progress_status", {"running": True}):
            with patch("linkedin_copilot.web.update_job_description") as mock_update_desc:
                with patch("linkedin_copilot.web.update_job_status"):
                    # WorkdayScraper is imported inside the function from careers.workday
                    with patch("linkedin_copilot.careers.workday.WorkdayScraper", mock_scraper_class):
                        n = await _scrape_descriptions_for_jobs([workday_job], update_progress=False)
    assert n == 1
    mock_fetch.assert_called_once_with(workday_job)
    mock_update_desc.assert_called_once_with(workday_job.id, fake_description)


@pytest.mark.asyncio
async def test_scrape_descriptions_uses_linkedin_extract_for_non_workday_jobs(linkedin_job):
    """When job.source is not WORKDAY, _scrape_descriptions_for_jobs uses scrape_job_description."""
    from linkedin_copilot.web import _scrape_descriptions_for_jobs

    with patch("linkedin_copilot.web.get_job_full_description", return_value=None):
        with patch("linkedin_copilot.web._progress_status", {"running": True}):
            with patch("linkedin_copilot.web.scrape_job_description", new_callable=AsyncMock) as mock_linkedin:
                with patch("linkedin_copilot.web.update_job_status"):
                    mock_linkedin.return_value = None
                    n = await _scrape_descriptions_for_jobs([linkedin_job], update_progress=False)
    assert n == 1
    mock_linkedin.assert_called_once_with(linkedin_job)
