"""
Greenhouse ATS scraper using the public JSON API.

API Documentation: https://developers.greenhouse.io/job-board.html

Key endpoints:
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}
- GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true (includes description)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import (
    JobSourceBase,
    SearchResult,
    job_matches_location_filter,
    normalize_location_filters,
)
from ..models import Company, JobRecord, JobSource, JobStatus
from ..logging_setup import logger
from ..db import job_exists_by_external_id


class GreenhouseScraper(JobSourceBase):
    """Scraper for Greenhouse job boards."""
    
    source_name = "greenhouse"
    source_type = JobSource.GREENHOUSE
    
    BASE_API_URL = "https://boards-api.greenhouse.io/v1/boards"
    
    async def fetch_jobs(self, company: Company, **filters) -> SearchResult:
        """
        Fetch all jobs from a Greenhouse job board.
        
        Args:
            company: Company with board_token set
            **filters: Optional filters. location_filters: list of str; only jobs whose
            location contains any term (case-insensitive) are included.
        
        Returns:
            SearchResult with jobs found
        """
        result = SearchResult()
        location_filters = normalize_location_filters(
            filters.get("location_filters") or filters.get("locations")
        )

        if not company.board_token:
            result.add_error(f"No board_token set for company {company.name}")
            logger.error("Cannot scrape Greenhouse: no board_token for {}", company.name)
            return result
        
        url = f"{self.BASE_API_URL}/{company.board_token}/jobs?content=true"
        logger.info("Fetching jobs from Greenhouse: {}", url)
        
        response = await self._request_with_retry(url)
        if response is None:
            result.add_error(f"Failed to fetch jobs from Greenhouse for {company.name}")
            return result
        
        try:
            data = response.json()
            jobs_data = data.get("jobs", [])
            
            logger.info("Found {} jobs on Greenhouse for {}", len(jobs_data), company.name)
            
            for raw_job in jobs_data:
                try:
                    job_id = self.extract_job_id(raw_job)
                    
                    if job_id and job_exists_by_external_id(job_id, self.source_type):
                        result.add_duplicate()
                        continue

                    job = self.normalize_job(raw_job, company)
                    if not job_matches_location_filter(job.location, location_filters):
                        continue
                    result.add_job(job)
                    
                except Exception as e:
                    logger.warning("Failed to parse Greenhouse job: {} - {}", raw_job.get("title", "unknown"), e)
                    result.add_error(f"Parse error: {e}")
            
        except Exception as e:
            logger.error("Failed to parse Greenhouse response: {}", e)
            result.add_error(f"JSON parse error: {e}")
        
        return result
    
    async def fetch_job_details(self, job: JobRecord) -> Optional[str]:
        """
        Fetch full job description from Greenhouse.
        
        For Greenhouse, we already get the full description in the list API
        when using content=true, so this is mainly for jobs that need refreshing.
        """
        if not job.external_job_id:
            logger.warning("No external_job_id for job, cannot fetch details")
            return None
        
        board_token = self._extract_board_token_from_url(str(job.url))
        if not board_token:
            logger.warning("Cannot extract board_token from URL: {}", job.url)
            return None
        
        url = f"{self.BASE_API_URL}/{board_token}/jobs/{job.external_job_id}"
        
        response = await self._request_with_retry(url)
        if response is None:
            return None
        
        try:
            data = response.json()
            return data.get("content", "")
        except Exception as e:
            logger.error("Failed to parse job details: {}", e)
            return None
    
    def normalize_job(self, raw_job: Dict[str, Any], company: Company) -> JobRecord:
        """Convert Greenhouse API response to JobRecord."""
        job_id = str(raw_job.get("id", ""))
        title = raw_job.get("title", "Unknown Position")
        
        location = self._extract_location(raw_job)
        
        absolute_url = raw_job.get("absolute_url", "")
        
        content = raw_job.get("content", "")
        snippet = self.extract_snippet(content)
        
        updated_at = raw_job.get("updated_at")
        date_posted = None
        if updated_at:
            try:
                date_posted = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        
        return JobRecord(
            title=title,
            company=company.name,
            location=location,
            url=absolute_url,
            external_job_id=job_id,
            date_found=datetime.utcnow(),
            date_posted=date_posted,
            easy_apply=False,
            description_snippet=snippet,
            company_logo_url=company.logo_url,
            status=JobStatus.PENDING_MATCH,
            source=JobSource.GREENHOUSE,
            company_id=company.id,
        )
    
    def _extract_location(self, raw_job: Dict[str, Any]) -> str:
        """Extract location from Greenhouse job data."""
        location = raw_job.get("location")
        if location:
            if isinstance(location, dict):
                loc_name = location.get("name")
                if loc_name:
                    return loc_name
            elif isinstance(location, str):
                return location
        
        offices = raw_job.get("offices", [])
        if offices:
            office_names = [o.get("name", "") for o in offices if o.get("name")]
            if office_names:
                return ", ".join(office_names)
        
        return "Remote"
    
    def _extract_board_token_from_url(self, url: str) -> Optional[str]:
        """Extract board token from Greenhouse URL."""
        import re
        
        match = re.search(r'boards\.greenhouse\.io/(\w+)', url)
        if match:
            return match.group(1)
        
        match = re.search(r'greenhouse\.io/(\w+)/jobs', url)
        if match:
            return match.group(1)
        
        return None


async def scrape_greenhouse_company(company: Company) -> SearchResult:
    """Convenience function to scrape a Greenhouse company."""
    scraper = GreenhouseScraper()
    try:
        return await scraper.fetch_jobs(company)
    finally:
        await scraper.close()
