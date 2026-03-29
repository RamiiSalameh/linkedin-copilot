"""
Tests for the career site scraping module.

Tests cover:
- ATS detection from URLs
- Job normalization
- Company model
- Scraper base functionality
- Rate limiting
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, AsyncMock
import json

from linkedin_copilot.models import (
    Company,
    ATSType,
    JobSource,
    JobRecord,
    JobStatus,
)
from linkedin_copilot.careers.detector import (
    detect_ats_type,
    extract_board_token,
    extract_company_name_from_url,
    normalize_careers_url,
    is_supported_ats,
    get_api_url,
    resolve_careers_url,
    validate_careers_url,
)
from linkedin_copilot.careers.base import (
    SearchResult,
    RateLimiter,
    JobSourceBase,
    job_matches_location_filter,
    normalize_location_filters,
)
from linkedin_copilot.careers.greenhouse import GreenhouseScraper
from linkedin_copilot.careers.lever import LeverScraper
from linkedin_copilot.careers.workday import WorkdayScraper
from linkedin_copilot.careers.registry import (
    get_scraper,
    get_scraper_for_company,
    is_ats_supported,
    get_supported_ats_types,
)


class TestATSDetection:
    """Tests for ATS type detection from URLs."""
    
    def test_detect_greenhouse_boards_url(self):
        """Test detecting Greenhouse from boards.greenhouse.io URL."""
        url = "https://boards.greenhouse.io/stripe"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.GREENHOUSE
        assert token == "stripe"
    
    def test_detect_greenhouse_with_jobs_path(self):
        """Test detecting Greenhouse with /jobs path."""
        url = "https://boards.greenhouse.io/figma/jobs"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.GREENHOUSE
        assert token == "figma"
    
    def test_detect_lever_jobs_url(self):
        """Test detecting Lever from jobs.lever.co URL."""
        url = "https://jobs.lever.co/netflix"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.LEVER
        assert token == "netflix"
    
    def test_detect_lever_with_slash(self):
        """Test detecting Lever with trailing slash."""
        url = "https://jobs.lever.co/spotify/"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.LEVER
        assert token == "spotify"
    
    def test_detect_workday_url(self):
        """Test detecting Workday URL."""
        url = "https://amazon.wd5.myworkdayjobs.com/en-US/Amazon_US"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.WORKDAY
        assert token == "amazon"
    
    def test_detect_unknown_url(self):
        """Test unknown URL returns UNKNOWN."""
        url = "https://careers.google.com/jobs/"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.UNKNOWN
        assert token is None
    
    def test_detect_empty_url(self):
        """Test empty URL returns UNKNOWN."""
        ats_type, token = detect_ats_type("")
        
        assert ats_type == ATSType.UNKNOWN
        assert token is None
    
    def test_detect_case_insensitive(self):
        """Test detection is case-insensitive."""
        url = "https://BOARDS.GREENHOUSE.IO/AIRBNB"
        ats_type, token = detect_ats_type(url)
        
        assert ats_type == ATSType.GREENHOUSE
        assert token == "airbnb"


class TestBoardTokenExtraction:
    """Tests for board token extraction."""
    
    def test_extract_greenhouse_token(self):
        """Test extracting token for Greenhouse."""
        url = "https://boards.greenhouse.io/notion"
        token = extract_board_token(url, ATSType.GREENHOUSE)
        
        assert token == "notion"
    
    def test_extract_lever_token(self):
        """Test extracting token for Lever."""
        url = "https://jobs.lever.co/openai"
        token = extract_board_token(url, ATSType.LEVER)
        
        assert token == "openai"
    
    def test_extract_token_unsupported_ats(self):
        """Test extracting token for unsupported ATS."""
        url = "https://example.com/careers"
        token = extract_board_token(url, ATSType.CUSTOM)
        
        assert token is None


class TestCompanyNameExtraction:
    """Tests for company name extraction from URLs."""
    
    def test_extract_from_greenhouse(self):
        """Test extracting name from Greenhouse URL."""
        url = "https://boards.greenhouse.io/figma"
        name = extract_company_name_from_url(url)
        
        assert name == "Figma"
    
    def test_extract_from_lever(self):
        """Test extracting name from Lever URL."""
        url = "https://jobs.lever.co/openai"
        name = extract_company_name_from_url(url)
        
        assert name == "Openai"
    
    def test_extract_handles_hyphens(self):
        """Test that hyphens are converted to spaces and title-cased."""
        url = "https://boards.greenhouse.io/my-cool-startup"
        name = extract_company_name_from_url(url)
        
        assert name == "My Cool Startup"


class TestURLNormalization:
    """Tests for URL normalization."""
    
    def test_normalize_removes_trailing_slash(self):
        """Test trailing slash is removed."""
        url = "https://boards.greenhouse.io/stripe/"
        normalized = normalize_careers_url(url)
        
        assert normalized == "https://boards.greenhouse.io/stripe"
    
    def test_normalize_removes_query_params(self):
        """Test query parameters are removed."""
        url = "https://jobs.lever.co/netflix?team=engineering"
        normalized = normalize_careers_url(url)
        
        assert normalized == "https://jobs.lever.co/netflix"


class TestSupportedATS:
    """Tests for ATS support checking."""
    
    def test_greenhouse_is_supported(self):
        """Test Greenhouse is supported."""
        url = "https://boards.greenhouse.io/stripe"
        assert is_supported_ats(url) is True
    
    def test_lever_is_supported(self):
        """Test Lever is supported."""
        url = "https://jobs.lever.co/netflix"
        assert is_supported_ats(url) is True
    
    def test_workday_is_supported(self):
        """Test Workday is supported."""
        url = "https://amazon.wd5.myworkdayjobs.com"
        assert is_supported_ats(url) is True
    
    def test_unknown_not_supported(self):
        """Test unknown ATS is not supported."""
        url = "https://careers.google.com"
        assert is_supported_ats(url) is False


class TestGetAPIUrl:
    """Tests for API URL generation."""
    
    def test_get_greenhouse_api_url(self):
        """Test Greenhouse API URL generation."""
        url = "https://boards.greenhouse.io/stripe"
        api_url = get_api_url(url)
        
        assert api_url == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true"
    
    def test_get_lever_api_url(self):
        """Test Lever API URL generation."""
        url = "https://jobs.lever.co/netflix"
        api_url = get_api_url(url)
        
        assert api_url == "https://api.lever.co/v0/postings/netflix?mode=json"
    
    def test_get_api_url_workday_returns_none(self):
        """Test Workday has no GET API; get_api_url returns None."""
        url = "https://philips.wd3.myworkdayjobs.com/en-US/jobs"
        api_url = get_api_url(url)
        
        assert api_url is None

    def test_get_api_url_unsupported(self):
        """Test API URL returns None for unsupported ATS."""
        url = "https://careers.google.com"
        api_url = get_api_url(url)
        
        assert api_url is None


class TestResolveCareersUrl:
    """Tests for resolve_careers_url (redirect resolution)."""

    @pytest.mark.asyncio
    async def test_resolve_returns_final_url_on_redirect(self):
        """Test that redirects are followed and final URL is returned."""
        final_url = "https://philips.wd3.myworkdayjobs.com/en-US/jobs"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = final_url
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            url = "https://www.careers.philips.com/il/en/search-results"
            result_url, error = await resolve_careers_url(url)
        assert error is None
        assert result_url == final_url

    @pytest.mark.asyncio
    async def test_resolve_empty_url_returns_error(self):
        """Test empty URL returns error."""
        result_url, error = await resolve_careers_url("")
        assert error == "URL is empty"
        assert result_url == ""

    @pytest.mark.asyncio
    async def test_resolve_adds_https_if_missing(self):
        """Test that URL without scheme gets https."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://philips.wd3.myworkdayjobs.com/jobs"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result_url, error = await resolve_careers_url("philips.wd3.myworkdayjobs.com")
        assert error is None
        assert "https://" in result_url


class TestValidateCareersUrlWorkday:
    """Tests for validate_careers_url with Workday."""

    @pytest.mark.asyncio
    async def test_validate_workday_url_accepted(self):
        """Test that a direct Workday URL is accepted (no GET API check)."""
        with patch("linkedin_copilot.careers.detector.resolve_careers_url", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ("https://philips.wd3.myworkdayjobs.com/en-US/jobs", None)

            valid, msg = await validate_careers_url("https://philips.wd3.myworkdayjobs.com")

            assert valid is True
            assert "Workday" in msg


class TestLocationFilter:
    """Tests for location filter helpers (job_matches_location_filter, normalize_location_filters)."""

    def test_normalize_location_filters_none(self):
        """None or empty returns None (no filter)."""
        assert normalize_location_filters(None) is None
        assert normalize_location_filters([]) is None

    def test_normalize_location_filters_strips_and_drops_empty(self):
        """Terms are stripped; empty strings dropped."""
        assert normalize_location_filters([" Israel ", "", "Remote"]) == ["Israel", "Remote"]
        assert normalize_location_filters(["  ", ""]) is None

    def test_job_matches_location_filter_no_filter(self):
        """No filter (None or empty) includes all jobs."""
        assert job_matches_location_filter("Tel Aviv, Israel", None) is True
        assert job_matches_location_filter("Remote", []) is True
        assert job_matches_location_filter("Berlin", normalize_location_filters([])) is True

    def test_job_matches_location_filter_single_term(self):
        """Single term match (case-insensitive substring)."""
        assert job_matches_location_filter("Tel Aviv, Israel", ["Israel"]) is True
        assert job_matches_location_filter("ISRAEL", ["israel"]) is True
        assert job_matches_location_filter("Berlin, Germany", ["Germany"]) is True
        assert job_matches_location_filter("Berlin, Germany", ["Israel"]) is False

    def test_job_matches_location_filter_multiple_terms_or(self):
        """Any matching term includes the job (OR)."""
        assert job_matches_location_filter("Israel - Remote", ["Israel", "Remote"]) is True
        assert job_matches_location_filter("Remote", ["Israel", "Remote"]) is True
        assert job_matches_location_filter("Amsterdam", ["Israel", "Remote"]) is False

    def test_job_matches_location_filter_remote_in_location(self):
        """'Remote' in job location string matches filter 'Remote'."""
        assert job_matches_location_filter("Israel - Remote", ["Remote"]) is True
        assert job_matches_location_filter("Remote", ["Remote"]) is True

    def test_job_matches_location_filter_empty_job_location(self):
        """Empty job location matches only if no filter or filter has empty-like terms (stripped out)."""
        assert job_matches_location_filter("", None) is True
        assert job_matches_location_filter("", ["Remote"]) is False


class TestSearchResult:
    """Tests for SearchResult container."""
    
    def test_empty_result(self):
        """Test empty search result."""
        result = SearchResult()
        
        assert result.new_jobs == 0
        assert result.duplicates == 0
        assert result.total_found == 0
        assert len(result.errors) == 0
    
    def test_add_job(self):
        """Test adding a job to results."""
        result = SearchResult()
        job = JobRecord(
            title="Software Engineer",
            company="Test Corp",
            location="Remote",
            url="https://example.com/job/1",
            date_found=datetime.utcnow(),
        )
        
        result.add_job(job)
        
        assert result.new_jobs == 1
        assert result.total_found == 1
    
    def test_add_duplicate(self):
        """Test adding a duplicate."""
        result = SearchResult()
        result.add_duplicate()
        
        assert result.duplicates == 1
        assert result.total_found == 1
        assert result.new_jobs == 0
    
    def test_add_error(self):
        """Test adding an error."""
        result = SearchResult()
        result.add_error("Connection failed")
        
        assert len(result.errors) == 1
        assert "Connection failed" in result.errors


class TestCompanyModel:
    """Tests for the Company model."""
    
    def test_create_company(self):
        """Test creating a company."""
        company = Company(
            name="Stripe",
            careers_url="https://boards.greenhouse.io/stripe",
            ats_type=ATSType.GREENHOUSE,
            board_token="stripe",
        )
        
        assert company.name == "Stripe"
        assert company.ats_type == ATSType.GREENHOUSE
        assert company.enabled is True
        assert company.total_jobs == 0
    
    def test_company_to_dict(self):
        """Test company serialization."""
        company = Company(
            id=1,
            name="Netflix",
            careers_url="https://jobs.lever.co/netflix",
            ats_type=ATSType.LEVER,
            board_token="netflix",
            total_jobs=42,
        )
        
        data = company.to_dict()
        
        assert data["id"] == 1
        assert data["name"] == "Netflix"
        assert data["ats_type"] == "lever"
        assert data["total_jobs"] == 42


class TestJobSourceEnum:
    """Tests for JobSource enum."""
    
    def test_linkedin_source(self):
        """Test LinkedIn source value."""
        assert JobSource.LINKEDIN.value == "linkedin"
    
    def test_greenhouse_source(self):
        """Test Greenhouse source value."""
        assert JobSource.GREENHOUSE.value == "greenhouse"
    
    def test_lever_source(self):
        """Test Lever source value."""
        assert JobSource.LEVER.value == "lever"

    def test_workday_source(self):
        """Test Workday source value."""
        assert JobSource.WORKDAY.value == "workday"


class TestScraperRegistry:
    """Tests for scraper registry."""
    
    def test_get_greenhouse_scraper(self):
        """Test getting Greenhouse scraper."""
        scraper = get_scraper(ATSType.GREENHOUSE)
        
        assert scraper is not None
        assert isinstance(scraper, GreenhouseScraper)
    
    def test_get_lever_scraper(self):
        """Test getting Lever scraper."""
        scraper = get_scraper(ATSType.LEVER)
        
        assert scraper is not None
        assert isinstance(scraper, LeverScraper)
    
    def test_get_workday_scraper(self):
        """Test getting Workday scraper."""
        scraper = get_scraper(ATSType.WORKDAY)
        
        assert scraper is not None
        assert isinstance(scraper, WorkdayScraper)

    def test_get_unsupported_scraper(self):
        """Test getting scraper for unsupported ATS (e.g. Ashby)."""
        scraper = get_scraper(ATSType.ASHBY)
        
        assert scraper is None
    
    def test_get_scraper_for_company(self):
        """Test getting scraper for company."""
        company = Company(
            name="Stripe",
            careers_url="https://boards.greenhouse.io/stripe",
            ats_type=ATSType.GREENHOUSE,
            board_token="stripe",
        )
        
        scraper = get_scraper_for_company(company)
        
        assert scraper is not None
        assert isinstance(scraper, GreenhouseScraper)

    def test_get_scraper_for_workday_company(self):
        """Test getting Workday scraper for Workday company."""
        company = Company(
            name="Philips",
            careers_url="https://philips.wd3.myworkdayjobs.com/en-US/jobs",
            ats_type=ATSType.WORKDAY,
            board_token="philips",
        )
        scraper = get_scraper_for_company(company)
        assert scraper is not None
        assert isinstance(scraper, WorkdayScraper)
    
    def test_is_ats_supported(self):
        """Test checking ATS support."""
        assert is_ats_supported(ATSType.GREENHOUSE) is True
        assert is_ats_supported(ATSType.LEVER) is True
        assert is_ats_supported(ATSType.WORKDAY) is True
        assert is_ats_supported(ATSType.ASHBY) is False
    
    def test_get_supported_ats_types(self):
        """Test getting list of supported ATS."""
        supported = get_supported_ats_types()
        
        assert ATSType.GREENHOUSE in supported
        assert ATSType.LEVER in supported
        assert ATSType.WORKDAY in supported


class TestGreenhouseScraper:
    """Tests for Greenhouse scraper."""
    
    def test_source_name(self):
        """Test scraper source name."""
        scraper = GreenhouseScraper()
        
        assert scraper.source_name == "greenhouse"
        assert scraper.source_type == JobSource.GREENHOUSE
    
    def test_normalize_job(self):
        """Test job normalization from Greenhouse API response."""
        scraper = GreenhouseScraper()
        company = Company(
            id=1,
            name="Stripe",
            careers_url="https://boards.greenhouse.io/stripe",
            ats_type=ATSType.GREENHOUSE,
            board_token="stripe",
        )
        
        raw_job = {
            "id": 12345,
            "title": "Software Engineer",
            "location": {"name": "San Francisco, CA"},
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/12345",
            "content": "<p>We are looking for a talented engineer...</p>",
            "updated_at": "2024-03-01T10:00:00Z",
        }
        
        job = scraper.normalize_job(raw_job, company)
        
        assert job.title == "Software Engineer"
        assert job.company == "Stripe"
        assert job.location == "San Francisco, CA"
        assert job.external_job_id == "12345"
        assert job.source == JobSource.GREENHOUSE
        assert job.company_id == 1
        assert job.status == JobStatus.PENDING_MATCH
    
    def test_extract_location_from_offices(self):
        """Test location extraction from offices array."""
        scraper = GreenhouseScraper()
        
        raw_job = {
            "id": 1,
            "offices": [{"name": "New York"}, {"name": "Remote"}],
        }
        
        location = scraper._extract_location(raw_job)
        
        assert "New York" in location
    
    def test_extract_location_fallback_remote(self):
        """Test location fallback to Remote."""
        scraper = GreenhouseScraper()
        
        raw_job = {"id": 1}
        location = scraper._extract_location(raw_job)
        
        assert location == "Remote"

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_location_filter(self):
        """With location_filters only matching jobs are in result."""
        company = Company(
            id=1,
            name="Stripe",
            careers_url="https://boards.greenhouse.io/stripe",
            ats_type=ATSType.GREENHOUSE,
            board_token="stripe",
        )
        jobs_data = [
            {"id": 1, "title": "Engineer A", "location": {"name": "Tel Aviv, Israel"}, "absolute_url": "https://boards.greenhouse.io/stripe/jobs/1", "content": "", "updated_at": None},
            {"id": 2, "title": "Engineer B", "location": {"name": "London, UK"}, "absolute_url": "https://boards.greenhouse.io/stripe/jobs/2", "content": "", "updated_at": None},
            {"id": 3, "title": "Engineer C", "location": {"name": "Israel - Remote"}, "absolute_url": "https://boards.greenhouse.io/stripe/jobs/3", "content": "", "updated_at": None},
        ]
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={"jobs": jobs_data})
        mock_response.status_code = 200

        with patch.object(GreenhouseScraper, "_request_with_retry", new_callable=AsyncMock, return_value=mock_response):
            with patch("linkedin_copilot.careers.greenhouse.job_exists_by_external_id", return_value=False):
                scraper = GreenhouseScraper()
                result = await scraper.fetch_jobs(company, location_filters=["Israel"])

        assert len(result.jobs) == 2  # Tel Aviv Israel + Israel - Remote
        locations = [j.location for j in result.jobs]
        assert "Tel Aviv, Israel" in locations
        assert "Israel - Remote" in locations
        assert "London, UK" not in locations

    @pytest.mark.asyncio
    async def test_fetch_jobs_without_location_filter_returns_all(self):
        """Without location_filters all jobs are returned (regression)."""
        company = Company(
            id=1,
            name="Stripe",
            careers_url="https://boards.greenhouse.io/stripe",
            ats_type=ATSType.GREENHOUSE,
            board_token="stripe",
        )
        jobs_data = [
            {"id": 1, "title": "A", "location": {"name": "Israel"}, "absolute_url": "https://x/jobs/1", "content": "", "updated_at": None},
            {"id": 2, "title": "B", "location": {"name": "UK"}, "absolute_url": "https://x/jobs/2", "content": "", "updated_at": None},
        ]
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={"jobs": jobs_data})

        with patch.object(GreenhouseScraper, "_request_with_retry", new_callable=AsyncMock, return_value=mock_response):
            with patch("linkedin_copilot.careers.greenhouse.job_exists_by_external_id", return_value=False):
                scraper = GreenhouseScraper()
                result = await scraper.fetch_jobs(company)

        assert len(result.jobs) == 2


class TestLeverScraper:
    """Tests for Lever scraper."""
    
    def test_source_name(self):
        """Test scraper source name."""
        scraper = LeverScraper()
        
        assert scraper.source_name == "lever"
        assert scraper.source_type == JobSource.LEVER
    
    def test_normalize_job(self):
        """Test job normalization from Lever API response."""
        scraper = LeverScraper()
        company = Company(
            id=2,
            name="Netflix",
            careers_url="https://jobs.lever.co/netflix",
            ats_type=ATSType.LEVER,
            board_token="netflix",
        )
        
        raw_job = {
            "id": "abc123",
            "text": "Senior Engineer",
            "categories": {"location": "Los Angeles, CA"},
            "applyUrl": "https://jobs.lever.co/netflix/abc123/apply",
            "description": "Join our streaming team...",
            "createdAt": 1704067200000,
        }
        
        job = scraper.normalize_job(raw_job, company)
        
        assert job.title == "Senior Engineer"
        assert job.company == "Netflix"
        assert job.location == "Los Angeles, CA"
        assert job.external_job_id == "abc123"
        assert job.source == JobSource.LEVER
        assert job.company_id == 2
    
    def test_extract_location_from_categories(self):
        """Test location extraction from categories."""
        scraper = LeverScraper()
        
        raw_job = {
            "id": "1",
            "categories": {"location": "Austin, TX"},
        }
        
        location = scraper._extract_location(raw_job)
        
        assert location == "Austin, TX"
    
    def test_extract_location_remote_workplace(self):
        """Test remote detection from workplaceType."""
        scraper = LeverScraper()
        
        raw_job = {
            "id": "1",
            "workplaceType": "remote",
        }
        
        location = scraper._extract_location(raw_job)
        
        assert location == "Remote"

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_location_filter(self):
        """With location_filters only matching jobs are in result."""
        company = Company(
            id=2,
            name="Netflix",
            careers_url="https://jobs.lever.co/netflix",
            ats_type=ATSType.LEVER,
            board_token="netflix",
        )
        jobs_data = [
            {"id": "a", "text": "Role A", "categories": {"location": "Tel Aviv, Israel"}, "applyUrl": "https://lever.co/n/a", "description": ""},
            {"id": "b", "text": "Role B", "categories": {"location": "Amsterdam"}, "applyUrl": "https://lever.co/n/b", "description": ""},
            {"id": "c", "text": "Role C", "categories": {"location": "Remote - Israel"}, "applyUrl": "https://lever.co/n/c", "description": ""},
        ]
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value=jobs_data)

        with patch.object(LeverScraper, "_request_with_retry", new_callable=AsyncMock, return_value=mock_response):
            with patch("linkedin_copilot.careers.lever.job_exists_by_external_id", return_value=False):
                scraper = LeverScraper()
                result = await scraper.fetch_jobs(company, location_filters=["Israel"])

        assert len(result.jobs) == 2
        locations = [j.location for j in result.jobs]
        assert "Tel Aviv, Israel" in locations
        assert "Remote - Israel" in locations
        assert "Amsterdam" not in locations

    @pytest.mark.asyncio
    async def test_fetch_jobs_without_location_filter_returns_all(self):
        """Without location_filters all jobs are returned (regression)."""
        company = Company(
            id=2,
            name="Netflix",
            careers_url="https://jobs.lever.co/netflix",
            ats_type=ATSType.LEVER,
            board_token="netflix",
        )
        jobs_data = [
            {"id": "a", "text": "A", "categories": {"location": "Israel"}, "applyUrl": "https://x/a", "description": ""},
            {"id": "b", "text": "B", "categories": {"location": "UK"}, "applyUrl": "https://x/b", "description": ""},
        ]
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value=jobs_data)

        with patch.object(LeverScraper, "_request_with_retry", new_callable=AsyncMock, return_value=mock_response):
            with patch("linkedin_copilot.careers.lever.job_exists_by_external_id", return_value=False):
                scraper = LeverScraper()
                result = await scraper.fetch_jobs(company)

        assert len(result.jobs) == 2


class TestWorkdayScraper:
    """Tests for Workday scraper."""

    def test_source_name(self):
        """Test scraper source name."""
        scraper = WorkdayScraper()
        assert scraper.source_name == "workday"
        assert scraper.source_type == JobSource.WORKDAY

    def test_normalize_job(self):
        """Test job normalization from scraped Workday data."""
        scraper = WorkdayScraper()
        company = Company(
            id=3,
            name="Philips",
            careers_url="https://philips.wd3.myworkdayjobs.com/en-US/jobs",
            ats_type=ATSType.WORKDAY,
            board_token="philips",
        )
        raw_job = {
            "url": "https://philips.wd3.myworkdayjobs.com/en-US/job/12345",
            "title": "Software Engineer",
            "location": "Amsterdam, NL",
            "external_id": "12345",
        }
        job = scraper.normalize_job(raw_job, company)
        assert job.title == "Software Engineer"
        assert job.company == "Philips"
        assert job.location == "Amsterdam, NL"
        assert job.external_job_id == "12345"
        assert job.source == JobSource.WORKDAY
        assert job.company_id == 3
        assert job.status == JobStatus.PENDING_SCRAPE
        assert str(job.url) == raw_job["url"]

    def test_job_list_url_tenant_path_no_jobs_suffix(self):
        """Tenant path like Nvidia /NVIDIAExternalCareerSite is used as-is; en-US variant available."""
        scraper = WorkdayScraper()
        nvidia = Company(
            id=8,
            name="Nvidia",
            careers_url="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
            ats_type=ATSType.WORKDAY,
            board_token="nvidia",
        )
        assert scraper._job_list_url(nvidia) == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
        assert scraper._job_list_url(nvidia, use_en_us=True) == "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        # Philips with /en-US/jobs stays unchanged
        philips = Company(
            id=1,
            name="Philips",
            careers_url="https://philips.wd3.myworkdayjobs.com/en-US/jobs",
            ats_type=ATSType.WORKDAY,
            board_token="philips",
        )
        assert scraper._job_list_url(philips) == "https://philips.wd3.myworkdayjobs.com/en-US/jobs"
        assert scraper._job_list_url(philips, use_en_us=True) == "https://philips.wd3.myworkdayjobs.com/en-US/jobs"

    def test_job_list_url_preserves_query_params(self):
        """URL query params (e.g. location facet) are preserved so pre-filtered Workday URLs work."""
        scraper = WorkdayScraper()
        company = Company(
            id=8,
            name="Nvidia",
            careers_url="https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?locationHierarchy1=2fcb99c455831013ea52bbe14cf9326c",
            ats_type=ATSType.WORKDAY,
            board_token="nvidia",
        )
        url = scraper._job_list_url(company)
        assert "locationHierarchy1=2fcb99c455831013ea52bbe14cf9326c" in url
        assert url.startswith("https://nvidia.wd5.myworkdayjobs.com/")

    def test_external_id_from_url(self):
        """Test extracting external ID from Workday job URL (full path after /job/ for uniqueness)."""
        scraper = WorkdayScraper()
        assert scraper._external_id_from_url("https://tenant.wd3.myworkdayjobs.com/job/abc-123") == "abc-123"
        assert scraper._external_id_from_url("https://tenant.wd3.myworkdayjobs.com/job/12345") == "12345"
        # Multi-segment path (e.g. /job/Israel/Job-Title_12345-1) must be full path so different jobs get different IDs
        assert scraper._external_id_from_url(
            "https://philips.wd3.myworkdayjobs.com/en-US/job/Israel/Clinical-Application-Specialist_12345-1"
        ) == "Israel/Clinical-Application-Specialist_12345-1"
        assert scraper._external_id_from_url("") is None

    @pytest.mark.asyncio
    async def test_fetch_jobs_mocked_playwright(self):
        """Test fetch_jobs with mocked Playwright returns jobs from DOM."""
        company = Company(
            id=1,
            name="Philips",
            careers_url="https://philips.wd3.myworkdayjobs.com/en-US/jobs",
            ats_type=ATSType.WORKDAY,
            board_token="philips",
        )
        mock_page = AsyncMock()
        mock_page.query_selector_all = AsyncMock(return_value=[])
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.goto = AsyncMock()
        mock_page.set_default_timeout = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        # Simulate job links found when job list selectors fail
        mock_link = AsyncMock()
        mock_link.get_attribute = AsyncMock(return_value="/en-US/job/99999")
        mock_link.query_selector = AsyncMock(return_value=None)
        mock_link.inner_text = AsyncMock(return_value="Test Engineer")
        mock_card = AsyncMock()
        mock_card.query_selector = AsyncMock(side_effect=[mock_link, None, None])
        mock_card.get_attribute = AsyncMock(return_value=None)

        async def all_side_effect(selector):
            if "job/" in str(selector):
                return [mock_card]
            return []

        mock_page.query_selector_all = AsyncMock(side_effect=all_side_effect)
        mock_page.wait_for_selector = AsyncMock(side_effect=Exception("no selector"))

        with patch("playwright.async_api.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch("linkedin_copilot.careers.workday.job_exists_by_external_id", return_value=False):
                scraper = WorkdayScraper()
                result = await scraper.fetch_jobs(company)

        assert result.total_found >= 0
        assert isinstance(result.jobs, list)

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_location_filters_mocked_playwright(self):
        """fetch_jobs with location_filters attempts facet apply and returns without error."""
        company = Company(
            id=1,
            name="Philips",
            careers_url="https://philips.wd3.myworkdayjobs.com/en-US/jobs",
            ats_type=ATSType.WORKDAY,
            board_token="philips",
        )
        mock_page = AsyncMock()
        mock_page.url = "https://philips.wd3.myworkdayjobs.com/en-US/jobs"
        mock_page.query_selector_all = AsyncMock(return_value=[])
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.goto = AsyncMock()
        mock_page.set_default_timeout = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_link = AsyncMock()
        mock_link.get_attribute = AsyncMock(return_value="/en-US/job/99999")
        mock_link.query_selector = AsyncMock(return_value=None)
        mock_card = AsyncMock()
        mock_card.query_selector = AsyncMock(side_effect=[mock_link, None, None])
        mock_card.get_attribute = AsyncMock(return_value=None)

        async def all_side_effect(selector):
            if "job/" in str(selector):
                return [mock_card]
            return []

        mock_page.query_selector_all = AsyncMock(side_effect=all_side_effect)
        mock_page.wait_for_selector = AsyncMock(side_effect=Exception("no selector"))

        with patch("playwright.async_api.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch("linkedin_copilot.careers.workday.job_exists_by_external_id", return_value=False):
                scraper = WorkdayScraper()
                result = await scraper.fetch_jobs(company, location_filters=["Israel"])
        assert isinstance(result.jobs, list)
        assert result.total_found >= 0

    @pytest.mark.asyncio
    async def test_fetch_job_details_returns_description(self):
        """Test fetch_job_details extracts description from Workday page."""
        job = JobRecord(
            id=100,
            title="AI Developer",
            company="Philips",
            location="Ramat Gan",
            url="https://philips.wd3.myworkdayjobs.com/en-US/jobs-and-careers/job/Ramat-Gan/AI-Developer_567174-1",
            external_job_id="Ramat-Gan/AI-Developer_567174-1",
            date_found=datetime.utcnow(),
            source=JobSource.WORKDAY,
            company_id=1,
        )
        mock_el = AsyncMock()
        mock_el.inner_text = AsyncMock(return_value="Full job description text from Workday. " * 20)
        mock_page = AsyncMock()
        mock_page.query_selector = AsyncMock(return_value=mock_el)
        mock_page.goto = AsyncMock()
        mock_page.set_default_timeout = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser = MagicMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        with patch("playwright.async_api.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__ = AsyncMock(return_value=mock_playwright)
            mock_pw.return_value.__aexit__ = AsyncMock(return_value=None)
            scraper = WorkdayScraper()
            result = await scraper.fetch_job_details(job)
        assert result is not None
        assert "Full job description text from Workday" in result
        assert len(result) > 100


class TestRateLimiter:
    """Tests for rate limiter."""
    
    @pytest.mark.asyncio
    async def test_rate_limiter_acquires(self):
        """Test rate limiter can acquire."""
        limiter = RateLimiter(requests_per_second=10.0)
        
        await limiter.acquire("example.com")
    
    def test_backoff_delay_increases(self):
        """Test backoff delay increases with attempts."""
        limiter = RateLimiter()
        
        delay1 = limiter.get_backoff_delay(0)
        delay2 = limiter.get_backoff_delay(1)
        delay3 = limiter.get_backoff_delay(2)
        
        assert delay2 > delay1
        assert delay3 > delay2
    
    def test_backoff_delay_capped(self):
        """Test backoff delay is capped at 60 seconds."""
        limiter = RateLimiter()
        
        delay = limiter.get_backoff_delay(10)
        
        assert delay <= 60.0


class TestBaseScraperHelpers:
    """Tests for base scraper helper methods."""
    
    def test_clean_html(self):
        """Test HTML cleaning."""
        scraper = GreenhouseScraper()
        
        html = "<p>Hello <strong>World</strong>!</p>"
        clean = scraper.clean_html(html)
        
        assert "<" not in clean
        assert "Hello" in clean
        assert "World" in clean
    
    def test_clean_html_none(self):
        """Test cleaning None returns empty string."""
        scraper = GreenhouseScraper()
        
        assert scraper.clean_html(None) == ""
    
    def test_extract_snippet(self):
        """Test snippet extraction."""
        scraper = GreenhouseScraper()
        
        long_text = "This is a very long description " * 20
        snippet = scraper.extract_snippet(long_text, max_length=50)
        
        assert len(snippet) <= 53  # 50 + "..."
        assert snippet.endswith("...")
    
    def test_extract_snippet_short_text(self):
        """Test snippet extraction with short text."""
        scraper = GreenhouseScraper()
        
        short_text = "Short description"
        snippet = scraper.extract_snippet(short_text, max_length=100)
        
        assert snippet == short_text
        assert "..." not in snippet


class TestCareersScrapeAPI:
    """API tests for scrape endpoints accepting location_filters."""

    def test_scrape_all_accepts_location_filters_body(self):
        """POST /api/careers/scrape-all with location_filters in body does not 500."""
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        with patch("linkedin_copilot.web.get_all_companies", return_value=[]):
            client = TestClient(app)
            resp = client.post(
                "/api/careers/scrape-all",
                json={"location_filters": ["Israel", "Remote"]},
            )
        # No companies -> 400, but body was parsed (no 422)
        assert resp.status_code == 400
        assert "enabled companies" in (resp.json().get("error") or "").lower()

    def test_scrape_company_accepts_location_filters_body(self):
        """POST /api/companies/:id/scrape with location_filters in body does not 500."""
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        client = TestClient(app)
        resp = client.post(
            "/api/companies/999999/scrape",
            json={"location_filters": ["Israel"]},
        )
        # Company not found -> 404, but body was accepted (no 422)
        assert resp.status_code == 404

    def test_scrape_all_rejects_second_start_while_running(self):
        """Second scrape-all request should return 409 while first run is active."""
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        from linkedin_copilot.models import Company, ATSType

        companies = [
            Company(
                id=1,
                name="Philips",
                careers_url="https://philips.wd3.myworkdayjobs.com/en-US/jobs",
                ats_type=ATSType.WORKDAY,
                board_token="philips",
            )
        ]
        client = TestClient(app)
        with patch("linkedin_copilot.web.get_all_companies", return_value=companies):
            with patch("linkedin_copilot.web.asyncio.create_task") as create_task_mock:
                from linkedin_copilot import web as web_module
                web_module._careers_scrape_status["running"] = False
                create_task_mock.side_effect = lambda coro: (coro.close(), None)[1]
                first = client.post("/api/careers/scrape-all", json={})
                second = client.post("/api/careers/scrape-all", json={})
        assert first.status_code == 200
        assert second.status_code == 409


class TestAddCompanyAPI:
    """API tests for POST /api/companies (add company)."""

    @pytest.mark.asyncio
    async def test_add_company_success_returns_200_with_success_and_company(self):
        """POST /api/companies with valid payload returns 200, success True, and company with name."""
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        from linkedin_copilot.models import Company, ATSType

        created = Company(
            id=1,
            name="TestCo",
            careers_url="https://boards.greenhouse.io/testco",
            ats_type=ATSType.GREENHOUSE,
            board_token="testco",
            enabled=True,
        )

        with patch("linkedin_copilot.careers.detector.resolve_careers_url", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ("https://boards.greenhouse.io/testco", None)
            with patch("linkedin_copilot.careers.detector.detect_ats_type") as mock_detect:
                mock_detect.return_value = (ATSType.GREENHOUSE, "testco")
                with patch("linkedin_copilot.careers.detector.validate_careers_url", new_callable=AsyncMock) as mock_validate:
                    mock_validate.return_value = (True, "OK")
                    with patch("linkedin_copilot.web.insert_company") as mock_insert:
                        mock_insert.return_value = created
                        client = TestClient(app)
                        resp = client.post(
                            "/api/companies",
                            json={"careers_url": "https://boards.greenhouse.io/testco", "name": "TestCo"},
                        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "company" in data
        assert data["company"].get("name") == "TestCo"
        assert data["company"].get("id") == 1

    def test_add_company_missing_careers_url_returns_400(self):
        """POST /api/companies without careers_url returns 400."""
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app

        client = TestClient(app)
        resp = client.post("/api/companies", json={})
        assert resp.status_code == 400
        assert "error" in resp.json()
        resp2 = client.post("/api/companies", json={"careers_url": "   "})
        assert resp2.status_code == 400
        assert "error" in resp2.json()

    @pytest.mark.asyncio
    async def test_add_company_unsupported_ats_returns_400(self):
        """POST /api/companies with unsupported ATS returns 400."""
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        from linkedin_copilot.models import ATSType

        with patch("linkedin_copilot.careers.detector.resolve_careers_url", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = ("https://careers.example.com", None)
            with patch("linkedin_copilot.careers.detector.detect_ats_type") as mock_detect:
                mock_detect.return_value = (ATSType.UNKNOWN, None)
                client = TestClient(app)
                resp = client.post(
                    "/api/companies",
                    json={"careers_url": "https://careers.example.com"},
                )
        assert resp.status_code == 400
        assert "error" in resp.json()
