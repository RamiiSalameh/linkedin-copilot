"""
Lever ATS scraper using the public JSON API.

API Documentation: https://github.com/lever/postings-api

Key endpoints:
- GET https://api.lever.co/v0/postings/{company}?mode=json
- GET https://api.lever.co/v0/postings/{company}/{posting_id}
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


class LeverScraper(JobSourceBase):
    """Scraper for Lever job boards."""
    
    source_name = "lever"
    source_type = JobSource.LEVER
    
    BASE_API_URL = "https://api.lever.co/v0/postings"
    
    async def fetch_jobs(self, company: Company, **filters) -> SearchResult:
        """
        Fetch all jobs from a Lever job board.
        
        Args:
            company: Company with board_token set (company slug)
            **filters: Optional filters. location_filters: list of str; only jobs
                whose location contains any term (case-insensitive) are included.
        
        Returns:
            SearchResult with jobs found
        """
        result = SearchResult()
        location_filters = normalize_location_filters(
            filters.get("location_filters") or filters.get("locations")
        )

        if not company.board_token:
            result.add_error(f"No board_token set for company {company.name}")
            logger.error("Cannot scrape Lever: no board_token for {}", company.name)
            return result
        
        url = f"{self.BASE_API_URL}/{company.board_token}?mode=json"
        logger.info("Fetching jobs from Lever: {}", url)
        
        response = await self._request_with_retry(url)
        if response is None:
            result.add_error(f"Failed to fetch jobs from Lever for {company.name}")
            return result
        
        try:
            jobs_data = response.json()
            
            if not isinstance(jobs_data, list):
                result.add_error(f"Unexpected Lever response format for {company.name}")
                return result
            
            logger.info("Found {} jobs on Lever for {}", len(jobs_data), company.name)
            
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
                    logger.warning("Failed to parse Lever job: {} - {}", raw_job.get("text", "unknown"), e)
                    result.add_error(f"Parse error: {e}")
            
        except Exception as e:
            logger.error("Failed to parse Lever response: {}", e)
            result.add_error(f"JSON parse error: {e}")
        
        return result
    
    async def fetch_job_details(self, job: JobRecord) -> Optional[str]:
        """
        Fetch full job description from Lever.
        
        For Lever, we get description in the list API, but this can fetch
        more details if needed.
        """
        if not job.external_job_id:
            logger.warning("No external_job_id for job, cannot fetch details")
            return None
        
        board_token = self._extract_board_token_from_url(str(job.url))
        if not board_token:
            logger.warning("Cannot extract board_token from URL: {}", job.url)
            return None
        
        url = f"{self.BASE_API_URL}/{board_token}/{job.external_job_id}"
        
        response = await self._request_with_retry(url)
        if response is None:
            return None
        
        try:
            data = response.json()
            description_parts = []
            
            if data.get("description"):
                description_parts.append(data["description"])
            
            lists = data.get("lists", [])
            for list_item in lists:
                if list_item.get("text"):
                    description_parts.append(f"\n{list_item['text']}")
                if list_item.get("content"):
                    description_parts.append(list_item["content"])
            
            additional = data.get("additional")
            if additional:
                description_parts.append(f"\n{additional}")
            
            return "\n".join(description_parts)
            
        except Exception as e:
            logger.error("Failed to parse job details: {}", e)
            return None
    
    def normalize_job(self, raw_job: Dict[str, Any], company: Company) -> JobRecord:
        """Convert Lever API response to JobRecord."""
        job_id = str(raw_job.get("id", ""))
        title = raw_job.get("text", "Unknown Position")
        
        location = self._extract_location(raw_job)
        
        apply_url = raw_job.get("applyUrl", raw_job.get("hostedUrl", ""))
        
        description = raw_job.get("description", "") or raw_job.get("descriptionPlain", "")
        snippet = self.extract_snippet(description)
        
        created_at = raw_job.get("createdAt")
        date_posted = None
        if created_at:
            try:
                date_posted = datetime.fromtimestamp(created_at / 1000)
            except (ValueError, TypeError):
                pass
        
        return JobRecord(
            title=title,
            company=company.name,
            location=location,
            url=apply_url,
            external_job_id=job_id,
            date_found=datetime.utcnow(),
            date_posted=date_posted,
            easy_apply=False,
            description_snippet=snippet,
            company_logo_url=company.logo_url,
            status=JobStatus.PENDING_MATCH,
            source=JobSource.LEVER,
            company_id=company.id,
        )
    
    def _extract_location(self, raw_job: Dict[str, Any]) -> str:
        """Extract location from Lever job data."""
        categories = raw_job.get("categories", {})
        if isinstance(categories, dict):
            location = categories.get("location")
            if location:
                return location
        
        workplace_type = raw_job.get("workplaceType", "")
        if workplace_type and workplace_type.lower() == "remote":
            return "Remote"
        
        return "Remote"
    
    def _extract_board_token_from_url(self, url: str) -> Optional[str]:
        """Extract board token (company slug) from Lever URL."""
        import re
        
        match = re.search(r'jobs\.lever\.co/([^/]+)', url)
        if match:
            return match.group(1)
        
        match = re.search(r'lever\.co/([^/]+)', url)
        if match:
            return match.group(1)
        
        return None
    
    def _extract_department(self, raw_job: Dict[str, Any]) -> Optional[str]:
        """Extract department from Lever job data."""
        categories = raw_job.get("categories", {})
        if isinstance(categories, dict):
            return categories.get("department") or categories.get("team")
        return None
    
    def _extract_commitment(self, raw_job: Dict[str, Any]) -> Optional[str]:
        """Extract commitment (full-time, part-time) from Lever job data."""
        categories = raw_job.get("categories", {})
        if isinstance(categories, dict):
            return categories.get("commitment")
        return None


async def scrape_lever_company(company: Company) -> SearchResult:
    """Convenience function to scrape a Lever company."""
    scraper = LeverScraper()
    try:
        return await scraper.fetch_jobs(company)
    finally:
        await scraper.close()
